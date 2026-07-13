from __future__ import annotations

from datetime import datetime
from typing import Any

from supabase import Client

from ._shared import Row, first_row, maybe_first_row, now_iso, require_supabase


def create_setup_token(
    supabase: Client | None,
    *,
    client_id: str,
    token_hash: str,
    expires_at: datetime,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("booking_setup_tokens")
        .insert(
            {
                "client_id": client_id,
                "token_hash": token_hash,
                "expires_at": expires_at.isoformat(),
            }
        )
        .execute()
    )
    return first_row(response, "booking setup token")


def find_setup_token(supabase: Client | None, *, token_hash: str) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_setup_tokens")
        .select("*")
        .eq("token_hash", token_hash)
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking setup token")


def claim_setup_token(
    supabase: Client | None,
    *,
    token_id: str,
    session_hash: str,
    session_expires_at: datetime,
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_setup_tokens")
        .update(
            {
                "claimed_at": now_iso(),
                "session_hash": session_hash,
                "session_expires_at": session_expires_at.isoformat(),
            }
        )
        .eq("id", token_id)
        .is_("claimed_at", "null")
        .is_("revoked_at", "null")
        .execute()
    )
    return maybe_first_row(response, "booking setup token")


def find_setup_session(
    supabase: Client | None, *, session_hash: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_setup_tokens")
        .select("*")
        .eq("session_hash", session_hash)
        .is_("revoked_at", "null")
        .limit(1)
        .execute()
    )
    return maybe_first_row(response, "booking setup token")


def create_oauth_state(
    supabase: Client | None,
    *,
    client_id: str,
    provider: str,
    state_hash: str,
    redirect_uri: str,
    expires_at: datetime,
    code_verifier_encrypted: str | None = None,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("booking_oauth_states")
        .insert(
            {
                "client_id": client_id,
                "provider": provider,
                "state_hash": state_hash,
                "redirect_uri": redirect_uri,
                "expires_at": expires_at.isoformat(),
                "code_verifier_encrypted": code_verifier_encrypted,
            }
        )
        .execute()
    )
    return first_row(response, "booking OAuth state")


def consume_oauth_state(
    supabase: Client | None, *, state_hash: str
) -> Row | None:
    db = require_supabase(supabase)
    response = (
        db.table("booking_oauth_states")
        .update({"consumed_at": now_iso()})
        .eq("state_hash", state_hash)
        .is_("consumed_at", "null")
        .execute()
    )
    return maybe_first_row(response, "booking OAuth state")
