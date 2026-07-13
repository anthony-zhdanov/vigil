from __future__ import annotations

import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260713103519_add_booking_foundation.sql"
)


class BookingSchemaTests(unittest.TestCase):
    def test_booking_tables_are_rls_protected_and_not_publicly_granted(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        tables = (
            "booking_connections",
            "booking_configs",
            "booking_services",
            "booking_slot_offers",
            "bookings",
            "booking_setup_tokens",
            "booking_oauth_states",
        )

        for table in tables:
            with self.subTest(table=table):
                self.assertIn(f"create table if not exists public.{table}", sql)
                self.assertIn(
                    f"alter table public.{table} enable row level security", sql
                )
                self.assertIn(
                    f"revoke all on table public.{table} from anon, authenticated",
                    sql,
                )
                self.assertIn(
                    "grant select, insert, update, delete on table "
                    f"public.{table} to service_role",
                    sql,
                )

    def test_booking_idempotency_constraints_are_present(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")

        self.assertIn("idempotency_key text not null unique", sql)
        self.assertIn("booking_slot_offers_one_pending_per_conversation_key", sql)
        self.assertIn("bookings_one_confirmed_per_conversation_key", sql)
