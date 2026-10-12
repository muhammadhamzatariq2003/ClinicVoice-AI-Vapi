"""Webhook authentication for Vapi requests.

Two ways Vapi can prove a request is genuine; both are accepted:
  * ``x-vapi-secret``    - the shared secret itself, sent as a header
  * ``x-vapi-signature`` - HMAC-SHA256 of the raw request body, hex encoded

Auth is only enforced when ``VAPI_WEBHOOK_SECRET`` is set.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

logger = logging.getLogger(__name__)

PROTECTED_PREFIX = "/api/vapi/"


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _eq(signature.strip().lower(), expected)


def verify_shared_secret(secret: str, header_value: str) -> bool:
    return _eq(header_value.strip(), secret)


class WebhookAuthMiddleware:
    """Pure ASGI middleware (no body-consumption quirks of BaseHTTPMiddleware)."""

    def __init__(self, app, protected_prefix: str = PROTECTED_PREFIX):
        self.app = app
        self.prefix = protected_prefix

    async def __call__(self, scope, receive, send):
        secret = os.environ.get("VAPI_WEBHOOK_SECRET")
        guarded = (
            secret
            and scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"].startswith(self.prefix)
        )
        if not guarded:
            return await self.app(scope, receive, send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        shared = headers.get("x-vapi-secret")
        if shared and verify_shared_secret(secret, shared):
            return await self.app(scope, receive, send)

        signature = headers.get("x-vapi-signature", "")
        if not signature:
            return await self._reject(scope, receive, send, "Missing signature")

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        if not verify_signature(secret, body, signature):
            return await self._reject(scope, receive, send, "Invalid signature")

        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)

    @staticmethod
    async def _reject(scope, receive, send, detail: str):
        logger.warning("Rejected webhook request: %s", detail)
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
