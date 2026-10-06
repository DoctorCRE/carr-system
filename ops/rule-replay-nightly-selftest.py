#!/usr/bin/env python3
"""rule-replay-nightly-selftest.py -- acceptance test for
ops/rule-replay-nightly.py (WR-000019 slice S12).

Runs entirely against synthetic fixtures: a throwaway ~/.claude/projects-like
directory (CARR_REPLAY_PROJECTS_DIR), a throwaway output directory
(CARR_REPLAY_OUT_DIR), and a throwaway gate-lifecycle repo
(CARR_REPLAY_LIFECYCLE_REPO). Never touches this machine's real out/ (a
symlink shared by every worktree) or the real ~/.claude/projects/. REPO
itself (hooks/, lib/, the real compiled trigger table) is left pointed at
this checkout on purpose -- that code is real and read-only, and exercising
it for real is the point of "reuse the live rail's matcher, not a copy".

WHAT IS PROVEN:
  1. is_known_failure_signature -- the signature matcher -- classifies
     exactly {shadow_writing_check, delegation_material} and nothing else.
  2. discover_session_files finds a transcript whose recorded cwd is under
     the target repo and inside the window, and excludes one whose cwd is
     unrelated and one whose mtime is too old.
  3. score_jit_triggers reuses the real hooks/rule-pack-preuse-reselection.py
     `_row_matches` against a synthetic, controlled trigger-row list (kept
     independent of the real, evolving compiled table) and a synthetic
     transcript tool_use call, producing the expected rule_id/trigger_id/
     fire_count.
  4. score_gate_ledgers reuses ops/gate-lifecycle-report.py's own
     read_rows()/is_true_positive() against synthetic conduct-gate-shadow and
     delegation-gate-ledger rows, correctly scoped to sessions already known
     active and to the window.
  5. Every emitted row carries "judge": "not_evaluated" (the documented stub).
  6. stage_defect_proposals emits STAGED rows with every record-defect
     required field, and is idempotent across repeated calls (same
     finding_key never staged twice).
  7. A full `main()` subprocess run against the synthetic fixtures produces
     the exact end-to-end counts expected.
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
SCRIPT = REPO / "ops" / "rule-replay-nightly.py"

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


def stamp(minutes_ago=0):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── 1. signature matcher ───────────────────────────────────────────────────

def test_signature_matcher(mod):
    check("shadow_writing_check IS a known failure signature",
          mod.is_known_failure_signature("shadow_writing_check") is True)
    check("delegation_material IS a known failure signature",
          mod.is_known_failure_signature("delegation_material") is True)
    check("jit_trigger is NOT a known failure signature",
          mod.is_known_failure_signature("jit_trigger") is False)
    check("gate_catch is NOT a known failure signature",
          mod.is_known_failure_signature("gate_catch") is False)
    check("an unknown string is NOT a known failure signature",
          mod.is_known_failure_signature("something_else") is False)


# ── judge stub ──────────────────────────────────────────────────────────────

def test_judge_stub(mod):
    out = mod.llm_judge_not_evaluated("jit_trigger", ["evidence"])
    check("judge stub returns judge: not_evaluated", out.get("judge") == "not_evaluated")
    check("judge stub names its reason", "S12" in out.get("judge_reason", ""))


# ── discover_session_files ─────────────────────────────────────────────────

def test_discover_session_files(mod, tmp):
    projects = tmp / "projects"
    target_repo = tmp / "target-repo"
    (target_repo / ".claude" / "worktrees" / "feature").mkdir(parents=True)
    projects.mkdir(parents=True)

    # The fast directory-name prefix filter only admits names that look like
    # this repo's own mangled slug -- exercise it honestly rather than
    # sidestepping it, by using the same prefix the script itself computes.
    prefix = sorted(mod._slug_prefix_candidates(target_repo))[0]  # noqa: SLF001

    # matching: cwd under the target repo tree (a worktree beneath it)
    good_dir = projects / f"{prefix}-good"
    good_dir.mkdir()
    good_file = good_dir / "s1.jsonl"
    good_file.write_text(json.dumps({
        "type": "user", "sessionId": "s1", "timestamp": stamp(),
        "cwd": str(target_repo / ".claude" / "worktrees" / "feature"),
    }) + "\n")

    # non-matching: same directory-name prefix (passes the fast filter) but
    # its recorded cwd is a different repo entirely -- this is the
    # correctness check the fast filter alone cannot make.
    bad_dir = projects / f"{prefix}-bad"
    bad_dir.mkdir()
    bad_file = bad_dir / "s2.jsonl"
    bad_file.write_text(json.dumps({
        "type": "user", "sessionId": "s2", "timestamp": stamp(),
        "cwd": str(tmp / "unrelated-repo"),
    }) + "\n")

    # too old: cwd matches but mtime predates the window
    old_dir = projects / f"{prefix}-old"
    old_dir.mkdir()
    old_file = old_dir / "s3.jsonl"
    old_file.write_text(json.dumps({
        "type": "user", "sessionId": "s3", "timestamp": stamp(),
        "cwd": str(target_repo),
    }) + "\n")
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
    os.utime(old_file, (old_time, old_time))

    window_start = datetime.now(timezone.utc) - timedelta(hours=24)
    found = mod.discover_session_files(target_repo, projects, window_start)
    check("finds the transcript under the target repo tree",
          good_file in found, found)
    check("excludes the transcript with an unrelated cwd",
          bad_file not in found, found)
    check("excludes the transcript older than the window (by mtime)",
          old_file not in found, found)


# ── score_jit_triggers, reusing the real hook matcher ──────────────────────

def test_score_jit_triggers(mod, tmp):
    row_matches = mod.preuse_matcher()
    trigger_rows = [{
        "trigger_id": "test0000001", "kind": "bash_family",
        "pattern": r"\bfrobnicate\b", "packs": ["test-pack"],
        "rule_ids": ["testrule1"],
    }]
    transcript = tmp / "score-test.jsonl"
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(hours=1)
    ts = stamp(minutes_ago=1)
    records = [
        {"type": "assistant", "sessionId": "sess-a", "timestamp": ts,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "tu1", "name": "Bash",
              "input": {"command": "frobnicate --now"}},
         ]}},
        # a call that does NOT match the pattern
        {"type": "assistant", "sessionId": "sess-a", "timestamp": ts,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "tu2", "name": "Bash",
              "input": {"command": "echo hello"}},
         ]}},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    agg, session_ids = mod.score_jit_triggers([transcript], window_start, window_end,
                                              row_matches, trigger_rows)
    check("session sess-a is recognized as active", "sess-a" in session_ids, session_ids)
    key = ("sess-a", "testrule1")
    check("the matching bash_family trigger fired exactly once", agg.get(key, {}).get("count") == 1,
          agg)
    check("the non-matching call did not also fire it",
          agg[key]["count"] == 1 if key in agg else False)
    check("trigger_id is recorded", "test0000001" in agg[key]["trigger_ids"])
    check("evidence excerpt is at most 120 chars", len(agg[key]["evidence"][0]) <= 120)


def test_score_jit_triggers_window_exclusion(mod, tmp):
    row_matches = mod.preuse_matcher()
    trigger_rows = [{
        "trigger_id": "test0000002", "kind": "bash_family",
        "pattern": r"\bfrobnicate\b", "packs": [], "rule_ids": ["testrule2"],
    }]
    transcript = tmp / "score-test-window.jsonl"
    outside_ts = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    record = {"type": "assistant", "sessionId": "sess-b", "timestamp": outside_ts,
              "message": {"role": "assistant", "content": [
                  {"type": "tool_use", "id": "tu3", "name": "Bash",
                   "input": {"command": "frobnicate --old"}},
              ]}}
    transcript.write_text(json.dumps(record) + "\n")
    window_start = datetime.now(timezone.utc) - timedelta(hours=24)
    window_end = datetime.now(timezone.utc)
    agg, session_ids = mod.score_jit_triggers([transcript], window_start, window_end,
                                              row_matches, trigger_rows)
    check("a call outside the window is excluded from session_ids", "sess-b" not in session_ids)
    check("a call outside the window produces no jit signal", not agg, agg)


# ── score_gate_ledgers, reusing gate-lifecycle-report.py ───────────────────

def test_score_gate_ledgers(mod, tmp):
    lifecycle_repo = tmp / "lifecycle-repo"
    (lifecycle_repo / "ops" / "config").mkdir(parents=True)
    (lifecycle_repo / "out").mkdir(parents=True)

    conduct_shadow_log = lifecycle_repo / "out" / "conduct-gate-shadow.jsonl"
    delegation_log = lifecycle_repo / "out" / "delegation-gate-ledger.jsonl"

    now = datetime.now(timezone.utc)
    in_window_ts = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_window_ts = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conduct_shadow_log.write_text("\n".join(json.dumps(r) for r in [
        {"ts": in_window_ts, "hook": "conduct-stop-gate-shadow", "rule": "5be2f462",
         "classes": ["vocab"], "session": "sess-c", "excerpt": "some banned phrase here",
         "would_have_blocked": True},
        # outside the window -- must be excluded
        {"ts": out_window_ts, "hook": "conduct-stop-gate-shadow", "rule": "5be2f462",
         "classes": ["vocab"], "session": "sess-c", "excerpt": "old finding",
         "would_have_blocked": True},
        # a session never seen active in transcripts -- must be excluded
        {"ts": in_window_ts, "hook": "conduct-stop-gate-shadow", "rule": "5be2f462",
         "classes": ["vocab"], "session": "sess-unseen", "excerpt": "irrelevant",
         "would_have_blocked": True},
    ]) + "\n")

    delegation_log.write_text(json.dumps({
        "ts": in_window_ts, "session": "sess-c", "mechanical_calls": 1,
        "broad_calls": 5, "broad_calls_while_latched": 4, "would_have_flagged": 4,
        "flag_classes": {"second_mechanical_call": 4}, "task_ids": [],
        "latch_active_at_end": False, "materially_under_delegated": True,
        "cwd": "/tmp", "started_at": in_window_ts,
    }) + "\n")

    gates_meta = {
        "gates": {
            "conduct-stop-gate.py:chat-writing-shadow": {
                "failure_class": "test", "review_date": "2026-11-24", "mode": "shadow",
                "catch_metric": {
                    "log_path": "out/conduct-gate-shadow.jsonl", "log_format": "jsonl",
                    "ts_field": "ts", "hook_filter": {"field": "hook",
                                                      "equals": "conduct-stop-gate-shadow"},
                    "true_positive": {"kind": "row_exists"},
                },
            },
            "delegation-gate.py": {
                "failure_class": "test", "review_date": "2026-11-24", "mode": "announce",
                "catch_metric": {
                    "log_path": "out/delegation-gate-ledger.jsonl", "log_format": "jsonl",
                    "ts_field": "ts", "hook_filter": None,
                    "true_positive": {"kind": "field_truthy",
                                      "field": "materially_under_delegated"},
                },
            },
        },
    }
    (lifecycle_repo / "ops" / "config" / "gate-lifecycle.json").write_text(json.dumps(gates_meta))

    os.environ["CARR_LIFECYCLE_REPO"] = str(lifecycle_repo)
    lc_mod = load_module("gate_lifecycle_report_test", REPO / "ops" / "gate-lifecycle-report.py")
    del os.environ["CARR_LIFECYCLE_REPO"]

    triage = {"rules": [
        {"id": "6cfb67f5", "home": "gate", "carrying_control": "delegation_names_model_and_effort"},
    ]}
    window_start = now - timedelta(hours=24)
    window_end = now + timedelta(hours=1)
    session_ids = {"sess-c"}  # sess-unseen deliberately excluded

    agg = mod.score_gate_ledgers(lc_mod, triage, session_ids, window_start, window_end)

    shadow_key = ("sess-c", "conduct-stop-gate.py:chat-writing-shadow")
    check("shadow finding for sess-c is counted exactly once (window + true-positive filter)",
          agg.get(shadow_key, {}).get("count") == 1, agg.get(shadow_key))
    check("shadow finding carries rule id 5be2f462 straight from the row",
          agg.get(shadow_key, {}).get("rule_ids") == {"5be2f462"})
    check("shadow signal_kind is shadow_writing_check",
          agg.get(shadow_key, {}).get("signal_kind") == "shadow_writing_check")

    deleg_key = ("sess-c", "delegation-gate.py")
    check("delegation ledger row for sess-c counted", agg.get(deleg_key, {}).get("count") == 1)
    check("delegation rule_ids resolved via carrying_control",
          agg.get(deleg_key, {}).get("rule_ids") == {"6cfb67f5"})
    check("delegation signal_kind is delegation_material",
          agg.get(deleg_key, {}).get("signal_kind") == "delegation_material")

    check("sess-unseen never produced a row (not in session_ids)",
          all(sid != "sess-unseen" for sid, _ in agg))


# ── stage_defect_proposals idempotency + required fields ──────────────────

def test_stage_defect_proposals(mod, tmp):
    out_dir = tmp / "defect-out"
    out_dir.mkdir()
    mod.OUT = out_dir
    mod.DEFECT_PROPOSALS_LOG = out_dir / "rule-replay-defect-proposals.jsonl"

    row = {
        "signal_kind": "shadow_writing_check", "session_id": "sess-d",
        "gate_key": "conduct-stop-gate.py:chat-writing-shadow",
        "rule_ids": ["5be2f462"], "evidence": ["a banned construction excerpt"],
    }
    other_kind_row = {
        "signal_kind": "jit_trigger", "session_id": "sess-d",
        "gate_key": None, "rule_ids": ["testrule1"], "evidence": ["irrelevant"],
    }

    staged_first = mod.stage_defect_proposals([row, other_kind_row])
    check("exactly one proposal staged (jit_trigger is not autofed)", staged_first == 1,
          staged_first)

    staged_again = mod.stage_defect_proposals([row])
    check("re-staging the identical finding produces zero new rows (idempotent)",
          staged_again == 0, staged_again)

    lines = [json.loads(line) for line in
             mod.DEFECT_PROPOSALS_LOG.read_text().splitlines() if line.strip()]
    check("exactly one line ever written to the proposals ledger", len(lines) == 1, lines)
    proposal = lines[0]
    required_fields = {"idempotency_key", "defect_class", "claimed", "actual", "detected_by"}
    check("every record-defect required field is present",
          required_fields <= set(proposal), sorted(proposal))
    check("idempotency_key is explicitly null, never a fake minted uuid",
          proposal["idempotency_key"] is None)
    check("claimed and actual differ (record-defect's own contradiction requirement)",
          proposal["claimed"].strip().lower() != proposal["actual"].strip().lower())
    check("detected_by is a valid record-defect enum value",
          proposal["detected_by"] in {"human", "self", "gate", "check", "peer_review", "downstream"})
    check("rule_violated resolved from the row's rule_ids", proposal["rule_violated"] == "5be2f462")


# ── end-to-end subprocess run ───────────────────────────────────────────────

def test_end_to_end(tmp):
    projects = tmp / "e2e-projects"
    target_repo = tmp / "e2e-target-repo"
    out_dir = tmp / "e2e-out"
    lifecycle_repo = tmp / "e2e-lifecycle-repo"
    target_repo.mkdir()
    out_dir.mkdir()
    (lifecycle_repo / "ops" / "config").mkdir(parents=True)
    (lifecycle_repo / "out").mkdir(parents=True)
    (lifecycle_repo / "ops" / "config" / "gate-lifecycle.json").write_text(json.dumps({"gates": {}}))

    mod = load_module("rule_replay_nightly_e2e_prefix", SCRIPT)
    prefix = sorted(mod._slug_prefix_candidates(target_repo))[0]  # noqa: SLF001
    slug_dir = projects / f"{prefix}-e2e"
    slug_dir.mkdir(parents=True)
    ts = stamp(minutes_ago=5)
    record = {"type": "assistant", "sessionId": "sess-e2e", "timestamp": ts,
              "cwd": str(target_repo),
              "message": {"role": "assistant", "content": [
                  {"type": "tool_use", "id": "tu-e2e", "name": "Bash",
                   "input": {"command": "git push origin main"}},
              ]}}
    (slug_dir / "sess-e2e.jsonl").write_text(json.dumps(record) + "\n")

    env = dict(os.environ)
    env["CARR_REPLAY_PROJECTS_DIR"] = str(projects)
    env["CARR_REPLAY_TARGET_REPO"] = str(target_repo)
    env["CARR_REPLAY_OUT_DIR"] = str(out_dir)
    env["CARR_REPLAY_LIFECYCLE_REPO"] = str(lifecycle_repo)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--since-hours", "24", "--json"],
        cwd=str(REPO), capture_output=True, text=True, timeout=60, env=env)
    check("end-to-end run exits 0", result.returncode == 0, result.stderr[-1000:])
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        check("end-to-end output is valid JSON", False, f"{exc}: {result.stdout[:500]}")
        return
    summary = payload["summary"]
    check("end-to-end: one session scored", summary["sessions_scored"] == 1, summary)
    check("end-to-end: at least one jit_trigger signal fired for a real git-push command "
          "(reused compiled table + real hook matcher)",
          summary["signals_by_kind"].get("jit_trigger", 0) >= 1, summary)
    check("end-to-end ledger file was actually written",
          (out_dir / "rule-replay-nightly.jsonl").exists())
    lines = [json.loads(x) for x in
             (out_dir / "rule-replay-nightly.jsonl").read_text().splitlines() if x.strip()]
    check("every emitted signal row carries judge: not_evaluated",
          all(r.get("judge") == "not_evaluated" for r in lines if r.get("schema") ==
              "rule-replay-nightly/v1"))


def main() -> int:
    mod = load_module("rule_replay_nightly_test", SCRIPT)
    test_signature_matcher(mod)
    test_judge_stub(mod)

    tmp = Path(tempfile.mkdtemp(prefix="rule-replay-selftest-"))
    try:
        test_discover_session_files(mod, tmp)
        test_score_jit_triggers(mod, tmp)
        test_score_jit_triggers_window_exclusion(mod, tmp)
        test_score_gate_ledgers(mod, tmp)
        test_stage_defect_proposals(mod, tmp)
        test_end_to_end(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return CHECKER.summary(limit=10)


if __name__ == "__main__":
    raise SystemExit(main())
