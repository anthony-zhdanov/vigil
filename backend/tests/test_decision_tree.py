from __future__ import annotations

import unittest

from app.decision_tree.classifier import classify_plumbing_sms
from app.decision_tree.contract import DecisionResult
from app.decision_tree.interpreter import run_plumbing_decision_tree
from app.decision_tree.templates.base import render_template


def action_types(result: DecisionResult) -> list[str]:
    return [action.type for action in result.actions]


def template_keys(result: DecisionResult) -> list[str]:
    return [
        action.template_key
        for action in result.actions
        if action.type == "send_sms_template" and action.template_key
    ]


class ClassifyPlumbingSmsTests(unittest.TestCase):
    def test_opt_out(self) -> None:
        result = classify_plumbing_sms("STOP")

        self.assertEqual(result.intent, "opt_out")
        self.assertEqual(result.reason, "opt_out_keyword")
        self.assertFalse(result.lead_signal)

    def test_wrong_number(self) -> None:
        result = classify_plumbing_sms("Sorry, wrong number.")

        self.assertEqual(result.intent, "wrong_number")
        self.assertEqual(result.reason, "wrong_number_phrase")

    def test_no_longer_needed(self) -> None:
        result = classify_plumbing_sms("Already fixed, no longer need help.")

        self.assertEqual(result.intent, "no_longer_needed")
        self.assertEqual(result.reason, "no_longer_needed_phrase")

    def test_location_extraction(self) -> None:
        result = classify_plumbing_sms("The issue is at 123 King St in Toronto.")

        self.assertEqual(result.intent, "lead")
        self.assertEqual(result.location, "123 King St")
        self.assertTrue(result.lead_signal)

    def test_job_type_extraction(self) -> None:
        result = classify_plumbing_sms("My water heater is leaking.")

        self.assertEqual(result.intent, "lead")
        self.assertEqual(result.job_type, "water_heater")

    def test_urgency_extraction(self) -> None:
        result = classify_plumbing_sms("Not urgent, next week is fine.")

        self.assertEqual(result.intent, "lead")
        self.assertEqual(result.urgency, "scheduled")

    def test_unclear_message(self) -> None:
        result = classify_plumbing_sms("Blue banana Tuesday.")

        self.assertEqual(result.intent, "unclear")
        self.assertEqual(result.reason, "no_route_matched")
        self.assertFalse(result.lead_signal)


class RunPlumbingDecisionTreeTests(unittest.TestCase):
    def test_opt_out(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="stop texting me",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "opt_out")
        self.assertEqual(result.conversation_state, "closed")
        self.assertEqual(result.conversation_status, "closed")
        self.assertEqual(
            action_types(result),
            [
                "send_sms_template",
                "create_opt_out",
                "mark_lead_status",
                "close_conversation",
            ],
        )
        self.assertEqual(template_keys(result), ["opt_out_confirm"])

    def test_wrong_number(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="You have the wrong number.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "wrong_number")
        self.assertEqual(result.conversation_state, "closed")
        self.assertEqual(result.conversation_status, "closed")
        self.assertEqual(template_keys(result), ["wrong_number_confirm"])
        self.assertIn("create_opt_out", action_types(result))

    def test_no_longer_needed(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="No thanks, already handled.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "no_longer_needed")
        self.assertEqual(result.conversation_state, "closed")
        self.assertEqual(result.conversation_status, "closed")
        self.assertEqual(template_keys(result), ["no_longer_needed"])

    def test_unclear_initial_reply(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Blue banana Tuesday.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "fallback_unclear")
        self.assertEqual(result.conversation_state, "awaiting_initial_reply")
        self.assertEqual(result.conversation_status, "waiting_for_customer")
        self.assertEqual(template_keys(result), ["clarification"])

    def test_collect_location(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Yes, I still need help with a clogged drain.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "collect_location")
        self.assertEqual(result.conversation_state, "awaiting_location")
        self.assertEqual(result.collected_info["job_type"], "drain_or_sewer")
        self.assertEqual(template_keys(result), ["request_location"])

    def test_collect_job_type(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="It is at 123 King St.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "collect_job_type")
        self.assertEqual(result.conversation_state, "awaiting_job_type")
        self.assertEqual(result.collected_info["location"], "123 King St")
        self.assertEqual(template_keys(result), ["request_job_type"])

    def test_collect_urgency(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Basement drain backing up at 123 King St.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "collect_urgency")
        self.assertEqual(result.conversation_state, "awaiting_urgency")
        self.assertEqual(result.collected_info["location"], "123 King St")
        self.assertEqual(result.collected_info["job_type"], "drain_or_sewer")
        self.assertEqual(template_keys(result), ["request_urgency"])

    def test_emergency(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Emergency, the basement is flooding.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "emergency_collect_location")
        self.assertEqual(result.conversation_state, "awaiting_location")
        self.assertEqual(result.collected_info["urgency"], "emergency")
        self.assertEqual(template_keys(result), ["emergency_ack"])
        self.assertEqual(result.actions[0].type, "notify_owner")
        self.assertEqual(result.actions[0].notification_priority, "urgent")

    def test_complete_handoff(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Basement drain backing up at 123 King St today.",
            current_state="awaiting_initial_reply",
        )

        self.assertEqual(result.matched_node_key, "lead_complete_handoff")
        self.assertEqual(result.conversation_state, "closed")
        self.assertEqual(result.conversation_status, "closed")
        self.assertEqual(result.collected_info["location"], "123 King St")
        self.assertEqual(result.collected_info["job_type"], "drain_or_sewer")
        self.assertEqual(result.collected_info["urgency"], "today")
        self.assertEqual(template_keys(result), ["handoff_to_team"])
        self.assertIn("notify_owner", action_types(result))

    def test_previously_notified_emergency_does_not_notify_owner_again(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Thanks",
            current_state="awaiting_urgency",
            collected_info={
                "location": "123 King St",
                "job_type": "drain_or_sewer",
                "urgency": "emergency",
                "emergency_notified": True,
            },
        )

        self.assertEqual(result.matched_node_key, "lead_complete_handoff")
        self.assertEqual(result.collected_info["emergency_notified"], True)
        self.assertEqual(result.collected_info["urgency"], "emergency")
        self.assertNotIn("notify_owner", action_types(result))

    def test_emergency_urgency_is_not_downgraded_by_follow_up_reply(self) -> None:
        result = run_plumbing_decision_tree(
            message_body="Today is fine.",
            current_state="awaiting_urgency",
            collected_info={
                "location": "123 King St",
                "job_type": "drain_or_sewer",
                "urgency": "emergency",
                "emergency_notified": True,
            },
        )

        self.assertEqual(result.collected_info["urgency"], "emergency")
        self.assertNotIn("notify_owner", action_types(result))


class TemplateRenderingTests(unittest.TestCase):
    def test_every_template_key_emitted_by_interpreter_renders(self) -> None:
        results = [
            run_plumbing_decision_tree(
                message_body="stop texting me",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="wrong number",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="already fixed",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="Blue banana Tuesday.",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="Yes, clogged drain",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="123 King St",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="Basement drain backing up at 123 King St.",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="Emergency flooding",
                current_state="awaiting_initial_reply",
            ),
            run_plumbing_decision_tree(
                message_body="Basement drain backing up at 123 King St today.",
                current_state="awaiting_initial_reply",
            ),
        ]
        emitted_keys = sorted({key for result in results for key in template_keys(result)})

        self.assertEqual(
            emitted_keys,
            [
                "clarification",
                "emergency_ack",
                "handoff_to_team",
                "no_longer_needed",
                "opt_out_confirm",
                "request_job_type",
                "request_location",
                "request_urgency",
                "wrong_number_confirm",
            ],
        )
        for key in emitted_keys:
            with self.subTest(template_key=key):
                rendered = render_template(
                    key,
                    client={"business_name": "Acme Plumbing"},
                    lead_phone="+14165550123",
                    collected_info={
                        "location": "123 King St",
                        "job_type": "drain_or_sewer",
                        "urgency": "today",
                    },
                    latest_message="Basement drain backing up.",
                    summary="Location and job type collected.",
                )

                self.assertTrue(rendered.strip())
                self.assertNotIn("{", rendered)
                self.assertNotIn("}", rendered)

    def test_owner_notification_renders_with_missing_optional_fields(self) -> None:
        rendered = render_template(
            "owner_notification",
            client={},
        )

        self.assertIn("Vigil lead for the team", rendered)
        self.assertIn("Lead: Unknown", rendered)
        self.assertIn("Priority: NORMAL", rendered)
        self.assertIn("Location: Unknown", rendered)
        self.assertIn("Service: Unknown", rendered)
        self.assertIn("Urgency: Unknown", rendered)
        self.assertIn("Photo: Not received", rendered)
        self.assertIn("Summary: No summary yet.", rendered)
        self.assertNotIn("{", rendered)
        self.assertNotIn("}", rendered)


if __name__ == "__main__":
    unittest.main()
