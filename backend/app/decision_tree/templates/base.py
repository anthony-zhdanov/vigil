from __future__ import annotations

from typing import Any

from app.decision_tree.contract import ClassifierOutput


DEFAULT_TEMPLATES = {
    "missed_call_initial": (
        "Hi, thanks for calling {business_name}. We could not answer right away. "
        "Do you still need plumbing help? Reply with what is going on and we will help route it."
    ),
    "opt_out_confirm": "No problem. We will not text this number again.",
    "wrong_number_confirm": "Sorry about that. We will not text this number again.",
    "no_longer_needed": "Understood. Glad you are all set. We will close this out.",
    "clarification": (
        "Sorry, I did not catch that. Do you still need plumbing help? "
        "If yes, reply with what is going on."
    ),
    "request_location": "Thanks. What address or nearest intersection is this for?",
    "request_job_type": (
        "Got it. What kind of plumbing work is it: leak, drain, water heater, "
        "fixture/install, or a quote for later?"
    ),
    "request_urgency": (
        "How urgent is it: emergency now, today, this week, or just looking for a quote?"
    ),
    "emergency_ack": (
        "We flagged this as urgent and alerted the team. Please reply with the service "
        "address if you have not already."
    ),
    "handoff_to_team": (
        "Thanks. We have the details and passed this to the team. Someone will follow up shortly."
    ),
    "owner_notification": (
        "Vigil lead for {business_name}\n"
        "Lead: {lead_phone}\n"
        "Priority: {priority}\n"
        "Location: {location}\n"
        "Service: {job_type}\n"
        "Urgency: {urgency}\n"
        "Latest: {latest_message}\n"
        "Summary: {summary}"
    ),
}


def _value(value: Any, fallback: str = "Unknown") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _clip(value: str, max_length: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3].rstrip()}..."


def render_template(
    template_key: str,
    *,
    client: dict[str, Any],
    lead_phone: str | None = None,
    collected_info: dict[str, Any] | None = None,
    classifier_output: ClassifierOutput | None = None,
    latest_message: str = "",
    priority: str = "normal",
    summary: str | None = None,
) -> str:
    template = DEFAULT_TEMPLATES[template_key]
    info = collected_info or {}
    business_name = _value(client.get("business_name"), "the team")
    rendered_summary = summary or (classifier_output.summary if classifier_output else "")

    return template.format(
        business_name=business_name,
        lead_phone=_value(lead_phone),
        location=_value(info.get("location")),
        job_type=_value(info.get("job_type")),
        urgency=_value(info.get("urgency")),
        latest_message=_clip(latest_message or ""),
        priority=priority.upper(),
        summary=_clip(rendered_summary or "No summary yet."),
    )
