from __future__ import annotations

from typing import Any

from app.decision_tree.classifier import classify_plumbing_sms
from app.decision_tree.contract import DecisionAction, DecisionResult
from app.decision_tree.trees.base.plumbing_default import TREE_KEY, TREE_VERSION


def _merged_collected_info(
    existing: dict[str, Any], classifier_output: Any
) -> dict[str, Any]:
    collected = dict(existing or {})
    if classifier_output.location:
        collected["location"] = classifier_output.location
    if classifier_output.job_type:
        collected["job_type"] = classifier_output.job_type
    if classifier_output.urgency:
        if collected.get("urgency") != "emergency":
            collected["urgency"] = classifier_output.urgency
    if classifier_output.summary:
        collected["latest_summary"] = classifier_output.summary
    return collected


def _conversation_summary(
    collected_info: dict[str, Any],
    classifier_output: Any,
) -> str:
    parts: list[str] = []
    if collected_info.get("location"):
        parts.append(f"Location: {collected_info['location']}")
    if collected_info.get("job_type"):
        parts.append(f"Service: {collected_info['job_type']}")
    if collected_info.get("urgency"):
        parts.append(f"Urgency: {collected_info['urgency']}")

    latest = classifier_output.summary or collected_info.get("latest_summary")
    if latest:
        parts.append(f"Latest reply: {latest}")

    return "; ".join(parts)


def _result(
    *,
    classifier_output: Any,
    matched_node_key: str,
    actions: list[DecisionAction],
    conversation_state: str,
    conversation_status: str,
    collected_info: dict[str, Any],
    summary: str,
) -> DecisionResult:
    return DecisionResult(
        tree_key=TREE_KEY,
        tree_version=TREE_VERSION,
        classifier_output=classifier_output,
        matched_node_key=matched_node_key,
        actions=actions,
        conversation_state=conversation_state,
        conversation_status=conversation_status,
        collected_info=collected_info,
        summary=summary,
    )


def run_plumbing_decision_tree(
    *,
    message_body: str,
    current_state: str,
    collected_info: dict[str, Any] | None = None,
    booking_mode: str = "disabled",
) -> DecisionResult:
    existing_info = dict(collected_info or {})
    state = current_state or "awaiting_initial_reply"
    classifier_output = classify_plumbing_sms(message_body, current_state=state)
    next_info = _merged_collected_info(existing_info, classifier_output)
    summary = _conversation_summary(next_info, classifier_output)

    if classifier_output.intent == "opt_out":
        return _result(
            classifier_output=classifier_output,
            matched_node_key="opt_out",
            actions=[
                DecisionAction("send_sms_template", template_key="opt_out_confirm"),
                DecisionAction(
                    "create_opt_out",
                    opt_out_reason="stop",
                ),
                DecisionAction("mark_lead_status", lead_status="opted_out"),
                DecisionAction("close_conversation"),
            ],
            conversation_state="closed",
            conversation_status="closed",
            collected_info=next_info,
            summary=summary,
        )

    if classifier_output.intent == "wrong_number":
        return _result(
            classifier_output=classifier_output,
            matched_node_key="wrong_number",
            actions=[
                DecisionAction("send_sms_template", template_key="wrong_number_confirm"),
                DecisionAction("create_opt_out", opt_out_reason="wrong_number"),
                DecisionAction("mark_lead_status", lead_status="wrong_number"),
                DecisionAction("close_conversation"),
            ],
            conversation_state="closed",
            conversation_status="closed",
            collected_info=next_info,
            summary=summary,
        )

    if classifier_output.intent == "no_longer_needed":
        return _result(
            classifier_output=classifier_output,
            matched_node_key="no_longer_needed",
            actions=[
                DecisionAction("send_sms_template", template_key="no_longer_needed"),
                DecisionAction("mark_lead_status", lead_status="lost"),
                DecisionAction("close_conversation"),
            ],
            conversation_state="closed",
            conversation_status="closed",
            collected_info=next_info,
            summary=summary,
        )

    if state == "awaiting_customer_name":
        customer_name = " ".join(message_body.strip().split())
        if len(customer_name.split()) < 2 or any(char.isdigit() for char in customer_name):
            return _result(
                classifier_output=classifier_output,
                matched_node_key="collect_customer_name",
                actions=[
                    DecisionAction(
                        "send_sms_template", template_key="request_customer_name"
                    )
                ],
                conversation_state="awaiting_customer_name",
                conversation_status="waiting_for_customer",
                collected_info=next_info,
                summary=summary,
            )
        next_info["customer_name"] = customer_name
        return _result(
            classifier_output=classifier_output,
            matched_node_key="find_booking_slots",
            actions=[DecisionAction("offer_booking_slots", booking_page_index=0)],
            conversation_state="finding_availability",
            conversation_status="waiting_for_system",
            collected_info=next_info,
            summary=summary,
        )

    if state == "awaiting_slot_selection":
        normalized_reply = message_body.strip().lower()
        if any(word in normalized_reply for word in ("cancel", "reschedule", "change")):
            return _result(
                classifier_output=classifier_output,
                matched_node_key="booking_change_handoff",
                actions=[DecisionAction("booking_handoff")],
                conversation_state="booking_handoff",
                conversation_status="waiting_for_owner",
                collected_info=next_info,
                summary=summary,
            )
        if normalized_reply == "more":
            page_index = int(next_info.get("booking_page_index") or 0) + 1
            return _result(
                classifier_output=classifier_output,
                matched_node_key="more_booking_slots",
                actions=[
                    DecisionAction("offer_booking_slots", booking_page_index=page_index)
                ],
                conversation_state="finding_availability",
                conversation_status="waiting_for_system",
                collected_info=next_info,
                summary=summary,
            )
        if normalized_reply in {"1", "2", "3"}:
            return _result(
                classifier_output=classifier_output,
                matched_node_key="create_booking",
                actions=[
                    DecisionAction(
                        "create_booking",
                        booking_slot_index=int(normalized_reply) - 1,
                    )
                ],
                conversation_state="booking",
                conversation_status="waiting_for_system",
                collected_info=next_info,
                summary=summary,
            )
        return _result(
            classifier_output=classifier_output,
            matched_node_key="invalid_slot_selection",
            actions=[
                DecisionAction(
                    "send_sms_template", template_key="invalid_slot_selection"
                )
            ],
            conversation_state="awaiting_slot_selection",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            summary=summary,
        )

    if state == "booked" and message_body.strip() in {"1", "2", "3"}:
        return _result(
            classifier_output=classifier_output,
            matched_node_key="repeat_booking_confirmation",
            actions=[
                DecisionAction(
                    "create_booking",
                    booking_slot_index=int(message_body.strip()) - 1,
                )
            ],
            conversation_state="booking",
            conversation_status="waiting_for_system",
            collected_info=next_info,
            summary=summary,
        )

    if state in {"booked", "booking", "finding_availability", "booking_handoff"}:
        return _result(
            classifier_output=classifier_output,
            matched_node_key="booking_followup_handoff",
            actions=[DecisionAction("booking_handoff")],
            conversation_state="booking_handoff",
            conversation_status="waiting_for_owner",
            collected_info=next_info,
            summary=summary,
        )

    if classifier_output.intent == "unclear" and state == "awaiting_initial_reply":
        return _result(
            classifier_output=classifier_output,
            matched_node_key="fallback_unclear",
            actions=[
                DecisionAction("send_sms_template", template_key="clarification"),
                DecisionAction("mark_lead_status", lead_status="needs_clarification"),
            ],
            conversation_state="awaiting_initial_reply",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            summary=summary,
        )

    actions: list[DecisionAction] = []
    urgency = next_info.get("urgency")
    priority = "urgent" if urgency == "emergency" else "normal"
    emergency_already_notified = (
        urgency == "emergency" and bool(next_info.get("emergency_notified"))
    )

    if urgency == "emergency" and not next_info.get("emergency_notified"):
        actions.append(DecisionAction("notify_owner", notification_priority="urgent"))
        next_info["emergency_notified"] = True
        emergency_already_notified = True
        if not next_info.get("location"):
            actions.extend(
                [
                    DecisionAction("send_sms_template", template_key="emergency_ack"),
                    DecisionAction("mark_lead_status", lead_status="emergency"),
                ]
            )
            return _result(
                classifier_output=classifier_output,
                matched_node_key="emergency_collect_location",
                actions=actions,
                conversation_state="awaiting_location",
                conversation_status="waiting_for_customer",
                collected_info=next_info,
                summary=summary,
            )

    if not next_info.get("location"):
        lead_status = "emergency" if urgency == "emergency" else "needs_location"
        actions.extend(
            [
                DecisionAction("send_sms_template", template_key="request_location"),
                DecisionAction("mark_lead_status", lead_status=lead_status),
            ]
        )
        return _result(
            classifier_output=classifier_output,
            matched_node_key="collect_location",
            actions=actions,
            conversation_state="awaiting_location",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            summary=summary,
        )

    if not next_info.get("job_type"):
        lead_status = "emergency" if urgency == "emergency" else "needs_job_type"
        actions.extend(
            [
                DecisionAction("send_sms_template", template_key="request_job_type"),
                DecisionAction("mark_lead_status", lead_status=lead_status),
            ]
        )
        return _result(
            classifier_output=classifier_output,
            matched_node_key="collect_job_type",
            actions=actions,
            conversation_state="awaiting_job_type",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            summary=summary,
        )

    if not next_info.get("urgency"):
        actions.extend(
            [
                DecisionAction("send_sms_template", template_key="request_urgency"),
                DecisionAction("mark_lead_status", lead_status="needs_urgency"),
            ]
        )
        return _result(
            classifier_output=classifier_output,
            matched_node_key="collect_urgency",
            actions=actions,
            conversation_state="awaiting_urgency",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            summary=summary,
        )

    if urgency != "emergency" and booking_mode == "live":
        actions.extend(
            [
                DecisionAction(
                    "send_sms_template", template_key="request_customer_name"
                ),
                DecisionAction("mark_lead_status", lead_status="needs_customer_name"),
            ]
        )
        return _result(
            classifier_output=classifier_output,
            matched_node_key="collect_customer_name",
            actions=actions,
            conversation_state="awaiting_customer_name",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            summary=summary,
        )

    if urgency != "emergency" and booking_mode == "shadow":
        actions.append(DecisionAction("offer_booking_slots", booking_page_index=0))

    actions.append(DecisionAction("send_sms_template", template_key="handoff_to_team"))
    if not any(action.type == "notify_owner" for action in actions) and not (
        priority == "urgent" and emergency_already_notified
    ):
        actions.append(DecisionAction("notify_owner", notification_priority=priority))
    actions.extend(
        [
            DecisionAction(
                "mark_lead_status",
                lead_status="emergency" if urgency == "emergency" else "needs_owner_call",
            ),
            DecisionAction("close_conversation"),
        ]
    )
    return _result(
        classifier_output=classifier_output,
        matched_node_key="lead_complete_handoff",
        actions=actions,
        conversation_state="closed",
        conversation_status="closed",
        collected_info=next_info,
        summary=summary,
    )
