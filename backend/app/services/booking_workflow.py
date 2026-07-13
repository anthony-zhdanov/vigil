from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from supabase import Client

from app.booking.domain import (
    BookingProvider,
    BookingProviderError,
    BookingRequest,
    BookingResource,
    ServiceSchedule,
    Slot,
    UnknownBookingOutcomeError,
)
from app.booking.slots import generate_slots, slot_is_still_available
from app.repositories import booking_config as config_repo
from app.repositories import booking_connections as connections_repo
from app.repositories import booking_services as services_repo
from app.repositories import booking_slot_offers as offers_repo
from app.repositories import bookings as bookings_repo
from app.repositories._shared import parse_datetime


@dataclass(frozen=True, slots=True)
class BookingActionOutcome:
    kind: str
    template_key: str | None
    conversation_state: str
    conversation_status: str
    collected_info: dict[str, Any]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class _BookingContext:
    config: dict[str, Any]
    connection: dict[str, Any]
    service: dict[str, Any]
    provider: BookingProvider
    schedule: ServiceSchedule
    resource: BookingResource


class BookingOrchestrator:
    def __init__(
        self,
        supabase: Client,
        providers: dict[str, BookingProvider],
        *,
        now: Any | None = None,
    ) -> None:
        self.supabase = supabase
        self.providers = providers
        self._now = now or (lambda: datetime.now(timezone.utc))

    def booking_mode(self, client_id: str, collected_info: dict[str, Any]) -> str:
        if collected_info.get("urgency") == "emergency":
            return "disabled"
        service_key = str(collected_info.get("job_type") or "")
        if not service_key:
            return "disabled"
        try:
            context = self._context(client_id, service_key)
        except (BookingProviderError, RuntimeError, ValueError):
            return "disabled"
        mode = str(context.config.get("mode") or "disabled")
        if context.connection.get("status") != "connected":
            return "disabled"
        return mode if mode in {"shadow", "live"} else "disabled"

    def _context(self, client_id: str, service_key: str) -> _BookingContext:
        config = config_repo.get_enabled_client_config(
            self.supabase, client_id=client_id
        )
        if config is None or not config.get("connection_id"):
            raise BookingProviderError(
                "Booking is not configured", code="booking_not_configured"
            )
        connection = connections_repo.get_connection(
            self.supabase, connection_id=str(config["connection_id"])
        )
        if connection is None or str(connection.get("client_id")) != client_id:
            raise BookingProviderError(
                "Booking connection is unavailable", code="booking_connection_missing"
            )
        provider = self.providers.get(str(connection.get("provider")))
        if provider is None:
            raise BookingProviderError(
                "Booking provider is unavailable", code="booking_provider_missing"
            )
        service = services_repo.get_service(
            self.supabase, client_id=client_id, service_key=service_key
        )
        if service is None:
            raise BookingProviderError(
                "Service is not configured for booking", code="booking_service_unsupported"
            )
        resource_id = str(config.get("resource_id") or "")
        if not resource_id:
            raise BookingProviderError(
                "Booking resource is not configured", code="booking_resource_missing"
            )
        schedule = ServiceSchedule.from_rows(service, config)
        return _BookingContext(
            config=config,
            connection=connection,
            service=service,
            provider=provider,
            schedule=schedule,
            resource=BookingResource(
                id=resource_id,
                name=str(config.get("resource_name") or resource_id),
                timezone=str(config.get("timezone") or schedule.timezone),
            ),
        )

    def offer_slots(
        self,
        *,
        client_id: str,
        lead_id: str,
        conversation_id: str,
        collected_info: dict[str, Any],
        page_index: int,
    ) -> BookingActionOutcome:
        next_info = dict(collected_info)
        service_key = str(next_info.get("job_type") or "")
        try:
            context = self._context(client_id, service_key)
            now = self._now()
            search_end = now + timedelta(days=context.schedule.horizon_days + 1)
            busy = context.provider.list_busy_intervals(
                context.connection,
                context.resource,
                now,
                search_end,
            )
            slots = generate_slots(
                context.schedule,
                busy,
                now=now,
                page_index=page_index,
            )
        except Exception as exc:
            return self._handoff(next_info, type(exc).__name__)

        if context.config.get("mode") == "shadow":
            next_info["booking_shadow_slot_count"] = len(slots)
            return BookingActionOutcome(
                kind="shadow",
                template_key=None,
                conversation_state="finding_availability",
                conversation_status="waiting_for_system",
                collected_info=next_info,
                detail=f"slots={len(slots)}",
            )
        if not slots:
            return self._handoff(next_info, "no_available_slots")

        expires_at = now + timedelta(minutes=15)
        offer = offers_repo.create_offer(
            self.supabase,
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            connection_id=str(context.connection["id"]),
            service_id=str(context.service["id"]),
            resource_id=context.resource.id,
            slots=[slot.to_dict() for slot in slots],
            page_index=page_index,
            expires_at=expires_at,
        )
        next_info.update(
            {
                "booking_offer_id": str(offer["id"]),
                "booking_page_index": page_index,
                "slot_options": _format_slot_options(slots, context.schedule.timezone),
            }
        )
        return BookingActionOutcome(
            kind="offered",
            template_key="booking_slots",
            conversation_state="awaiting_slot_selection",
            conversation_status="waiting_for_customer",
            collected_info=next_info,
            detail=str(offer["id"]),
        )

    def create_booking(
        self,
        *,
        client_id: str,
        lead_id: str,
        conversation_id: str,
        customer_phone: str,
        collected_info: dict[str, Any],
        slot_index: int,
    ) -> BookingActionOutcome:
        next_info = dict(collected_info)
        existing = bookings_repo.get_by_conversation(
            self.supabase, conversation_id=conversation_id
        )
        if existing is not None:
            return self._existing_booking_outcome(existing, next_info)

        offer = offers_repo.get_pending_offer(
            self.supabase, conversation_id=conversation_id
        )
        if offer is None:
            return self._handoff(next_info, "booking_offer_missing")
        expires_at = parse_datetime(offer.get("expires_at"))
        if expires_at is None or expires_at <= self._now():
            offers_repo.update_offer(
                self.supabase, offer_id=str(offer["id"]), values={"status": "expired"}
            )
            refreshed = self.offer_slots(
                client_id=client_id,
                lead_id=lead_id,
                conversation_id=conversation_id,
                collected_info=next_info,
                page_index=0,
            )
            return _replace_kind(refreshed, "stale")

        raw_slots = offer.get("slots")
        if not isinstance(raw_slots, list) or not 0 <= slot_index < len(raw_slots):
            return BookingActionOutcome(
                kind="invalid_selection",
                template_key="invalid_slot_selection",
                conversation_state="awaiting_slot_selection",
                conversation_status="waiting_for_customer",
                collected_info=next_info,
                detail="slot_index_out_of_range",
            )
        slot_value = raw_slots[slot_index]
        if not isinstance(slot_value, dict):
            return self._handoff(next_info, "booking_slot_invalid")
        selected = Slot.from_dict(slot_value)
        service_key = str(next_info.get("job_type") or "")
        try:
            context = self._context(client_id, service_key)
            busy = context.provider.list_busy_intervals(
                context.connection,
                context.resource,
                selected.starts_at - timedelta(minutes=context.schedule.buffer_before_minutes),
                selected.ends_at + timedelta(minutes=context.schedule.buffer_after_minutes),
            )
        except Exception as exc:
            return self._handoff(next_info, type(exc).__name__)
        if not slot_is_still_available(selected, busy, schedule=context.schedule):
            offers_repo.update_offer(
                self.supabase,
                offer_id=str(offer["id"]),
                values={"status": "replaced"},
            )
            refreshed = self.offer_slots(
                client_id=client_id,
                lead_id=lead_id,
                conversation_id=conversation_id,
                collected_info=next_info,
                page_index=0,
            )
            return _replace_kind(refreshed, "stale")

        booking_id = str(uuid4())
        idempotency_key = f"{conversation_id}:{offer['id']}:{selected.starts_at.isoformat()}"
        values = {
            "id": booking_id,
            "client_id": client_id,
            "lead_id": lead_id,
            "conversation_id": conversation_id,
            "connection_id": str(context.connection["id"]),
            "service_id": str(context.service["id"]),
            "slot_offer_id": str(offer["id"]),
            "provider": str(context.connection["provider"]),
            "status": "creating",
            "starts_at": selected.starts_at.isoformat(),
            "ends_at": selected.ends_at.isoformat(),
            "timezone": context.schedule.timezone,
            "customer_name": str(next_info.get("customer_name") or ""),
            "customer_phone": customer_phone,
            "customer_address": str(next_info.get("location") or ""),
            "urgency": next_info.get("urgency"),
            "idempotency_key": idempotency_key,
        }
        try:
            booking = bookings_repo.create_booking(self.supabase, values=values)
        except Exception:
            existing = bookings_repo.get_by_conversation(
                self.supabase, conversation_id=conversation_id
            )
            if existing is None:
                raise
            return self._existing_booking_outcome(existing, next_info)

        request = BookingRequest(
            booking_id=booking_id,
            client_id=client_id,
            service=context.schedule,
            resource_id=context.resource.id,
            slot=selected,
            customer_name=str(next_info.get("customer_name") or ""),
            customer_phone=customer_phone,
            customer_address=str(next_info.get("location") or ""),
            urgency=(str(next_info["urgency"]) if next_info.get("urgency") else None),
            notes=str(next_info.get("latest_summary") or "") or None,
        )
        try:
            provider_booking = context.provider.create_booking(
                context.connection, request, idempotency_key
            )
        except UnknownBookingOutcomeError as exc:
            bookings_repo.update_booking(
                self.supabase,
                booking_id=str(booking["id"]),
                values={
                    "status": "unknown",
                    "failure_code": exc.code,
                    "failure_message": "Provider confirmation required",
                },
            )
            return BookingActionOutcome(
                kind="unknown",
                template_key="booking_pending_confirmation",
                conversation_state="booking_handoff",
                conversation_status="waiting_for_owner",
                collected_info=next_info,
                detail=exc.code,
            )
        except BookingProviderError as exc:
            bookings_repo.update_booking(
                self.supabase,
                booking_id=str(booking["id"]),
                values={
                    "status": "failed",
                    "failure_code": exc.code,
                    "failure_message": "Provider booking failed",
                },
            )
            return self._handoff(next_info, exc.code)

        if provider_booking.status == "unknown":
            bookings_repo.update_booking(
                self.supabase,
                booking_id=str(booking["id"]),
                values={
                    "status": "unknown",
                    "failure_code": "booking_outcome_unknown",
                    "failure_message": "Provider confirmation required",
                },
            )
            return BookingActionOutcome(
                kind="unknown",
                template_key="booking_pending_confirmation",
                conversation_state="booking_handoff",
                conversation_status="waiting_for_owner",
                collected_info=next_info,
                detail="booking_outcome_unknown",
            )

        bookings_repo.update_booking(
            self.supabase,
            booking_id=str(booking["id"]),
            values={
                "status": "confirmed",
                "external_client_id": provider_booking.external_client_id,
                "external_property_id": provider_booking.external_property_id,
                "external_job_id": provider_booking.external_job_id,
                "external_visit_id": provider_booking.external_visit_id,
                "external_event_id": provider_booking.external_event_id,
                "external_data": provider_booking.metadata,
                "confirmed_at": self._now().isoformat(),
            },
        )
        offers_repo.update_offer(
            self.supabase,
            offer_id=str(offer["id"]),
            values={"status": "booked", "selected_slot_index": slot_index},
        )
        next_info.update(
            {
                "booking_id": booking_id,
                "booking_time": _format_slot(selected, context.schedule.timezone),
            }
        )
        return BookingActionOutcome(
            kind="confirmed",
            template_key="booking_confirmation",
            conversation_state="booked",
            conversation_status="booked",
            collected_info=next_info,
            detail=booking_id,
        )

    def _existing_booking_outcome(
        self, booking: dict[str, Any], collected_info: dict[str, Any]
    ) -> BookingActionOutcome:
        status = str(booking.get("status") or "")
        next_info = dict(collected_info)
        if status == "confirmed":
            starts_at = parse_datetime(booking.get("starts_at"))
            timezone_name = str(booking.get("timezone") or "UTC")
            if starts_at:
                duration_end = parse_datetime(booking.get("ends_at")) or starts_at
                next_info["booking_time"] = _format_slot(
                    Slot(starts_at, duration_end), timezone_name
                )
            next_info["booking_id"] = str(booking.get("id") or "")
            return BookingActionOutcome(
                kind="confirmed",
                template_key="booking_confirmation",
                conversation_state="booked",
                conversation_status="booked",
                collected_info=next_info,
                detail="existing_booking",
            )
        return BookingActionOutcome(
            kind="unknown",
            template_key="booking_pending_confirmation",
            conversation_state="booking_handoff",
            conversation_status="waiting_for_owner",
            collected_info=next_info,
            detail="existing_booking_unconfirmed",
        )

    @staticmethod
    def _handoff(
        collected_info: dict[str, Any], detail: str
    ) -> BookingActionOutcome:
        return BookingActionOutcome(
            kind="handoff",
            template_key="booking_handoff",
            conversation_state="booking_handoff",
            conversation_status="waiting_for_owner",
            collected_info=dict(collected_info),
            detail=detail,
        )


def _format_slot(slot: Slot, timezone_name: str) -> str:
    local = slot.starts_at.astimezone(ZoneInfo(timezone_name))
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%a, %b')} {local.day} at {clock}"


def _format_slot_options(slots: list[Slot], timezone_name: str) -> str:
    return "\n".join(
        f"{index}. {_format_slot(slot, timezone_name)}"
        for index, slot in enumerate(slots, start=1)
    )


def _replace_kind(outcome: BookingActionOutcome, kind: str) -> BookingActionOutcome:
    return BookingActionOutcome(
        kind=kind,
        template_key=outcome.template_key,
        conversation_state=outcome.conversation_state,
        conversation_status=outcome.conversation_status,
        collected_info=outcome.collected_info,
        detail=outcome.detail,
    )
