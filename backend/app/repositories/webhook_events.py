from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def request_hash_from_payload(payload: Mapping[str, Any]) -> str:
    normalized = json.dumps(
        {str(key): str(value) for key, value in sorted(payload.items())},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_webhook_event(
    supabase: Client | None,
    *,
    provider: str,
    event_type: str,
    provider_event_id: str | None = None,
    request_hash: str | None = None,
) -> Row | None:
    db = require_supabase(supabase)
    query = (
        db.table("webhook_events")
        .select("*")
        .eq("provider", provider)
        .eq("event_type", event_type)
    )
    if provider_event_id:
        query = query.eq("provider_event_id", provider_event_id)
    elif request_hash:
        query = query.eq("request_hash", request_hash)
    else:
        raise ValueError("provider_event_id or request_hash is required")

    response = query.limit(1).execute()
    return maybe_first_row(response, "webhook event")


def begin_webhook_event(
    supabase: Client | None,
    *,
    provider: str,
    event_type: str,
    provider_event_id: str | None,
    request_hash: str,
    raw_payload: dict[str, Any] | None = None,
) -> tuple[Row, bool]:
    db = require_supabase(supabase)
    existing = get_webhook_event(
        db,
        provider=provider,
        event_type=event_type,
        provider_event_id=provider_event_id,
        request_hash=request_hash,
    )
    if existing is not None:
        return existing, existing.get("processed_at") is None

    payload: Row = {
        "provider": provider,
        "event_type": event_type,
        "provider_event_id": provider_event_id,
        "request_hash": request_hash,
        "raw_payload": raw_payload or {},
    }
    try:
        response = db.table("webhook_events").insert(payload).execute()
        return first_row(response, "webhook event"), True
    except Exception:
        existing = get_webhook_event(
            db,
            provider=provider,
            event_type=event_type,
            provider_event_id=provider_event_id,
            request_hash=request_hash,
        )
        if existing is not None:
            return existing, False
        raise


def mark_webhook_event_processed(
    supabase: Client | None,
    *,
    event_id: str,
    processed_at: str | None = None,
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("webhook_events")
        .update({"processed_at": processed_at or now_iso()})
        .eq("id", event_id)
        .execute()
    )
    return maybe_first_row(response, "webhook event")


def list_unprocessed_webhook_events(
    supabase: Client | None,
    *,
    provider: str,
    limit: int = 25,
) -> list[Row]:
    db = require_supabase(supabase)
    response = (
        db.table("webhook_events")
        .select("*")
        .eq("provider", provider)
        .is_("processed_at", "null")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    data = getattr(response, "data", None)
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def mark_webhook_event_failed(
    supabase: Client | None,
    *,
    event_id: str,
    attempts: int,
    error_code: str,
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("webhook_events")
        .update(
            {
                "attempts": attempts,
                "last_attempted_at": now_iso(),
                "last_error": error_code[:160],
            }
        )
        .eq("id", event_id)
        .execute()
    )
    return maybe_first_row(response, "webhook event")
