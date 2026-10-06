#!/usr/bin/env python3
"""V5-A05 live acceptance check -- READ-ONLY.

Verifies each checkable_done clause against the DEPLOYED production record
layer, once this PR is live. Never writes: it calls only list-verbs (to
confirm the verb contract is deployed and matches the module's own closed
vocabulary), cadence-status (a read verb) and read-doctrine (a read verb).
It does not call record-cadence-receipt or raise-delivery-cadence-alert.

Exit 0 means every clause it can check read-only checks out; exit 1 names
which did not. A missing receipt history ("no_receipt_on_record") is NOT a
failure -- see checkable_done 1's own note below.

Same call path ops/timebomb-audit.py and ops/delivery-cadence-a05-sweep.py use: a
subprocess to `run.sh call <verb> '<json>'`, never the generic MCP call-verb
passthrough.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import record_call  # noqa: E402

BOUND_SUBJECT = {"subject_type": "engineering_program", "subject_ref": "doctorcre-v5"}


def call_verb(verb: str, args: dict) -> tuple[bool, Any]:
    """Never raises -- a verb call that fails is a finding, not a crash. Only
    an ok outcome is success: a refused write is reported, never accepted."""
    child_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""),
                 "LANG": os.environ.get("LANG", "C")}
    result = record_call.call_verb(verb, args, env=child_env, timeout=120)
    return (True, result.reply) if result.ok else (False, result.describe())


def check_verbs_deployed() -> dict:
    """The three V5-A05 verbs are live and shaped as the module specifies."""
    ok, verbs = call_verb("list-verbs", {"filter": "cadence"})
    if not ok:
        return {"clause": "verbs_deployed", "passed": False, "detail": verbs}
    entries = verbs.get("verbs", []) if isinstance(verbs, dict) else []
    names = {v.get("name") for v in entries if isinstance(v, dict)}
    required = {"cadence-status", "record-cadence-receipt", "raise-delivery-cadence-alert"}
    missing = sorted(required - names)
    return {"clause": "verbs_deployed", "passed": not missing, "missing": missing, "observed": sorted(names)}


def check_cadence_status_contract() -> dict:
    """checkable_done 1: cadence-status answers the closed vocabulary for the
    bound v1 subject, live, with interval_days=14. status
    'no_receipt_on_record' is a PASS here -- it proves the verb, the migration
    and the closed status vocabulary are live and correct; it is not itself
    evidence that cadence was kept or missed, which is exactly what
    checkable_done 1 asks this check to prove is decidable at all."""
    ok, status = call_verb("cadence-status", BOUND_SUBJECT)
    if not ok:
        return {"clause": "cadence_status_contract", "passed": False, "detail": status}
    problems = []
    if status.get("interval_days") != 14:
        problems.append(f"interval_days={status.get('interval_days')!r}, expected 14")
    if status.get("status") not in ("current", "missed", "no_receipt_on_record"):
        problems.append(f"status={status.get('status')!r} is outside the closed vocabulary")
    if "requires_replan" not in status:
        problems.append("requires_replan missing from live response")
    return {"clause": "cadence_status_contract", "passed": not problems,
            "problems": problems, "observed_status": status.get("status")}


def check_escalation_routing_reason_vocabulary() -> dict:
    """checkable_done 2/3: the deployed raise-delivery-cadence-alert verb's
    reason_id enum is EXACTLY the closed urgent/ordinary vocabulary the pure
    module defines -- checked read-only via list-verbs' schema, never by
    calling the write verb itself."""
    ok, verbs = call_verb("list-verbs", {"filter": "raise-delivery-cadence-alert"})
    if not ok:
        return {"clause": "escalation_reason_vocabulary", "passed": False, "detail": verbs}
    entries = verbs.get("verbs", []) if isinstance(verbs, dict) else []
    entry = next((v for v in entries if v.get("name") == "raise-delivery-cadence-alert"), None)
    if entry is None:
        return {"clause": "escalation_reason_vocabulary", "passed": False,
                "detail": "raise-delivery-cadence-alert not found in list-verbs"}
    schema = entry.get("inputSchema") or entry.get("input_schema") or {}
    reason_enum = (schema.get("properties", {}).get("reason_id", {}) or {}).get("enum")
    expected = sorted([
        "security_incident", "data_loss", "outward_harm",
        "cadence_miss_replan_required", "decision_required", "delivery_blocker", "review_blocker",
    ])
    observed = sorted(reason_enum or [])
    return {"clause": "escalation_reason_vocabulary", "passed": observed == expected,
            "expected": expected, "observed": observed}


def check_catalog_binding() -> dict:
    """The deployed module's catalog entry still matches the live doctrine
    store's V5-A05 entry -- a drift check, read-only against read-doctrine."""
    ok, doc = call_verb("read-doctrine", {"document": "doctorcre-v5-astra-integration-review"})
    if not ok:
        return {"clause": "catalog_binding", "passed": False, "detail": doc}
    return {"clause": "catalog_binding", "passed": True,
            "note": "live doctrine document reachable; byte-exact digest comparison happens in "
                    "mcp-server/test/delivery-cadence-a05.v5.test.mjs, not here (this script is read-only "
                    "and does not embed a copy of the catalog entry to compare against)."}


def main() -> int:
    checks = [
        check_verbs_deployed(),
        check_cadence_status_contract(),
        check_escalation_routing_reason_vocabulary(),
        check_catalog_binding(),
    ]
    failed = [c for c in checks if not c.get("passed")]
    print(json.dumps({"schema_version": "delivery-cadence-a05-live-acceptance-check.v1",
                       "read_only": True, "checks": checks}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
