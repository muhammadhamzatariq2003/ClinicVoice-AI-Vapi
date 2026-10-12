#!/usr/bin/env python3
"""Replay the scripted caller scenarios.

Default (offline, free, deterministic): sends the webhook traffic each scenario should produce
into this server's own handlers and checks the replies. No Vapi account or network needed.

    python scripts/run_scenarios.py              # run all
    python scripts/run_scenarios.py --scenario 1
    python scripts/run_scenarios.py --list

Live (costs money, needs Vapi): place a real outbound call that plays one scenario.

    python scripts/run_scenarios.py --live --scenario 1 \
        --assistant-id <id> --phone-number-id <id> --to +49151...

Offline mode proves the server side. It cannot judge what the LLM says; only a real call can.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import build_system_prompt  # noqa: E402
from app.clinic import load_clinic  # noqa: E402
from app.handlers import AppContext, create_context, process_message  # noqa: E402
from app.settings import get_settings, load_dotenv_file  # noqa: E402


def load_scenarios() -> list[dict]:
    with open(ROOT / "configs" / "scenarios.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["scenarios"]


def next_workday(ctx: AppContext, at: str = "09:00") -> str:
    """First future date on which the clinic has a free slot at ``at``."""
    today = ctx.availability.now().date()
    for offset in range(1, 60):
        iso = (today + timedelta(days=offset)).isoformat()
        if at in ctx.availability.slots(iso):
            return iso
    raise RuntimeError("No open day found in the next 60 days; check the clinic's office_hours")


def resolve(value, ctx: AppContext):
    """Replace @placeholders in scenario data."""
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if value == "@next_workday":
        return next_workday(ctx)
    if value == "@opening_hours":
        return ctx.clinic.opening_hours_text
    if value == "@transfer_number":
        return ctx.clinic.transfer_number
    return value


def tool_call_payload(name: str, arguments: dict) -> dict:
    return {"message": {"type": "tool-calls",
                        "toolCallList": [{"id": "call_demo", "name": name, "arguments": arguments}]}}


def run_offline(scenario: dict, ctx: AppContext) -> tuple[bool, str]:
    sim = resolve(scenario["simulate"], ctx)

    if "tool" in sim:
        status, body = process_message(tool_call_payload(sim["tool"], sim["arguments"]), ctx)
        result = body["results"][0]["result"] if status == 200 else str(body)
        ok = status == 200 and sim["expect_contains"] in result
        return ok, result

    if sim.get("transfer_destination"):
        status, body = process_message(
            {"message": {"type": "transfer-destination-request", "destination": "praxis"}}, ctx)
        number = body.get("destination", {}).get("number")
        return status == 200 and number == sim["expect_number"], f"transfer to {number}"

    if "prompt_contains" in sim:
        prompt = build_system_prompt(ctx.clinic)
        missing = [s for s in sim["prompt_contains"] if s not in prompt]
        return not missing, "system prompt contains all required rules" if not missing else f"prompt is missing {missing}"

    return False, "scenario has no runnable 'simulate' block"


def run_live(scenario: dict, args) -> int:
    settings = get_settings()
    if not settings.vapi_api_key:
        print("VAPI_API_KEY is not set")
        return 1
    if not (args.assistant_id and args.phone_number_id and args.to):
        print("--live needs --assistant-id, --phone-number-id and --to")
        return 1
    from vapi import Vapi

    client = Vapi(token=settings.vapi_api_key)
    call = client.calls.create(
        assistant_id=args.assistant_id,
        phone_number_id=args.phone_number_id,
        customer={"number": args.to, "name": scenario["caller"]},
    )
    print(f"Call started: {call.id}. Answer the phone and say, in German:")
    for hint in scenario["dialog_hints"]:
        print(f"  > {hint}")
    print(f"Expected outcome: {scenario['expected_action']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay clinic call scenarios")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scenario", type=int)
    parser.add_argument("--config", help="Clinic YAML to use")
    parser.add_argument("--live", action="store_true", help="Place a real call via Vapi (costs money)")
    parser.add_argument("--assistant-id")
    parser.add_argument("--phone-number-id")
    parser.add_argument("--to", help="Your phone number in E.164 format")
    args = parser.parse_args()

    scenarios = load_scenarios()
    if args.list:
        for s in scenarios:
            print(f"{s['id']}. {s['name']}: {s['description']} -> {s['expected_action']}")
        return 0

    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            print(f"No scenario with id {args.scenario}")
            return 1

    load_dotenv_file()
    if args.live:
        return run_live(scenarios[0], args)

    settings = get_settings()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        settings = dataclasses.replace(settings, data_dir=Path(tmp))
        clinic = load_clinic(args.config or settings.clinic_config, settings.transfer_number)
        ctx = create_context(settings, clinic=clinic)
        ctx.log_file = Path(tmp) / "call_log.jsonl"
        print(f"Clinic: {clinic.clinic_name} | now: {ctx.availability.now():%A %Y-%m-%d %H:%M} ({clinic.schedule.timezone})\n")
        for s in scenarios:
            ok, detail = run_offline(s, ctx)
            failures += 0 if ok else 1
            print(f"[{'PASS' if ok else 'FAIL'}] {s['id']}. {s['name']}\n       {detail}")
    print(f"\n{len(scenarios) - failures}/{len(scenarios)} scenarios passed (offline replay)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
