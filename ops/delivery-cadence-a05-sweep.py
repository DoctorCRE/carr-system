#!/usr/bin/env python3
"""V5-A05: daily cadence sweep -- detect a missed 14-day assurance receipt and
raise the "replan on miss" escalation.

Read-only against the record layer except for the ONE write this job owns:
raise-delivery-cadence-alert, called only when cadence-status (read) reports
status "missed". A subject that is "current" is left alone.
"no_receipt_on_record" is ALSO left alone while young -- but
ops.v5_a05_cadence_status (migration 0617) now starts the interval from a
genuine server-side activation anchor when no receipt has ever been issued,
so a subject that goes a full 14-day interval with zero receipts is reported
as "missed" too (review finding 3: "no_receipt_on_record forever" meant this
sweep could never fire for a subject nobody ever sends a receipt for, which
defeated the whole point). This script does not need to know that distinction
itself -- it only ever acts on status == "missed", from whichever reason_id
produced it.

Same call path ops/timebomb-audit.py and tools/cutover-watch.py use: a
subprocess to `run.sh call <verb> '<json>'`, never the generic MCP call-verb
passthrough. That path authenticates as the partner-sponsored local machine
credential -- the V5-A05 "system" seat -- so the write below is admitted; and
cadence-status runs on the writer connection in a read-only transaction
(writerConnection, PR #1236 review round 2 item 1), because
ops.v5_a05_cadence_status is not executable by carr_reader. The server
re-reads the cadence status itself before accepting the miss and derives
that a replan needs Joe's authority (held for his morning window); this
script sends no urgency or authority flag, because none would be honoured. No seal is owed for this script or its launchd plist (decision
05e144eb, 2026-09-24, PR #1174): script and launchd edits no longer reseal.

Bounded v1 scope: ONE subject, (engineering_program, doctorcre-v5) -- the
whole v5 delivery program's assurance cadence. Nothing in this repository yet
calls record-cadence-receipt to issue a receipt for that subject; this sweep
detects and escalates a miss honestly even while no receipt has ever been
issued (status: no_receipt_on_record is reported as such, never escalated,
since it is not a broken promise). Extending this to per-slice subjects, and
wiring a receipt-issuing producer, is a follow-up, named here rather than
silently assumed.
"""

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import record_call  # noqa: E402

# Bounded v1 subject list. Extend here as producers exist to issue receipts
# for a narrower subject; do not invent subjects nothing issues a receipt for.
SUBJECTS = [
    {"subject_type": "engineering_program", "subject_ref": "doctorcre-v5"},
]


def call_verb(verb: str, args: dict) -> tuple[bool, Any]:
    """Never raises -- a verb call that fails is a finding, not a crash. Only
    an ok outcome is success: a refused write is reported, never accepted."""
    child_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""),
                 "LANG": os.environ.get("LANG", "C")}
    result = record_call.call_verb(verb, args, env=child_env, timeout=120)
    return (True, result.reply) if result.ok else (False, result.describe())


# The complete closed vocabulary cadence-status can report (mirrors
# evaluateCadenceReceipt / ops.v5_a05_cadence_status). Review finding 4: a
# fourth, unrecognized status must fail the run rather than be silently
# treated as "nothing to do" -- an unrecognized status is exactly the shape
# a contract drift between the JS classifier and the SQL mirror would take.
KNOWN_STATUSES = {"current", "missed", "no_receipt_on_record"}


def sweep_subject(subject: dict) -> dict:
    ok, status = call_verb("cadence-status", subject)
    if not ok:
        return {**subject, "outcome": "status_read_failed", "detail": status}
    # Review finding 4: a non-object payload (e.g. a bare string, null, or a
    # list) must not raise AttributeError on .get -- it is a finding about
    # this subject, not a crash that takes the whole run down.
    if not isinstance(status, dict):
        return {**subject, "outcome": "status_read_failed",
                "detail": f"cadence-status returned a non-object payload: {status!r}"}
    reported_status = status.get("status")
    if reported_status not in KNOWN_STATUSES:
        return {**subject, "outcome": "status_read_failed",
                "detail": f"cadence-status returned an unrecognized status: {reported_status!r}"}
    if reported_status != "missed":
        return {**subject, "outcome": "no_action", "status": reported_status}

    expires_at = status.get("expires_at")
    if not expires_at:
        # Review finding 4: "missed" with no expires_at would silently mint an
        # idempotency key salted with the literal string "None" -- a real
        # subsequent miss could then collide with a bogus first one, or the
        # bogus key could dedupe forever. Refuse rather than raise a bad alert.
        return {**subject, "outcome": "escalation_failed",
                "detail": "cadence-status reported missed with no expires_at"}

    idempotency_key = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"delivery-cadence-a05-sweep:{subject['subject_type']}:{subject['subject_ref']}:"
        f"{expires_at}"))
    ok, raised = call_verb("raise-delivery-cadence-alert", {
        "idempotency_key": idempotency_key,
        "reason_id": "cadence_miss_replan_required",
        "subject_type": subject["subject_type"],
        "subject_ref": subject["subject_ref"],
        "detail": f"expired {expires_at}; sweep detected {status.get('days_since_last_receipt')} days since last receipt",
    })
    if not ok:
        return {**subject, "outcome": "escalation_failed", "detail": raised}
    if not isinstance(raised, dict):
        return {**subject, "outcome": "escalation_failed",
                "detail": f"raise-delivery-cadence-alert returned a non-object payload: {raised!r}"}

    duplicate = raised.get("duplicate") is True
    minted = isinstance(raised.get("notification"), dict) and raised["notification"].get("minted") is True
    if not duplicate and not minted:
        # Review finding 4: neither a fresh mint nor a recognized duplicate is
        # not success -- e.g. notification.reason_id "no_sponsoring_partner"
        # or "severity_not_notifiable" means the escalation landed nowhere a
        # human will see it. That is a failure this job must surface, not a
        # quiet "escalated": true.
        return {**subject, "outcome": "escalation_failed",
                "detail": f"neither minted nor a recognized duplicate: {raised!r}"}
    return {**subject, "outcome": "escalated", "duplicate": duplicate,
            "routing": raised.get("routing", {}).get("routing")}


def main() -> int:
    results = [sweep_subject(subject) for subject in SUBJECTS]
    failed = [r for r in results if r["outcome"] in ("status_read_failed", "escalation_failed")]
    print(json.dumps({"schema_version": "delivery-cadence-a05-sweep.v1", "results": results}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
