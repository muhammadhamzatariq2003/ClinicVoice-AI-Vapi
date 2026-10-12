"""Call-summary notifications: SMTP email and/or a generic webhook (Slack, n8n, Make...)."""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 10


def _one_line(text: str) -> str:
    """Strip line breaks so model-provided text can never inject email headers."""
    return " ".join(str(text).split())


def send_notification(summary: str, caller_name: str, action_required: bool) -> dict[str, bool]:
    """Send via every configured channel. Unconfigured channels are skipped, failures never raise."""
    return {
        "email": _send_email(summary, caller_name, action_required),
        "webhook": _send_webhook(summary, caller_name, action_required),
    }


def _send_email(summary: str, caller_name: str, action_required: bool) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("NOTIFICATION_EMAIL_TO")
    from_addr = os.environ.get("NOTIFICATION_EMAIL_FROM", user or "")

    if not all([host, user, password, to_addr]):
        return False

    caller = _one_line(caller_name)
    body = (
        "Anrufzusammenfassung\n"
        "====================\n"
        f"Anrufer: {caller}\n"
        f"Aktion erforderlich: {'Ja' if action_required else 'Nein'}\n\n"
        f"{summary}"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Anruf von {caller}" + (" - Aktion erforderlich" if action_required else "")
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS) as server:
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as server:
                server.starttls()
                server.login(user, password)
                server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send email notification")
        return False


def _send_webhook(summary: str, caller_name: str, action_required: bool) -> bool:
    url = os.environ.get("NOTIFICATION_WEBHOOK_URL")
    if not url:
        return False

    payload = {
        "caller_name": caller_name,
        "summary": summary,
        "action_required": action_required,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # Slack-compatible field so the same URL works for incoming webhooks
        "text": f"{'ACTION REQUIRED - ' if action_required else ''}Call from {_one_line(caller_name)}: {summary}",
    }
    try:
        response = httpx.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except Exception:
        logger.exception("Failed to send webhook notification")
        return False
