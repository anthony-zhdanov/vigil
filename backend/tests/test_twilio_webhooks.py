from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app import main


class TwilioWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.saved_attrs = {
            name: getattr(main, name)
            for name in [
                "PUBLIC_BASE_URL",
                "TWILIO_STATUS_CALLBACK_URL",
                "TWILIO_VALIDATE_SIGNATURE",
                "twilio_validator",
                "supabase",
                "twilio_client",
                "authorize_voice_number",
                "begin_twilio_webhook_event",
                "mark_twilio_webhook_event_processed",
                "process_missed_call",
                "update_message_status_by_twilio_sid",
                "process_inbound_sms",
            ]
        }

        main.PUBLIC_BASE_URL = None
        main.TWILIO_STATUS_CALLBACK_URL = None
        main.TWILIO_VALIDATE_SIGNATURE = False
        main.twilio_validator = None
        main.supabase = object()
        main.authorize_voice_number = lambda twilio_number: {
            "id": "client_1",
            "business_name": "Acme Plumbing",
        }
        main._process_local_webhook_events.clear()

    def tearDown(self) -> None:
        for name, value in self.saved_attrs.items():
            setattr(main, name, value)
        main._process_local_webhook_events.clear()

    def test_status_webhook_rejects_invalid_signature(self) -> None:
        main.TWILIO_VALIDATE_SIGNATURE = True
        main.twilio_validator = RequestValidator("test-auth-token")
        main.PUBLIC_BASE_URL = "https://vigil.example"

        response = self.client.post(
            "/webhooks/twilio/status",
            data={"MessageSid": "SM123", "MessageStatus": "delivered"},
            headers={"X-Twilio-Signature": "invalid"},
        )

        self.assertEqual(response.status_code, 403)

    def test_voice_webhook_rejects_unauthorized_voice_number(self) -> None:
        calls: dict[str, Any] = {"begin": 0, "processed": 0, "marked": 0}

        main.authorize_voice_number = lambda twilio_number: None
        main.begin_twilio_webhook_event = (
            lambda *args, **kwargs: calls.__setitem__("begin", calls["begin"] + 1)
            or ("event-1", True)
        )
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.process_missed_call = (
            lambda *args, **kwargs: calls.__setitem__(
                "processed", calls["processed"] + 1
            )
            or SimpleNamespace(processed=True, ignored_reason=None)
        )

        response = self.client.post(
            "/webhooks/twilio/voice",
            data={
                "From": "+14165550100",
                "To": "+14165550200",
                "CallSid": "CA123",
                "CallStatus": "no-answer",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("<Reject reason=\"rejected\"/>", response.text)
        self.assertNotIn("<Say", response.text)
        self.assertEqual(calls, {"begin": 0, "processed": 0, "marked": 0})

    def test_authorize_voice_number_uses_strict_phone_number_lookup(self) -> None:
        calls: list[dict[str, Any]] = []
        original_lookup = main.client_repository.find_client_for_voice_number
        main.authorize_voice_number = self.saved_attrs["authorize_voice_number"]

        def lookup(supabase: Any, twilio_number: str, **kwargs: Any) -> dict[str, str]:
            calls.append({"twilio_number": twilio_number, **kwargs})
            return {"id": "client_1"}

        main.client_repository.find_client_for_voice_number = lookup
        try:
            client = main.authorize_voice_number("+14165550200")
        finally:
            main.client_repository.find_client_for_voice_number = original_lookup

        self.assertEqual(client, {"id": "client_1"})
        self.assertEqual(
            calls,
            [{"twilio_number": "+14165550200", "legacy_fallback": False}],
        )

    def test_duplicate_voice_webhook_does_not_resend_recovery_sms(self) -> None:
        calls: dict[str, Any] = {"begin": [], "processed": 0, "marked": 0}

        def begin_event(
            *, event_type: str, provider_event_id: str | None, raw_payload: dict[str, str]
        ) -> tuple[str, bool]:
            calls["begin"].append((event_type, provider_event_id))
            return "event-1", len(calls["begin"]) == 1

        main.begin_twilio_webhook_event = begin_event
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.process_missed_call = (
            lambda *args, **kwargs: calls.__setitem__(
                "processed", calls["processed"] + 1
            )
            or SimpleNamespace(processed=True, ignored_reason=None)
        )

        payload = {
            "From": "+14165550100",
            "To": "+14165550200",
            "CallSid": "CA123",
            "CallStatus": "no-answer",
        }

        first = self.client.post("/webhooks/twilio/voice", data=payload)
        second = self.client.post("/webhooks/twilio/voice", data=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIn("<Say voice=\"alice\">", first.text)
        self.assertIn("<Hangup/>", first.text)
        self.assertEqual(calls["begin"], [("voice:no-answer", "CA123")] * 2)
        self.assertEqual(calls["processed"], 1)
        self.assertEqual(calls["marked"], 1)

    def test_voice_webhook_accepts_caller_called_aliases(self) -> None:
        calls: dict[str, Any] = {"kwargs": [], "marked": 0}

        def begin_event(
            *, event_type: str, provider_event_id: str | None, raw_payload: dict[str, str]
        ) -> tuple[str, bool]:
            return "event-1", True

        def process_voice(*args: Any, **kwargs: Any) -> Any:
            calls["kwargs"].append(kwargs)
            return SimpleNamespace(processed=True, ignored_reason=None)

        main.begin_twilio_webhook_event = begin_event
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.process_missed_call = process_voice

        response = self.client.post(
            "/webhooks/twilio/voice",
            data={
                "Caller": "+14165550100",
                "Called": "+14165550200",
                "CallSid": "CA123",
                "CallStatus": "ringing",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["kwargs"][0]["from_phone"], "+14165550100")
        self.assertEqual(calls["kwargs"][0]["to_phone"], "+14165550200")
        self.assertEqual(calls["kwargs"][0]["authorized_client"]["id"], "client_1")
        self.assertEqual(calls["marked"], 1)

    def test_voice_webhook_exception_does_not_mark_event_processed(self) -> None:
        calls: dict[str, Any] = {"marked": 0}

        def begin_event(
            *, event_type: str, provider_event_id: str | None, raw_payload: dict[str, str]
        ) -> tuple[str, bool]:
            return "event-1", True

        def process_voice(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("service failed")

        main.begin_twilio_webhook_event = begin_event
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.process_missed_call = process_voice

        response = self.client.post(
            "/webhooks/twilio/voice",
            data={
                "From": "+14165550100",
                "To": "+14165550200",
                "CallSid": "CA123",
                "CallStatus": "ringing",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["marked"], 0)

    def test_duplicate_sms_webhook_does_not_duplicate_side_effects(self) -> None:
        calls: dict[str, Any] = {"begin": [], "processed": 0, "marked": 0}

        class SmsWorkflowResult:
            processed = True
            inbound_message_id = "message-1"
            matched_node_key = "handoff"
            ignored_reason = None

        def begin_event(
            *, event_type: str, provider_event_id: str | None, raw_payload: dict[str, str]
        ) -> tuple[str, bool]:
            calls["begin"].append((event_type, provider_event_id))
            return "event-1", len(calls["begin"]) == 1

        main.begin_twilio_webhook_event = begin_event
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.process_inbound_sms = (
            lambda *args, **kwargs: calls.__setitem__(
                "processed", calls["processed"] + 1
            )
            or SmsWorkflowResult()
        )

        payload = {
            "From": "+14165550100",
            "To": "+14165550200",
            "Body": "Yes, I still need help.",
            "MessageSid": "SMIN123",
        }

        first = self.client.post("/webhooks/twilio/sms", data=payload)
        second = self.client.post("/webhooks/twilio/sms", data=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(calls["begin"], [("sms:inbound", "SMIN123")] * 2)
        self.assertEqual(calls["processed"], 1)
        self.assertEqual(calls["marked"], 1)

    def test_sms_webhook_passes_parsed_mms_media_to_workflow(self) -> None:
        calls: dict[str, Any] = {"kwargs": [], "marked": 0}

        class SmsWorkflowResult:
            processed = True
            inbound_message_id = "message-1"
            matched_node_key = "handoff"
            ignored_reason = None

        def begin_event(
            *, event_type: str, provider_event_id: str | None, raw_payload: dict[str, str]
        ) -> tuple[str, bool]:
            return "event-1", True

        def process_sms(*args: Any, **kwargs: Any) -> SmsWorkflowResult:
            calls["kwargs"].append(kwargs)
            return SmsWorkflowResult()

        main.begin_twilio_webhook_event = begin_event
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.process_inbound_sms = process_sms

        payload = {
            "From": "+14165550100",
            "To": "+14165550200",
            "Body": "Photo attached",
            "MessageSid": "SMIN123",
            "NumMedia": "2",
            "MediaUrl0": "https://api.twilio.com/media/ME0",
            "MediaContentType0": "image/jpeg",
            "MediaUrl1": "https://api.twilio.com/media/ME1",
            "MediaContentType1": "image/png",
        }

        response = self.client.post("/webhooks/twilio/sms", data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["marked"], 1)
        media = calls["kwargs"][0]["media"]
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0].twilio_media_url, "https://api.twilio.com/media/ME0")
        self.assertEqual(media[0].content_type, "image/jpeg")
        self.assertEqual(media[1].twilio_media_url, "https://api.twilio.com/media/ME1")
        self.assertEqual(media[1].content_type, "image/png")

    def test_status_webhook_updates_message_status_by_twilio_sid(self) -> None:
        calls: dict[str, Any] = {"begin": [], "updates": [], "marked": 0}

        def begin_event(
            *, event_type: str, provider_event_id: str | None, raw_payload: dict[str, str]
        ) -> tuple[str, bool]:
            calls["begin"].append((event_type, provider_event_id))
            return "event-1", True

        main.begin_twilio_webhook_event = begin_event
        main.mark_twilio_webhook_event_processed = (
            lambda event_id: calls.__setitem__("marked", calls["marked"] + 1)
        )
        main.update_message_status_by_twilio_sid = (
            lambda twilio_message_sid, message_status: calls["updates"].append(
                (twilio_message_sid, message_status)
            )
        )

        response = self.client.post(
            "/webhooks/twilio/status",
            data={"MessageSid": "SMOUT123", "MessageStatus": "delivered"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["begin"], [("sms:status:delivered", "SMOUT123")])
        self.assertEqual(calls["updates"], [("SMOUT123", "delivered")])
        self.assertEqual(calls["marked"], 1)

    def test_send_sms_includes_status_callback_when_public_base_url_is_set(self) -> None:
        class FakeMessage:
            sid = "SMOUT123"

        class FakeMessages:
            def __init__(self) -> None:
                self.kwargs: dict[str, str] | None = None

            def create(self, **kwargs: str) -> FakeMessage:
                self.kwargs = kwargs
                return FakeMessage()

        class FakeTwilioClient:
            def __init__(self) -> None:
                self.messages = FakeMessages()

        fake_twilio = FakeTwilioClient()
        main.twilio_client = fake_twilio
        main.PUBLIC_BASE_URL = "https://vigil.example"

        sid = main.send_sms("+14165550200", "+14165550100", "Hello")

        self.assertEqual(sid, "SMOUT123")
        self.assertEqual(
            fake_twilio.messages.kwargs,
            {
                "from_": "+14165550200",
                "to": "+14165550100",
                "body": "Hello",
                "status_callback": "https://vigil.example/webhooks/twilio/status",
            },
        )


if __name__ == "__main__":
    unittest.main()
