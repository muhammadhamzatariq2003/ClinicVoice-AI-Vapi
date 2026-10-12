"""Clinic configuration: one self-contained YAML file per clinic (identity, hours, closures)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from app.holidays import REGIONS

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_ABBR_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")

REQUIRED_FIELDS = (
    "clinic_name", "doctor_name", "doctor_title", "address", "phone", "services",
    "greeting", "farewell", "emergency_number", "transfer_number", "office_hours",
)

TimeRange = tuple[time, time]


class ConfigError(ValueError):
    """Raised when a clinic YAML file is incomplete or invalid."""


@dataclass(frozen=True)
class Schedule:
    """When the clinic is open and how appointment slots are cut."""

    hours: dict[int, tuple[TimeRange, ...]]  # weekday index (Mon=0) -> opening ranges
    slot_minutes: int = 30
    holiday_region: str = "BY"
    closed_days: dict[date, str] = field(default_factory=dict)  # extra closures (vacation etc.)
    timezone: str = "Europe/Berlin"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class ClinicConfig:
    clinic_name: str
    doctor_name: str
    doctor_title: str
    address: str
    phone: str
    services: tuple[str, ...]
    greeting: str
    farewell: str
    emergency_number: str
    transfer_number: str
    schedule: Schedule
    opening_hours_text: str
    assistant_name: str = "Lisa"

    def with_transfer_number(self, number: str | None) -> "ClinicConfig":
        if not number:
            return self
        _check_e164(number)
        return replace(self, transfer_number=number)


# --------------------------------------------------------------------------- parsing

def _check_e164(number: str) -> None:
    if not _E164.match(number):
        raise ConfigError(
            f"Transfer number {number!r} must be in international E.164 format, e.g. +498912345678"
        )


def _parse_hhmm(h: str, m: str, where: str) -> time:
    try:
        return time(int(h), int(m))
    except ValueError as exc:
        raise ConfigError(f"{where}: invalid time {h}:{m}") from exc


def parse_ranges(value: Any, where: str) -> tuple[TimeRange, ...]:
    """Turn ``["08:00-12:00", "14:00-18:00"]`` into sorted, non-overlapping time ranges."""
    if value in (None, [], ()):
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{where}: expected a list of 'HH:MM-HH:MM' ranges")
    ranges: list[TimeRange] = []
    for item in value:
        m = _RANGE.match(str(item).strip())
        if not m:
            raise ConfigError(f"{where}: {item!r} is not a 'HH:MM-HH:MM' range")
        start = _parse_hhmm(m[1], m[2], where)
        end = _parse_hhmm(m[3], m[4], where)
        if start >= end:
            raise ConfigError(f"{where}: range {item!r} must end after it starts")
        ranges.append((start, end))
    ranges.sort()
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
        if next_start < prev_end:
            raise ConfigError(f"{where}: opening ranges overlap")
    return tuple(ranges)


def _fmt_time(t: time) -> str:
    return f"{t.hour}:{t.minute:02d}"


def format_opening_hours_de(hours: dict[int, tuple[TimeRange, ...]]) -> str:
    """Spoken German summary, e.g. 'Mo-Fr 8:00-12:00 und 14:00-18:00 Uhr, Sa 9:00-14:00 Uhr'."""
    groups: list[tuple[int, int, tuple[TimeRange, ...]]] = []
    for day in range(7):
        ranges = hours.get(day, ())
        if not ranges:
            continue
        if groups and groups[-1][1] == day - 1 and groups[-1][2] == ranges:
            groups[-1] = (groups[-1][0], day, ranges)
        else:
            groups.append((day, day, ranges))
    parts = []
    for first, last, ranges in groups:
        label = _ABBR_DE[first] if first == last else f"{_ABBR_DE[first]}-{_ABBR_DE[last]}"
        spans = " und ".join(f"{_fmt_time(a)}-{_fmt_time(b)}" for a, b in ranges)
        parts.append(f"{label} {spans} Uhr")
    return ", ".join(parts)


def _parse_schedule(data: dict) -> Schedule:
    raw_hours = data["office_hours"]
    if not isinstance(raw_hours, dict):
        raise ConfigError("office_hours must be a mapping of weekday -> list of ranges")
    unknown = set(raw_hours) - set(WEEKDAYS)
    if unknown:
        raise ConfigError(f"office_hours has unknown days: {sorted(unknown)}")
    hours = {i: parse_ranges(raw_hours.get(name), f"office_hours.{name}") for i, name in enumerate(WEEKDAYS)}

    slot = int(data.get("slot_duration_minutes", 30))
    if not 5 <= slot <= 240:
        raise ConfigError("slot_duration_minutes must be between 5 and 240")

    region = str(data.get("holiday_region", "BY")).upper()
    if region not in REGIONS:
        raise ConfigError(f"holiday_region must be one of {REGIONS}")

    tz_name = str(data.get("timezone", "Europe/Berlin"))
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown timezone {tz_name!r} (is the 'tzdata' package installed?)") from exc

    closed: dict[date, str] = {}
    for entry in data.get("closed_days") or []:
        try:
            closed[date.fromisoformat(str(entry["date"]))] = str(entry.get("name", "Betriebsurlaub"))
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"closed_days entry {entry!r} needs an ISO 'date' (YYYY-MM-DD)") from exc

    return Schedule(hours=hours, slot_minutes=slot, holiday_region=region, closed_days=closed, timezone=tz_name)


def clinic_from_dict(data: dict) -> ClinicConfig:
    if not isinstance(data, dict):
        raise ConfigError("Clinic config must be a YAML mapping")
    missing = [k for k in REQUIRED_FIELDS if data.get(k) in (None, "", [])]
    if missing:
        raise ConfigError(f"Clinic config is missing required fields: {', '.join(missing)}")

    services = data["services"]
    if not isinstance(services, list) or not all(isinstance(s, str) for s in services):
        raise ConfigError("services must be a list of strings")

    _check_e164(str(data["transfer_number"]))
    schedule = _parse_schedule(data)
    spoken_hours = data.get("opening_hours_text") or format_opening_hours_de(schedule.hours)

    return ClinicConfig(
        clinic_name=str(data["clinic_name"]),
        doctor_name=str(data["doctor_name"]),
        doctor_title=str(data["doctor_title"]),
        address=str(data["address"]),
        phone=str(data["phone"]),
        services=tuple(services),
        greeting=str(data["greeting"]),
        farewell=str(data["farewell"]),
        emergency_number=str(data["emergency_number"]),
        transfer_number=str(data["transfer_number"]),
        schedule=schedule,
        opening_hours_text=spoken_hours,
        assistant_name=str(data.get("assistant_name", "Lisa")),
    )


def load_clinic(path: str | Path, transfer_number: str | None = None) -> ClinicConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Clinic config not found: {path}")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    try:
        clinic = clinic_from_dict(data)
    except ConfigError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    return clinic.with_transfer_number(transfer_number)
