import dataclasses
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent import (
    build_assistant_config, build_first_message, build_system_prompt, build_tool_definitions,
    build_transcriber_config, build_voice_config,
)
from app.settings import get_settings

BERLIN = ZoneInfo("Europe/Berlin")


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=BERLIN)


# --- system prompt ---------------------------------------------------------

def test_prompt_names_assistant_clinic_and_doctor(clinic):
    prompt = build_system_prompt(clinic)
    assert "Lisa" in prompt and "Praxis Dr. Test" in prompt and "Dr. Tina Test" in prompt


def test_prompt_has_emergency_rule(clinic):
    prompt = build_system_prompt(clinic)
    for needle in ("112", "Brustschmerzen", "Atemnot", "Keinen Termin buchen"):
        assert needle in prompt


def test_prompt_contains_opening_hours_address_and_services(clinic):
    prompt = build_system_prompt(clinic)
    assert clinic.opening_hours_text in prompt
    assert "Teststraße 1" in prompt and "Impfungen" in prompt


def test_prompt_injects_the_current_date_at_call_time(clinic):
    prompt = build_system_prompt(clinic)
    assert '{{"now" | date:' in prompt and "Europe/Berlin" in prompt


def test_prompt_tells_the_model_to_check_availability_before_offering_times(clinic):
    prompt = build_system_prompt(clinic)
    assert "check_availability" in prompt and "Erfinde niemals" in prompt


def test_prompt_is_german(clinic):
    assert "Sprich immer Deutsch" in build_system_prompt(clinic)


# --- greeting --------------------------------------------------------------

def test_normal_hours_use_the_clinic_greeting(clinic):
    assert build_first_message(clinic, at(2026, 4, 14, 10)) == clinic.greeting


def test_holiday_greeting_names_the_holiday(clinic):
    msg = build_first_message(clinic, at(2026, 4, 3, 10))  # Karfreitag
    assert "Karfreitag" in msg and "geschlossen" in msg and "112" in msg


def test_outside_hours_greeting_includes_opening_hours(clinic):
    msg = build_first_message(clinic, at(2026, 4, 14, 20))
    assert "derzeit geschlossen" in msg and clinic.opening_hours_text in msg


def test_weekend_counts_as_closed(clinic):
    assert "derzeit geschlossen" in build_first_message(clinic, at(2026, 4, 18, 10))


def test_greeting_mentions_vacation_closures(clinic_data):
    from app.clinic import clinic_from_dict
    clinic_data["closed_days"] = [{"date": "2026-04-14", "name": "Betriebsurlaub"}]
    msg = build_first_message(clinic_from_dict(clinic_data), at(2026, 4, 14, 10))
    assert "Betriebsurlaub" in msg


# --- tools ------------------------------------------------------------------

def tool(clinic, name):
    return next(t for t in build_tool_definitions(clinic) if t["function"]["name"] == name)


def test_four_tools_with_the_right_types(clinic):
    tools = build_tool_definitions(clinic)
    assert [t["function"]["name"] for t in tools] == [
        "check_availability", "book_appointment", "transfer_call", "send_summary"]
    assert {t["type"] for t in tools} == {"function", "transferCall"}


def test_check_availability_requires_only_a_date(clinic):
    assert tool(clinic, "check_availability")["function"]["parameters"]["required"] == ["date"]


def test_book_appointment_required_fields(clinic):
    required = tool(clinic, "book_appointment")["function"]["parameters"]["required"]
    assert set(required) == {"patient_name", "date", "time", "reason"}


def test_transfer_tool_uses_the_clinic_number(clinic):
    destinations = tool(clinic, "transfer_call")["destinations"]
    assert destinations[0]["number"] == "+498912345678" and destinations[0]["type"] == "number"


# --- assistant payload -------------------------------------------------------

def test_assistant_config_shape(clinic):
    settings = get_settings({})
    cfg = build_assistant_config("https://example.com/api/vapi/webhook", clinic, settings, at(2026, 4, 14, 10))
    assert cfg["serverUrl"] == "https://example.com/api/vapi/webhook"
    assert cfg["firstMessage"] == clinic.greeting
    assert cfg["endCallMessage"] == clinic.farewell
    assert cfg["maxDurationSeconds"] == 300
    assert len(cfg["model"]["messages"]) == 1 and cfg["model"]["messages"][0]["role"] == "system"
    assert len(cfg["name"]) <= 40


def test_transcriber_is_german_by_default():
    assert build_transcriber_config(get_settings({}))["language"] == "de"


def test_models_can_be_changed_through_environment():
    s = get_settings({"LLM_MODEL": "claude-haiku-5-5", "VOICE_ID": "abc", "STT_LANGUAGE": "en"})
    assert s.llm_model == "claude-haiku-5-5"
    assert build_voice_config(s)["voiceId"] == "abc"
    assert build_transcriber_config(s)["language"] == "en"


def test_voice_model_is_optional():
    s = dataclasses.replace(get_settings({}), voice_model=None)
    assert "model" not in build_voice_config(s)
