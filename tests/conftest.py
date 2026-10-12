"""Shared fixtures: a frozen clock, an isolated data directory and a ready-made app context."""

import dataclasses
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.clinic import clinic_from_dict
from app.handlers import create_context
from app.settings import get_settings

BERLIN = ZoneInfo("Europe/Berlin")
# Monday 2026-04-13, 09:00 in Berlin. The week after Easter, so Karfreitag (04-03) and Ostermontag (04-06) are past.
FROZEN_NOW = datetime(2026, 4, 13, 9, 0, tzinfo=BERLIN)

CLINIC_DATA = {
    "clinic_name": "Praxis Dr. Test",
    "doctor_name": "Dr. Tina Test",
    "doctor_title": "Fachärztin für Allgemeinmedizin",
    "address": "Teststraße 1, 80331 München",
    "phone": "089 123456",
    "services": ["Allgemeinmedizin", "Impfungen"],
    "greeting": "Praxis Dr. Test, hier spricht Lisa. Wie kann ich helfen?",
    "farewell": "Auf Wiederhören!",
    "emergency_number": "112",
    "transfer_number": "+498912345678",
    "office_hours": {
        "monday": ["08:00-12:00", "14:00-18:00"],
        "tuesday": ["08:00-12:00", "14:00-18:00"],
        "wednesday": ["08:00-12:00", "14:00-18:00"],
        "thursday": ["08:00-12:00", "14:00-18:00"],
        "friday": ["08:00-12:00", "14:00-18:00"],
        "saturday": [],
        "sunday": [],
    },
    "slot_duration_minutes": 30,
    "holiday_region": "BY",
}


@pytest.fixture
def clinic_data():
    return {**CLINIC_DATA, "office_hours": dict(CLINIC_DATA["office_hours"])}


@pytest.fixture
def clinic(clinic_data):
    return clinic_from_dict(clinic_data)


@pytest.fixture
def frozen_clock():
    return lambda: FROZEN_NOW


@pytest.fixture
def ctx(tmp_path, clinic, frozen_clock):
    settings = dataclasses.replace(get_settings({}), data_dir=Path(tmp_path))
    context = create_context(settings, clock=frozen_clock, clinic=clinic)
    context.log_file = Path(tmp_path) / "call_log.jsonl"
    return context
