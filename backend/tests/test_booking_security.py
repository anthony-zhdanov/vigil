from __future__ import annotations

import base64
import unittest
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.exceptions import InvalidTag

from app.booking.domain import ProviderAuthenticationError
from app.booking.oauth import OAuthClient, OAuthProviderConfig
from app.booking.security import (
    TokenCipher,
    hash_secret,
    json_for_log,
    sign_csrf,
    verify_csrf,
)


class BookingSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")

    def test_tokens_are_encrypted_with_bound_context(self) -> None:
        cipher = TokenCipher.from_base64(self.key)

        encrypted = cipher.encrypt("secret-token", context="connection-1:access")

        self.assertNotIn("secret-token", encrypted)
        self.assertEqual(
            cipher.decrypt(encrypted, context="connection-1:access"),
            "secret-token",
        )
        with self.assertRaises(InvalidTag):
            cipher.decrypt(encrypted, context="connection-2:access")

    def test_invalid_key_size_is_rejected(self) -> None:
        invalid = base64.urlsafe_b64encode(b"short").decode("ascii")

        with self.assertRaisesRegex(ValueError, "16, 24, or 32"):
            TokenCipher.from_base64(invalid)

    def test_hashes_and_csrf_are_bound_to_session(self) -> None:
        self.assertEqual(len(hash_secret("setup-token")), 64)
        token = sign_csrf("session-one", "signing-secret")

        self.assertTrue(verify_csrf("session-one", token, "signing-secret"))
        self.assertFalse(verify_csrf("session-two", token, "signing-secret"))

    def test_sensitive_provider_values_are_redacted_from_logs(self) -> None:
        rendered = json_for_log(
            {"access_token": "secret", "nested": {"code": "oauth-code"}, "ok": 1}
        )

        self.assertNotIn("secret", rendered)
        self.assertNotIn("oauth-code", rendered)
        self.assertIn('"ok":1', rendered)


class OAuthClientTests(unittest.TestCase):
    def config(self) -> OAuthProviderConfig:
        return OAuthProviderConfig(
            authorization_url="https://provider.example/authorize",
            token_url="https://provider.example/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=("calendar.read", "calendar.write"),
            authorization_params={"access_type": "offline"},
        )

    def test_authorization_url_contains_state_redirect_and_scopes(self) -> None:
        client = OAuthClient(self.config())

        url = client.authorization_url(
            redirect_uri="https://vigil.example/callback",
            state="state-value",
        )
        params = parse_qs(urlparse(url).query)

        self.assertEqual(params["state"], ["state-value"])
        self.assertEqual(params["redirect_uri"], ["https://vigil.example/callback"])
        self.assertEqual(params["scope"], ["calendar.read calendar.write"])
        self.assertEqual(params["access_type"], ["offline"])

    def test_code_exchange_parses_rotating_tokens(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "scope": "calendar.read calendar.write",
                },
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = OAuthClient(self.config(), http_client=http_client)

        tokens = client.exchange_code(
            code="authorization-code",
            redirect_uri="https://vigil.example/callback",
        )

        self.assertEqual(tokens.access_token, "access")
        self.assertEqual(tokens.refresh_token, "refresh")
        self.assertIsNotNone(tokens.expires_at)
        self.assertIn("grant_type=authorization_code", captured["body"])

    def test_rejected_token_exchange_has_no_response_secret(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"access_token": "do-not-leak"})
        )
        client = OAuthClient(
            self.config(), http_client=httpx.Client(transport=transport)
        )

        with self.assertRaisesRegex(
            ProviderAuthenticationError, "authorization was rejected"
        ):
            client.exchange_code(
                code="bad-code",
                redirect_uri="https://vigil.example/callback",
            )
