#!/usr/bin/env python3
"""Hermetic checks for the read-only audit/system collector registry."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.control_plane_collectors_audit import AUDIT_BUILDERS, SQL, audit_evidence_envelope, collect_audit_facts
from lib.control_plane_inputs import InputUnavailable, NoEligibleRecords


class Reader:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def fetch_all(self, key, sql, params=()):
        assert sql == SQL[key]
        self.calls.append((key, params))
        value = self.rows.get((key, params), self.rows.get(key, []))
        if isinstance(value, Exception): raise value
        return value


def check(label, ok):
    if not ok: raise AssertionError(label)
    print(f"ok: {label}")


def main():
    context = {"mode":"shadow", "scheduled_for":datetime(2026, 8, 16, 14, tzinfo=timezone.utc)}
    base = {
        "capability": [{"id": "wr-1", "state": "verification", "project_context": {"scope": "single", "requested_mutation": "none"}, "session_id": "s-1", "session_state": "verification"}],
        "release": [{"release_from": "1.0", "release_to": "1.1", "released_at": "2026-08-16T00:00:00Z", "source_ref": "release:1.1"}],
        "release_receipt": [{"created_at": "2026-08-01T00:00:00Z"}],
        "health_evidence": [{"evidence_class": "live", "source_ref": "health:live"}, {"evidence_class": "registry", "source_ref": "health:registry"}, {"evidence_class": "artifact", "source_ref": "health:artifact"}],
        "loops": [{"id": "loop-1", "owner": "system", "state": "actionable", "counterparty_ref": None, "event_blocker_ref": None}],
        "doctrine_due": [{"id": "section-1", "slug": "playbook", "review_after": "2026-08-01T00:00:00Z"}],
        "doctrine_failures": [{"source_ref": "gate:failed"}],
        "system_candidates": [{"subject_ref": "rule:1", "measurement": "stale"}],
        "monthly_receipt": [],
        "sweep_receipt": [{"receipt_ref": "job:sweep", "created_at": "2026-08-15T00:00:00Z"}],
    }
    for builder in sorted(AUDIT_BUILDERS):
        reader = Reader(base)
        facts = collect_audit_facts(builder, reader, **context)
        check(f"{builder} returns canonical facts", isinstance(facts, dict) and bool(facts))
        envelope = audit_evidence_envelope(builder, "fixture", reader, **context)
        check(f"{builder} preserves canonical provenance", envelope["source_kind"] == "canonical_db" and not envelope["values"].get("payload"))
    receipt_reader = Reader(base)
    collect_audit_facts("system-health.monthly-evidence", receipt_reader, **context)
    check("receipt state is read from immutable receipt query",
          any(key == "monthly_receipt" and params[:2] == ("health-audit-monthly", "shadow")
              for key, params in receipt_reader.calls))
    first_audit = collect_audit_facts("cc-release-diff", Reader({**base, "release_receipt": []}), **context)
    check("first release audit compares against an explicit epoch, not invented receipt state",
          first_audit["release"]["last_accepted_at"].startswith("1970-01-01"))
    playbook = collect_audit_facts("doctrine.review-due", Reader(base), **context)
    check("playbook keeps own monthly absence separate from sweep receipt presence",
          playbook["monthly_receipt_state"] == "absent" and playbook["sweep_receipt_state"] == "present")
    try: collect_audit_facts("system-health.monthly-evidence", Reader({**base, "health_evidence": base["health_evidence"][:2]}), **context)
    except InputUnavailable: refused = True
    else: refused = False
    check("missing evidence class refuses", refused)
    try: collect_audit_facts("system.prune-candidates", Reader({**base, "system_candidates": [{"subject_ref": "x", "measurement": "unmeasured"}]}), **context)
    except InputUnavailable: refused = True
    else: refused = False
    check("unmeasured destructive candidate refuses", refused)
    # An empty actionable-loop queue is the normal state on most weekdays: it
    # is "not needed right now" and must skip, never dead-letter (88e9b5eb).
    try: collect_audit_facts("loops.next-actionable", Reader({**base, "loops": []}), **context)
    except NoEligibleRecords: outcome = "skip"
    except InputUnavailable: outcome = "refused"
    else: outcome = "returned"
    check("empty actionable-loop queue is a clean no-work skip", outcome == "skip")
    try: collect_audit_facts("not-registered", Reader(base), **context)
    except InputUnavailable: refused = True
    else: refused = False
    check("unknown builder refuses", refused)
    return 0


if __name__ == "__main__": raise SystemExit(main())
