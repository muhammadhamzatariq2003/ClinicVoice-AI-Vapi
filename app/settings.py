"""Environment-driven settings. Nothing here touches the network or disk at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLINIC_CONFIG = "configs/examples/general_practice_munich.yaml"


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _path(value: str | None, default: str) -> Path:
    p = Path(value or default)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Settings:
    # Vapi
    vapi_api_key: str | None
    vapi_assistant_id: str | None
    webhook_secret: str | None
    # Access to the analytics API / dashboard (optional)
    dashboard_token: str | None
    # Files
    clinic_config: Path
    data_dir: Path
    # Optional override for the number calls are transferred to (E.164)
    transfer_number: str | None
    # Models
    llm_provider: str
    llm_model: str
    voice_provider: str
    voice_id: str
    voice_model: str | None
    stt_provider: str
    stt_model: str
    stt_language: str
    # Server
    host: str
    port: int
    debug: bool


def load_dotenv_file() -> None:
    """Load ``.env`` from the project root if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is in requirements.txt
        return
    load_dotenv(ROOT / ".env", override=False)


def get_settings(env: dict[str, str] | None = None) -> Settings:
    """Read settings from ``env`` (defaults to ``os.environ``). Never cached, so tests can change env freely."""
    e = os.environ if env is None else env
    return Settings(
        vapi_api_key=e.get("VAPI_API_KEY") or None,
        vapi_assistant_id=e.get("VAPI_ASSISTANT_ID") or None,
        webhook_secret=e.get("VAPI_WEBHOOK_SECRET") or None,
        dashboard_token=e.get("DASHBOARD_TOKEN") or None,
        clinic_config=_path(e.get("CLINIC_CONFIG"), DEFAULT_CLINIC_CONFIG),
        data_dir=_path(e.get("DATA_DIR"), "data"),
        transfer_number=e.get("CLINIC_TRANSFER_NUMBER") or None,
        llm_provider=e.get("LLM_PROVIDER", "anthropic"),
        llm_model=e.get("LLM_MODEL", "claude-sonnet-5-5"),
        voice_provider=e.get("VOICE_PROVIDER", "11labs"),
        voice_id=e.get("VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        voice_model=e.get("VOICE_MODEL", "eleven_multilingual_v2") or None,
        stt_provider=e.get("STT_PROVIDER", "deepgram"),
        stt_model=e.get("STT_MODEL", "nova-3"),
        stt_language=e.get("STT_LANGUAGE", "de"),
        host=e.get("HOST", "0.0.0.0"),
        port=int(e.get("PORT", "8000")),
        debug=_flag(e.get("DEBUG")),
    )
