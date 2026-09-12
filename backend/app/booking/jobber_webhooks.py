from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from supabase import Client

from app.repositories import booking_config as booking_config_repo
from app.repositories import booking_connections as connections_repo
from app.repositories import webhook_events as webhook_events_repo


ProcessorGetter = Callable[[], "JobberWebhookProcessor | None"]


def verify_jobber_signature(raw_body: bytes, signature: str, client_secret: str) -> bool:
    digest = hmac.new(
        client_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def _webhook_payload(value: dict[str, Any]) -> dict[str, Any] | None:
    data = value.get("data")
    event = data.get("webHookEvent") if isinstance(data, dict) else None
    return event if isinstance(event, dict) else None


class JobberWebhookProcessor:
    def __init__(self, supabase: Client, *, client_secret: str) -> None:
        self.supabase = supabase
        self.client_secret = client_secret

    def accepts_signature(self, raw_body: bytes, signature: str) -> bool:
        return verify_jobber_signature(raw_body, signature, self.client_secret)

    def store(self, raw_body: bytes, payload: dict[str, Any]) -> tuple[str | None, bool]:
        event = _webhook_payload(payload)
        if event is None:
            raise ValueError("Jobber webhook payload is invalid")
        event_type = str(event.get("topic") or "UNKNOWN")
        identity = "|".join(
            str(event.get(key) or "")
            for key in ("topic", "accountId", "itemId", "occurredAt", "occuredAt")
        )
        provider_event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        request_hash = hashlib.sha256(raw_body).hexdigest()
        row, is_new = webhook_events_repo.begin_webhook_event(
            self.supabase,
            provider="jobber",
            event_type=event_type,
            provider_event_id=provider_event_id,
            request_hash=request_hash,
            raw_payload=payload,
        )
        raw_id = row.get("id")
        return (str(raw_id) if raw_id else None), is_new

    def process_pending(self, limit: int = 25) -> None:
        rows = webhook_events_repo.list_unprocessed_webhook_events(
            self.supabase, provider="jobber", limit=limit
        )
        for row in rows:
            event_id = str(row.get("id") or "")
            if not event_id:
                continue
            try:
                self._process_event(row)
            except Exception as exc:
                webhook_events_repo.mark_webhook_event_failed(
                    self.supabase,
                    event_id=event_id,
                    attempts=int(row.get("attempts") or 0) + 1,
                    error_code=type(exc).__name__,
                )
                continue
            webhook_events_repo.mark_webhook_event_processed(
                self.supabase, event_id=event_id
            )

    def _process_event(self, row: dict[str, Any]) -> None:
        payload = row.get("raw_payload")
        event = _webhook_payload(payload) if isinstance(payload, dict) else None
        if event is None:
            raise ValueError("Stored Jobber webhook payload is invalid")
        if str(event.get("topic")) != "APP_DISCONNECT":
            return
        account_id = str(event.get("accountId") or "")
        if not account_id:
            raise ValueError("Jobber disconnect webhook has no account id")
        connection = connections_repo.get_by_provider_account_id(
            self.supabase,
            provider="jobber",
            provider_account_id=account_id,
        )
        if connection is None:
            return
        connection_id = str(connection["id"])
        connections_repo.update_connection(
            self.supabase,
            connection_id=connection_id,
            values={
                "status": "revoked",
                "access_token_encrypted": None,
                "refresh_token_encrypted": None,
                "token_expires_at": None,
            },
        )
        booking_config_repo.disable_connection(
            self.supabase, connection_id=connection_id
        )


def create_jobber_webhook_router(get_processor: ProcessorGetter) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/jobber")
    async def jobber_webhook(
        request: Request, background_tasks: BackgroundTasks
    ) -> Response:
        processor = get_processor()
        if processor is None:
            raise HTTPException(status_code=503, detail="Jobber webhooks are unavailable")
        raw_body = await request.body()
        signature = request.headers.get("X-Jobber-Hmac-SHA256", "")
        if not signature or not processor.accepts_signature(raw_body, signature):
            raise HTTPException(status_code=401, detail="Invalid Jobber signature")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        try:
            processor.store(raw_body, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        background_tasks.add_task(processor.process_pending)
        return Response(status_code=202)

    return router
