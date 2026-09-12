from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from supabase import Client

from app.booking.credentials import ConnectionCredentialManager
from app.booking.domain import BookingProvider, BookingResource
from app.booking.google import GoogleCalendarProvider
from app.booking.jobber import JobberGraphQLClient, JobberProvider
from app.booking.oauth import OAuthClient, OAuthProviderConfig, OAuthTokenSet
from app.booking.security import TokenCipher, generate_secret, hash_secret
from app.repositories import booking_config as booking_config_repo
from app.repositories import booking_connections as connections_repo
from app.repositories import booking_services as services_repo
from app.repositories import booking_setup as setup_repo
from app.repositories import clients as clients_repo
from app.services.booking_workflow import BookingOrchestrator


EnvironmentReader = Callable[[str], str | None]


GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.freebusy",
)


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    oauth: OAuthClient
    provider: BookingProvider
    pkce: bool


@dataclass(frozen=True, slots=True)
class SetupSession:
    client: dict[str, Any]
    token_row: dict[str, Any]

    @property
    def client_id(self) -> str:
        return str(self.client["id"])


@dataclass(frozen=True, slots=True)
class SetupView:
    session: SetupSession
    connections: list[dict[str, Any]]
    config: dict[str, Any] | None
    services: list[dict[str, Any]]
    resources: list[BookingResource]


class BookingRuntime:
    def __init__(
        self,
        supabase: Client,
        *,
        cipher: TokenCipher,
        session_secret: str,
        public_base_url: str,
        provider_bundles: dict[str, ProviderBundle],
        secure_cookies: bool = True,
    ) -> None:
        self.supabase = supabase
        self.cipher = cipher
        self.session_secret = session_secret
        self.public_base_url = public_base_url.rstrip("/")
        self.provider_bundles = provider_bundles
        self.secure_cookies = secure_cookies

    def booking_orchestrator(self) -> BookingOrchestrator:
        return BookingOrchestrator(
            self.supabase,
            {
                provider_name: bundle.provider
                for provider_name, bundle in self.provider_bundles.items()
            },
        )

    def create_setup_link(self, client_id: str, *, valid_hours: int = 24) -> str:
        raw_token = generate_secret()
        setup_repo.create_setup_token(
            self.supabase,
            client_id=client_id,
            token_hash=hash_secret(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=valid_hours),
        )
        return f"{self.public_base_url}/booking/setup/claim?token={raw_token}"

    def claim_setup_token(self, raw_token: str) -> str | None:
        row = setup_repo.find_setup_token(
            self.supabase, token_hash=hash_secret(raw_token)
        )
        if row is None or not _setup_token_is_claimable(row):
            return None
        session_token = generate_secret()
        claimed = setup_repo.claim_setup_token(
            self.supabase,
            token_id=str(row["id"]),
            session_hash=hash_secret(session_token),
            session_expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        return session_token if claimed is not None else None

    def get_setup_session(self, raw_session: str | None) -> SetupSession | None:
        if not raw_session:
            return None
        row = setup_repo.find_setup_session(
            self.supabase, session_hash=hash_secret(raw_session)
        )
        if row is None or not _setup_session_is_active(row):
            return None
        client = clients_repo.get_client_by_id(self.supabase, str(row["client_id"]))
        if client is None:
            return None
        return SetupSession(client=client, token_row=row)

    def setup_view(self, session: SetupSession) -> SetupView:
        connections = connections_repo.list_client_connections(
            self.supabase, client_id=session.client_id
        )
        config = booking_config_repo.get_client_config(
            self.supabase, client_id=session.client_id
        )
        services = services_repo.list_services(
            self.supabase, client_id=session.client_id
        )
        resources: list[BookingResource] = []
        if config and config.get("connection_id"):
            connection = next(
                (
                    item
                    for item in connections
                    if str(item.get("id")) == str(config["connection_id"])
                ),
                None,
            )
            if connection and connection.get("status") in {
                "connected",
                "availability_unsupported",
            }:
                bundle = self.provider_bundles.get(str(connection.get("provider")))
                if bundle:
                    try:
                        resources = bundle.provider.list_resources(connection)
                    except Exception:
                        resources = []
        return SetupView(session, connections, config, services, resources)

    def start_oauth(self, session: SetupSession, provider_name: str) -> str:
        bundle = self.provider_bundles.get(provider_name)
        if bundle is None:
            raise ValueError("Provider is not configured")
        state = generate_secret()
        state_hash = hash_secret(state)
        redirect_uri = (
            f"{self.public_base_url}/booking/setup/oauth/{provider_name}/callback"
        )
        code_verifier = generate_secret(48) if bundle.pkce else None
        encrypted_verifier = (
            self.cipher.encrypt(code_verifier, context=f"oauth-state:{state_hash}")
            if code_verifier
            else None
        )
        setup_repo.create_oauth_state(
            self.supabase,
            client_id=session.client_id,
            provider=provider_name,
            state_hash=state_hash,
            redirect_uri=redirect_uri,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            code_verifier_encrypted=encrypted_verifier,
        )
        challenge = None
        if code_verifier:
            digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return bundle.oauth.authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
        )

    def finish_oauth(
        self, provider_name: str, *, state: str, code: str
    ) -> dict[str, Any]:
        bundle = self.provider_bundles.get(provider_name)
        if bundle is None:
            raise ValueError("Provider is not configured")
        state_hash = hash_secret(state)
        state_row = setup_repo.consume_oauth_state(
            self.supabase, state_hash=state_hash
        )
        if state_row is None or not _oauth_state_is_valid(state_row, provider_name):
            raise ValueError("OAuth state is invalid or expired")
        verifier = None
        encrypted_verifier = state_row.get("code_verifier_encrypted")
        if isinstance(encrypted_verifier, str):
            verifier = self.cipher.decrypt(
                encrypted_verifier, context=f"oauth-state:{state_hash}"
            )
        tokens = bundle.oauth.exchange_code(
            code=code,
            redirect_uri=str(state_row["redirect_uri"]),
            code_verifier=verifier,
        )
        pending = connections_repo.upsert_connection(
            self.supabase,
            client_id=str(state_row["client_id"]),
            provider=provider_name,
            values={"status": "pending"},
        )
        connection_id = str(pending["id"])
        credential_manager = _credential_manager(
            self.supabase, self.cipher
        )
        encrypted = credential_manager.encrypt_tokens(
            connection_id=connection_id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
        )
        expires_at = _token_expiry(tokens, provider_name)
        connection = connections_repo.update_connection(
            self.supabase,
            connection_id=connection_id,
            values={
                **encrypted,
                "token_expires_at": expires_at.isoformat() if expires_at else None,
                "scopes": list(tokens.scopes),
                "token_version": int(pending.get("token_version") or 1) + 1,
                "status": "connected",
            },
        )
        if connection is None:
            raise RuntimeError("Provider connection could not be stored")
        validation = bundle.provider.validate_connection(connection)
        updated = connections_repo.update_connection(
            self.supabase,
            connection_id=connection_id,
            values={
                "status": validation.status,
                "provider_account_id": validation.account_id,
                "provider_account_name": validation.account_name,
                "last_validated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        final_connection = updated or connection
        booking_config_repo.upsert_client_config(
            self.supabase,
            client_id=str(state_row["client_id"]),
            values={
                "connection_id": connection_id,
                "mode": "disabled",
                "enabled": False,
            },
        )
        return final_connection

    def save_configuration(
        self,
        session: SetupSession,
        *,
        connection_id: str,
        resource_id: str,
        resource_name: str,
        timezone_name: str,
        mode: str,
        service_values: dict[str, Any],
    ) -> None:
        connection = connections_repo.get_connection(
            self.supabase, connection_id=connection_id
        )
        if connection is None or str(connection.get("client_id")) != session.client_id:
            raise ValueError("Provider connection does not belong to this client")
        if mode not in {"disabled", "shadow", "live"}:
            raise ValueError("Booking mode is invalid")
        if mode == "live" and connection.get("status") != "connected":
            raise ValueError("Provider is not ready for live booking")
        booking_config_repo.upsert_client_config(
            self.supabase,
            client_id=session.client_id,
            values={
                "connection_id": connection_id,
                "resource_id": resource_id,
                "resource_name": resource_name,
                "timezone": timezone_name,
                "mode": mode,
                "enabled": mode != "disabled",
            },
        )
        service_key = str(service_values.pop("service_key"))
        services_repo.upsert_service(
            self.supabase,
            client_id=session.client_id,
            service_key=service_key,
            values=service_values,
        )

    def disconnect(self, session: SetupSession, connection_id: str) -> None:
        connection = connections_repo.get_connection(
            self.supabase, connection_id=connection_id
        )
        if connection is None or str(connection.get("client_id")) != session.client_id:
            raise ValueError("Provider connection does not belong to this client")
        bundle = self.provider_bundles.get(str(connection.get("provider")))
        if bundle and hasattr(bundle.provider, "disconnect"):
            getattr(bundle.provider, "disconnect")(connection)
        connections_repo.update_connection(
            self.supabase,
            connection_id=connection_id,
            values={
                "status": "disconnected",
                "access_token_encrypted": None,
                "refresh_token_encrypted": None,
                "token_expires_at": None,
            },
        )
        booking_config_repo.upsert_client_config(
            self.supabase,
            client_id=session.client_id,
            values={"connection_id": None, "mode": "disabled", "enabled": False},
        )


def _credential_manager(
    supabase: Client, cipher: TokenCipher
) -> ConnectionCredentialManager:
    return ConnectionCredentialManager(
        cipher,
        update_connection=lambda connection_id, values: connections_repo.update_connection(
            supabase, connection_id=connection_id, values=values
        ),
    )


def build_booking_runtime(
    supabase: Client | None,
    env_value: EnvironmentReader,
) -> BookingRuntime | None:
    encryption_key = env_value("BOOKING_TOKEN_ENCRYPTION_KEY")
    session_secret = env_value("BOOKING_SESSION_SECRET")
    public_base_url = env_value("PUBLIC_BASE_URL")
    if not supabase or not encryption_key or not session_secret or not public_base_url:
        return None

    cipher = TokenCipher.from_base64(encryption_key)
    credentials = _credential_manager(supabase, cipher)
    bundles: dict[str, ProviderBundle] = {}
    google_id = env_value("GOOGLE_CLIENT_ID")
    google_secret = env_value("GOOGLE_CLIENT_SECRET")
    if google_id and google_secret:
        google_oauth = OAuthClient(
            OAuthProviderConfig(
                authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                client_id=google_id,
                client_secret=google_secret,
                scopes=GOOGLE_SCOPES,
                authorization_params={
                    "access_type": "offline",
                    "include_granted_scopes": "true",
                    "prompt": "consent",
                },
            )
        )
        bundles["google"] = ProviderBundle(
            google_oauth,
            GoogleCalendarProvider(credentials, google_oauth),
            True,
        )

    jobber_id = env_value("JOBBER_CLIENT_ID")
    jobber_secret = env_value("JOBBER_CLIENT_SECRET")
    if jobber_id and jobber_secret:
        jobber_oauth = OAuthClient(
            OAuthProviderConfig(
                authorization_url="https://api.getjobber.com/api/oauth/authorize",
                token_url="https://api.getjobber.com/api/oauth/token",
                client_id=jobber_id,
                client_secret=jobber_secret,
            )
        )
        graphql = JobberGraphQLClient(
            credentials,
            jobber_oauth,
            api_version=env_value("JOBBER_GRAPHQL_VERSION") or "2025-04-16",
        )
        bundles["jobber"] = ProviderBundle(
            jobber_oauth,
            JobberProvider(graphql),
            False,
        )

    return BookingRuntime(
        supabase,
        cipher=cipher,
        session_secret=session_secret,
        public_base_url=public_base_url,
        provider_bundles=bundles,
        secure_cookies=public_base_url.startswith("https://"),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _setup_token_is_claimable(row: dict[str, Any] | None) -> bool:
    if row is None or row.get("claimed_at") or row.get("revoked_at"):
        return False
    expires_at = _parse_datetime(row.get("expires_at"))
    return expires_at is not None and expires_at > datetime.now(timezone.utc)


def _setup_session_is_active(row: dict[str, Any] | None) -> bool:
    if row is None or row.get("revoked_at") or not row.get("claimed_at"):
        return False
    expires_at = _parse_datetime(row.get("session_expires_at"))
    return expires_at is not None and expires_at > datetime.now(timezone.utc)


def _oauth_state_is_valid(row: dict[str, Any] | None, provider_name: str) -> bool:
    if row is None or str(row.get("provider")) != provider_name:
        return False
    expires_at = _parse_datetime(row.get("expires_at"))
    return expires_at is not None and expires_at > datetime.now(timezone.utc)


def _token_expiry(tokens: OAuthTokenSet, provider_name: str) -> datetime | None:
    if tokens.expires_at:
        return tokens.expires_at
    if provider_name == "jobber":
        return datetime.now(timezone.utc) + timedelta(minutes=55)
    return None
