from __future__ import annotations

from datetime import datetime
from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def get_pending_offer(
    supabase: Client | None, *, conversation_id: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_slot_offers")
        .select("*")
        .eq("conversation_id", conversation_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking slot offer")


def replace_pending_offer(
    supabase: Client | None, *, conversation_id: str
) -> None:
    db = require_supabase(supabase)
    db.table("booking_slot_offers").update(
        {"status": "replaced", "updated_at": now_iso()}
    ).eq("conversation_id", conversation_id).eq("status", "pending").execute()


def create_offer(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    conversation_id: str,
    connection_id: str,
    service_id: str,
    resource_id: str,
    slots: list[dict[str, Any]],
    page_index: int,
    expires_at: datetime,
) -> Row:
    db = require_supabase(supabase)
    replace_pending_offer(db, conversation_id=conversation_id)
    response = (
        db.table("booking_slot_offers")
        .insert(
            {
                "client_id": client_id,
                "lead_id": lead_id,
                "conversation_id": conversation_id,
                "connection_id": connection_id,
                "service_id": service_id,
                "resource_id": resource_id,
                "slots": slots,
                "page_index": page_index,
                "expires_at": expires_at.isoformat(),
                "status": "pending",
            }
        )
        .execute()
    )
    return first_row(response, "booking slot offer")


def update_offer(
    supabase: Client | None, *, offer_id: str, values: dict[str, Any]
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_slot_offers")
        .update({**values, "updated_at": now_iso()})
        .eq("id", offer_id)
        .execute()
    )
    return maybe_first_row(response, "booking slot offer")
