from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TOKEN_VERSION = "v1"


def generate_secret(byte_count: int = 32) -> str:
    return secrets.token_urlsafe(byte_count)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TokenCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) not in {16, 24, 32}:
            raise ValueError("Booking token encryption key must be 16, 24, or 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded_key: str) -> TokenCipher:
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Booking token encryption key is not valid base64") from exc
        return cls(key)

    def encrypt(self, value: str, *, context: str) -> str:
        nonce = secrets.token_bytes(12)
        encrypted = self._cipher.encrypt(
            nonce,
            value.encode("utf-8"),
            context.encode("utf-8"),
        )
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
        return f"{TOKEN_VERSION}:{payload}"

    def decrypt(self, value: str, *, context: str) -> str:
        version, separator, encoded = value.partition(":")
        if separator != ":" or version != TOKEN_VERSION:
            raise ValueError("Unsupported encrypted token version")
        try:
            payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Encrypted token payload is invalid") from exc
        if len(payload) <= 12:
            raise ValueError("Encrypted token payload is incomplete")
        decrypted = self._cipher.decrypt(
            payload[:12],
            payload[12:],
            context.encode("utf-8"),
        )
        return decrypted.decode("utf-8")


def sign_csrf(session_token: str, signing_secret: str) -> str:
    return hmac.new(
        signing_secret.encode("utf-8"),
        session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf(session_token: str, submitted: str, signing_secret: str) -> bool:
    expected = sign_csrf(session_token, signing_secret)
    return hmac.compare_digest(expected, submitted)


def redact_sensitive(value: Any) -> Any:
    sensitive_keys = {
        "access_token",
        "refresh_token",
        "client_secret",
        "code",
        "token",
        "authorization",
    }
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() in sensitive_keys
                else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def json_for_log(value: Any) -> str:
    return json.dumps(redact_sensitive(value), separators=(",", ":"), sort_keys=True)
