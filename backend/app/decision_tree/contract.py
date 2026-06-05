from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Intent = Literal["lead", "opt_out", "wrong_number", "no_longer_needed", "unclear"]
ActionType = Literal[
    "send_sms_template",
    "mark_lead_status",
    "create_opt_out",
    "notify_owner",
    "close_conversation",
]


@dataclass(frozen=True, slots=True)
class ClassifierOutput:
    intent: Intent
    confidence: float
    summary: str
    urgency: str | None = None
    job_type: str | None = None
    location: str | None = None
    lead_signal: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "summary": self.summary,
            "urgency": self.urgency,
            "job_type": self.job_type,
            "location": self.location,
            "lead_signal": self.lead_signal,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DecisionAction:
    type: ActionType
    template_key: str | None = None
    lead_status: str | None = None
    opt_out_reason: str | None = None
    notification_priority: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "template_key": self.template_key,
            "lead_status": self.lead_status,
            "opt_out_reason": self.opt_out_reason,
            "notification_priority": self.notification_priority,
        }


@dataclass(frozen=True, slots=True)
class DecisionResult:
    tree_key: str
    tree_version: str
    classifier_output: ClassifierOutput
    matched_node_key: str
    actions: list[DecisionAction]
    conversation_state: str
    conversation_status: str
    collected_info: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def actions_json(self) -> list[dict[str, Any]]:
        return [action.to_dict() for action in self.actions]
