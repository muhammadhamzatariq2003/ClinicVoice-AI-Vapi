"""Turn the call log into dashboard metrics."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from app.call_log import log_path


def _event_date(timestamp: str, tz: tzinfo | None = None) -> date | None:
    """Parse an ISO timestamp to a calendar date, shown in ``tz`` when the timestamp is timezone-aware."""
    try:
        moment = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return None
    if tz is not None and moment.tzinfo is not None:
        moment = moment.astimezone(tz)
    return moment.date()


def load_events(
    path: Path | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tz: tzinfo | None = None,
) -> list[dict]:
    """Read events from the JSONL log, skipping malformed lines; optionally filter by date range."""
    source = path or log_path()
    if not source.exists():
        return []

    events: list[dict] = []
    with open(source, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if date_from or date_to:
                when = _event_date(entry.get("timestamp", ""), tz)
                if when is None:
                    continue
                if date_from and when < date_from:
                    continue
                if date_to and when > date_to:
                    continue
            events.append(entry)
    return events


def compute_metrics(events: list[dict], today: date | None = None, tz: tzinfo | None = None) -> dict:
    """One pass over the events.

    ``booking_rate`` is bookings divided by completed calls (percent). Only bookings that
    actually succeeded count; old log lines without a ``status`` are treated as booked.
    """
    today = today or date.today()
    calls_total = calls_today = tool_calls = bookings = 0
    durations: list[float] = []
    per_day: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    ended: Counter[str] = Counter()

    for e in events:
        details = e.get("details") or {}
        kind = e.get("event_type")

        if kind == "end_of_call":
            calls_total += 1
            when = _event_date(e.get("timestamp", ""), tz)
            if when:
                per_day[when.isoformat()] += 1
                if when == today:
                    calls_today += 1
            try:
                if details.get("duration") is not None:
                    durations.append(float(details["duration"]))
            except (TypeError, ValueError):
                pass
            if details.get("ended_reason"):
                ended[str(details["ended_reason"])] += 1

        elif kind == "tool_call":
            tool_calls += 1
            if e.get("action") == "book_appointment" and details.get("status", "booked") == "booked":
                bookings += 1
                if details.get("reason"):
                    reasons[details["reason"]] += 1

    return {
        "calls_total": calls_total,
        "calls_today": calls_today,
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else 0.0,
        "bookings_total": bookings,
        "booking_rate": round(min(bookings / calls_total, 1.0) * 100, 1) if calls_total else 0.0,
        "tool_calls_total": tool_calls,
        "top_reasons": reasons.most_common(10),
        "ended_reasons": ended.most_common(5),
        "calls_per_day": dict(sorted(per_day.items())),
    }


def get_analytics(
    date_from: date | None = None,
    date_to: date | None = None,
    log_file: Path | None = None,
    timezone_name: str = "Europe/Berlin",
) -> dict:
    tz = ZoneInfo(timezone_name)
    events = load_events(log_file, date_from, date_to, tz)
    return compute_metrics(events, today=datetime.now(tz).date(), tz=tz)
