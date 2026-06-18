from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from app.repositories import conversations


class FakeQuery:
    def __init__(self, table: "FakeTable", operation: str, payload: dict[str, Any] | None = None) -> None:
        self.table = table
        self.operation = operation
        self.payload = payload or {}
        self.filters: dict[str, Any] = {}

    def select(self, columns: str) -> "FakeQuery":
        return self

    def insert(self, payload: dict[str, Any]) -> "FakeQuery":
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload: dict[str, Any]) -> "FakeQuery":
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key: str, value: Any) -> "FakeQuery":
        self.filters[key] = value
        return self

    def neq(self, key: str, value: Any) -> "FakeQuery":
        self.filters[f"{key}__neq"] = value
        return self

    def is_(self, key: str, value: Any) -> "FakeQuery":
        self.filters[f"{key}__is"] = value
        return self

    def order(self, *args: Any, **kwargs: Any) -> "FakeQuery":
        return self

    def limit(self, count: int) -> "FakeQuery":
        return self

    def execute(self) -> SimpleNamespace:
        if self.operation == "select":
            return SimpleNamespace(data=self.table.select_rows(self.filters))
        if self.operation == "update":
            return SimpleNamespace(data=self.table.update_rows(self.filters, self.payload))
        if self.operation == "insert":
            return SimpleNamespace(data=self.table.insert_row(self.payload))
        raise AssertionError(f"unsupported operation {self.operation}")


class FakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.inserts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    def select(self, columns: str) -> FakeQuery:
        return FakeQuery(self, "select")

    def insert(self, payload: dict[str, Any]) -> FakeQuery:
        return FakeQuery(self, "insert", payload)

    def update(self, payload: dict[str, Any]) -> FakeQuery:
        return FakeQuery(self, "update", payload)

    def _matches(self, row: dict[str, Any], filters: dict[str, Any]) -> bool:
        for key, value in filters.items():
            if key.endswith("__neq"):
                if row.get(key.removesuffix("__neq")) == value:
                    return False
            elif key.endswith("__is"):
                row_value = row.get(key.removesuffix("__is"))
                if value == "null" and row_value is not None:
                    return False
            elif row.get(key) != value:
                return False
        return True

    def select_rows(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows if self._matches(row, filters)]

    def update_rows(
        self, filters: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        self.updates.append({"filters": dict(filters), "payload": dict(payload)})
        updated: list[dict[str, Any]] = []
        for row in self.rows:
            if self._matches(row, filters):
                row.update(payload)
                updated.append(dict(row))
        return updated

    def insert_row(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.inserts.append(dict(payload))
        row = {"id": f"conversation_{len(self.rows) + 1}", **payload}
        self.rows.append(row)
        return [dict(row)]


class FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.conversations = FakeTable(rows)

    def table(self, name: str) -> FakeTable:
        if name != "conversations":
            raise AssertionError(f"unexpected table {name}")
        return self.conversations


class ConversationRepositoryTests(unittest.TestCase):
    def test_get_or_create_reopens_existing_closed_conversation(self) -> None:
        db = FakeSupabase(
            [
                {
                    "id": "conversation_1",
                    "client_id": "client_1",
                    "lead_id": "lead_1",
                    "channel": "sms",
                    "status": "closed",
                    "current_state": "closed",
                    "closed_at": "2026-06-17T00:00:00+00:00",
                    "last_message_at": "2026-06-17T00:00:00+00:00",
                    "collected_info": {"location": "old"},
                    "summary": "old summary",
                }
            ]
        )

        row = conversations.get_or_create_active_conversation(
            db, client_id="client_1", lead_id="lead_1", channel="sms"
        )

        self.assertEqual(row["id"], "conversation_1")
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["current_state"], "awaiting_initial_reply")
        self.assertIsNone(row["closed_at"])
        self.assertEqual(row["collected_info"], {})
        self.assertEqual(len(db.conversations.inserts), 0)
        self.assertEqual(len(db.conversations.updates), 1)


if __name__ == "__main__":
    unittest.main()
