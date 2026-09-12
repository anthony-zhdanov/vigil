from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.booking.domain import BusyInterval, ServiceSchedule, Slot


WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid scheduling time: {value}") from exc
    if parsed.tzinfo is not None:
        raise ValueError("Weekly scheduling times must not include a timezone")
    return parsed


def _valid_local_datetime(day: date, value: str, tz: ZoneInfo) -> datetime | None:
    candidate = datetime.combine(day, _parse_time(value), tzinfo=tz)
    round_trip = candidate.astimezone(timezone.utc).astimezone(tz)
    if round_trip.replace(fold=candidate.fold) != candidate:
        return None
    return candidate


def _overlaps(
    candidate_start: datetime,
    candidate_end: datetime,
    busy_start: datetime,
    busy_end: datetime,
) -> bool:
    return candidate_start < busy_end and candidate_end > busy_start


def _slot_is_free(
    slot: Slot,
    busy_intervals: list[BusyInterval],
    *,
    buffer_before_minutes: int,
    buffer_after_minutes: int,
) -> bool:
    occupied_start = slot.starts_at - timedelta(minutes=buffer_before_minutes)
    occupied_end = slot.ends_at + timedelta(minutes=buffer_after_minutes)
    return not any(
        _overlaps(
            occupied_start,
            occupied_end,
            busy.starts_at,
            busy.ends_at,
        )
        for busy in busy_intervals
    )


def generate_slots(
    schedule: ServiceSchedule,
    busy_intervals: list[BusyInterval],
    *,
    now: datetime,
    page_index: int = 0,
    page_size: int = 3,
) -> list[Slot]:
    if now.tzinfo is None:
        raise ValueError("Slot generation requires a timezone-aware current time")
    if page_index < 0 or page_size <= 0:
        raise ValueError("Slot page values are invalid")

    try:
        tz = ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown scheduling timezone: {schedule.timezone}") from exc

    local_now = now.astimezone(tz)
    earliest = now + timedelta(minutes=schedule.lead_time_minutes)
    final_day = local_now.date() + timedelta(days=schedule.horizon_days)
    required_slots = (page_index + 1) * page_size
    available: list[Slot] = []
    day = local_now.date()

    while day <= final_day and len(available) < required_slots:
        periods = schedule.weekly_hours.get(WEEKDAYS[day.weekday()], [])
        for period in periods:
            opens_at = _valid_local_datetime(day, str(period.get("start", "")), tz)
            closes_at = _valid_local_datetime(day, str(period.get("end", "")), tz)
            if opens_at is None or closes_at is None:
                continue
            if closes_at <= opens_at:
                raise ValueError("Scheduling periods must end after they start")

            candidate = opens_at
            while candidate + timedelta(minutes=schedule.duration_minutes) <= closes_at:
                slot = Slot(
                    starts_at=candidate,
                    ends_at=candidate + timedelta(minutes=schedule.duration_minutes),
                )
                if slot.starts_at >= earliest and _slot_is_free(
                    slot,
                    busy_intervals,
                    buffer_before_minutes=schedule.buffer_before_minutes,
                    buffer_after_minutes=schedule.buffer_after_minutes,
                ):
                    available.append(slot)
                    if len(available) >= required_slots:
                        break
                candidate += timedelta(minutes=schedule.slot_interval_minutes)
        day += timedelta(days=1)

    start = page_index * page_size
    return available[start : start + page_size]


def parse_slot_reply(message: str) -> int | Literal["more"] | None:
    normalized = message.strip().lower()
    if normalized == "more":
        return "more"
    if normalized in {"1", "2", "3"}:
        return int(normalized) - 1
    return None


def slot_is_still_available(
    selected: Slot,
    busy_intervals: list[BusyInterval],
    *,
    schedule: ServiceSchedule,
) -> bool:
    return _slot_is_free(
        selected,
        busy_intervals,
        buffer_before_minutes=schedule.buffer_before_minutes,
        buffer_after_minutes=schedule.buffer_after_minutes,
    )
