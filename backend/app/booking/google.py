from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.booking.credentials import ConnectionCredentialManager
from app.booking.domain import (
    BookingProviderError,
    BookingRequest,
    BookingResource,
    BusyInterval,
    ConnectionValidation,
    ProviderAuthenticationError,
    ProviderBooking,
    ProviderRateLimitError,
    UnknownBookingOutcomeError,
)
from app.booking.oauth import OAuthClient


GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarProvider:
    name = "google"

    def __init__(
        self,
        credentials: ConnectionCredentialManager,
        oauth_client: OAuthClient,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._credentials = credentials
        self._oauth = oauth_client
        self._http = http_client or httpx.Client(timeout=15.0)

    def _headers(self, connection: dict[str, Any]) -> dict[str, str]:
        token = self._credentials.access_token(connection, self._oauth)
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _request(
        self,
        connection: dict[str, Any],
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = self._http.request(
                method,
                url,
                headers={**self._headers(connection), **kwargs.pop("headers", {})},
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise BookingProviderError(
                "Google Calendar timed out",
                code="google_timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise BookingProviderError(
                "Google Calendar request failed",
                code="google_transport",
                retryable=True,
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Google Calendar authorization failed")
        if response.status_code == 429:
            raise ProviderRateLimitError("Google Calendar rate limit reached")
        if response.status_code >= 500:
            raise BookingProviderError(
                "Google Calendar is temporarily unavailable",
                code="google_server_error",
                retryable=True,
            )
        return response

    def validate_connection(
        self, connection: dict[str, Any]
    ) -> ConnectionValidation:
        response = self._request(
            connection,
            "GET",
            f"{GOOGLE_CALENDAR_API}/calendars/primary",
        )
        if response.is_error:
            return ConnectionValidation(status="error", detail="calendar_lookup_failed")
        payload = response.json()
        return ConnectionValidation(
            status="connected",
            account_id=str(payload.get("id") or "primary"),
            account_name=str(payload.get("summary") or "Google Calendar"),
        )

    def list_resources(
        self, connection: dict[str, Any]
    ) -> list[BookingResource]:
        response = self._request(
            connection,
            "GET",
            f"{GOOGLE_CALENDAR_API}/users/me/calendarList",
        )
        if response.is_error:
            raise BookingProviderError(
                "Google calendars could not be listed",
                code="google_calendar_list",
            )
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [
            BookingResource(
                id=str(item["id"]),
                name=str(item.get("summary") or item["id"]),
                timezone=(str(item["timeZone"]) if item.get("timeZone") else None),
            )
            for item in items
            if isinstance(item, dict)
            and item.get("id")
            and item.get("accessRole") in {"owner", "writer"}
        ]

    def list_busy_intervals(
        self,
        connection: dict[str, Any],
        resource: BookingResource,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[BusyInterval]:
        response = self._request(
            connection,
            "POST",
            f"{GOOGLE_CALENDAR_API}/freeBusy",
            json={
                "timeMin": starts_at.isoformat(),
                "timeMax": ends_at.isoformat(),
                "items": [{"id": resource.id}],
            },
        )
        if response.is_error:
            raise BookingProviderError(
                "Google availability lookup failed",
                code="google_freebusy",
            )
        payload = response.json()
        calendars = payload.get("calendars", {}) if isinstance(payload, dict) else {}
        calendar = calendars.get(resource.id, {}) if isinstance(calendars, dict) else {}
        if calendar.get("errors"):
            raise BookingProviderError(
                "Google calendar availability was incomplete",
                code="google_freebusy_incomplete",
            )
        return [
            BusyInterval(
                starts_at=datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00")),
                ends_at=datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00")),
            )
            for item in calendar.get("busy", [])
            if isinstance(item, dict) and item.get("start") and item.get("end")
        ]

    @staticmethod
    def event_id(booking_id: str) -> str:
        normalized = "".join(char for char in booking_id.lower() if char in "0123456789abcdef")
        if len(normalized) < 5:
            raise ValueError("Booking id cannot produce a valid Google event id")
        return f"vigil{normalized}"

    def _existing_event(
        self,
        connection: dict[str, Any],
        resource_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        response = self._request(
            connection,
            "GET",
            f"{GOOGLE_CALENDAR_API}/calendars/{quote(resource_id, safe='')}/events/{event_id}",
        )
        if response.status_code == 404:
            return None
        if response.is_error:
            raise BookingProviderError(
                "Google event reconciliation failed",
                code="google_event_reconcile",
            )
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def create_booking(
        self,
        connection: dict[str, Any],
        request: BookingRequest,
        idempotency_key: str,
    ) -> ProviderBooking:
        event_id = self.event_id(request.booking_id)
        marker = f"Vigil booking: {request.booking_id}"
        description = "\n".join(
            value
            for value in [
                marker,
                f"Customer: {request.customer_name}",
                f"Phone: {request.customer_phone}",
                f"Address: {request.customer_address}",
                f"Service: {request.service.display_name}",
                f"Urgency: {request.urgency or 'routine'}",
                request.notes,
            ]
            if value
        )
        payload = {
            "id": event_id,
            "summary": f"{request.service.display_name} - {request.customer_name}",
            "description": description,
            "location": request.customer_address,
            "start": {
                "dateTime": request.slot.starts_at.isoformat(),
                "timeZone": request.service.timezone,
            },
            "end": {
                "dateTime": request.slot.ends_at.isoformat(),
                "timeZone": request.service.timezone,
            },
            "extendedProperties": {
                "private": {
                    "vigilBookingId": request.booking_id,
                    "vigilIdempotencyKey": idempotency_key,
                }
            },
        }
        url = (
            f"{GOOGLE_CALENDAR_API}/calendars/"
            f"{quote(request.resource_id, safe='')}/events"
        )
        try:
            response = self._request(
                connection,
                "POST",
                url,
                params={"sendUpdates": "none"},
                json=payload,
            )
        except BookingProviderError as exc:
            if exc.code not in {"google_timeout", "google_transport"}:
                raise
            existing = self._existing_event(
                connection, request.resource_id, event_id
            )
            if existing is None:
                raise UnknownBookingOutcomeError(
                    "Google event creation could not be reconciled"
                ) from exc
            response_payload = existing
        else:
            if response.status_code == 409:
                response_payload = self._existing_event(
                    connection, request.resource_id, event_id
                )
                if response_payload is None:
                    raise UnknownBookingOutcomeError(
                        "Google reported a conflict without an existing event"
                    )
            elif response.is_error:
                raise BookingProviderError(
                    "Google event creation failed",
                    code="google_event_create",
                )
            else:
                payload_value = response.json()
                response_payload = payload_value if isinstance(payload_value, dict) else {}

        return ProviderBooking(
            provider="google",
            status="confirmed",
            external_event_id=str(response_payload.get("id") or event_id),
            metadata={"html_link": response_payload.get("htmlLink")},
        )
