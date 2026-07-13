from __future__ import annotations

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, require_supabase


def get_opt_out(
    supabase: Client | None, *, client_id: str, phone_number: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("opt_outs")
        .select("*")
        .eq("client_id", client_id)
        .eq("phone_number", phone_number)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "opt-out")


def is_opted_out(
    supabase: Client | None, *, client_id: str, phone_number: str
) -> bool:
    return get_opt_out(supabase, client_id=client_id, phone_number=phone_number) is not None


def create_opt_out(
    supabase: Client | None,
    *,
    client_id: str,
    phone_number: str,
    lead_id: str | None = None,
    reason: str = "manual",
    source: str | None = None,
    notes: str | None = None,
) -> Row:
    db = require_supabase(supabase)
    existing = get_opt_out(db, client_id=client_id, phone_number=phone_number)
    if existing is not None:
        return existing

    payload: Row = {
        "client_id": client_id,
        "lead_id": lead_id,
        "phone_number": phone_number,
        "reason": reason,
        "source": source,
        "notes": notes,
    }
    try:
        response = db.table("opt_outs").insert(payload).execute()
        return first_row(response, "opt-out")
    except Exception:
        existing = get_opt_out(db, client_id=client_id, phone_number=phone_number)
        if existing is not None:
            return existing
        raise

