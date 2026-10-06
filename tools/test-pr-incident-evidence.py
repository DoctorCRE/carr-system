#!/usr/bin/env python3
"""Hermetic tests for ops-record.py's pr-evidence pure logic (WR-000019 slice
S1). No database, no gh binary, no network — extract_incident_refs and
pr_evidence_decision are pure functions exercised directly, the same way
resolve_preconditions and sweep_decision are already proven apart from the
connection that executes them.

Named with a HYPHEN (tools/test-*.py), not an underscore, because that is the
exact glob ops/ci.sh's selftest loop discovers (`for t in ops/*-selftest.py
tools/test-*.py`) — an underscore-named file in this directory silently never
runs in CI, which is not the mistake this file gets to make."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ops_record", ROOT / "tools" / "ops-record.py")
assert spec and spec.loader
ops_record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops_record)


def test_extracts_one_ref_from_title():
    assert ops_record.extract_incident_refs("Fix INC-20260822-02 approve-rule", None) == ["INC-20260822-02"]


def test_extracts_from_title_and_body_deduplicated_in_first_seen_order():
    refs = ops_record.extract_incident_refs(
        "Repair INC-20260825-07 and INC-20260826-01",
        "Also closes INC-20260825-07 again and mentions INC-20260822-02.",
    )
    assert refs == ["INC-20260825-07", "INC-20260826-01", "INC-20260822-02"]


def test_no_refs_is_an_empty_list_not_an_error():
    assert ops_record.extract_incident_refs("just an ordinary PR", "") == []
    assert ops_record.extract_incident_refs(None, None) == []


def test_ignores_lookalike_text_that_is_not_the_exact_ref_shape():
    # INC- alone, a short day, and a bare word must not match — the format is
    # fixed by mcp-server/src/incident.js's own ref generator: INC-<8-digit
    # day>-<seq>.
    refs = ops_record.extract_incident_refs("INC-2026-01 is not a ref; neither is INCIDENT-20260822-02", None)
    assert refs == []


def test_three_or_more_digit_sequence_still_matches():
    # The generator pads to 2 digits today but nothing stops a busy day reaching
    # 100; the regex must not go stale the day that happens.
    assert ops_record.extract_incident_refs("INC-20260822-123") == ["INC-20260822-123"]


def test_decision_attaches_when_open_and_bare():
    action, evidence = ops_record.pr_evidence_decision(None, "detected", 716)
    assert (action, evidence) == ("attach", "pr:716")


def test_decision_never_overwrites_existing_evidence():
    action, evidence = ops_record.pr_evidence_decision("ops.run:already-here", "monitoring", 716)
    assert action == "skip_evidenced"
    assert evidence is None


def test_decision_never_touches_a_closed_incident():
    for state in ("resolved", "reviewed"):
        action, evidence = ops_record.pr_evidence_decision(None, state, 716)
        assert action == "skip_closed", state
        assert evidence is None


def test_decision_prefers_closed_over_evidenced_when_somehow_both():
    # A resolved incident with evidence is still "nothing to do here", and the
    # closed reading is the more informative one to print.
    action, _ = ops_record.pr_evidence_decision("ops.run:x", "resolved", 716)
    assert action == "skip_closed"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"pr-incident-evidence: {len(tests)} tests passed")
    sys.exit(0)
