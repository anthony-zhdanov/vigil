from fastapi import FastAPI, Request, Response

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhooks/twilio/voice")
async def twilio_voice_webhook(request: Request):
    form = await request.form()

    caller = form.get("From")
    twilio_number = form.get("To")
    call_sid = form.get("CallSid")

    print("Voice webhook received")
    print("From:", caller)
    print("To:", twilio_number)
    print("CallSid:", call_sid)

    twiml = """<?xml version = "1.0" encoding"UTF-8"?>
<Response>
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


@app.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(request: Request):
    form = await request.form()

    sender = form.get("From")
    twilio_number = form.get("To")
    body = form.get("Body")

    print("SMS webhook received")
    print("From:", sender)
    print("To:", twilio_number)
    print("Body:", body)

    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""

    return Response(content=twiml, media_type="text/xml")
