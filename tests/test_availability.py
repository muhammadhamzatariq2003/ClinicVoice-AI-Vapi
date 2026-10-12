from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.availability import (
    Availability, BookingStore, BookingStoreError, all_slots, closed_reason, format_date_de, is_outside_hours,
)

# Frozen "now" is Monday 2026-04-13 09:00 Berlin (see conftest).


@pytest.fixture
def av(ctx):
    return ctx.availability


def test_open_day_generates_morning_and_afternoon_slots(clinic):
    slots = all_slots(clinic.schedule, date(2026, 4, 14))  # Tuesday
    assert len(slots) == 16
    assert slots[0] == "08:00" and "11:30" in slots and "14:00" in slots and slots[-1] == "17:30"
    assert "12:00" not in slots and "18:00" not in slots


def test_slot_must_fit_before_closing_time(clinic_data):
    from app.clinic import clinic_from_dict
    clinic_data["slot_duration_minutes"] = 45
    clinic_data["office_hours"]["tuesday"] = ["08:00-09:30"]
    slots = all_slots(clinic_from_dict(clinic_data).schedule, date(2026, 4, 14))
    assert slots == ["08:00", "08:45"]  # a third slot would start at 09:30 and end after closing


def test_weekend_has_no_slots(av):
    assert av.slots("2026-04-18") == [] and av.slots("2026-04-19") == []


def test_public_holiday_has_no_slots(av):
    # Fronleichnam (Thursday) is a Bavarian holiday: the original code would have happily booked it
    assert av.slots("2026-06-04") == []
    assert av.closed_reason(date(2026, 6, 4)) == "Fronleichnam"


def test_closed_reason_for_normal_days(clinic):
    assert closed_reason(clinic.schedule, date(2026, 4, 14)) is None
    assert closed_reason(clinic.schedule, date(2026, 4, 18)) == "Ruhetag"


def test_slots_in_the_past_are_not_offered(av):
    today = av.slots("2026-04-13")
    assert "08:00" not in today and "09:00" not in today  # now is 09:00
    assert "09:30" in today
    assert av.slots("2026-04-10") == []  # a past day


def test_booking_removes_the_slot(av):
    assert av.is_available("2026-04-14", "09:00")
    assert av.book("2026-04-14", "09:00", "Thomas Schneider") is True
    assert not av.is_available("2026-04-14", "09:00")


def test_double_booking_is_rejected(av):
    assert av.book("2026-04-14", "09:00", "A") is True
    assert av.book("2026-04-14", "09:00", "B") is False


def test_cannot_book_outside_opening_hours_or_on_closed_days(av):
    assert av.book("2026-04-14", "13:00", "A") is False  # lunch break
    assert av.book("2026-04-18", "09:00", "A") is False  # Saturday
    assert av.book("2026-04-14", "09:10", "A") is False  # not on the 30-minute grid


def test_booking_persists_in_the_store(av):
    av.book("2026-04-14", "10:30", "Maria Weber")
    assert av.store.load() == {"2026-04-14": {"10:30": "Maria Weber"}}


def test_next_available_skips_booked_and_closed_times(av):
    av.book("2026-04-17", "17:30", "A")  # last Friday slot
    result = av.next_available("2026-04-17", "17:00", count=3)
    assert result == [
        {"date": "2026-04-17", "time": "17:00"},
        {"date": "2026-04-20", "time": "08:00"},  # weekend skipped
        {"date": "2026-04-20", "time": "08:30"},
    ]


def test_outside_hours(clinic):
    s = clinic.schedule
    mon = lambda h, m: datetime(2026, 4, 13, h, m)
    assert not is_outside_hours(s, mon(9, 0))
    assert not is_outside_hours(s, mon(15, 0))
    assert is_outside_hours(s, mon(12, 0))   # end is exclusive
    assert is_outside_hours(s, mon(12, 30))
    assert is_outside_hours(s, mon(7, 0))
    assert is_outside_hours(s, mon(19, 0))
    assert is_outside_hours(s, datetime(2026, 4, 18, 10, 0))  # Saturday


def test_german_date_format_does_not_depend_on_locale():
    assert format_date_de(date(2026, 4, 14)) == "Dienstag, 14.04.2026"


def test_corrupt_bookings_file_raises_instead_of_being_overwritten(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text("{not json")
    with pytest.raises(BookingStoreError):
        BookingStore(path).load()
    assert path.read_text() == "{not json"


def test_missing_or_empty_bookings_file_means_no_bookings(tmp_path):
    assert BookingStore(tmp_path / "nope.json").load() == {}
    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert BookingStore(empty).load() == {}


def test_save_leaves_no_temp_files_behind(tmp_path):
    store = BookingStore(tmp_path / "b.json")
    store.save({"2026-04-14": {"09:00": "A"}})
    assert [p.name for p in tmp_path.iterdir()] == ["b.json"]


def test_concurrent_bookings_never_double_book(av):
    import threading
    winners = []

    def attempt(name):
        if av.book("2026-04-14", "10:00", name):
            winners.append(name)

    threads = [threading.Thread(target=attempt, args=(f"P{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1


def test_timezone_is_applied_when_no_clock_is_injected(clinic, tmp_path):
    av = Availability(clinic.schedule, BookingStore(tmp_path / "b.json"))
    assert av.now().tzinfo is not None
    assert av.now().utcoffset() == datetime.now(ZoneInfo("Europe/Berlin")).utcoffset()
