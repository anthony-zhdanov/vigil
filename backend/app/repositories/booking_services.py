from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def get_service(
    supabase: Client | None, *, client_id: str, service_key: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_services")
        .select("*")
        .eq("client_id", client_id)
        .eq("service_key", service_key)
        .eq("enabled", True)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking service")


def list_services(supabase: Client | None, *, client_id: str) -> list[Row]:
    db = require_supabase(supabase)
    response = (
        db.table("booking_services")
        .select("*")
        .eq("client_id", client_id)
        .order("display_name")
        .execute()
    )
    data = getattr(response, "data", None)
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def upsert_service(
    supabase: Client | None,
    *,
    client_id: str,
    service_key: str,
    values: dict[str, Any],
) -> Row:
    db = require_supabase(supabase)
    payload: Row = {
        "client_id": client_id,
        "service_key": service_key,
        "updated_at": now_iso(),
        **values,
    }
    response = (
        db.table("booking_services")
        .upsert(payload, on_conflict="client_id,service_key")
        .execute()
    )
    return first_row(response, "booking service")
