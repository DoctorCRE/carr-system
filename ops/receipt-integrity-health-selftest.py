#!/usr/bin/env python3
"""Selftest for ops/receipt-integrity-health.py's interpretation of the
read-jev-call-receipt-integrity answer (no network, no database). Each case
replays what `./run.sh call` prints through the check's injectable runner."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("receipt_integrity_health", HERE / "receipt-integrity-health.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def audit(orphans: int = 0, enabled: bool = True) -> str:
    return json.dumps({
        "ok": True,
        "receipts_total": 5,
        "receipts_without_tool_call": {"count": orphans, "receipt_ids": [f"r{i}" for i in range(orphans)]},
        "trigger_enabled": enabled,
        "triggers": [
            {"name": "jev_call_receipt_append_only", "tgenabled": "O" if enabled else "D", "enabled": enabled},
            {"name": "jev_call_receipt_no_truncate", "tgenabled": "O", "enabled": True},
        ],
        "checked_at": "2026-09-24T12:00:00+00:00",
    })


CASES = [
    ("clean audit is OK", (0, audit(), ""), "OK"),
    ("an uncredited receipt fails", (0, audit(orphans=2), ""), "FAIL"),
    ("a disabled trigger fails", (0, audit(enabled=False), ""), "FAIL"),
    ("verb not deployed is a skip", (1, "", 'TOOL ERROR {"error": "unknown_tool", "name": "x"}'), "SKIP"),
    ("any other error fails", (1, "", "could not reach the deployed Worker"), "FAIL"),
    ("garbage output fails", (0, "not json", ""), "FAIL"),
    ("a refusal on exit 0 fails", (0, json.dumps({"ok": False, "error": "forbidden"}), ""), "FAIL"),
]


def replay(rc: int, out: str, err: str):
    return lambda argv, **_kw: subprocess.CompletedProcess(argv, rc, out, err)


def main() -> int:
    failures = 0
    for label, (rc, out, err), want in CASES:
        got, message = mod.check(replay(rc, out, err))
        ok = got == want
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {label}: {got} ({message})")
    disabled = mod.check(replay(0, audit(enabled=False), ""))[1]
    ok = "jev_call_receipt_append_only" in disabled
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  a disabled trigger is named: {disabled}")
    print(f"receipt-integrity-health-selftest: {len(CASES) + 1 - failures}/{len(CASES) + 1} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
