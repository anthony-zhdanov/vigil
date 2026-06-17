from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, require_supabase


def insert_owner_notification(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    conversation_id: str | None,
    channel: str,
    recipient: str | None,
    priority: str,
    body: str,
    status: str,
    raw_response: dict[str, Any] | None = None,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("owner_notifications")
        .insert(
            {
                "client_id": client_id,
                "lead_id": lead_id,
                "conversation_id": conversation_id,
                "channel": channel,
                "recipient": recipient,
                "priority": priority,
                "body": body,
                "status": status,
                "raw_response": raw_response,
            }
        )
        .execute()
    )
    return first_row(response, "owner notification")

