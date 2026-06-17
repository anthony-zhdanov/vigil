from __future__ import annotations

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def get_lead_by_client_phone(
    supabase: Client | None, client_id: str, phone_number: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("leads")
        .select("*")
        .eq("client_id", client_id)
        .eq("phone_number", phone_number)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "lead")


def upsert_lead(
    supabase: Client | None,
    *,
    client_id: str,
    phone_number: str,
    status: str = "missed_call",
    summary: str | None = None,
) -> Row:
    db = require_supabase(supabase)
    payload: Row = {
        "client_id": client_id,
        "phone_number": phone_number,
        "status": status,
        "updated_at": now_iso(),
    }
    if summary is not None:
        payload["summary"] = summary

    response = (
        db.table("leads")
        .upsert(payload, on_conflict="client_id,phone_number")
        .execute()
    )
    return first_row(response, "lead")


def update_lead_status(
    supabase: Client | None,
    *,
    lead_id: str,
    status: str,
    summary: str | None = None,
) -> Row | None:
    db = require_supabase(supabase)
    payload: Row = {"status": status, "updated_at": now_iso()}
    if summary is not None:
        payload["summary"] = summary

    response = db.table("leads").update(payload).eq("id", lead_id).execute()
    return maybe_first_row(response, "lead")

