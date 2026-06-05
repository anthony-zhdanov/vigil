import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values
from fastapi import FastAPI, Request, Response
from supabase import Client, create_client
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator

from app.decision_tree.contract import DecisionAction, DecisionResult
from app.decision_tree.interpreter import run_plumbing_decision_tree
from app.decision_tree.templates.base import render_template as render_sms_template

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

# Twilio webhook signature validation. On by default (secure); set
# TWILIO_VALIDATE_SIGNATURE=false to disable for local curl testing.
TWILIO_VALIDATE_SIGNATURE = (
    env_value("TWILIO_VALIDATE_SIGNATURE") or "true"
).strip().lower() not in {"false", "0", "no", "off"}
# The public base URL Twilio calls (e.g. https://<sub>.ngrok-free.dev or your cloud
# domain). The signature is computed over the exact public URL, which a proxy hides
# from the app; set this for reliable validation. Falls back to X-Forwarded-* headers.
PUBLIC_BASE_URL = env_value("PUBLIC_BASE_URL")

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

twilio_validator: RequestValidator | None = None
if TWILIO_AUTH_TOKEN:
    twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN)


def public_request_url(request: Request) -> str:
    # Twilio signs the exact public URL it called. Behind ngrok / a cloud proxy the
    # app only sees an internal http URL, so reconstruct the public one here.
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
    # Verify the X-Twilio-Signature header so only genuine Twilio requests are
    # processed. Without this, anyone who learns the URL can forge missed-call
    # webhooks and make us send SMS to arbitrary numbers (toll fraud / SMS pumping).
    if not TWILIO_VALIDATE_SIGNATURE:
        return True
    if twilio_validator is None:
        print(
            "Twilio signature validation is enabled but TWILIO_AUTH_TOKEN is missing; rejecting."
        )
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    return twilio_validator.validate(public_request_url(request), params, signature)


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


def get_or_create_conversation(
    *,
    client_id: str,
    lead_id: str,
    reopen_closed: bool = False,
    initial_state: str = "awaiting_initial_reply",
) -> dict[str, Any]:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    response = (
        supabase.table("conversations")
        .select("*")
        .eq("client_id", client_id)
        .eq("lead_id", lead_id)
        .eq("channel", "sms")
        .limit(1)
        .execute()
    )
    if response.data:
        conversation = response.data[0]
        if not isinstance(conversation, dict):
            raise RuntimeError("Supabase returned a non-object conversation row")

        if reopen_closed and conversation.get("status") == "closed":
            update_payload = {
                "status": "waiting_for_customer",
                "current_state": initial_state,
                "collected_info": {},
                "summary": None,
                "last_message_at": now_iso(),
                "closed_at": None,
            }
            update_response = (
                supabase.table("conversations")
                .update(update_payload)
                .eq("id", conversation["id"])
                .execute()
            )
            if update_response.data and isinstance(update_response.data[0], dict):
                return cast(dict[str, Any], update_response.data[0])
            conversation.update(update_payload)

        return cast(dict[str, Any], conversation)

    insert_response = (
        supabase.table("conversations")
        .insert(
            {
                "client_id": client_id,
                "lead_id": lead_id,
                "channel": "sms",
                "status": "waiting_for_customer",
                "current_state": initial_state,
                "collected_info": {},
                "last_message_at": now_iso(),
            }
        )
        .execute()
    )
    if not insert_response.data or not isinstance(insert_response.data[0], dict):
        raise RuntimeError("Supabase did not return a conversation row")
    return cast(dict[str, Any], insert_response.data[0])


def update_conversation(
    *,
    conversation_id: str,
    state: str,
    status: str,
    collected_info: dict[str, Any],
    summary: str,
) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    payload: dict[str, Any] = {
        "current_state": state,
        "status": status,
        "collected_info": collected_info,
        "summary": summary,
        "last_message_at": now_iso(),
        "closed_at": now_iso() if status == "closed" else None,
    }
    supabase.table("conversations").update(payload).eq("id", conversation_id).execute()


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
    conversation_id: str | None = None,
    direction: str,
    from_phone: str,
    to_phone: str,
    body: str,
    twilio_message_sid: str | None,
    template_key: str | None,
    status: str,
    raw_payload: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    payload = {
        "client_id": client_id,
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "direction": direction,
        "from_phone": from_phone,
        "to_phone": to_phone,
        "body": body,
        "twilio_message_sid": twilio_message_sid,
        "template_key": template_key,
        "status": status,
        "raw_payload": raw_payload,
    }
    response = supabase.table("messages").insert(payload).execute()
    if response.data and isinstance(response.data[0], dict):
        return cast(dict[str, Any], response.data[0])
    return None


def update_lead_status(lead_id: str, status: str, summary: str | None = None) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    payload = {"status": status, "updated_at": now_iso()}
    if summary is not None:
        payload["summary"] = summary
    supabase.table("leads").update(payload).eq("id", lead_id).execute()


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


def create_opt_out(
    client_id: str, lead_id: str, phone_number: str, reason: str
) -> None:
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
    conversation_id: str | None = None,
    from_phone: str,
    to_phone: str,
    body: str,
    template_key: str,
) -> dict[str, Any] | None:
    status = "sent"
    message_sid: str | None = None
    try:
        message_sid = send_sms(from_phone, to_phone, body)
    except Exception as exc:
        status = "failed"
        print("Failed to send SMS through Twilio:", repr(exc))

    return insert_message(
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        direction="outbound",
        from_phone=from_phone,
        to_phone=to_phone,
        body=body,
        twilio_message_sid=message_sid,
        template_key=template_key,
        status=status,
    )


def insert_decision_tree_run(
    *,
    client_id: str,
    lead_id: str,
    conversation_id: str,
    inbound_message_id: str | None,
    result: DecisionResult,
) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    supabase.table("decision_tree_runs").insert(
        {
            "client_id": client_id,
            "lead_id": lead_id,
            "conversation_id": conversation_id,
            "inbound_message_id": inbound_message_id,
            "decision_tree_key": result.tree_key,
            "decision_tree_version": result.tree_version,
            "classifier_output": result.classifier_output.to_dict(),
            "matched_node_key": result.matched_node_key,
            "actions_json": result.actions_json(),
            "result_status": result.conversation_status,
        }
    ).execute()


def insert_owner_notification(
    *,
    client_id: str,
    lead_id: str,
    conversation_id: str,
    recipient: str | None,
    priority: str,
    body: str,
    status: str,
    raw_response: dict[str, Any] | None = None,
) -> None:
    if supabase is None:
        raise RuntimeError("Supabase is not configured")

    supabase.table("owner_notifications").insert(
        {
            "client_id": client_id,
            "lead_id": lead_id,
            "conversation_id": conversation_id,
            "channel": "sms",
            "recipient": recipient,
            "priority": priority,
            "body": body,
            "status": status,
            "raw_response": raw_response,
        }
    ).execute()


def notify_owner(
    *,
    client: dict[str, Any],
    lead_id: str,
    conversation_id: str,
    lead_phone: str,
    twilio_number: str,
    priority: str,
    result: DecisionResult,
    latest_message: str,
) -> None:
    client_id = str(client["id"])
    owner_phone = client.get("owner_phone")
    recipient = str(owner_phone) if owner_phone else None
    body = render_sms_template(
        "owner_notification",
        client=client,
        lead_phone=lead_phone,
        collected_info=result.collected_info,
        classifier_output=result.classifier_output,
        latest_message=latest_message,
        priority=priority,
        summary=result.summary,
    )

    status = "sent"
    raw_response: dict[str, Any] | None = None
    if not recipient:
        status = "failed"
        raw_response = {"error": "client_owner_phone_missing"}
    else:
        try:
            message_sid = send_sms(twilio_number, recipient, body)
            raw_response = {"twilio_message_sid": message_sid}
        except Exception as exc:
            status = "failed"
            raw_response = {"error": repr(exc)}
            print("Failed to send owner notification:", repr(exc))

    insert_owner_notification(
        client_id=client_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        recipient=recipient,
        priority=priority,
        body=body,
        status=status,
        raw_response=raw_response,
    )


def execute_decision_action(
    *,
    action: DecisionAction,
    result: DecisionResult,
    client: dict[str, Any],
    lead_id: str,
    conversation_id: str,
    lead_phone: str,
    twilio_number: str,
    latest_message: str,
) -> None:
    client_id = str(client["id"])

    if action.type == "send_sms_template" and action.template_key:
        body = render_sms_template(
            action.template_key,
            client=client,
            lead_phone=lead_phone,
            collected_info=result.collected_info,
            classifier_output=result.classifier_output,
            latest_message=latest_message,
            summary=result.summary,
        )
        send_and_log_sms(
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            from_phone=twilio_number,
            to_phone=lead_phone,
            body=body,
            template_key=action.template_key,
        )
        return

    if action.type == "mark_lead_status" and action.lead_status:
        update_lead_status(lead_id, action.lead_status, result.summary)
        return

    if action.type == "create_opt_out" and action.opt_out_reason:
        create_opt_out(client_id, lead_id, lead_phone, action.opt_out_reason)
        return

    if action.type == "notify_owner":
        notify_owner(
            client=client,
            lead_id=lead_id,
            conversation_id=conversation_id,
            lead_phone=lead_phone,
            twilio_number=twilio_number,
            priority=action.notification_priority or "normal",
            result=result,
            latest_message=latest_message,
        )
        return

    if action.type == "close_conversation":
        return


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
        return end_call_twiml()

    try:
        if supabase is None:
            print("Supabase not configured; skipping database insert.")
            return end_call_twiml()

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
            return end_call_twiml()

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
            conversation = get_or_create_conversation(
                client_id=client_id,
                lead_id=lead_id,
                reopen_closed=True,
                initial_state="awaiting_initial_reply",
            )
            conversation_id = str(conversation["id"])
            body = render_sms_template("missed_call_initial", client=client)
            send_and_log_sms(
                client_id=client_id,
                lead_id=lead_id,
                conversation_id=conversation_id,
                from_phone=twilio_number,
                to_phone=caller,
                body=body,
                template_key="missed_call_initial",
            )
            update_lead_status(lead_id, "texted")
            print("Recovery SMS processed")

    except Exception as exc:
        print("Failed to log voice webhook in Supabase:", repr(exc))

    return end_call_twiml()


@app.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()
    raw_payload = form_to_dict(form)

    if not twilio_signature_is_valid(request, raw_payload):
        print("Rejected Twilio sms webhook: invalid signature.")
        return Response(status_code=403)

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
        already_opted_out = is_opted_out(client_id, sender)
        conversation = get_or_create_conversation(
            client_id=client_id,
            lead_id=lead_id,
            reopen_closed=not already_opted_out,
            initial_state="awaiting_initial_reply",
        )
        conversation_id = str(conversation["id"])

        inbound_message = insert_message(
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            direction="inbound",
            from_phone=sender,
            to_phone=twilio_number,
            body=body,
            twilio_message_sid=message_sid,
            template_key=None,
            status="received",
            raw_payload=raw_payload,
        )
        inbound_message_id = (
            str(inbound_message["id"])
            if inbound_message and inbound_message.get("id")
            else None
        )

        if already_opted_out:
            print("Sender has opted out; skipping decision-tree response.")
            raw_collected_info = conversation.get("collected_info")
            collected_info = (
                cast(dict[str, Any], raw_collected_info)
                if isinstance(raw_collected_info, dict)
                else {}
            )
            update_conversation(
                conversation_id=conversation_id,
                state="closed",
                status="closed",
                collected_info=collected_info,
                summary="Inbound SMS received from opted-out number; no automated response sent.",
            )
            return empty_twiml()

        raw_collected_info = conversation.get("collected_info")
        collected_info = (
            cast(dict[str, Any], raw_collected_info)
            if isinstance(raw_collected_info, dict)
            else {}
        )
        result = run_plumbing_decision_tree(
            message_body=body,
            current_state=str(
                conversation.get("current_state") or "awaiting_initial_reply"
            ),
            collected_info=collected_info,
        )

        for action in result.actions:
            execute_decision_action(
                action=action,
                result=result,
                client=client,
                lead_id=lead_id,
                conversation_id=conversation_id,
                lead_phone=sender,
                twilio_number=twilio_number,
                latest_message=body,
            )

        update_conversation(
            conversation_id=conversation_id,
            state=result.conversation_state,
            status=result.conversation_status,
            collected_info=result.collected_info,
            summary=result.summary,
        )
        insert_decision_tree_run(
            client_id=client_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            inbound_message_id=inbound_message_id,
            result=result,
        )
        print(f"SMS decision-tree route processed: {result.matched_node_key}")

    except Exception as exc:
        print("Failed to process SMS webhook:", repr(exc))

    return empty_twiml()
