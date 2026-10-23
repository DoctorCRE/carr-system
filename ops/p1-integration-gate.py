#!/usr/bin/env python3
"""
p1-integration-gate.py — PROGRAM 1's INTEGRATION CLAUSE, executable.

THE REQUIREMENT. Program 1's bullet list names three uses for the ephemeral
rehearsal lane: "ephemeral Neon branches for MIGRATION/INTEGRATION/RECOVERY
tests". Two of the three were covered before this file and one was not:

  migration  ops/p1-rebuild-gate.py — the committed schema plus every migration,
             loaded on Neon rather than on a container.
  recovery   bin/restore-rehearse.sh — an encrypted dump decrypted into a
             throwaway branch and reconciled against production. It branches
             PRODUCTION deliberately and by its own route, which
             ops/config/environments.json records as chosen; that is a different
             lane from this one and is not duplicated here.
  integration  NOTHING. This file.

WHAT THIS PROVES THAT THE REBUILD GATE DOES NOT. The rebuild gate proves the
SCHEMA stands up: the SQL loads, the migrations apply, the tables exist. It
never asks whether the APPLICATION works on what it just built. A schema can be
structurally perfect and still be unusable by the code that owns it — a missing
grant, a role that does not exist on a fresh branch, a sequence the app writes
through but the schema load left owned by the wrong role. Those failures are
invisible to `to_regclass` and fatal in practice. Permission-denied on a table
whose CREATE succeeded is exactly the shape found by hand on 2026-08-16, when
recording the first release candidate returned "permission denied for table
release" against a database whose schema was entirely correct.

So this reconstructs the environment the same way, then drives the system's OWN
entry points against it — a write and a read back through real code, not raw
SQL — and asks whether the answer that comes back is the one that went in.

WHY NOT THE SANITIZED FIXTURES. tools/staging-fixtures.py pins staging in the
file and takes no DSN argument, deliberately, so a fixture load can never be
aimed somewhere else. Rather than widen that surface for a test's convenience,
this gate generates the only rows it needs through the application itself, which
is a stronger assertion anyway: fixtures prove data can be inserted, a
write-then-read through the real code proves the system works.

THE GUARDS are deliberately a copy of ops/p1-rebuild-gate.py's rather than a
shared import, and that is a choice rather than an oversight. Each gate is
self-contained, so a refactor of one can never quietly weaken the other's
refusal to touch production. Duplication is the cheaper risk here.

  0. The target project is NOT production. Compared by pinned id, first.
  1. The created branch is not the project's DEFAULT branch.
  2. The branch endpoint is a different HOST from production and from staging.
  3. Work happens in a FRESH database, never the branch's inherited copy.
  4. Teardown deletes the branch by the id create returned, on every exit path.

Exit codes: 0 all assertions pass · 1 an assertion failed · 78 no Neon
credential in this environment (EX_CONFIG, the convention bin/nightly.sh and
bin/run-scheduled.sh already use for "not configured here").

Fixtures: ops/p1-integration-gate-selftest.py.
"""
# ci: runs-outside-ci — needs a Neon API credential and vendor branch create/delete, which CI has by construction and by choice not got
#
# WHY THIS CANNOT CARRY `# ci: db-gate` — the same reasoning as
# ops/p1-rebuild-gate.py, and stated here too rather than cross-referenced,
# because these two files keep their guards duplicated on purpose (see THE
# GUARDS above) and a reader who opens only one should get the whole answer.
#
# CI's db-gate lane supplies a DATABASE_URL for a throwaway local Postgres. This
# gate never reads that variable; it only SETS it for subprocesses it points at
# a Neon branch of staging that it creates and destroys. Handed CI's DSN it
# would look for NEON_API_KEY, find none, and exit 78 on every push.
#
# And a container would be the wrong substrate anyway. What this gate proves is
# that the APPLICATION works on the environment the rebuild gate just built —
# grants, roles and sequences as the VENDOR creates them. The failure it exists
# for is 2026-08-16's "permission denied for table release" against a schema
# that was structurally perfect. A local Postgres whose roles CI made itself
# cannot stage that failure, so passing here would mean less than nothing.
#
# WHAT ACTUALLY RUNS IT: the nightly chain, where its step is currently
# an unconditional exit 78, for want of the same
# provisioned Neon admin capability. Loops 497 and 499 carry that. Until it is
# granted this gate executes nowhere, which is a fact worth printing on every
# push rather than a marker worth faking.
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urlsplit, urlunsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import importlib.util

from lib.platform_metering import MeteringRefusal, authorize_metered_execution

_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("p1-integration-gate: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

INTEGRATION_DB = "integration_check"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def say(msg: str) -> None:
    print(msg)


def host_of(conn_string: str) -> str:
    if "@" not in conn_string:
        return ""
    return conn_string.split("@", 1)[1].split("/", 1)[0].split("?", 1)[0]


def neon(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([db_tap.NEONCTL, *args], capture_output=True, text=True,
                          timeout=300, env=env)


def wait_for_branch_connection_string(
    env: dict[str, str],
    branch_id: str,
    project_id: str,
    *,
    attempts: int = 12,
    delay_seconds: float = 5.0,
    runner: Callable[..., subprocess.CompletedProcess] = neon,
    sleeper: Callable[[float], None] = time.sleep,
) -> str:
    """Bound the readiness race after Neon creates a branch and its compute."""
    if attempts < 1:
        return ""
    for attempt in range(attempts):
        result = runner(
            env,
            "connection-string", branch_id,
            "--project-id", project_id,
            "--role-name", "neondb_owner",
            "--database-name", "neondb",
            "--endpoint-type", "read_write",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if attempt + 1 < attempts:
            sleeper(delay_seconds)
    return ""


def psql(conn_string: str, *args: str) -> subprocess.CompletedProcess:
    # db_tap.psql_bin() rather than the bare name: it searches the Homebrew AND
    # user-local prefixes and, when there is no client at all, refuses with a
    # sentence naming the missing dependency. The bare name failed as
    # FileNotFoundError from inside subprocess, which named neither. This is the
    # lookup ops/p1-rebuild-gate.py already uses; the production-safety guards
    # above stay a deliberate copy of that gate's, per this file's header.
    return subprocess.run([db_tap.psql_bin(), conn_string, "-v", "ON_ERROR_STOP=1", *args],
                          capture_output=True, text=True, timeout=1800)


# Cover the conventional DATABASE_URL plus every DATABASE_URL_* spelling and
# every CARR credential spelling in use today (CARR_DB_*,
# CARR_IMPORT_DB_URL, CARR_RECONCILE_DB_URL, and future CARR_*DB*_URL names).
# This is intentionally broader than ops-record's current DSN_FOR list: the
# rehearsal boundary must not depend on which tool happens to consume an
# ambient credential today.
_DB_URL_ENV = re.compile(r"(?:DATABASE_URL|^CARR_.*DB.*_URL$)")


def _same_database_target(owner_dsn: str, jobs_dsn: str) -> bool:
    """Prove a routine DSN is the exact carr_jobs target of the owner DSN."""
    try:
        owner = urlsplit(owner_dsn)
        jobs = urlsplit(jobs_dsn)
        owner_port, jobs_port = owner.port, jobs.port
    except (AttributeError, ValueError):
        return False
    return (
        owner.scheme in {"postgres", "postgresql"}
        and jobs.scheme == owner.scheme
        and bool(owner.hostname)
        and jobs.hostname == owner.hostname
        and jobs_port == owner_port
        and jobs.path == owner.path
        and jobs.query == owner.query
        and unquote(jobs.username or "") == "carr_jobs"
    )


def _isolated_tool_env(owner_dsn: str, jobs_dsn: str | None = None) -> dict[str, str]:
    """Return a subprocess environment bound only to this rehearsal database.

    `tools/ops-record.py` deliberately loads ~/.config/carr/db.env as a last
    resort.  Passing the caller's ambient environment therefore is not an
    isolation boundary: a missing CARR_DB_JOBS_URL silently turns a rehearsal
    write into a production write.  Remove every known database-credential
    shape before installing the exact owner/jobs URLs for this ephemeral
    database.  The explicit jobs URL is required for routine ledger writes;
    there is no fallback to DATABASE_URL or the operator's db.env.
    """
    if not owner_dsn:
        raise ValueError("p1 integration tool invocation requires an owner DSN")
    env = {key: value for key, value in os.environ.items()
           if not _DB_URL_ENV.search(key)}
    env["DATABASE_URL"] = owner_dsn
    if jobs_dsn is not None:
        env["CARR_DB_JOBS_URL"] = jobs_dsn
    return env


def _jobs_login_dsn(owner_dsn: str, password: str) -> str:
    """Render a carr_jobs URL preserving the same host/database/query target."""
    parsed = urlsplit(owner_dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("integration owner DSN has no PostgreSQL host")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme,
                       f"carr_jobs:{quote(password, safe='')}@{host}",
                       parsed.path, parsed.query, ""))


def prepare_jobs_login(owner_dsn: str, *, connect=None, sql_module=None) -> str:
    """Set a throwaway carr_jobs password and return its same-database DSN.

    The schema snapshot creates carr_jobs with a random placeholder password.
    Resetting it in-process on the disposable database is the same probe used
    by p1-rebuild-gate; no credential is printed or persisted in the repo.
    """
    if connect is None or sql_module is None:
        import psycopg
        from psycopg import sql
        connect = connect or psycopg.connect
        sql_module = sql_module or sql
    password = secrets.token_urlsafe(32)
    with connect(owner_dsn, autocommit=True) as owner:
        with owner.cursor() as cur:
            cur.execute(sql_module.SQL("alter role {} password {}").format(
                sql_module.Identifier("carr_jobs"), sql_module.Literal(password)))
    return _jobs_login_dsn(owner_dsn, password)


def run_tool(script: str, *args: str, dsn: str, jobs_dsn: str | None,
             runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> subprocess.CompletedProcess:
    """Drive one of the system's real entry points against the rebuilt database.

    Owner operations receive DATABASE_URL; routine ops-record writes receive
    CARR_DB_JOBS_URL. Both are exact credentials for this branch/database.
    Ambient database URLs are removed first, so ops-record's db.env loader
    cannot silently redirect the run to a developer or production database.
    """
    if jobs_dsn is None:
        raise ValueError("p1 integration tool invocation requires an explicit carr_jobs DSN")
    if not _same_database_target(dsn, jobs_dsn):
        raise ValueError("p1 integration tool invocation requires a carr_jobs DSN for the exact owner database target")
    return runner([sys.executable, str(REPO / script), *args],
                  capture_output=True, text=True, timeout=600,
                  env=_isolated_tool_env(dsn, jobs_dsn))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-branch", action="store_true",
                    help="leave the ephemeral branch for debugging (it is a cost "
                         "and a database nobody watches — delete it by hand)")
    args = ap.parse_args()

    say("p1-integration-gate: the application works on a reconstructed environment")

    key = db_tap._neon_api_key()
    if not key and not os.environ.get("NEON_API_KEY"):
        say("p1-integration-gate: no Neon credential here — not configured (EX_CONFIG)")
        return 78

    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    if key:
        env["NEON_API_KEY"] = key

    staging_spec = db_tap.PROJECTS["staging"]
    prod_spec = db_tap.PROJECTS["production"]
    project_id = staging_spec.get("id") or db_tap._project_id_by_name(staging_spec["name"], env)

    # ── GUARD 0: never production ────────────────────────────────────────────
    if project_id == prod_spec.get("id"):
        sys.exit("p1-integration-gate: the staging name resolved to the PRODUCTION project "
                 "id. Refusing to branch, write to, or delete anything.")
    say(f"  guard 0: target project is staging ({staging_spec['name']}), not production")

    prod_host = host_of(db_tap.dsn(project="production"))
    stg_host = host_of(db_tap.dsn(project="staging"))

    branch_id = ""
    branch_name = f"integration-check-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    try:
        metering_rows = neon(env, "branches", "list", "--project-id", project_id, "--output", "json")
        if metering_rows.returncode != 0:
            sys.exit("p1-integration-gate: cannot read branch count for metering admission")
        try:
            authorize_metered_execution(
                json.loads((REPO / "ops/config/platform-metering.v1.json").read_text()),
                "neon-disposable-branch",
                {"requested_lifetime_minutes": 120, "cleanup_registered": True,
                 "active_nondefault_branches": sum(
                     1 for row in json.loads(metering_rows.stdout) if not row.get("default"))})
        except (MeteringRefusal, ValueError, TypeError) as exc:
            sys.exit(f"p1-integration-gate: metered branch refused: {exc}")
        out = neon(env, "branches", "create", "--project-id", project_id,
                   "--name", branch_name, "--output", "json")
        if out.returncode != 0:
            sys.exit(f"p1-integration-gate: could not create the branch: "
                     f"{out.stderr.strip()[:300]}")
        payload = json.loads(out.stdout)
        branch = payload.get("branch", payload)
        branch_id = branch.get("id", "")
        if not branch_id:
            sys.exit("p1-integration-gate: branch create returned no id")

        # ── GUARD 1: not the default branch ──────────────────────────────────
        listed = neon(env, "branches", "list", "--project-id", project_id, "--output", "json")
        default_ids = {b.get("id")
                       for b in (json.loads(listed.stdout) if listed.returncode == 0 else [])
                       if b.get("default")}
        if branch_id in default_ids:
            branch_id = ""      # do not let teardown delete it
            sys.exit("p1-integration-gate: branch create returned the DEFAULT branch id — "
                     "refusing to continue or delete")
        say(f"  guard 1: created {branch_name} ({branch_id}), which is not the default branch")

        # ── GUARD 2: a different host from production AND staging ────────────
        branch_dsn = wait_for_branch_connection_string(env, branch_id, project_id)
        if not branch_dsn:
            sys.exit("p1-integration-gate: could not obtain the branch connection string "
                     "after the bounded readiness window")
        branch_host = host_of(branch_dsn)
        if branch_host in (prod_host, stg_host):
            sys.exit("p1-integration-gate: the branch resolves to the same host as an "
                     "existing environment — refusing to write anywhere near it")
        say("  guard 2: the branch endpoint is its own host")

        # ── GUARD 3: a FRESH database, never the inherited one ───────────────
        made = psql(branch_dsn, "-c", f"create database {INTEGRATION_DB}")
        if made.returncode != 0:
            sys.exit(f"p1-integration-gate: could not create {INTEGRATION_DB}: "
                     f"{made.stderr.strip()[:300]}")
        head, _, query = branch_dsn.partition("?")
        target_dsn = head.rsplit("/", 1)[0] + "/" + INTEGRATION_DB + (f"?{query}" if query else "")
        say("  guard 3: working in a fresh database, not the inherited copy")
        say("")

        # ── reconstruct, the same way the rebuild gate does ──────────────────
        loaded = psql(target_dsn, "-f", str(REPO / "db" / "schema.sql"))
        if loaded.returncode != 0:
            check("0. the environment reconstructs before integration can be tested", False,
                  loaded.stderr.strip().splitlines()[-1][:200] if loaded.stderr.strip() else "")
            return 1
        migrated = subprocess.run(
            [sys.executable, str(REPO / "tools" / "migrate.py"), "--apply", "--yes"],
            capture_output=True, text=True, timeout=1800,
            env=_isolated_tool_env(target_dsn))
        if migrated.returncode != 0:
            check("0. the environment reconstructs before integration can be tested", False,
                  migrated.stderr.strip().splitlines()[-1][:200] if migrated.stderr.strip() else "")
            return 1
        say("  reconstructed: schema loaded, migrations applied")
        say("")

        # The schema creates carr_jobs with an in-process placeholder password.
        # Establish a disposable login now, before any real routine entry point
        # is called, and keep its DSN bound to this exact fresh database.
        try:
            jobs_dsn = prepare_jobs_login(target_dsn)
        except Exception as exc:
            check("0b. the rebuilt carr_jobs login binds to this database", False,
                  f"{type(exc).__name__}: {str(exc)[:160]}")
            return 1
        bound = _same_database_target(target_dsn, jobs_dsn)
        check("0b. the rebuilt carr_jobs login binds to this database", bound)
        if not bound:
            return 1
        say("")

        # ── 1. THE APPLICATION CAN POPULATE ITS OWN CATALOG ──────────────────
        # sync-registry applies ops/config/services.json into ops.service. It has
        # to run FIRST and that ordering is itself the finding: a run row names a
        # service, so on a genuinely fresh environment the operational layer is
        # unusable until the repo's declarations have been pushed into it. The
        # rebuild gate never discovers this, because a table can exist and be
        # empty and still satisfy `to_regclass`.
        synced = run_tool("tools/ops-record.py", "sync-registry",
                          dsn=target_dsn, jobs_dsn=jobs_dsn)
        check("1. the application populates its own service catalog from the repo",
              synced.returncode == 0,
              (synced.stderr.strip() or synced.stdout.strip()).splitlines()[-1][:200]
              if (synced.stderr.strip() or synced.stdout.strip()) else "")

        # ── 2. AND WRITES AN OPERATIONAL ROW THROUGH ITS OWN ENTRY POINT ─────
        # A run row is the right probe: ops-record.py is real production code, it
        # is what every scheduled job already writes through, and the row it
        # creates is operational rather than business data, so nothing about this
        # test invents a client record. environment=rehearsal because that is the
        # lane this branch IS.
        run_key = f"p1.integration.{uuid.uuid4().hex[:12]}"
        wrote = run_tool("tools/ops-record.py", "run",
                         "--service", "carr-mcp", "--environment", "rehearsal",
                         "--key", run_key, "--state", "succeeded",
                         "--kind", "check", "--source-kind", "wrapper",
                         "--source-ref", "ops/p1-integration-gate.py",
                         dsn=target_dsn, jobs_dsn=jobs_dsn)
        check("2. the application writes through its own entry point, not raw SQL",
              wrote.returncode == 0,
              (wrote.stderr.strip() or wrote.stdout.strip()).splitlines()[-1][:200]
              if (wrote.stderr.strip() or wrote.stdout.strip()) else "")

        # ── 3. AND READS THE SAME VALUE BACK ─────────────────────────────────
        # The assertion that matters: not that a write returned zero, but that
        # what comes back is what went in. An `ok` from a write confirms the call
        # parsed, never that the value landed (rule c53beeaa).
        back = psql(target_dsn, "-At", "-c",
                    f"select run_key from ops.run where run_key = '{run_key}'")
        check("3. the value read back is the one that went in",
              back.returncode == 0 and back.stdout.strip() == run_key,
              f"got {back.stdout.strip()!r}" if back.returncode == 0
              else back.stderr.strip()[:200])

        # ── 4. THE REBUILT GRANT SURFACE MATCHES PRODUCTION'S ────────────────
        # THE ASSERTION THIS REPLACED WAS WRONG, and the first real run is what
        # showed it. It asked whether carr_jobs could read EVERY table and
        # reported 107 failures. Production answers the same query with the same
        # 107 of 131: carr_jobs is a LEAST-PRIVILEGE role, so "can read
        # everything" is the opposite of correct and a gate asserting it would
        # have pushed the system toward over-granting.
        #
        # The question worth asking on a reconstruction is FIDELITY: does the
        # environment the repo just built grant what production grants? A missing
        # grant makes a table unusable; an extra one is a privilege leak. Both
        # are divergence, and divergence is the finding either way. Checked
        # against the baseline rather than against the spec (rule a6e6ab4e).
        grant_sql = ("select c.relnamespace::regnamespace || '.' || c.relname "
                     "from pg_class c where c.relkind='r' "
                     "and c.relnamespace::regnamespace::text in ('ops','public') "
                     "and has_table_privilege('carr_jobs', c.oid,'SELECT') order by 1")
        rebuilt = psql(target_dsn, "-At", "-c", grant_sql)
        if "does not exist" in (rebuilt.stderr or ""):
            check("4. the rebuilt grant surface matches production's", True,
                  "role carr_jobs absent on this branch — nothing to compare, not asserted")
        else:
            live = psql(db_tap.dsn(project="production"), "-At", "-c", grant_sql)
            rebuilt_grants = {l.strip() for l in rebuilt.stdout.splitlines() if l.strip()}
            live_grants = {l.strip() for l in live.stdout.splitlines() if l.strip()}
            if live.returncode != 0:
                check("4. the rebuilt grant surface matches production's", True,
                      "production unreadable from here — not asserted rather than guessed")
            else:
                absent, extra = sorted(live_grants - rebuilt_grants), sorted(rebuilt_grants - live_grants)
                check("4. the rebuilt grant surface matches production's",
                      rebuilt.returncode == 0 and not absent and not extra,
                      (f"{len(absent)} grant(s) production has and the rebuild lacks: "
                       + ", ".join(absent[:5]) if absent else "")
                      + ("; " if absent and extra else "")
                      + (f"{len(extra)} the rebuild grants and production does not: "
                         + ", ".join(extra[:5]) if extra else ""))

        # ── 5. A MIGRATION-CREATED OBJECT IS REACHABLE, NOT JUST PRESENT ─────
        # to_regclass answers "does it exist". This asks whether it can be
        # queried, which is what the system actually needs from it.
        for view in ("ops.v_service_environment_health", "public.v_decision_entry"):
            got = psql(target_dsn, "-At", "-c", f"select count(*) from {view}")
            check(f"5. {view} is queryable, not merely present",
                  got.returncode == 0,
                  got.stderr.strip().splitlines()[-1][:160] if got.stderr.strip() else "")

    finally:
        # ── teardown, on every exit path ─────────────────────────────────────
        if branch_id:
            if args.keep_branch:
                say(f"\n  teardown: KEEPING branch {branch_id} (--keep-branch).")
                say(f"            {db_tap.NEONCTL} branches delete {branch_id} "
                    f"--project-id {project_id}")
            else:
                gone = neon(env, "branches", "delete", branch_id, "--project-id", project_id)
                if gone.returncode == 0:
                    say(f"\n  ok    6. the ephemeral branch {branch_id} is gone")
                else:
                    FAILURES.append("6. teardown deleted the ephemeral branch")
                    say(f"\n  FAIL  6. COULD NOT DELETE branch {branch_id} — delete it by hand:")
                    say(f"            {db_tap.NEONCTL} branches delete {branch_id} "
                        f"--project-id {project_id}")

    print()
    if FAILURES:
        print(f"p1-integration-gate: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("p1-integration-gate: the application writes, reads back and is usable "
          "on an environment rebuilt from the repo alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
