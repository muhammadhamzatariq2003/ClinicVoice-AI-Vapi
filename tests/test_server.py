"""HTTP-level tests. They need FastAPI's test client (httpx) and are skipped if it is missing."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import handlers  # noqa: E402
from app.server import app  # noqa: E402


@pytest.fixture
def client(ctx, monkeypatch):
    monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    handlers.set_default_context(ctx)
    yield TestClient(app)
    handlers.set_default_context(None)


def tool_payload(name, arguments):
    return {"message": {"type": "tool-calls", "toolCallList": [{"id": "1", "name": name, "arguments": arguments}]}}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_webhook_books_an_appointment(client):
    r = client.post("/api/vapi/webhook", json=tool_payload("book_appointment", {
        "patient_name": "Thomas Schneider", "date": "2026-04-14", "time": "09:00", "reason": "Rücken"}))
    assert r.status_code == 200
    assert "Termin erfolgreich gebucht" in r.json()["results"][0]["result"]


def test_webhook_rejects_invalid_json(client):
    r = client.post("/api/vapi/webhook", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_webhook_rejects_payload_without_message(client):
    assert client.post("/api/vapi/webhook", json={"hello": "world"}).status_code == 400


def test_webhook_requires_signature_when_secret_is_set(client, monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({"message": {"type": "status-update"}}).encode()
    assert client.post("/api/vapi/webhook", content=body).status_code == 401
    good = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    ok = client.post("/api/vapi/webhook", content=body, headers={"x-vapi-signature": good, "content-type": "application/json"})
    assert ok.status_code == 200
    assert client.get("/health").status_code == 200  # health stays open


def test_analytics_endpoint(client):
    r = client.get("/api/analytics", params={"from": "2026-04-01", "to": "2026-04-30"})
    assert r.status_code == 200 and r.json()["calls_total"] == 0


def test_analytics_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "tok")
    assert client.get("/api/analytics").status_code == 401
    assert client.get("/api/analytics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/analytics", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert client.get("/api/analytics", headers={"x-api-key": "tok"}).status_code == 200


def test_dashboard_page_is_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Call analytics" in r.text
