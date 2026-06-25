from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase

_UNSET = object()


def get_active_conversation(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    channel: str = "sms",
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("conversations")
        .select("*")
        .eq("client_id", client_id)
        .eq("lead_id", lead_id)
        .eq("channel", channel)
        .neq("status", "closed")
        .is_("closed_at", "null")
        .order("last_message_at", desc=True)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "conversation")


def create_conversation(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    channel: str = "sms",
    status: str = "open",
    current_state: str = "awaiting_initial_reply",
    collected_info: dict[str, Any] | None = None,
    summary: str | None = None,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("conversations")
        .insert(
            {
                "client_id": client_id,
                "lead_id": lead_id,
                "channel": channel,
                "status": status,
                "current_state": current_state,
                "collected_info": collected_info or {},
                "summary": summary,
                "last_message_at": now_iso(),
            }
        )
        .execute()
    )
    return first_row(response, "conversation")


def get_or_create_active_conversation(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    channel: str = "sms",
) -> Row:
    db = require_supabase(supabase)
    existing = get_active_conversation(
        db, client_id=client_id, lead_id=lead_id, channel=channel
    )
    if existing is not None:
        return existing

    try:
        return create_conversation(
            db, client_id=client_id, lead_id=lead_id, channel=channel
        )
    except Exception:
        existing = get_active_conversation(
            db, client_id=client_id, lead_id=lead_id, channel=channel
        )
        if existing is not None:
            return existing
        raise


def update_conversation(
    supabase: Client | None,
    *,
    conversation_id: str,
    status: str | None = None,
    current_state: str | None = None,
    collected_info: dict[str, Any] | None = None,
    summary: str | None = None,
    last_message_at: str | None | object = _UNSET,
    closed_at: str | None | object = _UNSET,
) -> Row | None:
    db = require_supabase(supabase)
    payload: Row = {}
    if status is not None:
        payload["status"] = status
    if current_state is not None:
        payload["current_state"] = current_state
    if collected_info is not None:
        payload["collected_info"] = collected_info
    if summary is not None:
        payload["summary"] = summary
    if last_message_at is not _UNSET:
        payload["last_message_at"] = last_message_at
    if closed_at is not _UNSET:
        payload["closed_at"] = closed_at

    if not payload:
        response = (
            db.table("conversations")
            .select("*")
            .eq("id", conversation_id)
            .limit(1)
            .execute()
        )
        return maybe_first_row(response, "conversation")

    response = (
        db.table("conversations").update(payload).eq("id", conversation_id).execute()
    )
    return maybe_first_row(response, "conversation")


def close_conversation(
    supabase: Client | None,
    *,
    conversation_id: str,
    collected_info: dict[str, Any] | None = None,
    summary: str | None = None,
) -> Row | None:
    closed_at = now_iso()
    return update_conversation(
        supabase,
        conversation_id=conversation_id,
        status="closed",
        current_state="closed",
        collected_info=collected_info,
        summary=summary,
        last_message_at=closed_at,
        closed_at=closed_at,
    )
