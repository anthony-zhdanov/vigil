from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, parse_datetime, require_supabase


def insert_message(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    direction: str,
    from_phone: str,
    to_phone: str,
    body: str,
    twilio_message_sid: str | None,
    template_key: str | None,
    status: str,
    conversation_id: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> Row:
    db = require_supabase(supabase)
    payload: Row = {
        "client_id": client_id,
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "direction": direction,
        "from_phone": from_phone,
        "to_phone": to_phone,
        "body": body,
        "twilio_message_sid": twilio_message_sid,
        "template_key": template_key,
        "status": status,
        "raw_payload": raw_payload,
    }
    response = db.table("messages").insert(payload).execute()
    return first_row(response, "message")


def get_message_by_twilio_sid(
    supabase: Client | None, twilio_message_sid: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("messages")
        .select("*")
        .eq("twilio_message_sid", twilio_message_sid)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "message")


def recent_recovery_sms_exists(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    duplicate_suppression_minutes: int,
) -> bool:
    db = require_supabase(supabase)
    response = (
        db.table("messages")
        .select("created_at")
        .eq("client_id", client_id)
        .eq("lead_id", lead_id)
        .eq("direction", "outbound")
        .eq("template_key", "missed_call_initial")
        .eq("status", "sent")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    message = maybe_first_row(response, "message")
    if message is None:
        return False

    created_at = parse_datetime(message.get("created_at"))
    if created_at is None:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=duplicate_suppression_minutes
    )
    return created_at >= cutoff


def update_message_status(
    supabase: Client | None,
    *,
    twilio_message_sid: str,
    status: str,
    raw_payload: dict[str, Any] | None = None,
) -> Row | None:
    db = require_supabase(supabase)
    payload: Row = {"status": status}
    if raw_payload is not None:
        payload["raw_payload"] = raw_payload

    response = (
        db.table("messages")
        .update(payload)
        .eq("twilio_message_sid", twilio_message_sid)
        .execute()
    )
    return maybe_first_row(response, "message")

