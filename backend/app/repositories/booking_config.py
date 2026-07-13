from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def get_client_config(supabase: Client | None, *, client_id: str) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_configs")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking config")


def upsert_client_config(
    supabase: Client | None, *, client_id: str, values: dict[str, Any]
) -> Row:
    db = require_supabase(supabase)
    payload: Row = {"client_id": client_id, "updated_at": now_iso(), **values}
    response = (
        db.table("booking_configs")
        .upsert(payload, on_conflict="client_id")
        .execute()
    )
    return first_row(response, "booking config")


def get_enabled_client_config(
    supabase: Client | None, *, client_id: str
) -> Row | None:
    config = get_client_config(supabase, client_id=client_id)
    if config is None or not config.get("enabled"):
        return None
    if config.get("mode") not in {"shadow", "live"}:
        return None
    return config


def disable_connection(
    supabase: Client | None, *, connection_id: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_configs")
        .update(
            {
                "connection_id": None,
                "enabled": False,
                "mode": "disabled",
                "updated_at": now_iso(),
            }
        )
        .eq("connection_id", connection_id)
        .execute()
    )
    return maybe_first_row(response, "booking config")
