#!/usr/bin/env python3
# ci: db-gate
"""Replay rollback preparation with real local PostgreSQL and synthetic credentials."""
import importlib.util
import hashlib
import os
from pathlib import Path
import socket
import shutil
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
import psycopg
from psycopg.conninfo import conninfo_to_dict

spec = importlib.util.spec_from_file_location("recovery_fixture", ROOT / "ops/staging-app-writer-recovery-selftest.py")
assert spec is not None and spec.loader is not None
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)
p = suite.provision
real_connect = psycopg.connect
real_apply = p.apply_login_profile
real_validate = p.validate_profile_login
real_old_grants = p.rollback_authority_grants
binary = next((str(path) for path in (
        Path("/opt/homebrew/opt/postgresql@17/bin/initdb"),
        Path("/usr/local/opt/postgresql@17/bin/initdb"),
    ) if path.is_file()), None) or shutil.which("initdb")
if binary is None:
    raise RuntimeError("PostgreSQL binaries are required for the rollback proof")
bins = Path(binary).parent
clean = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "TMPDIR") if k in os.environ}
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

with postgres_fixture_group(), DisposablePostgres("staging-authority-proof-", bins / "pg_ctl", env=clean) as pg:
    data = pg.root / "data"
    pg.run([bins / "initdb", "-D", data, "-U", "postgres", "--auth=trust", "--no-locale"], capture_output=True, check=True)
    (data / "pg_hba.conf").write_text("host all postgres 127.0.0.1/32 trust\nhost all all 127.0.0.1/32 scram-sha-256\n")
    pg.run([bins / "pg_ctl", "-D", data, "-l", pg.root / "postgres.log", "-o", f"-h 127.0.0.1 -p {port} -k {pg.root}", "-w", "start"], capture_output=True, check=True)
    with real_connect(host="127.0.0.1", port=port, user="postgres", dbname="postgres", autocommit=True) as admin:
        admin.execute("create role neondb_owner login createrole password 'synthetic-owner'")
        admin.execute("create database neondb")
        admin.execute("create role carr_authority nologin")
        admin.execute("grant carr_authority to neondb_owner with admin true")
    migrations = pg.root / "migrations"
    migrations.mkdir()
    old_sql = "grant usage on schema public to carr_authority;\ngrant execute on function public.old_clock(timestamptz) to carr_authority;"
    (migrations / "0001_authority.sql").write_text(old_sql)
    with real_connect(host="127.0.0.1", port=port, user="postgres", dbname="neondb", autocommit=True) as admin:
        admin.execute("create table public.schema_migrations(filename text primary key,sha256 text)")
        admin.execute("insert into public.schema_migrations values (%s,%s)", ("0001_authority.sql", hashlib.sha256(old_sql.encode()).hexdigest()))
        admin.execute("grant select on public.schema_migrations to neondb_owner")
        admin.execute("create function public.old_clock(timestamptz) returns integer language sql as 'select 1'")
        admin.execute("grant execute on function public.old_clock(timestamptz) to carr_authority")
        admin.execute("grant execute on function public.old_clock(timestamptz) to neondb_owner with grant option")
        admin.execute("grant usage on schema public to carr_authority")
        admin.execute("grant usage on schema public to neondb_owner with grant option")
    def connect(value):
        parsed = conninfo_to_dict(value)
        return real_connect(host="127.0.0.1", port=port, user=parsed["user"], password=parsed.get("password"), dbname="neondb")
    fixture = suite.Recovery()
    fixture.setUp()
    try:
        plans: dict[str, tuple[str, ...]] = {profile.label: () for profile in p.PROFILES}
        plans["authority_joe"] = ("grant execute on function public.future() to carr_authority;",)
        def prepare():
            with p.credential.exclusive_lock(fixture.root / ".worker.lock"):
                outcomes = []
                values = p.prepare_worker_rollback(fixture.binding, capture=True, plans=plans, rotations=outcomes)
                return values, outcomes
        with (
            mock.patch.object(p, "MIGRATIONS", migrations),
            mock.patch.object(p, "rollback_authority_grants", side_effect=real_old_grants),
            mock.patch.object(p.psycopg, "connect", side_effect=connect),
            mock.patch.object(p, "apply_login_profile", side_effect=real_apply),
            mock.patch.object(p, "validate_profile_login",
                              side_effect=lambda *a, **kw: real_validate(*a, **kw, connect=connect)),
        ):
            values, outcomes = prepare()
            assert outcomes[0]["outcome"] == "created"
            assert prepare()[0] == values
            with connect(values["CARR_DB_AUTHORITY_JOE_URL"]) as login:
                assert login.execute("select session_user, current_user").fetchone() == ("carr_authority_joe", "carr_authority_joe")
            try:
                real_connect(host="127.0.0.1", port=port, user="carr_authority_joe", password="wrong-fixture-password", dbname="neondb")
            except psycopg.OperationalError:
                pass
            else:
                raise AssertionError("wrong password authenticated")
        print("PASS: real PostgreSQL 17 rollback preparation creates missing authority, proves exact closure, authenticates via SCRAM, rejects wrong password, and reuses canonical value")
    finally:
        fixture.doCleanups()
