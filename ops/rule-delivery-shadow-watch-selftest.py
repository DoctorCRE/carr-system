#!/usr/bin/env python3
"""Selftest for ops/rule-delivery-shadow-watch.py.

The watch's whole job is to tell a quiet week apart from a stopped one, so the
cases below are mostly about silence: an empty log, a stale log, a log full of
gate errors. Each is a state that reads as "no problem" if nothing prints it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "rule_delivery_shadow_watch", REPO / "ops" / "rule-delivery-shadow-watch.py")
assert SPEC and SPEC.loader
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(**kw):
    base = {"ts": stamp(1), "hook": "rule-pack-drift-gate", "session": "s1",
            "mode": "shadow", "needed": [], "loaded": [], "missing": [],
            "triggers": {}, "would_omit_count": 0, "missed_rules": []}
    base.update(kw)
    return base


def write(rows) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in rows:
        handle.write(json.dumps(r) + "\n")
    handle.close()
    return Path(handle.name)


# ── an empty log is a finding, not a clean week ─────────────────────────────
empty = watch.summarize([], now=NOW)
check("an empty log is stale", empty["stale"] is True)
check("an empty log reports no newest row", empty["newest"] is None)
check("an empty log has no misses to hide", empty["miss_count"] == 0)

# ── a stale log is a finding even when every row in it was clean ────────────
stale = watch.summarize([row(ts=stamp(200), needed=["engineering-git"])], now=NOW)
check("a log whose newest row is older than the window is stale", stale["stale"] is True)
check("and its age is reported in hours", round(stale["age_hours"]) == 200,
      str(stale["age_hours"]))

fresh = watch.summarize([row(ts=stamp(3), needed=["engineering-git"])], now=NOW)
check("a fresh log is not stale", fresh["stale"] is False)
with_receipt = watch.summarize([
    row(ts=stamp(3), needed=["engineering-git"]),
    {"record_type": "epoch", "ts": stamp(1), "record_id": "a" * 64},
], now=NOW)
check("ledger receipts are not counted as observed turns", with_receipt["turns"] == 1,
      str(with_receipt))

# ── the miss is the number the enforcement flip turns on ────────────────────
missed = watch.summarize([
    row(ts=stamp(2), needed=["engineering-git"], missing=["engineering-git"],
        missed_rules=["4a53ff82", "308ef1de"], would_omit_count=9),
    row(ts=stamp(1), needed=["client-deal"], loaded=["client-deal"]),
], now=NOW)
check("a miss is counted", missed["miss_count"] == 1, str(missed["miss_count"]))
check("the rules behind the miss are carried, not just the count",
      missed["misses"][0]["rules"] == ["4a53ff82", "308ef1de"])
check("turns are counted whether or not they missed", missed["turns"] == 2)
check("packs the work implied are tallied",
      missed["packs_seen"] == {"engineering-git": 1, "client-deal": 1},
      str(missed["packs_seen"]))

# ── a gate that failed open did not measure that turn ───────────────────────
errored = watch.summarize([row(ts=stamp(1), error="KeyError"),
                           row(ts=stamp(1))], now=NOW)
check("turns the gate could not measure are counted separately",
      errored["gate_errors"] == 1, str(errored["gate_errors"]))

# ── unreadable lines are counted rather than silently skipped ───────────────
path = write([row(ts=stamp(1))])
with path.open("a") as handle:
    handle.write("this is not json\n")
    handle.write("[1,2,3]\n")
rows, unreadable = watch.read_log(path)
check("a corrupt line does not stop the read", len(rows) == 1)
check("and it is counted", unreadable == 2, str(unreadable))
path.unlink()

missing_file = watch.read_log(Path("/nonexistent/shadow.jsonl"))
check("a missing log reads as empty rather than raising", missing_file == ([], 0))

# ── the printed report says the thing a reader must not miss ────────────────
import io, contextlib  # noqa: E402

def rendered(summary, eligibility=None):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        watch.report(summary, eligibility)
    return buffer.getvalue()

check("an empty log prints that nothing was measured",
      "NO SHADOW OBSERVATIONS" in rendered(empty), rendered(empty))
check("a stale log prints that the comparison stopped",
      "STALE" in rendered(stale), rendered(stale))
check("a miss prints that enforcement stays off",
      "MISS" in rendered(missed) and "does not flip on" in rendered(missed),
      rendered(missed))
check("a clean fresh week prints the turn count and zero misses",
      "0 misses" in rendered(fresh), rendered(fresh))
check("and it does not claim to have counted turns it never saw",
      "with a pack signal" in rendered(fresh), rendered(fresh))

blocked = {"eligible": False, "reasons": ["one unresolved finding"],
           "open": [{"kind": "miss", "event_id": "a" * 64}], "closed": []}
check("nightly report surfaces eligibility and missing ownership/remedy",
      "ENFORCEMENT BLOCKED" in rendered(missed)
      and "owner=UNASSIGNED" in rendered(missed, blocked),
      rendered(missed, blocked))

# ── delivery scope is part of the production acceptance signal ─────────────
audit = watch.load_audit()
clean_counts = {
    "total": 4, "untagged": 0, "orphaned": 0, "layer0": 1,
    "layer0_shared": 1, "layer0_shared_cap": 35, "control": 1, "pack": 2,
    "packs": 1, "wildcarded": 0, "packless": 0, "emptypack": 0,
    "scope_mismatch": 0, "mode": "shadow",
}
check("a scope-clean delivery audit can pass", audit.failing(clean_counts) is False)
bad_scope = {**clean_counts, "scope_mismatch": 1}
check("one personal-scope mismatch fails the delivery audit",
      audit.failing(bad_scope) is True)
check("the rendered audit names the scope mismatch",
      "scope_mismatch=1" in audit.render(bad_scope), audit.render(bad_scope))

if FAILURES:
    print("rule-delivery-shadow-watch-selftest: FAIL", file=sys.stderr)
    for line in FAILURES:
        print(f"  {line}", file=sys.stderr)
    raise SystemExit(1)
print("rule-delivery-shadow-watch-selftest: 22 cases passed")
