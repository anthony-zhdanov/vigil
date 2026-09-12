from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def get_by_conversation(
    supabase: Client | None, *, conversation_id: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("bookings")
        .select("*")
        .eq("conversation_id", conversation_id)
        .in_("status", ["creating", "confirmed", "unknown"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking")


def get_by_idempotency_key(
    supabase: Client | None, *, idempotency_key: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("bookings")
        .select("*")
        .eq("idempotency_key", idempotency_key)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking")


def create_booking(supabase: Client | None, *, values: dict[str, Any]) -> Row:
    db = require_supabase(supabase)
    response = db.table("bookings").insert(values).execute()
    return first_row(response, "booking")


def update_booking(
    supabase: Client | None, *, booking_id: str, values: dict[str, Any]
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("bookings")
        .update({**values, "updated_at": now_iso()})
        .eq("id", booking_id)
        .execute()
    )
    return maybe_first_row(response, "booking")
