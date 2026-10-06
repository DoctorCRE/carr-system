#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""V5-J102 live acceptance on a disposable loopback sibling database.

WHAT IT PROVES. The CRE lifecycle is a numbered migration (0704, sealed by 0705),
so the migrated schema already carries it; the gate refuses to run on a schema that
does not. Then, against real PostgreSQL:

  * the SQL fixture (mcp-server/test/cre-lifecycle-postgres.sql) passes as a
    verified partner (Joe, Dell) and runs its admitted groups as a sponsored agent;
  * the live Node suite (mcp-server/test/cre-lifecycle-live-pg.v5.test.mjs) passes:
    the full truth table and its refusals, Q103 concurrent merge/reconcile, the
    writer-credential attribution split, and the Q081 migration shadow.

WHERE. Never in the CI database itself. A sibling is copied from it (so it carries
every numbered migration, F01 and J102 included) and is always dropped.

CLUSTER STATE IS RESTORED. The two authority logins and carr_writer's LOGIN are
cluster-global; whatever this gate adds is recorded first and put back after, so a
later gate sees the cluster as it found it.
"""

from __future__ import annotations

import ipaddress
import secrets
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "mcp-server/test/cre-lifecycle-postgres.sql"
LIVE_SUITE = "test/cre-lifecycle-live-pg.v5.test.mjs"
AUTHORITY_LOGINS = ("carr_authority_joe", "carr_authority_dell")


def fail(message: str) -> int:
    print(f"cre-lifecycle-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def loopback(dsn: str) -> dict:
    parts = psycopg.conninfo.conninfo_to_dict(dsn)
    host = str(parts.get("hostaddr") or parts.get("host") or "")
    if not host or "," in host:
        raise RuntimeError("one explicit loopback host is required")
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        raise RuntimeError("refusing a non-loopback DATABASE_URL")
    return parts


@contextmanager
def sibling(base: str) -> Iterator[str]:
    parts = loopback(base)
    source = parts.get("dbname")
    if not source:
        raise RuntimeError("DATABASE_URL must name its database")
    name = f"j102_gate_{os.getpid()}_{time.time_ns()}"[:63]
    admin = psycopg.conninfo.make_conninfo(base, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
        cur.execute(sql.SQL("create database {} template {}").format(
            sql.Identifier(name), sql.Identifier(source)))
    try:
        yield psycopg.conninfo.make_conninfo(base, dbname=name)
    finally:
        # A failed drop is reported, never allowed to replace the error that got
        # us here; with no error in flight it fails the gate like anything else.
        in_flight = sys.exc_info()[0] is not None
        try:
            with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
                cur.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                            "where datname=%s and pid<>pg_backend_pid()", (name,))
                cur.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))
        except Exception as exc:  # noqa: BLE001
            print(f"cre-lifecycle-local-pg-gate: could not drop sibling {name}: {exc}", file=sys.stderr)
            if not in_flight:
                raise


LOGINS = AUTHORITY_LOGINS + ("carr_writer",)


@contextmanager
def principals(base: str) -> Iterator[str]:
    """Make the three logins usable for this run; restore the cluster exactly after.

    Yields a per-run password. Hosted CI authenticates TCP logins by password
    (scram), so each login gets this run's random password, and its previous
    verifier — read from pg_authid, possibly NULL — is put back afterwards, as are
    carr_writer's LOGIN flag and every role or grant this gate created. Every step
    is recorded BEFORE it is taken, so a failure partway through is undone too.
    """
    admin = psycopg.conninfo.make_conninfo(base, dbname="postgres")
    password = secrets.token_hex(24)
    created: list[str] = []
    granted: list[tuple[str, str]] = []
    prior_verifier: dict[str, str | None] = {}
    writer_could_login: bool | None = None
    try:
        with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
            cur.execute("select rolcanlogin from pg_roles where rolname='carr_writer'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("carr_writer does not exist; the migrated schema is incomplete")
            writer_could_login = bool(row[0])
            for login in AUTHORITY_LOGINS:
                cur.execute("select 1 from pg_roles where rolname=%s", (login,))
                if cur.fetchone() is None:
                    created.append(login)
                    cur.execute(sql.SQL("create role {} login").format(sql.Identifier(login)))
                for bundle in ("carr_authority", "carr_writer"):
                    cur.execute("select pg_has_role(%s, %s, 'member')", (login, bundle))
                    member = cur.fetchone()
                    if member is None or not member[0]:
                        granted.append((bundle, login))
                        cur.execute(sql.SQL("grant {} to {}").format(
                            sql.Identifier(bundle), sql.Identifier(login)))
            if not writer_could_login:
                cur.execute("alter role carr_writer login")
            for login in LOGINS:
                if login not in created:
                    cur.execute("select rolpassword from pg_authid where rolname=%s", (login,))
                    got = cur.fetchone()
                    prior_verifier[login] = None if got is None else got[0]
                cur.execute(sql.SQL("alter role {} password {}").format(
                    sql.Identifier(login), sql.Literal(password)))
        yield password
    finally:
        with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
            for login, verifier in prior_verifier.items():
                if verifier is None:
                    cur.execute(sql.SQL("alter role {} password null").format(sql.Identifier(login)))
                else:
                    cur.execute(sql.SQL("alter role {} password {}").format(
                        sql.Identifier(login), sql.Literal(verifier)))
            for bundle, login in granted:
                if login not in created:
                    cur.execute(sql.SQL("revoke {} from {}").format(
                        sql.Identifier(bundle), sql.Identifier(login)))
            for login in created:
                cur.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(login)))
            if writer_could_login is False:
                cur.execute("alter role carr_writer nologin")


def psql(dsn: str, path: Path, *, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("psql")
    if binary is None:
        raise RuntimeError("psql is required")
    return subprocess.run([binary, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn, "-f", str(path)],
                          env={**os.environ, **(env or {})}, text=True, capture_output=True, timeout=900)


def as_login(dsn: str, login: str, password: str) -> str:
    return psycopg.conninfo.make_conninfo(dsn, user=login, password=password)


def url_for(dsn: str, login: str | None = None, login_password: str = "") -> str:
    """node-pg reads URLs, not key=value conninfo. A login's password is this
    run's throwaway one, on a loopback sibling that is dropped at the end."""
    from urllib.parse import quote
    parts = psycopg.conninfo.conninfo_to_dict(dsn)
    host = parts.get("hostaddr") or parts.get("host") or "127.0.0.1"
    port = parts.get("port") or "5432"
    user = str(login or parts.get("user") or "")
    password = login_password if login else str(parts.get("password") or "")
    auth = quote(user) + (":" + quote(password) if password else "")
    return f"postgresql://{auth}@{host}:{port}/{quote(str(parts['dbname']))}"


def main() -> int:
    base = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not base:
        return fail("a disposable loopback DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with principals(base) as password, sibling(base) as dsn:
            with psycopg.connect(dsn) as con, con.cursor() as cur:
                cur.execute("select to_regprocedure('ops.f01_principal()') is not null, "
                            "to_regprocedure('ops.j102_sponsoring_partner()') is not null, "
                            "exists(select 1 from public.schema_migrations "
                            "where filename='0704_cre_lifecycle.sql')")
                probe = cur.fetchone()
            if not (probe and all(probe)):
                return fail("the migrated schema does not carry F01 and J102 (0704); "
                            "apply every numbered migration first")

            # The SQL fixture: both partners must pass every runnable group; the
            # agent runs the groups its class admits and names the rest.
            for who, login, options in (
                ("joe", "carr_authority_joe", ""),
                ("dell", "carr_authority_dell", ""),
                # The server sets the sponsor transaction-locally on every write
                # (mcp.js setWriterActorContext); a session stands in for it here.
                ("agent", "carr_writer",
                 "-c carr.acting_actor_slug=claude-ci -c carr.sponsoring_human_slug=joe"),
            ):
                run = psql(as_login(dsn, login, password), FIXTURE,
                           env={"PGOPTIONS": f"--client-min-messages=notice {options}".strip()})
                out = run.stdout + run.stderr
                if run.returncode:
                    return fail(f"SQL fixture as {who} failed: {out[-1200:]}")
                expected = "PARTIAL RUN" if who == "agent" else "ALL RUNNABLE GROUPS PASSED"
                if expected not in out:
                    return fail(f"SQL fixture as {who} did not report {expected!r}: {out[-800:]}")

            env = {
                **os.environ,
                "CARR_J102_LIVE_PG_DSN_JOE": url_for(dsn, "carr_authority_joe", password),
                "CARR_J102_LIVE_PG_DSN_DELL": url_for(dsn, "carr_authority_dell", password),
                "CARR_J102_LIVE_PG_DSN_WRITER": url_for(dsn, "carr_writer", password),
                "CARR_J102_LIVE_PG_DSN_OWNER": url_for(dsn),
            }
            # The shadow test requires an empty legacy table; the copied CI
            # database may carry seeded deals, and this is a throwaway sibling.
            with psycopg.connect(dsn) as con, con.cursor() as cur:
                cur.execute("set session_replication_role = replica")
                cur.execute("delete from public.deal")
            live = subprocess.run(["node", "--test", LIVE_SUITE], cwd=REPO / "mcp-server",
                                  env=env, text=True, capture_output=True, timeout=1800)
            if live.returncode:
                return fail(f"live suite failed: {(live.stdout + live.stderr)[-2000:]}")
            if "# skipped 0" not in live.stdout and "ℹ skipped 0" not in live.stdout:
                return fail(f"live suite skipped tests: {live.stdout[-800:]}")
    except Exception as exc:  # noqa: BLE001 - report the exact disposable-DB failure
        return fail(str(exc))
    print("cre-lifecycle live acceptance passed: fixture (joe, dell, agent) and live suite "
          "(truth table, Q103 merge/reconcile, credential split, Q081 shadow)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
