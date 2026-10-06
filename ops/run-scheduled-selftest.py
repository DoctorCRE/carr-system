#!/usr/bin/env python3
"""ops/run-scheduled-selftest.py — the acceptance test for bin/run-scheduled.sh,
the generic recording wrapper that closes Program 4's first gap.

WHAT THE GAP WAS, measured 2026-08-14. `tools/ops-record.py health` showed 21 of
its 25 registered service/environment rows at "last seen never" — only
nightly-record-layer and social-batch-weekly healthy. Not because those jobs
were down; they run on schedule. Only bin/nightly.sh and bin/smoke-and-record.sh
ever called `ops-record run`, so the seven other launchd jobs (rules-refresh,
partner-ping, capture-poll, local-briefs, notes-sweep, recordings-purge,
cc-version-sentinel) invoked their script directly and a failure in any of them
was durable NOWHERE. Program 4's gate reads "forced job failure is durable and
actionable"; for those seven it was neither. This wrapper closes those seven;
the rest of the 21 are the Claude Code scheduled tasks (a Stop hook's job, not a
wrapper's) and two staging rows nothing observes yet.

WHY A WRAPPER RATHER THAN SEVEN EDITS. The seven scripts are zsh, sh and python,
written by different hands over months. Teaching each one to record would create
seven copies of the same recording decision and guarantee they drift — rule
a8c55a47 in the other direction. One wrapper is one implementation, and the
launchd plist is the only thing that changes per job.

THE ONE DESIGN RULE THIS FILE EXISTS TO HOLD: THE WRAPPER IS TRANSPARENT.
It never changes what the job does, what the job prints, or what the job's exit
code says. A wrapper that can turn a passing job red is worse than no recording
at all, because it puts the observer in the failure path of the thing observed.
Every tier-1 check below is some restatement of that sentence.

TESTED THROUGH A LINE PRODUCTION ALSO WRITES. The wrapper appends one provenance
line to out/run-scheduled.log on EVERY run, carrying the state it derived and
the exact recorder argv it built. This file asserts against that line. It does
NOT introduce a test-only seam — an injectable recorder path, a dry-run flag, a
mock — and that is deliberate: on 2026-08-14 the settings-change gate shipped
with two defects, and both were "a test that exercised a path production never
takes" (team loop T75). The line the test reads is the line the job writes at
02:05, so a test that passes proves something about the real thing.

TWO TIERS, matching ops/scheduled-run-record-selftest.py:

  TIER 1 (always runs; no DB, no credential). Drives the REAL wrapper as a
  subprocess against real child commands, with a DELIBERATELY UNREACHABLE
  DATABASE_URL. That is not a degraded mode being tolerated — it is the exact
  production path bin/nightly.sh already handles as EX_CONFIG, so proving the
  wrapper stays transparent while the recorder refuses is proving the case that
  actually happens on a Mac whose credential has not loaded yet.

  TIER 2 (only when DATABASE_URL is already set — i.e. run through
  `tools/db-tap.py --project staging run ops/run-scheduled-selftest.py`, never
  bare). Registers a throwaway probe service, runs a deliberately failing job
  through the real wrapper, reads the row back out of ops.run, and DELETES it
  before exiting. It never runs against production, and since 2026-08-18 that is
  CHECKED rather than assumed: tools/staging_jobs_dsn.py proves the DSN
  addresses the isolated staging endpoint before the tier touches anything.

  The wrapper's child gets CARR_DB_JOBS_URL and no broader credential, which is
  the shape bin/routine-credential-env.sh hands it in production. It has to:
  since PR #288 `ops-record.py run` is connect("routine") and does not look at
  DATABASE_URL at all. Passing the ambient environment instead let
  ops-record.py's own db.env setdefault supply the PRODUCTION jobs DSN, which is
  exactly what broke this tier between 2026-08-16 and 2026-08-18.

RUN IT:
    python3 ops/run-scheduled-selftest.py
    tools/db-tap.py --project staging run ops/run-scheduled-selftest.py
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.loadpy import load_module_from_path  # noqa: E402

WRAPPER = os.path.join(REPO, "bin", "run-scheduled.sh")
LOG = os.path.join(REPO, "out", "run-scheduled.log")
OPS_RECORD = load_module_from_path("run_scheduled_ops_record",
                                    os.path.join(REPO, "tools", "ops-record.py"))
DEAD_DSN = "postgresql://carr_jobs:probe@127.0.0.1:1/nonexistent"

# One throwaway spool per suite run. The wrapper's recorder is tools/ops-spool.py
# (2026-08-18), and its default spool file lives under out/ — which is shared
# with production state, the exact hazard CARR_RUN_SCHEDULED_STATE_DIR already
# guards against for throttle stamps. Every drive below overrides it here.
TMP_SPOOL = os.path.join(
    tempfile.mkdtemp(prefix="carr-selftest-spool-"), "spool.sqlite3")


def spool_rows(run_key: str, db: str = TMP_SPOOL) -> list:
    """Rows queued in the throwaway spool for one run key."""
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "select service, run_key, state, argv from spool where run_key = ?",
            (run_key,)).fetchall()
    finally:
        conn.close()

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


# ── tier 1 ───────────────────────────────────────────────────────────────────

def unreachable_env() -> dict[str, str]:
    """The environment a Mac has before its DB credential loads.

    Port 1 on loopback refuses instantly rather than hanging, so a suite that
    runs on every push does not pay a connect timeout nine times over.

    EVERY name the recorder reads is SET to that dead port rather than deleted,
    and the list comes from ops-record.py's own credential_names() so it cannot
    drift. Deleting was the bug. This helper used to point DATABASE_URL at a
    dead port and unset CARR_DB_JOBS_URL, which blinded the recorder completely
    while `run` was connect("write") — DATABASE_URL was that mode's first
    choice. PR #288 made `run` connect("routine"), which reads CARR_DB_JOBS_URL
    and nothing else, so ops-record.py's _load_db_env() quietly re-supplied the
    PRODUCTION jobs DSN by setdefault and every "unreachable database" check
    below spent 2026-08-16 to 2026-08-18 authenticating against production. It
    wrote nothing here — every service key in tier 1 is a carr-selftest-* key
    production has never registered, so the recorder refused EX_CONFIG — but
    ops/scheduled-run-record-selftest.py, whose fixtures name REAL services, was
    landing two fabricated rows in production's ledger per run.

    The username stays carr_jobs so routine mode's own credential-shape check
    passes and the tier proves what it claims to: the CONNECTION fails, not the
    credential's spelling.

    THE SPOOL SITS BEHIND ALL OF THAT (2026-08-18). Since bin/run-scheduled.sh
    records through tools/ops-spool.py, an unreachable database is no longer a
    failed recording: a succeeded row queues locally and recorder_exit is 0,
    because the row is durable in the queue whatever the network is doing. That
    makes blinding the credentials MORE load-bearing rather than less — a
    failed/timed_out/cancelled row is tried against the ledger DIRECTLY before
    it ever reaches the queue, so a live jobs credential here would put tier 1's
    deliberate failures on production's doorstep. CARR_RUN_SPOOL_DB is pinned to
    a throwaway file for the same reason CARR_RUN_SCHEDULED_STATE_DIR is: the
    default lives under out/, which is production state shared with every
    worktree. broken_recording_env() below is the state where recording really
    does fail — spool unwritable too.
    """
    env = dict(os.environ)
    for name in OPS_RECORD.credential_names():
        env[name] = DEAD_DSN
    env["CARR_RUN_SPOOL_DB"] = TMP_SPOOL
    for leak in ("CARR_DB_URL", "PGSERVICE"):
        env.pop(leak, None)
    return env


def broken_recording_env() -> dict[str, str]:
    """The state in which recording GENUINELY fails now: the database is
    unreachable AND the local spool cannot be written either (its path sits
    under a file, which mkdir cannot create a directory inside). This is what
    recorder_exit=nonzero means since the spool: not 'the network wobbled'
    but 'this row is durable nowhere'."""
    env = unreachable_env()
    env["CARR_RUN_SPOOL_DB"] = os.path.join(os.path.devnull, "spool.sqlite3")
    return env


def tail_line(run_key: str) -> str:
    """The wrapper's own provenance line for this run key — last one wins."""
    try:
        with open(LOG) as fh:
            hits = [ln.rstrip("\n") for ln in fh if f"key={run_key} " in ln]
    except FileNotFoundError:
        return ""
    return hits[-1] if hits else ""


def field(line: str, name: str) -> str:
    m = re.search(rf"\b{re.escape(name)}=(\S*)", line)
    return m.group(1) if m else ""


def drive(run_key: str, script: str, env: Optional[dict[str, str]] = None,
          cwd: Optional[str] = None) -> tuple[Any, str]:
    """Run the real wrapper over a real child, return (proc, provenance line)."""
    proc = subprocess.run(
        [WRAPPER, "carr-selftest-probe", run_key, "/bin/sh", "-c", script],
        capture_output=True, text=True, timeout=120,
        env=env if env is not None else unreachable_env(),
        cwd=cwd or REPO,
    )
    return proc, tail_line(run_key)


def tier1() -> None:
    print("\nTIER 1 — the wrapper is transparent (no DB, unreachable credential)")

    check("the wrapper exists and is executable",
          os.access(WRAPPER, os.X_OK), WRAPPER)
    if not os.access(WRAPPER, os.X_OK):
        return

    # ── exit-code pass-through, the whole point ──────────────────────────────
    # A job's exit code is what launchd, and every human reading a log, treats
    # as the truth about that job. The wrapper must never author it.
    for rc, state in ((0, "succeeded"), (3, "failed"), (1, "failed")):
        proc, line = drive(f"selftest.exit{rc}", f"exit {rc}")
        check(f"child exit {rc} passes through untouched",
              proc.returncode == rc, f"got {proc.returncode}")
        check(f"child exit {rc} is recorded as state={state}",
              field(line, "state") == state, line or "(no provenance line)")

    # ── EX_CONFIG is a skip, not a failed night ──────────────────────────────
    # Same convention bin/nightly.sh and bin/smoke-and-record.sh already hold:
    # a step that ran, found a credential absent, wrote nothing and said so did
    # not FAIL. Alarming nightly until someone pastes a token is how the smoke
    # suite was lost the first time (see bin/smoke-and-record.sh's re-arm note).
    proc, line = drive("selftest.exit78", "exit 78")
    check("exit 78 (EX_CONFIG) passes through as 78",
          proc.returncode == 78, f"got {proc.returncode}")
    check("exit 78 is recorded as state=skipped, NOT failed",
          field(line, "state") == "skipped", line)

    # ── a killed job is not a failed job ─────────────────────────────────────
    # The Mac sleeping mid-job is the documented 2026-08-14 failure. Recording
    # "failed" for a job the machine killed sends whoever reads it hunting for
    # a bug in the job.
    proc, line = drive("selftest.sigkill", "kill -9 $$")
    check("a SIGKILLed child reports 137 to the caller",
          proc.returncode == 137, f"got {proc.returncode}")
    check("a SIGKILLed child is recorded as timed_out, not failed",
          field(line, "state") == "timed_out", line)

    proc, line = drive("selftest.sigterm", "kill -15 $$")
    check("a SIGTERMed child is recorded as cancelled, not failed",
          field(line, "state") == "cancelled", line)

    # ── the recorder is never in the job's failure path ──────────────────────
    # Every tier-1 run above used an unreachable database. Since the
    # 2026-08-18 spool that is no longer a recording failure: the row queues
    # locally and recorder_exit is 0, because "recorded" now means durable —
    # landed or queued with a scheduled path to ops.run. Assert both halves:
    # the DB-less success, and the row actually sitting in the spool.
    proc, line = drive("selftest.recorder-down", "exit 0")
    check("with the database unreachable, recording still succeeds — the row "
          "queues in the local spool (recorder_exit=0)",
          field(line, "recorder_exit") == "0", line)
    check("...and the job still reports success",
          proc.returncode == 0, f"got {proc.returncode}")
    check("...and the row is REALLY in the spool, not merely claimed durable",
          len(spool_rows("selftest.recorder-down")) >= 1,
          f"spool rows: {spool_rows('selftest.recorder-down')!r}")

    # The state in which recording DOES fail now: spool unwritable too. The
    # transparency property is unchanged — the job must not notice even that.
    proc, line = drive("selftest.recorder-broken", "exit 0",
                       env=broken_recording_env())
    check("with the spool ALSO unwritable, the recorder genuinely fails",
          field(line, "recorder_exit") not in ("0", ""), line)
    check("...and the job STILL reports success — recording is never in the "
          "job's failure path", proc.returncode == 0, f"got {proc.returncode}")
    check("...and the failure is logged rather than hidden",
          "recorder_exit=" in line, line)

    # ── output belongs to the job ────────────────────────────────────────────
    # launchd captures the child's stdout/stderr via StandardOutPath. A wrapper
    # that swallows or reorders it silently blinds every existing job log.
    proc, _ = drive("selftest.stdout", "echo CHILD_OUT; echo CHILD_ERR >&2")
    check("child stdout reaches the caller untouched",
          "CHILD_OUT" in proc.stdout, repr(proc.stdout[:200]))
    check("child stderr reaches the caller untouched",
          "CHILD_ERR" in proc.stderr, repr(proc.stderr[:200]))
    check("the recorder's own chatter stays OUT of the job's stdout",
          "ops-record" not in proc.stdout and "ops-spool" not in proc.stdout,
          repr(proc.stdout[:200]))

    # ── the child's world is the child's ─────────────────────────────────────
    # None of the seven plists sets WorkingDirectory, so each script currently
    # starts in launchd's cwd. A wrapper that cd's to REPO first would silently
    # change every relative path those scripts resolve.
    with tempfile.TemporaryDirectory() as tmp:
        real_tmp = os.path.realpath(tmp)
        proc, _ = drive("selftest.cwd", "pwd", cwd=real_tmp)
        check("the child inherits the caller's cwd, not the repo root",
              os.path.realpath(proc.stdout.strip() or "/") == real_tmp,
              f"child saw {proc.stdout.strip()!r}, expected {real_tmp!r}")

    # ── arguments survive ────────────────────────────────────────────────────
    # notes-sweep is invoked as `notes-sweep-post.sh --scheduled`, and that flag
    # is what gates it to weekday business hours. A wrapper that drops or
    # re-splits trailing arguments would make it run at 3am.
    proc = subprocess.run(
        [WRAPPER, "carr-selftest-probe", "selftest.args",
         "/bin/sh", "-c", 'printf "%s|" "$@"', "_", "one", "two three", "--flag"],
        capture_output=True, text=True, env=unreachable_env(), cwd=REPO)
    check("child arguments pass through intact, including one carrying a space",
          proc.stdout.strip() == "one|two three|--flag|", repr(proc.stdout))

    # ── the run key is not rewritten ─────────────────────────────────────────
    # bin/nightly.sh DERIVES its key from a label and documents the tradeoff.
    # This wrapper is handed the key explicitly, so it must record it verbatim:
    # a mangled key silently starts a new history and orphans the old one.
    _, line = drive("selftest.key.with.dots-and-dashes", "exit 0")
    check("the run key is recorded verbatim, dots and dashes included",
          field(line, "key") == "selftest.key.with.dots-and-dashes", line)

    # ── the recorder argv is the contract ────────────────────────────────────
    # This is what makes the provenance line worth asserting on: it carries the
    # exact call the wrapper made, so a change in flag names cannot pass this
    # suite while quietly writing nothing.
    _, line = drive("selftest.argv", "exit 5")
    argv = line.split("argv=", 1)[1] if "argv=" in line else ""
    for flag in ("--service carr-selftest-probe", "--key selftest.argv",
                 "--state failed", "--exit-code 5",
                 "--source-kind wrapper", "--source-ref bin/run-scheduled.sh"):
        check(f"recorder argv carries {flag!r}", flag in argv, argv[:400])
    check("a failed run carries a failure-class "
          "(ops.run's own constraint refuses one without it)",
          "--failure-class" in argv, argv[:400])
    check("a succeeded run does NOT invent a failure-class",
          "--failure-class" not in (
              tail_line("selftest.exit0").split("argv=", 1) + [""])[1],
          tail_line("selftest.exit0")[:400])

    # ── it joins a journey rather than starting a lone one ───────────────────
    # bin/smoke-and-record.sh inherits CARR_CORRELATION_ID for exactly this
    # reason: a job a nightly chain launched should trace with that chain.
    env = unreachable_env()
    env["CARR_CORRELATION_ID"] = "11111111-2222-3333-4444-555555555555"
    _, line = drive("selftest.corr", "exit 0", env=env)
    check("an inherited CARR_CORRELATION_ID is passed to the recorder",
          "--correlation 11111111-2222-3333-4444-555555555555"
          in line.split("argv=", 1)[-1], line[:400])

    env2 = unreachable_env()
    env2.pop("CARR_CORRELATION_ID", None)
    _, line = drive("selftest.nocorr", "exit 0", env=env2)
    check("...and no correlation flag is invented when none was inherited",
          "--correlation" not in line.split("argv=", 1)[-1], line[:400])

    # ── misuse is loud and harmless ──────────────────────────────────────────
    # A plist edited wrong must fail visibly at install time, not run a job
    # under the wrong key for a month.
    for bad, why in (([], "no arguments"),
                     (["only-a-service"], "a service but no run key"),
                     (["svc", "key"], "a service and key but no command")):
        proc = subprocess.run([WRAPPER] + bad, capture_output=True,
                              text=True, env=unreachable_env(), cwd=REPO)
        check(f"usage error on {why}: exits 64 (EX_USAGE)",
              proc.returncode == 64, f"got {proc.returncode}")
        check(f"usage error on {why}: says so on stderr",
              "usage" in proc.stderr.lower(), repr(proc.stderr[:200]))

    # ── a missing command is the job's failure, reported as one ──────────────
    proc = subprocess.run(
        [WRAPPER, "carr-selftest-probe", "selftest.enoent",
         "/nonexistent/definitely-not-here"],
        capture_output=True, text=True, env=unreachable_env(), cwd=REPO)
    check("an unrunnable command exits 127, the shell's own convention",
          proc.returncode == 127, f"got {proc.returncode}")
    check("...and is still recorded rather than vanishing",
          field(tail_line("selftest.enoent"), "state") == "failed",
          tail_line("selftest.enoent"))


# ── tier 1b: --heartbeat-interval / --also-heartbeat (Program 4 follow-up) ───
# partner-ping (2 min) and capture-poll (5 min) would otherwise flood ops.run
# with ~1000 succeeded rows/day. --heartbeat-interval throttles a job's own
# succeeded row to at most one per interval, failures always record and clear
# the throttle, and --also-heartbeat records a second, independent row for
# carr-local-edge-node (PROP-010's local-Mac presence signal) riding the same
# wake. Same no-mock discipline as tier1() above: this drives the REAL
# wrapper as a subprocess and reads the REAL provenance line back — no
# injectable recorder. Every check here keys off a fresh uuid4 service or run
# key, so exact substring/line matching is correct regardless of how much
# history out/run-scheduled.log already carries (the failure mode a windowed
# line-diff hit during this package's own development).
#
# STAMP-ONLY-ON-A-LANDED-ROW, observed live 2026-08-14. The first cut of this
# throttle stamped the state file after every ATTEMPT, regardless of whether
# the recorder actually reached the database. A dev-iteration selftest run
# whose recorder pointed at an unreachable DB (recorder_exit=1, no row landed)
# stamped carr-local-edge-node's state file in the SHARED
# out/run-scheduled-state — out/ is symlinked into every worktree — so the
# FIRST REAL heartbeat after install came up 'throttled' against a row that
# never existed, and health read "last seen never". General form: a recorder
# outage would silence every subsequent fire for the interval, which is
# exactly the silence this wrapper's own header promises never to produce.
# Fixed in bin/run-scheduled.sh: both stamp sites now write only when
# recorder_exit -eq 0.
#
# WHAT THAT MEANS FOR THESE TESTS, and why they no longer test "does a second
# fire throttle" by driving two fires back to back with a broken recorder:
# under broken_recording_env() recorder_exit is always nonzero, so nothing
# ever stamps, and a test built on "fire twice, expect throttled" would now
# correctly get "recorded" both times — which is not a bug, it is the WRITE
# side of the fix, tested in (a) below. (Since the 2026-08-18 spool, plain
# unreachable_env() is NOT that state: the row queues locally, that IS
# durable, and stamping on it is correct — also asserted in (a).) The
# THROTTLE's READ side (given a stamp, does a fresh one skip and a stale one
# not) still needs proving, and is proven in (b) by pre-seeding the state
# file directly — no mock, no injected recorder: a real file, in the real
# format the wrapper itself writes (`date -u +%s`), read by the real wrapper.
# Every scenario below sets CARR_RUN_SCHEDULED_STATE_DIR to a per-test
# mkdtemp via drive_flags()'s state_dir parameter, so nothing here can ever
# touch the shared out/run-scheduled-state again — the exact mechanism of
# the live incident.

def drive_flags(flags: list, service: str, run_key: str, script: str,
                 env: Optional[dict[str, str]] = None,
                 state_dir: Optional[str] = None) -> Any:
    """Run the real wrapper with leading flags, service, run key, then an
    `/bin/sh -c <script>` child — returns the CompletedProcess. state_dir, if
    given, points CARR_RUN_SCHEDULED_STATE_DIR at a throwaway directory: the
    default out/run-scheduled-state is symlinked into every worktree, so a
    test that stamps there stamps production state for whatever real job
    shares that service/run key — exactly the live incident this section's
    header describes. Every throttle test below passes one."""
    use_env = dict(env if env is not None else unreachable_env())
    if state_dir is not None:
        use_env["CARR_RUN_SCHEDULED_STATE_DIR"] = state_dir
    return subprocess.run(
        [WRAPPER, *flags, service, run_key, "/bin/sh", "-c", script],
        capture_output=True, text=True, timeout=120,
        env=use_env, cwd=REPO)


def last_line_for(run_key: str, service: str) -> str:
    """The wrapper's own provenance line naming BOTH this run key and this
    service — last one wins. Distinct from tail_line() (run key alone)
    because --also-heartbeat's line always carries the fixed run key
    launchd.heartbeat, so multiple heartbeat services in one test run need
    the service name to disambiguate which line is whose."""
    try:
        with open(LOG) as fh:
            hits = [ln.rstrip("\n") for ln in fh
                    if f"key={run_key} " in ln and f"service={service} " in ln]
    except FileNotFoundError:
        return ""
    return hits[-1] if hits else ""


def stamp_path(state_dir: str, service: str, run_key: str) -> str:
    """The exact path bin/run-scheduled.sh itself computes for a state
    stamp: $STATE_DIR/$SERVICE.$RUN_KEY.last-success (primary) or
    $STATE_DIR/$SERVICE.launchd.heartbeat.last-success (heartbeat, since
    HB_RUN_KEY is always the fixed string launchd.heartbeat)."""
    return os.path.join(state_dir, f"{service}.{run_key}.last-success")


def seed_stamp(path: str, age_seconds: int = 0) -> None:
    """Write a state file directly, the same one-line epoch-seconds format
    `date -u +%s` produces — the no-mock way to test the throttle's READ
    side without depending on a recorder call ever landing."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(int(time.time()) - age_seconds))


def tier1_throttle() -> None:
    print("\nTIER 1b — --heartbeat-interval and --also-heartbeat throttle "
          "without ever hiding a failure")

    # ── backward compatibility: no flags behaves exactly as tier1 proved ────
    # (already covered end-to-end by tier1() above, which passes zero flags on
    # every call — restated here as the explicit contract this section adds
    # flags on TOP of, never in place of.)
    rk0 = "selftest.noflags." + uuid.uuid4().hex[:8]
    drive_flags([], "carr-selftest-probe", rk0, "exit 0")
    check("with no --heartbeat-interval, record_action is always 'recorded' "
          "— the new field never changes behavior for the six jobs that "
          "never pass it", field(tail_line(rk0), "record_action") == "recorded",
          tail_line(rk0))

    # ── (a) a failed recording never arms the throttle ───────────────────────
    # Every check in this block uses broken_recording_env() — since the
    # 2026-08-18 spool, an unreachable database alone no longer fails a
    # recording (the row queues locally and THAT is durable, so stamping is
    # then correct); genuine failure means the spool is unwritable too. In
    # that state recorder_exit is nonzero and, under the fix, the state file
    # must never appear no matter how many times this fires. This is the
    # WRITE side of the fix: the live incident happened exactly here.
    print("\n  (a) a failed recording never arms the throttle")
    dir_a = tempfile.mkdtemp(prefix="carr-selftest-throttle-a-")
    svc_a = "carr-selftest-probe"
    rk_a = "selftest.throttle.nofile." + uuid.uuid4().hex[:8]
    file_a = stamp_path(dir_a, svc_a, rk_a)

    p1 = drive_flags(["--heartbeat-interval", "1800"], svc_a, rk_a, "exit 0",
                      env=broken_recording_env(), state_dir=dir_a)
    check("first success still records the attempt (state=succeeded)",
          field(tail_line(rk_a), "state") == "succeeded", tail_line(rk_a))
    check("...but the recorder genuinely could not capture the row anywhere",
          field(tail_line(rk_a), "recorder_exit") not in ("0", ""), tail_line(rk_a))
    check("...and exit code passes through untouched", p1.returncode == 0,
          f"got {p1.returncode}")
    check("a failed recording writes NO state file — the fix's whole point",
          not os.path.exists(file_a), f"exists={os.path.exists(file_a)}")

    p2 = drive_flags(["--heartbeat-interval", "1800"], svc_a, rk_a, "exit 0",
                      env=broken_recording_env(), state_dir=dir_a)
    check("with no state file armed, the SECOND success ALSO records — it "
          "is not, and must not be, throttled by a recording that never "
          "landed", field(tail_line(rk_a), "record_action") == "recorded",
          tail_line(rk_a))
    check("...that second attempt exits 0 untouched too", p2.returncode == 0,
          f"got {p2.returncode}")
    check("...and still no state file afterward", not os.path.exists(file_a),
          f"exists={os.path.exists(file_a)}")

    # The other half of the same property, new with the spool: a DB-less but
    # spool-writable recording IS durable, so it DOES arm the throttle now.
    rk_a2 = "selftest.throttle.spoolarms." + uuid.uuid4().hex[:8]
    file_a2 = stamp_path(dir_a, svc_a, rk_a2)
    drive_flags(["--heartbeat-interval", "1800"], svc_a, rk_a2, "exit 0",
                state_dir=dir_a)
    check("a spooled (DB-less) success DOES arm the throttle — queued is "
          "durable, so silence for the interval is honest",
          os.path.exists(file_a2), f"exists={os.path.exists(file_a2)}")

    # ── (b) the throttle's READ side, proven by pre-seeding — no mock ───────
    print("\n  (b) the throttle's read side, proven by pre-seeding the state "
          "file directly")
    dir_b = tempfile.mkdtemp(prefix="carr-selftest-throttle-b-")
    svc_b = "carr-selftest-probe"

    rk_fresh = "selftest.throttle.seed." + uuid.uuid4().hex[:8]
    seed_stamp(stamp_path(dir_b, svc_b, rk_fresh), age_seconds=0)
    drive_flags(["--heartbeat-interval", "1800"], svc_b, rk_fresh, "exit 0",
                state_dir=dir_b)
    check("a FRESH pre-seeded stamp throttles the next success — pure "
          "read-side proof, no recorder success required to arm it",
          field(tail_line(rk_fresh), "record_action") == "throttled",
          tail_line(rk_fresh))

    rk_stale = "selftest.throttle.stale." + uuid.uuid4().hex[:8]
    seed_stamp(stamp_path(dir_b, svc_b, rk_stale), age_seconds=3600)
    drive_flags(["--heartbeat-interval", "1800"], svc_b, rk_stale, "exit 0",
                state_dir=dir_b)
    check("a STALE pre-seeded stamp (older than the 1800s interval) does "
          "NOT throttle", field(tail_line(rk_stale), "record_action") == "recorded",
          tail_line(rk_stale))

    # ── (c) a failure still clears a pre-seeded stamp ────────────────────────
    print("\n  (c) a failure still clears a pre-seeded stamp")
    dir_c = tempfile.mkdtemp(prefix="carr-selftest-throttle-c-")
    svc_c = "carr-selftest-probe"
    rk_c = "selftest.throttle.clear." + uuid.uuid4().hex[:8]
    file_c = stamp_path(dir_c, svc_c, rk_c)
    seed_stamp(file_c, age_seconds=0)

    p3 = drive_flags(["--heartbeat-interval", "1800"], svc_c, rk_c, "exit 9",
                      state_dir=dir_c)
    check("a failure inside a throttle window is still recorded immediately, "
          "not throttled", field(tail_line(rk_c), "record_action") == "recorded"
          and field(tail_line(rk_c), "state") == "failed"
          and "--failure-class exit_9" in tail_line(rk_c), tail_line(rk_c))
    check("...and the failure itself is still the child's own exit code",
          p3.returncode == 9, f"got {p3.returncode}")
    check("...and it clears the PRE-SEEDED stamp — proving the clear works "
          "regardless of how the stamp got there, not only on one this "
          "process itself wrote", not os.path.exists(file_c),
          f"exists={os.path.exists(file_c)}")

    # ── (d) the --also-heartbeat path: the same rules, independently ────────
    print("\n  (d) --also-heartbeat: the same stamp-only-on-a-landed-row "
          "rules apply to the edge-node signal too")

    # (d-a) a failed heartbeat recording never arms its own throttle. Same
    # broken_recording_env() as tier 1b(a): with the spool, only "durable
    # nowhere" counts as a failed recording.
    dir_da = tempfile.mkdtemp(prefix="carr-selftest-hb-a-")
    hb_da = "carr-selftest-edge-" + uuid.uuid4().hex[:8]
    file_da = stamp_path(dir_da, hb_da, "launchd.heartbeat")
    rk_da1 = "selftest.hb.nofile1." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_da],
                "carr-selftest-probe", rk_da1, "exit 0",
                env=broken_recording_env(), state_dir=dir_da)
    hb_line_da1 = last_line_for("launchd.heartbeat", hb_da)
    check("the heartbeat's own first attempt records (state=succeeded) even "
          "though the recorder is broken",
          field(hb_line_da1, "state") == "succeeded", hb_line_da1)
    check("...recorder_exit shows the real failure",
          field(hb_line_da1, "recorder_exit") not in ("0", ""), hb_line_da1)
    check("a failed heartbeat recording writes NO state file",
          not os.path.exists(file_da), f"exists={os.path.exists(file_da)}")

    rk_da2 = "selftest.hb.nofile2." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_da],
                "carr-selftest-probe", rk_da2, "exit 0",
                env=broken_recording_env(), state_dir=dir_da)
    hb_line_da2 = last_line_for("launchd.heartbeat", hb_da)
    check("with no state file armed, the heartbeat records on the NEXT wake "
          "too, instead of wrongly throttling",
          field(hb_line_da2, "record_action") == "recorded", hb_line_da2)

    # (d-b) the heartbeat throttle's own read side, pre-seeded.
    dir_db = tempfile.mkdtemp(prefix="carr-selftest-hb-b-")
    hb_db = "carr-selftest-edge-" + uuid.uuid4().hex[:8]
    seed_stamp(stamp_path(dir_db, hb_db, "launchd.heartbeat"), age_seconds=0)
    rk_db1 = "selftest.hb.seed." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_db],
                "carr-selftest-probe", rk_db1, "exit 0", state_dir=dir_db)
    check("a FRESH pre-seeded heartbeat stamp throttles the next wake's "
          "heartbeat", field(last_line_for("launchd.heartbeat", hb_db),
          "record_action") == "throttled", last_line_for("launchd.heartbeat", hb_db))

    seed_stamp(stamp_path(dir_db, hb_db, "launchd.heartbeat"), age_seconds=3600)
    rk_db2 = "selftest.hb.stale." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_db],
                "carr-selftest-probe", rk_db2, "exit 0", state_dir=dir_db)
    check("a STALE pre-seeded heartbeat stamp does NOT throttle",
          field(last_line_for("launchd.heartbeat", hb_db), "record_action")
          == "recorded", last_line_for("launchd.heartbeat", hb_db))

    # (d-c) the heartbeat throttle is independent of the PRIMARY job's own
    # outcome — there is no "heartbeat failed" state for a primary failure to
    # clear FROM (the heartbeat's own state is always succeeded when it
    # fires), and bin/run-scheduled.sh never clears HB_STATE_FILE on a
    # primary failure by design: doing so would make the edge node's
    # presence signal depend on an unrelated job's health again, exactly the
    # coupling --also-heartbeat exists to avoid. Proven directly: a
    # pre-seeded heartbeat stamp survives a primary job FAILURE untouched.
    dir_dc = tempfile.mkdtemp(prefix="carr-selftest-hb-c-")
    hb_dc = "carr-selftest-edge-" + uuid.uuid4().hex[:8]
    file_dc = stamp_path(dir_dc, hb_dc, "launchd.heartbeat")
    seed_stamp(file_dc, age_seconds=0)
    rk_dc = "selftest.hb.primaryfail." + uuid.uuid4().hex[:8]
    proc_dc = drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_dc],
                           "carr-selftest-probe", rk_dc, "exit 5", state_dir=dir_dc)
    check("the primary job's own failure still passes through untouched",
          proc_dc.returncode == 5, f"got {proc_dc.returncode}")
    check("the primary job's own line records the real failure",
          field(tail_line(rk_dc), "state") == "failed", tail_line(rk_dc))
    hb_line_dc = last_line_for("launchd.heartbeat", hb_dc)
    check("...the heartbeat STILL records succeeded on the same wake — its "
          "presence signal never depends on the wrapped job's own success",
          field(hb_line_dc, "state") == "succeeded", hb_line_dc)
    check("a primary job FAILURE does not clear the heartbeat's own "
          "pre-seeded stamp: it is STILL throttled",
          field(hb_line_dc, "record_action") == "throttled", hb_line_dc)
    check("...the pre-seeded heartbeat stamp file itself is untouched",
          os.path.exists(file_dc), f"exists={os.path.exists(file_dc)}")

    # ── the first heartbeat fire's line shape (unaffected by the fix) ───────
    print("\n  (recap) --also-heartbeat's own provenance line shape")
    dir_shape = tempfile.mkdtemp(prefix="carr-selftest-hb-shape-")
    hb_shape = "carr-selftest-edge-" + uuid.uuid4().hex[:8]
    rk_shape = "selftest.hb.shape." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_shape],
                "carr-selftest-probe", rk_shape, "exit 0", state_dir=dir_shape)
    hb_line_shape = last_line_for("launchd.heartbeat", hb_shape)
    check("--also-heartbeat writes its OWN provenance line, "
          "key=launchd.heartbeat", hb_line_shape != "", hb_line_shape)
    check("...for the named heartbeat service, not the primary job's service",
          field(hb_line_shape, "service") == hb_shape, hb_line_shape)
    check("...recorder argv names the heartbeat service and its fixed key",
          f"--service {hb_shape}" in hb_line_shape
          and "--key launchd.heartbeat" in hb_line_shape, hb_line_shape)


# ── tier 1c: bin/refresh-rules.sh's own exit code (Program 4 follow-up) ──────
# Found while building this package: the script always ended via its trailing
# `tail | mv` log-trim, so its own exit code was always whatever THAT
# returned (0) regardless of a FAIL logged above it — wrapped here, a real
# export failure would still be recorded as succeeded. Fixed by threading an
# EXIT_CODE variable to an explicit `exit $EXIT_CODE` at the bottom. Proven
# WITHOUT ever running a live refresh: CARR_REFRESH_RULES_EXPORT_CMD (a
# test-only hook the script itself defines, inert unless set) substitutes a
# stub for the real network/DB-touching export, and HOME points at an empty
# fixture directory so the script's hardcoded $HOME/carr-system never
# resolves to the real checkout.

def tier1_refresh_rules() -> None:
    print("\nTIER 1c — bin/refresh-rules.sh propagates its own internal "
          "failure honestly")
    script = os.path.join(REPO, "bin", "refresh-rules.sh")
    if not check("bin/refresh-rules.sh exists", os.path.exists(script), script):
        return

    fixture_home = tempfile.mkdtemp(prefix="carr-selftest-refresh-rules-home-")
    os.makedirs(os.path.join(fixture_home, ".config", "carr"), exist_ok=True)
    os.makedirs(os.path.join(fixture_home, "carr-system", "out"), exist_ok=True)
    with open(os.path.join(fixture_home, ".config", "carr", "db.env"), "w") as fh:
        fh.write("# selftest fixture — never read for a real credential\n")

    def make_stub(exit_code: int) -> str:
        stub_dir = tempfile.mkdtemp(prefix="carr-selftest-refresh-rules-stub-")
        stub_path = os.path.join(stub_dir, "stub-export")
        with open(stub_path, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\nexit {exit_code}\n")
        os.chmod(stub_path, 0o755)
        return stub_path

    env = dict(os.environ)
    env["HOME"] = fixture_home
    env["CARR_REFRESH_RULES_EXPORT_CMD"] = make_stub(9)
    proc = subprocess.run(["/bin/zsh", script], capture_output=True,
                           text=True, timeout=30, env=env)
    check("a stubbed export failure makes the SCRIPT's own exit code nonzero "
          "(the bug: it used to always be 0, from the trailing tail/mv chain)",
          proc.returncode != 0, f"rc={proc.returncode}")
    check("...and it carries the REAL failing exit code through (9), not a "
          "generic 1 — so bin/run-scheduled.sh's failure_class is accurate",
          proc.returncode == 9, f"rc={proc.returncode}")
    log_path = os.path.join(fixture_home, "carr-system", "out", "rules-refresh.log")
    log_text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
    check("the durable log still names the failure (FAIL rules refresh rc=9)",
          "FAIL rules refresh rc=9" in log_text, log_text)

    env["CARR_REFRESH_RULES_EXPORT_CMD"] = make_stub(0)
    proc = subprocess.run(["/bin/zsh", script], capture_output=True,
                           text=True, timeout=30, env=env)
    check("a stubbed export SUCCESS still exits 0 — the fix does not touch "
          "the success path", proc.returncode == 0, f"rc={proc.returncode}")


# ── tier 2 ───────────────────────────────────────────────────────────────────

def tier2() -> None:
    owner = os.environ.get("DATABASE_URL", "")
    if not owner:
        print("\nTIER 2 — skipped (no DATABASE_URL; run through "
              "tools/db-tap.py --project staging to include it)")
        return

    print("\nTIER 2 — a forced failure lands a real, readable ops.run row")
    try:
        import psycopg
    except ImportError:
        print("  (psycopg unavailable — tier 2 skipped)")
        return

    # THE RECORDER STOPPED READING DATABASE_URL, and for a while nothing here
    # noticed. PR #288 (cd3d7386, 2026-08-16) moved `ops-record.py run` to
    # connect("routine"), which reads CARR_DB_JOBS_URL and only that. db-tap
    # exports DATABASE_URL and only that, so ops-record.py's _load_db_env()
    # fell through to ~/.config/carr/db.env and its setdefault handed the
    # wrapper the PRODUCTION jobs credential. Every row this tier believed it
    # was writing to staging was aimed at production's registry instead,
    # refused EX_CONFIG because the probe service is registered only here, and
    # all three read-backs below failed (measured 2026-08-18).
    #
    # So mint the identity the recorder now demands. tools/staging_jobs_dsn.py
    # proves the target is the isolated staging endpoint BEFORE it alters
    # anything, which also makes "tier 2 never runs against production" a
    # checked property rather than the convention it was.
    staging_jobs = load_module_from_path(
        "staging_jobs_dsn", os.path.join(REPO, "tools", "staging_jobs_dsn.py"))
    try:
        jobs_dsn = staging_jobs.mint(owner)
    except staging_jobs.StagingJobsRefusal as exc:
        check("tier 2 runs against isolated staging, with a usable carr_jobs identity",
              False, str(exc))
        return
    routine = staging_jobs.routine_env(os.environ, jobs_dsn)
    check("the wrapped child gets the jobs credential and no broader one — the "
          "same shape bin/routine-credential-env.sh gives it in production",
          "DATABASE_URL" not in routine
          and routine.get("CARR_DB_JOBS_URL") == jobs_dsn,
          f"DATABASE_URL present={'DATABASE_URL' in routine}")

    probe_key = "carr-run-scheduled-probe"
    run_key = "selftest.forced-failure"
    corr = "9f9f9f9f-1111-4222-8333-444444444444"
    corrs_to_clean = [corr]

    # The suite's own connection stays the OWNER's: carr_jobs may not insert a
    # service, and may not delete anything at all, so registering and cleaning
    # up the probe is not work the routine identity can or should do.
    conn = psycopg.connect(owner, autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(
            """insert into ops.service (key, name, purpose, family, criticality,
                                        owner_actor, runtime)
               values (%s, 'run-scheduled selftest probe',
                       'throwaway; deleted at the end of this run',
                       'Local Mac edge', 'low', 'joe', 'launchd')
               on conflict (key) do update set name = excluded.name
               returning id""", (probe_key,))
        inserted = cur.fetchone()
        assert inserted is not None, "the probe service insert returned no id"
        service_id = inserted[0]
        cur.execute(
            """insert into ops.service_environment (service_id, environment)
               values (%s, 'production') on conflict do nothing""", (service_id,))

        # A throwaway spool for tier 2 as well: the wrapper records through
        # tools/ops-spool.py now, and its default spool file lives under the
        # shared out/. A FAILED child goes to the ledger DIRECT (the spool
        # tries failure states against ops-record first), so this forced
        # failure must land with NO flush step — that immediacy is itself
        # part of the contract and is what this block now proves.
        tier2_spool = os.path.join(
            tempfile.mkdtemp(prefix="carr-selftest-tier2-spool-"), "spool.sqlite3")

        # `routine`, not the ambient environment: the wrapper's child gets the
        # STAGING jobs credential and no broader one, which is both the shape
        # production hands it and the thing that keeps this tier off
        # production's registry.
        env = dict(routine)
        env["CARR_CORRELATION_ID"] = corr
        env["CARR_RUN_SPOOL_DB"] = tier2_spool
        proc = subprocess.run(
            [WRAPPER, probe_key, run_key, "/bin/sh", "-c", "exit 9"],
            capture_output=True, text=True, env=env, cwd=REPO, timeout=120)
        check("the wrapper still reports the child's 9 with a live database",
              proc.returncode == 9, f"got {proc.returncode}")

        cur.execute(
            """select run_key, state, exit_code, failure_class, source_kind,
                      source_ref, started_at, ended_at
                 from ops.run where correlation_id = %s""", (corr,))
        row = cur.fetchone()
        check("exactly one row landed in ops.run — DIRECT, no flush ran: a "
              "failure does not wait for the spool", row is not None)
        if row:
            key, state, code, fclass, skind, sref, started, ended = row
            check("run_key is verbatim", key == run_key, str(key))
            check("state is failed", state == "failed", str(state))
            check("exit_code is the child's 9", code == 9, str(code))
            check("failure_class names the exit", fclass == "exit_9", str(fclass))
            check("source_kind is 'wrapper', which is what it is",
                  skind == "wrapper", str(skind))
            check("source_ref points at the wrapper",
                  sref == "bin/run-scheduled.sh", str(sref))
            check("the row carries a real elapsed window, not a single instant",
                  started is not None and ended is not None and ended >= started,
                  f"{started} -> {ended}")

        # ── the throttle's POSITIVE path, end to end against a real database ─
        # Tier 1b proves the throttle's read side by pre-seeding a state file
        # directly (unreachable_env() means no recorder call there ever
        # lands, by design). This is the other half, the one tier 1 cannot
        # reach: a REAL recorder success writes the stamp, and the very next
        # fire is throttled BECAUSE of that landed row — the full loop the
        # live incident broke, proven here with a real ops.run row, not a
        # synthetic file. Isolated into its own throwaway state dir so this
        # probe can never touch the shared out/run-scheduled-state either.
        print("\n  (throttle positive path) a real landed success stamps, "
              "and the next fire throttles because of it")
        throttle_state_dir = tempfile.mkdtemp(prefix="carr-selftest-tier2-throttle-")
        throttle_run_key = "selftest.throttle.tier2." + uuid.uuid4().hex[:8]
        corr_land = str(uuid.uuid4())
        corr_throttled = str(uuid.uuid4())
        corrs_to_clean += [corr_land, corr_throttled]

        env_land = dict(routine)
        env_land["CARR_CORRELATION_ID"] = corr_land
        env_land["CARR_RUN_SCHEDULED_STATE_DIR"] = throttle_state_dir
        env_land["CARR_RUN_SPOOL_DB"] = tier2_spool
        proc_land = subprocess.run(
            [WRAPPER, "--heartbeat-interval", "1800", probe_key, throttle_run_key,
             "/bin/sh", "-c", "exit 0"],
            capture_output=True, text=True, env=env_land, cwd=REPO, timeout=120)
        check("tier 2 throttle: the first real success exits 0",
              proc_land.returncode == 0, f"got {proc_land.returncode}")

        stamp = os.path.join(throttle_state_dir,
                              f"{probe_key}.{throttle_run_key}.last-success")
        check("tier 2 throttle: a durably QUEUED row stamps the state file — "
              "the stamp means 'this row has a path to the ledger', which "
              "since the spool is true at enqueue time",
              os.path.exists(stamp), f"exists={os.path.exists(stamp)}")

        cur.execute("select count(*) from ops.run where correlation_id = %s",
                    (corr_land,))
        pre_flush = cur.fetchone()
        check("tier 2 throttle: before any flush, the succeeded row is NOT in "
              "ops.run — it is waiting in the spool, which is the whole "
              "autosuspend point", pre_flush is not None and pre_flush[0] == 0,
              str(pre_flush))

        # The flusher is the other half of the new path; run it for real.
        flush = subprocess.run(
            [sys.executable, os.path.join(REPO, "tools", "ops-spool.py"), "flush"],
            capture_output=True, text=True, env=env_land, cwd=REPO, timeout=300)
        check("tier 2 throttle: a real flush over a live database exits 0",
              flush.returncode == 0,
              f"rc={flush.returncode} out={flush.stdout[-300:]!r} "
              f"err={flush.stderr[-300:]!r}")

        cur.execute("select count(*) from ops.run where correlation_id = %s",
                    (corr_land,))
        landed = cur.fetchone()
        check("tier 2 throttle: after the flush, that fire's row landed in "
              "ops.run", landed is not None and landed[0] == 1, str(landed))

        env_throttled = dict(routine)
        env_throttled["CARR_CORRELATION_ID"] = corr_throttled
        env_throttled["CARR_RUN_SCHEDULED_STATE_DIR"] = throttle_state_dir
        env_throttled["CARR_RUN_SPOOL_DB"] = tier2_spool
        proc_throttled = subprocess.run(
            [WRAPPER, "--heartbeat-interval", "1800", probe_key, throttle_run_key,
             "/bin/sh", "-c", "exit 0"],
            capture_output=True, text=True, env=env_throttled, cwd=REPO, timeout=120)
        check("tier 2 throttle: the SECOND immediate fire still exits 0 — "
              "throttling never touches the wrapped exit code",
              proc_throttled.returncode == 0, f"got {proc_throttled.returncode}")

        cur.execute("select count(*) from ops.run where correlation_id = %s",
                    (corr_throttled,))
        throttled_count = cur.fetchone()
        check("tier 2 throttle: the throttled second fire wrote NO ops.run "
              "row at all — the stamp from the first fire worked",
              throttled_count is not None and throttled_count[0] == 0,
              str(throttled_count))
        check("tier 2 throttle: ...and it queued nothing in the spool either",
              len(spool_rows(throttle_run_key, db=tier2_spool)) == 0,
              repr(spool_rows(throttle_run_key, db=tier2_spool)))
    finally:
        # Autocommit, same as tools/ops-record.py's own connections: there is no
        # transaction to roll back, so deleting what we inserted IS the isolation.
        for c in corrs_to_clean:
            cur.execute("delete from ops.run where correlation_id = %s", (c,))
        cur.execute("""delete from ops.service_environment where service_id =
                       (select id from ops.service where key = %s)""", (probe_key,))
        cur.execute("delete from ops.service where key = %s", (probe_key,))
        conn.close()
        print("  (tier 2 probe rows deleted)")


# ─────────────────────────────────────────────────────────────────────────────
# TIER 1 — THE RECEIPT THIS WRAPPER MINTS FOR ITSELF, added 2026-09-11 and
# rewritten 2026-09-12 after review.
#
# WHY THIS SECTION EXISTS. Gate Zero's fourth predecessor step,
# `step:scheduler-active-receipt`, is read by
# mcp-server/src/gate-zero-seam-readers.v5.js, whose `receipt_binding` clause
# wants a run row bound to a receipt. Measured against production on
# 2026-09-11: 28,309 rows in ops.run, 21,894 of them written by
# bin/run-scheduled.sh, and ZERO carrying an evidence_ref. The clause was
# unsatisfiable by construction.
#
# WHAT THE FIRST DRAFT GOT WRONG, and it is the reason for half these checks.
# It took `--evidence-ref-file PATH` and promoted whatever that file held. A
# stale file from last week's run, or a file the child wrote whatever it liked
# into, was then indistinguishable from a receipt minted during THIS run — and
# a binding whose evidence the bound party supplies is not a binding. The
# wrapper now mints the receipt itself: its own clock read after the child
# exits, its own entropy, and the HASH of the run key rather than the key.
#
# WHAT THESE CHECKS DEFEND:
#
#   NOTHING OUTSIDE THE WRAPPER CAN SUPPLY A RECEIPT. There is no flag and no
#   path. A file pre-seeded at the exact path the wrapper computes is
#   overwritten by this run's mint and its content never reaches the row.
#
#   THE DIRECTORY IS NOT CALLER-SELECTABLE, which is the 2026-09-12 correction
#   and the reason this section owns INSTALLATIONS instead of passing paths. It
#   used to be $CARR_RUN_SCHEDULED_STATE_DIR/receipts: whoever set that variable
#   chose the directory the wrapper validated and wrote in, and choosing the
#   parents is choosing the file. It is now derived from the wrapper's own
#   resolved location, every spelling of an override REFUSES, and each hostile
#   directory below is built by installing a copy of this same wrapper in a
#   temporary tree whose out/ is its own.
#
#   ONLY THE WRAPPER'S OWN REGULAR FILE IS WRITTEN, and the check and the write
#   are the same open file rather than the same name looked up twice. One
#   O_CREAT|O_EXCL|O_NOFOLLOW create, then fstat, write, rewind and read back on
#   that descriptor. A symlink at the leaf, a FIFO, a directory, a hard link, a
#   symlink where the receipt DIRECTORY belongs, a directory anyone may write
#   in, and a FIFO swapped in while the mint runs each refuse under their own
#   registered code, follow nothing and block on nothing — and each has a
#   mutation control that deletes the guard and shows the hazard arriving.
#
#   A REFUSAL IS NAMED, NOT ANONYMOUS. The provenance line carries
#   receipt_code=<one of the wrapper's own RECEIPT_CODES>, read here out of the
#   wrapper's source rather than restated, so "this job records no evidence" and
#   "something moved the directory under us" stop looking identical. The codes
#   are swept against the privileged-word union too: a code is an export.
#
#   REJECT, NEVER REPAIR. A run key or service key carrying a carriage return,
#   a newline, a tab, a space or any other byte outside [A-Za-z0-9:._-] mints
#   NO receipt. It is not stripped down to an acceptable shape first: the
#   earliest draft normalized with `tr -d '[:space:]'`, which turned
#   `--state failed --exit-code 1` into a token the whitelist accepted. That is
#   the exact shape of a check that reports green over a broken substrate.
#
#   NO CALLER WORD TRAVELS. A run key of `complete` or `allow-commit-green`
#   yields a receipt of a fixed prefix and hex, in the recorder's argv AND in
#   the provenance line — swept here against the closed privileged-word union.
#
#   AND IT STILL CANNOT FAIL A JOB. Every refusal above records exactly the row
#   this wrapper recorded before receipts existed, and returns the child's own
#   exit code.
# ─────────────────────────────────────────────────────────────────────────────

RECEIPT_SHAPE = re.compile(
    r"^carr-run-receipt:v1:(\d{8}T\d{6}\.\d{3})Z:[0-9a-f]{16}:([0-9a-f]{32})$")

# The closed union the 2026-09-11 standing rule names, swept as a substring.
PRIVILEGED_WORDS = (
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable", "would_", "_if_authoritative",
)


def recorded_argv(run_key: str, db: str) -> list:
    """The argument list the wrapper handed the recorder for one run key."""
    rows = spool_rows(run_key, db=db)
    return json.loads(rows[0][3]) if rows else []


def evidence_ref_of(run_key: str, db: str):
    """The --evidence-ref value in that argv, or None when the flag is absent."""
    argv = recorded_argv(run_key, db)
    return argv[argv.index("--evidence-ref") + 1] if "--evidence-ref" in argv else None


def argv_value(run_key: str, db: str, flag: str):
    argv = recorded_argv(run_key, db)
    return argv[argv.index(flag) + 1] if flag in argv else None


RECEIPT_DIRNAME = "run-scheduled-receipts"


def receipt_dir_of(root: str) -> str:
    """The directory bin/run-scheduled.sh derives for itself, computed the same
    way it does: the install's own out/, resolved once because out/ is a symlink
    in every worktree, plus one fixed name. There is no parameter for this and
    no environment variable that moves it — which is the whole of the 2026-09-12
    correction, and the reason every hostile-directory check below OWNS AN
    INSTALL instead of passing a path."""
    return os.path.join(os.path.realpath(os.path.join(root, "out")), RECEIPT_DIRNAME)


def receipt_path(root: str, service: str, run_key: str) -> str:
    """$REPO/out/run-scheduled-receipts/<sha256(service)[:16]>.<sha256(key)[:32]>.receipt"""
    return os.path.join(
        receipt_dir_of(root),
        hashlib.sha256(service.encode()).hexdigest()[:16] + "."
        + hashlib.sha256(run_key.encode()).hexdigest()[:32] + ".receipt")


def registered_codes() -> list:
    """The closed code list the wrapper declares in its own source. Read from
    the file rather than restated here: a code these tests assert on must be one
    the wrapper actually registers, and a code the wrapper stops registering
    must fail a check rather than quietly stop being produced."""
    with open(WRAPPER, encoding="utf-8") as fh:
        src = fh.read()
    match = re.search(r"^RECEIPT_CODES=\(\n(.*?)^\)$", src, re.S | re.M)
    return match.group(1).split() if match else []


def install_root(work: str, mutations: Optional[list] = None) -> Optional[str]:
    """A SECOND INSTALL of this same wrapper, whose out/ is its own.

    The receipt directory is derived from ${0:A:h:h} and from nothing else, so a
    test that needs a symlink where the receipts go, an unwritable directory, or
    a world-writable one cannot pass a path — it has to own the installation.
    Every top-level entry is symlinked so the recorder, the venv and the tools
    resolve exactly as they do in the real tree; bin/ is a real directory
    holding a COPY of the wrapper, which is what makes REPO resolve here.

    mutations, when given, are (old, new) substitutions applied to that copy —
    the mutation control for a guard. Each must apply EXACTLY ONCE: a
    substitution that silently matches nothing proves nothing and reports green,
    which is the failure mode this repo has paid for twice."""
    root = os.path.realpath(tempfile.mkdtemp(prefix="carr-selftest-install-", dir=work))
    for name in os.listdir(REPO):
        if name in ("out", "bin", ".git"):
            continue
        os.symlink(os.path.join(REPO, name), os.path.join(root, name))
    os.mkdir(os.path.join(root, "bin"))
    with open(WRAPPER, encoding="utf-8") as fh:
        src = fh.read()
    for old, new in (mutations or []):
        if src.count(old) != 1:
            check(f"the mutation control {old.strip()[:52]!r} applies exactly once",
                  False, f"matched {src.count(old)} times")
            return None
        src = src.replace(old, new)
    dst = os.path.join(root, "bin", "run-scheduled.sh")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(src)
    os.chmod(dst, 0o755)
    return root


def run_install(root: str, work: str, run_key: str,
                service: str = "carr-selftest-probe", child: str = "exit 0",
                env_extra: Optional[dict] = None, argv_prefix=(),
                timeout: int = 120) -> tuple:
    """One run of the wrapper installed at root, with its own spool and its own
    throttle-stamp directory. A TIMEOUT IS A RESULT: a wrapper that blocks after
    its child has already exited is the exact failure this mechanism must never
    have, so it is caught and reported rather than aborting the suite."""
    db = os.path.join(work, uuid.uuid4().hex + ".sqlite3")
    env = unreachable_env()
    env["CARR_RUN_SPOOL_DB"] = db
    env["CARR_RUN_SCHEDULED_STATE_DIR"] = tempfile.mkdtemp(
        prefix="carr-selftest-state-", dir=work)
    env.update(env_extra or {})
    wrapper = os.path.join(root, "bin", "run-scheduled.sh")
    try:
        proc = subprocess.run(
            [wrapper, *argv_prefix, service, run_key, "/bin/sh", "-c", child],
            capture_output=True, text=True, timeout=timeout, env=env, cwd=REPO)
    except subprocess.TimeoutExpired:
        proc = subprocess.CompletedProcess([], 99, "", f"timed out after {timeout}s")
    return proc, db


def install_line(root: str, run_key: str) -> str:
    """The provenance line the install at root wrote for this run key."""
    try:
        with open(os.path.join(root, "out", "run-scheduled.log")) as fh:
            hits = [ln.rstrip("\n") for ln in fh if f"key={run_key} " in ln]
    except FileNotFoundError:
        return ""
    return hits[-1] if hits else ""


def carries_privileged_word(value: str) -> str:
    lowered = value.lower()
    return next((w for w in PRIVILEGED_WORDS if w in lowered), "")


# The fd discipline, asserted against the wrapper's own source. Behaviour proves
# what a hazard DOES; this proves there is no second resolution of the name to
# race at all — the property a passing behavioural check cannot distinguish from
# a window nobody happened to hit. Its own mutation control is below: the
# retired path-addressed shape must make this report the hazard.
def fd_discipline(src: str) -> tuple:
    body = src[src.index("mint_receipt() {"):src.index('\nmint_receipt || true')]
    opens = body.count("sysopen -r -w -o creat,excl,nofollow -m 600 -u 3 --")
    if opens != 1:
        return False, f"{opens} exclusive creates in the mint, expected exactly 1"
    # from the END of the open's own line: that line names the path once, which
    # is the last time the name is allowed to appear.
    after = body[body.index("sysopen -r -w -o creat,excl,nofollow"):]
    after = after[after.index("\n"):after.index('evidence_ref="$candidate"')]
    if '"$RECEIPT_FILE"' in after:
        return False, "the pathname is resolved again after the open"
    for needed in ("zstat -f 3 -H fst", "syswrite -o 3 -c wrote",
                   "sysseek -u 3 0", "sysread -c gotn -i 3 got"):
        if needed not in after:
            return False, f"{needed!r} does not address the open descriptor"
    return True, ""


def tier1_receipt_mint() -> None:
    print()
    print("TIER 1 — the receipt this wrapper mints for itself")

    work = tempfile.mkdtemp(prefix="carr-selftest-receipt-")
    service = "carr-selftest-probe"
    codes = registered_codes()
    check("the wrapper declares a closed list of refusal codes", len(codes) >= 10,
          repr(codes))

    def code_of(root: str, run_key: str) -> str:
        return field(install_line(root, run_key), "receipt_code")

    def key(tag: str) -> str:
        return f"selftest.receipt.{tag}.{uuid.uuid4().hex[:8]}"

    # (a) THE REAL FLEET PATH, in the real tree. Every plist in ops/launchd/
    #     passes no flag — there is no flag to pass — so this is the case that
    #     covers all seven jobs, and it runs against the real installation
    #     rather than a copy so the fixed directory it derives is the real one.
    rk = key("fleet")
    db = os.path.join(work, uuid.uuid4().hex + ".sqlite3")
    env = unreachable_env()
    env["CARR_RUN_SPOOL_DB"] = db
    state_dir = tempfile.mkdtemp(prefix="carr-selftest-state-", dir=work)
    env["CARR_RUN_SCHEDULED_STATE_DIR"] = state_dir
    proc = subprocess.run([WRAPPER, service, rk, "/bin/sh", "-c", "exit 0"],
                          capture_output=True, text=True, timeout=120, env=env, cwd=REPO)
    minted = evidence_ref_of(rk, db)
    shape = RECEIPT_SHAPE.match(minted or "")
    live = receipt_path(REPO, service, rk)
    check("an ordinary run — no flags, the way every plist calls this — passes "
          "the recorder a minted --evidence-ref", shape is not None, repr(minted))
    check("the child's exit code is still its own", proc.returncode == 0,
          f"got {proc.returncode}")
    check("the provenance line names no refusal", field(tail_line(rk), "receipt_code") == "none",
          tail_line(rk))
    if shape:
        check("the receipt carries the HASH of THIS run key, so the reader can "
              "tell it apart from a receipt minted for another job",
              shape.group(2) == hashlib.sha256(rk.encode()).hexdigest()[:32],
              f"{shape.group(2)} vs {hashlib.sha256(rk.encode()).hexdigest()[:32]}")
        started = argv_value(rk, db, "--started-at")
        check("the receipt was minted STRICTLY AFTER the dispatch this row "
              "records, which is what the reader's clause requires",
              shape.group(1).replace(".", "") >
              started.replace("-", "").replace(":", "").rstrip("Z") + "000",
              f"minted {shape.group(1)} vs started {started}")
        check("the receipt is at most 128 characters", len(minted) <= 128, f"{len(minted)}")
        check("it landed in the FIXED directory under this repository's own "
              "out/, which no argument and no variable named",
              os.path.exists(live), live)
        if os.path.exists(live):
            check("and that file holds exactly what reached the recorder",
                  open(live).read() == minted + "\n", live)
            check("the receipt directory is this user's and is not writable by "
                  "anyone else", (os.stat(os.path.dirname(live)).st_mode & 0o022) == 0,
                  oct(os.stat(os.path.dirname(live)).st_mode))
    check("NOTHING was written under the throttle-stamp directory the "
          "environment did name — a stamp is a timestamp, not evidence",
          not os.path.exists(os.path.join(state_dir, "receipts")),
          str(os.listdir(state_dir)))
    if os.path.exists(live):
        os.unlink(live)

    # Every check from here on owns its installation, because the directory is
    # no longer something a test can be handed.
    plain = install_root(work)
    if plain is None:
        return

    # (b) NOTHING OUTSIDE THE WRAPPER CAN SUPPLY ONE. A caller who works out the
    #     path and pre-seeds it cannot get that content into the row: the file it
    #     left is unlinked and a fresh inode created. The seeded content is a
    #     privileged word on purpose.
    rk = key("preseeded")
    seeded = receipt_path(plain, service, rk)
    os.makedirs(os.path.dirname(seeded), mode=0o700, exist_ok=True)
    with open(seeded, "w") as fh:
        fh.write("complete\n")
    proc, db = run_install(plain, work, rk)
    landed = evidence_ref_of(rk, db)
    check("a receipt file pre-seeded at the exact path the wrapper computes "
          "CANNOT bind the row — this run's own mint is what is recorded",
          landed is not None and landed != "complete"
          and RECEIPT_SHAPE.match(landed) is not None, repr(landed))
    check("...and the pre-seeded content is gone from the wrapper's own file",
          open(seeded).read().strip() == (landed or ""), open(seeded).read())

    # (c) THERE IS NO FLAG AND NO ARGUMENT. Neither the retired
    #     --evidence-ref-file nor a --receipt-dir anyone might reach for is
    #     parsed as an option: both fall through to the positional arguments,
    #     which is visible in what the recorder was told the service key was.
    for flag in ("--evidence-ref-file", "--receipt-dir"):
        hostile = os.path.join(work, "argument-" + uuid.uuid4().hex[:8])
        os.makedirs(hostile)
        rk = key("argument")
        proc, db = run_install(plain, work, rk, service=flag,
                              argv_prefix=(), env_extra=None)
        # the flag IS the service key, and a service key starting with a dash is
        # not a shape this wrapper mints for
        check(f"{flag} is not an option: it is consumed as a positional "
              f"argument, not as a door to a caller's directory",
              argv_value(rk, db, "--service") == flag, repr(recorded_argv(rk, db)))
        check(f"...and that run mints NO receipt, refused as {flag}",
              evidence_ref_of(rk, db) is None and code_of(plain, rk) == "key_shape",
              f"{evidence_ref_of(rk, db)!r} code={code_of(plain, rk)!r}")
        check(f"...and nothing was written into the directory {flag} named",
              os.listdir(hostile) == [], str(os.listdir(hostile)))

    # (d) REJECT, NEVER REPAIR. A run key carrying a byte outside the token
    #     shape mints nothing at all — it is not stripped into something
    #     acceptable, and the job is recorded and returns its own code anyway.
    for label, bad_key in (
        ("a carriage return", "selftest.receipt.cr\rcomplete"),
        ("a newline", "selftest.receipt.lf\ncomplete"),
        ("a tab", "selftest.receipt.tab\tcomplete"),
        ("a space", "selftest.receipt.sp complete"),
        ("an argument-injection attempt", "selftest --state failed --exit-code 1"),
        ("a NUL-adjacent control byte", "selftest.receipt.\x01complete"),
    ):
        proc, db = run_install(plain, work, bad_key)
        got = evidence_ref_of(bad_key, db)
        check(f"a run key carrying {label} mints NO receipt — refused, never "
              f"repaired into an acceptable token", got is None, repr(got))
        check(f"...and {label} still leaves the job's own exit code alone",
              proc.returncode == 0, f"got {proc.returncode}")
        check(f"...and the row for {label} is still recorded",
              recorded_argv(bad_key, db) != [], "no row")

    # (e) WHAT IS ALREADY AT THE LEAF, each class under its own code. Nothing is
    #     followed, nothing is opened, and what was there is left as it was.
    def occupy(kind: str, run_key: str) -> tuple:
        root = install_root(work)
        if root is None:
            return None, None, None
        path = receipt_path(root, service, run_key)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        target = os.path.join(work, "target-" + uuid.uuid4().hex[:8])
        if kind == "symlink":
            with open(target, "w") as fh:
                fh.write("complete\n")
            os.symlink(target, path)
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "hardlink":
            with open(path, "w") as fh:
                fh.write("complete\n")
            os.link(path, target)
        else:
            os.makedirs(path)
        return root, path, target

    for kind, label, want in (("symlink", "a symlink", "leaf_symlink"),
                              ("fifo", "a FIFO", "leaf_not_regular"),
                              ("dir", "a directory", "leaf_not_regular"),
                              ("hardlink", "a hard-linked regular file",
                               "leaf_hard_linked")):
        rk = key(kind)
        root, path, target = occupy(kind, rk)
        if root is None:
            continue
        proc, db = run_install(root, work, rk, timeout=60)
        got = evidence_ref_of(rk, db)
        code = code_of(root, rk)
        check(f"{label} at the receipt path mints NO receipt, and says so as "
              f"{want}", got is None and code == want, f"{got!r} code={code!r}")
        check(f"...and {label} still leaves the job's own exit code alone",
              proc.returncode == 0, f"got {proc.returncode}")
        check(f"...and the row is still recorded over {label}",
              recorded_argv(rk, db) != [], "no row")
        if kind == "symlink":
            check("...and the symlink was not followed: its target is untouched",
                  open(target).read() == "complete\n", open(target).read())
        if kind == "fifo":
            check("...and the FIFO was never OPENED, so the wrapper did not "
                  "block forever on a reader that never comes",
                  proc.returncode == 0, proc.stderr or "the wrapper blocked")
        if kind == "hardlink":
            check("...and neither name was written: a stranger's inode is not "
                  "this wrapper's file", open(path).read() == "complete\n"
                  and open(target).read() == "complete\n",
                  f"{open(path).read()!r} {open(target).read()!r}")

    # (f) THE DIRECTORY ITSELF MUST BE OURS AND PRIVATE. Unwritable is a refusal
    #     like any other; so is one anybody may write in, because a directory
    #     others can write in makes every leaf check below it meaningless.
    for mode, label in ((0o500, "an unwritable receipt directory"),
                        (0o777, "a receipt directory anyone may write in")):
        root = install_root(work)
        if root is None:
            continue
        os.makedirs(receipt_dir_of(root), exist_ok=True)
        os.chmod(receipt_dir_of(root), mode)
        rk = key("dirmode")
        try:
            proc, db = run_install(root, work, rk)
            code = code_of(root, rk)
            check(f"{label} mints NO receipt and records the row anyway, as "
                  f"dir_unusable", evidence_ref_of(rk, db) is None
                  and code == "dir_unusable" and recorded_argv(rk, db) != [],
                  f"{evidence_ref_of(rk, db)!r} code={code!r}")
            check(f"...and the job's own exit code is still its own over {label}",
                  proc.returncode == 0, f"got {proc.returncode}")
        finally:
            os.chmod(receipt_dir_of(root), 0o700)

    # (g) A PARENT SYMLINK IS NOT A PARENT. The leaf check was never the whole
    #     story: whoever controls a directory ON THE WAY to the leaf controls
    #     where the write lands, so the directory is required to BE the fixed
    #     one — no component under the resolved root may be a link.
    root = install_root(work)
    if root is not None:
        elsewhere = os.path.join(work, "elsewhere-" + uuid.uuid4().hex[:8])
        os.makedirs(elsewhere, mode=0o700)
        os.makedirs(os.path.join(root, "out"), exist_ok=True)
        os.symlink(elsewhere, receipt_dir_of(root))
        rk = key("parentlink")
        proc, db = run_install(root, work, rk)
        code = code_of(root, rk)
        check("a SYMLINK where the receipt directory belongs mints no receipt "
              "and says dir_not_fixed",
              evidence_ref_of(rk, db) is None and code == "dir_not_fixed",
              f"{evidence_ref_of(rk, db)!r} code={code!r}")
        check("...and nothing was written through it",
              os.listdir(elsewhere) == [], str(os.listdir(elsewhere)))
        check("...and the job's own exit code is still its own",
              proc.returncode == 0, f"got {proc.returncode}")

    # (h) NO VARIABLE NAMES THE DIRECTORY. Every spelling anyone might reach for
    #     REFUSES rather than being honoured, so an attempt to move the evidence
    #     is in the log instead of being silently obeyed; and the variable that
    #     legitimately moves the throttle stamp does not move the receipt.
    for var in ("CARR_RUN_SCHEDULED_RECEIPT_DIR", "CARR_RUN_SCHEDULED_RECEIPT_ROOT",
                "CARR_RUN_SCHEDULED_RECEIPT_FILE", "CARR_RUN_SCHEDULED_RECEIPTS"):
        hostile = os.path.join(work, "hostile-" + uuid.uuid4().hex[:8])
        os.makedirs(hostile, mode=0o700)
        rk = key("envredirect")
        proc, db = run_install(plain, work, rk, env_extra={var: hostile})
        code = code_of(plain, rk)
        check(f"{var} mints NO receipt: naming the directory is refused, not "
              f"honoured", evidence_ref_of(rk, db) is None
              and code == "dir_not_selectable", f"{evidence_ref_of(rk, db)!r} code={code!r}")
        check(f"...and nothing was written under the directory {var} named",
              os.listdir(hostile) == [], str(os.listdir(hostile)))
        check(f"...and the row is still recorded with {var} set",
              recorded_argv(rk, db) != [], "no row")

    hostile = os.path.join(work, "stampdir-" + uuid.uuid4().hex[:8])
    os.makedirs(hostile, mode=0o700)
    rk = key("stampdir")
    proc, db = run_install(plain, work, rk,
                          env_extra={"CARR_RUN_SCHEDULED_STATE_DIR": hostile})
    check("CARR_RUN_SCHEDULED_STATE_DIR still redirects the throttle stamp and "
          "does NOT move the receipt: the mint lands in the fixed directory",
          RECEIPT_SHAPE.match(evidence_ref_of(rk, db) or "") is not None
          and os.path.exists(receipt_path(plain, service, rk)),
          f"{evidence_ref_of(rk, db)!r} {receipt_path(plain, service, rk)}")
    check("...and no receipt appeared under the directory it did name",
          not os.path.exists(os.path.join(hostile, RECEIPT_DIRNAME))
          and not os.path.exists(os.path.join(hostile, "receipts")),
          str(os.listdir(hostile)))

    # (i) THE SWAP THAT ARRIVES AFTER THE CHILD HAS EXITED. The mint runs after
    #     the child returns, so the child's own background process is the
    #     adversary with the best timing available: it races the create itself.
    #     O_CREAT|O_EXCL|O_NOFOLLOW makes both outcomes safe — we created this
    #     inode, or we refused — and neither may block or change the job's code.
    root = install_root(work)
    if root is not None:
        rk = key("swaprace")
        leaf = receipt_path(root, service, rk)
        os.makedirs(os.path.dirname(leaf), mode=0o700, exist_ok=True)
        racer = (f"( sleep 0.05; rm -f '{leaf}'; mkfifo '{leaf}' ) "
                 f">/dev/null 2>&1 & exit 0")
        proc, db = run_install(root, work, rk, child=racer, timeout=60)
        code = code_of(root, rk)
        minted = evidence_ref_of(rk, db)
        settled = (RECEIPT_SHAPE.match(minted or "") is not None and code == "none") \
            or (minted is None and code in codes and code != "none")
        check("a FIFO swapped in around the mint settles one way or the other — "
              "this run's own token, or a named refusal — and never both",
              settled, f"{minted!r} code={code!r}")
        check("...and the wrapper did not block: the child's exit code came "
              "back", proc.returncode == 0, f"got {proc.returncode}")
        check("...and the row is recorded either way", recorded_argv(rk, db) != [],
              "no row")

    # (j) AND THERE IS NO SECOND RESOLUTION OF THE NAME TO RACE. Asserted
    #     against the source, because a behavioural check cannot tell a window
    #     nobody hit from one that is not there.
    with open(WRAPPER, encoding="utf-8") as fh:
        wrapper_src = fh.read()
    held, why = fd_discipline(wrapper_src)
    check("the mint opens the receipt exactly once, exclusively, and every "
          "check, write and read-back addresses that descriptor", held, why)
    retired = wrapper_src.replace(
        'sysread -c gotn -i 3 got 2>/dev/null',
        'got="$( (cat -- "$RECEIPT_FILE") 2>/dev/null )"')
    held_retired, _ = fd_discipline(retired)
    check("...and that assertion is load-bearing: the retired path-addressed "
          "read-back fails it", not held_retired, "the mutated source passed")

    # (k) NO CALLER WORD TRAVELS, in the recorder's argv, in the provenance line
    #     OR in a refusal code. The run keys here are the hostile ones on
    #     purpose: a receipt that quoted its run key would put `complete` and
    #     `allow-commit-green` into ops.run.evidence_ref and into the log.
    for hostile_key in ("complete", "allow-commit-green", "passing.and.ok"):
        rk = f"selftest.receipt.{hostile_key}"
        proc, db = run_install(plain, work, rk)
        got = evidence_ref_of(rk, db) or ""
        offending = carries_privileged_word(got)
        check(f"the receipt minted for run key {rk!r} carries no privileged "
              f"word", RECEIPT_SHAPE.match(got) is not None and offending == "",
              f"{got!r} carries {offending!r}")
        line = install_line(plain, rk)
        check(f"...and the provenance line for {rk!r} exports the minted token "
              f"and not the caller's own", field(line, "evidence_ref") == got,
              f"{field(line, 'evidence_ref')!r} vs {got!r}")
        check(f"...and that provenance field carries no privileged word",
              carries_privileged_word(field(line, "evidence_ref")) == "",
              field(line, "evidence_ref"))
    offenders = {c: carries_privileged_word(c) for c in codes
                 if carries_privileged_word(c)}
    check("no refusal code the wrapper registers carries a privileged word — a "
          "code is an export too", offenders == {}, repr(offenders))

    # (l) AND THE LINE SAYS `none` WHEN NOTHING WAS MINTED, rather than dropping
    #     the field and shifting every regex that reads this log.
    rk = "selftest.receipt.none complete"
    proc, db = run_install(plain, work, rk)
    line = install_line(plain, "selftest.receipt.none")
    check("a run that minted no receipt still writes evidence_ref=none on its "
          "provenance line", field(line, "evidence_ref") == "none", line)
    check("...and names the refusal rather than leaving it anonymous",
          field(line, "receipt_code") == "key_shape", line)

    # ── MUTATION CONTROLS ─────────────────────────────────────────────────────
    # Each removes exactly one guard and shows the hazard the checks above claim
    # to close becoming reachable. A check whose guard can be deleted with the
    # suite still green is a check that proves nothing.
    print()
    print("TIER 1 — mutation controls: each guard removed, each hazard reachable")

    DIR_LINK_GUARDS = [
        ("""  if [ -L "$RECEIPT_DIR" ]; then
    receipt_code=dir_not_fixed
    return 1
  fi
  if [ ! -e "$RECEIPT_DIR" ]; then""",
         """  if [ ! -e "$RECEIPT_DIR" ]; then"""),
        ("""  if [ -L "$RECEIPT_DIR" ] || [ "${RECEIPT_DIR:A}" != "$RECEIPT_DIR" ]; then
    receipt_code=dir_not_fixed
    return 1
  fi
""", ""),
        ("""  if (( (dlnk[mode] & 8#170000) == 8#120000 )); then
    receipt_code=dir_not_fixed
    return 1
  fi
""", ""),
    ]
    root = install_root(work, DIR_LINK_GUARDS)
    if root is not None:
        elsewhere = os.path.join(work, "mutated-elsewhere-" + uuid.uuid4().hex[:8])
        os.makedirs(elsewhere, mode=0o700)
        os.makedirs(os.path.join(root, "out"), exist_ok=True)
        os.symlink(elsewhere, receipt_dir_of(root))
        rk = key("mutparentlink")
        proc, db = run_install(root, work, rk)
        check("CONTROL — without the directory-is-the-fixed-one guard, a "
              "symlinked receipt directory IS followed and the write lands "
              "outside the repository", os.listdir(elsewhere) != [],
              f"{os.listdir(elsewhere)} code={code_of(root, rk)!r}")

    NLINK_GUARD = [("""    if [ "$lst[nlink]" -ne 1 ]; then
      receipt_code=leaf_hard_linked
      return 1
    fi
""", "")]
    root = install_root(work, NLINK_GUARD)
    if root is not None:
        rk = key("muthardlink")
        path = receipt_path(root, service, rk)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w") as fh:
            fh.write("complete\n")
        other = os.path.join(work, "mutated-link-" + uuid.uuid4().hex[:8])
        os.link(path, other)
        proc, db = run_install(root, work, rk)
        check("CONTROL — without the link-count guard, a hard-linked leaf is "
              "cleared and minted over instead of refused",
              RECEIPT_SHAPE.match(evidence_ref_of(rk, db) or "") is not None,
              f"{evidence_ref_of(rk, db)!r} code={code_of(root, rk)!r}")

    DIRMODE_GUARD = [("""  if [ "$lst[uid]" -ne "$UID" ] || (( (lst[mode] & 8#22) != 0 )); then
    receipt_code=dir_unusable
    return 1
  fi
""", "")]
    root = install_root(work, DIRMODE_GUARD)
    if root is not None:
        os.makedirs(receipt_dir_of(root), exist_ok=True)
        os.chmod(receipt_dir_of(root), 0o777)
        rk = key("mutdirmode")
        try:
            proc, db = run_install(root, work, rk)
            check("CONTROL — without the ownership-and-mode guard, a directory "
                  "anyone may write in is accepted",
                  RECEIPT_SHAPE.match(evidence_ref_of(rk, db) or "") is not None,
                  f"{evidence_ref_of(rk, db)!r} code={code_of(root, rk)!r}")
        finally:
            os.chmod(receipt_dir_of(root), 0o700)

    # The retired shape: the leaf is validated by name and then opened by name.
    PATH_WRITE = [
        ("""  if [ -L "$RECEIPT_FILE" ]; then
    receipt_code=leaf_symlink
    return 1
  fi
  if [ -e "$RECEIPT_FILE" ]; then""",
         """  if false; then"""),
        ('if ! sysopen -r -w -o creat,excl,nofollow -m 600 -u 3 -- "$RECEIPT_FILE" 2>/dev/null; then',
         'if ! : > "$RECEIPT_FILE" 2>/dev/null; then'),
    ]
    root = install_root(work, PATH_WRITE)
    if root is not None:
        rk = key("mutfifo")
        leaf = receipt_path(root, service, rk)
        os.makedirs(os.path.dirname(leaf), mode=0o700, exist_ok=True)
        os.mkfifo(leaf)
        proc, db = run_install(root, work, rk, timeout=25)
        check("CONTROL — with the retired path-addressed write, a FIFO at the "
              "receipt path BLOCKS the wrapper after its child has already "
              "exited", proc.returncode == 99, f"got {proc.returncode}")

    root = install_root(work, PATH_WRITE)
    if root is not None:
        rk = key("mutsymlink")
        leaf = receipt_path(root, service, rk)
        os.makedirs(os.path.dirname(leaf), mode=0o700, exist_ok=True)
        target = os.path.join(work, "mutated-target-" + uuid.uuid4().hex[:8])
        with open(target, "w") as fh:
            fh.write("complete\n")
        os.symlink(target, leaf)
        proc, db = run_install(root, work, rk, timeout=60)
        check("CONTROL — with the retired path-addressed write, a symlink at "
              "the receipt path is FOLLOWED and its target is overwritten",
              open(target).read() != "complete\n", open(target).read())

    CALLER_ROOT = [('  root="$REPO/out"',
                    '  root="${CARR_RUN_SCHEDULED_STATE_DIR:-$REPO/out}"')]
    root = install_root(work, CALLER_ROOT)
    if root is not None:
        hostile = os.path.join(work, "mutated-stampdir-" + uuid.uuid4().hex[:8])
        os.makedirs(hostile, mode=0o700)
        rk = key("mutcallerroot")
        proc, db = run_install(root, work, rk,
                              env_extra={"CARR_RUN_SCHEDULED_STATE_DIR": hostile})
        check("CONTROL — make the directory caller-selectable again and the "
              "receipt lands wherever the caller said, which is what the "
              "checks above would catch",
              os.path.isdir(os.path.join(hostile, RECEIPT_DIRNAME)),
              f"{os.listdir(hostile)} code={code_of(root, rk)!r}")

    shutil.rmtree(work, ignore_errors=True)

def main() -> int:
    print("run-scheduled-selftest — bin/run-scheduled.sh must never change what "
          "a job does, prints, or returns")
    tier1()
    tier1_receipt_mint()
    tier1_throttle()
    tier1_refresh_rules()
    tier2()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
