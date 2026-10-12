#!/usr/bin/env python3
"""Create the Vapi tools and assistant for a clinic.

Examples:
    python scripts/setup_vapi.py --webhook-url https://abc123.ngrok.app --dry-run
    python scripts/setup_vapi.py --webhook-url https://abc123.ngrok.app
    python scripts/setup_vapi.py --webhook-url https://abc123.ngrok.app --phone-number
    python scripts/setup_vapi.py --webhook-url https://abc123.ngrok.app --dynamic-greeting \
        --config configs/examples/dental_clinic_berlin.yaml

--dry-run prints the exact payloads without calling Vapi (no API key needed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import build_assistant_config, build_tool_definitions  # noqa: E402
from app.clinic import ConfigError, load_clinic  # noqa: E402
from app.settings import get_settings, load_dotenv_file  # noqa: E402

WEBHOOK_PATH = "/api/vapi/webhook"


def create_tools(client, webhook_full_url: str, tool_defs: list[dict]) -> list[str]:
    """Create each tool on Vapi and return the IDs."""
    ids = []
    for tool_def in tool_defs:
        func = tool_def["function"]
        request = {
            "type": tool_def["type"],
            "function": {k: func[k] for k in ("name", "description", "parameters")},
            "server": {"url": webhook_full_url},
        }
        if "destinations" in tool_def:
            request["destinations"] = tool_def["destinations"]
        tool = client.tools.create(request=request)
        ids.append(tool.id)
        print(f"  Created tool {func['name']} ({tool_def['type']}) -> {tool.id}")
    return ids


def create_hmac_credential(client, secret: str) -> str | None:
    try:
        credential = client.credentials.create(
            request={
                "provider": "custom",
                "name": "clinic-webhook-hmac",
                "authenticationPlan": {
                    "type": "hmac",
                    "secretKey": secret,
                    "algorithm": "sha256",
                    "signatureHeader": "x-vapi-signature",
                },
            }
        )
        return credential.id
    except Exception as exc:
        print(f"  Warning: could not create HMAC credential ({exc}).")
        print("  Add the secret manually in the Vapi dashboard.")
        return None


def create_assistant(client, config: dict, tool_ids: list[str], credential_id: str | None) -> str:
    server = {"url": config["serverUrl"]}
    if credential_id:
        server["credentialId"] = credential_id
    assistant = client.assistants.create(
        name=config["name"],
        first_message=config["firstMessage"],
        model={**config["model"], "toolIds": tool_ids},
        voice=config["voice"],
        transcriber=config["transcriber"],
        server=server,
        max_duration_seconds=config["maxDurationSeconds"],
        end_call_message=config["endCallMessage"],
    )
    return assistant.id


def provision_phone_number(client, assistant_id: str) -> str | None:
    try:
        phone = client.phone_numbers.create(request={"provider": "vapi", "assistantId": assistant_id})
        return getattr(phone, "number", None)
    except Exception as exc:
        print(f"  Warning: could not provision a phone number ({exc}).")
        print("  Assign one in the Vapi dashboard instead.")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the Vapi assistant for a clinic")
    parser.add_argument("--webhook-url", required=True, help="Public base URL of this server, e.g. https://abc.ngrok.app")
    parser.add_argument("--config", help="Clinic YAML (default: CLINIC_CONFIG or the Munich demo clinic)")
    parser.add_argument("--webhook-secret", help="Create an HMAC credential with this secret")
    parser.add_argument("--phone-number", action="store_true", help="Provision a Vapi number bound to the assistant")
    parser.add_argument("--dynamic-greeting", action="store_true",
                        help="Do not bind a number; use Server URL + assistant-request so the greeting is built per call")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads and exit without calling Vapi")
    args = parser.parse_args()

    if args.phone_number and args.dynamic_greeting:
        parser.error("--phone-number and --dynamic-greeting are mutually exclusive")

    load_dotenv_file()
    settings = get_settings()
    config_path = Path(args.config) if args.config else settings.clinic_config
    try:
        clinic = load_clinic(config_path, settings.transfer_number)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Config error: {exc}")
        return 2

    webhook_full_url = args.webhook_url.rstrip("/") + WEBHOOK_PATH
    tool_defs = build_tool_definitions(clinic)
    assistant_config = build_assistant_config(webhook_full_url, clinic, settings)

    print(f"Clinic:   {clinic.clinic_name}")
    print(f"Webhook:  {webhook_full_url}")
    print(f"LLM:      {settings.llm_provider}/{settings.llm_model}")
    print(f"STT:      {settings.stt_provider}/{settings.stt_model} ({settings.stt_language})")

    if args.dry_run:
        print("\n--- tools ---")
        print(json.dumps(tool_defs, indent=2, ensure_ascii=False))
        print("\n--- assistant ---")
        print(json.dumps(assistant_config, indent=2, ensure_ascii=False))
        return 0

    if not settings.vapi_api_key:
        print("Error: VAPI_API_KEY is not set (put it in .env). Get one at https://dashboard.vapi.ai/")
        return 1

    from vapi import Vapi  # imported late so --dry-run works without the SDK

    client = Vapi(token=settings.vapi_api_key)
    try:
        credential_id = None
        if args.webhook_secret:
            print("\nCreating HMAC credential...")
            credential_id = create_hmac_credential(client, args.webhook_secret)

        print("\nCreating tools...")
        tool_ids = create_tools(client, webhook_full_url, tool_defs)

        print("\nCreating assistant...")
        assistant_id = create_assistant(client, assistant_config, tool_ids, credential_id)
        print(f"  Assistant ID: {assistant_id}")

        phone = None
        if args.phone_number:
            print("\nProvisioning phone number...")
            phone = provision_phone_number(client, assistant_id)
            if phone:
                print(f"  Phone number: {phone}")
    except Exception as exc:
        print(f"Setup failed: {exc}")
        return 1

    print("\nDone. Next steps:")
    print("  1. Start the server:  python -m app.main")
    if args.webhook_secret:
        print(f"  2. Put VAPI_WEBHOOK_SECRET={args.webhook_secret} in .env")
    if args.dynamic_greeting:
        print(f"  3. Put VAPI_ASSISTANT_ID={assistant_id} in .env")
        print(f"  4. In the Vapi dashboard open your phone number, clear 'Assistant' and set Server URL to {webhook_full_url}")
    elif not phone:
        print("  3. Attach a phone number to the assistant in the Vapi dashboard")
    print("  Call the number and say: 'Ich hätte gerne einen Termin.'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
