from pathlib import Path

from app.settings import ROOT, get_settings


def test_defaults():
    s = get_settings({})
    assert s.port == 8000 and s.debug is False
    assert s.llm_provider == "anthropic" and s.llm_model
    assert s.stt_language == "de"
    assert s.webhook_secret is None and s.dashboard_token is None
    assert s.clinic_config == ROOT / "configs/examples/general_practice_munich.yaml"
    assert s.data_dir == ROOT / "data"


def test_overrides_and_flags():
    s = get_settings({"PORT": "9000", "DEBUG": "True", "DATA_DIR": "/var/clinic", "VAPI_WEBHOOK_SECRET": "x",
                      "CLINIC_CONFIG": "configs/examples/dental_clinic_berlin.yaml"})
    assert s.port == 9000 and s.debug is True
    assert s.data_dir == Path("/var/clinic")
    assert s.webhook_secret == "x"
    assert s.clinic_config == ROOT / "configs/examples/dental_clinic_berlin.yaml"


def test_empty_strings_count_as_unset():
    s = get_settings({"VAPI_API_KEY": "", "CLINIC_TRANSFER_NUMBER": ""})
    assert s.vapi_api_key is None and s.transfer_number is None
