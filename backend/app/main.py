import hashlib
import json
import os
import socket
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from fastapi import FastAPI, Request, Response
from supabase import Client, create_client
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient

from app.repositories import messages as message_repository
from app.services.missed_call_recovery import process_missed_call
from app.services.sms_workflow import parse_inbound_media, process_inbound_sms

try:
    from app.repositories import webhook_events as webhook_event_repository
except ImportError:
    webhook_event_repository = None

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
env = dotenv_values(ENV_PATH)

app = FastAPI()


def env_value(key: str) -> str | None:
    return os.getenv(key) or env.get(key)


def env_flag(key: str, default: bool = False) -> bool:
    value = env_value(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def force_ipv4_dns_resolution() -> None:
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4_only(*args: Any, **kwargs: Any) -> Any:
        return [
            result
            for result in original_getaddrinfo(*args, **kwargs)
            if result[0] == socket.AF_INET
        ]

    socket.getaddrinfo = getaddrinfo_ipv4_only


SUPABASE_URL = env_value("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = env_value("SUPABASE_SERVICE_ROLE_KEY") or env_value(
    "SUPABASE_KEY"
)
TWILIO_ACCOUNT_SID = env_value("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env_value("TWILIO_AUTH_TOKEN")
TWILIO_FORCE_IPV4 = env_flag("TWILIO_FORCE_IPV4")
PUBLIC_BASE_URL = (
    env_value("PUBLIC_BASE_URL")
    or env_value("PUBLIC_WEBHOOK_BASE_URL")
    or env_value("WEBHOOK_BASE_URL")
)
TWILIO_STATUS_CALLBACK_URL = env_value("TWILIO_STATUS_CALLBACK_URL")
RUNTIME_ENV = (
    env_value("APP_ENV") or env_value("ENVIRONMENT") or env_value("ENV") or "production"
).strip().lower()
LOCAL_RUNTIME_ENVIRONMENTS = {"local", "development", "dev", "test", "testing"}
TWILIO_VALIDATE_SIGNATURE_SETTING = (
    env_value("TWILIO_VALIDATE_SIGNATURE") or "true"
).strip().lower()
TWILIO_SIGNATURE_VALIDATION_DISABLED = TWILIO_VALIDATE_SIGNATURE_SETTING in {
    "false",
    "0",
    "no",
    "off",
}
TWILIO_VALIDATE_SIGNATURE = not (
    TWILIO_SIGNATURE_VALIDATION_DISABLED and RUNTIME_ENV in LOCAL_RUNTIME_ENVIRONMENTS
)
TWILIO_STATUS_CALLBACK_PATH = "/webhooks/twilio/status"
_process_local_webhook_events: set[tuple[str, str, str]] = set()

if TWILIO_SIGNATURE_VALIDATION_DISABLED and TWILIO_VALIDATE_SIGNATURE:
    print(
        "TWILIO_VALIDATE_SIGNATURE=false ignored because runtime environment is not local/development/test."
    )

if TWILIO_FORCE_IPV4:
    force_ipv4_dns_resolution()
    print("TWILIO_FORCE_IPV4 enabled; outbound HTTP DNS resolution is IPv4-only.")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    print(
        f"Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in {ENV_PATH}."
    )

twilio_client: TwilioClient | None = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    print(
        f"Twilio is not configured. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in {ENV_PATH}."
    )

twilio_validator: RequestValidator | None = (
    RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None
)


def end_call_twiml() -> Response:
    # Answer, then hang up. The call arrives via carrier conditional forwarding,
    # and carrier voicemail ANSWERS forwarded calls -- that's how the carrier knows
    # the call was handled and stops ringing the client. <Reject> declines the call,
    # so the carrier treats the forward as failed and keeps ringing the client.
    # <Say> forces a clean answer (releasing the original leg); <Hangup> then ends
    # the call. The recovery SMS does the real work.
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Thanks for calling. We'll text you right back.</Say>
    <Hangup/>
</Response>"""
    return Response(content=twiml, media_type="text/xml")


def empty_twiml() -> Response:
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
    return Response(content=twiml, media_type="text/xml")


def form_value(form: Any, key: str) -> str | None:
    value = form.get(key)
    return value if isinstance(value, str) else None


def form_to_dict(form: Any) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key in form.keys():
        value = form.get(key)
        if value is not None:
            payload[str(key)] = str(value)
    return payload


def public_request_url(request: Request) -> str:
    path = request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL.rstrip('/')}{path}{query}"

    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}{path}{query}"


def twilio_signature_is_valid(request: Request, params: dict[str, str]) -> bool:
    if not TWILIO_VALIDATE_SIGNATURE:
        return True

    if twilio_validator is None:
        print("Twilio signature validation is enabled but auth token is missing.")
        return False

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False
    return bool(twilio_validator.validate(public_request_url(request), params, signature))


def request_hash_from_payload(payload: dict[str, str]) -> str:
    if webhook_event_repository is not None:
        return str(webhook_event_repository.request_hash_from_payload(payload))

    normalized = json.dumps(
        {str(key): str(value) for key, value in sorted(payload.items())},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def begin_twilio_webhook_event(
    *,
    event_type: str,
    provider_event_id: str | None,
    raw_payload: dict[str, str],
) -> tuple[str | None, bool]:
    request_hash = request_hash_from_payload(raw_payload)

    if supabase is not None and webhook_event_repository is not None:
        try:
            event, is_new = webhook_event_repository.begin_webhook_event(
                supabase,
                provider="twilio",
                event_type=event_type,
                provider_event_id=provider_event_id,
                request_hash=request_hash,
            )
            raw_event_id = event.get("id")
            event_id = str(raw_event_id) if raw_event_id is not None else None
            return event_id, is_new
        except Exception as exc:
            print(
                "Persistent webhook idempotency unavailable; using process-local guard:",
                repr(exc),
            )

    key = ("twilio", event_type, provider_event_id or request_hash)
    if key in _process_local_webhook_events:
        return None, False
    _process_local_webhook_events.add(key)
    return None, True


def mark_twilio_webhook_event_processed(event_id: str | None) -> None:
    if event_id is None or supabase is None or webhook_event_repository is None:
        return

    try:
        webhook_event_repository.mark_webhook_event_processed(
            supabase, event_id=event_id
        )
    except Exception as exc:
        print("Failed to mark Twilio webhook event processed:", repr(exc))


def twilio_status_callback_url() -> str | None:
    if TWILIO_STATUS_CALLBACK_URL:
        return TWILIO_STATUS_CALLBACK_URL
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL.rstrip('/')}{TWILIO_STATUS_CALLBACK_PATH}"
    return None


def update_message_status_by_twilio_sid(
    twilio_message_sid: str, message_status: str
) -> None:
    message_repository.update_message_status(
        supabase, twilio_message_sid=twilio_message_sid, status=message_status
    )


def send_sms(
    from_phone: str,
    to_phone: str,
    body: str,
    *,
    status_callback: str | None = None,
) -> str:
    if twilio_client is None:
        raise RuntimeError("Twilio is not configured")

    create_kwargs = {"from_": from_phone, "to": to_phone, "body": body}
    callback_url = status_callback or twilio_status_callback_url()
    if callback_url:
        create_kwargs["status_callback"] = callback_url

    message = twilio_client.messages.create(**create_kwargs)
    sid = getattr(message, "sid", None)
    return str(sid) if sid else ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhooks/twilio/voice")
async def twilio_voice_webhook(request: Request):
    form = await request.form()
    raw_payload = form_to_dict(form)

    if not twilio_signature_is_valid(request, raw_payload):
        print("Rejected Twilio voice webhook: invalid signature.")
        return Response(status_code=403)

    caller = form_value(form, "From") or form_value(form, "Caller")
    twilio_number = form_value(form, "To") or form_value(form, "Called")
    call_sid = form_value(form, "CallSid")
    call_status = form_value(form, "CallStatus")

    webhook_event_id, should_process = begin_twilio_webhook_event(
        event_type=f"voice:{call_status or 'unknown'}",
        provider_event_id=call_sid,
        raw_payload=raw_payload,
    )
    if not should_process:
        print("Duplicate Twilio voice webhook suppressed.")
        return end_call_twiml()

    print("Voice webhook received")
    print("From:", caller)
    print("To:", twilio_number)
    print("CallSid:", call_sid)
    print("CallStatus:", call_status)

    webhook_processed = False
    try:
        result = process_missed_call(
            supabase,
            twilio_client,
            from_phone=caller,
            to_phone=twilio_number,
            call_sid=call_sid,
            call_status=call_status,
            raw_payload=raw_payload,
            status_callback_url=twilio_status_callback_url(),
        )
        if result.processed:
            print("Missed-call recovery SMS processed")
        else:
            print("Missed-call recovery skipped:", result.ignored_reason)
        webhook_processed = True

    except Exception as exc:
        print("Failed to log voice webhook in Supabase:", repr(exc))
    finally:
        if webhook_processed:
            mark_twilio_webhook_event_processed(webhook_event_id)

    return end_call_twiml()


@app.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()
    raw_payload = form_to_dict(form)

    if not twilio_signature_is_valid(request, raw_payload):
        print("Rejected Twilio SMS webhook: invalid signature.")
        return Response(status_code=403)

    sender = form_value(form, "From")
    twilio_number = form_value(form, "To")
    body = form_value(form, "Body") or ""
    message_sid = form_value(form, "MessageSid")
    media = parse_inbound_media(raw_payload)

    webhook_event_id, should_process = begin_twilio_webhook_event(
        event_type="sms:inbound",
        provider_event_id=message_sid,
        raw_payload=raw_payload,
    )
    if not should_process:
        print("Duplicate Twilio SMS webhook suppressed.")
        return empty_twiml()

    print("SMS webhook received")
    print("From:", sender)
    print("To:", twilio_number)
    print("Body length:", len(body))
    print("MessageSid:", message_sid)
    print("Media count:", len(media))

    if not sender or not twilio_number:
        print("Missing From or To in Twilio SMS webhook; skipping database insert.")
        mark_twilio_webhook_event_processed(webhook_event_id)
        return empty_twiml()

    webhook_processed = False
    try:
        if supabase is None:
            print("Supabase not configured; skipping SMS database insert.")
            return empty_twiml()

        result = process_inbound_sms(
            supabase,
            twilio_client,
            from_phone=sender,
            to_phone=twilio_number,
            body=body,
            message_sid=message_sid,
            raw_payload=raw_payload,
            media=media,
            status_callback_url=twilio_status_callback_url(),
        )
        if result.processed:
            print("SMS decision-tree workflow processed")
            print("Inbound message:", result.inbound_message_id)
            print("Matched node:", result.matched_node_key)
        else:
            print("SMS workflow skipped:", result.ignored_reason)
        webhook_processed = True

    except Exception as exc:
        print("Failed to process SMS webhook:", repr(exc))
    finally:
        if webhook_processed:
            mark_twilio_webhook_event_processed(webhook_event_id)

    return empty_twiml()


@app.post("/webhooks/twilio/status")
async def twilio_status_webhook(request: Request):
    form = await request.form()
    raw_payload = form_to_dict(form)

    if not twilio_signature_is_valid(request, raw_payload):
        print("Rejected Twilio status webhook: invalid signature.")
        return Response(status_code=403)

    message_sid = form_value(form, "MessageSid")
    message_status = form_value(form, "MessageStatus") or form_value(form, "SmsStatus")

    webhook_event_id, should_process = begin_twilio_webhook_event(
        event_type=f"sms:status:{message_status or 'unknown'}",
        provider_event_id=message_sid,
        raw_payload=raw_payload,
    )
    if not should_process:
        print("Duplicate Twilio status webhook suppressed.")
        return empty_twiml()

    webhook_processed = False
    try:
        if not message_sid or not message_status:
            print("Missing MessageSid or MessageStatus in Twilio status webhook.")
            return empty_twiml()

        if supabase is None:
            print("Supabase not configured; skipping status update.")
            return empty_twiml()

        update_message_status_by_twilio_sid(message_sid, message_status)
        print("Twilio message status updated:", message_status)
        webhook_processed = True

    except Exception as exc:
        print("Failed to process Twilio status webhook:", repr(exc))
    finally:
        if webhook_processed:
            mark_twilio_webhook_event_processed(webhook_event_id)

    return empty_twiml()
