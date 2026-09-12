from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.booking.domain import ProviderAuthenticationError
from app.booking.oauth import OAuthClient
from app.booking.security import TokenCipher


ConnectionUpdater = Callable[[str, dict[str, Any]], dict[str, Any] | None]


class ConnectionCredentialManager:
    def __init__(
        self,
        cipher: TokenCipher,
        *,
        update_connection: ConnectionUpdater,
        refresh_early_seconds: int = 300,
    ) -> None:
        self._cipher = cipher
        self._update_connection = update_connection
        self._refresh_early = timedelta(seconds=refresh_early_seconds)

    @staticmethod
    def _context(connection_id: str, token_type: str) -> str:
        return f"booking-connection:{connection_id}:{token_type}"

    def encrypt_tokens(
        self,
        *,
        connection_id: str,
        access_token: str,
        refresh_token: str | None,
    ) -> dict[str, str | None]:
        return {
            "access_token_encrypted": self._cipher.encrypt(
                access_token,
                context=self._context(connection_id, "access"),
            ),
            "refresh_token_encrypted": (
                self._cipher.encrypt(
                    refresh_token,
                    context=self._context(connection_id, "refresh"),
                )
                if refresh_token
                else None
            ),
        }

    def access_token(
        self,
        connection: dict[str, Any],
        oauth_client: OAuthClient,
        *,
        now: datetime | None = None,
    ) -> str:
        connection_id = str(connection.get("id") or "")
        encrypted_access = connection.get("access_token_encrypted")
        if not connection_id or not isinstance(encrypted_access, str):
            raise ProviderAuthenticationError("Provider connection has no access token")

        current_time = now or datetime.now(timezone.utc)
        expires_at = _parse_datetime(connection.get("token_expires_at"))
        if expires_at is None or expires_at > current_time + self._refresh_early:
            return self._cipher.decrypt(
                encrypted_access,
                context=self._context(connection_id, "access"),
            )

        encrypted_refresh = connection.get("refresh_token_encrypted")
        if not isinstance(encrypted_refresh, str):
            raise ProviderAuthenticationError("Provider connection must be reauthorized")
        refresh_token = self._cipher.decrypt(
            encrypted_refresh,
            context=self._context(connection_id, "refresh"),
        )
        tokens = oauth_client.refresh(refresh_token)
        rotated_refresh = tokens.refresh_token or refresh_token
        encrypted = self.encrypt_tokens(
            connection_id=connection_id,
            access_token=tokens.access_token,
            refresh_token=rotated_refresh,
        )
        values: dict[str, Any] = {
            **encrypted,
            "token_expires_at": (
                tokens.expires_at.isoformat() if tokens.expires_at else None
            ),
            "scopes": list(tokens.scopes),
            "token_version": int(connection.get("token_version") or 1) + 1,
            "status": "connected",
        }
        updated = self._update_connection(connection_id, values)
        connection.update(updated or values)
        return tokens.access_token


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
