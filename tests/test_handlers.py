import json

from app.handlers import TECH_ERROR, process_message, run_tool


def tool_call(name, arguments, call_id="call_1"):
    return {"message": {"type": "tool-calls", "toolCallList": [{"id": call_id, "name": name, "arguments": arguments}]}}


def result_of(ctx, name, arguments):
    status, body = process_message(tool_call(name, arguments), ctx)
    assert status == 200
    return body["results"][0]["result"]


def log_lines(ctx):
    return [json.loads(line) for line in ctx.log_file.read_text(encoding="utf-8").splitlines()]


BOOK = {"patient_name": "Thomas Schneider", "date": "2026-04-14", "time": "09:00", "reason": "Rückenschmerzen"}


# --- message envelope ------------------------------------------------------------

def test_response_echoes_tool_call_id(ctx):
    status, body = process_message(tool_call("check_availability", {"date": "2026-04-14"}, call_id="abc"), ctx)
    assert status == 200 and body["results"][0]["toolCallId"] == "abc"


def test_arguments_may_arrive_as_a_json_string(ctx):
    payload = tool_call("check_availability", json.dumps({"date": "2026-04-14"}))
    assert "Freie Termine" in process_message(payload, ctx)[1]["results"][0]["result"]


def test_tool_with_tool_call_list_format_is_understood(ctx):
    payload = {"message": {"type": "tool-calls", "toolWithToolCallList": [
        {"toolCall": {"id": "t1", "function": {"name": "check_availability", "arguments": {"date": "2026-04-14"}}}}]}}
    status, body = process_message(payload, ctx)
    assert status == 200 and body["results"][0]["toolCallId"] == "t1"


def test_several_tool_calls_in_one_message(ctx):
    payload = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "1", "name": "check_availability", "arguments": {"date": "2026-04-14"}},
        {"id": "2", "name": "check_availability", "arguments": {"date": "2026-04-15"}}]}}
    assert [r["toolCallId"] for r in process_message(payload, ctx)[1]["results"]] == ["1", "2"]


def test_legacy_function_call_format_still_works(ctx):
    payload = {"message": {"type": "function-call", "functionCall": {"name": "check_availability", "parameters": {"date": "2026-04-14"}}}}
    status, body = process_message(payload, ctx)
    assert status == 200 and "Freie Termine" in body["result"]


def test_legacy_unknown_function_is_a_400(ctx):
    payload = {"message": {"type": "function-call", "functionCall": {"name": "nope", "parameters": {}}}}
    assert process_message(payload, ctx)[0] == 400


def test_unknown_tool_in_new_format_returns_a_result_not_an_error(ctx):
    assert "Unbekannte Funktion" in result_of(ctx, "nope", {})


def test_garbage_payloads_are_rejected(ctx):
    for payload in (None, [], "text", {}, {"message": "x"}):
        assert process_message(payload, ctx)[0] == 400


def test_status_updates_and_unknown_types_are_acknowledged(ctx):
    assert process_message({"message": {"type": "status-update"}}, ctx) == (200, {"status": "received", "type": "status-update"})
    assert process_message({"message": {"type": "speech-update"}}, ctx)[1]["status"] == "ignored"


# --- check_availability ------------------------------------------------------------

def test_check_availability_lists_free_slots(ctx):
    text = result_of(ctx, "check_availability", {"date": "2026-04-14"})
    assert "Dienstag, 14.04.2026" in text and "08:00" in text and "weitere" in text


def test_check_availability_respects_earliest_time(ctx):
    text = result_of(ctx, "check_availability", {"date": "2026-04-14", "earliest_time": "15:00"})
    assert "15:00" in text and "08:00" not in text


def test_check_availability_on_holiday_offers_alternatives(ctx):
    text = result_of(ctx, "check_availability", {"date": "2026-06-04"})
    assert "Fronleichnam" in text and "geschlossen" in text and "Nächste freie Termine" in text


def test_check_availability_on_weekend(ctx):
    assert "Ruhetag" in result_of(ctx, "check_availability", {"date": "2026-04-18"})


def test_check_availability_bad_date(ctx):
    assert "ungültig" in result_of(ctx, "check_availability", {"date": "morgen"})


# --- book_appointment ----------------------------------------------------------------

def test_booking_succeeds_and_is_logged_as_booked(ctx):
    text = result_of(ctx, "book_appointment", {**BOOK, "is_new_patient": True})
    assert "Termin erfolgreich gebucht" in text and "neuer Patient" in text and "Thomas Schneider" in text
    assert ctx.availability.store.load() == {"2026-04-14": {"09:00": "Thomas Schneider"}}
    event = log_lines(ctx)[-1]
    assert event["action"] == "book_appointment" and event["details"]["status"] == "booked"


def test_second_caller_for_the_same_slot_gets_alternatives(ctx):
    result_of(ctx, "book_appointment", BOOK)
    text = result_of(ctx, "book_appointment", {**BOOK, "patient_name": "Maria Weber"})
    assert "nicht verfügbar" in text and "Nächste freie Termine" in text
    assert ctx.availability.store.load()["2026-04-14"]["09:00"] == "Thomas Schneider"
    assert log_lines(ctx)[-1]["details"]["status"] == "unavailable"


def test_booking_on_a_holiday_is_refused(ctx):
    text = result_of(ctx, "book_appointment", {**BOOK, "date": "2026-06-04"})
    assert "Fronleichnam" in text and ctx.availability.store.load() == {}


def test_booking_outside_opening_hours_is_refused(ctx):
    assert "nicht verfügbar" in result_of(ctx, "book_appointment", {**BOOK, "time": "13:00"})


def test_booking_in_the_past_is_refused(ctx):
    assert "Vergangenheit" in result_of(ctx, "book_appointment", {**BOOK, "date": "2026-04-10"})


def test_invalid_input_is_refused_with_a_hint(ctx):
    assert "ungültig" in result_of(ctx, "book_appointment", {**BOOK, "date": "14.04.2026"})
    assert "ungültig" in result_of(ctx, "book_appointment", {**BOOK, "time": "neun Uhr"})
    assert "Name" in result_of(ctx, "book_appointment", {**BOOK, "patient_name": "  "})
    assert ctx.availability.store.load() == {}


def test_failed_attempts_are_logged_but_not_counted_as_bookings(ctx):
    from app.analytics import compute_metrics
    result_of(ctx, "book_appointment", BOOK)
    result_of(ctx, "book_appointment", {**BOOK, "patient_name": "Maria Weber"})
    metrics = compute_metrics(log_lines(ctx))
    assert metrics["tool_calls_total"] == 2 and metrics["bookings_total"] == 1


# --- transfer / summary / end of call ----------------------------------------------------

def test_transfer_call_tool(ctx):
    text = result_of(ctx, "transfer_call", {"destination": "praxis", "reason": "Rezept"})
    assert "weitergeleitet" in text and log_lines(ctx)[-1]["action"] == "transfer_call"


def test_transfer_destination_request_returns_clinic_number(ctx):
    status, body = process_message({"message": {"type": "transfer-destination-request", "destination": "praxis"}}, ctx)
    assert status == 200 and body["destination"]["number"] == "+498912345678"
    assert log_lines(ctx)[-1]["event_type"] == "transfer_destination"


def test_send_summary_with_no_channels_configured_still_confirms(ctx, monkeypatch):
    for var in ("SMTP_HOST", "NOTIFICATION_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
    assert result_of(ctx, "send_summary", {"summary": "Rückruf gewünscht", "action_required": True}) == \
        "Zusammenfassung gesendet. Aktion erforderlich."


def test_end_of_call_report_is_logged(ctx):
    payload = {"message": {"type": "end-of-call-report", "durationSeconds": 83.4, "endedReason": "customer-ended-call",
                           "analysis": {"summary": "Termin gebucht"}, "call": {"id": "c-1"}}}
    assert process_message(payload, ctx) == (200, {"status": "logged"})
    event = log_lines(ctx)[-1]
    assert event["event_type"] == "end_of_call"
    assert event["details"] == {"call_id": "c-1", "duration": 83.4, "ended_reason": "customer-ended-call",
                                "summary": "Termin gebucht", "cost": None}


# --- assistant-request (dynamic greeting) --------------------------------------------------

def test_assistant_request_without_assistant_id_explains_itself(ctx):
    status, body = process_message({"message": {"type": "assistant-request"}}, ctx)
    assert status == 200 and "VAPI_ASSISTANT_ID" in body["error"]


def test_assistant_request_returns_a_fresh_greeting(ctx):
    import dataclasses
    ctx.settings = dataclasses.replace(ctx.settings, vapi_assistant_id="asst_123")
    status, body = process_message({"message": {"type": "assistant-request"}}, ctx)
    assert body["assistantId"] == "asst_123"
    assert body["assistantOverrides"]["firstMessage"] == ctx.clinic.greeting  # frozen clock: Monday 09:00, open


# --- resilience ------------------------------------------------------------------------------

def test_tool_crash_becomes_a_polite_spoken_error(ctx, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(ctx.availability, "slots", boom)
    assert run_tool("check_availability", {"date": "2026-04-14"}, ctx) == TECH_ERROR


def test_corrupt_bookings_file_becomes_a_polite_spoken_error(ctx):
    ctx.availability.store.path.write_text("{broken")
    assert run_tool("check_availability", {"date": "2026-04-14"}, ctx) == TECH_ERROR
