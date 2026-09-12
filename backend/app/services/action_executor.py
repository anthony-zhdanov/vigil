from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from supabase import Client

from app.decision_tree.contract import DecisionAction, DecisionResult
from app.decision_tree.templates.base import render_template
from app.repositories import conversations as conversations_repo
from app.repositories import leads as leads_repo
from app.repositories import messages as messages_repo
from app.repositories import opt_outs as opt_outs_repo
from app.repositories import owner_notifications as owner_notifications_repo
from app.services.booking_workflow import BookingActionOutcome, BookingOrchestrator


@dataclass(frozen=True, slots=True)
class ExecutedAction:
    action_type: str
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ActionExecutionResult:
    actions: list[ExecutedAction]
    conversation_state: str
    conversation_status: str
    collected_info: dict[str, Any]
    summary: str


def _row_id(row: dict[str, Any], context: str) -> str:
    row_id = row.get("id")
    if row_id is None:
        raise RuntimeError(f"{context} row is missing id")
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


def _phone_digits(value: str | None) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _owner_recipient(client: dict[str, Any]) -> str | None:
    value = client.get("owner_phone")
    if value is None:
        return None

    recipient = str(value).strip()
    return recipient or None


def _execute_send_sms_template(
    supabase: Client | None,
    twilio_client: Any | None,
    *,
    action: DecisionAction,
    result: DecisionResult,
    client: dict[str, Any],
    lead: dict[str, Any],
    conversation: dict[str, Any],
    customer_phone: str,
    twilio_number: str,
    latest_message: str,
    status_callback_url: str | None,
) -> ExecutedAction:
    if not action.template_key:
        raise ValueError("send_sms_template action is missing template_key")

    client_id = _row_id(client, "client")
    lead_id = _row_id(lead, "lead")
    conversation_id = _row_id(conversation, "conversation")

    body = render_template(
        action.template_key,
        client=client,
        lead_phone=customer_phone,
        collected_info=result.collected_info,
        classifier_output=result.classifier_output,
        latest_message=latest_message,
        summary=result.summary,
    )

    if opt_outs_repo.is_opted_out(
        supabase, client_id=client_id, phone_number=customer_phone
    ):
        messages_repo.insert_message(
            supabase,
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            direction="outbound",
            from_phone=twilio_number,
            to_phone=customer_phone,
            body=body,
            twilio_message_sid=None,
            template_key=action.template_key,
            status="suppressed_opt_out",
        )
        return ExecutedAction(
            action_type=action.type,
            status="suppressed_opt_out",
            detail=action.template_key,
        )

    status = "sent"
    message_sid: str | None = None
    try:
        message_sid = _send_sms(
            twilio_client,
            from_phone=twilio_number,
            to_phone=customer_phone,
            body=body,
            status_callback_url=status_callback_url,
        )
    except Exception as exc:
        status = "failed"
        print("Failed to send SMS through Twilio:", repr(exc))

    messages_repo.insert_message(
        supabase,
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        direction="outbound",
        from_phone=twilio_number,
        to_phone=customer_phone,
        body=body,
        twilio_message_sid=message_sid,
        template_key=action.template_key,
        status=status,
    )
    return ExecutedAction(
        action_type=action.type,
        status=status,
        detail=action.template_key,
    )


def _execute_mark_lead_status(
    supabase: Client | None,
    *,
    action: DecisionAction,
    result: DecisionResult,
    lead: dict[str, Any],
) -> ExecutedAction:
    if not action.lead_status:
        raise ValueError("mark_lead_status action is missing lead_status")

    leads_repo.update_lead_status(
        supabase,
        lead_id=_row_id(lead, "lead"),
        status=action.lead_status,
        summary=result.summary,
    )
    return ExecutedAction(
        action_type=action.type,
        status="updated",
        detail=action.lead_status,
    )


def _execute_create_opt_out(
    supabase: Client | None,
    *,
    action: DecisionAction,
    client: dict[str, Any],
    lead: dict[str, Any],
    customer_phone: str,
) -> ExecutedAction:
    reason = action.opt_out_reason or "decision_tree"
    opt_outs_repo.create_opt_out(
        supabase,
        client_id=_row_id(client, "client"),
        lead_id=_row_id(lead, "lead"),
        phone_number=customer_phone,
        reason=reason,
        source="sms_decision_tree",
    )
    return ExecutedAction(
        action_type=action.type,
        status="created",
        detail=reason,
    )


def _execute_notify_owner(
    supabase: Client | None,
    twilio_client: Any | None,
    *,
    action: DecisionAction,
    result: DecisionResult,
    client: dict[str, Any],
    lead: dict[str, Any],
    conversation: dict[str, Any],
    customer_phone: str,
    twilio_number: str,
    latest_message: str,
) -> ExecutedAction:
    priority = action.notification_priority or "normal"
    recipient_phone = _owner_recipient(client)
    body = render_template(
        "owner_notification",
        client=client,
        lead_phone=customer_phone,
        collected_info=result.collected_info,
        classifier_output=result.classifier_output,
        latest_message=latest_message,
        priority=priority,
        summary=result.summary,
    )
    status = "sent"
    raw_response: dict[str, Any] | None = None

    if recipient_phone is None:
        status = "failed"
        raw_response = {"error": "missing_owner_phone"}
    elif _phone_digits(recipient_phone) == _phone_digits(customer_phone):
        status = "failed"
        raw_response = {"error": "owner_recipient_matches_customer"}
    else:
        try:
            message_sid = _send_sms(
                twilio_client,
                from_phone=twilio_number,
                to_phone=recipient_phone,
                body=body,
            )
            raw_response = {"twilio_message_sid": message_sid}
        except Exception as exc:
            status = "failed"
            raw_response = {"error": repr(exc)}
            print("Failed to send owner notification through Twilio:", repr(exc))

    owner_notifications_repo.insert_owner_notification(
        supabase,
        client_id=_row_id(client, "client"),
        lead_id=_row_id(lead, "lead"),
        conversation_id=_row_id(conversation, "conversation"),
        channel="sms",
        recipient=recipient_phone,
        priority=priority,
        body=body,
        status=status,
        raw_response=raw_response,
    )
    return ExecutedAction(
        action_type=action.type,
        status=status,
        detail=priority,
    )


def _execute_close_conversation(
    supabase: Client | None,
    *,
    result: DecisionResult,
    conversation: dict[str, Any],
) -> ExecutedAction:
    return ExecutedAction(action_type="close_conversation", status="closed")


def _execute_booking_outcome(
    supabase: Client | None,
    twilio_client: Any | None,
    *,
    outcome: BookingActionOutcome,
    source_action: DecisionAction,
    result: DecisionResult,
    client: dict[str, Any],
    lead: dict[str, Any],
    conversation: dict[str, Any],
    customer_phone: str,
    twilio_number: str,
    latest_message: str,
    status_callback_url: str | None,
) -> list[ExecutedAction]:
    if outcome.kind == "shadow":
        return [
            ExecutedAction(
                action_type=source_action.type,
                status="shadow",
                detail=outcome.detail,
            )
        ]

    effective_result = replace(result, collected_info=outcome.collected_info)
    executed: list[ExecutedAction] = []
    if outcome.template_key:
        executed.append(
            _execute_send_sms_template(
                supabase,
                twilio_client,
                action=DecisionAction(
                    "send_sms_template", template_key=outcome.template_key
                ),
                result=effective_result,
                client=client,
                lead=lead,
                conversation=conversation,
                customer_phone=customer_phone,
                twilio_number=twilio_number,
                latest_message=latest_message,
                status_callback_url=status_callback_url,
            )
        )

    if outcome.kind == "confirmed":
        executed.append(
            _execute_mark_lead_status(
                supabase,
                action=DecisionAction(
                    "mark_lead_status", lead_status="appointment_booked"
                ),
                result=effective_result,
                lead=lead,
            )
        )
    elif outcome.kind in {"handoff", "unknown"}:
        executed.append(
            _execute_notify_owner(
                supabase,
                twilio_client,
                action=DecisionAction(
                    "notify_owner", notification_priority="normal"
                ),
                result=effective_result,
                client=client,
                lead=lead,
                conversation=conversation,
                customer_phone=customer_phone,
                twilio_number=twilio_number,
                latest_message=latest_message,
            )
        )
        executed.append(
            _execute_mark_lead_status(
                supabase,
                action=DecisionAction(
                    "mark_lead_status", lead_status="needs_owner_call"
                ),
                result=effective_result,
                lead=lead,
            )
        )

    executed.append(
        ExecutedAction(
            action_type=source_action.type,
            status=outcome.kind,
            detail=outcome.detail,
        )
    )
    return executed


def execute_actions(
    supabase: Client | None,
    twilio_client: Any | None,
    *,
    result: DecisionResult,
    client: dict[str, Any],
    lead: dict[str, Any],
    conversation: dict[str, Any],
    inbound_message: dict[str, Any],
    customer_phone: str,
    twilio_number: str,
    status_callback_url: str | None = None,
    booking_orchestrator: BookingOrchestrator | None = None,
) -> ActionExecutionResult:
    latest_message = str(inbound_message.get("body") or "")
    executed: list[ExecutedAction] = []
    final_state = result.conversation_state
    final_status = result.conversation_status
    final_info = dict(result.collected_info)

    for action in result.actions:
        if action.type == "send_sms_template":
            executed.append(
                _execute_send_sms_template(
                    supabase,
                    twilio_client,
                    action=action,
                    result=result,
                    client=client,
                    lead=lead,
                    conversation=conversation,
                    customer_phone=customer_phone,
                    twilio_number=twilio_number,
                    latest_message=latest_message,
                    status_callback_url=status_callback_url,
                )
            )
        elif action.type == "mark_lead_status":
            executed.append(
                _execute_mark_lead_status(
                    supabase, action=action, result=result, lead=lead
                )
            )
        elif action.type == "create_opt_out":
            executed.append(
                _execute_create_opt_out(
                    supabase,
                    action=action,
                    client=client,
                    lead=lead,
                    customer_phone=customer_phone,
                )
            )
        elif action.type == "notify_owner":
            executed.append(
                _execute_notify_owner(
                    supabase,
                    twilio_client,
                    action=action,
                    result=result,
                    client=client,
                    lead=lead,
                    conversation=conversation,
                    customer_phone=customer_phone,
                    twilio_number=twilio_number,
                    latest_message=latest_message,
                )
            )
        elif action.type == "close_conversation":
            executed.append(
                _execute_close_conversation(
                    supabase, result=result, conversation=conversation
                )
            )
        elif action.type in {"offer_booking_slots", "create_booking", "booking_handoff"}:
            if booking_orchestrator is None:
                outcome = BookingActionOutcome(
                    kind="handoff",
                    template_key="booking_handoff",
                    conversation_state="booking_handoff",
                    conversation_status="waiting_for_owner",
                    collected_info=final_info,
                    detail="booking_runtime_unavailable",
                )
            elif action.type == "offer_booking_slots":
                outcome = booking_orchestrator.offer_slots(
                    client_id=_row_id(client, "client"),
                    lead_id=_row_id(lead, "lead"),
                    conversation_id=_row_id(conversation, "conversation"),
                    collected_info=final_info,
                    page_index=action.booking_page_index or 0,
                )
            elif action.type == "create_booking":
                outcome = booking_orchestrator.create_booking(
                    client_id=_row_id(client, "client"),
                    lead_id=_row_id(lead, "lead"),
                    conversation_id=_row_id(conversation, "conversation"),
                    customer_phone=customer_phone,
                    collected_info=final_info,
                    slot_index=action.booking_slot_index or 0,
                )
            else:
                outcome = BookingActionOutcome(
                    kind="handoff",
                    template_key="booking_handoff",
                    conversation_state="booking_handoff",
                    conversation_status="waiting_for_owner",
                    collected_info=final_info,
                    detail="customer_requested_booking_change",
                )
            executed.extend(
                _execute_booking_outcome(
                    supabase,
                    twilio_client,
                    outcome=outcome,
                    source_action=action,
                    result=result,
                    client=client,
                    lead=lead,
                    conversation=conversation,
                    customer_phone=customer_phone,
                    twilio_number=twilio_number,
                    latest_message=latest_message,
                    status_callback_url=status_callback_url,
                )
            )
            if outcome.kind != "shadow":
                final_state = outcome.conversation_state
                final_status = outcome.conversation_status
                final_info = dict(outcome.collected_info)
        else:
            raise ValueError(f"Unsupported decision action: {action.type}")

    return ActionExecutionResult(
        actions=executed,
        conversation_state=final_state,
        conversation_status=final_status,
        collected_info=final_info,
        summary=result.summary,
    )
