# Webhook & HTTP reference

Everything the server exposes, with the payload shapes it understands.

## Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/api/vapi/webhook` | Receives every Vapi server message | `VAPI_WEBHOOK_SECRET` (if set) |
| `GET` | `/api/analytics?from=YYYY-MM-DD&to=YYYY-MM-DD` | Call metrics as JSON | `DASHBOARD_TOKEN` (if set) |
| `GET` | `/dashboard` | Analytics page (Chart.js) | page is open, data needs the token |
| `GET` | `/health` | Liveness probe (used by Docker and Fly) | none |
| `GET` | `/docs` | Interactive OpenAPI docs | none |

## Authenticating Vapi → server

Set `VAPI_WEBHOOK_SECRET` and the webhook accepts a request only if it carries either header:

* `x-vapi-secret: <secret>`: the secret itself (Vapi "server secret" setting)
* `x-vapi-signature: <hex>`: HMAC-SHA256 of the raw body keyed with the secret
  (create it with `python scripts/setup_vapi.py --webhook-secret <secret>`)

Anything else gets `401`. With no secret set the webhook is open, and the server logs a warning at start-up.

## Message types

| `message.type` | What the server does |
|---|---|
| `tool-calls` | Runs each tool in `toolCallList` and answers `{"results":[{"toolCallId","result"}]}` |
| `function-call` | Legacy single-call format; answers `{"result": "..."}` |
| `transfer-destination-request` | Answers with the clinic's `transfer_number` |
| `assistant-request` | Answers with `assistantId` + a freshly built `firstMessage` (needs `VAPI_ASSISTANT_ID`) |
| `end-of-call-report` | Logs duration, end reason, summary and call id |
| `status-update` | Acknowledged, nothing else |
| anything else | `{"status":"ignored"}` |

### `tool-calls` example

```json
{ "message": { "type": "tool-calls",
    "toolCallList": [ { "id": "call_1", "name": "check_availability",
                        "arguments": { "date": "2026-04-14", "earliest_time": "09:00" } } ] } }
```

Reply (the text is written for the LLM, which then speaks it):

```json
{ "results": [ { "toolCallId": "call_1",
    "result": "Freie Termine am Dienstag, 14.04.2026: 09:00, 09:30, 10:00, 10:30, 11:00, 11:30 Uhr. Es gibt weitere freie Zeiten." } ] }
```

`arguments` may be an object or a JSON string; both work. A tool that crashes never produces an HTTP error:
the model receives a polite "technical problem, try again later" sentence instead, so the caller is not left in silence.

## Tools

| Tool | Arguments | Result |
|---|---|---|
| `check_availability` | `date`, optional `earliest_time` | Up to six free slots, or why the day is closed plus the next three free slots |
| `book_appointment` | `patient_name`, `date`, `time`, `reason`, optional `is_new_patient` | Confirmation, or the reason it failed plus alternatives |
| `transfer_call` | `destination`, optional `reason` | Text for the model; the actual transfer is Vapi's native `transferCall` tool |
| `send_summary` | `summary`, optional `caller_name`, `action_required` | Sends email and/or webhook notification |

`book_appointment` refuses: unknown or malformed date/time, past times, holidays, closed days, times outside opening
hours, times off the slot grid, and slots that are already taken. The check and the write happen under one lock.

## Call log (`data/call_log.jsonl`)

One JSON object per line:

```json
{"timestamp":"2026-04-14T08:02:11+00:00","event_type":"tool_call","caller_name":"Thomas Schneider",
 "action":"book_appointment","details":{"date":"2026-04-14","time":"09:30","reason":"Rückenschmerzen",
 "is_new_patient":true,"status":"booked"}}
```

`event_type` is `tool_call`, `transfer_destination` or `end_of_call`. `status` on bookings is
`booked`, `unavailable` or `invalid`; only `booked` counts towards the booking rate.

## Analytics response

```json
{ "calls_total": 12, "calls_today": 3, "avg_duration_sec": 74.2,
  "bookings_total": 5, "booking_rate": 41.7, "tool_calls_total": 17,
  "top_reasons": [["Rückenschmerzen", 2], ["Impfung", 1]],
  "ended_reasons": [["customer-ended-call", 10]],
  "calls_per_day": { "2026-04-13": 4, "2026-04-14": 8 } }
```

Days are grouped in the clinic's timezone, not UTC.
