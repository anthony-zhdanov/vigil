from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from supabase import Client

from app.decision_tree.interpreter import run_plumbing_decision_tree
from app.repositories import clients as clients_repo
from app.repositories import conversations as conversations_repo
from app.repositories import decision_tree_runs as decision_tree_runs_repo
from app.repositories import leads as leads_repo
from app.repositories import message_media as message_media_repo
from app.repositories import messages as messages_repo
from app.repositories._shared import now_iso
from app.services.action_executor import ExecutedAction, execute_actions


@dataclass(frozen=True, slots=True)
class InboundMedia:
    twilio_media_url: str
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class SmsWorkflowResult:
    processed: bool
    ignored_reason: str | None = None
    client_id: str | None = None
    lead_id: str | None = None
    conversation_id: str | None = None
    inbound_message_id: str | None = None
    decision_tree_run_id: str | None = None
    matched_node_key: str | None = None
    executed_actions: list[ExecutedAction] = field(default_factory=list)


def _row_id(row: dict[str, Any], context: str) -> str:
    row_id = row.get("id")
    if row_id is None:
        raise RuntimeError(f"{context} row is missing id")
    return str(row_id)


def _collected_info(conversation: dict[str, Any]) -> dict[str, Any]:
    value = conversation.get("collected_info")
    return dict(value) if isinstance(value, dict) else {}


def _conversation_state(conversation: dict[str, Any]) -> str:
    value = conversation.get("current_state")
    return str(value) if value else "awaiting_initial_reply"


def _parse_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(str(value or "0")))
    except ValueError:
        return 0


def parse_inbound_media(raw_payload: dict[str, Any] | None) -> list[InboundMedia]:
    if not raw_payload:
        return []

    indexes = set(range(_parse_non_negative_int(raw_payload.get("NumMedia"))))
    for key in raw_payload:
        if not key.startswith("MediaUrl"):
            continue
        suffix = key.removeprefix("MediaUrl")
        if suffix.isdigit():
            indexes.add(int(suffix))

    media: list[InboundMedia] = []
    for index in sorted(indexes):
        url_value = raw_payload.get(f"MediaUrl{index}")
        if url_value is None:
            continue

        media_url = str(url_value).strip()
        if not media_url:
            continue

        content_type_value = raw_payload.get(f"MediaContentType{index}")
        content_type = (
            str(content_type_value).strip() if content_type_value is not None else None
        )
        media.append(
            InboundMedia(
                twilio_media_url=media_url,
                content_type=content_type or None,
            )
        )

    return media


def process_inbound_sms(
    supabase: Client | None,
    twilio_client: Any | None,
    *,
    from_phone: str,
    to_phone: str,
    body: str,
    message_sid: str | None,
    raw_payload: dict[str, Any] | None = None,
    media: list[InboundMedia] | None = None,
    status_callback_url: str | None = None,
) -> SmsWorkflowResult:
    if not from_phone or not to_phone:
        return SmsWorkflowResult(
            processed=False, ignored_reason="missing_from_or_to"
        )
    if supabase is None:
        return SmsWorkflowResult(
            processed=False, ignored_reason="supabase_not_configured"
        )

    client = clients_repo.find_client_for_sms_number(supabase, to_phone)
    if client is None:
        return SmsWorkflowResult(
            processed=False, ignored_reason="unknown_twilio_number"
        )

    client_id = _row_id(client, "client")
    lead = leads_repo.upsert_lead(
        supabase,
        client_id=client_id,
        phone_number=from_phone,
        status="sms_reply",
    )
    lead_id = _row_id(lead, "lead")

    conversation = conversations_repo.get_or_create_active_conversation(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        channel="sms",
    )
    conversation_id = _row_id(conversation, "conversation")

    inbound_message = messages_repo.insert_message(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        direction="inbound",
        from_phone=from_phone,
        to_phone=to_phone,
        body=body,
        twilio_message_sid=message_sid,
        template_key=None,
        status="received",
        raw_payload=raw_payload,
    )
    inbound_message_id = _row_id(inbound_message, "message")

    media_items = list(media) if media is not None else parse_inbound_media(raw_payload)
    for media_item in media_items:
        message_media_repo.insert_message_media(
            supabase,
            message_id=inbound_message_id,
            client_id=client_id,
            lead_id=lead_id,
            twilio_media_url=media_item.twilio_media_url,
            content_type=media_item.content_type,
        )

    collected_info = _collected_info(conversation)
    if media_items:
        collected_info["photo_received"] = True

    decision_result = run_plumbing_decision_tree(
        message_body=body,
        current_state=_conversation_state(conversation),
        collected_info=collected_info,
    )

    decision_tree_run = decision_tree_runs_repo.insert_decision_tree_run(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        inbound_message_id=inbound_message_id,
        decision_tree_key=decision_result.tree_key,
        decision_tree_version=decision_result.tree_version,
        classifier_output=decision_result.classifier_output.to_dict(),
        matched_node_key=decision_result.matched_node_key,
        actions_json=decision_result.actions_json(),
        result_status=decision_result.conversation_status,
    )
    decision_tree_run_id = _row_id(decision_tree_run, "decision tree run")

    executed_actions = execute_actions(
        supabase,
        twilio_client,
        result=decision_result,
        client=client,
        lead=lead,
        conversation=conversation,
        inbound_message=inbound_message,
        customer_phone=from_phone,
        twilio_number=to_phone,
        status_callback_url=status_callback_url,
    )

    timestamp = now_iso()
    conversations_repo.update_conversation(
        supabase,
        conversation_id=conversation_id,
        status=decision_result.conversation_status,
        current_state=decision_result.conversation_state,
        collected_info=decision_result.collected_info,
        summary=decision_result.summary,
        last_message_at=timestamp,
        closed_at=timestamp
        if decision_result.conversation_status == "closed"
        else None,
    )

    return SmsWorkflowResult(
        processed=True,
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        inbound_message_id=inbound_message_id,
        decision_tree_run_id=decision_tree_run_id,
        matched_node_key=decision_result.matched_node_key,
        executed_actions=executed_actions,
    )
