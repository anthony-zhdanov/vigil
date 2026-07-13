from __future__ import annotations

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.services.sms_workflow import process_inbound_sms


class FakeTwilioMessages:
    def __init__(
        self,
        sent_messages: list[dict[str, Any]],
        *,
        fail_to: set[str] | None = None,
    ) -> None:
        self.sent_messages = sent_messages
        self.fail_to = fail_to or set()

    def create(self, **kwargs: Any) -> Any:
        if kwargs.get("to") in self.fail_to:
            raise RuntimeError("Twilio owner send failed")
        self.sent_messages.append(dict(kwargs))
        return SimpleNamespace(sid=f"SM_OUT_{len(self.sent_messages)}")


class FakeTwilioClient:
    def __init__(self, *, fail_to: set[str] | None = None) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.messages = FakeTwilioMessages(self.sent_messages, fail_to=fail_to)


class WorkflowHarness:
    def __init__(
        self,
        *,
        existing_opt_out: bool = False,
        fail_owner_send: bool = False,
    ) -> None:
        self.supabase = object()
        self.customer_phone = "+14165550123"
        self.twilio_number = "+16475550199"
        self.client = {
            "id": "client_1",
            "business_name": "Acme Plumbing",
            "owner_phone": "+14165550999",
        }
        fail_to = {self.client["owner_phone"]} if fail_owner_send else set()
        self.twilio = FakeTwilioClient(fail_to=fail_to)
        self.lead = {
            "id": "lead_1",
            "client_id": "client_1",
            "phone_number": self.customer_phone,
            "status": "sms_reply",
        }
        self.conversation = {
            "id": "conversation_1",
            "client_id": "client_1",
            "lead_id": "lead_1",
            "channel": "sms",
            "status": "open",
            "current_state": "awaiting_initial_reply",
            "collected_info": {},
        }
        self.messages: list[dict[str, Any]] = []
        self.message_media: list[dict[str, Any]] = []
        self.decision_runs: list[dict[str, Any]] = []
        self.lead_status_updates: list[dict[str, Any]] = []
        self.conversation_updates: list[dict[str, Any]] = []
        self.close_conversation_calls: list[dict[str, Any]] = []
        self.opt_out_creates: list[dict[str, Any]] = []
        self.owner_notifications: list[dict[str, Any]] = []
        self.opted_out_numbers: set[str] = (
            {self.customer_phone} if existing_opt_out else set()
        )

    def patchers(self) -> list[Any]:
        return [
            patch(
                "app.services.sms_workflow.clients_repo.find_client_for_sms_number",
                side_effect=self.find_client_for_sms_number,
            ),
            patch(
                "app.services.sms_workflow.leads_repo.upsert_lead",
                side_effect=self.upsert_lead,
            ),
            patch(
                "app.services.sms_workflow.conversations_repo.get_or_create_active_conversation",
                side_effect=self.get_or_create_active_conversation,
            ),
            patch(
                "app.services.sms_workflow.conversations_repo.update_conversation",
                side_effect=self.update_conversation,
            ),
            patch(
                "app.services.sms_workflow.messages_repo.insert_message",
                side_effect=self.insert_message,
            ),
            patch(
                "app.services.sms_workflow.message_media_repo.insert_message_media",
                side_effect=self.insert_message_media,
            ),
            patch(
                "app.services.sms_workflow.decision_tree_runs_repo.insert_decision_tree_run",
                side_effect=self.insert_decision_tree_run,
            ),
            patch(
                "app.services.action_executor.opt_outs_repo.is_opted_out",
                side_effect=self.is_opted_out,
            ),
            patch(
                "app.services.action_executor.opt_outs_repo.create_opt_out",
                side_effect=self.create_opt_out,
            ),
            patch(
                "app.services.action_executor.leads_repo.update_lead_status",
                side_effect=self.update_lead_status,
            ),
            patch(
                "app.services.action_executor.conversations_repo.close_conversation",
                side_effect=self.close_conversation,
            ),
            patch(
                "app.services.action_executor.owner_notifications_repo.insert_owner_notification",
                side_effect=self.insert_owner_notification,
            ),
        ]

    def process(
        self,
        body: str,
        *,
        message_sid: str = "SM_IN_1",
        raw_payload: dict[str, Any] | None = None,
        status_callback_url: str | None = None,
    ) -> Any:
        payload = raw_payload or {
            "From": self.customer_phone,
            "To": self.twilio_number,
        }
        with ExitStack() as stack:
            for patcher in self.patchers():
                stack.enter_context(patcher)
            return process_inbound_sms(
                self.supabase,
                self.twilio,
                from_phone=self.customer_phone,
                to_phone=self.twilio_number,
                body=body,
                message_sid=message_sid,
                raw_payload=payload,
                status_callback_url=status_callback_url,
            )

    def inbound_messages(self) -> list[dict[str, Any]]:
        return [message for message in self.messages if message["direction"] == "inbound"]

    def outbound_messages(self) -> list[dict[str, Any]]:
        return [
            message for message in self.messages if message["direction"] == "outbound"
        ]

    def find_client_for_sms_number(
        self, supabase: Any, twilio_number: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        return self.client if twilio_number == self.twilio_number else None

    def upsert_lead(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.lead.update(kwargs)
        return dict(self.lead)

    def get_or_create_active_conversation(
        self, supabase: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return dict(self.conversation)

    def update_conversation(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.conversation_updates.append(dict(kwargs))
        self.conversation.update(
            {key: value for key, value in kwargs.items() if key != "conversation_id"}
        )
        return dict(self.conversation)

    def close_conversation(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.close_conversation_calls.append(dict(kwargs))
        self.conversation.update(
            {
                "status": "closed",
                "current_state": "closed",
                "collected_info": kwargs.get("collected_info"),
                "summary": kwargs.get("summary"),
            }
        )
        return dict(self.conversation)

    def insert_message(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        row = {"id": f"message_{len(self.messages) + 1}", **kwargs}
        self.messages.append(row)
        return row

    def insert_message_media(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        row = {"id": f"media_{len(self.message_media) + 1}", **kwargs}
        self.message_media.append(row)
        return row

    def insert_decision_tree_run(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        row = {"id": f"run_{len(self.decision_runs) + 1}", **kwargs}
        self.decision_runs.append(row)
        return row

    def is_opted_out(self, supabase: Any, **kwargs: Any) -> bool:
        return kwargs["phone_number"] in self.opted_out_numbers

    def create_opt_out(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.opted_out_numbers.add(kwargs["phone_number"])
        row = {"id": f"opt_out_{len(self.opt_out_creates) + 1}", **kwargs}
        self.opt_out_creates.append(row)
        return row

    def update_lead_status(self, supabase: Any, **kwargs: Any) -> dict[str, Any]:
        self.lead_status_updates.append(dict(kwargs))
        self.lead.update(kwargs)
        return dict(self.lead)

    def insert_owner_notification(
        self, supabase: Any, **kwargs: Any
    ) -> dict[str, Any]:
        row = {"id": f"notification_{len(self.owner_notifications) + 1}", **kwargs}
        self.owner_notifications.append(row)
        return row


class SmsWorkflowTests(unittest.TestCase):
    def test_normal_reply_collects_location_and_sends_request(self) -> None:
        harness = WorkflowHarness()

        result = harness.process("Yes, clogged drain")

        self.assertTrue(result.processed)
        self.assertEqual(result.matched_node_key, "collect_location")
        self.assertEqual(
            harness.decision_runs[0]["matched_node_key"], "collect_location"
        )
        self.assertEqual(
            harness.decision_runs[0]["inbound_message_id"],
            harness.inbound_messages()[0]["id"],
        )
        outbound = harness.outbound_messages()[0]
        self.assertEqual(outbound["template_key"], "request_location")
        self.assertEqual(outbound["status"], "sent")
        self.assertEqual(outbound["from_phone"], harness.twilio_number)
        self.assertEqual(outbound["to_phone"], harness.customer_phone)
        self.assertEqual(harness.twilio.sent_messages[0]["from_"], harness.twilio_number)
        self.assertNotIn("status_callback", harness.twilio.sent_messages[0])
        self.assertEqual(harness.lead_status_updates[-1]["status"], "needs_location")
        final_update = harness.conversation_updates[-1]
        self.assertEqual(final_update["current_state"], "awaiting_location")
        self.assertEqual(final_update["status"], "waiting_for_customer")
        self.assertEqual(final_update["collected_info"]["job_type"], "drain_or_sewer")

    def test_customer_sms_includes_status_callback_when_provided(self) -> None:
        harness = WorkflowHarness()

        harness.process(
            "Yes, clogged drain",
            status_callback_url="https://vigil.example/webhooks/twilio/status",
        )

        self.assertEqual(
            harness.twilio.sent_messages[0]["status_callback"],
            "https://vigil.example/webhooks/twilio/status",
        )

    def test_opt_out_reply_confirms_and_closes(self) -> None:
        harness = WorkflowHarness()

        result = harness.process("STOP")

        self.assertTrue(result.processed)
        self.assertEqual(result.matched_node_key, "opt_out")
        self.assertEqual(
            harness.outbound_messages()[0]["template_key"], "opt_out_confirm"
        )
        self.assertEqual(harness.opt_out_creates[-1]["reason"], "stop")
        self.assertEqual(harness.lead_status_updates[-1]["status"], "opted_out")
        self.assertEqual(
            harness.close_conversation_calls[-1]["conversation_id"], "conversation_1"
        )
        self.assertEqual(harness.conversation_updates[-1]["status"], "closed")

    def test_wrong_number_reply_opts_out_and_closes(self) -> None:
        harness = WorkflowHarness()

        result = harness.process("Sorry, you have the wrong number.")

        self.assertTrue(result.processed)
        self.assertEqual(result.matched_node_key, "wrong_number")
        self.assertEqual(
            harness.outbound_messages()[0]["template_key"], "wrong_number_confirm"
        )
        self.assertEqual(harness.opt_out_creates[-1]["reason"], "wrong_number")
        self.assertEqual(harness.lead_status_updates[-1]["status"], "wrong_number")
        self.assertEqual(harness.conversation_updates[-1]["current_state"], "closed")

    def test_unclear_reply_sends_clarification(self) -> None:
        harness = WorkflowHarness()

        result = harness.process("Blue banana Tuesday.")

        self.assertTrue(result.processed)
        self.assertEqual(result.matched_node_key, "fallback_unclear")
        self.assertEqual(harness.outbound_messages()[0]["template_key"], "clarification")
        self.assertEqual(
            harness.lead_status_updates[-1]["status"], "needs_clarification"
        )
        final_update = harness.conversation_updates[-1]
        self.assertEqual(final_update["current_state"], "awaiting_initial_reply")
        self.assertEqual(final_update["status"], "waiting_for_customer")
        self.assertEqual(len(harness.close_conversation_calls), 0)

    def test_handoff_reply_sends_template_notifies_owner_and_closes(self) -> None:
        harness = WorkflowHarness()

        result = harness.process("Basement drain backing up at 123 King St today.")

        self.assertTrue(result.processed)
        self.assertEqual(result.matched_node_key, "lead_complete_handoff")
        self.assertEqual(harness.outbound_messages()[0]["template_key"], "handoff_to_team")
        self.assertEqual(harness.lead_status_updates[-1]["status"], "needs_owner_call")
        self.assertEqual(len(harness.owner_notifications), 1)
        self.assertEqual(harness.owner_notifications[0]["priority"], "normal")
        self.assertEqual(harness.owner_notifications[0]["status"], "sent")
        self.assertEqual(
            harness.owner_notifications[0]["recipient"], harness.client["owner_phone"]
        )
        self.assertEqual(
            harness.owner_notifications[0]["raw_response"],
            {"twilio_message_sid": "SM_OUT_2"},
        )
        self.assertIn("Acme Plumbing", harness.owner_notifications[0]["body"])
        self.assertIn("Photo: Not received", harness.owner_notifications[0]["body"])
        self.assertEqual(harness.twilio.sent_messages[1]["from_"], harness.twilio_number)
        self.assertEqual(
            harness.twilio.sent_messages[1]["to"], harness.client["owner_phone"]
        )
        self.assertEqual(harness.conversation_updates[-1]["status"], "closed")

    def test_notify_owner_records_failed_send(self) -> None:
        harness = WorkflowHarness(fail_owner_send=True)

        result = harness.process("Basement drain backing up at 123 King St today.")

        self.assertTrue(result.processed)
        self.assertEqual(len(harness.owner_notifications), 1)
        notification = harness.owner_notifications[0]
        self.assertEqual(notification["status"], "failed")
        self.assertIn("Twilio owner send failed", notification["raw_response"]["error"])
        self.assertEqual(len(harness.twilio.sent_messages), 1)
        self.assertEqual(harness.twilio.sent_messages[0]["to"], harness.customer_phone)

    def test_notify_owner_does_not_send_to_customer_phone(self) -> None:
        harness = WorkflowHarness()
        harness.client["owner_phone"] = harness.customer_phone

        result = harness.process("Basement drain backing up at 123 King St today.")

        self.assertTrue(result.processed)
        self.assertEqual(len(harness.owner_notifications), 1)
        notification = harness.owner_notifications[0]
        self.assertEqual(notification["status"], "failed")
        self.assertEqual(
            notification["raw_response"], {"error": "owner_recipient_matches_customer"}
        )
        self.assertEqual(len(harness.twilio.sent_messages), 1)
        self.assertEqual(harness.twilio.sent_messages[0]["to"], harness.customer_phone)

    def test_mms_metadata_is_persisted_and_photo_context_reaches_owner(self) -> None:
        harness = WorkflowHarness()
        raw_payload = {
            "From": harness.customer_phone,
            "To": harness.twilio_number,
            "Body": "Basement drain backing up at 123 King St today.",
            "MessageSid": "SM_IN_1",
            "NumMedia": "2",
            "MediaUrl0": "https://api.twilio.com/media/ME0",
            "MediaContentType0": "image/jpeg",
            "MediaUrl1": "https://api.twilio.com/media/ME1",
            "MediaContentType1": "image/png",
        }

        result = harness.process(raw_payload["Body"], raw_payload=raw_payload)

        self.assertTrue(result.processed)
        inbound = harness.inbound_messages()[0]
        self.assertEqual(
            harness.message_media,
            [
                {
                    "id": "media_1",
                    "message_id": inbound["id"],
                    "client_id": "client_1",
                    "lead_id": "lead_1",
                    "twilio_media_url": "https://api.twilio.com/media/ME0",
                    "content_type": "image/jpeg",
                },
                {
                    "id": "media_2",
                    "message_id": inbound["id"],
                    "client_id": "client_1",
                    "lead_id": "lead_1",
                    "twilio_media_url": "https://api.twilio.com/media/ME1",
                    "content_type": "image/png",
                },
            ],
        )
        final_info = harness.conversation_updates[-1]["collected_info"]
        self.assertTrue(final_info["photo_received"])
        self.assertIn("Photo: Received", harness.owner_notifications[0]["body"])

    def test_existing_opt_out_suppresses_customer_sms(self) -> None:
        harness = WorkflowHarness(existing_opt_out=True)

        result = harness.process("Yes, clogged drain")

        self.assertTrue(result.processed)
        self.assertEqual(result.matched_node_key, "collect_location")
        self.assertEqual(len(harness.twilio.sent_messages), 0)
        outbound = harness.outbound_messages()[0]
        self.assertEqual(outbound["template_key"], "request_location")
        self.assertEqual(outbound["status"], "suppressed_opt_out")


if __name__ == "__main__":
    unittest.main()
