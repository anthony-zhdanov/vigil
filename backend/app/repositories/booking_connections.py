from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def get_connection(
    supabase: Client | None, *, connection_id: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_connections")
        .select("*")
        .eq("id", connection_id)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking connection")


def get_client_connection(
    supabase: Client | None, *, client_id: str, provider: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_connections")
        .select("*")
        .eq("client_id", client_id)
        .eq("provider", provider)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking connection")


def list_client_connections(
    supabase: Client | None, *, client_id: str
) -> list[Row]:
    db = require_supabase(supabase)
    response = (
        db.table("booking_connections")
        .select("*")
        .eq("client_id", client_id)
        .order("provider")
        .execute()
    )
    data = getattr(response, "data", None)
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def get_by_provider_account_id(
    supabase: Client | None,
    *,
    provider: str,
    provider_account_id: str,
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_connections")
        .select("*")
        .eq("provider", provider)
        .eq("provider_account_id", provider_account_id)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking connection")


def upsert_connection(
    supabase: Client | None,
    *,
    client_id: str,
    provider: str,
    values: dict[str, Any],
) -> Row:
    db = require_supabase(supabase)
    payload: Row = {
        "client_id": client_id,
        "provider": provider,
        "updated_at": now_iso(),
        **values,
    }
    response = (
        db.table("booking_connections")
        .upsert(payload, on_conflict="client_id,provider")
        .execute()
    )
    return first_row(response, "booking connection")


def update_connection(
    supabase: Client | None, *, connection_id: str, values: dict[str, Any]
) -> Row | None:
    db = require_supabase(supabase)
    payload = {**values, "updated_at": now_iso()}
    response = (
        db.table("booking_connections")
        .update(payload)
        .eq("id", connection_id)
        .execute()
    )
    return maybe_first_row(response, "booking connection")
