import smtplib
from unittest.mock import MagicMock

import pytest

from app import notifications

SMTP_ENV = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "bot@example.com", "SMTP_PASSWORD": "pw",
            "NOTIFICATION_EMAIL_TO": "team@example.com", "NOTIFICATION_EMAIL_FROM": "lisa@example.com"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in list(SMTP_ENV) + ["SMTP_PORT", "NOTIFICATION_WEBHOOK_URL"]:
        monkeypatch.delenv(var, raising=False)


def configure_smtp(monkeypatch, **extra):
    for k, v in {**SMTP_ENV, **extra}.items():
        monkeypatch.setenv(k, v)


def test_nothing_configured_means_nothing_sent():
    assert notifications.send_notification("s", "Anna", False) == {"email": False, "webhook": False}


def test_email_uses_starttls_on_port_587(monkeypatch):
    configure_smtp(monkeypatch)
    server = MagicMock()
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value = server
    monkeypatch.setattr(smtplib, "SMTP", smtp)

    assert notifications._send_email("Rückruf bitte", "Anna Schmidt", True) is True
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("bot@example.com", "pw")
    message = server.send_message.call_args[0][0]
    assert "Aktion erforderlich" in message["Subject"] and message["To"] == "team@example.com"


def test_email_uses_ssl_on_port_465(monkeypatch):
    configure_smtp(monkeypatch, SMTP_PORT="465")
    ssl_cls = MagicMock()
    monkeypatch.setattr(smtplib, "SMTP_SSL", ssl_cls)
    assert notifications._send_email("s", "Anna", False) is True
    ssl_cls.assert_called_once()


def test_caller_name_cannot_inject_email_headers(monkeypatch):
    configure_smtp(monkeypatch)
    server = MagicMock()
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value = server
    monkeypatch.setattr(smtplib, "SMTP", smtp)

    notifications._send_email("s", "Anna\r\nBcc: attacker@evil.test", False)
    message = server.send_message.call_args[0][0]
    assert "\n" not in message["Subject"] and "\r" not in message["Subject"]
    assert message["Bcc"] is None


def test_smtp_failure_is_swallowed(monkeypatch):
    configure_smtp(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", MagicMock(side_effect=OSError("down")))
    assert notifications._send_email("s", "Anna", False) is False


def test_webhook_posts_json(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://hooks.example.com/x")
    post = MagicMock()
    monkeypatch.setattr(notifications.httpx, "post", post)

    assert notifications._send_webhook("Termin gebucht", "Anna", True) is True
    url = post.call_args[0][0]
    payload = post.call_args[1]["json"]
    assert url == "https://hooks.example.com/x"
    assert payload["caller_name"] == "Anna" and payload["action_required"] is True
    assert payload["text"].startswith("ACTION REQUIRED")


def test_webhook_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_WEBHOOK_URL", "https://hooks.example.com/x")
    monkeypatch.setattr(notifications.httpx, "post", MagicMock(side_effect=RuntimeError("boom")))
    assert notifications._send_webhook("s", "Anna", False) is False
