from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.booking.credentials import ConnectionCredentialManager
from app.booking.domain import (
    BookingRequest,
    BookingResource,
    ServiceSchedule,
    Slot,
    UnknownBookingOutcomeError,
)
from app.booking.google import GoogleCalendarProvider
from app.booking.oauth import OAuthClient, OAuthProviderConfig
from app.booking.security import TokenCipher


class GoogleBookingProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        cipher = TokenCipher(base64.urlsafe_b64decode(base64.urlsafe_b64encode(b"g" * 32)))
        self.updates: list[dict[str, Any]] = []

        def update(connection_id: str, values: dict[str, Any]) -> dict[str, Any]:
            self.updates.append({"connection_id": connection_id, **values})
            return values

        self.credentials = ConnectionCredentialManager(cipher, update_connection=update)
        encrypted = self.credentials.encrypt_tokens(
            connection_id="connection-1",
            access_token="google-access",
            refresh_token="google-refresh",
        )
        self.connection = {
            "id": "connection-1",
            "provider": "google",
            "status": "connected",
            "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            **encrypted,
        }
        self.oauth = OAuthClient(
            OAuthProviderConfig(
                authorization_url="https://accounts.example/authorize",
                token_url="https://accounts.example/token",
                client_id="client",
                client_secret="secret",
            )
        )

    def provider(self, handler: Any) -> GoogleCalendarProvider:
        return GoogleCalendarProvider(
            self.credentials,
            self.oauth,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    def request(self) -> BookingRequest:
        tz = ZoneInfo("America/Toronto")
        service = ServiceSchedule(
            service_id="service-1",
            service_key="drain_or_sewer",
            display_name="Drain service",
            timezone="America/Toronto",
            duration_minutes=60,
            lead_time_minutes=60,
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            horizon_days=14,
            slot_interval_minutes=30,
            weekly_hours={},
        )
        return BookingRequest(
            booking_id="123e4567-e89b-12d3-a456-426614174000",
            client_id="client-1",
            service=service,
            resource_id="calendar@example.com",
            slot=Slot(
                datetime(2026, 7, 14, 9, 0, tzinfo=tz),
                datetime(2026, 7, 14, 10, 0, tzinfo=tz),
            ),
            customer_name="Alex Smith",
            customer_phone="+14165550123",
            customer_address="10 King St W, Toronto",
            urgency="scheduled",
        )

    def test_lists_only_writable_calendars(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer google-access")
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "one", "summary": "Jobs", "accessRole": "owner"},
                        {"id": "two", "summary": "Read only", "accessRole": "reader"},
                    ]
                },
            )

        resources = self.provider(handler).list_resources(self.connection)

        self.assertEqual(resources, [BookingResource(id="one", name="Jobs")])

    def test_freebusy_response_becomes_typed_intervals(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["items"], [{"id": "one"}])
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        "one": {
                            "busy": [
                                {
                                    "start": "2026-07-14T13:00:00Z",
                                    "end": "2026-07-14T14:00:00Z",
                                }
                            ]
                        }
                    }
                },
            )

        intervals = self.provider(handler).list_busy_intervals(
            self.connection,
            BookingResource(id="one", name="Jobs"),
            datetime(2026, 7, 14, tzinfo=timezone.utc),
            datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(intervals[0].starts_at.hour, 13)

    def test_event_creation_uses_deterministic_id_and_customer_context(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"id": captured["payload"]["id"], "htmlLink": "https://calendar/event"},
            )

        result = self.provider(handler).create_booking(
            self.connection,
            self.request(),
            "idempotency-1",
        )

        self.assertEqual(result.status, "confirmed")
        self.assertEqual(
            result.external_event_id,
            "vigil123e4567e89b12d3a456426614174000",
        )
        self.assertIn("Alex Smith", captured["payload"]["description"])
        self.assertIn("+14165550123", captured["payload"]["description"])

    def test_existing_deterministic_event_resolves_create_conflict(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            if request.method == "POST":
                return httpx.Response(409, json={"error": "duplicate"})
            return httpx.Response(200, json={"id": "existing-event"})

        result = self.provider(handler).create_booking(
            self.connection,
            self.request(),
            "idempotency-1",
        )

        self.assertEqual(result.external_event_id, "existing-event")
        self.assertEqual(calls, ["POST", "GET"])

    def test_expiring_token_is_refreshed_and_rotation_is_persisted(self) -> None:
        self.connection["token_expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()

        def oauth_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "access_token": "rotated-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                },
            )

        self.oauth = OAuthClient(
            OAuthProviderConfig(
                authorization_url="https://accounts.example/authorize",
                token_url="https://accounts.example/token",
                client_id="client",
                client_secret="secret",
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(oauth_handler)),
        )

        def calendar_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer rotated-access")
            return httpx.Response(200, json={"items": []})

        self.provider(calendar_handler).list_resources(self.connection)

        self.assertEqual(len(self.updates), 1)
        self.assertEqual(self.updates[0]["token_version"], 2)
        self.assertNotIn("rotated-access", self.updates[0]["access_token_encrypted"])

    def test_failed_reconciliation_preserves_unknown_create_outcome(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout", request=request)

        with self.assertRaises(UnknownBookingOutcomeError):
            self.provider(handler).create_booking(
                self.connection,
                self.request(),
                "idempotency-1",
            )
