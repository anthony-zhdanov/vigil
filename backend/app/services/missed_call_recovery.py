from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from supabase import Client

from app.decision_tree.templates.base import render_template
from app.repositories import call_events as call_events_repo
from app.repositories import clients as clients_repo
from app.repositories import conversations as conversations_repo
from app.repositories import leads as leads_repo
from app.repositories import messages as messages_repo
from app.repositories import opt_outs as opt_outs_repo
from app.repositories._shared import now_iso


DUPLICATE_SUPPRESSION_MINUTES = 60
MISSED_CALL_TEMPLATE_KEY = "missed_call_initial"


@dataclass(frozen=True, slots=True)
class MissedCallRecoveryResult:
    processed: bool
    ignored_reason: str | None = None
    client_id: str | None = None
    lead_id: str | None = None
    conversation_id: str | None = None
    call_event_id: str | None = None
    outbound_message_id: str | None = None
    outbound_status: str | None = None


def _row_id(row: dict[str, Any], context: str) -> str:
    row_id = row.get("id")
    if row_id is None:
        raise RuntimeError(f"{context} row is missing id")
    return str(row_id)


def _maybe_row_id(row: dict[str, Any], context: str) -> str | None:
    row_id = row.get("id")
    if row_id is None:
        return None
    return str(row_id)


def _send_sms(
    twilio_client: Any | None,
    *,
    from_phone: str,
    to_phone: str,
    body: str,
    status_callback_url: str | None = None,
) -> str:
    if twilio_client is None:
        raise RuntimeError("Twilio is not configured")

    create_kwargs: dict[str, Any] = {
        "from_": from_phone,
        "to": to_phone,
        "body": body,
    }
    if status_callback_url:
        create_kwargs["status_callback"] = status_callback_url

    message = twilio_client.messages.create(**create_kwargs)
    sid = getattr(message, "sid", None)
    return str(sid) if sid else ""


def process_missed_call(
    supabase: Client | None,
    twilio_client: Any | None,
    *,
    from_phone: str | None,
    to_phone: str | None,
    call_sid: str | None,
    call_status: str | None,
    raw_payload: dict[str, Any] | None = None,
    status_callback_url: str | None = None,
) -> MissedCallRecoveryResult:
    if not from_phone or not to_phone:
        return MissedCallRecoveryResult(
            processed=False, ignored_reason="missing_from_or_to"
        )
    if supabase is None:
        return MissedCallRecoveryResult(
            processed=False, ignored_reason="supabase_not_configured"
        )

    client = clients_repo.find_client_for_voice_number(
        supabase, to_phone, legacy_fallback=True
    )
    if client is None:
        call_event = call_events_repo.insert_call_event(
            supabase,
            client_id=None,
            lead_id=None,
            from_phone=from_phone,
            to_phone=to_phone,
            call_sid=call_sid,
            call_status=call_status,
            raw_payload=raw_payload,
        )
        return MissedCallRecoveryResult(
            processed=False,
            ignored_reason="unknown_twilio_number",
            call_event_id=_maybe_row_id(call_event, "call event"),
        )

    client_id = _row_id(client, "client")
    lead = leads_repo.get_lead_by_client_phone(supabase, client_id, from_phone)
    lead_created = lead is None
    lead_id = _maybe_row_id(lead, "lead") if lead is not None else None

    opted_out = opt_outs_repo.is_opted_out(
        supabase, client_id=client_id, phone_number=from_phone
    )
    if opted_out:
        call_event = call_events_repo.insert_call_event(
            supabase,
            client_id=client_id,
            lead_id=lead_id,
            from_phone=from_phone,
            to_phone=to_phone,
            call_sid=call_sid,
            call_status=call_status,
            raw_payload=raw_payload,
        )
        return MissedCallRecoveryResult(
            processed=False,
            ignored_reason="opted_out",
            client_id=client_id,
            lead_id=lead_id,
            call_event_id=_maybe_row_id(call_event, "call event"),
        )

    if lead is None:
        lead = leads_repo.upsert_lead(
            supabase,
            client_id=client_id,
            phone_number=from_phone,
            status="missed_call",
        )
        lead_id = _row_id(lead, "lead")
    elif lead_id is None:
        raise RuntimeError("lead row is missing id")

    call_event = call_events_repo.insert_call_event(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        from_phone=from_phone,
        to_phone=to_phone,
        call_sid=call_sid,
        call_status=call_status,
        raw_payload=raw_payload,
    )
    call_event_id = _maybe_row_id(call_event, "call event")

    active_conversation = conversations_repo.get_active_conversation(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        channel="sms",
    )
    if active_conversation is not None:
        return MissedCallRecoveryResult(
            processed=False,
            ignored_reason="active_conversation_exists",
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=_row_id(active_conversation, "conversation"),
            call_event_id=call_event_id,
        )

    if messages_repo.recent_recovery_sms_exists(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        duplicate_suppression_minutes=DUPLICATE_SUPPRESSION_MINUTES,
    ):
        return MissedCallRecoveryResult(
            processed=False,
            ignored_reason="recent_recovery_sms_exists",
            client_id=client_id,
            lead_id=lead_id,
            call_event_id=call_event_id,
        )

    if not lead_created:
        lead = leads_repo.upsert_lead(
            supabase,
            client_id=client_id,
            phone_number=from_phone,
            status="missed_call",
        )
        lead_id = _row_id(lead, "lead")

    try:
        conversation = conversations_repo.create_conversation(
            supabase,
            client_id=client_id,
            lead_id=lead_id,
            channel="sms",
            status="waiting_for_customer",
            current_state="awaiting_initial_reply",
            collected_info={},
            summary="",
        )
    except Exception:
        active_conversation = conversations_repo.get_active_conversation(
            supabase,
            client_id=client_id,
            lead_id=lead_id,
            channel="sms",
        )
        if active_conversation is None:
            raise
        return MissedCallRecoveryResult(
            processed=False,
            ignored_reason="active_conversation_exists",
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=_row_id(active_conversation, "conversation"),
            call_event_id=call_event_id,
        )
    conversation_id = _row_id(conversation, "conversation")

    body = render_template(
        MISSED_CALL_TEMPLATE_KEY,
        client=client,
        lead_phone=from_phone,
    )

    outbound_status = "sent"
    message_sid: str | None = None
    try:
        message_sid = _send_sms(
            twilio_client,
            from_phone=to_phone,
            to_phone=from_phone,
            body=body,
            status_callback_url=status_callback_url,
        )
    except Exception as exc:
        outbound_status = "failed"
        print("Failed to send missed-call recovery SMS through Twilio:", repr(exc))

    outbound_message = messages_repo.insert_message(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        direction="outbound",
        from_phone=to_phone,
        to_phone=from_phone,
        body=body,
        twilio_message_sid=message_sid,
        template_key=MISSED_CALL_TEMPLATE_KEY,
        status=outbound_status,
    )
    outbound_message_id = _row_id(outbound_message, "message")

    conversations_repo.update_conversation(
        supabase,
        conversation_id=conversation_id,
        status="waiting_for_customer",
        current_state="awaiting_initial_reply",
        last_message_at=now_iso(),
    )

    return MissedCallRecoveryResult(
        processed=True,
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        call_event_id=call_event_id,
        outbound_message_id=outbound_message_id,
        outbound_status=outbound_status,
    )
