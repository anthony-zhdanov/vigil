from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from supabase import Client

Row = dict[str, Any]


def require_supabase(supabase: Client | None) -> Client:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")
    return supabase


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def response_data(response: Any) -> list[Any]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else []


def coerce_row(value: Any, context: str) -> Row:
    if not isinstance(value, dict):
        raise RuntimeError(f"Supabase returned a non-object {context} row")
    return cast(Row, value)


def maybe_first_row(response: Any, context: str) -> Row | None:
    rows = response_data(response)
    if not rows:
        return None
    return coerce_row(rows[0], context)


def first_row(response: Any, context: str) -> Row:
    row = maybe_first_row(response, context)
    if row is None:
        raise RuntimeError(f"Supabase did not return a {context} row")
    return row


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed

