from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.booking.jobber_webhooks import (
    JobberWebhookProcessor,
    create_jobber_webhook_router,
    verify_jobber_signature,
)


SECRET = "jobber-client-secret"


def signed_payload(topic: str = "APP_DISCONNECT") -> tuple[bytes, str, dict[str, Any]]:
    payload = {
        "data": {
            "webHookEvent": {
                "topic": topic,
                "accountId": "account-1",
                "itemId": "item-1",
                "occurredAt": "2026-07-13T10:00:00Z",
            }
        }
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    return raw, base64.b64encode(digest).decode("ascii"), payload


class FakeProcessor:
    def __init__(self) -> None:
        self.stored = 0
        self.processed = 0

    def accepts_signature(self, raw_body: bytes, signature: str) -> bool:
        return verify_jobber_signature(raw_body, signature, SECRET)

    def store(self, raw_body: bytes, payload: dict[str, Any]) -> tuple[str, bool]:
        self.stored += 1
        return "event-1", self.stored == 1

    def process_pending(self) -> None:
        self.processed += 1


class JobberWebhookRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = FakeProcessor()
        app = FastAPI()
        app.include_router(create_jobber_webhook_router(lambda: self.processor))
        self.client = TestClient(app)

    def test_invalid_signature_is_rejected_before_storage(self) -> None:
        raw, _, _ = signed_payload()

        response = self.client.post(
            "/webhooks/jobber",
            content=raw,
            headers={"X-Jobber-Hmac-SHA256": "invalid"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.processor.stored, 0)

    def test_valid_webhook_is_stored_and_queued(self) -> None:
        raw, signature, _ = signed_payload()

        response = self.client.post(
            "/webhooks/jobber",
            content=raw,
            headers={"X-Jobber-Hmac-SHA256": signature},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.processor.stored, 1)
        self.assertEqual(self.processor.processed, 1)


class JobberWebhookProcessorTests(unittest.TestCase):
    def test_duplicate_payload_uses_stable_provider_identity(self) -> None:
        raw, _, payload = signed_payload()
        processor = JobberWebhookProcessor(object(), client_secret=SECRET)  # type: ignore[arg-type]
        calls: list[dict[str, Any]] = []

        def begin(supabase: Any, **kwargs: Any) -> tuple[dict[str, str], bool]:
            calls.append(kwargs)
            return {"id": "event-1"}, len(calls) == 1

        with patch(
            "app.booking.jobber_webhooks.webhook_events_repo.begin_webhook_event",
            side_effect=begin,
        ):
            first = processor.store(raw, payload)
            second = processor.store(raw, payload)

        self.assertTrue(first[1])
        self.assertFalse(second[1])
        self.assertEqual(
            calls[0]["provider_event_id"], calls[1]["provider_event_id"]
        )
        self.assertEqual(calls[0]["raw_payload"], payload)

    def test_disconnect_revokes_connection_and_disables_booking(self) -> None:
        _, _, payload = signed_payload()
        processor = JobberWebhookProcessor(object(), client_secret=SECRET)  # type: ignore[arg-type]
        connection_updates: list[dict[str, Any]] = []
        disabled: list[str] = []

        with (
            patch(
                "app.booking.jobber_webhooks.webhook_events_repo.list_unprocessed_webhook_events",
                return_value=[
                    {"id": "event-1", "raw_payload": payload, "attempts": 0}
                ],
            ),
            patch(
                "app.booking.jobber_webhooks.connections_repo.get_by_provider_account_id",
                return_value={"id": "connection-1"},
            ),
            patch(
                "app.booking.jobber_webhooks.connections_repo.update_connection",
                side_effect=lambda supabase, **kwargs: connection_updates.append(kwargs),
            ),
            patch(
                "app.booking.jobber_webhooks.booking_config_repo.disable_connection",
                side_effect=lambda supabase, connection_id: disabled.append(connection_id),
            ),
            patch(
                "app.booking.jobber_webhooks.webhook_events_repo.mark_webhook_event_processed"
            ) as marked,
        ):
            processor.process_pending()

        self.assertEqual(connection_updates[0]["values"]["status"], "revoked")
        self.assertIsNone(connection_updates[0]["values"]["access_token_encrypted"])
        self.assertEqual(disabled, ["connection-1"])
        marked.assert_called_once()
