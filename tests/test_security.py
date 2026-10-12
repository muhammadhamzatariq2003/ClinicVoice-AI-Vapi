import asyncio
import hashlib
import hmac
import json

from app.security import WebhookAuthMiddleware, verify_shared_secret, verify_signature

SECRET = "s3cret"
BODY = b'{"message":{"type":"status-update"}}'


def sign(body=BODY, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature():
    assert verify_signature(SECRET, BODY, sign()) is True
    assert verify_signature(SECRET, BODY, sign().upper()) is True


def test_wrong_secret_or_tampered_body_fails():
    assert verify_signature(SECRET, BODY, sign(secret="other")) is False
    assert verify_signature(SECRET, BODY + b" ", sign()) is False
    assert verify_signature(SECRET, BODY, "") is False


def test_non_ascii_signature_does_not_crash():
    assert verify_signature(SECRET, BODY, "ünïcode") is False


def test_shared_secret_header():
    assert verify_shared_secret(SECRET, SECRET) is True
    assert verify_shared_secret(SECRET, "nope") is False


# --- the ASGI middleware, driven without any web framework ---------------------------

def run(path="/api/vapi/webhook", method="POST", headers=None, body=BODY):
    """Call the middleware and return (inner_app_was_called, status_or_None, body_seen_by_inner_app)."""
    seen = {"called": False, "body": None}

    async def inner(scope, receive, send):
        seen["called"] = True
        message = await receive()
        seen["body"] = message["body"]

    sent = []

    async def send(message):
        sent.append(message)

    queue = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    scope = {"type": "http", "method": method, "path": path,
             "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    asyncio.run(WebhookAuthMiddleware(inner)(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return seen["called"], status, seen["body"]


def test_no_secret_configured_lets_everything_through(monkeypatch):
    monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
    assert run()[0] is True


def test_missing_signature_is_401(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    called, status, _ = run()
    assert called is False and status == 401


def test_bad_signature_is_401(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    called, status, _ = run(headers={"x-vapi-signature": "deadbeef"})
    assert called is False and status == 401


def test_good_signature_passes_and_the_body_is_still_readable(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    called, _, body = run(headers={"x-vapi-signature": sign()})
    assert called is True and json.loads(body)["message"]["type"] == "status-update"


def test_shared_secret_header_passes(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    assert run(headers={"x-vapi-secret": SECRET})[0] is True
    assert run(headers={"x-vapi-secret": "wrong"})[0] is False


def test_other_paths_and_methods_are_not_guarded(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    assert run(path="/health", method="GET")[0] is True
    assert run(path="/api/analytics", method="GET")[0] is True
