from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol


ProviderName = Literal["google", "jobber"]
ConnectionStatus = Literal[
    "pending",
    "connected",
    "expired",
    "revoked",
    "availability_unsupported",
    "error",
    "disconnected",
]


@dataclass(frozen=True, slots=True)
class BookingResource:
    id: str
    name: str
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class BusyInterval:
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Busy intervals must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("Busy interval end must follow its start")


@dataclass(frozen=True, slots=True)
class Slot:
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Slots must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("Slot end must follow its start")

    def to_dict(self) -> dict[str, str]:
        return {
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Slot:
        return cls(
            starts_at=datetime.fromisoformat(str(value["starts_at"])),
            ends_at=datetime.fromisoformat(str(value["ends_at"])),
        )


@dataclass(frozen=True, slots=True)
class ServiceSchedule:
    service_id: str
    service_key: str
    display_name: str
    timezone: str
    duration_minutes: int
    lead_time_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    horizon_days: int
    slot_interval_minutes: int
    weekly_hours: dict[str, list[dict[str, str]]]
    provider_service_id: str | None = None

    @classmethod
    def from_rows(
        cls, service: dict[str, Any], config: dict[str, Any]
    ) -> ServiceSchedule:
        raw_hours = service.get("weekly_hours")
        weekly_hours = raw_hours if isinstance(raw_hours, dict) else {}
        return cls(
            service_id=str(service["id"]),
            service_key=str(service["service_key"]),
            display_name=str(service["display_name"]),
            timezone=str(config["timezone"]),
            duration_minutes=int(service["duration_minutes"]),
            lead_time_minutes=int(service.get("lead_time_minutes") or 0),
            buffer_before_minutes=int(service.get("buffer_before_minutes") or 0),
            buffer_after_minutes=int(service.get("buffer_after_minutes") or 0),
            horizon_days=int(service["horizon_days"]),
            slot_interval_minutes=int(service["slot_interval_minutes"]),
            weekly_hours=weekly_hours,
            provider_service_id=(
                str(service["provider_service_id"])
                if service.get("provider_service_id")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class BookingRequest:
    booking_id: str
    client_id: str
    service: ServiceSchedule
    resource_id: str
    slot: Slot
    customer_name: str
    customer_phone: str
    customer_address: str
    urgency: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderBooking:
    provider: ProviderName
    status: Literal["confirmed", "unknown"]
    external_event_id: str | None = None
    external_client_id: str | None = None
    external_property_id: str | None = None
    external_job_id: str | None = None
    external_visit_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectionValidation:
    status: ConnectionStatus
    account_id: str | None = None
    account_name: str | None = None
    detail: str | None = None


class BookingProvider(Protocol):
    name: ProviderName

    def validate_connection(
        self, connection: dict[str, Any]
    ) -> ConnectionValidation: ...

    def list_resources(
        self, connection: dict[str, Any]
    ) -> list[BookingResource]: ...

    def list_busy_intervals(
        self,
        connection: dict[str, Any],
        resource: BookingResource,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[BusyInterval]: ...

    def create_booking(
        self,
        connection: dict[str, Any],
        request: BookingRequest,
        idempotency_key: str,
    ) -> ProviderBooking: ...


class BookingProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


class ProviderAuthenticationError(BookingProviderError):
    def __init__(self, message: str = "Provider authorization is invalid") -> None:
        super().__init__(message, code="provider_authentication")


class AvailabilityUnsupportedError(BookingProviderError):
    def __init__(self, message: str = "Complete availability is unavailable") -> None:
        super().__init__(message, code="availability_unsupported")


class SlotConflictError(BookingProviderError):
    def __init__(self, message: str = "The selected slot is no longer available") -> None:
        super().__init__(message, code="slot_conflict")


class ProviderRateLimitError(BookingProviderError):
    def __init__(self, message: str = "Provider rate limit reached") -> None:
        super().__init__(message, code="provider_rate_limit", retryable=True)


class UnknownBookingOutcomeError(BookingProviderError):
    def __init__(self, message: str = "Booking outcome could not be confirmed") -> None:
        super().__init__(
            message,
            code="booking_outcome_unknown",
            outcome_unknown=True,
        )
