from __future__ import annotations

import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.booking.domain import (
    BookingRequest,
    BookingResource,
    BusyInterval,
    ConnectionValidation,
    ProviderBooking,
    UnknownBookingOutcomeError,
)
from app.services.booking_workflow import BookingOrchestrator


TORONTO = ZoneInfo("America/Toronto")


class FakeProvider:
    name = "google"

    def __init__(self) -> None:
        self.busy: list[BusyInterval] = []
        self.created: list[BookingRequest] = []
        self.error: Exception | None = None
        self.result_status = "confirmed"

    def validate_connection(self, connection: dict[str, Any]) -> ConnectionValidation:
        return ConnectionValidation("connected")

    def list_resources(self, connection: dict[str, Any]) -> list[BookingResource]:
        return [BookingResource("calendar-1", "Jobs")]

    def list_busy_intervals(
        self,
        connection: dict[str, Any],
        resource: BookingResource,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[BusyInterval]:
        return list(self.busy)

    def create_booking(
        self,
        connection: dict[str, Any],
        request: BookingRequest,
        idempotency_key: str,
    ) -> ProviderBooking:
        self.created.append(request)
        if self.error:
            raise self.error
        return ProviderBooking(
            provider="google",
            status=self.result_status,  # type: ignore[arg-type]
            external_event_id="event-1",
        )


class BookingWorkflowHarness:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
        self.provider = FakeProvider()
        self.config = {
            "id": "config-1",
            "client_id": "client-1",
            "connection_id": "connection-1",
            "resource_id": "calendar-1",
            "resource_name": "Jobs",
            "timezone": "America/Toronto",
            "mode": "live",
            "enabled": True,
        }
        self.connection = {
            "id": "connection-1",
            "client_id": "client-1",
            "provider": "google",
            "status": "connected",
        }
        self.service = {
            "id": "service-1",
            "service_key": "drain_or_sewer",
            "display_name": "Drain service",
            "duration_minutes": 60,
            "lead_time_minutes": 0,
            "buffer_before_minutes": 0,
            "buffer_after_minutes": 0,
            "horizon_days": 7,
            "slot_interval_minutes": 60,
            "weekly_hours": {
                "monday": [{"start": "09:00", "end": "17:00"}],
                "tuesday": [{"start": "09:00", "end": "17:00"}],
            },
        }
        self.offers: list[dict[str, Any]] = []
        self.offer_updates: list[dict[str, Any]] = []
        self.bookings: list[dict[str, Any]] = []
        self.booking_updates: list[dict[str, Any]] = []
        self.existing_booking: dict[str, Any] | None = None
        self.orchestrator = BookingOrchestrator(
            object(),  # type: ignore[arg-type]
            {"google": self.provider},
            now=lambda: self.now,
        )

    @property
    def collected(self) -> dict[str, Any]:
        return {
            "job_type": "drain_or_sewer",
            "urgency": "scheduled",
            "location": "10 King St W Toronto",
            "customer_name": "Alex Smith",
        }

    def patchers(self) -> list[Any]:
        return [
            patch(
                "app.services.booking_workflow.config_repo.get_enabled_client_config",
                return_value=self.config,
            ),
            patch(
                "app.services.booking_workflow.connections_repo.get_connection",
                return_value=self.connection,
            ),
            patch(
                "app.services.booking_workflow.services_repo.get_service",
                return_value=self.service,
            ),
            patch(
                "app.services.booking_workflow.offers_repo.create_offer",
                side_effect=self.create_offer,
            ),
            patch(
                "app.services.booking_workflow.offers_repo.get_pending_offer",
                side_effect=self.get_pending_offer,
            ),
            patch(
                "app.services.booking_workflow.offers_repo.update_offer",
                side_effect=lambda supabase, **kwargs: self.offer_updates.append(kwargs),
            ),
            patch(
                "app.services.booking_workflow.bookings_repo.get_by_conversation",
                side_effect=lambda supabase, **kwargs: self.existing_booking,
            ),
            patch(
                "app.services.booking_workflow.bookings_repo.create_booking",
                side_effect=self.create_booking_row,
            ),
            patch(
                "app.services.booking_workflow.bookings_repo.update_booking",
                side_effect=lambda supabase, **kwargs: self.booking_updates.append(kwargs),
            ),
        ]

    def run(self, callback: Any) -> Any:
        with ExitStack() as stack:
            for patcher in self.patchers():
                stack.enter_context(patcher)
            return callback()

    def create_offer(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        expires_at = kwargs.get("expires_at")
        if isinstance(expires_at, datetime):
            kwargs["expires_at"] = expires_at.isoformat()
        row = {"id": f"offer-{len(self.offers) + 1}", **kwargs}
        self.offers.append(row)
        return row

    def get_pending_offer(self, supabase: Any, **kwargs: Any) -> dict[str, Any] | None:
        return self.offers[-1] if self.offers else None

    def create_booking_row(self, supabase: Any, *, values: dict[str, Any]) -> dict[str, Any]:
        self.bookings.append(dict(values))
        return dict(values)

    def offer(self, page_index: int = 0) -> Any:
        return self.run(
            lambda: self.orchestrator.offer_slots(
                client_id="client-1",
                lead_id="lead-1",
                conversation_id="conversation-1",
                collected_info=self.collected,
                page_index=page_index,
            )
        )

    def book(self, slot_index: int = 0) -> Any:
        return self.run(
            lambda: self.orchestrator.create_booking(
                client_id="client-1",
                lead_id="lead-1",
                conversation_id="conversation-1",
                customer_phone="+14165550123",
                collected_info=self.collected,
                slot_index=slot_index,
            )
        )


class BookingWorkflowTests(unittest.TestCase):
    def test_live_mode_offers_three_numbered_slots_for_fifteen_minutes(self) -> None:
        harness = BookingWorkflowHarness()

        outcome = harness.offer()

        self.assertEqual(outcome.kind, "offered")
        self.assertEqual(outcome.conversation_state, "awaiting_slot_selection")
        self.assertEqual(len(harness.offers[0]["slots"]), 3)
        self.assertEqual(
            harness.offers[0]["expires_at"],
            (harness.now + timedelta(minutes=15)).isoformat(),
        )
        self.assertIn("1. Mon, Jul 13", outcome.collected_info["slot_options"])

    def test_shadow_mode_computes_without_persisting_or_offering(self) -> None:
        harness = BookingWorkflowHarness()
        harness.config["mode"] = "shadow"

        outcome = harness.offer()

        self.assertEqual(outcome.kind, "shadow")
        self.assertEqual(outcome.collected_info["booking_shadow_slot_count"], 3)
        self.assertEqual(harness.offers, [])

    def test_selected_slot_is_revalidated_then_created_once(self) -> None:
        harness = BookingWorkflowHarness()
        harness.offer()

        outcome = harness.book(1)

        self.assertEqual(outcome.kind, "confirmed")
        self.assertEqual(outcome.conversation_state, "booked")
        self.assertEqual(len(harness.provider.created), 1)
        self.assertEqual(harness.booking_updates[-1]["values"]["status"], "confirmed")
        self.assertEqual(
            harness.booking_updates[-1]["values"]["external_event_id"], "event-1"
        )
        self.assertEqual(harness.offer_updates[-1]["values"]["status"], "booked")

    def test_duplicate_selection_returns_existing_confirmation(self) -> None:
        harness = BookingWorkflowHarness()
        harness.existing_booking = {
            "id": "booking-existing",
            "status": "confirmed",
            "starts_at": "2026-07-13T13:00:00-04:00",
            "ends_at": "2026-07-13T14:00:00-04:00",
            "timezone": "America/Toronto",
        }

        outcome = harness.book()

        self.assertEqual(outcome.kind, "confirmed")
        self.assertEqual(outcome.detail, "existing_booking")
        self.assertEqual(harness.provider.created, [])

    def test_expired_offer_is_replaced_with_fresh_options(self) -> None:
        harness = BookingWorkflowHarness()
        harness.offer()
        harness.offers[0]["expires_at"] = (
            harness.now - timedelta(minutes=1)
        ).isoformat()

        outcome = harness.book()

        self.assertEqual(outcome.kind, "stale")
        self.assertEqual(outcome.template_key, "booking_slots")
        self.assertEqual(harness.offer_updates[0]["values"]["status"], "expired")
        self.assertEqual(len(harness.offers), 2)

    def test_unknown_provider_outcome_is_never_retried(self) -> None:
        harness = BookingWorkflowHarness()
        harness.offer()
        harness.provider.error = UnknownBookingOutcomeError()

        outcome = harness.book()

        self.assertEqual(outcome.kind, "unknown")
        self.assertEqual(outcome.template_key, "booking_pending_confirmation")
        self.assertEqual(len(harness.provider.created), 1)
        self.assertEqual(harness.booking_updates[-1]["values"]["status"], "unknown")

    def test_explicit_unknown_provider_result_requires_confirmation(self) -> None:
        harness = BookingWorkflowHarness()
        harness.offer()
        harness.provider.result_status = "unknown"

        outcome = harness.book()

        self.assertEqual(outcome.kind, "unknown")
        self.assertEqual(harness.booking_updates[-1]["values"]["status"], "unknown")

    def test_emergencies_are_never_booking_eligible(self) -> None:
        harness = BookingWorkflowHarness()
        collected = {**harness.collected, "urgency": "emergency"}

        mode = harness.run(
            lambda: harness.orchestrator.booking_mode("client-1", collected)
        )

        self.assertEqual(mode, "disabled")
