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

    if urgency == "emergency" and not next_info.get("emergency_notified"):
        actions.append(DecisionAction("notify_owner", notification_priority="urgent"))
        next_info["emergency_notified"] = True
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

    actions.append(DecisionAction("send_sms_template", template_key="handoff_to_team"))
    if not any(action.type == "notify_owner" for action in actions):
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
