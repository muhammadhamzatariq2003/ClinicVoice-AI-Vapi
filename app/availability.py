"""Appointment slots, bookings and opening-hours checks."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from app.clinic import WEEKDAYS_DE, Schedule
from app.holidays import is_holiday

Clock = Callable[[], datetime]
MAX_LOOKAHEAD_DAYS = 60


class BookingStoreError(RuntimeError):
    """The bookings file exists but cannot be read."""


# --------------------------------------------------------------------------- helpers

def parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def parse_time(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def format_date_de(day: date) -> str:
    """'Dienstag, 14.04.2026' (locale-independent, safe for speech)."""
    return f"{WEEKDAYS_DE[day.weekday()]}, {day.strftime('%d.%m.%Y')}"


def closed_reason(schedule: Schedule, day: date) -> str | None:
    """Why the clinic is closed on ``day`` (holiday / closure name / 'Ruhetag'), or None if open."""
    holiday, name = is_holiday(day, schedule.holiday_region, schedule.closed_days)
    if holiday:
        return name
    if not schedule.hours.get(day.weekday()):
        return "Ruhetag"
    return None


def is_outside_hours(schedule: Schedule, moment: datetime) -> bool:
    """True if ``moment`` falls outside every opening range of that weekday (end is exclusive)."""
    t = moment.time().replace(tzinfo=None)
    return not any(start <= t < end for start, end in schedule.hours.get(moment.weekday(), ()))


def all_slots(schedule: Schedule, day: date) -> list[str]:
    """Every bookable slot start ('HH:MM') on ``day``, ignoring existing bookings."""
    if closed_reason(schedule, day):
        return []
    step = timedelta(minutes=schedule.slot_minutes)
    base = datetime.combine(day, time())
    slots: list[str] = []
    for start, end in schedule.hours.get(day.weekday(), ()):
        cur, stop = datetime.combine(day, start), datetime.combine(day, end)
        while cur + step <= stop:
            slots.append(cur.strftime("%H:%M"))
            cur += step
    return slots


# --------------------------------------------------------------------------- storage

class BookingStore:
    """Tiny JSON file store: ``{"YYYY-MM-DD": {"HH:MM": "Patient name"}}``.

    Good for a demo or a single-worker deployment. Swap this class for a database
    when more than one process needs to write bookings.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            text = self.path.read_text(encoding="utf-8").strip()
            return json.loads(text) if text else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise BookingStoreError(f"Cannot read bookings file {self.path}: {exc}") from exc

    def save(self, bookings: dict) -> None:
        """Write atomically so a crash mid-write can never corrupt the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".bookings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(bookings, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


# --------------------------------------------------------------------------- service

class Availability:
    """Slot logic for one clinic. Pass ``clock`` in tests to freeze 'now'."""

    def __init__(self, schedule: Schedule, store: BookingStore, clock: Clock | None = None):
        self.schedule = schedule
        self.store = store
        self._clock = clock or (lambda: datetime.now(schedule.tz))

    def now(self) -> datetime:
        return self._clock()

    def closed_reason(self, day: date) -> str | None:
        return closed_reason(self.schedule, day)

    def is_outside_hours(self, moment: datetime | None = None) -> bool:
        return is_outside_hours(self.schedule, moment or self.now())

    def is_past(self, day: date, at: time) -> bool:
        now = self.now()
        return datetime.combine(day, at) <= now.replace(tzinfo=None)

    def _free(self, day: date, bookings: dict) -> list[str]:
        taken = set(bookings.get(day.isoformat(), {}))
        now = self.now().replace(tzinfo=None)
        free = []
        for slot in all_slots(self.schedule, day):
            if slot in taken:
                continue
            if datetime.combine(day, parse_time(slot)) <= now:
                continue  # already in the past
            free.append(slot)
        return free

    def slots(self, date_str: str) -> list[str]:
        """Free, future slots on a date ('YYYY-MM-DD')."""
        return self._free(parse_date(date_str), self.store.load())

    def is_available(self, date_str: str, time_str: str) -> bool:
        return time_str in self.slots(date_str)

    def book(self, date_str: str, time_str: str, patient_name: str) -> bool:
        """Book a slot. Check and write happen under one lock, so two callers can never get the same slot."""
        day = parse_date(date_str)
        time_str = parse_time(time_str).strftime("%H:%M")
        with self.store.lock:
            bookings = self.store.load()
            if time_str not in self._free(day, bookings):
                return False
            bookings.setdefault(day.isoformat(), {})[time_str] = patient_name
            self.store.save(bookings)
            return True

    def next_available(self, date_str: str, time_str: str = "00:00", count: int = 3) -> list[dict]:
        """Next ``count`` free slots at or after the given date/time."""
        day = parse_date(date_str)
        start_time = parse_time(time_str).strftime("%H:%M")
        bookings = self.store.load()
        found: list[dict] = []
        for offset in range(MAX_LOOKAHEAD_DAYS):
            current = day + timedelta(days=offset)
            for slot in self._free(current, bookings):
                if offset == 0 and slot < start_time:
                    continue
                found.append({"date": current.isoformat(), "time": slot})
                if len(found) >= count:
                    return found
        return found
