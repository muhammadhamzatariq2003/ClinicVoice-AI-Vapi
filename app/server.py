"""FastAPI app: Vapi webhook, analytics API and the dashboard page."""

from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app import __version__, handlers
from app.analytics import get_analytics
from app.security import WebhookAuthMiddleware
from app.settings import ROOT, get_settings

logger = logging.getLogger(__name__)

DASHBOARD_FILE = ROOT / "dashboard" / "index.html"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    handlers.default_context()  # fail fast on a broken clinic config instead of on the first call
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Hamza Clinic Voice Agent",
    version=__version__,
    description="Webhook server for a Vapi phone receptionist: bookings, transfers, summaries, analytics.",
)
app.add_middleware(WebhookAuthMiddleware)


def require_dashboard_token(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Protect analytics when DASHBOARD_TOKEN is set (Bearer token or x-api-key header)."""
    token = get_settings().dashboard_token
    if not token:
        return
    supplied = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard token")


@app.get("/")
async def index():
    return {"service": app.title, "version": __version__, "dashboard": "/dashboard", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    if not DASHBOARD_FILE.exists():
        raise HTTPException(status_code=404, detail="Dashboard file missing")
    return FileResponse(DASHBOARD_FILE)


@app.get("/api/analytics", dependencies=[Depends(require_dashboard_token)])
async def analytics(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
):
    """Call metrics as JSON, optionally limited to a date range."""
    ctx = handlers.default_context()
    return await asyncio.to_thread(
        get_analytics,
        date_from,
        date_to,
        ctx.log_file,
        ctx.clinic.schedule.timezone,
    )


@app.post("/api/vapi/webhook")
async def vapi_webhook(request: Request):
    """Receives every Vapi server message (tool calls, transfers, end-of-call reports...)."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # File and SMTP work is blocking, so keep it off the event loop.
    status, body = await asyncio.to_thread(handlers.process_message, payload)
    return JSONResponse(status_code=status, content=body)
