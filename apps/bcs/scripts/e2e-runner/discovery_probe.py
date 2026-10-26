#!/usr/bin/env python3
"""Task 1.3 read-only discovery probe (no fixture repair, no writes).

Runs the four sanctioned read-only observations against the profile
origin — environment.read (manifest: deployment env/version),
identity.read (authenticated Human), and bot.read for each actor
(id, kind, created_by, env, visible status) — then the formal
read-only preflight. Every request is a GET with a fresh
e2e-check request ID; the transport never follows redirects, never
sends credentials anywhere else, and never records auth values.

Run from the office network (pilot-preprod-v1.md runner host):

    source ./preprod_cookie_env.sh
    python3 ./discovery_probe.py --profile ~/.codex/bcs-e2e/preprod.json

Exit code 0 only when preflight reports DISCOVERY_PASSED.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bcs_e2e.preflight import check
from bcs_e2e.profile import load
from bcs_e2e.transport import ObservationError, ReadClient


def dump(label: str, observation) -> None:
    record = observation.evidence
    print(f"--- {label}  [http_status={record['http_status']} "
          f"elapsed_ms={record['elapsed_ms']} "
          f"request_id_echoed={record['request_id_echoed']}]")
    for key, value in sorted(observation.data.items()):
        if isinstance(value, (str, int, float, bool)) or value is None:
            print(f"  {key} = {value!r}")
        elif isinstance(value, dict):
            print(f"  {key}: keys={sorted(value.keys())[:15]}")
        elif isinstance(value, list):
            print(f"  {key}: list len={len(value)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    arguments = parser.parse_args()
    profile = load(arguments.profile)
    print("profile origin:", profile["entrypoint"]["origin"])
    client = ReadClient(profile)

    for operation in ("environment.read", "identity.read"):
        try:
            dump(operation, client.read(operation))
        except ObservationError as error:
            print(f"--- {operation} FAILED: {error}")

    for role, binding in profile["actors"].items():
        try:
            dump(f"bot.read {binding['bot_id']}",
                 client.read("bot.read", binding["bot_id"]))
        except ObservationError as error:
            print(f"--- bot.read {role} FAILED: {error}")

    result = check(profile, client)
    print("preflight status:", result["status"])
    print("ready_to_execute:", result["ready_to_execute"])
    for item in result["checks"]:
        print(f"  {item['status']} {item['id']}: {item['reason']}")
    return 0 if result["status"] == "DISCOVERY_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
