import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values
from fastapi import FastAPI, Request, Response
from supabase import Client, create_client
from twilio.rest import Client as TwilioClient

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
env = dotenv_values(ENV_PATH)

app = FastAPI()

def env_value(key: str) -> str | None:
    return os.getenv(key) or env.get(key)


SUPABASE_URL = env_value("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = env_value("SUPABASE_SERVICE_ROLE_KEY") or env_value(
    "SUPABASE_KEY"
)
TWILIO_ACCOUNT_SID = env_value("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env_value("TWILIO_AUTH_TOKEN")
DUPLICATE_SUPPRESSION_MINUTES = 60

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


def hangup_twiml() -> Response:
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Hangup/>
</Response>"""
    return Response(content=twiml, media_type="text/xml")


def empty_twiml() -> Response:
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
    return Response(content=twiml, media_type="text/xml")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def find_client_by_twilio_number(twilio_number: str) -> dict[str, Any] | None:
    if supabase is None:
        return None

    response = (
        supabase.table("clients")
        .select("*")
        .eq("twilio_phone", twilio_number)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None

    client = response.data[0]
    if not isinstance(client, dict):
        raise RuntimeError("Supabase returned a non-object client row")
    return cast(dict[str, Any], client)


def upsert_lead(
    client_id: str, phone_number: str, status: str = "missed_call"
) -> dict[str, Any]:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    response = (
        supabase.table("leads")
        .upsert(
            {
                "client_id": client_id,
                "phone_number": phone_number,
                "status": status,
                "updated_at": now_iso(),
            },
            on_conflict="client_id,phone_number",
        )
        .execute()
    )
    if not response.data:
        raise RuntimeError("Supabase did not return a lead row")

    lead = response.data[0]
    if not isinstance(lead, dict):
        raise RuntimeError("Supabase returned a non-object lead row")
    return cast(dict[str, Any], lead)


def insert_call_event(
    *,
    client_id: str | None,
    lead_id: str | None,
    from_phone: str,
    to_phone: str,
    call_sid: str | None,
    call_status: str | None,
    raw_payload: dict[str, str],
) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    supabase.table("call_events").insert(
        {
            "client_id": client_id,
            "lead_id": lead_id,
            "from_phone": from_phone,
            "to_phone": to_phone,
            "call_sid": call_sid,
            "call_status": call_status,
            "raw_payload": raw_payload,
        }
    ).execute()


def insert_message(
    *,
    client_id: str,
    lead_id: str,
    direction: str,
    from_phone: str,
    to_phone: str,
    body: str,
    twilio_message_sid: str | None,
    template_key: str | None,
    status: str,
    raw_payload: dict[str, str] | None = None,
) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    supabase.table("messages").insert(
        {
            "client_id": client_id,
            "lead_id": lead_id,
            "direction": direction,
            "from_phone": from_phone,
            "to_phone": to_phone,
            "body": body,
            "twilio_message_sid": twilio_message_sid,
            "template_key": template_key,
            "status": status,
            "raw_payload": raw_payload,
        }
    ).execute()


def update_lead_status(lead_id: str, status: str) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    supabase.table("leads").update({"status": status, "updated_at": now_iso()}).eq(
        "id", lead_id
    ).execute()


def is_opted_out(client_id: str, phone_number: str) -> bool:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    response = (
        supabase.table("opt_outs")
        .select("id")
        .eq("client_id", client_id)
        .eq("phone_number", phone_number)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def create_opt_out(client_id: str, lead_id: str, phone_number: str, reason: str) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    if is_opted_out(client_id, phone_number):
        return

    supabase.table("opt_outs").insert(
        {
            "client_id": client_id,
            "lead_id": lead_id,
            "phone_number": phone_number,
            "reason": reason,
        }
    ).execute()


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def recent_recovery_sms_exists(client_id: str, lead_id: str) -> bool:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    response = (
        supabase.table("messages")
        .select("created_at")
        .eq("client_id", client_id)
        .eq("lead_id", lead_id)
        .eq("direction", "outbound")
        .eq("template_key", "missed_call_initial")
        .eq("status", "sent")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return False

    message = response.data[0]
    if not isinstance(message, dict):
        return False

    created_at = parse_datetime(message.get("created_at"))
    if created_at is None:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=DUPLICATE_SUPPRESSION_MINUTES
    )
    return created_at >= cutoff


def render_template(template_key: str, client: dict[str, Any]) -> str:
    business_name = str(client.get("business_name") or "the team")
    templates = {
        "missed_call_initial": f"Hi, this is {business_name}. Sorry we missed your call — do you still need help? Reply here and we’ll get back to you.",
        "opt_out_confirm": "No problem — we won’t text this number again.",
        "handoff_to_team": "Thanks — we’ve passed this to the team and someone will follow up shortly.",
    }
    return templates[template_key]


def send_sms(from_phone: str, to_phone: str, body: str) -> str:
    if twilio_client is None:
        raise RuntimeError("Twilio is not configured")

    message = twilio_client.messages.create(from_=from_phone, to=to_phone, body=body)
    sid = getattr(message, "sid", None)
    return str(sid) if sid else ""


def send_and_log_sms(
    *,
    client_id: str,
    lead_id: str,
    from_phone: str,
    to_phone: str,
    body: str,
    template_key: str,
) -> None:
    status = "sent"
    message_sid: str | None = None
    try:
        message_sid = send_sms(from_phone, to_phone, body)
    except Exception as exc:
        status = "failed"
        print("Failed to send SMS through Twilio:", repr(exc))

    insert_message(
        client_id=client_id,
        lead_id=lead_id,
        direction="outbound",
        from_phone=from_phone,
        to_phone=to_phone,
        body=body,
        twilio_message_sid=message_sid,
        template_key=template_key,
        status=status,
    )


def route_sms_placeholder(body: str) -> tuple[str, str, str | None]:
    normalized = body.strip().lower()
    opt_out_keywords = {"stop", "unsubscribe", "cancel", "end", "quit"}
    if normalized in opt_out_keywords:
        return "opt_out_confirm", "opted_out", "stop"
    if "wrong number" in normalized or "wrong #" in normalized:
        return "opt_out_confirm", "wrong_number", "wrong_number"
    return "handoff_to_team", "needs_owner_call", None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhooks/twilio/voice")
async def twilio_voice_webhook(request: Request):
    form = await request.form()
    raw_payload = form_to_dict(form)

    caller = form_value(form, "From")
    twilio_number = form_value(form, "To")
    call_sid = form_value(form, "CallSid")
    call_status = form_value(form, "CallStatus")

    print("Voice webhook received")
    print("From:", caller)
    print("To:", twilio_number)
    print("CallSid:", call_sid)
    print("CallStatus:", call_status)

    if not caller or not twilio_number:
        print("Missing From or To in Twilio voice webhook; skipping database insert.")
        return hangup_twiml()

    try:
        if supabase is None:
            print("Supabase not configured; skipping database insert.")
            return hangup_twiml()

        client = find_client_by_twilio_number(twilio_number)
        if client is None:
            print(
                f"No client found for Twilio number {twilio_number}; logging unknown call."
            )
            insert_call_event(
                client_id=None,
                lead_id=None,
                from_phone=caller,
                to_phone=twilio_number,
                call_sid=call_sid,
                call_status=call_status,
                raw_payload=raw_payload,
            )
            return hangup_twiml()

        client_id = str(client["id"])
        lead = upsert_lead(client_id, caller)
        lead_id = str(lead["id"])
        insert_call_event(
            client_id=client_id,
            lead_id=lead_id,
            from_phone=caller,
            to_phone=twilio_number,
            call_sid=call_sid,
            call_status=call_status,
            raw_payload=raw_payload,
        )
        print("Call event logged in Supabase")

        if is_opted_out(client_id, caller):
            print("Caller has opted out; skipping recovery SMS.")
        elif recent_recovery_sms_exists(client_id, lead_id):
            print("Recent recovery SMS already sent; suppressing duplicate.")
        else:
            body = render_template("missed_call_initial", client)
            send_and_log_sms(
                client_id=client_id,
                lead_id=lead_id,
                from_phone=twilio_number,
                to_phone=caller,
                body=body,
                template_key="missed_call_initial",
            )
            print("Recovery SMS processed")

    except Exception as exc:
        print("Failed to log voice webhook in Supabase:", repr(exc))

    return hangup_twiml()


@app.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()
    raw_payload = form_to_dict(form)

    sender = form_value(form, "From")
    twilio_number = form_value(form, "To")
    body = form_value(form, "Body") or ""
    message_sid = form_value(form, "MessageSid")

    print("SMS webhook received")
    print("From:", sender)
    print("To:", twilio_number)
    print("Body:", body)
    print("MessageSid:", message_sid)

    if not sender or not twilio_number:
        print("Missing From or To in Twilio SMS webhook; skipping database insert.")
        return empty_twiml()

    try:
        if supabase is None:
            print("Supabase not configured; skipping SMS database insert.")
            return empty_twiml()

        client = find_client_by_twilio_number(twilio_number)
        if client is None:
            print(f"No client found for Twilio number {twilio_number}; ignoring SMS.")
            return empty_twiml()

        client_id = str(client["id"])
        lead = upsert_lead(client_id, sender, status="sms_reply")
        lead_id = str(lead["id"])

        insert_message(
            client_id=client_id,
            lead_id=lead_id,
            direction="inbound",
            from_phone=sender,
            to_phone=twilio_number,
            body=body,
            twilio_message_sid=message_sid,
            template_key=None,
            status="received",
            raw_payload=raw_payload,
        )

        template_key, lead_status, opt_out_reason = route_sms_placeholder(body)
        if opt_out_reason is not None:
            create_opt_out(client_id, lead_id, sender, opt_out_reason)
        elif is_opted_out(client_id, sender):
            print("Sender has opted out; skipping placeholder response.")
            return empty_twiml()

        update_lead_status(lead_id, lead_status)
        response_body = render_template(template_key, client)
        send_and_log_sms(
            client_id=client_id,
            lead_id=lead_id,
            from_phone=twilio_number,
            to_phone=sender,
            body=response_body,
            template_key=template_key,
        )
        print("SMS placeholder route processed")

    except Exception as exc:
        print("Failed to process SMS webhook:", repr(exc))

    return empty_twiml()
