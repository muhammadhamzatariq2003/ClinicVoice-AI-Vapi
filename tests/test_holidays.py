from datetime import date

import pytest

from app.holidays import easter_sunday, german_holidays, is_holiday


@pytest.mark.parametrize("year, expected", [
    (2024, date(2024, 3, 31)),
    (2025, date(2025, 4, 20)),
    (2026, date(2026, 4, 5)),
    (2027, date(2027, 3, 28)),
    (2038, date(2038, 4, 25)),
])
def test_easter_sunday(year, expected):
    assert easter_sunday(year) == expected


def test_bavaria_2026_has_thirteen_holidays():
    assert len(german_holidays(2026, "BY")) == 13


def test_federal_region_has_only_nationwide_holidays():
    assert len(german_holidays(2026, "FEDERAL")) == 9


@pytest.mark.parametrize("day, name", [
    (date(2026, 1, 1), "Neujahr"),
    (date(2026, 4, 3), "Karfreitag"),
    (date(2026, 4, 6), "Ostermontag"),
    (date(2026, 5, 1), "Tag der Arbeit"),
    (date(2026, 5, 14), "Christi Himmelfahrt"),
    (date(2026, 5, 25), "Pfingstmontag"),
    (date(2026, 6, 4), "Fronleichnam"),
    (date(2026, 10, 3), "Tag der Deutschen Einheit"),
    (date(2026, 12, 25), "1. Weihnachtstag"),
])
def test_known_2026_holidays(day, name):
    assert is_holiday(day, "BY") == (True, name)


def test_works_for_any_year_not_just_2026():
    assert is_holiday(date(2031, 4, 11), "BY") == (True, "Karfreitag")  # Easter 2031 is April 13


def test_regional_differences():
    assert is_holiday(date(2026, 1, 6), "BY")[0] is True
    assert is_holiday(date(2026, 1, 6), "BE")[0] is False
    assert is_holiday(date(2026, 3, 8), "BE") == (True, "Internationaler Frauentag")
    assert is_holiday(date(2026, 10, 31), "HH") == (True, "Reformationstag")


def test_ordinary_day_is_not_a_holiday():
    assert is_holiday(date(2026, 4, 14)) == (False, None)


def test_extra_closed_days_count_as_holidays():
    extra = {date(2026, 8, 10): "Betriebsurlaub"}
    assert is_holiday(date(2026, 8, 10), "BY", extra) == (True, "Betriebsurlaub")


def test_unknown_region_is_rejected():
    with pytest.raises(ValueError):
        german_holidays(2026, "XX")
