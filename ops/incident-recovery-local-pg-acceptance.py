#!/usr/bin/env python3
"""Fingerprint and success-clears, driven end to end as carr_jobs on real Postgres.

WHAT THE OTHER TWO GUARDS CANNOT SEE. ops/incident-fingerprint-selftest.py holds
the decision rules and touches no database. Migration 0293's own proof block
holds ops.clear_recovered_incident and touches no Python. Between them sits the
thing that actually has to work on 2026-08-23: tools/ops-record.py writing a run
row AS THE JOB ROLE and having the right thing happen to the incident ledger.

That seam is where this change could fail silently and still look green. The
close path it replaces failed exactly there — ops-record.py sweep is correct
Python over a correct schema, and it has never once run in production, because
carr_jobs has no grant on resolved_at and bin/nightly.sh runs as carr_jobs. A
test that connects as the owner would have proved that path healthy every night
while the fleet accumulated 26 open incidents. So this connects as carr_jobs and
nothing else, and the first thing it asserts is that the role it is using still
cannot reach resolved_at by any route but the function.

WHAT IT DELIBERATELY CANNOT DISTINGUISH, found by mutating the writer under it.
Lowering HEALTHY_RUNS_TO_CLEAR to 2, or adding SEV-1 to AUTO_CLEARED_SEVERITIES,
leaves every assertion below still passing — because ops.clear_recovered_incident
clamps the sequence to three and refuses SEV-1 itself, so the OUTCOME stays
correct while the Python is wrong. That is the layering working as designed and
not a hole: both mutations are caught by migration 0293's proof block (cases 5
and 7) and by ops/incident-fingerprint-selftest.py, which assert the rule rather
than the outcome. Recorded here so a later reader does not mistake the silence
for coverage.

Needs a disposable loopback cluster; ops/local-pg-ci.py supplies one and runs
this in its acceptance chain. Exits 78 when there is no such cluster to use.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OPS_RECORD = os.path.join(REPO, "tools", "ops-record.py")
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
if not os.access(PYTHON, os.X_OK):
    PYTHON = sys.executable

SERVICE = "incident-recovery-acceptance"
RUN_KEY = "acceptance.ticker"
ENVIRONMENT = "local"

FAILURES: list[str] = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
    return cond


def refuse(msg):
    print(f"incident-recovery-acceptance: {msg}", file=sys.stderr)
    sys.exit(78)


def loopback_dsn():
    dsn = os.environ.get("CARR_LOCAL_PG_DSN") or os.environ.get("DATABASE_URL", "")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        refuse("needs a loopback CARR_LOCAL_PG_DSN or DATABASE_URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        refuse("refuses any host but loopback — this test writes incidents")
    return dsn


def jobs_dsn(owner_dsn):
    """The same cluster, reached as carr_jobs. The role is the point of the test."""
    parsed = urlparse(owner_dsn)
    return (f"postgres://carr_jobs@{parsed.hostname}:{parsed.port or 5432}"
            f"{parsed.path}")


def record(dsn, *, state, failure_class=None, key=RUN_KEY):
    """One tools/ops-record.py run, as carr_jobs, exactly as a wrapper calls it."""
    argv = [PYTHON, OPS_RECORD, "run",
            "--service", SERVICE, "--key", key, "--kind", "job",
            "--environment", ENVIRONMENT, "--state", state,
            "--source-kind", "wrapper", "--source-ref", "acceptance"]
    if failure_class:
        argv += ["--failure-class", failure_class]
    env = dict(os.environ)
    env["CARR_DB_JOBS_URL"] = dsn
    env.pop("DATABASE_URL", None)
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=REPO)
    if proc.returncode != 0:
        FAILURES.append(f"ops-record run --state {state} exited {proc.returncode}: "
                        f"{proc.stderr.strip()[:300]}")
    return proc.stdout


def incidents(cur):
    cur.execute(
        """select i.ref, i.severity, i.state, i.signature, i.occurrence_count,
                  i.last_seen_at, i.resolved_at, i.root_cause,
                  i.recovery_evidence_ref, i.monitoring_until
             from ops.incident i
             join ops.service s on s.key = split_part(i.signature, '|', 1)
            where s.key = %s
         order by i.detected_at, i.ref""", (SERVICE,))
    cols = ("ref", "severity", "state", "signature", "occurrence_count",
            "last_seen_at", "resolved_at", "root_cause",
            "recovery_evidence_ref", "monitoring_until")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def check_receipt_chronology(owner, dsn):
    with tempfile.TemporaryDirectory(prefix="receipt-chronology-") as td:
        root = Path(td)
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=2)).isoformat()
        recent = (now - timedelta(minutes=10)).isoformat()
        harness = (
            "import runpy, sys; from datetime import datetime; "
            "from unittest.mock import patch; from lib import scheduled_run; "
            "scheduled_run.LOG = sys.argv[2]; "
            "clock = patch('lib.scheduled_run.datetime'); "
            "mock = clock.start(); mock.now.return_value = datetime.fromisoformat(sys.argv[1]); "
            "runpy.run_path('hooks/scheduled-run-record.py', run_name='__main__')")
        env = dict(os.environ, HOME=td, CARR_REPO_ROOT=td)

        def stop(session, state, at, database):
            transcript = root / (session + ".jsonl")
            transcript.write_text("\n".join(json.dumps(row) for row in [
                {"type": "queue-operation", "operation": "enqueue", "timestamp": old,
                 "content": f'<scheduled-task name="{SERVICE}" file="/x/SKILL.md">'},
                {"message": {"role": "user" if state == "failed" else "assistant",
                             "content": [{"type": "tool_result", "is_error": True,
                                          "content": "synthetic failure"}] if state == "failed"
                             else [{"type": "text", "text": "completed"}]}},
            ]) + "\n")
            result = subprocess.run(
                [PYTHON, "-c", harness, at, str(root / "hook.log")],
                input=json.dumps({"hook_event_name": "Stop", "session_id": session,
                                  "transcript_path": str(transcript)}),
                text=True, capture_output=True, cwd=REPO,
                env=dict(env, CARR_DB_JOBS_URL=database), timeout=30)
            assert result.returncode == 0 and not result.stdout, result.stderr

        def snapshot():
            with psycopg.connect(owner) as conn, conn.cursor() as cur:
                cur.execute("select health from ops.v_service_environment_health "
                            "where service_key = %s and environment = 'production'", (SERVICE,))
                health = cur.fetchone()[0]
                rows = [r for r in incidents(cur) if '|production|scheduled-session|' in r['signature']]
                return health, [(r['state'], r['recovery_evidence_ref'], r['monitoring_until']) for r in rows]

        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute("insert into ops.service_environment "
                         "(service_id,environment,expected_cadence_seconds) "
                         "select id,'production',3600 from ops.service where key=%s", (SERVICE,))
        dead = "postgresql://carr_jobs@127.0.0.1:1/nonexistent"
        stop("old-success", "succeeded", old, dead)
        pending = root / "out/scheduled-run-record-cache/old-success.pending"
        check(pending.exists(), "failed delivery must retain the old success receipt")
        stop("new-failure", "failed", recent, dsn)
        before = snapshot()
        check(before[0] == 'unavailable' and before[1] and before[1][0][0] == 'detected',
              "new failure must establish unhealthy evidence and a detected incident")
        stop("old-success", "succeeded", recent, dsn)
        check(snapshot() == before, "old success retry must not recover a newer failure")
        stop("new-success", "succeeded", (now - timedelta(minutes=5)).isoformat(), dsn)
        before = snapshot()
        check(before[0] == 'healthy' and before[1][0][0] == 'monitoring',
              "a genuinely newer success must establish monitoring")
        stop("another-old-success", "succeeded", old, dead)
        stop("another-old-success", "succeeded", recent, dsn)
        check(snapshot() == before, "old success must not replace newer recovery evidence")
        stop("old-failure", "failed", old, dead)
        stop("old-failure", "failed", recent, dsn)
        check(snapshot() == before, "old failure retry must not invalidate newer recovery")
        with psycopg.connect(owner) as conn:
            rows = conn.execute("select observed_at, ended_at, expires_at < now() from ops.run "
                                "where service_id=(select id from ops.service where key=%s) "
                                "and environment='production' order by ended_at", (SERVICE,)).fetchall()
            check(len(rows) == 5 and all(r[0] == r[1] for r in rows),
                  "recorded observation times must preserve each frozen completion time")
            check(all(r[2] for r in rows[:3]), "replaying old receipts must not renew their freshness")
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute("delete from ops.run where environment='production' and "
                         "service_id=(select id from ops.service where key=%s)", (SERVICE,))
        stop("race-baseline", "failed", old, dsn)
        with psycopg.connect(owner) as blocker, ThreadPoolExecutor(max_workers=2) as pool:
            blocker.execute("select id from ops.incident where signature like %s "
                            "for no key update", (SERVICE + '|production|%',))
            newer = pool.submit(stop, "race-new-success", "succeeded", recent, dsn)

            def wait_for_recorder(fragment, expected=1):
                deadline = time.monotonic() + 10
                with psycopg.connect(owner, autocommit=True) as observer:
                    while time.monotonic() < deadline:
                        count = observer.execute(
                            "select count(*) from pg_stat_activity where usename='carr_jobs' "
                            "and wait_event_type='Lock' and query like %s", ('%' + fragment + '%',)).fetchone()[0]
                        if count >= expected:
                            return
                        time.sleep(0.01)
                raise AssertionError("recorder did not reach expected lock wait")

            try:
                wait_for_recorder("update ops.incident")
                older = pool.submit(stop, "race-old-failure", "failed",
                                    (now - timedelta(minutes=20)).isoformat(), dsn)
                wait_for_recorder("", 2)
            finally:
                blocker.rollback()
            newer.result(timeout=30)
            older.result(timeout=30)
        after = snapshot()
        check(after[0] == 'healthy' and after[1][0][0] == 'monitoring',
              "concurrent older failure must not invalidate the newer success")
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute("delete from ops.incident where signature like %s",
                         (SERVICE + '|production|%',))
            conn.execute("delete from ops.run where environment='production' and "
                         "service_id=(select id from ops.service where key=%s)", (SERVICE,))


def main():
    owner = loopback_dsn()
    with psycopg.connect(owner, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select to_regprocedure('ops.clear_recovered_incident(text,int)')")
        if cur.fetchone()[0] is None:
            refuse("migration 0293 is not applied to this cluster")
        cur.execute("select 1 from pg_roles where rolname = 'carr_jobs'")
        if cur.fetchone() is None:
            refuse("this cluster has no carr_jobs role to run as")
        cur.execute("alter role carr_jobs login")
        # A clean slate every run: this service is ours and nothing else writes it.
        cur.execute("delete from ops.incident where signature like %s",
                    (SERVICE + "|%",))
        cur.execute("delete from ops.run where service_id in "
                    "(select id from ops.service where key = %s)", (SERVICE,))
        cur.execute("delete from ops.service where key = %s", (SERVICE,))
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values (%s, 'incident recovery acceptance', 'Data', 'medium', 'system')""",
            (SERVICE,))

    dsn = jobs_dsn(owner)
    try:
        with psycopg.connect(dsn) as probe, probe.cursor() as cur:
            cur.execute("select 1")
    except psycopg.OperationalError as exc:
        refuse(f"cannot reach this cluster as carr_jobs: {str(exc).splitlines()[0]}")

    check_receipt_chronology(owner, dsn)

    # ── 0. THE GRANT 0117 WROTE IS STILL THE GRANT. ─────────────────────────
    # If this ever passes for the wrong reason — because someone widened the
    # column grant instead of using the function — every assertion below stops
    # meaning what it says.
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""select count(*) from information_schema.column_privileges
                        where table_schema='ops' and table_name='incident'
                          and grantee='carr_jobs' and privilege_type='UPDATE'
                          and column_name in ('resolved_at','root_cause')""")
        check(cur.fetchone()[0] == 0,
              "carr_jobs has gained a direct UPDATE grant on resolved_at or "
              "root_cause — 0117's boundary is gone and the SECURITY DEFINER "
              "function is no longer the only door")

    # ── 1. REPEAT FAILURES ARE ONE INCIDENT THAT COUNTS ─────────────────────
    # Derived, not spelled out. Rule b01edd26 bans a count a later edit can
    # falsify, and a message reading "three occurrences" beside a loop somebody
    # may widen is exactly that shape: the prose goes stale silently while the
    # assertion still passes. The fixture size is the single source here, so
    # changing it changes the expectation and the sentence together.
    repeats = 3
    for _ in range(repeats):
        record(dsn, state="failed", failure_class="exit_1")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        rows = incidents(cur)
    check(len(rows) == 1,
          f"identical failures should collapse to one incident, got "
          f"{[r['ref'] for r in rows]}")
    if rows:
        check(rows[0]["occurrence_count"] == repeats,
              f"the incident should have counted every one of the "
              f"{repeats} failures, has {rows[0]['occurrence_count']}")
        check(rows[0]["last_seen_at"] is not None,
              "a recurring incident must carry a last_seen_at")
        check(rows[0]["signature"].endswith("|exit_status"),
              f"exit_1 should fingerprint as a plain nonzero: {rows[0]['signature']}")

    # ── 2. A DIFFERENT BARE EXIT CODE JOINS IT; A MEANINGFUL ONE DOES NOT ───
    record(dsn, state="failed", failure_class="exit_2")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        rows = incidents(cur)
    check(len(rows) == 1,
          f"exit_2 is the same job failing the same way and must not open a "
          f"second row, got {[r['signature'] for r in rows]}")
    if rows:
        check(rows[0]["occurrence_count"] == repeats + 1,
              f"and it should have been counted: {rows[0]['occurrence_count']}")

    record(dsn, state="failed", failure_class="exit_69")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        rows = incidents(cur)
    check(len(rows) == 2,
          f"a dependency being unavailable is different work and must be its own "
          f"row, got {[r['signature'] for r in rows]}")
    check(any(r["signature"].endswith("|dependency_unavailable") for r in rows),
          f"and it should say so: {[r['signature'] for r in rows]}")

    # ── 3. TWO GREEN RUNS RECORD RECOVERY AND DO NOT CLOSE ──────────────────
    for _ in range(2):
        record(dsn, state="succeeded")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        rows = incidents(cur)
    check(all(r["state"] == "monitoring" for r in rows),
          f"two green runs should move both rows to monitoring, got "
          f"{[(r['ref'], r['state']) for r in rows]}")
    check(all(r["resolved_at"] is None for r in rows),
          "two green runs must not close anything")

    # ── 4. THE THIRD CLOSES BOTH, BECAUSE THE JOB IS WHAT RECOVERED ─────────
    record(dsn, state="succeeded")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        rows = incidents(cur)
    check(all(r["state"] == "resolved" for r in rows),
          f"the third consecutive healthy run should close both of this job's "
          f"incidents, got {[(r['ref'], r['state']) for r in rows]}")
    check(all(r["resolved_at"] is not None and r["root_cause"] for r in rows),
          "a closed incident must carry both a stamp and a reason")
    check(all("consecutive" in (r["root_cause"] or "") for r in rows),
          f"and the reason should name the evidence it stood on: "
          f"{[r['root_cause'] for r in rows]}")

    # ── 5. A FAILURE DURING THE WATCH REOPENS RATHER THAN LYING ─────────────
    record(dsn, state="failed", failure_class="exit_1")
    record(dsn, state="succeeded")
    record(dsn, state="failed", failure_class="exit_1")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        rows = [r for r in incidents(cur) if r["state"] not in ("resolved", "reviewed")]
    check(len(rows) == 1,
          f"the returning failure should be one new open incident, got {len(rows)}")
    if rows:
        check(rows[0]["state"] == "detected",
              f"a job that failed again during its watch must not read "
              f"'monitoring', reads {rows[0]['state']}")
        check(rows[0]["recovery_evidence_ref"] is None
              and rows[0]["monitoring_until"] is None,
              "a returning failure must clear the recovery evidence/window "
              "that previously made the incident eligible for closure")
        check(rows[0]["resolved_at"] is None,
              "a recurrence may invalidate recovery but must never close the incident")

    # ── 6. SEV-1 STAYS WITH A HUMAN, HOWEVER GREEN THE LEDGER ──────────────
    with psycopg.connect(owner, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("update ops.incident set severity = 'SEV-1' where ref = %s",
                    (rows[0]["ref"],))
    for _ in range(4):
        record(dsn, state="succeeded")
    with psycopg.connect(owner) as conn, conn.cursor() as cur:
        after = [r for r in incidents(cur) if r["ref"] == rows[0]["ref"]][0]
    check(after["state"] == "monitoring",
          f"a SEV-1 with four green runs must stop at monitoring, reads "
          f"{after['state']}")
    check(after["resolved_at"] is None,
          "a SEV-1 must never be closed by the automatic path")
    check(after["recovery_evidence_ref"] is not None
          and after["monitoring_until"] is not None,
          "new healthy evidence after a recurrence must be able to establish "
          "a fresh monitoring window")

    # ── 7. AND THE JOB ROLE STILL CANNOT DO IT BY HAND ─────────────────────
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        denied = False
        try:
            cur.execute("update ops.incident set resolved_at = now() where ref = %s",
                        (after["ref"],))
        except psycopg.errors.InsufficientPrivilege:
            denied = True
        check(denied, "carr_jobs was able to write resolved_at directly, which is "
                      "the exact thing 0117 exists to prevent")

    with psycopg.connect(owner, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("delete from ops.incident where signature like %s",
                    (SERVICE + "|%",))
        cur.execute("delete from ops.run where service_id in "
                    "(select id from ops.service where key = %s)", (SERVICE,))
        cur.execute("delete from ops.service where key = %s", (SERVICE,))

    if FAILURES:
        for f in FAILURES:
            print(f"  - {f}", file=sys.stderr)
        print(f"incident-recovery-acceptance: {len(FAILURES)} FAILED", file=sys.stderr)
        return 1
    print("incident-recovery-acceptance: repeat failures count on one row, a bare "
          "exit code joins it and a meaningful one does not, three consecutive "
          "healthy runs close it as carr_jobs, a SEV-1 never closes, and the job "
          "role still cannot write resolved_at by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
