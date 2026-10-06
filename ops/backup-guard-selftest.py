#!/usr/bin/env python3
"""Disposable-PostgreSQL black-box fixtures for ``bin/backup-dump.sh``.

The wrapper CLI is the seam.  This suite never imports the guard helper and
never reads a production URL or private key.  A caller supplies one explicitly
disposable local administrator DSN; the suite creates uniquely named databases,
the exact ``carr_backup`` login, and a fresh age identity, then removes them.

Without an explicit DSN the suite runs its portable authority/source checks and
labels the live proof NOT RUN. WR54 evidence must also include a run with
``CARR_LOCAL_PG_DSN`` set to literal loopback or an owned Unix socket.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "backup-dump.sh"
GUARD = ROOT / "bin" / "backup-guard.py"
PG_DUMP = Path(os.environ.get("CARR_WR54_PG_DUMP", "/opt/homebrew/opt/libpq/bin/pg_dump"))
PSQL = Path(os.environ.get("CARR_WR54_PSQL", "/opt/homebrew/opt/postgresql@17/bin/psql"))
AGE = Path(os.environ.get("CARR_WR54_AGE", "/opt/homebrew/bin/age"))
AGE_KEYGEN = Path(os.environ.get("CARR_WR54_AGE_KEYGEN", "/opt/homebrew/bin/age-keygen"))

PASS = 0
FAIL: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}  {detail}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def conninfo_text(value: str | int | None, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"CARR_LOCAL_PG_DSN {name} must be text")
    return value


def require_local_dsn(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("CARR_LOCAL_PG_DSN must be a PostgreSQL URI")
    info = conninfo_to_dict(raw)
    if info.get("service") or info.get("servicefile"):
        raise ValueError("service-based DSNs are not fixture authority")
    host = conninfo_text(info.get("host"), "host")
    hostaddr = conninfo_text(info.get("hostaddr"), "hostaddr")
    port = conninfo_text(info.get("port"), "port")
    if any("," in value for value in (host, hostaddr, port)):
        raise ValueError("multi-host DSNs are not permitted")
    if hostaddr and hostaddr not in {"127.0.0.1", "::1"}:
        raise ValueError("effective hostaddr is not literal loopback")
    if host in {"127.0.0.1", "::1"} and (not hostaddr or hostaddr == host):
        return raw
    if host.startswith("/"):
        socket_dir = Path(host).resolve(strict=True)
        mode = socket_dir.stat()
        if socket_dir.is_dir() and mode.st_uid == os.getuid() and not (mode.st_mode & 0o022):
            return raw
    raise ValueError(
        "fixture DSN must use literal 127.0.0.1/::1 or an owned, non-writable Unix socket directory"
    )


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@dataclass
class Run:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Fixture:
    def __init__(self, admin_dsn: str, scratch: Path):
        self.admin_dsn = admin_dsn
        self.scratch = scratch
        suffix = uuid.uuid4().hex[:10]
        self.source_db = f"carr_wr54_source_{suffix}"
        self.restore_db = f"carr_wr54_restore_{suffix}"
        admin_info: dict[str, str] = {}
        for name, value in conninfo_to_dict(admin_dsn).items():
            if not isinstance(value, str):
                raise ValueError(f"administrator DSN {name} must be text")
            admin_info[name] = value
        self.admin_info = admin_info
        self.admin_user = ""
        self.repo = scratch / "fixture-repo"
        self.identity = scratch / "fixture-age-identity.txt"
        self.real_bin = scratch / "real-bin"
        self.real_bin.mkdir()

    def server_dsn(self, database: str, user: str | None = None) -> str:
        values = dict(self.admin_info)
        values["dbname"] = database
        if user is not None:
            values["user"] = user
            values.pop("password", None)
        return make_conninfo(**values)

    def backup_url(self, *, user: str = "carr_backup") -> str:
        host = self.admin_info.get("host", "")
        port = self.admin_info.get("port", "5432")
        if host not in {"127.0.0.1", "::1"}:
            raise RuntimeError("the black-box wrapper fixture currently requires literal TCP loopback")
        host_part = f"[{host}]" if host == "::1" else host
        return (
            f"postgresql://{quote(user)}:synthetic-only@{host_part}:{port}/"  # ci-secret-scan: allow — synthetic disposable loopback fixture DSN
            f"{quote(self.source_db)}?sslmode=disable"
        )

    def admin(self, database: str | None = None, *, autocommit: bool = True):
        return psycopg.connect(
            self.server_dsn(database or self.admin_info.get("dbname", "postgres")),
            autocommit=autocommit,
        )

    def execute_source(self, statement: str) -> None:
        with psycopg.connect(self.server_dsn(self.source_db)) as conn:
            conn.execute(statement)

    def setup_cluster_objects(self) -> None:
        with self.admin() as conn:
            row = conn.execute(
                "select current_user, rolsuper, rolcreatedb from pg_roles where rolname=current_user"
            ).fetchone()
            if row is None or not row[1] or not row[2]:
                raise RuntimeError("fixture DSN must name its disposable cluster superuser")
            self.admin_user = str(row[0])
            if conn.execute("select 1 from pg_roles where rolname='carr_backup'").fetchone():
                raise RuntimeError("refusing cluster with pre-existing carr_backup; use a fresh disposable cluster")
            conn.execute("create role carr_backup login inherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls")
            conn.execute("create role wr54_inactive nologin nosuperuser")
            conn.execute(sql.SQL("create database {} owner {}").format(
                sql.Identifier(self.source_db), sql.Identifier(self.admin_user)))
            conn.execute(sql.SQL("create database {} owner {}").format(
                sql.Identifier(self.restore_db), sql.Identifier(self.admin_user)))

    def cleanup_cluster_objects(self) -> None:
        try:
            with self.admin() as conn:
                for database in (self.source_db, self.restore_db):
                    conn.execute(
                        "select pg_terminate_backend(pid) from pg_stat_activity where datname=%s and pid<>pg_backend_pid()",
                        (database,),
                    )
                    conn.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(database)))
                conn.execute("drop role if exists carr_backup")
                conn.execute("drop role if exists wr54_inactive")
        except Exception as exc:
            check("disposable cluster cleanup", False, repr(exc))

    def seed(self) -> None:
        body = """
        create schema ops;
        create type public.wr54_state as enum ('ready','done');
        create sequence public.shared_unowned_seq start with 10;
        create table public.ordinary (
          id bigint primary key default nextval('public.shared_unowned_seq'),
          state public.wr54_state not null default 'ready',
          note text not null check (length(note)>0), touched boolean not null default false
        );
        create table public.shared_second (
          id bigint default nextval('public.shared_unowned_seq'), note text
        );
        create index ordinary_note_idx on public.ordinary(note);
        create function public.mark_touched() returns trigger language plpgsql as $$
        begin new.touched := true; return new; end $$;
        create trigger ordinary_touch before insert on public.ordinary
          for each row execute function public.mark_touched();
        create function public.wr54_double(value integer) returns integer
          language sql immutable as $$ select value * 2 $$;
        create view public.ordinary_ready as select id,note from public.ordinary where state='ready';
        create table public.parted(id integer, note text) partition by range(id);
        create table public.parted_low partition of public.parted for values from (0) to (10);
        create table public.parted_high partition of public.parted for values from (10) to (20);
        create table public.inherit_parent(id integer primary key, note text);
        create table public.inherit_child(extra text) inherits(public.inherit_parent);
        create table public.rls_visible(id integer primary key, secret text not null);
        alter table public.rls_visible enable row level security;
        create policy wr54_full_read on public.rls_visible for select to carr_backup using (true);
        create table public.spoof_target(id integer primary key, note text);
        create table public.spoof_sink(payload text);
        create table public.large_payload(id integer primary key, payload text not null);
        create table ops.audit(id integer primary key, note text not null);
        insert into public.ordinary(note) values('ordinary-sentinel');
        insert into public.shared_second(note) values('shared-sequence-sentinel');
        insert into public.parted values(3,'low-sentinel'),(13,'high-sentinel');
        insert into public.inherit_parent values(1,'parent-sentinel');
        insert into public.inherit_child values(2,'child-sentinel','extra-sentinel');
        insert into public.rls_visible values(1,'rls-sentinel');
        insert into public.spoof_target values(1,'toc-target');
        insert into public.spoof_sink values('ordinary-copy-row');
        insert into public.large_payload
          select n, repeat(md5(n::text),4096) from generate_series(1,20) n;
        insert into ops.audit values(1,'ops-sentinel');
        select setval('public.shared_unowned_seq',77,true);
        grant usage on schema public,ops to carr_backup;
        grant select on all tables in schema public,ops to carr_backup;
        grant select on all sequences in schema public,ops to carr_backup;
        """
        self.execute_source(body)

    def prepare_private_repo(self) -> None:
        (self.repo / "bin").mkdir(parents=True)
        shutil.copyfile(WRAPPER, self.repo / "bin" / WRAPPER.name)
        (self.repo / "bin" / WRAPPER.name).chmod(0o700)
        check("private fixture wrapper is byte-identical", digest(WRAPPER) == digest(self.repo / "bin" / WRAPPER.name))
        if GUARD.is_file():
            shutil.copyfile(GUARD, self.repo / "bin" / GUARD.name)
            (self.repo / "bin" / GUARD.name).chmod(0o700)
            check("private fixture guard is byte-identical", digest(GUARD) == digest(self.repo / "bin" / GUARD.name))
        else:
            check("bounded guard helper exists for the wrapper", False, str(GUARD))
        active_venv = Path(sys.prefix).resolve()
        if sys.prefix == sys.base_prefix or not (active_venv / "pyvenv.cfg").is_file():
            raise RuntimeError("backup-guard-selftest must run from the repository virtual environment")
        fixture_venv = self.repo / ".venv"
        fixture_venv.symlink_to(active_venv, target_is_directory=True)
        fixture_python = fixture_venv / "bin" / "python"
        imported = subprocess.run(
            [str(fixture_python), "-c", "import psycopg; print(psycopg.__version__)"],
            text=True, capture_output=True, timeout=15, check=False,
        )
        check(
            "private fixture interpreter retains repository psycopg",
            imported.returncode == 0 and bool(imported.stdout.strip()),
            imported.stderr,
        )
        if imported.returncode:
            raise RuntimeError("private fixture interpreter cannot import psycopg")
        generated = subprocess.run(
            [str(AGE_KEYGEN), "-o", str(self.identity)], text=True,
            capture_output=True, timeout=15, check=False,
        )
        if generated.returncode:
            raise RuntimeError(generated.stderr)
        public = subprocess.run(
            [str(AGE_KEYGEN), "-y", str(self.identity)], text=True,
            capture_output=True, timeout=15, check=True,
        ).stdout.strip()
        (self.repo / "backups-public-key.txt").write_text(public + "\n", encoding="utf-8")
        check("positive fixture uses a fresh synthetic age identity", public.startswith("age1"))

    def run_backup(
        self, output: Path, *, pg_dump: Path = PG_DUMP, path_prefix: Path | None = None,
        extra_env: dict[str, str] | None = None, url: str | None = None, timeout: int = 30,
    ) -> Run:
        output.mkdir(parents=True, exist_ok=True)
        clean_names = {
            "DATABASE_URL", "BACKUP_DATABASE_URL", "CARR_DB_BACKUP_URL", "NEON_API_KEY",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "CLOUDFLARE_API_TOKEN",
            "PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPORT",
            "PGDATABASE", "PGUSER", "PGPASSFILE", "PGPASSWORD", "PGOPTIONS",
        }
        env = {k: v for k, v in os.environ.items() if k not in clean_names}
        prefix = str(path_prefix) + os.pathsep if path_prefix else ""
        env.update({
            "CARR_DB_BACKUP_URL": url or self.backup_url(),
            "BACKUP_SKIP_R2": "1",
            "BACKUP_OUTPUT_DIR": str(output),
            "PG_DUMP_BIN": str(pg_dump),
            "PATH": prefix + f"{AGE.parent}:/usr/bin:/bin:/usr/local/bin",
        })
        if extra_env:
            env.update(extra_env)
        proc = subprocess.Popen(
            ["/bin/zsh", str(self.repo / "bin" / "backup-dump.sh")],
            cwd=self.repo, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return Run(proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            return Run(124, stdout, stderr, True)

    @staticmethod
    def artifact(output: Path) -> Path:
        artifacts = list(output.glob("carr-*.sql.age"))
        if len(artifacts) != 1:
            raise AssertionError(f"expected one artifact, got {artifacts}")
        return artifacts[0]


def seed_previous(output: Path) -> tuple[Path, str]:
    output.mkdir(parents=True, exist_ok=True)
    prior = output / "carr-20000101.sql.age"
    prior.write_bytes(b"previous-artifact\n" * 65536)
    return prior, digest(prior)


def expect_refusal(fx: Fixture, label: str, **run_args: object) -> Run:
    output = fx.scratch / "refusals" / label
    prior, before = seed_previous(output)
    run = fx.run_backup(output, **run_args)  # type: ignore[arg-type]
    promoted = [p for p in output.glob("carr-*.sql.age") if p != prior]
    ok = (
        not run.timed_out and run.returncode != 0 and digest(prior) == before
        and not promoted and not list(output.glob("*.tmp"))
    )
    check(label, ok, f"rc={run.returncode} timeout={run.timed_out} promoted={promoted}\n{run.stdout}{run.stderr}")
    return run


def refusal_result(output: Path, prior: Path, before: str, run: Run) -> bool:
    promoted = [p for p in output.glob("carr-*.sql.age") if p != prior]
    return (
        not run.timed_out and run.returncode != 0 and digest(prior) == before
        and not promoted and not list(output.glob("*.tmp"))
    )


def wait_for(path: Path, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def barrier_pg_dump(path: Path, *, hold_child: bool = False) -> None:
    if not hold_child:
        executable(path, f"""#!{sys.executable}
import os, pathlib, sys, time
pathlib.Path(os.environ['CARR_BARRIER_SIGNAL']).write_text('ready')
release=pathlib.Path(os.environ['CARR_BARRIER_RELEASE'])
deadline=time.monotonic()+15
while not release.exists() and time.monotonic()<deadline: time.sleep(0.02)
if not release.exists(): raise SystemExit(92)
os.execv(os.environ['CARR_REAL_PG_DUMP'],[os.environ['CARR_REAL_PG_DUMP'],*sys.argv[1:]])
""")
    else:
        executable(path, f"""#!{sys.executable}
import os, pathlib, subprocess, sys, time
p=subprocess.Popen([os.environ['CARR_REAL_PG_DUMP'],*sys.argv[1:]],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
pathlib.Path(os.environ['CARR_BARRIER_SIGNAL']).write_text('child-started')
release=pathlib.Path(os.environ['CARR_BARRIER_RELEASE'])
deadline=time.monotonic()+15
while not release.exists() and time.monotonic()<deadline: time.sleep(0.02)
if not release.exists(): p.kill()
out,err=p.communicate()
sys.stdout.buffer.write(out); sys.stderr.buffer.write(err)
raise SystemExit(p.returncode if release.exists() else 92)
""")


def guard_waiting_pid(fx: Fixture) -> int | None:
    with fx.admin(fx.source_db) as conn:
        row = conn.execute("""
          select pid from pg_stat_activity
           where datname=current_database() and usename='carr_backup'
             and application_name<>'pg_dump' and wait_event_type='Lock'
             and query ilike '%lock table%'
           order by backend_start limit 1
        """).fetchone()
        return int(row[0]) if row else None


def active_backends(fx: Fixture) -> tuple[list[int], list[int]]:
    with fx.admin(fx.source_db) as conn:
        rows = conn.execute("""
          select pid,application_name,backend_xmin is not null
            from pg_stat_activity
           where datname=current_database() and usename='carr_backup'
           order by pid
        """).fetchall()
    dumps = [int(pid) for pid, app, has_snapshot in rows if app == "pg_dump" and has_snapshot]
    guards = [int(pid) for pid, app, _ in rows if app != "pg_dump"]
    return guards, dumps


def concurrency_cases(fx: Fixture) -> None:
    timeout_env = {
        "BACKUP_TIMEOUT_SECONDS": "4", "BACKUP_LOCK_TIMEOUT_MS": "350",
        "BACKUP_POLL_SECONDS": "0.02",
    }
    topology_env = {
        "BACKUP_TIMEOUT_SECONDS": "8", "BACKUP_LOCK_TIMEOUT_MS": "4000",
        "BACKUP_POLL_SECONDS": "0.02",
    }

    # The pg_dump executable is invoked only after the guard has exported its
    # snapshot. Holding that public process seam lets a new table commit in the
    # interval without reaching into the helper.
    barrier_dir = fx.scratch / "barriers"
    barrier_dir.mkdir()
    shim = barrier_dir / "pg_dump-before-import"
    barrier_pg_dump(shim)
    signal_path, release = barrier_dir / "after-export.signal", barrier_dir / "after-export.release"
    output = barrier_dir / "after-export-out"
    fake_bin = barrier_dir / "plain-age"
    fake_bin.mkdir()
    executable(fake_bin / "age", "#!/bin/sh\ncat\n")
    env = {"CARR_REAL_PG_DUMP": str(PG_DUMP), "CARR_BARRIER_SIGNAL": str(signal_path),
           "CARR_BARRIER_RELEASE": str(release), **timeout_env}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fx.run_backup, output, pg_dump=shim, path_prefix=fake_bin,
                             extra_env=env, timeout=12)
        signalled = wait_for(signal_path)
        if signalled:
            fx.execute_source("create table public.after_export(id integer); grant select on public.after_export to carr_backup")
        release.touch()
        run = future.result()
    artifact_text = ""
    if run.returncode == 0:
        artifact_text = Fixture.artifact(output).read_text(encoding="utf-8", errors="replace")
    check("table committed after snapshot export is excluded by imported snapshot",
          signalled and run.returncode == 0 and "CREATE TABLE public.after_export" not in artifact_text,
          f"signal={signalled} rc={run.returncode}")
    if signalled:
        fx.execute_source("drop table public.after_export")

    # Revoke after guard validation but before pg_dump's first COPY. Imported
    # snapshot visibility does not freeze privileges; the affected read must die.
    signal_path, release = barrier_dir / "revoke.signal", barrier_dir / "revoke.release"
    output = barrier_dir / "revoke-out"
    prior, before = seed_previous(output)
    env.update({"CARR_BARRIER_SIGNAL": str(signal_path), "CARR_BARRIER_RELEASE": str(release)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fx.run_backup, output, pg_dump=shim, extra_env=env, timeout=12)
        signalled = wait_for(signal_path)
        if signalled:
            fx.execute_source("revoke select on public.spoof_target from carr_backup")
        release.touch()
        run = future.result()
    check("SELECT revoked before affected COPY refuses promotion",
          signalled and refusal_result(output, prior, before, run),
          f"signal={signalled} rc={run.returncode} timeout={run.timed_out}")
    fx.execute_source("grant select on public.spoof_target to carr_backup")

    # Killing the one original guard before pg_dump imports its snapshot must
    # never trigger a reconnect or allow the producer to continue.
    signal_path, release = barrier_dir / "death-before.signal", barrier_dir / "death-before.release"
    output = barrier_dir / "death-before-out"
    prior, before = seed_previous(output)
    env.update({"CARR_BARRIER_SIGNAL": str(signal_path), "CARR_BARRIER_RELEASE": str(release)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fx.run_backup, output, pg_dump=shim, extra_env=env, timeout=12)
        signalled = wait_for(signal_path)
        guards, _ = active_backends(fx) if signalled else ([], [])
        if guards:
            with fx.admin(fx.source_db) as conn:
                conn.execute("select pg_terminate_backend(%s)", (guards[0],))
        release.touch()
        run = future.result()
    check("guard death before snapshot import refuses promotion",
          signalled and len(guards) == 1 and refusal_result(output, prior, before, run),
          f"signal={signalled} guards={guards} rc={run.returncode}")

    # Hold the real pg_dump child after it has connected. Once backend_xmin is
    # visible, snapshot import has occurred; killing the other carr_backup
    # backend exercises supervision during the dump rather than before it.
    hold = barrier_dir / "pg_dump-after-import"
    barrier_pg_dump(hold, hold_child=True)
    signal_path, release = barrier_dir / "death-after.signal", barrier_dir / "death-after.release"
    output = barrier_dir / "death-after-out"
    prior, before = seed_previous(output)
    env.update({"CARR_BARRIER_SIGNAL": str(signal_path), "CARR_BARRIER_RELEASE": str(release)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fx.run_backup, output, pg_dump=hold, extra_env=env, timeout=12)
        child_started = wait_for(signal_path)
        guards = []
        dumps: list[int] = []
        deadline = time.monotonic() + 6
        while child_started and time.monotonic() < deadline:
            guards, dumps = active_backends(fx)
            if guards and dumps:
                break
            time.sleep(0.05)
        if guards and dumps:
            with fx.admin(fx.source_db) as conn:
                conn.execute("select pg_terminate_backend(%s)", (guards[0],))
        release.touch()
        run = future.result()
    check("guard death after snapshot import refuses promotion",
          child_started and len(guards) == 1 and bool(dumps)
          and refusal_result(output, prior, before, run),
          f"started={child_started} guards={guards} dumps={dumps} rc={run.returncode}")

    def topology_case(label: str, mutate: str, cleanup: str) -> None:
        output = barrier_dir / label
        prior, before = seed_previous(output)
        blocker = psycopg.connect(fx.server_dsn(fx.source_db))
        blocker.execute("lock table public.ordinary in access exclusive mode")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fx.run_backup, output, extra_env=topology_env, timeout=14)
            pid = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and pid is None:
                pid = guard_waiting_pid(fx)
                if pid is None:
                    time.sleep(0.05)
            mutated = False
            if pid is not None:
                fx.execute_source(mutate)
                mutated = True
            blocker.rollback(); blocker.close()
            run = future.result()
        check(label, pid is not None and mutated and refusal_result(output, prior, before, run),
              f"guard_wait_pid={pid} mutated={mutated} rc={run.returncode}")
        if mutated:
            fx.execute_source(cleanup)

    topology_case(
        "new table between initial census and exported snapshot",
        "create table public.mid_guard_table(id integer); grant select on public.mid_guard_table to carr_backup",
        "drop table public.mid_guard_table",
    )
    fx.execute_source("create table public.attach_candidate(id integer,note text); grant select on public.attach_candidate to carr_backup")
    topology_case(
        "partition ATTACH between lock plan and topology census",
        "alter table public.parted attach partition public.attach_candidate for values from (20) to (30)",
        "alter table public.parted detach partition public.attach_candidate",
    )
    fx.execute_source("drop table public.attach_candidate")
    topology_case(
        "partition DETACH between lock plan and topology census",
        "alter table public.parted detach partition public.parted_high",
        "alter table public.parted attach partition public.parted_high for values from (10) to (20)",
    )

    # A queued ACCESS EXCLUSIVE policy change must stand ahead of the guard's
    # ACCESS SHARE request and produce the configured lock-timeout refusal.
    blocker = psycopg.connect(fx.server_dsn(fx.source_db))
    blocker.execute("lock table public.rls_visible in access share mode")
    ddl_done = threading.Event()
    ddl_error: list[str] = []
    def queued_ddl() -> None:
        try:
            with psycopg.connect(fx.server_dsn(fx.source_db)) as conn:
                conn.execute("alter table public.rls_visible disable row level security")
        except Exception as exc:
            ddl_error.append(repr(exc))
        finally:
            ddl_done.set()
    thread = threading.Thread(target=queued_ddl, daemon=True)
    thread.start()
    time.sleep(0.2)
    output = barrier_dir / "queued-policy-ddl"
    prior, before = seed_previous(output)
    started = time.monotonic()
    run = fx.run_backup(output, extra_env=timeout_env, timeout=8)
    elapsed = time.monotonic() - started
    blocker.rollback(); blocker.close(); thread.join(timeout=5)
    if ddl_done.is_set() and not ddl_error:
        fx.execute_source("alter table public.rls_visible enable row level security")
    check("queued policy DDL causes bounded lock refusal",
          refusal_result(output, prior, before, run) and elapsed < 5,
          f"rc={run.returncode} timeout={run.timed_out} elapsed={elapsed:.2f}s ddl_error={ddl_error}")


def transformer(path: Path) -> None:
    executable(path, f"""#!{sys.executable}
import os, re, subprocess, sys
real = os.environ['CARR_REAL_PG_DUMP']
mode = os.environ.get('CARR_TOC_MODE','valid')
p = subprocess.run([real, *sys.argv[1:]], capture_output=True)
sys.stderr.buffer.write(p.stderr)
if p.returncode: raise SystemExit(p.returncode)
raw = p.stdout
pattern = re.compile(
    rb'^-- TOC entry [0-9]+ \\(class 1259 OID ([0-9]+)\\)\\n'
    rb'(?:-- Dependencies: [^\\n]*\\n)?'
    rb'-- Name: ([^;\\n]+); Type: TABLE; Schema: (public|ops); Owner: -'
    rb'(?:; Tablespace: [^;\\n]+)?\\n', re.M)
matches = list(pattern.finditer(raw))
target = next((m for m in matches if m.group(2) == b'spoof_target'), None)
marker = target.group(0) if target else b'-- TOC entry 999 (class 1259 OID 999999999)\\n-- Name: spoof_target; Type: TABLE; Schema: public; Owner: -\\n'
if mode == 'missing' and target: raw = raw[:target.start()] + raw[target.end():]
elif mode == 'duplicate' and target: raw = raw[:target.end()] + marker + raw[target.end():]
elif mode == 'extra':
    raw += b'\\n-- TOC entry 998 (class 1259 OID 999999998)\\n-- Name: injected_extra; Type: TABLE; Schema: public; Owner: -\\n'
elif mode.startswith('omit_spoof_'):
    if target: raw = raw[:target.start()] + raw[target.end():]
    payload = marker
    kind = mode.removeprefix('omit_spoof_')
    if kind == 'copy':
        needle = b'COPY public.spoof_sink (payload) FROM stdin;\\n'
        raw = raw.replace(needle, needle + payload, 1)
    elif kind == 'string': raw += b"\\nSELECT '" + payload.replace(b"'", b"''") + b"';\\n"
    elif kind == 'dollar': raw += b'\\nDO $wr54$\\nBEGIN\\nPERFORM 1;\\n' + payload + b'\\nEND\\n$wr54$;\\n'
    elif kind == 'nested_comment': raw += b'\\n/* outer /* inner\\n' + payload + b'\\n*/ outer */\\n'
elif mode == 'non_table_decoys':
    for typ in (b'TABLE DATA',b'TABLE ATTACH',b'SEQUENCE',b'VIEW'):
        raw += b'\\n-- TOC entry 997 (class 1259 OID 999999997)\\n-- Name: decoy; Type: ' + typ + b'; Schema: public; Owner: -\\n'
elif mode == 'truncated_string': raw += b"\\nSELECT 'unterminated"
elif mode == 'truncated_identifier': raw += b'\\nSELECT "unterminated'
elif mode == 'truncated_dollar': raw += b'\\nDO $wr54$ unterminated'
elif mode == 'truncated_comment': raw += b'\\n/* outer /* nested */ unterminated'
capture = os.environ.get('CARR_DUMP_CAPTURE')
if capture: open(capture,'wb').write(raw)
sys.stdout.buffer.write(raw)
""")


def run_positive_round_trip(fx: Fixture) -> bool:
    output = fx.scratch / "positive" / "out"
    run = fx.run_backup(output, timeout=45)
    pipeline_ok = run.returncode == 0 and not run.timed_out
    check("real pg_dump and real age complete", pipeline_ok, run.stdout + run.stderr)
    if not pipeline_ok:
        return False
    artifact = fx.artifact(output)
    plain = fx.scratch / "positive" / "restore.sql"
    decrypted = subprocess.run(
        [str(AGE), "--decrypt", "-i", str(fx.identity), "-o", str(plain), str(artifact)],
        text=True, capture_output=True, timeout=20, check=False,
    )
    decrypt_ok = (
        decrypted.returncode == 0
        and plain.is_file()
        and plain.stat().st_size > 1_048_576
    )
    check("fresh synthetic private key decrypts the promoted artifact",
          decrypt_ok, decrypted.stderr)
    if not decrypt_ok:
        return False
    # PostgreSQL 15+ creates public in every fresh database; the scoped dump
    # carries its own CREATE SCHEMA public, so restore into a truly blank scope.
    with psycopg.connect(fx.server_dsn(fx.restore_db)) as restore:
        restore.execute("drop schema public cascade")
    restored = subprocess.run(
        [str(PSQL), fx.server_dsn(fx.restore_db), "-v", "ON_ERROR_STOP=1", "-f", str(plain)],
        text=True, capture_output=True, timeout=45, check=False,
    )
    restore_ok = restored.returncode == 0
    check("full plain-SQL artifact restores on PostgreSQL 17", restore_ok, restored.stderr)
    if not restore_ok:
        return False
    with psycopg.connect(fx.server_dsn(fx.restore_db)) as conn:
        row = conn.execute("""
          select
            (select note from public.ordinary where id=10),
            (select count(*) from public.parted),
            (select count(*) from public.inherit_child),
            (select secret from public.rls_visible where id=1),
            (select note from ops.audit where id=1),
            public.wr54_double(6),
            (select last_value from public.shared_unowned_seq),
            (select count(*) from pg_indexes where schemaname='public' and indexname='ordinary_note_idx'),
            (select count(*) from pg_trigger where tgname='ordinary_touch' and not tgisinternal),
            (select count(*) from pg_type where typname='wr54_state')
        """).fetchone()
        row_ok = row == (
            "ordinary-sentinel", 2, 1, "rls-sentinel", "ops-sentinel",
            12, 77, 1, 1, 1,
        )
        check("restored rows, partitions, inheritance, schema objects and sequence survive",
              row_ok, repr(row))
        touched = conn.execute(
            "insert into public.ordinary(note) values('trigger-after-restore') returning touched"
        ).fetchone()
        trigger_ok = touched == (True,)
        check("restored trigger remains executable", trigger_ok, repr(touched))
        return row_ok and trigger_ok


def sql_refusal_cases(fx: Fixture) -> None:
    def case(label: str, setup: str, cleanup: str) -> None:
        fx.execute_source(setup)
        try:
            expect_refusal(fx, label)
        finally:
            fx.execute_source(cleanup)

    case("missing permissive RLS policy",
         "drop policy wr54_full_read on public.rls_visible",
         "create policy wr54_full_read on public.rls_visible for select to carr_backup using (true)")
    case("false permissive RLS policy",
         "drop policy wr54_full_read on public.rls_visible; create policy wr54_full_read on public.rls_visible for select to carr_backup using(false)",
         "drop policy wr54_full_read on public.rls_visible; create policy wr54_full_read on public.rls_visible for select to carr_backup using(true)")
    case("nonliteral permissive RLS policy",
         "drop policy wr54_full_read on public.rls_visible; create policy wr54_full_read on public.rls_visible for select to carr_backup using(current_user='carr_backup')",
         "drop policy wr54_full_read on public.rls_visible; create policy wr54_full_read on public.rls_visible for select to carr_backup using(true)")
    case("inactive-role restrictive false policy",
         "create policy wr54_restrictive on public.rls_visible as restrictive for select to wr54_inactive using(false)",
         "drop policy wr54_restrictive on public.rls_visible")
    case("column-only table SELECT",
         "revoke select on public.spoof_target from carr_backup; grant select(id) on public.spoof_target to carr_backup",
         "grant select on public.spoof_target to carr_backup")
    case("missing sequence SELECT",
         "revoke select on sequence public.shared_unowned_seq from carr_backup",
         "grant select on sequence public.shared_unowned_seq to carr_backup")
    case("unsupported materialized view",
         "create materialized view public.wr54_mv as select 1 id; grant select on public.wr54_mv to carr_backup",
         "drop materialized view public.wr54_mv")
    case("unsupported foreign table",
         "create foreign data wrapper wr54_fdw no handler; create server wr54_server foreign data wrapper wr54_fdw; create foreign table public.wr54_foreign(id integer) server wr54_server; grant select on public.wr54_foreign to carr_backup",
         "drop server wr54_server cascade; drop foreign data wrapper wr54_fdw")
    case("business table with omitted large-object dependency",
         "create extension lo with schema public; create table public.wr54_large_object(payload public.lo); insert into public.wr54_large_object values(lo_create(70001)); grant select on public.wr54_large_object to carr_backup",
         "drop table public.wr54_large_object; select lo_unlink(70001); drop extension lo")
    case("failed guard catalog query",
         "revoke select on pg_catalog.pg_policy from public",
         "grant select on pg_catalog.pg_policy to public")

    with fx.admin() as admin:
        admin.execute("alter role carr_backup superuser")
    try:
        expect_refusal(fx, "SUPERUSER carr_backup login")
    finally:
        with fx.admin() as admin:
            admin.execute("alter role carr_backup nosuperuser")
    with fx.admin() as admin:
        admin.execute("alter role carr_backup bypassrls")
    try:
        expect_refusal(fx, "BYPASSRLS carr_backup login")
    finally:
        with fx.admin() as admin:
            admin.execute("alter role carr_backup nobypassrls")
    with fx.admin() as admin:
        admin.execute(sql.SQL("alter database {} owner to carr_backup").format(sql.Identifier(fx.source_db)))
    try:
        expect_refusal(fx, "database-owner carr_backup login")
    finally:
        with fx.admin() as admin:
            admin.execute(sql.SQL("alter database {} owner to {}").format(
                sql.Identifier(fx.source_db), sql.Identifier(fx.admin_user)))

    wrong_url = fx.backup_url() + f"&user={quote(fx.admin_user)}"
    expect_refusal(fx, "URL text carr_backup but effective principal differs", url=wrong_url)


def toc_cases(fx: Fixture) -> None:
    shim = fx.scratch / "toc-bin" / "pg_dump"
    shim.parent.mkdir()
    transformer(shim)
    common = {"CARR_REAL_PG_DUMP": str(PG_DUMP)}

    # Valid verbose output also contains class-1259 TABLE DATA/ATTACH/SEQUENCE/
    # VIEW entries. They are deliberately outside the TABLE-OID oracle.
    observe_out = fx.scratch / "observe" / "out"
    fake_bin = fx.scratch / "observe" / "bin"
    fake_bin.mkdir(parents=True)
    dump_capture = fx.scratch / "observe" / "pg-dump.stdout"
    age_capture = fx.scratch / "observe" / "age.stdin"
    executable(fake_bin / "age", "#!/bin/sh\ntee \"$CARR_AGE_CAPTURE\"\n")
    run = fx.run_backup(
        observe_out, pg_dump=shim, path_prefix=fake_bin,
        extra_env={**common, "CARR_TOC_MODE": "non_table_decoys",
                   "CARR_DUMP_CAPTURE": str(dump_capture), "CARR_AGE_CAPTURE": str(age_capture)},
        timeout=45,
    )
    check("valid class-1259 TABLE census ignores other TOC types",
          run.returncode == 0 and not run.timed_out, run.stdout + run.stderr)
    grammar = (dump_capture.read_bytes() if dump_capture.exists() else b"")
    check("actual pg_dump stream carries pinned class-1259 TABLE grammar",
          re.search(
              rb"(?m)^-- TOC entry [0-9]+ \(class 1259 OID [0-9]+\)\n"
              rb"(?:-- Dependencies: [^\n]*\n)?"
              rb"-- Name: [^;\n]+; Type: TABLE; Schema: (?:public|ops); Owner: -"
              rb"(?:; Tablespace: [^;\n]+)?$", grammar,
          ) is not None,
          "fixture must not rely on synthetic metadata alone")
    check("TOC observer passes pg_dump stdout to age byte-for-byte",
          dump_capture.exists() and age_capture.exists()
          and digest(dump_capture) == digest(age_capture),
          "observer must not reserialize the SQL stream")

    for mode, label in (
        ("missing", "missing real TABLE OID"),
        ("duplicate", "duplicate real TABLE OID"),
        ("extra", "extra forged TABLE OID"),
        ("omit_spoof_copy", "omitted TABLE with OID spoofed in COPY data"),
        ("omit_spoof_string", "omitted TABLE with OID spoofed in SQL string"),
        ("omit_spoof_dollar", "omitted TABLE with OID spoofed in dollar body"),
        ("omit_spoof_nested_comment", "omitted TABLE with OID spoofed in nested comment"),
        ("truncated_string", "EOF inside SQL string"),
        ("truncated_identifier", "EOF inside quoted identifier"),
        ("truncated_dollar", "EOF inside dollar body"),
        ("truncated_comment", "EOF inside nested comment"),
    ):
        expect_refusal(fx, label, pg_dump=shim, extra_env={**common, "CARR_TOC_MODE": mode}, timeout=45)


def refused_dsn(raw: str) -> bool:
    try:
        require_local_dsn(raw)
    except (OSError, ValueError):
        return True
    return False


def portable_contract() -> None:
    check("literal IPv4 loopback fixture DSN is accepted",
          require_local_dsn("postgresql://fixture@127.0.0.1:5432/fixture") != "")
    check("localhost hostname is refused; transport must be literal",
          refused_dsn("postgresql://fixture@localhost:5432/fixture"))
    check("URI query host cannot override loopback authority",
          refused_dsn("postgresql://fixture@127.0.0.1:5432/fixture?host=remote.invalid"))
    check("URI query hostaddr cannot override loopback authority",
          refused_dsn("postgresql://fixture@127.0.0.1:5432/fixture?hostaddr=203.0.113.7"))
    check("service and multi-host DSNs are refused",
          refused_dsn("postgresql:///fixture?service=production")
          and refused_dsn("postgresql://fixture@127.0.0.1,203.0.113.7/fixture"))
    with tempfile.TemporaryDirectory(prefix="carr-wr54-socket-") as raw:
        socket_dir = Path(raw)
        socket_dir.chmod(0o700)
        socket_dsn = "postgresql:///fixture?host=" + quote(str(socket_dir), safe="")
        check("owned non-writable Unix socket directory is accepted",
              require_local_dsn(socket_dsn) == socket_dsn)
        socket_dir.chmod(0o777)
        check("world-writable Unix socket directory is refused", refused_dsn(socket_dsn))
    source = WRAPPER.read_text(encoding="utf-8")
    check("wrapper is wired to backup-guard.py", "backup-guard.py" in source)
    helper_source = GUARD.read_text(encoding="utf-8") if GUARD.is_file() else ""
    check("pg_dump command owner requests verbose metadata and imported snapshot",
          "--verbose" in helper_source and "--snapshot" in helper_source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="require and run the disposable PostgreSQL/real-age fixtures")
    args = parser.parse_args(argv)
    portable_contract()
    raw_dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    if not raw_dsn and not args.live:
        print("  --    live PostgreSQL/age fixtures NOT RUN (use --live with CARR_LOCAL_PG_DSN)")
        print(f"\nbackup-guard-selftest: {PASS}/{PASS + len(FAIL)} portable checks passed")
        return 1 if FAIL else 0
    if not raw_dsn:
        print("backup-guard-selftest: --live requires CARR_LOCAL_PG_DSN", file=sys.stderr)
        return 1
    try:
        dsn = require_local_dsn(raw_dsn)
    except (OSError, ValueError) as exc:
        print(f"backup-guard-selftest: REFUSED: {exc}", file=sys.stderr)
        return 78
    missing = [str(path) for path in (PG_DUMP, PSQL, AGE, AGE_KEYGEN) if not path.is_file()]
    if missing:
        print("backup-guard-selftest: required local binaries missing: " + ", ".join(missing), file=sys.stderr)
        return 78

    with tempfile.TemporaryDirectory(prefix="carr-wr54-guard-") as raw:
        fx = Fixture(dsn, Path(raw))
        try:
            fx.setup_cluster_objects()
            fx.seed()
            fx.prepare_private_repo()
            if run_positive_round_trip(fx):
                sql_refusal_cases(fx)
                toc_cases(fx)
                concurrency_cases(fx)
            else:
                print("  --    downstream guard refusal/concurrency fixtures NOT RUN: positive pipeline failed")
        except Exception as exc:
            check("fixture completed without harness error", False, repr(exc))
        finally:
            fx.cleanup_cluster_objects()

    print(f"\nbackup-guard-selftest: {PASS}/{PASS + len(FAIL)} passed")
    if FAIL:
        print("failed: " + ", ".join(FAIL), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
