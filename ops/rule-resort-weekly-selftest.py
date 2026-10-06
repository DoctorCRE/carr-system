#!/usr/bin/env python3
"""rule-resort-weekly-selftest.py -- acceptance test for
ops/rule-resort-weekly.py (WR-000019 slice S12).

Synthetic fixtures throughout: a throwaway out/ (CARR_RESORT_OUT_DIR) holding
a synthetic replay ledger and shadow ledger, and a throwaway gate-lifecycle
repo (CARR_RESORT_LIFECYCLE_REPO). Never touches the real, worktree-shared
out/ tree.

WHAT IS PROVEN:
  1. is_gate_candidate -- the weekly threshold -- requires BOTH a minimum
     fire count AND a minimum session count; either alone is insufficient.
  2. is_scheduled_weekday recognizes exactly Monday (ISO weekday 1).
  3. jit_gate_candidates only considers SEEDED-detector rule ids (never
     pack_fallback), sums fire_count correctly across rows, counts distinct
     sessions correctly, and cites evidence rows.
  4. gate_downgrade_proposals reproduces ops/gate-lifecycle-report.py's own
     build() output verbatim -- no re-derivation.
  5. core_jit_candidates proposes a core-home rule whose pack never appears
     in the shadow ledger's `needed` field, leaves alone one whose pack DID
     appear, and reports a rule with no pack membership as a data gap
     (never a guessed verdict either way).
  6. A full `main()` subprocess run: --force bypasses the weekday guard, a
     plain run on a non-Monday SKIPs and writes nothing.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "rule-resort-weekly.py"

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stamp(days_ago=0, hours_ago=0):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── weekly thresholds ───────────────────────────────────────────────────────

def test_is_gate_candidate(mod):
    check("fires and sessions both over threshold -> candidate",
          mod.is_gate_candidate(10, 3, min_fires=10, min_sessions=3) is True)
    check("fires exactly at threshold, sessions exactly at threshold -> candidate",
          mod.is_gate_candidate(10, 3, min_fires=10, min_sessions=3) is True)
    check("fires under threshold, sessions over -> NOT a candidate",
          mod.is_gate_candidate(9, 5, min_fires=10, min_sessions=3) is False)
    check("fires over threshold, sessions under -> NOT a candidate "
          "(one heavy session alone is not 'repeated')",
          mod.is_gate_candidate(100, 1, min_fires=10, min_sessions=3) is False)
    check("both zero -> NOT a candidate",
          mod.is_gate_candidate(0, 0, min_fires=10, min_sessions=3) is False)


def test_is_scheduled_weekday(mod):
    monday = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)  # a known Monday
    tuesday = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    sunday = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    check("Monday is the scheduled weekday", mod.is_scheduled_weekday(monday) is True)
    check("Tuesday is not", mod.is_scheduled_weekday(tuesday) is False)
    check("Sunday is not", mod.is_scheduled_weekday(sunday) is False)


# ── jit_gate_candidates: seeded-only, sums, evidence ───────────────────────

def test_jit_gate_candidates(mod, tmp):
    out_dir = tmp / "jit-out"
    out_dir.mkdir()
    replay_log = out_dir / "rule-replay-nightly.jsonl"

    now = datetime.now(timezone.utc)
    rows = [
        # rule "seeded1" fires across 3 distinct sessions, 12 total fires -> candidate
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(hours_ago=1),
         "session_id": "s1", "signal_kind": "jit_trigger", "rule_ids": ["seeded1"],
         "fire_count": 5, "evidence": ["ev1"]},
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(hours_ago=2),
         "session_id": "s2", "signal_kind": "jit_trigger", "rule_ids": ["seeded1"],
         "fire_count": 5, "evidence": ["ev2"]},
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(hours_ago=3),
         "session_id": "s3", "signal_kind": "jit_trigger", "rule_ids": ["seeded1"],
         "fire_count": 2, "evidence": ["ev3"]},
        # rule "fallback1" fires a lot too, but is a pack_fallback rule id --
        # must NOT be counted at all.
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(hours_ago=1),
         "session_id": "s1", "signal_kind": "jit_trigger", "rule_ids": ["fallback1"],
         "fire_count": 50, "evidence": ["noisy"]},
        # rule "seeded2" fires once, in one session -> below threshold
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(hours_ago=1),
         "session_id": "s1", "signal_kind": "jit_trigger", "rule_ids": ["seeded2"],
         "fire_count": 1, "evidence": ["once"]},
        # a gate_catch row -- must be ignored entirely by this function
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(hours_ago=1),
         "session_id": "s1", "signal_kind": "gate_catch", "rule_ids": ["seeded1"],
         "fire_count": 99, "evidence": ["not jit"]},
        # outside the window
        {"schema": "rule-replay-nightly/v1", "run_ts": stamp(days_ago=30),
         "session_id": "s4", "signal_kind": "jit_trigger", "rule_ids": ["seeded1"],
         "fire_count": 99, "evidence": ["too old"]},
    ]
    replay_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    mod.OUT = out_dir
    mod.REPLAY_LOG = replay_log

    triggers_doc = {"triggers": [
        {"trigger_id": "t1", "source": "seeded_detector", "rule_ids": ["seeded1"]},
        {"trigger_id": "t2", "source": "seeded_detector", "rule_ids": ["seeded2"]},
        {"trigger_id": "t3", "source": "pack_fallback", "rule_ids": ["fallback1"]},
    ]}
    window_start = now - timedelta(days=7)
    window_end = now + timedelta(hours=1)

    candidates = mod.jit_gate_candidates(window_start, window_end, triggers_doc,
                                         min_fires=10, min_sessions=3)
    ids = {c["rule_id"] for c in candidates}
    check("seeded1 (12 fires, 3 sessions) IS proposed", "seeded1" in ids, ids)
    check("fallback1 (50 fires, pack_fallback) is NEVER proposed", "fallback1" not in ids, ids)
    check("seeded2 (1 fire, 1 session) is NOT proposed", "seeded2" not in ids, ids)
    check("exactly one candidate", len(candidates) == 1, candidates)
    seeded1 = candidates[0]
    check("total_fires summed correctly (5+5+2=12)", seeded1["total_fires"] == 12, seeded1)
    check("distinct_sessions counted correctly (3)", seeded1["distinct_sessions"] == 3, seeded1)
    check("evidence rows cited", len(seeded1["evidence_rows"]) >= 1, seeded1)


# ── gate_downgrade_proposals: verbatim delegation ──────────────────────────

def test_gate_downgrade_proposals(mod, tmp):
    lifecycle_repo = tmp / "gd-lifecycle-repo"
    (lifecycle_repo / "ops" / "config").mkdir(parents=True)
    (lifecycle_repo / "out").mkdir(parents=True)
    meta = {"gates": {
        "quiet-gate.py": {
            "failure_class": "test", "review_date": "2026-11-24", "mode": "enforcing",
            "catch_metric": {
                "log_path": "out/quiet.jsonl", "log_format": "jsonl",
                "ts_field": "ts", "hook_filter": None,
                "true_positive": {"kind": "row_exists"},
            },
        },
    }}
    (lifecycle_repo / "ops" / "config" / "gate-lifecycle.json").write_text(json.dumps(meta))
    # An absent log is a data gap, never evidence of quiet windows.

    os.environ["CARR_LIFECYCLE_REPO"] = str(lifecycle_repo)
    lc_mod = load_module("gate_lifecycle_report_resort_test",
                         REPO / "ops" / "gate-lifecycle-report.py")
    del os.environ["CARR_LIFECYCLE_REPO"]

    missing = mod.gate_downgrade_proposals(lc_mod, 7)
    check("an absent catch log is a data gap and produces no proposal",
          missing["data_gaps"] == ["quiet-gate.py"] and not missing["proposals"], missing)
    # An existing empty log supplies the quiet-window fixture.
    (lifecycle_repo / "out" / "quiet.jsonl").touch()
    direct = lc_mod.build(7)
    via_resort = mod.gate_downgrade_proposals(lc_mod, 7)
    check("rule-resort-weekly reproduces gate-lifecycle-report.build() verbatim",
          via_resort["proposals"] == direct["proposals"], (via_resort["proposals"], direct["proposals"]))
    check("the quiet gate actually produced a proposal in this fixture (sanity)",
          any(p["gate"] == "quiet-gate.py" for p in direct["proposals"]), direct["proposals"])


# ── core_jit_candidates ─────────────────────────────────────────────────────

def test_core_jit_candidates(mod, tmp):
    out_dir = tmp / "core-out"
    out_dir.mkdir()
    shadow_log = out_dir / "rule-delivery-shadow.jsonl"
    now = datetime.now(timezone.utc)
    rows = [
        {"ts": stamp(hours_ago=1), "needed": ["packA"]},
        {"ts": stamp(days_ago=30), "needed": ["packB"]},  # outside window
    ]
    shadow_log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    mod.OUT = out_dir
    mod.SHADOW_LOG = shadow_log

    triage = {"rules": [
        {"id": "core-fired", "home": "core"},
        {"id": "core-quiet", "home": "core"},
        {"id": "core-nopack", "home": "core"},
        {"id": "not-core", "home": "jit"},
    ]}
    enforcement_map = {"rule_load_layers": {
        "core-fired": {"packs": ["packA"]},     # packA WAS needed this window
        "core-quiet": {"packs": ["packB"]},      # packB only needed OUTSIDE the window
        "core-nopack": {"packs": []},            # no pack membership at all
        "not-core": {"packs": ["packA"]},
    }}
    window_start = now - timedelta(days=7)
    window_end = now + timedelta(hours=1)

    candidates, data_gaps = mod.core_jit_candidates(window_start, window_end, triage,
                                                     enforcement_map)
    ids = {c["rule_id"] for c in candidates}
    check("core-fired (pack DID fire in window) is NOT proposed", "core-fired" not in ids, ids)
    check("core-quiet (pack never fired in window) IS proposed", "core-quiet" in ids, ids)
    check("core-nopack is a data gap, not a guessed candidate",
          "core-nopack" in data_gaps and "core-nopack" not in ids, (data_gaps, ids))
    check("a non-core rule is never considered", "not-core" not in ids and
          "not-core" not in data_gaps)


# ── end-to-end subprocess: weekday guard ───────────────────────────────────

def test_end_to_end_weekday_guard(tmp):
    out_dir = tmp / "e2e-out"
    lifecycle_repo = tmp / "e2e-lifecycle-repo"
    out_dir.mkdir()
    (lifecycle_repo / "ops" / "config").mkdir(parents=True)
    (lifecycle_repo / "out").mkdir(parents=True)
    (lifecycle_repo / "ops" / "config" / "gate-lifecycle.json").write_text(json.dumps({"gates": {}}))

    env = dict(os.environ)
    env["CARR_RESORT_OUT_DIR"] = str(out_dir)
    env["CARR_RESORT_LIFECYCLE_REPO"] = str(lifecycle_repo)

    result_plain = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=str(REPO), capture_output=True,
        text=True, timeout=60, env=env)
    check("plain run exits 0 even when skipped", result_plain.returncode == 0)
    report_log = out_dir / "rule-resort-weekly.jsonl"
    is_monday = datetime.now(timezone.utc).isoweekday() == 1
    if not is_monday:
        check("plain run on a non-Monday prints SKIP and writes no report row",
              "SKIP" in result_plain.stdout and not report_log.exists(),
              result_plain.stdout)

    result_forced = subprocess.run(
        [sys.executable, str(SCRIPT), "--force", "--json"], cwd=str(REPO),
        capture_output=True, text=True, timeout=60, env=env)
    check("--force run exits 0", result_forced.returncode == 0, result_forced.stderr[-1000:])
    try:
        payload = json.loads(result_forced.stdout)
    except Exception as exc:
        check("--force run prints valid JSON", False, f"{exc}: {result_forced.stdout[:400]}")
        return
    check("--force run's schema is rule-resort-weekly/v1",
          payload.get("schema") == "rule-resort-weekly/v1", payload.get("schema"))
    check("--force run actually wrote the report row",
          report_log.exists())


def main() -> int:
    mod = load_module("rule_resort_weekly_test", SCRIPT)
    test_is_gate_candidate(mod)
    test_is_scheduled_weekday(mod)

    tmp = Path(tempfile.mkdtemp(prefix="rule-resort-selftest-"))
    try:
        test_jit_gate_candidates(mod, tmp)
        test_gate_downgrade_proposals(mod, tmp)
        test_core_jit_candidates(mod, tmp)
        test_end_to_end_weekday_guard(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return CHECKER.summary(limit=10)


if __name__ == "__main__":
    raise SystemExit(main())
