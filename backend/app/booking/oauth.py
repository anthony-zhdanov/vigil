from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.booking.domain import BookingProviderError, ProviderAuthenticationError


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    authorization_url: str
    token_url: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...] = ()
    authorization_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OAuthTokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...]
    raw: dict[str, Any] = field(default_factory=dict)


class OAuthClient:
    def __init__(
        self,
        config: OAuthProviderConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._http = http_client or httpx.Client(timeout=15.0)

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str | None = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            **self.config.authorization_params,
        }
        if self.config.scopes:
            params["scope"] = " ".join(self.config.scopes)
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{self.config.authorization_url}?{urlencode(params)}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> OAuthTokenSet:
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        return self._request_token(data)

    def refresh(self, refresh_token: str) -> OAuthTokenSet:
        return self._request_token(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    def _request_token(self, data: dict[str, str]) -> OAuthTokenSet:
        try:
            response = self._http.post(
                self.config.token_url,
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise BookingProviderError(
                "OAuth provider timed out",
                code="oauth_timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise BookingProviderError(
                "OAuth provider request failed",
                code="oauth_transport",
                retryable=True,
            ) from exc

        if response.status_code in {400, 401, 403}:
            raise ProviderAuthenticationError("OAuth authorization was rejected")
        if response.status_code == 429:
            raise BookingProviderError(
                "OAuth provider rate limit reached",
                code="oauth_rate_limit",
                retryable=True,
            )
        if response.is_error:
            raise BookingProviderError(
                "OAuth provider returned an error",
                code="oauth_provider_error",
                retryable=response.status_code >= 500,
            )

        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ProviderAuthenticationError("OAuth response did not include an access token")

        expires_at = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))

        raw_scope = payload.get("scope")
        scopes = (
            tuple(str(raw_scope).split())
            if raw_scope
            else self.config.scopes
        )
        return OAuthTokenSet(
            access_token=str(payload["access_token"]),
            refresh_token=(
                str(payload["refresh_token"])
                if payload.get("refresh_token")
                else None
            ),
            expires_at=expires_at,
            scopes=scopes,
            raw=payload,
        )
