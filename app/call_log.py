"""Append-only JSONL call log (one event per line)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.settings import get_settings

LOG_NAME = "call_log.jsonl"
_LOCK = threading.Lock()


def log_path() -> Path:
    return get_settings().data_dir / LOG_NAME


def log_call_event(
    event_type: str,
    caller_name: str | None = None,
    action: str | None = None,
    details: dict | None = None,
    *,
    path: Path | None = None,
) -> dict:
    """Append one event and return it. The data directory is created on first write, not at import."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "caller_name": caller_name,
        "action": action,
        "details": details or {},
    }
    target = path or log_path()
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    return entry
