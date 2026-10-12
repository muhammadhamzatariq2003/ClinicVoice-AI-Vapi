"""Webhook logic, kept free of any web framework so it is trivial to test.

``process_message(payload)`` takes the JSON body Vapi posts and returns ``(status_code, json_body)``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from app.agent import build_first_message
from app.availability import (
    Availability,
    BookingStore,
    BookingStoreError,
    format_date_de,
    parse_date,
    parse_time,
)
from app.call_log import log_call_event
from app.clinic import ClinicConfig, load_clinic
from app.notifications import send_notification
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

TECH_ERROR = (
    "Es ist ein technischer Fehler aufgetreten. Bitte entschuldigen Sie sich beim Anrufer und "
    "bitten Sie ihn, es später erneut zu versuchen oder die Praxis direkt anzurufen."
)
MAX_SLOTS_SPOKEN = 6


@dataclass
class AppContext:
    clinic: ClinicConfig
    settings: Settings
    availability: Availability
    log_file: Path | None = None  # tests can redirect the call log


def create_context(
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    clinic: ClinicConfig | None = None,
) -> AppContext:
    settings = settings or get_settings()
    clinic = clinic or load_clinic(settings.clinic_config, settings.transfer_number)
    store = BookingStore(settings.data_dir / "bookings.json")
    return AppContext(clinic, settings, Availability(clinic.schedule, store, clock))


_default_context: AppContext | None = None


def default_context() -> AppContext:
    global _default_context
    if _default_context is None:
        _default_context = create_context()
    return _default_context


def set_default_context(ctx: AppContext | None) -> None:
    """Replace (or reset with None) the process-wide context. Used by tests and scripts."""
    global _default_context
    _default_context = ctx


def _log(ctx: AppContext, *args, **kwargs) -> None:
    log_call_event(*args, path=ctx.log_file, **kwargs)


# --------------------------------------------------------------------------- spoken helpers

def _slot_text(items: list[dict]) -> str:
    return ", ".join(f"{format_date_de(parse_date(i['date']))} um {i['time']} Uhr" for i in items)


def _alternatives_text(ctx: AppContext, day: str, at: str = "00:00") -> str:
    alternatives = ctx.availability.next_available(day, at, count=3)
    if not alternatives:
        return "Derzeit sind keine freien Termine vorhanden. Bitte bitten Sie den Anrufer, es später erneut zu versuchen."
    return f"Nächste freie Termine: {_slot_text(alternatives)}. Bitte fragen Sie den Anrufer, welcher Termin passt."


def _parse_when(date_str: str, time_str: str | None = None) -> tuple[date, str] | str:
    """Return ``(date, 'HH:MM')`` or a German error sentence for the model."""
    try:
        day = parse_date(date_str or "")
    except ValueError:
        return "Das Datum ist ungültig. Bitte das Datum im Format YYYY-MM-DD angeben."
    if time_str is None:
        return day, "00:00"
    try:
        return day, parse_time(time_str).strftime("%H:%M")
    except ValueError:
        return "Die Uhrzeit ist ungültig. Bitte die Uhrzeit im Format HH:MM angeben."


# --------------------------------------------------------------------------- tools

def tool_check_availability(params: dict, ctx: AppContext) -> str:
    parsed = _parse_when(params.get("date", ""), params.get("earliest_time") or "00:00")
    if isinstance(parsed, str):
        return parsed
    day, earliest = parsed
    iso = day.isoformat()

    reason = ctx.availability.closed_reason(day)
    if reason:
        return f"Am {format_date_de(day)} ist die Praxis geschlossen ({reason}). {_alternatives_text(ctx, iso)}"

    free = [s for s in ctx.availability.slots(iso) if s >= earliest]
    if not free:
        return f"Am {format_date_de(day)} sind keine Termine mehr frei. {_alternatives_text(ctx, iso, earliest)}"

    shown = ", ".join(free[:MAX_SLOTS_SPOKEN])
    more = " Es gibt weitere freie Zeiten." if len(free) > MAX_SLOTS_SPOKEN else ""
    return f"Freie Termine am {format_date_de(day)}: {shown} Uhr.{more}"


def tool_book_appointment(params: dict, ctx: AppContext) -> str:
    patient = (params.get("patient_name") or "").strip()
    reason = (params.get("reason") or "").strip()
    is_new = bool(params.get("is_new_patient", False))
    date_str, time_str = params.get("date", ""), params.get("time", "")

    def record(status: str) -> None:
        _log(ctx, "tool_call", caller_name=patient or None, action="book_appointment",
             details={"date": date_str, "time": time_str, "reason": reason,
                      "is_new_patient": is_new, "status": status})

    if not patient:
        record("invalid")
        return "Der Name des Patienten fehlt. Bitte fragen Sie den Anrufer nach dem vollständigen Namen."

    parsed = _parse_when(date_str, time_str)
    if isinstance(parsed, str):
        record("invalid")
        return parsed
    day, at = parsed
    iso = day.isoformat()

    if ctx.availability.is_past(day, parse_time(at)):
        record("invalid")
        return "Dieser Zeitpunkt liegt in der Vergangenheit. Bitte fragen Sie nach einem zukünftigen Termin."

    closed = ctx.availability.closed_reason(day)
    if closed:
        record("unavailable")
        return f"Am {format_date_de(day)} ist die Praxis geschlossen ({closed}). {_alternatives_text(ctx, iso)}"

    if ctx.availability.book(iso, at, patient):
        record("booked")
        kind = "neuer Patient" if is_new else "Patient"
        return (
            f"Termin erfolgreich gebucht: {kind} {patient} am {format_date_de(day)} um {at} Uhr. "
            f"Grund: {reason}. Bitte bestätigen Sie dem Anrufer den Termin."
        )

    record("unavailable")
    return f"Der Termin am {format_date_de(day)} um {at} Uhr ist leider nicht verfügbar. {_alternatives_text(ctx, iso, at)}"


def tool_transfer_call(params: dict, ctx: AppContext) -> str:
    destination = params.get("destination", "praxis")
    reason = params.get("reason", "")
    _log(ctx, "tool_call", action="transfer_call", details={"destination": destination, "reason": reason})
    return (
        f"Anruf wird an {destination} weitergeleitet. Grund: {reason}. "
        "Bitte informieren Sie den Anrufer über die Weiterleitung."
    )


def tool_send_summary(params: dict, ctx: AppContext) -> str:
    summary = params.get("summary", "")
    caller = params.get("caller_name") or "Unbekannt"
    action_required = bool(params.get("action_required", False))

    _log(ctx, "tool_call", caller_name=caller, action="send_summary",
         details={"summary": summary, "action_required": action_required})

    channels = send_notification(summary, caller, action_required)
    if not any(channels.values()):
        logger.warning("No notification channel delivered the summary for %s", caller)
    return "Zusammenfassung gesendet." + (" Aktion erforderlich." if action_required else "")


TOOLS: dict[str, Callable[[dict, AppContext], str]] = {
    "check_availability": tool_check_availability,
    "book_appointment": tool_book_appointment,
    "transfer_call": tool_transfer_call,
    "send_summary": tool_send_summary,
}


def run_tool(name: str, params: dict, ctx: AppContext) -> str:
    handler = TOOLS.get(name)
    if handler is None:
        return f"Unbekannte Funktion: {name}"
    try:
        return handler(params, ctx)
    except BookingStoreError:
        logger.exception("Booking store unavailable")
        return TECH_ERROR
    except Exception:
        logger.exception("Tool %s failed", name)
        return TECH_ERROR


# --------------------------------------------------------------------------- message types

def _as_dict(value) -> dict:
    """Vapi sends tool arguments either as an object or as a JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _handle_tool_calls(message: dict, ctx: AppContext) -> tuple[int, dict]:
    """Current Vapi format: ``tool-calls`` in, ``{"results": [{"toolCallId", "result"}]}`` out."""
    items = message.get("toolCallList") or [
        entry.get("toolCall", entry) for entry in message.get("toolWithToolCallList", [])
    ]
    results = []
    for item in items:
        function = item.get("function") or {}
        name = item.get("name") or function.get("name", "")
        args = _as_dict(item.get("arguments", function.get("arguments")))
        results.append({"toolCallId": item.get("id", ""), "result": run_tool(name, args, ctx)})
    return 200, {"results": results}


def _handle_function_call(message: dict, ctx: AppContext) -> tuple[int, dict]:
    """Legacy ``function-call`` format, kept for older assistants."""
    call = message.get("functionCall") or {}
    name = call.get("name", "")
    if name not in TOOLS:
        return 400, {"detail": f"Unknown function: {name}"}
    return 200, {"result": run_tool(name, _as_dict(call.get("parameters")), ctx)}


def _handle_transfer_destination(message: dict, ctx: AppContext) -> tuple[int, dict]:
    _log(ctx, "transfer_destination", action="transfer_call",
         details={"destination": message.get("destination", "praxis")})
    return 200, {
        "destination": {
            "type": "number",
            "number": ctx.clinic.transfer_number,
            "message": "Ich verbinde Sie jetzt mit der Praxis. Einen Moment bitte.",
        }
    }


def _handle_assistant_request(message: dict, ctx: AppContext) -> tuple[int, dict]:
    """Per-call greeting: Vapi asks us which assistant to use when a number has a Server URL but no assistant."""
    assistant_id = ctx.settings.vapi_assistant_id
    if not assistant_id:
        return 200, {"error": "VAPI_ASSISTANT_ID is not configured on the server."}
    greeting = build_first_message(ctx.clinic, ctx.availability.now())
    return 200, {"assistantId": assistant_id, "assistantOverrides": {"firstMessage": greeting}}


def _handle_end_of_call(message: dict, ctx: AppContext) -> tuple[int, dict]:
    analysis = message.get("analysis") or {}
    call = message.get("call") or {}
    _log(ctx, "end_of_call", details={
        "call_id": call.get("id"),
        "duration": message.get("durationSeconds"),
        "ended_reason": message.get("endedReason"),
        "summary": message.get("summary") or analysis.get("summary"),
        "cost": message.get("cost"),
    })
    return 200, {"status": "logged"}


MESSAGE_HANDLERS: dict[str, Callable[[dict, AppContext], tuple[int, dict]]] = {
    "tool-calls": _handle_tool_calls,
    "function-call": _handle_function_call,
    "transfer-destination-request": _handle_transfer_destination,
    "assistant-request": _handle_assistant_request,
    "end-of-call-report": _handle_end_of_call,
}


def process_message(payload: object, ctx: AppContext | None = None) -> tuple[int, dict]:
    """Route one Vapi server message. Always returns a status code and a JSON-serialisable body."""
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
        return 400, {"detail": "Expected a JSON object with a 'message' field"}

    message = payload["message"]
    kind = message.get("type", "")
    handler = MESSAGE_HANDLERS.get(kind)
    if handler is None:
        status = "received" if kind == "status-update" else "ignored"
        return 200, {"status": status, "type": kind}
    return handler(message, ctx or default_context())
