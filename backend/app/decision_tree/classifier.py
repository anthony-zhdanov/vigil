from __future__ import annotations

import re

from app.decision_tree.contract import ClassifierOutput
from app.decision_tree.trees.base.plumbing_default import (
    GTA_LOCATION_HINTS,
    JOB_TYPE_KEYWORDS,
    NO_LONGER_NEEDED_EXACT,
    NO_LONGER_NEEDED_PHRASES,
    OPT_OUT_EXACT,
    OPT_OUT_PHRASES,
    URGENCY_KEYWORDS,
    WRONG_NUMBER_PHRASES,
    YES_PHRASES,
)


POSTAL_CODE_RE = re.compile(
    r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\s?\d[ABCEGHJ-NPRSTV-Z]\d\b",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.' -]{2,80}\s+"
    r"(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|"
    r"crescent|cres|lane|ln|court|ct|way|parkway|pkwy|highway|hwy)\b",
    re.IGNORECASE,
)
INTERSECTION_RE = re.compile(
    r"\b(?:near|around|at|by|intersection of)\s+([A-Za-z0-9.' -]{3,80})",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _contains(normalized: str, phrase: str) -> bool:
    if " " in phrase:
        return phrase in normalized
    return bool(re.search(rf"\b{re.escape(phrase)}\b", normalized))


def _matches_any(normalized: str, phrases: set[str] | tuple[str, ...]) -> bool:
    return any(_contains(normalized, phrase) for phrase in phrases)


def _is_short_affirmation(normalized: str) -> bool:
    return normalized in YES_PHRASES or _matches_any(normalized, YES_PHRASES)


def _clip(value: str, max_length: int = 180) -> str:
    text = " ".join(value.split())
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3].rstrip()}..."


def _extract_location(message_body: str, normalized: str, current_state: str) -> str | None:
    address = ADDRESS_RE.search(message_body)
    if address:
        return address.group(0).strip()

    postal_code = POSTAL_CODE_RE.search(message_body)
    if postal_code:
        return postal_code.group(0).strip().upper()

    intersection = INTERSECTION_RE.search(message_body)
    if intersection:
        return intersection.group(1).strip(" .")

    for location in GTA_LOCATION_HINTS:
        if _contains(normalized, location):
            return location.title()

    if current_state == "awaiting_location" and len(normalized) > 3:
        return message_body.strip()

    return None


def _extract_job_type(message_body: str, normalized: str, current_state: str) -> str | None:
    for job_type, keywords in JOB_TYPE_KEYWORDS.items():
        if any(_contains(normalized, keyword) for keyword in keywords):
            return job_type

    if current_state == "awaiting_job_type" and len(normalized) > 2:
        return _clip(message_body, 80)

    return None


def _extract_urgency(message_body: str, normalized: str, current_state: str) -> str | None:
    for urgency, keywords in URGENCY_KEYWORDS.items():
        if any(_contains(normalized, keyword) for keyword in keywords):
            return urgency

    if current_state == "awaiting_urgency" and len(normalized) > 2:
        return _clip(message_body, 80)

    return None


def classify_plumbing_sms(
    message_body: str,
    *,
    current_state: str = "awaiting_initial_reply",
) -> ClassifierOutput:
    normalized = _normalize(message_body)
    if not normalized:
        return ClassifierOutput(
            intent="unclear",
            confidence=0.2,
            summary="Customer sent an empty SMS reply.",
            reason="empty_message",
        )

    if normalized in OPT_OUT_EXACT or _matches_any(normalized, OPT_OUT_PHRASES):
        return ClassifierOutput(
            intent="opt_out",
            confidence=0.98,
            summary="Customer asked not to receive more texts.",
            reason="opt_out_keyword",
        )

    if _matches_any(normalized, WRONG_NUMBER_PHRASES):
        return ClassifierOutput(
            intent="wrong_number",
            confidence=0.95,
            summary="Customer says this is the wrong number.",
            reason="wrong_number_phrase",
        )

    if normalized in NO_LONGER_NEEDED_EXACT or _matches_any(
        normalized, NO_LONGER_NEEDED_PHRASES
    ):
        return ClassifierOutput(
            intent="no_longer_needed",
            confidence=0.9,
            summary="Customer says they no longer need help.",
            reason="no_longer_needed_phrase",
        )

    location = _extract_location(message_body, normalized, current_state)
    job_type = _extract_job_type(message_body, normalized, current_state)
    urgency = _extract_urgency(message_body, normalized, current_state)
    lead_signal = bool(
        location
        or job_type
        or urgency
        or _is_short_affirmation(normalized)
        or current_state != "awaiting_initial_reply"
    )

    if lead_signal:
        details = [f'Customer said: "{_clip(message_body)}"']
        if location:
            details.append(f"location={location}")
        if job_type:
            details.append(f"job_type={job_type}")
        if urgency:
            details.append(f"urgency={urgency}")

        return ClassifierOutput(
            intent="lead",
            confidence=0.86 if (location or job_type or urgency) else 0.72,
            summary="; ".join(details),
            urgency=urgency,
            job_type=job_type,
            location=location,
            lead_signal=True,
            reason="lead_signal_detected",
        )

    return ClassifierOutput(
        intent="unclear",
        confidence=0.35,
        summary=f'Customer reply was unclear: "{_clip(message_body)}"',
        reason="no_route_matched",
    )
