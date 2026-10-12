from datetime import date, time
from pathlib import Path

import pytest

from app.clinic import ConfigError, clinic_from_dict, format_opening_hours_de, load_clinic, parse_ranges

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "configs" / "examples").glob("*.yaml"))


def test_valid_config_loads(clinic):
    assert clinic.clinic_name == "Praxis Dr. Test"
    assert clinic.schedule.slot_minutes == 30
    assert clinic.assistant_name == "Lisa"


def test_spoken_hours_are_generated_from_office_hours(clinic):
    assert clinic.opening_hours_text == "Mo-Fr 8:00-12:00 und 14:00-18:00 Uhr"


def test_spoken_hours_can_be_overridden(clinic_data):
    clinic_data["opening_hours_text"] = "nach Vereinbarung"
    assert clinic_from_dict(clinic_data).opening_hours_text == "nach Vereinbarung"


def test_different_friday_hours_are_grouped_separately():
    day = parse_ranges(["08:00-13:00"], "x")
    short = parse_ranges(["08:00-14:00"], "x")
    text = format_opening_hours_de({0: day, 1: day, 2: day, 3: day, 4: short})
    assert text == "Mo-Do 8:00-13:00 Uhr, Fr 8:00-14:00 Uhr"


def test_missing_required_fields_are_listed(clinic_data):
    del clinic_data["address"]
    del clinic_data["farewell"]
    with pytest.raises(ConfigError) as err:
        clinic_from_dict(clinic_data)
    assert "address" in str(err.value) and "farewell" in str(err.value)


@pytest.mark.parametrize("number", ["089 12345678", "0049123456", "+0123456", "abc"])
def test_transfer_number_must_be_e164(clinic_data, number):
    clinic_data["transfer_number"] = number
    with pytest.raises(ConfigError):
        clinic_from_dict(clinic_data)


@pytest.mark.parametrize("ranges", [["12:00-08:00"], ["8-12"], ["08:00-12:00", "11:00-13:00"], ["25:00-26:00"]])
def test_bad_ranges_are_rejected(ranges):
    with pytest.raises(ConfigError):
        parse_ranges(ranges, "monday")


def test_unknown_weekday_is_rejected(clinic_data):
    clinic_data["office_hours"]["funday"] = ["08:00-12:00"]
    with pytest.raises(ConfigError):
        clinic_from_dict(clinic_data)


def test_unknown_region_and_timezone_are_rejected(clinic_data):
    clinic_data["holiday_region"] = "ZZ"
    with pytest.raises(ConfigError):
        clinic_from_dict(clinic_data)
    clinic_data["holiday_region"] = "BY"
    clinic_data["timezone"] = "Mars/Olympus"
    with pytest.raises(ConfigError):
        clinic_from_dict(clinic_data)


def test_closed_days_are_parsed(clinic_data):
    clinic_data["closed_days"] = [{"date": "2026-08-10", "name": "Betriebsurlaub"}]
    assert clinic_from_dict(clinic_data).schedule.closed_days == {date(2026, 8, 10): "Betriebsurlaub"}


def test_transfer_number_override_is_validated(clinic):
    assert clinic.with_transfer_number("+4915112345678").transfer_number == "+4915112345678"
    assert clinic.with_transfer_number(None) is clinic
    with pytest.raises(ConfigError):
        clinic.with_transfer_number("12345")


def test_parse_ranges_sorts_and_returns_times():
    assert parse_ranges(["14:00-18:00", "08:00-12:00"], "x") == (
        (time(8, 0), time(12, 0)), (time(14, 0), time(18, 0)))


@pytest.mark.parametrize("path", EXAMPLES + [ROOT / "configs" / "clinic_template.yaml"], ids=lambda p: p.name)
def test_every_shipped_config_is_valid(path):
    assert load_clinic(path).schedule.slot_minutes > 0
