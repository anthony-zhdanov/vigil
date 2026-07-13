from __future__ import annotations

from supabase import Client

from ._shared import Row, first_row, require_supabase


def insert_message_media(
    supabase: Client | None,
    *,
    message_id: str,
    client_id: str,
    lead_id: str,
    twilio_media_url: str,
    content_type: str | None,
    storage_url: str | None = None,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("message_media")
        .insert(
            {
                "message_id": message_id,
                "client_id": client_id,
                "lead_id": lead_id,
                "twilio_media_url": twilio_media_url,
                "content_type": content_type,
                "storage_url": storage_url,
            }
        )
        .execute()
    )
    return first_row(response, "message media")

