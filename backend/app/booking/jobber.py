from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.booking.credentials import ConnectionCredentialManager
from app.booking.domain import (
    AvailabilityUnsupportedError,
    BookingProviderError,
    BookingRequest,
    BookingResource,
    BusyInterval,
    ConnectionValidation,
    ProviderAuthenticationError,
    ProviderBooking,
    ProviderName,
    ProviderRateLimitError,
    UnknownBookingOutcomeError,
)
from app.booking.oauth import OAuthClient


JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"

ACCOUNT_QUERY = """
query VigilAccount {
  account { id name }
}
"""

USERS_QUERY = """
query VigilUsers {
  users(first: 100) {
    nodes { id name { full } status timezone { ianaName } }
    pageInfo { hasNextPage }
  }
}
"""

# This operation must be checked against the authenticated schema for the pinned
# API version before availability_complete can be enabled on a connection.
AVAILABILITY_QUERY = """
query VigilAvailability(
  $userId: EncodedId!,
  $startsAt: ISO8601DateTime!,
  $endsAt: ISO8601DateTime!
) {
  visits(
    first: 100,
    filter: { assignedUserId: $userId, startsAfter: $startsAt, startsBefore: $endsAt }
  ) {
    nodes {
      id startAt endAt allDay
      assignedUsers(first: 100) { nodes { id } }
    }
    pageInfo { hasNextPage }
  }
  assessments(
    first: 100,
    filter: { assignedUserId: $userId, startsAfter: $startsAt, startsBefore: $endsAt }
  ) {
    nodes {
      id startAt endAt allDay
      assignedUsers(first: 100) { nodes { id } }
    }
    pageInfo { hasNextPage }
  }
  tasks(
    first: 100,
    filter: { assignedUserId: $userId, startsAfter: $startsAt, startsBefore: $endsAt }
  ) {
    nodes { id startAt endAt }
    pageInfo { hasNextPage }
  }
}
"""

CLIENTS_QUERY = """
query VigilClients($cursor: String) {
  clients(first: 100, after: $cursor) {
    nodes {
      id firstName lastName name
      phones { number primary }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CLIENT_CREATE_MUTATION = """
mutation VigilClientCreate($input: ClientCreateInput!) {
  clientCreate(input: $input) {
    client { id firstName lastName name }
    userErrors { message path }
  }
}
"""

PROPERTIES_QUERY = """
query VigilClientProperties($clientId: EncodedId!) {
  client(id: $clientId) {
    clientProperties(first: 100) {
      nodes {
        id
        address { street1 street2 city province postalCode country }
      }
      pageInfo { hasNextPage }
    }
  }
}
"""

PROPERTY_CREATE_MUTATION = """
mutation VigilPropertyCreate($input: PropertyCreateInput!) {
  propertyCreate(input: $input) {
    property { id address { street1 street2 city province postalCode country } }
    userErrors { message path }
  }
}
"""

JOB_CREATE_MUTATION = """
mutation VigilJobCreate($input: JobCreateInput!) {
  jobCreate(input: $input) {
    job { id instructions jobNumber }
    userErrors { message path }
  }
}
"""

VISIT_CREATE_MUTATION = """
mutation VigilVisitCreate($input: VisitCreateInput!) {
  visitCreate(input: $input) {
    visit { id startAt endAt instructions job { id } }
    userErrors { message path }
  }
}
"""

JOB_RECONCILE_QUERY = """
query VigilJobReconcile($clientId: EncodedId!) {
  client(id: $clientId) {
    jobs(first: 100) {
      nodes { id instructions visits(first: 100) { nodes { id instructions } } }
      pageInfo { hasNextPage }
    }
  }
}
"""

APP_DISCONNECT_MUTATION = """
mutation VigilAppDisconnect {
  appDisconnect {
    app { name author }
    userErrors { message path }
  }
}
"""


def normalize_phone(value: str) -> str:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) == 10:
        digits = f"1{digits}"
    return f"+{digits}" if digits else ""


def normalize_address(value: str) -> str:
    return " ".join(value.lower().replace(",", " ").split())


def _address_text(address: Any) -> str:
    if not isinstance(address, dict):
        return ""
    return " ".join(
        str(address.get(key) or "").strip()
        for key in ("street1", "street2", "city", "province", "postalCode", "country")
        if address.get(key)
    )


def _user_errors(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    errors = payload.get("userErrors")
    if not isinstance(errors, list):
        return []
    return [str(error.get("message")) for error in errors if isinstance(error, dict)]


class JobberGraphQLClient:
    def __init__(
        self,
        credentials: ConnectionCredentialManager,
        oauth_client: OAuthClient,
        *,
        api_version: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._credentials = credentials
        self._oauth = oauth_client
        self.api_version = api_version
        self._http = http_client or httpx.Client(timeout=15.0)

    def execute(
        self,
        connection: dict[str, Any],
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        mutation: bool = False,
    ) -> dict[str, Any]:
        token = self._credentials.access_token(connection, self._oauth)
        try:
            response = self._http.post(
                JOBBER_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-JOBBER-GRAPHQL-VERSION": self.api_version,
                },
                json={"query": query, "variables": variables or {}},
            )
        except httpx.TimeoutException as exc:
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation timed out before its result was known"
                ) from exc
            raise BookingProviderError(
                "Jobber timed out", code="jobber_timeout", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation failed before its result was known"
                ) from exc
            raise BookingProviderError(
                "Jobber request failed", code="jobber_transport", retryable=True
            ) from exc

        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("Jobber authorization failed")
        if response.status_code == 429:
            raise ProviderRateLimitError("Jobber rate limit reached")
        if response.status_code >= 500:
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation returned an uncertain server error"
                )
            raise BookingProviderError(
                "Jobber is temporarily unavailable",
                code="jobber_server_error",
                retryable=True,
            )
        if response.is_error:
            raise BookingProviderError(
                "Jobber rejected the request", code="jobber_http_error"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation returned an unreadable response"
                ) from exc
            raise BookingProviderError(
                "Jobber returned an invalid response", code="jobber_invalid_response"
            ) from exc
        if not isinstance(payload, dict):
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation returned an invalid response"
                )
            raise BookingProviderError(
                "Jobber returned an invalid response", code="jobber_invalid_response"
            )
        if payload.get("errors"):
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation returned GraphQL errors after submission"
                )
            raise BookingProviderError(
                "Jobber GraphQL operation failed", code="jobber_graphql_error"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            if mutation:
                raise UnknownBookingOutcomeError(
                    "Jobber mutation response did not contain data"
                )
            raise BookingProviderError(
                "Jobber response did not contain data", code="jobber_missing_data"
            )
        return data


class JobberProvider:
    name: ProviderName = "jobber"

    def __init__(self, graphql: JobberGraphQLClient) -> None:
        self._graphql = graphql

    def validate_connection(
        self, connection: dict[str, Any]
    ) -> ConnectionValidation:
        data = self._graphql.execute(connection, ACCOUNT_QUERY)
        account = data.get("account")
        if not isinstance(account, dict) or not account.get("id"):
            return ConnectionValidation(status="error", detail="account_lookup_failed")
        metadata = connection.get("metadata")
        availability_ready = (
            isinstance(metadata, dict)
            and metadata.get("availability_complete") is True
            and metadata.get("schema_verified_version") == self._graphql.api_version
        )
        return ConnectionValidation(
            status="connected" if availability_ready else "availability_unsupported",
            account_id=str(account["id"]),
            account_name=str(account.get("name") or "Jobber"),
            detail=None if availability_ready else "authenticated_schema_not_verified",
        )

    def list_resources(
        self, connection: dict[str, Any]
    ) -> list[BookingResource]:
        data = self._graphql.execute(connection, USERS_QUERY)
        users = data.get("users")
        nodes = users.get("nodes", []) if isinstance(users, dict) else []
        resources: list[BookingResource] = []
        for user in nodes:
            if not isinstance(user, dict) or not user.get("id"):
                continue
            if user.get("status") not in {None, "ACTIVE"}:
                continue
            name = user.get("name")
            timezone = user.get("timezone")
            resources.append(
                BookingResource(
                    id=str(user["id"]),
                    name=(
                        str(name.get("full"))
                        if isinstance(name, dict) and name.get("full")
                        else str(user["id"])
                    ),
                    timezone=(
                        str(timezone.get("ianaName"))
                        if isinstance(timezone, dict) and timezone.get("ianaName")
                        else None
                    ),
                )
            )
        return resources

    def _require_verified_availability(self, connection: dict[str, Any]) -> None:
        metadata = connection.get("metadata")
        if not (
            isinstance(metadata, dict)
            and metadata.get("availability_complete") is True
            and metadata.get("schema_verified_version") == self._graphql.api_version
        ):
            raise AvailabilityUnsupportedError(
                "Jobber availability schema has not been verified for this API version"
            )

    def list_busy_intervals(
        self,
        connection: dict[str, Any],
        resource: BookingResource,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[BusyInterval]:
        self._require_verified_availability(connection)
        data = self._graphql.execute(
            connection,
            AVAILABILITY_QUERY,
            {
                "userId": resource.id,
                "startsAt": starts_at.isoformat(),
                "endsAt": ends_at.isoformat(),
            },
        )
        intervals: list[BusyInterval] = []
        for collection_name in ("visits", "assessments", "tasks"):
            collection = data.get(collection_name)
            if not isinstance(collection, dict):
                raise AvailabilityUnsupportedError(
                    f"Jobber did not return {collection_name} availability"
                )
            page_info = collection.get("pageInfo")
            if isinstance(page_info, dict) and page_info.get("hasNextPage"):
                raise AvailabilityUnsupportedError(
                    "Jobber availability exceeded the verified query page"
                )
            for item in collection.get("nodes", []):
                if not isinstance(item, dict) or not item.get("startAt") or not item.get("endAt"):
                    continue
                assigned = item.get("assignedUsers")
                assigned_nodes = (
                    assigned.get("nodes", []) if isinstance(assigned, dict) else []
                )
                if assigned_nodes and resource.id not in {
                    str(user.get("id"))
                    for user in assigned_nodes
                    if isinstance(user, dict)
                }:
                    continue
                intervals.append(
                    BusyInterval(
                        datetime.fromisoformat(str(item["startAt"]).replace("Z", "+00:00")),
                        datetime.fromisoformat(str(item["endAt"]).replace("Z", "+00:00")),
                    )
                )
        return sorted(intervals, key=lambda interval: interval.starts_at)

    def _find_clients_by_phone(
        self, connection: dict[str, Any], phone: str
    ) -> list[dict[str, Any]]:
        expected = normalize_phone(phone)
        matches: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(5):
            data = self._graphql.execute(
                connection, CLIENTS_QUERY, {"cursor": cursor}
            )
            collection = data.get("clients")
            if not isinstance(collection, dict):
                break
            for client in collection.get("nodes", []):
                if not isinstance(client, dict):
                    continue
                phones = client.get("phones")
                numbers = (
                    [item.get("number") for item in phones if isinstance(item, dict)]
                    if isinstance(phones, list)
                    else []
                )
                if expected and expected in {
                    normalize_phone(str(number)) for number in numbers if number
                }:
                    matches.append(client)
            page_info = collection.get("pageInfo")
            if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
                break
            cursor_value = page_info.get("endCursor")
            if not cursor_value:
                raise BookingProviderError(
                    "Jobber client pagination was incomplete",
                    code="jobber_client_pagination",
                )
            cursor = str(cursor_value)
        else:
            raise BookingProviderError(
                "Jobber client search exceeded its safe page limit",
                code="jobber_client_search_limit",
            )
        return matches

    def _create_client(
        self, connection: dict[str, Any], request: BookingRequest
    ) -> str:
        first_name, separator, last_name = request.customer_name.strip().partition(" ")
        data = self._graphql.execute(
            connection,
            CLIENT_CREATE_MUTATION,
            {
                "input": {
                    "firstName": first_name,
                    "lastName": last_name if separator else "",
                    "phones": [
                        {
                            "description": "MAIN",
                            "primary": True,
                            "number": normalize_phone(request.customer_phone),
                        }
                    ],
                }
            },
            mutation=True,
        )
        result = data.get("clientCreate")
        errors = _user_errors(result)
        client = result.get("client") if isinstance(result, dict) else None
        if errors or not isinstance(client, dict) or not client.get("id"):
            raise BookingProviderError(
                "Jobber client creation failed", code="jobber_client_create"
            )
        return str(client["id"])

    def _resolve_client(
        self, connection: dict[str, Any], request: BookingRequest
    ) -> str:
        matches = self._find_clients_by_phone(connection, request.customer_phone)
        if len(matches) > 1:
            raise BookingProviderError(
                "More than one Jobber client has this phone number",
                code="jobber_ambiguous_client",
            )
        if matches:
            return str(matches[0]["id"])
        return self._create_client(connection, request)

    def _resolve_property(
        self,
        connection: dict[str, Any],
        request: BookingRequest,
        client_id: str,
    ) -> str:
        data = self._graphql.execute(
            connection, PROPERTIES_QUERY, {"clientId": client_id}
        )
        client = data.get("client")
        collection = (
            client.get("clientProperties") if isinstance(client, dict) else None
        )
        if isinstance(collection, dict):
            page_info = collection.get("pageInfo")
            if isinstance(page_info, dict) and page_info.get("hasNextPage"):
                raise BookingProviderError(
                    "Jobber property search was incomplete",
                    code="jobber_property_pagination",
                )
            expected = normalize_address(request.customer_address)
            for prop in collection.get("nodes", []):
                if (
                    isinstance(prop, dict)
                    and prop.get("id")
                    and normalize_address(_address_text(prop.get("address"))) == expected
                ):
                    return str(prop["id"])

        created = self._graphql.execute(
            connection,
            PROPERTY_CREATE_MUTATION,
            {
                "input": {
                    "clientId": client_id,
                    "address": {"street1": request.customer_address},
                }
            },
            mutation=True,
        )
        result = created.get("propertyCreate")
        errors = _user_errors(result)
        prop = result.get("property") if isinstance(result, dict) else None
        if errors or not isinstance(prop, dict) or not prop.get("id"):
            raise BookingProviderError(
                "Jobber property creation failed", code="jobber_property_create"
            )
        return str(prop["id"])

    def _reconcile(
        self,
        connection: dict[str, Any],
        *,
        client_id: str,
        marker: str,
    ) -> tuple[str | None, str | None]:
        data = self._graphql.execute(
            connection, JOB_RECONCILE_QUERY, {"clientId": client_id}
        )
        client = data.get("client")
        jobs = client.get("jobs") if isinstance(client, dict) else None
        if not isinstance(jobs, dict):
            return None, None
        if isinstance(jobs.get("pageInfo"), dict) and jobs["pageInfo"].get("hasNextPage"):
            return None, None
        for job in jobs.get("nodes", []):
            if not isinstance(job, dict):
                continue
            if marker not in str(job.get("instructions") or ""):
                continue
            visits = job.get("visits")
            visit_id = None
            if isinstance(visits, dict):
                visit_id = next(
                    (
                        str(visit["id"])
                        for visit in visits.get("nodes", [])
                        if isinstance(visit, dict)
                        and visit.get("id")
                        and marker in str(visit.get("instructions") or "")
                    ),
                    None,
                )
            return str(job.get("id")), visit_id
        return None, None

    def create_booking(
        self,
        connection: dict[str, Any],
        request: BookingRequest,
        idempotency_key: str,
    ) -> ProviderBooking:
        self._require_verified_availability(connection)
        marker = f"[Vigil:{request.booking_id}]"
        client_id = self._resolve_client(connection, request)
        property_id = self._resolve_property(connection, request, client_id)
        instructions = "\n".join(
            value
            for value in [
                marker,
                f"Customer phone: {request.customer_phone}",
                f"Address: {request.customer_address}",
                f"Urgency: {request.urgency or 'routine'}",
                request.notes,
            ]
            if value
        )

        try:
            data = self._graphql.execute(
                connection,
                JOB_CREATE_MUTATION,
                {
                    "input": {
                        "clientId": client_id,
                        "propertyId": property_id,
                        "title": request.service.display_name,
                        "instructions": instructions,
                        "jobType": "ONE_OFF",
                    }
                },
                mutation=True,
            )
        except UnknownBookingOutcomeError as unknown_error:
            try:
                job_id, visit_id = self._reconcile(
                    connection, client_id=client_id, marker=marker
                )
            except Exception:
                raise unknown_error
            if not job_id:
                raise unknown_error
            if visit_id:
                return ProviderBooking(
                    provider="jobber",
                    status="confirmed",
                    external_client_id=client_id,
                    external_property_id=property_id,
                    external_job_id=job_id,
                    external_visit_id=visit_id,
                )
        else:
            result = data.get("jobCreate")
            errors = _user_errors(result)
            job = result.get("job") if isinstance(result, dict) else None
            if errors or not isinstance(job, dict) or not job.get("id"):
                raise BookingProviderError(
                    "Jobber job creation failed", code="jobber_job_create"
                )
            job_id = str(job["id"])

        try:
            visit_data = self._graphql.execute(
                connection,
                VISIT_CREATE_MUTATION,
                {
                    "input": {
                        "jobId": job_id,
                        "title": request.service.display_name,
                        "instructions": instructions,
                        "startAt": request.slot.starts_at.isoformat(),
                        "endAt": request.slot.ends_at.isoformat(),
                        "assignedUserIds": [request.resource_id],
                    }
                },
                mutation=True,
            )
        except UnknownBookingOutcomeError as unknown_error:
            try:
                reconciled_job_id, visit_id = self._reconcile(
                    connection, client_id=client_id, marker=marker
                )
            except Exception:
                raise unknown_error
            if not visit_id:
                raise unknown_error
            job_id = reconciled_job_id or job_id
        else:
            result = visit_data.get("visitCreate")
            errors = _user_errors(result)
            visit = result.get("visit") if isinstance(result, dict) else None
            if errors or not isinstance(visit, dict) or not visit.get("id"):
                raise BookingProviderError(
                    "Jobber visit creation failed", code="jobber_visit_create"
                )
            visit_id = str(visit["id"])

        return ProviderBooking(
            provider="jobber",
            status="confirmed",
            external_client_id=client_id,
            external_property_id=property_id,
            external_job_id=job_id,
            external_visit_id=visit_id,
            metadata={"vigil_marker": marker, "idempotency_key": idempotency_key},
        )

    def disconnect(self, connection: dict[str, Any]) -> None:
        data = self._graphql.execute(
            connection,
            APP_DISCONNECT_MUTATION,
            mutation=True,
        )
        errors = _user_errors(data.get("appDisconnect"))
        if errors:
            raise BookingProviderError(
                "Jobber disconnect failed", code="jobber_disconnect"
            )
