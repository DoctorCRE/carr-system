#!/usr/bin/env python3
"""ops/scheduled-run-record-selftest.py — the acceptance test for the
Program 3 scheduled-task recording gap: "Job failure becomes a durable failed
run, incident, or Work Request. It cannot exist only in stdout or a local
log." (roadmap line, quoted verbatim), for the ~17 Claude Code scheduled
tasks specifically.

Same house style as ops/program3-trace-gate.py and ops/program3-incident-gate.py:
inline assertions, printed as ok/FAIL, isolated fixtures. Two tiers:

  TIER 1 (always runs, no DB, no credential). Pure-function checks against
  lib/scheduled_run.py using fabricated transcript fixtures written to temp
  files, plus subprocess checks of the REAL hook with a
  DELIBERATELY UNREACHABLE DATABASE_URL — so "tolerates a missing DB
  credential by failing loud but not corrupting the run" is proven without
  ever touching a real database, staging or production.

  TIER 2 (only when DATABASE_URL is already set in the environment — i.e. run
  through `tools/db-tap.py --project staging run ops/scheduled-run-record-
  selftest.py`, never bare). Registers a throwaway probe service, records one
  run through the recording library, reads it back from ops.run, and DELETES it
  explicitly before exiting — psycopg autocommit is what ops-record.py's own
  connections use (see tools/ops-record.py connect()), so there is no
  transaction to roll back; explicit cleanup is the isolation instead.

  This tier never runs against production, and since 2026-08-18 that is CHECKED:
  tools/staging_jobs_dsn.py proves the DSN addresses the isolated staging
  endpoint before minting the ephemeral carr_jobs identity the recorder now
  requires, and the library subprocess runs with that credential and no broader one.
  The old wording — "it only activates when DATABASE_URL is present, and that is
  what db-tap --project staging sets" — described a convention, and PR #288 is
  what proved a convention was not enough.

RUN IT:
    .venv/bin/python ops/scheduled-run-record-selftest.py                  # tier 1 only
    .venv/bin/python tools/db-tap.py --project staging run \\
        ops/scheduled-run-record-selftest.py                              # tier 1 + 2
"""
import json
import os
import subprocess
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone

from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "lib"))
from selftest_harness import temporary_directory

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.scheduled_run import (  # noqa: E402
    quick_launched_task, load_records, load_raw, compute_outcome,
    build_run_args, RUN_KEY,
)
from lib.pgrow import fetch_one  # noqa: E402
from lib.loadpy import load_module_from_path  # noqa: E402

OPS_RECORD = load_module_from_path("scheduled_run_record_ops_record",
                                    os.path.join(REPO, "tools", "ops-record.py"))
DEAD_DSN = "postgresql://carr_jobs:probe@127.0.0.1:1/nonexistent"

HOOK = os.path.join(REPO, "hooks", "scheduled-run-record.py")
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
PY = PYTHON if os.path.exists(PYTHON) else sys.executable

# A PRIVATE REPO ROOT PER RUN (2026-08-23 load-flake sweep). The hook derives
# its per-session idempotency cache from CARR_REPO_ROOT when the variable is
# set (hooks/scheduled-run-record.py _repo_root()); without it every concurrent
# ci.sh run shared one fixed-name out/scheduled-run-record-cache directory.
PRIVATE_ROOT = temporary_directory(prefix="scheduled-run-record-selftest.")
os.makedirs(os.path.join(PRIVATE_ROOT, "out"), exist_ok=True)

PASSES: list[str] = []
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def write_fixture(records: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for line in records:
            fh.write(line + "\n")
    return path


LAUNCH = ('{{"type": "queue-operation", "operation": "enqueue", '
          '"timestamp": "2026-08-14T11:00:00.000Z", "sessionId": "{sid}", '
          '"content": "<scheduled-task name=\\"{task}\\" file=\\"/x/SKILL.md\\">\\nrun\\n"}}')


def fixture_succeeded(sid="fx-succeed", task="loop-drain-weekdays") -> str:
    return write_fixture([
        LAUNCH.format(sid=sid, task=task),
        json.dumps({"type": "queue-operation", "operation": "dequeue", "sessionId": sid}),
        json.dumps({"message": {"role": "assistant",
                    "content": [{"type": "text", "text": "closed three loops"}]},
                    "sessionId": sid}),
    ])


def fixture_gate_denied(sid="fx-denied", task="x-reply-run-daily") -> str:
    stdout_blob = json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "MODEL FLOOR TEST REASON"}})
    return write_fixture([
        LAUNCH.format(sid=sid, task=task),
        json.dumps({"attachment": {"type": "hook_success", "hookName": "PreToolUse",
                                    "stdout": stdout_blob}, "sessionId": sid}),
    ])


def fixture_trailing_tool_error(sid="fx-toolerr", task="restore-rehearse-weekly") -> str:
    return write_fixture([
        LAUNCH.format(sid=sid, task=task),
        json.dumps({"message": {"role": "user", "content": [
            {"type": "tool_result", "is_error": True, "content": "pg_restore: not found"}]},
            "sessionId": sid}),
    ])


def fixture_not_scheduled(sid="fx-notsched") -> str:
    return write_fixture([
        json.dumps({"message": {"role": "user", "content": [
            {"type": "text", "text": "hey what's up with loop-drain-weekdays"}]},
            "sessionId": sid}),
    ])


def run_hook(payload: dict, env: dict | None = None) -> tuple[int, str]:
    full_env = dict(os.environ)
    if env is not None:
        full_env = env
    proc = subprocess.run([PY, HOOK], input=json.dumps(payload), text=True,
                           capture_output=True, timeout=30, env=full_env)
    return proc.returncode, (proc.stdout + proc.stderr)


def unreachable_db_env() -> dict:
    """An environment where EVERY credential name ops-record.py reads points at
    a port nothing listens on, so its connection attempt fails FAST and LOCALLY
    — this proves the missing/bad-credential tolerance without ever reaching a
    real database, staging or production, and without depending on
    ~/.config/carr/db.env being absent (Path.home() resolves via the system's
    user database even with HOME unset, so simply clearing env vars does not
    guarantee no credential is found).

    SETTING every name, from ops-record.py's own credential_names(), is the
    whole point and it is written in blood. This helper used to set DATABASE_URL
    and DELETE CARR_DB_JOBS_URL, which blinded the recorder completely while
    `run` was connect("write"). PR #288 (cd3d7386, 2026-08-16) made it
    connect("routine") — CARR_DB_JOBS_URL only — and ops-record.py's
    _load_db_env() re-supplied the PRODUCTION jobs DSN by setdefault. The checks
    below then recorded against production for real: 46 fabricated SUCCEEDED
    rows in ops.run, two per CI run, against loop-drain-weekdays (the hook
    fixture) and radar-weekly (the CLI fixture), each carrying the fixture's own
    2026-08-14T11:00:00Z start. radar-weekly had no other observation at all, so
    its entire health signal in production was this suite's exhaust. Found
    2026-08-18.

    The username stays carr_jobs so routine mode's credential-shape check passes
    and what fails is the CONNECTION, which is the property under test."""
    env = dict(os.environ)
    for name in OPS_RECORD.credential_names():
        env[name] = DEAD_DSN
    # PRIVATE REPO ROOT PER RUN (2026-08-23 load-flake sweep): the hook derives
    # its idempotency cache from CARR_REPO_ROOT when set, so pointing it at a
    # throwaway tree keeps concurrent ci.sh runs from sharing one out/ dir.
    env["CARR_REPO_ROOT"] = PRIVATE_ROOT
    return env


def main() -> int:
    print("TIER 1 — pure logic + subprocess, no real database\n")

    # ── task identification ──────────────────────────────────────────────
    print("1. launch-marker task identification")
    p = fixture_succeeded(task="npi-sweep-weekly")
    check("quick_launched_task reads the marker", quick_launched_task(p) == "npi-sweep-weekly")
    p2 = fixture_not_scheduled()
    check("an ordinary session (no marker) returns None", quick_launched_task(p2) is None)
    os.unlink(p); os.unlink(p2)

    # ── outcome determination ────────────────────────────────────────────
    print("\n2. deterministic outcome signals")
    p = fixture_succeeded()
    recs, raw = load_records(p), load_raw(p)
    state, fc, detail = compute_outcome(recs, raw)
    check("no deny / no trailing error -> succeeded", state == "succeeded" and fc is None)
    os.unlink(p)

    p = fixture_gate_denied()
    recs, raw = load_records(p), load_raw(p)
    state, fc, detail = compute_outcome(recs, raw)
    check("a PreToolUse deny -> failed/gate_denied", state == "failed" and fc == "gate_denied")
    check("the deny reason is captured in detail", "MODEL FLOOR TEST REASON" in detail,
          detail)
    os.unlink(p)

    p = fixture_trailing_tool_error()
    recs, raw = load_records(p), load_raw(p)
    state, fc, detail = compute_outcome(recs, raw)
    check("a trailing unrecovered tool error -> failed/tool_error",
          state == "failed" and fc == "tool_error")
    os.unlink(p)

    social_launch = json.loads(LAUNCH.format(sid="fx-social", task="social-batch-weekly"))
    state, fc, detail = compute_outcome([social_launch], json.dumps(social_launch))
    check("social batch without a reported queue result -> failed/no_output",
          state == "failed" and fc == "social_batch_no_output")

    failure_marker = ("SOCIAL_BATCH_RESULT state=failed week_start=2026-11-02 "
                      "queue_count=0 complete=true failure_class=zero_drafts")
    social_failure = {"message": {"role": "assistant", "content": [
        {"type": "tool_result", "is_error": True, "content": failure_marker}]}}
    social_summary = {"message": {"role": "assistant", "content": [
        {"type": "text", "text": "The social batch ended with no scheduled posts."}]}}
    state, fc, detail = compute_outcome(
        [social_launch, social_failure, social_summary],
        json.dumps([social_launch, social_failure, social_summary]))
    check("social batch failure remains failed after its summary",
          state == "failed" and fc == "social_batch_zero_drafts")

    success_marker = ("SOCIAL_BATCH_RESULT state=succeeded week_start=2026-11-02 "
                      "queue_count=12 complete=true failure_class=none")
    social_success = {"message": {"role": "assistant", "content": [
        {"type": "tool_result", "is_error": False, "content": success_marker}]}}
    state, fc, detail = compute_outcome(
        [social_launch, social_success], json.dumps([social_launch, social_success]))
    check("social batch succeeds only with a reported covered-week queue",
          state == "succeeded" and fc is None)

    other_launch = json.loads(LAUNCH.format(sid="fx-other", task="loop-drain-weekdays"))
    state, fc, detail = compute_outcome([other_launch], json.dumps(other_launch))
    check("other scheduled tasks keep their existing outcome rule",
          state == "succeeded" and fc is None)

    # ── argv construction (the piece that maps outcome -> ops-record call) ─
    print("\n3. ops-record.py argv construction")
    argv = build_run_args("loop-drain-weekdays", "failed", "gate_denied",
                           "2026-08-14T11:00:00Z", "2026-08-14T11:05:00Z",
                           "detail line", None, "hooks/scheduled-run-record.py")
    check("--service names the task", "--service" in argv and
          argv[argv.index("--service") + 1] == "loop-drain-weekdays")
    check("--key is the fixed RUN_KEY", "--key" in argv and
          argv[argv.index("--key") + 1] == RUN_KEY == "scheduled-session")
    check("--failure-class is present for a failed state",
          "--failure-class" in argv and argv[argv.index("--failure-class") + 1] == "gate_denied")

    argv_ok = build_run_args("loop-drain-weekdays", "succeeded", None,
                              "2026-08-14T11:00:00Z", "2026-08-14T11:05:00Z",
                              "ok", None, "hooks/scheduled-run-record.py")
    check("no --failure-class on a succeeded state", "--failure-class" not in argv_ok)

    argv_nightly = build_run_args("nightly-record-layer", "succeeded", None,
                                   "2026-08-14T07:05:00Z", "2026-08-14T07:20:00Z",
                                   "ok", None, "hooks/scheduled-run-record.py")
    check("nightly-record-layer's outer session uses the SAME distinct run_key "
          "(never collides with the chain's own nightly.* step keys)",
          argv_nightly[argv_nightly.index("--key") + 1] == "scheduled-session")

    # ── the hook itself, subprocess, real payloads ───────────────────────
    print("\n4. the real hook, end to end, with an unreachable database")
    cache_dir = os.path.join(PRIVATE_ROOT, "out", "scheduled-run-record-cache")

    p = fixture_not_scheduled(sid="selftest-notsched-" + uuid.uuid4().hex[:8])
    rc, out = run_hook({"hook_event_name": "Stop", "session_id": "selftest-notsched",
                         "transcript_path": p, "stop_hook_active": False})
    check("a non-scheduled session: hook exits 0 with no output", rc == 0 and out.strip() == "")
    os.unlink(p)

    sid = "selftest-succeed-" + uuid.uuid4().hex[:8]
    p = fixture_succeeded(sid=sid, task="loop-drain-weekdays")
    cache_file = os.path.join(cache_dir, sid)
    if os.path.exists(cache_file):
        os.unlink(cache_file)
    rc, out = run_hook({"hook_event_name": "Stop", "session_id": sid,
                         "transcript_path": p, "stop_hook_active": False},
                        env=unreachable_db_env())
    check("a scheduled session with an unreachable DB: hook still exits 0 "
          "(never blocks/crashes the run)", rc == 0)
    check("...and marks the session recorded (idempotency cache written)",
          os.path.exists(cache_file))

    rc2, out2 = run_hook({"hook_event_name": "Stop", "session_id": sid,
                           "transcript_path": p, "stop_hook_active": False},
                          env=unreachable_db_env())
    check("a SECOND Stop of the same session is a no-op (per-session idempotency)",
          rc2 == 0 and out2.strip() == "")
    if os.path.exists(cache_file):
        os.unlink(cache_file)
    os.unlink(p)

    rc, out = run_hook({"hook_event_name": "PreToolUse", "session_id": "wrong-event",
                         "transcript_path": "/dev/null"})
    check("a non-Stop event is ignored", rc == 0 and out.strip() == "")

    sid = "selftest-social-empty-" + uuid.uuid4().hex[:8]
    p = write_fixture([LAUNCH.format(sid=sid, task="social-batch-weekly")])
    rc, out = run_hook({"hook_event_name": "Stop", "session_id": sid,
                         "transcript_path": p, "stop_hook_active": False},
                        env=unreachable_db_env())
    digest = subprocess.run([PY, os.path.join(REPO, "hooks", "job-watchdog-digest.py")],
                            input="{}", text=True, capture_output=True, timeout=30,
                            env=dict(unreachable_db_env(), CARR_JOB_ROOT=PRIVATE_ROOT))
    context = json.loads(digest.stdout)["hookSpecificOutput"]["additionalContext"] if digest.stdout else ""
    check("a social batch without a queue result reaches the existing watchdog digest",
          rc == 0 and digest.returncode == 0 and "social-batch-weekly" in context
          and "no batch output" in context, out + digest.stderr + context)
    os.unlink(p)

    sid = "selftest-social-zero-" + uuid.uuid4().hex[:8]
    p = write_fixture([LAUNCH.format(sid=sid, task="social-batch-weekly"),
                       json.dumps(social_failure)])
    rc, out = run_hook({"hook_event_name": "Stop", "session_id": sid,
                         "transcript_path": p, "stop_hook_active": False},
                        env=unreachable_db_env())
    digest = subprocess.run([PY, os.path.join(REPO, "hooks", "job-watchdog-digest.py")],
                            input="{}", text=True, capture_output=True, timeout=30,
                            env=dict(unreachable_db_env(), CARR_JOB_ROOT=PRIVATE_ROOT))
    context = json.loads(digest.stdout)["hookSpecificOutput"]["additionalContext"] if digest.stdout else ""
    check("a social batch reporting zero drafts reaches the existing watchdog digest",
          rc == 0 and digest.returncode == 0 and "zero drafts" in context,
          out + digest.stderr + context)
    os.unlink(p)

    print(f"\nTIER 1: {len(PASSES)} passed, {len(FAILURES)} failed")

    # ── TIER 2 — only with a real (staging) DATABASE_URL already set ────
    if not os.environ.get("DATABASE_URL"):
        print("\nTIER 2 SKIPPED — no DATABASE_URL in the environment. Run via:\n"
              "  .venv/bin/python tools/db-tap.py --project staging run "
              "ops/scheduled-run-record-selftest.py\nto exercise the real "
              "ops.run write path against staging.")
    else:
        print("\nTIER 2 — real write against the database named by DATABASE_URL "
              "(intended: staging)\n")
        run_tier2()

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    # The private repo root is throwaway state; nothing downstream reads it.
    shutil.rmtree(PRIVATE_ROOT, ignore_errors=True)
    if FAILURES:
        print("\nSELFTEST NOT MET:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SELFTEST MET: a scheduled-task session's own outcome becomes a durable "
          "ops.run row, deterministically, without a live database corrupting the run.")
    return 0


def run_tier2() -> None:
    try:
        import psycopg
    except ImportError:
        check("psycopg importable for tier 2", False, "pip install 'psycopg[binary]'")
        return

    owner = os.environ["DATABASE_URL"]
    probe_key = "sched-probe-" + uuid.uuid4().hex[:8]
    corr = uuid.uuid4()

    # The recorder uses the routine identity. Mint only an isolated staging identity.
    staging_jobs = load_module_from_path(
        "staging_jobs_dsn", os.path.join(REPO, "tools", "staging_jobs_dsn.py"))
    try:
        jobs_dsn = staging_jobs.mint(owner)
    except staging_jobs.StagingJobsRefusal as exc:
        check("tier 2 runs against isolated staging, with a usable carr_jobs identity",
              False, str(exc))
        return
    routine = staging_jobs.routine_env(os.environ, jobs_dsn)
    check("the recorder subprocess gets the jobs credential and no broader one — "
          "the same shape bin/routine-credential-env.sh gives it in production",
          "DATABASE_URL" not in routine
          and routine.get("CARR_DB_JOBS_URL") == jobs_dsn,
          f"DATABASE_URL present={'DATABASE_URL' in routine}")

    # The suite's own connection stays the OWNER's: carr_jobs may not insert a
    # service and may not delete anything, so registering and cleaning up the
    # probe is not work the routine identity can or should do.
    with psycopg.connect(owner, autocommit=True) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """insert into ops.service (key, name, family, criticality, owner_actor)
                   values (%s, 'Program 3 scheduled-run-record probe', 'Local Mac edge',
                           'low', 'joe')
                   returning id""",
                (probe_key,))
            service_id = fetch_one(cur, "the inserted probe service's id")[0]
            cur.execute(
                """insert into ops.service_environment (service_id, environment,
                       expected_cadence_seconds, cadence_grace_seconds)
                   values (%s, 'production', 604800, 172800)""",
                (service_id,))

            # Exercise the same public recording function as the hook.
            p = fixture_gate_denied(task=probe_key)
            proc = subprocess.run(
                [PY, "-c", "import sys; from lib.scheduled_run import record_from_transcript; "
                 "result = record_from_transcript(sys.argv[1], correlation=sys.argv[2]); "
                 "print(result['stderr']); sys.exit(0 if result['recorded'] else 1)", p, str(corr)],
                capture_output=True, text=True, timeout=30, env=dict(routine), cwd=REPO)
            os.unlink(p)
            check("tier 2: the recording library succeeded against isolated staging",
                  proc.returncode == 0, proc.stderr[-500:])

            cur.execute(
                """select service_id, environment, run_key, state, failure_class, detail
                     from ops.run where correlation_id = %s""",
                (corr,))
            row = cur.fetchone()
            check("tier 2: exactly one row landed in ops.run", row is not None)
            if row:
                sid, env, key, state, fc, detail = row
                check("tier 2: service_id matches the probe service", sid == service_id)
                check("tier 2: environment defaulted to production", env == "production")
                check("tier 2: run_key is the fixed RUN_KEY", key == RUN_KEY)
                check("tier 2: state is failed (gate denial fixture)", state == "failed")
                check("tier 2: failure_class is gate_denied", fc == "gate_denied")
                check("tier 2: a failed run always carries a failure_class "
                      "(the database itself refuses one without it)", fc is not None)
        finally:
            # Explicit cleanup — ops-record.py's connections are autocommit
            # (see tools/ops-record.py connect()), so there is no transaction
            # to roll back; deleting what we inserted IS the isolation.
            cur.execute("delete from ops.run where correlation_id = %s", (corr,))
            cur.execute("delete from ops.service_environment where service_id = "
                        "(select id from ops.service where key = %s)", (probe_key,))
            cur.execute("delete from ops.service where key = %s", (probe_key,))
            print("  (tier 2 probe rows deleted)")


if __name__ == "__main__":
    sys.exit(main())
