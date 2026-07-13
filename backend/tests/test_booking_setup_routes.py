from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.booking.domain import BookingResource
from app.booking.runtime import (
    SetupSession,
    SetupView,
    _oauth_state_is_valid,
    _setup_session_is_active,
    _setup_token_is_claimable,
)
from app.booking.setup_routes import SESSION_COOKIE, create_booking_setup_router
from app.booking.security import sign_csrf


class FakeBookingRuntime:
    def __init__(self) -> None:
        self.session_secret = "session-signing-secret"
        self.secure_cookies = False
        self.provider_bundles = {"google": object(), "jobber": object()}
        self.session = SetupSession(
            client={"id": "client-1", "business_name": "Acme Plumbing"},
            token_row={"id": "setup-1", "client_id": "client-1"},
        )
        self.saved: list[dict[str, Any]] = []
        self.disconnected: list[str] = []
        self.finished: list[tuple[str, str, str]] = []

    def claim_setup_token(self, raw_token: str) -> str | None:
        return "session-token" if raw_token == "valid-token" else None

    def get_setup_session(self, raw_session: str | None) -> SetupSession | None:
        return self.session if raw_session == "session-token" else None

    def setup_view(self, session: SetupSession) -> SetupView:
        return SetupView(
            session=session,
            connections=[
                {
                    "id": "connection-1",
                    "provider": "google",
                    "status": "connected",
                    "provider_account_name": "Acme Calendar",
                }
            ],
            config={
                "connection_id": "connection-1",
                "resource_id": "calendar-1",
                "resource_name": "Jobs",
                "timezone": "America/Toronto",
                "mode": "shadow",
            },
            services=[],
            resources=[BookingResource("calendar-1", "Jobs", "America/Toronto")],
        )

    def start_oauth(self, session: SetupSession, provider_name: str) -> str:
        return f"https://provider.example/authorize?provider={provider_name}"

    def finish_oauth(
        self, provider_name: str, *, state: str, code: str
    ) -> dict[str, str]:
        self.finished.append((provider_name, state, code))
        return {"id": "connection-1"}

    def save_configuration(self, session: SetupSession, **kwargs: Any) -> None:
        self.saved.append(kwargs)

    def disconnect(self, session: SetupSession, connection_id: str) -> None:
        self.disconnected.append(connection_id)


class BookingSetupRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = FakeBookingRuntime()
        app = FastAPI()
        app.include_router(create_booking_setup_router(lambda: self.runtime))
        self.client = TestClient(app)

    def authenticate(self) -> str:
        self.client.cookies.set(SESSION_COOKIE, "session-token")
        return sign_csrf("session-token", self.runtime.session_secret)

    def test_claim_exchanges_raw_token_and_strips_it_from_redirect(self) -> None:
        response = self.client.get(
            "/booking/setup/claim?token=valid-token", follow_redirects=False
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/booking/setup")
        self.assertNotIn("valid-token", response.headers["location"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    def test_setup_page_requires_session_and_renders_provider_resource(self) -> None:
        denied = self.client.get("/booking/setup")
        self.assertEqual(denied.status_code, 401)

        self.authenticate()
        response = self.client.get("/booking/setup")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Acme Plumbing", response.text)
        self.assertIn("Acme Calendar", response.text)
        self.assertIn("Jobs", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_oauth_start_requires_valid_csrf(self) -> None:
        csrf = self.authenticate()

        denied = self.client.post(
            "/booking/setup/oauth/google",
            data={"csrf_token": "wrong"},
            follow_redirects=False,
        )
        allowed = self.client.post(
            "/booking/setup/oauth/google",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 303)
        self.assertIn("provider=google", allowed.headers["location"])

    def test_configuration_form_builds_weekly_hours(self) -> None:
        csrf = self.authenticate()

        response = self.client.post(
            "/booking/setup/config",
            data={
                "csrf_token": csrf,
                "connection_id": "connection-1",
                "resource_id": "calendar-1",
                "resource_name": "Jobs",
                "timezone": "America/Toronto",
                "mode": "shadow",
                "service_key": "drain_or_sewer",
                "display_name": "Drain service",
                "duration_minutes": "90",
                "monday": "on",
                "wednesday": "on",
                "day_start": "08:30",
                "day_end": "16:30",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        weekly = self.runtime.saved[0]["service_values"]["weekly_hours"]
        self.assertEqual(
            weekly,
            {
                "monday": [{"start": "08:30", "end": "16:30"}],
                "wednesday": [{"start": "08:30", "end": "16:30"}],
            },
        )


class BookingSetupExpiryTests(unittest.TestCase):
    def test_setup_and_oauth_values_expire(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        self.assertTrue(
            _setup_token_is_claimable(
                {"expires_at": future, "claimed_at": None, "revoked_at": None}
            )
        )
        self.assertFalse(
            _setup_token_is_claimable(
                {"expires_at": past, "claimed_at": None, "revoked_at": None}
            )
        )
        self.assertTrue(
            _setup_session_is_active(
                {
                    "claimed_at": future,
                    "revoked_at": None,
                    "session_expires_at": future,
                }
            )
        )
        self.assertFalse(
            _oauth_state_is_valid(
                {"provider": "google", "expires_at": past}, "google"
            )
        )
