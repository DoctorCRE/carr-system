#!/usr/bin/env python3
"""Health line for the server-side Jev call log's tamper audit.

The Jev call receipt store (migrations/0587) is DETECTABLE, NOT PREVENTED
against its own database owner: the model's OS user can read the owner
credential, so it can insert, backdate or delete receipt rows or disable the
append-only trigger. This check is the detection half. It asks the deployed
Worker for `read-jev-call-receipt-integrity` through the ordinary local verb
door (`./run.sh call`, no credential of its own) and turns the answer into one
health line:

  OK    every receipt has its ask-jev tool_call row and the triggers are on
  FAIL  a receipt has no matching tool_call row, or a trigger is not enabled,
        or the audit could not be read at all
  SKIP  the Worker does not serve the verb yet (unknown_tool: not deployed)

A trigger disabled and re-enabled between two runs is not seen.

The first line of stdout is the line ./run.sh health prints; the exit status
is 0 for OK/SKIP and 1 for FAIL.
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.record_call import Runner, VerbResult, call_verb  # noqa: E402

VERB = "read-jev-call-receipt-integrity"


def interpret(result: VerbResult) -> tuple[str, str]:
    """Map one verb outcome to (status, message); status is OK/FAIL/SKIP."""
    if result.error == "unknown_tool":
        return "SKIP", f"{VERB} is not served by the Worker yet (not deployed)"
    if not result.ok:
        return "FAIL", "audit unreadable: " + (result.detail[:160] or result.kind)
    audit = result.reply
    if not isinstance(audit, dict) or not isinstance(audit.get("receipts_without_tool_call"), dict):
        return "FAIL", "audit returned an unexpected shape"
    orphans = audit["receipts_without_tool_call"]
    count = orphans.get("count")
    total = audit.get("receipts_total")
    problems: list[str] = []
    if not isinstance(count, int) or count != 0:
        ids = orphans.get("receipt_ids") or []
        problems.append(f"{count} receipt(s) without a matching ask-jev tool_call row "
                        f"(e.g. {', '.join(str(i) for i in ids[:3])})")
    if audit.get("trigger_enabled") is not True:
        off = [t.get("name") for t in audit.get("triggers") or []
               if isinstance(t, dict) and t.get("enabled") is not True]
        problems.append("append-only trigger not enabled: " + (", ".join(str(n) for n in off) or "unknown"))
    if problems:
        return "FAIL", "; ".join(problems)
    return "OK", f"{total} receipt(s), all credited by tool_call; append-only triggers enabled"


def check(runner: Runner = subprocess.run) -> tuple[str, str]:
    return interpret(call_verb(VERB, {}, timeout=90, runner=runner))


def main() -> int:
    status, message = check()
    if status == "SKIP":
        print(f"SKIP: {message}")
        return 0
    print(f"{status} jev receipts — {message}")
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
