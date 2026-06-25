from __future__ import annotations

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.services.missed_call_recovery import process_missed_call


class FakeTwilioMessages:
    def __init__(self, sent_messages: list[dict[str, Any]]) -> None:
        self.sent_messages = sent_messages

    def create(self, **kwargs: Any) -> Any:
        self.sent_messages.append(dict(kwargs))
        return SimpleNamespace(sid=f"SM_OUT_{len(self.sent_messages)}")


class FakeTwilioClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.messages = FakeTwilioMessages(self.sent_messages)


class MissedCallHarness:
    def __init__(
        self,
        *,
        known_client: bool = True,
        existing_lead: bool = False,
        active_conversation: bool = False,
        existing_opt_out: bool = False,
        recent_recovery_sms: bool = False,
    ) -> None:
        self.supabase = object()
        self.twilio = FakeTwilioClient()
        self.customer_phone = "+14165550123"
        self.twilio_number = "+16475550199"
        self.call_sid = "CA_IN_1"
        self.call_status = "no-answer"
        self.known_client = known_client
        self.existing_lead = existing_lead
        self.has_active_conversation = active_conversation
        self.existing_opt_out = existing_opt_out
        self.recent_recovery_sms = recent_recovery_sms
        self.client = {"id": "client_1", "business_name": "Acme Plumbing"}
        self.lead = {
            "id": "lead_1",
            "client_id": "client_1",
            "phone_number": self.customer_phone,
            "status": "missed_call",
        }
        self.conversation = {
            "id": "conversation_1",
            "client_id": "client_1",
            "lead_id": "lead_1",
            "channel": "sms",
            "status": "waiting_for_customer",
            "current_state": "awaiting_location"
            if active_conversation
            else "awaiting_initial_reply",
            "collected_info": {"job_type": "drain_or_sewer"}
            if active_conversation
            else {},
            "closed_at": None if active_conversation else None,
        }
        self.client_lookups: list[dict[str, Any]] = []
        self.lead_lookups: list[dict[str, Any]] = []
        self.lead_upserts: list[dict[str, Any]] = []
        self.active_conversation_gets: list[dict[str, Any]] = []
        self.conversation_creates: list[dict[str, Any]] = []
        self.call_events: list[dict[str, Any]] = []
        self.opt_out_checks: list[dict[str, Any]] = []
        self.duplicate_checks: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.conversation_updates: list[dict[str, Any]] = []

    def patchers(self) -> list[Any]:
        return [
            patch(
                "app.services.missed_call_recovery.clients_repo.find_client_for_voice_number",
                side_effect=self.find_client_for_voice_number,
            ),
            patch(
                "app.services.missed_call_recovery.leads_repo.get_lead_by_client_phone",
                side_effect=self.get_lead_by_client_phone,
            ),
            patch(
                "app.services.missed_call_recovery.leads_repo.upsert_lead",
                side_effect=self.upsert_lead,
            ),
            patch(
                "app.services.missed_call_recovery.conversations_repo.get_active_conversation",
                side_effect=self.get_active_conversation,
            ),
            patch(
                "app.services.missed_call_recovery.conversations_repo.create_conversation",
                side_effect=self.create_conversation,
            ),
            patch(
                "app.services.missed_call_recovery.call_events_repo.insert_call_event",
                side_effect=self.insert_call_event,
            ),
            patch(
                "app.services.missed_call_recovery.opt_outs_repo.is_opted_out",
                side_effect=self.is_opted_out,
            ),
            patch(
                "app.services.missed_call_recovery.messages_repo.recent_recovery_sms_exists",
                side_effect=self.recent_recovery_sms_exists,
            ),
            patch(
                "app.services.missed_call_recovery.messages_repo.insert_message",
                side_effect=self.insert_message,
            ),
            patch(
                "app.services.missed_call_recovery.conversations_repo.update_conversation",
                side_effect=self.update_conversation,
            ),
            patch(
                "app.services.missed_call_recovery.now_iso",
                return_value="2026-06-17T00:00:00+00:00",
            ),
        ]

    def process(self) -> Any:
        with ExitStack() as stack:
            for patcher in self.patchers():
                stack.enter_context(patcher)
            return process_missed_call(
                self.supabase,
                self.twilio,
                from_phone=self.customer_phone,
                to_phone=self.twilio_number,
                call_sid=self.call_sid,
                call_status=self.call_status,
                raw_payload={"From": self.customer_phone, "To": self.twilio_number},
                status_callback_url="https://vigil.example/webhooks/twilio/status",
            )

    def outbound_messages(self) -> list[dict[str, Any]]:
        return [
            message for message in self.messages if message["direction"] == "outbound"
        ]

    def find_client_for_voice_number(
        self, supabase: Any, twilio_number: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        self.client_lookups.append({"twilio_number": twilio_number, **kwargs})
        if not self.known_client:
            return None
        return self.client if twilio_number == self.twilio_number else None

    def get_lead_by_client_phone(
        self, supabase: Any, client_id: str, phone_number: str
    ) -> dict[str, Any] | None:
        self.lead_lookups.append(
            {"client_id": client_id, "phone_number": phone_number}
        )
        if not self.existing_lead:
            return None
        return dict(self.lead)

    def upsert_lead(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.lead_upserts.append(dict(kwargs))
        self.lead.update(kwargs)
        return dict(self.lead)

    def get_active_conversation(
        self, supabase: Any, **kwargs: Any
    ) -> dict[str, Any] | None:
        self.active_conversation_gets.append(dict(kwargs))
        if self.has_active_conversation:
            return dict(self.conversation)
        return None

    def create_conversation(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.conversation_creates.append(dict(kwargs))
        self.conversation.update(kwargs)
        return dict(self.conversation)

    def insert_call_event(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        row = {"id": f"call_event_{len(self.call_events) + 1}", **kwargs}
        self.call_events.append(row)
        return row

    def is_opted_out(self, supabase: Any, **kwargs: Any) -> bool:
        self.opt_out_checks.append(dict(kwargs))
        return self.existing_opt_out

    def recent_recovery_sms_exists(self, supabase: Any, **kwargs: Any) -> bool:
        self.duplicate_checks.append(dict(kwargs))
        return self.recent_recovery_sms

    def insert_message(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        row = {"id": f"message_{len(self.messages) + 1}", **kwargs}
        self.messages.append(row)
        return row

    def update_conversation(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.conversation_updates.append(dict(kwargs))
        self.conversation.update(
            {key: value for key, value in kwargs.items() if key != "conversation_id"}
        )
        return dict(self.conversation)


class MissedCallRecoveryTests(unittest.TestCase):
    def test_known_client_sends_and_logs_initial_sms(self) -> None:
        harness = MissedCallHarness()

        result = harness.process()

        self.assertTrue(result.processed)
        self.assertEqual(result.client_id, "client_1")
        self.assertEqual(result.lead_id, "lead_1")
        self.assertEqual(result.conversation_id, "conversation_1")
        self.assertEqual(result.outbound_status, "sent")
        self.assertEqual(
            harness.client_lookups,
            [{"twilio_number": harness.twilio_number, "legacy_fallback": True}],
        )
        self.assertEqual(
            harness.lead_upserts[0],
            {
                "client_id": "client_1",
                "phone_number": harness.customer_phone,
                "status": "missed_call",
            },
        )
        self.assertEqual(harness.call_events[0]["client_id"], "client_1")
        self.assertEqual(harness.call_events[0]["lead_id"], "lead_1")
        self.assertEqual(harness.call_events[0]["from_phone"], harness.customer_phone)
        self.assertEqual(harness.call_events[0]["to_phone"], harness.twilio_number)
        self.assertEqual(
            harness.conversation_creates[0],
            {
                "client_id": "client_1",
                "lead_id": "lead_1",
                "channel": "sms",
                "status": "waiting_for_customer",
                "current_state": "awaiting_initial_reply",
                "collected_info": {},
                "summary": "",
            },
        )

        outbound = harness.outbound_messages()[0]
        self.assertEqual(outbound["template_key"], "missed_call_initial")
        self.assertEqual(outbound["status"], "sent")
        self.assertEqual(outbound["from_phone"], harness.twilio_number)
        self.assertEqual(outbound["to_phone"], harness.customer_phone)
        self.assertIn("Acme Plumbing", outbound["body"])
        self.assertEqual(outbound["twilio_message_sid"], "SM_OUT_1")
        self.assertEqual(
            harness.twilio.sent_messages[0],
            {
                "from_": harness.twilio_number,
                "to": harness.customer_phone,
                "body": outbound["body"],
                "status_callback": "https://vigil.example/webhooks/twilio/status",
            },
        )
        self.assertEqual(
            harness.conversation_updates[-1],
            {
                "conversation_id": "conversation_1",
                "status": "waiting_for_customer",
                "current_state": "awaiting_initial_reply",
                "last_message_at": "2026-06-17T00:00:00+00:00",
            },
        )

    def test_unknown_number_logs_call_without_customer_sms(self) -> None:
        harness = MissedCallHarness(known_client=False)

        result = harness.process()

        self.assertFalse(result.processed)
        self.assertEqual(result.ignored_reason, "unknown_twilio_number")
        self.assertEqual(len(harness.lead_upserts), 0)
        self.assertEqual(len(harness.active_conversation_gets), 0)
        self.assertEqual(len(harness.conversation_creates), 0)
        self.assertEqual(len(harness.twilio.sent_messages), 0)
        self.assertEqual(len(harness.messages), 0)
        self.assertEqual(harness.call_events[0]["client_id"], None)
        self.assertEqual(harness.call_events[0]["lead_id"], None)
        self.assertEqual(harness.call_events[0]["from_phone"], harness.customer_phone)
        self.assertEqual(harness.call_events[0]["to_phone"], harness.twilio_number)

    def test_opted_out_caller_skips_recovery_sms(self) -> None:
        harness = MissedCallHarness(existing_opt_out=True)

        result = harness.process()

        self.assertFalse(result.processed)
        self.assertEqual(result.ignored_reason, "opted_out")
        self.assertEqual(len(harness.twilio.sent_messages), 0)
        self.assertEqual(len(harness.messages), 0)
        self.assertEqual(len(harness.duplicate_checks), 0)
        self.assertEqual(len(harness.conversation_creates), 0)
        self.assertEqual(len(harness.conversation_updates), 0)
        self.assertEqual(harness.opt_out_checks[0]["phone_number"], harness.customer_phone)
        self.assertEqual(harness.call_events[0]["client_id"], "client_1")

    def test_active_conversation_preserves_state_without_restarting_intake(self) -> None:
        harness = MissedCallHarness(existing_lead=True, active_conversation=True)

        result = harness.process()

        self.assertFalse(result.processed)
        self.assertEqual(result.ignored_reason, "active_conversation_exists")
        self.assertEqual(result.conversation_id, "conversation_1")
        self.assertEqual(len(harness.lead_upserts), 0)
        self.assertEqual(len(harness.duplicate_checks), 0)
        self.assertEqual(len(harness.conversation_creates), 0)
        self.assertEqual(len(harness.conversation_updates), 0)
        self.assertEqual(len(harness.twilio.sent_messages), 0)
        self.assertEqual(harness.conversation["current_state"], "awaiting_location")
        self.assertEqual(
            harness.conversation["collected_info"], {"job_type": "drain_or_sewer"}
        )

    def test_duplicate_suppression_skips_recovery_sms(self) -> None:
        harness = MissedCallHarness(recent_recovery_sms=True)

        result = harness.process()

        self.assertFalse(result.processed)
        self.assertEqual(result.ignored_reason, "recent_recovery_sms_exists")
        self.assertEqual(len(harness.twilio.sent_messages), 0)
        self.assertEqual(len(harness.messages), 0)
        self.assertEqual(len(harness.conversation_creates), 0)
        self.assertEqual(len(harness.conversation_updates), 0)
        self.assertEqual(
            harness.duplicate_checks[0],
            {
                "client_id": "client_1",
                "lead_id": "lead_1",
                "duplicate_suppression_minutes": 60,
            },
        )

    def test_previous_caller_without_active_conversation_starts_new_intake(self) -> None:
        harness = MissedCallHarness(existing_lead=True)

        result = harness.process()

        self.assertTrue(result.processed)
        self.assertEqual(result.conversation_id, "conversation_1")
        self.assertEqual(len(harness.conversation_creates), 1)
        self.assertEqual(len(harness.conversation_updates), 1)
        self.assertEqual(
            harness.lead_upserts[-1],
            {
                "client_id": "client_1",
                "phone_number": harness.customer_phone,
                "status": "missed_call",
            },
        )
        self.assertEqual(
            harness.conversation_creates[0]["current_state"], "awaiting_initial_reply"
        )
        self.assertEqual(harness.conversation_creates[0]["collected_info"], {})


if __name__ == "__main__":
    unittest.main()
