from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values
from fastapi import FastAPI, Request, Response
from supabase import Client, create_client

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
env = dotenv_values(ENV_PATH)

app = FastAPI()

SUPABASE_URL = env.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    print(f"Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in {ENV_PATH}.")


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


def upsert_lead(client_id: str, phone_number: str) -> dict[str, Any]:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    response = (
        supabase.table("leads")
        .upsert(
            {
                "client_id": client_id,
                "phone_number": phone_number,
                "status": "missed_call",
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

    except Exception as exc:
        print("Failed to log voice webhook in Supabase:", repr(exc))

    return hangup_twiml()


@app.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()

    sender = form_value(form, "From")
    twilio_number = form_value(form, "To")
    body = form_value(form, "Body")

    print("SMS webhook received")
    print("From:", sender)
    print("To:", twilio_number)
    print("Body:", body)

    return empty_twiml()
