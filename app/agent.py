"""Builds everything Vapi needs: system prompt, greeting, tool schemas, assistant payload.

The caller-facing text stays German on purpose (the agent answers German-speaking patients).
Code, config keys and documentation are English.
"""

from __future__ import annotations

from datetime import datetime

from app.availability import closed_reason, is_outside_hours
from app.clinic import ClinicConfig
from app.settings import Settings

MAX_CALL_SECONDS = 300
VAPI_NAME_LIMIT = 40  # Vapi rejects assistant names longer than this


def _vapi_now_template(tz_name: str) -> str:
    """Liquid expression Vapi renders at call time, so the model always knows today's date."""
    return '{{"now" | date: "%A, %d.%m.%Y, %H:%M", "' + tz_name + '"}}'


def build_system_prompt(clinic: ClinicConfig) -> str:
    services = ", ".join(clinic.services)
    emergency = clinic.emergency_number
    return (
        f"Du bist {clinic.assistant_name}, die KI-Assistentin der {clinic.clinic_name}, "
        f"{clinic.doctor_name}, {clinic.doctor_title}.\n\n"
        f"Aktuelles Datum und Uhrzeit (Zeitzone {clinic.schedule.timezone}): "
        f"{_vapi_now_template(clinic.schedule.timezone)} Uhr. "
        f"Rechne relative Angaben wie \"morgen\" oder \"nächsten Dienstag\" immer von diesem Datum aus.\n\n"
        "Deine Aufgaben:\n"
        "1. Begrüße den Anrufer freundlich und frage nach dem Anliegen\n"
        "2. Finde heraus: Neuer oder bestehender Patient?\n"
        "3. Erfasse: Name, Geburtsdatum (bei neuen Patienten), Anliegen/Beschwerden\n"
        "4. Bei Terminwunsch: Rufe zuerst check_availability auf und schlage nur Zeiten vor, "
        "die das Tool zurückgibt. Erfinde niemals freie Termine. Buche danach mit book_appointment\n"
        f"5. Bei Notfall (Brustschmerzen, Atemnot, starke Blutung, Bewusstlosigkeit): "
        f"Sofort empfehlen den Notruf {emergency} anzurufen. "
        "Keinen Termin buchen, keine Weiterleitung.\n"
        "6. Bei Rezepten/Überweisungen: An die Praxis weiterleiten mit transfer_call\n"
        "7. Bei allgemeinen Fragen (Öffnungszeiten, Adresse): Direkt beantworten\n"
        "8. Fasse am Ende zusammen was vereinbart wurde und sende bei Bedarf eine Zusammenfassung "
        "mit send_summary\n\n"
        "Wichtige Informationen:\n"
        f"- Öffnungszeiten: {clinic.opening_hours_text}\n"
        f"- Adresse: {clinic.address}\n"
        f"- Telefon Praxis: {clinic.phone}\n"
        f"- Notfallnummer: {emergency}\n"
        f"- Leistungen: {services}\n\n"
        "Regeln: Gib keine medizinischen Diagnosen oder Behandlungsempfehlungen. "
        "Übergib Daten an Tools immer als Datum YYYY-MM-DD und Uhrzeit HH:MM.\n"
        "Ton: Freundlich, professionell, empathisch. Sprich immer Deutsch. "
        "Halte dich kurz und präzise. Frage immer nur eine Sache auf einmal."
    )


def build_first_message(clinic: ClinicConfig, now: datetime | None = None) -> str:
    """Holiday / closed-day greeting, outside-hours greeting, or the clinic's normal greeting."""
    now = now or datetime.now(clinic.schedule.tz)
    intro = f"{clinic.clinic_name}, hier spricht {clinic.assistant_name}, die digitale Assistentin. "

    reason = closed_reason(clinic.schedule, now.date())
    if reason and reason != "Ruhetag":
        return (
            f"{intro}Die Praxis ist heute wegen {reason} geschlossen. "
            f"In Notfällen rufen Sie bitte die {clinic.emergency_number} an. "
            "Kann ich Ihnen anderweitig helfen?"
        )
    if is_outside_hours(clinic.schedule, now):
        return (
            f"{intro}Die Praxis ist derzeit geschlossen. "
            f"Unsere Öffnungszeiten sind {clinic.opening_hours_text}. "
            f"In Notfällen rufen Sie bitte die {clinic.emergency_number} an. "
            "Kann ich Ihnen anderweitig helfen?"
        )
    return clinic.greeting


def _param(kind: str, description: str) -> dict:
    return {"type": kind, "description": description}


def build_tool_definitions(clinic: ClinicConfig) -> list[dict]:
    """Tool schemas as Vapi expects them (``function`` tools plus a native ``transferCall``)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": f"Prüft freie Termine in der {clinic.clinic_name} an einem bestimmten Tag",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": _param("string", "Datum im Format YYYY-MM-DD"),
                        "earliest_time": _param("string", "Optional: frühestmögliche Uhrzeit im Format HH:MM"),
                    },
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "book_appointment",
                "description": f"Bucht einen Termin für den Patienten in der {clinic.clinic_name}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patient_name": _param("string", "Vollständiger Name des Patienten"),
                        "date": _param("string", "Gewünschtes Datum im Format YYYY-MM-DD"),
                        "time": _param("string", "Gewünschte Uhrzeit im Format HH:MM"),
                        "reason": _param("string", "Grund des Termins / Beschwerden"),
                        "is_new_patient": _param("boolean", "Ob es ein neuer Patient ist"),
                    },
                    "required": ["patient_name", "date", "time", "reason"],
                },
            },
        },
        {
            "type": "transferCall",
            "function": {
                "name": "transfer_call",
                "description": "Leitet den Anruf an die Praxis weiter (z.B. für Rezepte, Überweisungen)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "destination": {**_param("string", "Zielnummer oder Abteilung (z.B. 'praxis')"), "default": "praxis"},
                        "reason": _param("string", "Grund der Weiterleitung"),
                    },
                    "required": ["destination"],
                },
            },
            "destinations": [
                {
                    "type": "number",
                    "number": clinic.transfer_number,
                    "message": "Ich verbinde Sie jetzt mit der Praxis. Einen Moment bitte.",
                }
            ],
        },
        {
            "type": "function",
            "function": {
                "name": "send_summary",
                "description": "Sendet eine Zusammenfassung des Anrufs an das Praxisteam",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": _param("string", "Zusammenfassung des Anrufs"),
                        "caller_name": _param("string", "Name des Anrufers"),
                        "action_required": _param("boolean", "Ob eine Aktion vom Team erforderlich ist"),
                    },
                    "required": ["summary"],
                },
            },
        },
    ]


def build_model_config(settings: Settings, system_prompt: str) -> dict:
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "temperature": 0.3,
        "maxTokens": 300,
        "messages": [{"role": "system", "content": system_prompt}],
    }


def build_voice_config(settings: Settings) -> dict:
    voice = {
        "provider": settings.voice_provider,
        "voiceId": settings.voice_id,
        "stability": 0.7,
        "similarityBoost": 0.8,
        "language": "de",
    }
    if settings.voice_model:
        voice["model"] = settings.voice_model
    return voice


def build_transcriber_config(settings: Settings) -> dict:
    """Speech-to-text must be told the language, otherwise Vapi's default transcribes English."""
    return {
        "provider": settings.stt_provider,
        "model": settings.stt_model,
        "language": settings.stt_language,
    }


def build_assistant_config(
    webhook_url: str,
    clinic: ClinicConfig,
    settings: Settings,
    now: datetime | None = None,
) -> dict:
    """Full assistant payload. ``webhook_url`` is the complete URL, e.g. https://host/api/vapi/webhook."""
    return {
        "name": f"{clinic.assistant_name} - {clinic.clinic_name}"[:VAPI_NAME_LIMIT],
        "firstMessage": build_first_message(clinic, now),
        "model": build_model_config(settings, build_system_prompt(clinic)),
        "voice": build_voice_config(settings),
        "transcriber": build_transcriber_config(settings),
        "serverUrl": webhook_url,
        "maxDurationSeconds": MAX_CALL_SECONDS,
        "endCallMessage": clinic.farewell,
    }
