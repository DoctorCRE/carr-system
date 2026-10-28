#!/usr/bin/env python3
"""ops.run retention, driven through tools/ops-record.py as carr_jobs on real Postgres.

Migration 1038 gives ops.run one retention rule, ops.reap_expired_runs, and the
run writer calls it after every recorded run. This seeds one service's ledger
with rows each reader still needs and rows nobody needs, records a run of each
key the way a wrapper does, and asserts exactly the unneeded rows are gone. It connects as
carr_jobs because that is the role the nightly chain uses, and that role must
still have no DELETE on ops.run: the function is the only door.

Needs a disposable loopback cluster; ops/local-pg-ci.py supplies one and runs
this in its acceptance chain. Exits 78 when there is no such cluster to use.
"""
import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OPS_RECORD = os.path.join(REPO, "tools", "ops-record.py")
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
if not os.access(PYTHON, os.X_OK):
    PYTHON = sys.executable

SERVICE = "run-reaper-acceptance"
KEY = "acceptance.reaper"
SPARSE_KEY = "acceptance.reaper.sparse"
ENVIRONMENT = "local"


def refuse(msg):
    print(f"run-reaper-acceptance: {msg}", file=sys.stderr)
    sys.exit(78)


def loopback_dsn():
    dsn = os.environ.get("CARR_LOCAL_PG_DSN") or os.environ.get("DATABASE_URL", "")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        refuse("needs a loopback CARR_LOCAL_PG_DSN or DATABASE_URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        refuse("refuses any host but loopback — this test deletes run rows")
    return dsn


def jobs_dsn(owner_dsn):
    parsed = urlparse(owner_dsn)
    return f"postgres://carr_jobs@{parsed.hostname}:{parsed.port or 5432}{parsed.path}"


def seed(cur, service_id, key, state, days_ago, expires_days_ago="default"):
    """One terminal run observed `days_ago` days back, expiring a day later."""
    if expires_days_ago == "default":
        expires_days_ago = days_ago - 1
    cur.execute(
        """insert into ops.run (kind, service_id, environment, run_key, state, failure_class,
                                started_at, ended_at, source_kind, source_ref, observed_at, expires_at)
           values ('job', %s, %s, %s, %s, %s,
                   now() - make_interval(days => %s), now() - make_interval(days => %s),
                   'wrapper', 'acceptance', now() - make_interval(days => %s),
                   case when %s::int is null then null else now() - make_interval(days => %s::int) end)
           returning id""",
        (service_id, ENVIRONMENT, key, state, "exit_status" if state == "failed" else None,
         days_ago, days_ago, days_ago, expires_days_ago, expires_days_ago))
    return cur.fetchone()[0]


def main():
    owner = loopback_dsn()
    with psycopg.connect(owner, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select to_regprocedure('ops.reap_expired_runs(uuid,text,text,integer)')")
        if cur.fetchone()[0] is None:
            refuse("migration 1038 is not applied to this cluster")
        cur.execute("select 1 from pg_roles where rolname = 'carr_jobs'")
        if cur.fetchone() is None:
            refuse("this cluster has no carr_jobs role to run as")
        cur.execute("alter role carr_jobs login")
        cur.execute("delete from ops.incident where source_ref = %s", (SERVICE,))
        cur.execute("delete from ops.run where service_id in "
                    "(select id from ops.service where key = %s)", (SERVICE,))
        cur.execute("delete from ops.service where key = %s", (SERVICE,))
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values (%s, 'run reaper acceptance', 'Data', 'medium', 'system') returning id""",
            (SERVICE,))
        sid = cur.fetchone()[0]

        reaped = {
            "superseded 40 days": seed(cur, sid, KEY, "succeeded", 40),
            "superseded 35 days": seed(cur, sid, KEY, "succeeded", 35),
            "superseded 20 days": seed(cur, sid, KEY, "succeeded", 20),
        }
        linked = seed(cur, sid, KEY, "succeeded", 30)
        recovery = seed(cur, sid, KEY, "succeeded", 29)
        kept = {
            "the only failed run of its key": seed(cur, sid, KEY, "failed", 50),
            "cited by an incident link": linked,
            "cited as recovery evidence": recovery,
            "no expiry": seed(cur, sid, KEY, "succeeded", 45, None),
            "expired 9 days ago": seed(cur, sid, KEY, "succeeded", 10),
            "expired 7 days ago": seed(cur, sid, KEY, "succeeded", 8),
            "recent": seed(cur, sid, KEY, "succeeded", 2),
            "sparse key, one of its newest three": seed(cur, sid, SPARSE_KEY, "succeeded", 60),
            "sparse key, newest": seed(cur, sid, SPARSE_KEY, "succeeded", 59),
        }
        cur.execute(
            """insert into ops.incident (ref, title, severity, environment, detected_source,
                                         source_kind, source_ref, recovery_evidence_ref)
               values (%s, 'run reaper acceptance', 'SEV-3', %s, 'acceptance',
                       'operator', %s, %s) returning id""",
            (f"INC-ACC-{uuid.uuid4().hex[:8]}", ENVIRONMENT, SERVICE, f"ops.run:{recovery}"))
        incident = cur.fetchone()[0]
        cur.execute("insert into ops.incident_link (incident_id, kind, ref) values (%s, 'run', %s)",
                    (incident, str(linked).upper()))

    failures = []
    with psycopg.connect(jobs_dsn(owner), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select has_table_privilege('ops.run', 'DELETE')")
        if cur.fetchone()[0]:
            failures.append("carr_jobs can DELETE from ops.run directly; the reaper must be the only door")

    env = dict(os.environ, CARR_DB_JOBS_URL=jobs_dsn(owner))
    env.pop("DATABASE_URL", None)
    for key in (KEY, SPARSE_KEY):
        proc = subprocess.run(
            [PYTHON, OPS_RECORD, "run", "--service", SERVICE, "--key", key, "--kind", "job",
             "--environment", ENVIRONMENT, "--state", "succeeded",
             "--source-kind", "wrapper", "--source-ref", "acceptance"],
            capture_output=True, text=True, env=env, cwd=REPO)
        if proc.returncode != 0:
            failures.append(f"ops-record run {key} exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        if proc.stderr.strip():
            failures.append(f"ops-record run {key} wrote to stderr: {proc.stderr.strip()[:300]}")

    with psycopg.connect(owner, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select id from ops.run where service_id = (select id from ops.service where key = %s)",
                    (SERVICE,))
        present = {row[0] for row in cur.fetchall()}
        cur.execute("delete from ops.incident where source_ref = %s", (SERVICE,))
        cur.execute("delete from ops.run where service_id in "
                    "(select id from ops.service where key = %s)", (SERVICE,))
        cur.execute("delete from ops.service where key = %s", (SERVICE,))

    failures += [f"{label} should have been reaped" for label, rid in reaped.items() if rid in present]
    failures += [f"{label} was reaped" for label, rid in kept.items() if rid not in present]
    if len(present) != len(kept) + 2:
        failures.append(f"expected the kept rows plus the two new runs, found {len(present)} rows")

    for failure in failures:
        print(f"run-reaper-acceptance: FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"run-reaper-acceptance: ok ({len(reaped)} reaped, {len(kept)} kept)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
