from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, require_supabase


def insert_call_event(
    supabase: Client | None,
    *,
    client_id: str | None,
    lead_id: str | None,
    from_phone: str,
    to_phone: str,
    call_sid: str | None,
    call_status: str | None,
    raw_payload: dict[str, Any] | None,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("call_events")
        .insert(
            {
                "client_id": client_id,
                "lead_id": lead_id,
                "from_phone": from_phone,
                "to_phone": to_phone,
                "call_sid": call_sid,
                "call_status": call_status,
                "raw_payload": raw_payload,
            }
        )
        .execute()
    )
    return first_row(response, "call event")

