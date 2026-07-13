from __future__ import annotations

from typing import Any

from supabase import Client

from ._shared import Row, first_row, require_supabase


def insert_decision_tree_run(
    supabase: Client | None,
    *,
    client_id: str,
    lead_id: str,
    conversation_id: str | None,
    inbound_message_id: str | None,
    decision_tree_key: str,
    decision_tree_version: str,
    classifier_output: dict[str, Any],
    matched_node_key: str,
    actions_json: list[dict[str, Any]],
    result_status: str,
) -> Row:
    db = require_supabase(supabase)
    response = (
        db.table("decision_tree_runs")
        .insert(
            {
                "client_id": client_id,
                "lead_id": lead_id,
                "conversation_id": conversation_id,
                "inbound_message_id": inbound_message_id,
                "decision_tree_key": decision_tree_key,
                "decision_tree_version": decision_tree_version,
                "classifier_output": classifier_output,
                "matched_node_key": matched_node_key,
                "actions_json": actions_json,
                "result_status": result_status,
            }
        )
        .execute()
    )
    return first_row(response, "decision tree run")

