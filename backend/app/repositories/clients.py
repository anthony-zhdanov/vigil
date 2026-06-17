from __future__ import annotations

from typing import Any, cast

from supabase import Client

from ._shared import Row, maybe_first_row, require_supabase


def get_client_by_id(supabase: Client | None, client_id: str) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("clients")
        .select("*")
        .eq("id", client_id)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "client")


def find_client_by_legacy_twilio_phone(
    supabase: Client | None, twilio_number: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("clients")
        .select("*")
        .eq("twilio_phone", twilio_number)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "client")


def find_client_by_twilio_number(
    supabase: Client | None,
    twilio_number: str,
    *,
    require_voice_enabled: bool = False,
    require_sms_enabled: bool = False,
    legacy_fallback: bool = True,
) -> Row | None:
    db = require_supabase(supabase)

    try:
        query = (
            db.table("client_phone_numbers")
            .select("client_id, clients(*)")
            .eq("phone_number", twilio_number)
            .eq("active", True)
        )
        if require_voice_enabled:
            query = query.eq("voice_enabled", True)
        if require_sms_enabled:
            query = query.eq("sms_enabled", True)

        phone_response = query.limit(1).execute()
        phone_row = maybe_first_row(phone_response, "phone-number")
        if phone_row is not None:
            client = phone_row.get("clients")
            if isinstance(client, dict):
                return cast(dict[str, Any], client)

            client_id = phone_row.get("client_id")
            if isinstance(client_id, str):
                return get_client_by_id(db, client_id)
    except Exception:
        if not legacy_fallback:
            raise

    if legacy_fallback:
        return find_client_by_legacy_twilio_phone(db, twilio_number)
    return None


def find_client_for_voice_number(
    supabase: Client | None, twilio_number: str, *, legacy_fallback: bool = True
) -> Row | None:
    return find_client_by_twilio_number(
        supabase,
        twilio_number,
        require_voice_enabled=True,
        legacy_fallback=legacy_fallback,
    )


def find_client_for_sms_number(
    supabase: Client | None, twilio_number: str, *, legacy_fallback: bool = True
) -> Row | None:
    return find_client_by_twilio_number(
        supabase,
        twilio_number,
        require_sms_enabled=True,
        legacy_fallback=legacy_fallback,
    )

