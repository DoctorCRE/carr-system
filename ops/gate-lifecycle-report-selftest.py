#!/usr/bin/env python3
"""gate-lifecycle-report-selftest.py — acceptance test for
ops/gate-lifecycle-report.py (WR-000019 slice S5).

Drives the report as a subprocess against synthetic fixture repos (its own
CARR_LIFECYCLE_REPO env var, same convention as ops/hook-telemetry-rollup.py's
CARR_ROLLUP_REPO), so it never touches this machine's real out/ or
ops/config/ files -- those are shared with every other worktree and session on
this Mac (out/ is a symlink back to the canonical checkout), and a test that
wrote real timestamps into them would be a flake waiting to collide with
whatever else is running.

THE THREE SCENARIOS THE SLICE ASKS FOR, PROVEN AGAINST SYNTHETIC LOGS:
  1. one quiet window on an ENFORCING gate       -> downgrade_to_announce
  2. two consecutive quiet windows                -> propose_retirement
     (checked for both an enforcing gate and an already-announce gate, since
     the rule fires "regardless of current mode" once the second window is
     quiet)
  3. an ACTIVE gate (a true positive in the current window) -> no proposal

Plus the coverage invariant (check:s5-lifecycle-fields-present): a gate wired
in ops/config/hooks.json with no entry in ops/config/gate-lifecycle.json must
fail the script's exit code, by name, every time -- and a gate that legitimately
lacks a timestamp in its own log (data_available: false) must never receive a
proposal even if every window it CAN see is empty, because "no data" and
"quiet" are different findings and conflating them is exactly the kind of
guess this codebase's other rollups (hook-telemetry-rollup.py's own
window_truncated_by_rotation) refuse to make silently.

Also proven: a shadow-mode gate never receives a downgrade/retirement proposal
regardless of how long it has been quiet (there is nothing to downgrade FROM),
and the report's own --no-append flag actually skips the
out/gate-lifecycle-report.jsonl write (so a caller who wants dry-run output
gets it).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "ops", "gate-lifecycle-report.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def stamp(days_ago, hour=12):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hour)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── a small, self-contained wiring: one plain gate, one run-record-gate.py-
#    wrapped gate (to prove the execve-dispatcher unwrap), one gate deliberately
#    left OUT of the metadata (to prove the coverage check fails by name).
WIRING = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/quiet-once.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/quiet-twice.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/announce-quiet-twice.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/active-gate.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/shadow-quiet.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/no-data-gate.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/run-record-gate.py wrapped-gate.py"},
        ]},
    ],
}

# The metadata for every gate above EXCEPT wrapped-gate.py, which is used only
# by the "missing metadata" fixture variant.
BASE_ENTRIES = {
    "quiet-once.py": {
        "failure_class": "test: one quiet window",
        "review_date": "2026-11-24",
        "mode": "enforcing",
    },
    "quiet-twice.py": {
        "failure_class": "test: two quiet windows, enforcing",
        "review_date": "2026-11-24",
        "mode": "enforcing",
    },
    "announce-quiet-twice.py": {
        "failure_class": "test: two quiet windows, already announce",
        "review_date": "2026-11-24",
        "mode": "announce",
    },
    "active-gate.py": {
        "failure_class": "test: still catching things",
        "review_date": "2026-11-24",
        "mode": "enforcing",
    },
    "shadow-quiet.py": {
        "failure_class": "test: shadow gate, quiet forever",
        "review_date": "2026-11-24",
        "mode": "shadow",
    },
    "no-data-gate.py": {
        "failure_class": "test: no timestamp in its own log",
        "review_date": "2026-11-24",
        "mode": "enforcing",
    },
    "wrapped-gate.py": {
        "failure_class": "test: run-record-gate.py-wrapped gate",
        "review_date": "2026-11-24",
        "mode": "enforcing",
    },
}


def metric_for(gate, log_name="gates.jsonl"):
    return {
        "log_path": f"out/{log_name}",
        "log_format": "jsonl",
        "ts_field": "ts",
        "hook_filter": {"field": "hook", "equals": gate},
        "true_positive": {"kind": "field_in", "field": "outcome", "values": ["deny"]},
        "rule_description": "test fixture",
        "note": "",
    }


def build_repo(tmp, drop_metadata_for=None, no_timestamp_field=False):
    """A synthetic repo: ops/config/hooks.json, ops/config/gate-lifecycle.json,
    out/gates.jsonl -- with rows placed to build exactly the three required
    scenarios, plus the shadow and no-data edge cases.
    """
    os.makedirs(os.path.join(tmp, "ops", "config"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "out"), exist_ok=True)

    with open(os.path.join(tmp, "ops", "config", "hooks.json"), "w", encoding="utf-8") as fh:
        json.dump(WIRING, fh)

    entries = {g: dict(e) for g, e in BASE_ENTRIES.items()}
    for gate in entries:
        entries[gate]["catch_metric"] = metric_for(gate)
    if no_timestamp_field:
        entries["no-data-gate.py"]["catch_metric"]["ts_field"] = None
    if drop_metadata_for:
        for gate in drop_metadata_for:
            entries.pop(gate, None)

    with open(os.path.join(tmp, "ops", "config", "gate-lifecycle.json"), "w", encoding="utf-8") as fh:
        json.dump({"gates": entries}, fh)

    rows = []

    def row(gate, days_ago, outcome="deny"):
        return {"ts": stamp(days_ago), "hook": gate, "outcome": outcome}

    # quiet-once.py: fired (deny) 10 days ago (prior window, days 7-14), NOTHING
    # in the current window (days 0-7) -> exactly one quiet window.
    rows.append(row("quiet-once.py", 10))

    # quiet-twice.py: fired 17 days ago (window index 2), nothing in windows 0
    # or 1 -> two consecutive quiet windows from the most recent end.
    rows.append(row("quiet-twice.py", 17))

    # announce-quiet-twice.py: same shape, already in announce mode.
    rows.append(row("announce-quiet-twice.py", 17))

    # active-gate.py: fired today, well inside the current window.
    rows.append(row("active-gate.py", 1))

    # shadow-quiet.py: nothing, ever -- proves shadow gates get no proposal.
    # (no rows appended)

    # no-data-gate.py: would look "quiet forever" by row content alone, but its
    # metric has no ts_field in the no_timestamp_field fixture variant, so it
    # must be reported data_available=false instead of proposed for anything.
    rows.append(row("no-data-gate.py", 1))

    with open(os.path.join(tmp, "out", "gates.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    return tmp


def run_report(tmp, *args):
    env = dict(os.environ)
    env["CARR_LIFECYCLE_REPO"] = tmp
    proc = subprocess.run([sys.executable, SCRIPT, "--days", "7"] + list(args),
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def main():
    print("gate-lifecycle-report-selftest")

    # ── the happy-path fixture: every wired gate has metadata ──
    tmp = tempfile.mkdtemp(prefix="lifecycle-selftest-")
    try:
        build_repo(tmp, no_timestamp_field=True)

        rc, out, err = run_report(tmp, "--json", "--no-append")
        check("the report runs against a fixture repo", rc == 0, err[:400])
        report = json.loads(out)
        gates = report["gates"]

        check("check:s5-lifecycle-fields-present — no missing metadata",
              report["missing_metadata"] == [], report["missing_metadata"])
        check("every wired gate is represented in the per-gate table",
              all(g in gates for g in
                  ["quiet-once.py", "quiet-twice.py", "announce-quiet-twice.py",
                   "active-gate.py", "shadow-quiet.py", "no-data-gate.py",
                   "wrapped-gate.py"]),
              sorted(gates))
        check("the run-record-gate.py-wrapped gate is unwrapped to its own name",
              "wrapped-gate.py" in gates and "run-record-gate.py" not in gates,
              sorted(gates))

        # ── scenario 1: one quiet window on an enforcing gate ──
        q1 = gates["quiet-once.py"]
        check("quiet-once.py: exactly one consecutive quiet window",
              q1["consecutive_quiet_windows"] == 1, q1)
        check("quiet-once.py: proposal is downgrade_to_announce",
              q1["proposal"] is not None and q1["proposal"]["action"] == "downgrade_to_announce",
              q1["proposal"])

        # ── scenario 2: two consecutive quiet windows -> retirement ──
        q2 = gates["quiet-twice.py"]
        check("quiet-twice.py: at least two consecutive quiet windows",
              q2["consecutive_quiet_windows"] >= 2, q2)
        check("quiet-twice.py: proposal is propose_retirement",
              q2["proposal"] is not None and q2["proposal"]["action"] == "propose_retirement",
              q2["proposal"])

        q2b = gates["announce-quiet-twice.py"]
        check("an already-announce gate ALSO escalates to retirement on a "
              "second quiet window",
              q2b["proposal"] is not None and q2b["proposal"]["action"] == "propose_retirement",
              q2b["proposal"])

        # ── scenario 3: an active gate gets no proposal ──
        act = gates["active-gate.py"]
        check("active-gate.py: no quiet windows",
              act["consecutive_quiet_windows"] == 0, act)
        check("active-gate.py: no proposal",
              act["proposal"] is None, act["proposal"])

        # ── shadow gates never get a proposal, however long they're quiet ──
        shadow = gates["shadow-quiet.py"]
        check("shadow-quiet.py: quiet the whole lookback",
              shadow["consecutive_quiet_windows"] == 8, shadow)
        check("shadow-quiet.py: still no proposal (mode == shadow)",
              shadow["proposal"] is None, shadow["proposal"])

        # ── a gate with no timestamp field is a data gap, not a false quiet ──
        nodata = gates["no-data-gate.py"]
        check("no-data-gate.py: reported as data_available == false",
              nodata["data_available"] is False, nodata)
        check("no-data-gate.py: never proposed anything despite looking quiet",
              nodata["proposal"] is None, nodata["proposal"])
        check("no-data-gate.py is listed in the report's data_gaps",
              "no-data-gate.py" in report["data_gaps"], report["data_gaps"])

        # ── the printed form ──
        rc, text, _ = run_report(tmp, "--no-append")
        check("the human report prints", rc == 0)
        check("it prints the coverage OK line",
              "check:s5-lifecycle-fields-present" in text, text[:400])
        check("it names the retirement proposal", "quiet-twice.py" in text
              and "propose_retirement" in text)
        check("it names the downgrade proposal", "downgrade_to_announce" in text)

        # ── --no-append actually skips the write ──
        report_log = os.path.join(tmp, "out", "gate-lifecycle-report.jsonl")
        check("--no-append leaves no report row behind",
              not os.path.exists(report_log))
        rc, _, _ = run_report(tmp)
        check("without --no-append, a report row IS appended",
              os.path.exists(report_log) and
              sum(1 for _ in open(report_log)) == 1)
        rc, _, _ = run_report(tmp)
        check("a second run appends a second row rather than overwriting",
              sum(1 for _ in open(report_log)) == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── missing metadata fails the exit code, by name ──
    tmp2 = tempfile.mkdtemp(prefix="lifecycle-selftest-missing-")
    try:
        build_repo(tmp2, drop_metadata_for=["active-gate.py"])
        rc, out, err = run_report(tmp2, "--json", "--no-append")
        check("a wired gate with no lifecycle metadata FAILS the exit code",
              rc == 1, rc)
        report = json.loads(out)
        check("check:s5-lifecycle-fields-present names the exact missing gate",
              report["missing_metadata"] == ["active-gate.py"],
              report["missing_metadata"])

        rc, text, _ = run_report(tmp2, "--no-append")
        check("the human report also names it under MISSING LIFECYCLE METADATA",
              "MISSING LIFECYCLE METADATA" in text and "active-gate.py" in text,
              text[:600])
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ── stale metadata (entry present, no longer wired) is a warning, not a
    #    failure -- a parallel slice may have retired or rewired a gate ──
    tmp3 = tempfile.mkdtemp(prefix="lifecycle-selftest-stale-")
    try:
        build_repo(tmp3, no_timestamp_field=True)
        meta_path = os.path.join(tmp3, "ops", "config", "gate-lifecycle.json")
        meta = json.load(open(meta_path))
        meta["gates"]["retired-elsewhere.py"] = {
            "failure_class": "test: stale entry",
            "review_date": "2026-11-24", "mode": "enforcing",
            "catch_metric": metric_for("retired-elsewhere.py"),
        }
        json.dump(meta, open(meta_path, "w"))

        rc, out, err = run_report(tmp3, "--json", "--no-append")
        check("a stale metadata entry does NOT fail the exit code", rc == 0, err[:300])
        report = json.loads(out)
        check("it IS reported as stale_metadata, by name",
              "retired-elsewhere.py" in report["stale_metadata"],
              report["stale_metadata"])
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    return CHECKER.summary(limit=10)


if __name__ == "__main__":
    raise SystemExit(main())
