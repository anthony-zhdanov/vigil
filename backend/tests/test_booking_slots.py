from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.booking.domain import BusyInterval, ServiceSchedule, Slot
from app.booking.slots import generate_slots, parse_slot_reply, slot_is_still_available


TORONTO = ZoneInfo("America/Toronto")


def schedule(**overrides: object) -> ServiceSchedule:
    values: dict[str, object] = {
        "service_id": "service-1",
        "service_key": "drain_or_sewer",
        "display_name": "Drain service",
        "timezone": "America/Toronto",
        "duration_minutes": 60,
        "lead_time_minutes": 60,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "horizon_days": 7,
        "slot_interval_minutes": 30,
        "weekly_hours": {
            "monday": [{"start": "09:00", "end": "13:00"}],
            "tuesday": [{"start": "09:00", "end": "13:00"}],
            "wednesday": [{"start": "09:00", "end": "13:00"}],
            "thursday": [{"start": "09:00", "end": "13:00"}],
            "friday": [{"start": "09:00", "end": "13:00"}],
        },
    }
    values.update(overrides)
    return ServiceSchedule(**values)  # type: ignore[arg-type]


class BookingSlotTests(unittest.TestCase):
    def test_lead_time_and_busy_intervals_remove_slots(self) -> None:
        now = datetime(2026, 7, 13, 9, 15, tzinfo=TORONTO)
        busy = [
            BusyInterval(
                datetime(2026, 7, 13, 11, 0, tzinfo=TORONTO),
                datetime(2026, 7, 13, 12, 0, tzinfo=TORONTO),
            )
        ]

        slots = generate_slots(schedule(), busy, now=now)

        self.assertEqual(
            [slot.starts_at.hour * 60 + slot.starts_at.minute for slot in slots],
            [12 * 60, 9 * 60, 9 * 60 + 30],
        )
        self.assertEqual(slots[1].starts_at.date().isoformat(), "2026-07-14")

    def test_buffers_are_enforced_around_busy_time(self) -> None:
        busy = [
            BusyInterval(
                datetime(2026, 7, 13, 11, 0, tzinfo=TORONTO),
                datetime(2026, 7, 13, 12, 0, tzinfo=TORONTO),
            )
        ]
        configured = schedule(
            lead_time_minutes=0,
            buffer_before_minutes=30,
            buffer_after_minutes=30,
        )

        slots = generate_slots(
            configured,
            busy,
            now=datetime(2026, 7, 13, 8, 0, tzinfo=TORONTO),
        )

        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in slots],
            ["09:00", "09:30", "09:00"],
        )
        self.assertEqual(slots[2].starts_at.date().isoformat(), "2026-07-14")

    def test_pagination_returns_next_three_slots(self) -> None:
        now = datetime(2026, 7, 13, 7, 0, tzinfo=TORONTO)

        first = generate_slots(schedule(lead_time_minutes=0), [], now=now)
        second = generate_slots(
            schedule(lead_time_minutes=0), [], now=now, page_index=1
        )

        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in first],
            ["09:00", "09:30", "10:00"],
        )
        self.assertEqual(
            [slot.starts_at.strftime("%H:%M") for slot in second],
            ["10:30", "11:00", "11:30"],
        )

    def test_horizon_stops_search(self) -> None:
        sunday = datetime(2026, 7, 12, 8, 0, tzinfo=TORONTO)

        slots = generate_slots(schedule(horizon_days=0), [], now=sunday)

        self.assertEqual(slots, [])

    def test_dst_offset_changes_across_spring_transition(self) -> None:
        configured = schedule(
            weekly_hours={
                "sunday": [{"start": "09:00", "end": "11:00"}],
                "monday": [{"start": "09:00", "end": "11:00"}],
            },
            lead_time_minutes=0,
        )
        now = datetime(2026, 3, 7, 12, 0, tzinfo=TORONTO)

        slots = generate_slots(configured, [], now=now)

        self.assertEqual(slots[0].starts_at.utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(slots[0].starts_at.hour, 9)

    def test_nonexistent_local_period_is_skipped(self) -> None:
        configured = schedule(
            weekly_hours={
                "sunday": [{"start": "02:15", "end": "03:45"}],
                "monday": [{"start": "09:00", "end": "11:00"}],
            },
            lead_time_minutes=0,
        )
        now = datetime(2026, 3, 7, 12, 0, tzinfo=TORONTO)

        slots = generate_slots(configured, [], now=now)

        self.assertEqual(slots[0].starts_at.weekday(), 0)

    def test_selected_slot_is_revalidated_with_buffers(self) -> None:
        selected = Slot(
            datetime(2026, 7, 13, 10, 0, tzinfo=TORONTO),
            datetime(2026, 7, 13, 11, 0, tzinfo=TORONTO),
        )
        busy = [
            BusyInterval(
                datetime(2026, 7, 13, 11, 15, tzinfo=TORONTO),
                datetime(2026, 7, 13, 12, 0, tzinfo=TORONTO),
            )
        ]

        self.assertFalse(
            slot_is_still_available(
                selected,
                busy,
                schedule=schedule(buffer_after_minutes=30),
            )
        )

    def test_slot_reply_parser_is_strict(self) -> None:
        self.assertEqual(parse_slot_reply(" 2 "), 1)
        self.assertEqual(parse_slot_reply("MORE"), "more")
        self.assertIsNone(parse_slot_reply("tomorrow"))

    def test_naive_now_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            generate_slots(schedule(), [], now=datetime(2026, 7, 13, 9, 0))
