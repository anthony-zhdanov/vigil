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
    AvailabilityUnsupportedError,
    BookingProviderError,
    BookingRequest,
    BookingResource,
    ServiceSchedule,
    Slot,
    UnknownBookingOutcomeError,
)
from app.booking.jobber import JobberGraphQLClient, JobberProvider
from app.booking.oauth import OAuthClient, OAuthProviderConfig
from app.booking.security import TokenCipher


class JobberBookingProviderTests(unittest.TestCase):
    api_version = "2025-04-16"

    def setUp(self) -> None:
        cipher = TokenCipher(base64.urlsafe_b64decode(base64.urlsafe_b64encode(b"j" * 32)))
        credentials = ConnectionCredentialManager(
            cipher, update_connection=lambda connection_id, values: values
        )
        encrypted = credentials.encrypt_tokens(
            connection_id="connection-1",
            access_token="jobber-access",
            refresh_token="jobber-refresh",
        )
        self.connection = {
            "id": "connection-1",
            "provider": "jobber",
            "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "metadata": {
                "availability_complete": True,
                "schema_verified_version": self.api_version,
            },
            **encrypted,
        }
        self.credentials = credentials
        self.oauth = OAuthClient(
            OAuthProviderConfig(
                authorization_url="https://api.getjobber.com/api/oauth/authorize",
                token_url="https://api.getjobber.com/api/oauth/token",
                client_id="client",
                client_secret="secret",
            )
        )

    def provider(self, handler: Any) -> JobberProvider:
        graphql = JobberGraphQLClient(
            self.credentials,
            self.oauth,
            api_version=self.api_version,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        return JobberProvider(graphql)

    def request(self) -> BookingRequest:
        tz = ZoneInfo("America/Toronto")
        return BookingRequest(
            booking_id="123e4567-e89b-12d3-a456-426614174000",
            client_id="client-1",
            service=ServiceSchedule(
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
            ),
            resource_id="user-1",
            slot=Slot(
                datetime(2026, 7, 14, 9, 0, tzinfo=tz),
                datetime(2026, 7, 14, 10, 0, tzinfo=tz),
            ),
            customer_name="Alex Smith",
            customer_phone="+1 (416) 555-0123",
            customer_address="10 King St W Toronto ON",
        )

    @staticmethod
    def operation(request: httpx.Request) -> tuple[str, dict[str, Any]]:
        payload = json.loads(request.content)
        query = payload["query"]
        name = query.split("query ", 1)[1].split("(", 1)[0].split(" {", 1)[0] if "query " in query else query.split("mutation ", 1)[1].split("(", 1)[0]
        return name.strip(), payload.get("variables", {})

    def test_validation_is_gated_by_authenticated_schema_version(self) -> None:
        unverified = {**self.connection, "metadata": {}}
        provider = self.provider(
            lambda request: httpx.Response(
                200, json={"data": {"account": {"id": "account-1", "name": "Acme"}}}
            )
        )

        validation = provider.validate_connection(unverified)

        self.assertEqual(validation.status, "availability_unsupported")
        with self.assertRaises(AvailabilityUnsupportedError):
            provider.list_busy_intervals(
                unverified,
                BookingResource(id="user-1", name="Owner"),
                datetime(2026, 7, 14, tzinfo=timezone.utc),
                datetime(2026, 7, 15, tzinfo=timezone.utc),
            )

    def test_lists_active_jobber_users(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                request.headers["X-JOBBER-GRAPHQL-VERSION"], self.api_version
            )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "users": {
                            "nodes": [
                                {
                                    "id": "user-1",
                                    "name": {"full": "Alex Owner"},
                                    "status": "ACTIVE",
                                    "timezone": {"ianaName": "America/Toronto"},
                                },
                                {
                                    "id": "user-2",
                                    "name": {"full": "Former User"},
                                    "status": "DEACTIVATED",
                                },
                            ]
                        }
                    }
                },
            )

        resources = self.provider(handler).list_resources(self.connection)

        self.assertEqual(
            resources,
            [BookingResource("user-1", "Alex Owner", "America/Toronto")],
        )

    def test_complete_availability_includes_all_verified_collections(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "visits": {
                            "nodes": [
                                {
                                    "startAt": "2026-07-14T13:00:00Z",
                                    "endAt": "2026-07-14T14:00:00Z",
                                    "assignedUsers": {"nodes": [{"id": "user-1"}]},
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "assessments": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "tasks": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        },
                    }
                },
            )

        intervals = self.provider(handler).list_busy_intervals(
            self.connection,
            BookingResource(id="user-1", name="Owner"),
            datetime(2026, 7, 14, tzinfo=timezone.utc),
            datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(len(intervals), 1)

    def test_ambiguous_phone_match_never_creates_a_booking(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "clients": {
                            "nodes": [
                                {"id": "one", "phones": [{"number": "+14165550123"}]},
                                {"id": "two", "phones": [{"number": "+14165550123"}]},
                            ],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                },
            )

        with self.assertRaisesRegex(BookingProviderError, "More than one"):
            self.provider(handler).create_booking(
                self.connection, self.request(), "idempotency-1"
            )

    def test_creates_client_property_job_and_visit(self) -> None:
        operations: list[tuple[str, dict[str, Any]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            operation, variables = self.operation(request)
            operations.append((operation, variables))
            responses = {
                "VigilClients": {
                    "clients": {"nodes": [], "pageInfo": {"hasNextPage": False}}
                },
                "VigilClientCreate": {
                    "clientCreate": {"client": {"id": "client-new"}, "userErrors": []}
                },
                "VigilClientProperties": {
                    "client": {
                        "clientProperties": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                },
                "VigilPropertyCreate": {
                    "propertyCreate": {
                        "property": {"id": "property-new"},
                        "userErrors": [],
                    }
                },
                "VigilJobCreate": {
                    "jobCreate": {"job": {"id": "job-new"}, "userErrors": []}
                },
                "VigilVisitCreate": {
                    "visitCreate": {"visit": {"id": "visit-new"}, "userErrors": []}
                },
            }
            return httpx.Response(200, json={"data": responses[operation]})

        result = self.provider(handler).create_booking(
            self.connection, self.request(), "idempotency-1"
        )

        self.assertEqual(result.external_client_id, "client-new")
        self.assertEqual(result.external_property_id, "property-new")
        self.assertEqual(result.external_job_id, "job-new")
        self.assertEqual(result.external_visit_id, "visit-new")
        visit_input = operations[-1][1]["input"]
        self.assertEqual(visit_input["assignedUserIds"], ["user-1"])
        self.assertIn("[Vigil:123e4567", visit_input["instructions"])

    def test_graphql_mutation_errors_preserve_unknown_outcome(self) -> None:
        graphql = JobberGraphQLClient(
            self.credentials,
            self.oauth,
            api_version=self.api_version,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, json={"errors": [{"message": "unexpected"}]}
                    )
                )
            ),
        )

        with self.assertRaises(UnknownBookingOutcomeError):
            graphql.execute(
                self.connection,
                "mutation VigilTest { testMutation { id } }",
                mutation=True,
            )
