#!/usr/bin/env python3
# ci: db-gate
# doctrine: doctorcre-v5-astra-integration-review
"""Verify the scheduled canary through its wrapper and persisted ledger row."""
from __future__ import annotations

import os
import plistlib
import secrets
import shutil
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "bin" / "run-scheduled.sh"
CANARY = REPO / "bin" / "gate-zero-canary.sh"
PLIST = REPO / "ops" / "launchd" / "com.carr.gate-zero-canary.plist"

SERVICE_KEY = "gate-zero-canary"
CANARY_RUN_KEY = "gatezero.canary"

# The reader's own vocabulary for the three clauses. The names moved once
# already -- `scheduler_readback_*` became `scheduler_observation_*`, and the
# module says so in its header -- so they are read out of the answer by the
# names the module emits TODAY and asserted one by one, rather than summarised.
HELD = "held"


def fail(message: str) -> int:
    print(f"gate-zero-scheduler-canary: FAIL {message}", file=sys.stderr)
    return 1


def required(cur: psycopg.Cursor[Any], label: str) -> tuple[Any, ...]:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return tuple(row)


def disposable_dsn() -> str:
    """The loopback carr_ci database this gate will write to, or a refusal.

    Same shape as ops/calendar-prebrief-projection-local-pg-gate.py, and for the
    same reason: this gate COMMITS a row, so "disposable" has to be a property
    the database itself confirms rather than a promise in the caller's head.
    """
    dsn = os.environ.get("CARR_LOCAL_PG_DSN") or os.environ.get("DATABASE_URL", "")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} \
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "the Gate Zero canary acceptance requires a loopback CARR_LOCAL_PG_DSN "
            "or DATABASE_URL; it records a real run row and will not point that at "
            "anything but a throwaway")
    return dsn


def assert_disposable(cur: psycopg.Cursor[Any]) -> None:
    cur.execute("select current_database(),current_user,current_setting('data_directory'),"
                "(select rolsuper from pg_roles where rolname=current_user)")
    database_name, role_name, data_directory, is_superuser = required(cur, "local cluster identity")
    data_path = os.path.realpath(str(data_directory))
    local_disposable = (os.path.isfile(os.path.join(data_path, "PG_VERSION"))
                        and os.path.basename(os.path.dirname(data_path)).startswith("carr-local-pg-ci."))
    hosted_disposable = (os.environ.get("GITHUB_ACTIONS") == "true"
                         and os.environ.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
                         and bool(os.environ.get("GITHUB_RUN_ID"))
                         and data_path == "/var/lib/postgresql/data")
    if database_name != "carr_ci" or role_name != "carr_ci" or is_superuser is not True \
            or not (local_disposable or hosted_disposable):
        raise RuntimeError("the Gate Zero canary acceptance requires a dedicated disposable "
                           "carr_ci database")


def jobs_dsn(dsn: str, password: str) -> str:
    """The same disposable database, addressed as carr_jobs with a throwaway password.

    tools/ops-record.py's `run` is connect("routine"), which reads ONLY
    CARR_DB_JOBS_URL and refuses a connection whose session_user is not
    carr_jobs. Setting it explicitly is also the safety belt: ops-record loads
    ~/.config/carr/db.env with setdefault, so a test that merely UNSETS the
    variable gets production's jobs DSN handed back to it -- the exact shape
    that recorded 46 fabricated rows into production in August.

    THE PASSWORD IS NOT DECORATION, and its absence is what turned this gate red
    on the hosted runner while it stayed green on this Mac (run 34690610990).
    The local disposable cluster ops/local-pg-ci.py builds authenticates
    loopback connections by trust, so a passwordless DSN connects; the runner's
    postgres service container requires a password on every host connection, so
    the same DSN dies at `fe_sendauth: no password supplied` inside ops-record,
    AFTER the wrapper has already spooled its row. Minting one here, the way
    ops/p1-integration-gate.py does, makes the gate carry its own credential
    into whichever cluster it was pointed at. It is generated per run, never
    printed and never written down.
    """
    parsed = urlparse(dsn)
    if parsed.port is None:
        raise RuntimeError("the disposable DSN names no port, so the jobs DSN cannot be derived")
    if not password:
        raise RuntimeError("the jobs DSN needs the throwaway password this gate just set")
    encoded = quote(password, safe="")
    host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
    rebuilt = urlunparse(parsed._replace(
        netloc=f"carr_jobs:{encoded}@{host}:{parsed.port}"))
    check = urlparse(rebuilt)
    if check.username != "carr_jobs" or check.password != encoded \
            or check.hostname not in {"127.0.0.1", "localhost", "::1"} \
            or check.port != parsed.port:
        raise RuntimeError("derived jobs DSN is not the loopback disposable database")
    return rebuilt


def install_root(work: Path) -> Path:
    """A private installation of the wrapper, with its own out/.

    Every top-level entry is symlinked so .venv, tools/ and lib/ resolve exactly
    as they do in the real tree; bin/ is a real directory holding byte-identical
    COPIES of the two scripts, which is what makes the wrapper's ${0:A:h:h} land
    here. The copies are digest-checked against the originals, because a test
    that runs a mutated copy of the program proves nothing about the program.
    """
    root = Path(tempfile.mkdtemp(prefix="carr-gate-zero-canary-", dir=work)).resolve()
    for entry in os.listdir(REPO):
        if entry in ("out", "bin", ".git"):
            continue
        os.symlink(REPO / entry, root / entry)
    (root / "bin").mkdir()
    (root / "out").mkdir()
    for source in (WRAPPER, CANARY):
        target = root / "bin" / source.name
        shutil.copyfile(source, target)
        target.chmod(0o755)
        if sha256(target.read_bytes()).hexdigest() != sha256(source.read_bytes()).hexdigest():
            raise RuntimeError(f"the installed copy of {source.name} is not the repository's file")
    return root


def scheduled_argv(root: Path) -> list[str]:
    """The LaunchAgent's OWN ProgramArguments, with {{REPO}} pointed at the
    install. Nothing is retyped: if the plist's arguments do not work, this gate
    is what says so."""
    program = plistlib.loads(PLIST.read_bytes())["ProgramArguments"]
    if not isinstance(program, list) or not all(isinstance(item, str) for item in program):
        raise RuntimeError("the canary plist has no string ProgramArguments")
    argv = [item.replace("{{REPO}}", str(root)) for item in program]
    # The two facts this gate refuses to lose if the plist is edited again: it
    # must go through the wrapper, and it must address THIS service and run key.
    if str(root / "bin" / "run-scheduled.sh") not in argv:
        raise RuntimeError("the canary plist does not invoke bin/run-scheduled.sh")
    if SERVICE_KEY not in argv or CANARY_RUN_KEY not in argv:
        raise RuntimeError("the canary plist does not address the canary service and run key")
    return argv


def child_env(jobs: str, spool: Path, state: Path) -> dict[str, str]:
    """The environment the dispatch and the flush share.

    CARR_DB_JOBS_URL is set, never merely cleared, for the reason
    `jobs_dsn` states. Everything else that could carry a second DSN is removed
    so the row can only land in the disposable database: ops-record's `run` is
    connect("routine") today, and the August incident happened precisely when a
    selftest neutralised the variable that mode NO LONGER read.
    """
    env = dict(os.environ)
    env["CARR_DB_JOBS_URL"] = jobs
    env["CARR_RUN_SPOOL_DB"] = str(spool)
    env["CARR_RUN_SCHEDULED_STATE_DIR"] = str(state)
    for leak in ("DATABASE_URL", "CARR_DB_URL", "CARR_DB_EXPORTER_URL",
                 "CARR_LOCAL_PG_DSN", "PGSERVICE"):
        env.pop(leak, None)
    return env


def main() -> int:
    dsn = disposable_dsn()
    checks = 0

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        assert_disposable(cur)
        cur.execute("select id from ops.service where key=%s", (SERVICE_KEY,))
        service = cur.fetchone()
        if service is None:
            return fail(f"ops.service has no {SERVICE_KEY!r} row — the canary is declared in "
                        "ops/config/services.json, so run `tools/control-plane.py sync` against "
                        "this database first (the migration class does that before gates)")
        checks += 1
        # carr_jobs exists in the schema as a NOLOGIN bundle on production, where
        # the deployed jobs credential is a separate login role. On this
        # throwaway it has to be able to connect for the recorder's identity
        # check to be the real one rather than a superuser wearing its name.
        cur.execute("select rolcanlogin from pg_roles where rolname='carr_jobs'")
        role = cur.fetchone()
        if role is None:
            return fail("this database has no carr_jobs role, so the recorder's own identity "
                        "check cannot be exercised")
        # LOGIN and a password, not LOGIN alone: the hosted runner's postgres
        # container authenticates host connections by password, so a jobs DSN
        # without one never reaches the recorder's identity check at all.
        secret = secrets.token_urlsafe(32)
        cur.execute(sql.SQL("alter role {} login password {}").format(
            sql.Identifier("carr_jobs"), sql.Literal(secret)))
        jobs = jobs_dsn(dsn, secret)
        # Any leftover from an earlier run of this gate, so the assertions below
        # are about the row this run produced.
        cur.execute("delete from ops.run where run_key=%s and service_id=%s",
                    (CANARY_RUN_KEY, service[0]))

    with tempfile.TemporaryDirectory(prefix="carr-gate-zero-canary-work-") as work_dir:
        work = Path(work_dir)
        root = install_root(work)
        spool = work / "run-spool.sqlite3"
        state = work / "state"
        state.mkdir()
        argv = scheduled_argv(root)
        env = child_env(jobs, spool, state)

        dispatch = subprocess.run(argv, capture_output=True, text=True, env=env,
                                  cwd=str(REPO), timeout=180)
        if dispatch.returncode != 0:
            return fail("the LaunchAgent's own ProgramArguments did not run the canary "
                        f"(exit {dispatch.returncode}): {dispatch.stderr.strip()[:400]}")
        checks += 1
        log = (root / "out" / "run-scheduled.log")
        log_text = log.read_text(encoding="utf-8") if log.exists() else ""
        if f"key={CANARY_RUN_KEY}" not in log_text or "state=succeeded" not in log_text:
            return fail(f"the wrapper recorded no succeeded dispatch for {CANARY_RUN_KEY}: "
                        f"{log_text.strip()[-400:]!r}")
        if "evidence_ref=none" in log_text or "receipt_code=none" not in log_text:
            return fail(f"the wrapper minted no receipt for this dispatch: {log_text.strip()[-400:]!r}")
        checks += 1

        flush = subprocess.run([sys.executable, str(REPO / "tools" / "ops-spool.py"), "flush"],
                               capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180)
        if flush.returncode != 0:
            return fail(f"the spool did not flush into the ledger: {flush.stderr.strip()[:400]}")
        checks += 1

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """select r.evidence_ref, r.source_kind, r.source_ref, r.state,
                      r.started_at, r.ended_at, r.observed_at
                 from ops.run r join ops.service s on s.id=r.service_id
                where s.key=%s and r.run_key=%s
                order by r.observed_at desc limit 1""",
            (SERVICE_KEY, CANARY_RUN_KEY))
        row = cur.fetchone()
        if row is None:
            return fail("the flush landed no ops.run row for the canary")
        evidence_ref, source_kind, source_ref, state, started_at, _ended, observed_at = row
        if not evidence_ref:
            return fail("the canary's ledger row carries no evidence_ref, which is the exact "
                        "state production was measured in on 2026-09-11")
        if (source_kind, source_ref) != ("wrapper", "bin/run-scheduled.sh"):
            return fail(f"the row was not written by the wrapper: {source_kind}/{source_ref}")
        if state != "succeeded":
            return fail(f"the canary recorded state {state!r}; a no-op job has nothing to fail at")
        if not observed_at > started_at:
            return fail("observed_at is not strictly after started_at, so no ordering is proven")
        checks += 1

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""delete from ops.run r using ops.service s
                        where s.id=r.service_id and s.key=%s and r.run_key=%s""",
                    (SERVICE_KEY, CANARY_RUN_KEY))

    print(f"gate-zero-scheduler-canary: {checks} checks passed — the plist's own arguments "
          "produce a successful ledger row with wrapper provenance and ordered timestamps")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        raise SystemExit(fail(str(exc)))
