import json
from datetime import date
from zoneinfo import ZoneInfo

from app.analytics import compute_metrics, get_analytics, load_events

TODAY = date(2026, 4, 14)


def end(ts, duration=60, ended="customer-ended-call"):
    return {"event_type": "end_of_call", "timestamp": ts, "details": {"duration": duration, "ended_reason": ended}}


def book(reason, status=None):
    details = {"reason": reason}
    if status:
        details["status"] = status
    return {"event_type": "tool_call", "action": "book_appointment", "timestamp": "2026-04-14T08:00:00+00:00", "details": details}


def write(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_empty_log_gives_zeroes():
    m = compute_metrics([], today=TODAY)
    assert m["calls_total"] == 0 and m["booking_rate"] == 0.0 and m["avg_duration_sec"] == 0.0
    assert m["top_reasons"] == [] and m["calls_per_day"] == {}


def test_counts_calls_today_and_per_day():
    events = [end("2026-04-14T09:00:00+00:00"), end("2026-04-14T10:00:00+00:00"), end("2026-04-13T10:00:00+00:00")]
    m = compute_metrics(events, today=TODAY)
    assert m["calls_total"] == 3 and m["calls_today"] == 2
    assert m["calls_per_day"] == {"2026-04-13": 1, "2026-04-14": 2}


def test_average_duration_ignores_bad_values():
    events = [end("2026-04-14T09:00:00+00:00", 30), end("2026-04-14T09:05:00+00:00", 90),
              end("2026-04-14T09:10:00+00:00", None), end("2026-04-14T09:15:00+00:00", "n/a")]
    assert compute_metrics(events, today=TODAY)["avg_duration_sec"] == 60.0


def test_booking_rate_is_bookings_per_completed_call():
    events = [end("2026-04-14T09:00:00+00:00"), end("2026-04-14T10:00:00+00:00"),
              end("2026-04-14T11:00:00+00:00"), end("2026-04-14T12:00:00+00:00"), book("Rückenschmerzen", "booked")]
    m = compute_metrics(events, today=TODAY)
    assert m["bookings_total"] == 1 and m["booking_rate"] == 25.0


def test_only_successful_bookings_count_and_old_logs_without_status_still_count():
    events = [end("2026-04-14T09:00:00+00:00"), book("A", "booked"), book("B", "unavailable"),
              book("C", "invalid"), book("D")]
    m = compute_metrics(events, today=TODAY)
    assert m["bookings_total"] == 2 and m["tool_calls_total"] == 4
    assert dict(m["top_reasons"]) == {"A": 1, "D": 1}


def test_top_reasons_are_sorted_by_frequency():
    events = [book("Impfung", "booked")] * 3 + [book("Rücken", "booked")]
    assert compute_metrics(events, today=TODAY)["top_reasons"][0] == ("Impfung", 3)


def test_dates_are_grouped_in_the_clinics_timezone():
    # 22:30 UTC on the 13th is already the 14th in Berlin (UTC+2 in April)
    m = compute_metrics([end("2026-04-13T22:30:00+00:00")], today=TODAY, tz=ZoneInfo("Europe/Berlin"))
    assert m["calls_per_day"] == {"2026-04-14": 1} and m["calls_today"] == 1


def test_ended_reasons_are_summarised():
    m = compute_metrics([end("2026-04-14T09:00:00+00:00", 5, "voicemail"), end("2026-04-14T09:05:00+00:00", 5, "voicemail")], today=TODAY)
    assert m["ended_reasons"] == [("voicemail", 2)]


def test_load_events_skips_blank_and_malformed_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"event_type":"end_of_call"}\n\nnot json\n[1,2]\n{"event_type":"tool_call"}\n', encoding="utf-8")
    assert len(load_events(p)) == 2


def test_missing_log_file_is_an_empty_list(tmp_path):
    assert load_events(tmp_path / "missing.jsonl") == []


def test_date_range_filter(tmp_path):
    p = tmp_path / "log.jsonl"
    write(p, [end("2026-04-12T10:00:00+00:00"), end("2026-04-13T10:00:00+00:00"), end("2026-04-14T10:00:00+00:00")])
    assert len(load_events(p, date_from=date(2026, 4, 13))) == 2
    assert len(load_events(p, date_to=date(2026, 4, 13))) == 2
    assert len(load_events(p, date(2026, 4, 13), date(2026, 4, 13))) == 1


def test_events_without_a_valid_timestamp_are_dropped_by_a_date_filter(tmp_path):
    p = tmp_path / "log.jsonl"
    write(p, [{"event_type": "end_of_call"}, end("2026-04-13T10:00:00+00:00")])
    assert len(load_events(p, date_from=date(2026, 4, 1))) == 1


def test_get_analytics_returns_every_field(tmp_path):
    p = tmp_path / "log.jsonl"
    write(p, [end("2026-04-13T10:00:00+00:00")])
    assert set(get_analytics(log_file=p)) == {
        "calls_total", "calls_today", "avg_duration_sec", "bookings_total", "booking_rate",
        "tool_calls_total", "top_reasons", "ended_reasons", "calls_per_day"}
