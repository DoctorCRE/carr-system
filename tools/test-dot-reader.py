#!/usr/bin/env python3
"""Dot SQL boundary proof on an owned disposable PostgreSQL cluster.

No production DSN is accepted. Set CARR_DOT_REPO_SCHEMA=1 to repeat against
the committed schema rather than the small behavioral fixture.
"""
import importlib.util
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit, quote
import contextlib
import io
import re
import ast
import hashlib
import sys
import signal
import shutil
import time

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
MIGRATION = ROOT / "migrations/0756_dot_reader.sql"


class DotReader(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("local_pg", ROOT / "ops/local-pg-ci.py")
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            cls.bin = module.find_postgres_binaries()
        except module.LocalPGRefusal:
            raise unittest.SkipTest("disposable PostgreSQL binaries unavailable")
        cls.pg_budget = postgres_fixture_group()
        cls.pg_budget.__enter__()
        cls.addClassCleanup(cls.pg_budget.__exit__, None, None, None)
        cls.fixture = DisposablePostgres("dot-reader-test-", cls.bin.pg_ctl)
        cls.addClassCleanup(cls.fixture.close)
        cls.tmp = cls.fixture.root
        cls.data = cls.tmp / "data"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.run_pg([cls.bin.initdb, "-D", cls.data, "-U", "carr_ci",
                    "--auth-local=trust", "--auth-host=scram-sha-256", "--encoding=UTF8", "--no-locale"])
        hba = cls.data / "pg_hba.conf"
        hba.write_text("host carr_ci carr_ci 127.0.0.1/32 trust\n" + hba.read_text())
        cls.run_pg([cls.bin.pg_ctl, "-D", cls.data, "-l", cls.tmp / "pg.log",
                    "-o", f"-h 127.0.0.1 -k {cls.tmp} -p {cls.port} -c fsync=off -c synchronous_commit=off -c full_page_writes=off", "-w", "start"])
        cls.owner_args = dict(host=str(cls.tmp), port=cls.port, user="carr_ci", dbname="postgres")
        if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
            with psycopg.connect(**cls.owner_args, autocommit=True) as owner:
                owner.execute("create database carr_ci")
            cls.owner_args["dbname"] = "carr_ci"
        with psycopg.connect(**cls.owner_args, autocommit=True) as owner:
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                owner.execute("create role neondb_owner")
                # psql understands the snapshot's meta-commands. No credentials
                # are on argv: this owned Unix socket authenticates locally.
                cls.run_pg([cls.bin.psql, "-h", str(cls.tmp), "-p", str(cls.port),
                            "-U", "carr_ci", "-d", cls.owner_args["dbname"], "-v", "ON_ERROR_STOP=1",
                            "-q", "-1", "-f", ROOT / "db/schema.sql"])
                pending = subprocess.run([sys.executable, str(ROOT / "tools/migrate.py"),
                    "--apply", "--yes", "--through", "0755_property_evidence_scac_successor.sql"],
                    env={**os.environ, "DATABASE_URL": f"postgres://carr_ci@127.0.0.1:{cls.port}/carr_ci"},
                    capture_output=True, text=True, timeout=180)
                if pending.returncode:
                    raise RuntimeError("full-schema pending migration setup failed: " + pending.stderr[-2000:])
            else:
                owner.execute("create schema ops; create role carr_writer; create role neondb_owner")
                owner.execute("""
                    create function ops.completion_runtime_tenant() returns text
                    language plpgsql stable as $$ begin
                      if nullif(current_setting('carr.organization_tenant_id',true),'') is null then
                        raise exception 'completion register requires a server-derived tenant';
                      end if;
                      return current_setting('carr.organization_tenant_id');
                    end $$;
                    revoke execute on function ops.completion_runtime_tenant() from public;
                    create table ops.dot_completion_fixture(organization_tenant_id text);
                    insert into ops.dot_completion_fixture values ('tenant-a'),('tenant-b');
                    create view ops.completion_current_observation as select * from ops.dot_completion_fixture
                      where organization_tenant_id=ops.completion_runtime_tenant();
                    create view ops.completion_dimension_matrix as select * from ops.dot_completion_fixture s
                      where s.organization_tenant_id=ops.completion_runtime_tenant();
                    create view ops.completion_projection as select * from ops.completion_dimension_matrix;
                """)
                # Model the two legacy PUBLIC-executable write doors. The
                # full-schema run exercises their production definitions.
                owner.execute("""
                    create function ops.engineering_register_slice_plan(text,jsonb,text,uuid)
                    returns void language plpgsql security definer as $$
                    begin
                      raise exception 'unguarded writer reached' using errcode='P0001';
                    end
                    $$;
                    create function ops.issue_execution_envelope_v1(text,text,uuid)
                    returns void language plpgsql security definer as $$
                    begin
                      raise exception 'unguarded writer reached' using errcode='P0001';
                    end
                    $$;
                """)
            owner.execute("create role app_reader login")
            if os.environ.get("CARR_DOT_MANAGED_OWNER") == "1":
                owner.execute("alter role neondb_owner createrole createdb")
                owner.execute("alter database postgres owner to neondb_owner")
                owner.execute("alter schema ops owner to neondb_owner")
                owner.execute("alter function ops.engineering_register_slice_plan(text,jsonb,text,uuid) owner to neondb_owner")
                owner.execute("alter function ops.issue_execution_envelope_v1(text,text,uuid) owner to neondb_owner")
                owner.execute("alter function ops.completion_runtime_tenant() owner to neondb_owner")
                for relation in ("dot_completion_fixture", "completion_current_observation", "completion_dimension_matrix", "completion_projection"):
                    owner.execute(sql.SQL("alter table ops.{} owner to neondb_owner").format(sql.Identifier(relation)))
                owner.execute("set role neondb_owner")
            owner.execute("""
                create table public.dot_fixture (id int primary key, sponsor text, scope text);
                create table ops.dot_fixture (id int primary key, sponsor text, scope text);
                insert into public.dot_fixture values (1,'sponsor_a','shared'),
                  (2,'sponsor_a','personal'),(3,'sponsor_b','personal');
                insert into ops.dot_fixture select * from public.dot_fixture;
                alter table public.dot_fixture enable row level security;
                alter table public.dot_fixture force row level security;
                alter table ops.dot_fixture enable row level security;
                create policy fixture_sponsor on public.dot_fixture for select
                  using (sponsor = current_setting('test.sponsor',true));
                create view public.dot_fixture_view with (security_invoker=true) as select * from public.dot_fixture;
                create sequence public.dot_fixture_seq;
                create function public.dot_fixture_mutation() returns void language sql
                  security definer as 'insert into ops.dot_fixture values (4, ''sponsor_a'', ''shared'')';
                revoke execute on function public.dot_fixture_mutation() from public;
            """)
            cls.temp_before = owner.execute("select has_database_privilege('app_reader',current_database(),'TEMP')").fetchone()[0]
            owner.execute(MIGRATION.read_text())
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                owner.execute("insert into schema_migrations(filename,sha256) values (%s,%s)",
                              (MIGRATION.name, hashlib.sha256(MIGRATION.read_bytes()).hexdigest()))
            owner.execute("reset role")
            # Only the fixture sets a password. It stays in memory and never
            # reaches a source file, command argument or test output.
            cls.password = secrets.token_urlsafe(32)
            owner.execute(sql.SQL("alter role dot_reader password {}").format(sql.Literal(cls.password)))
            if os.environ.get("CARR_DOT_MANAGED_OWNER") == "1":
                owner.execute("set role neondb_owner")
            owner.execute("create table public.dot_future (id int); insert into public.dot_future values (9)")
            owner.execute("create table ops.dot_future (id int); insert into ops.dot_future values (10)")
            owner.execute("""create function ops.dot_future_mutation() returns void language sql
                security definer as 'insert into ops.dot_fixture values (6, ''sponsor_a'', ''shared'')'""")
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                owner.execute("""
                    insert into public.actor(slug,kind,display_name) values
                      ('dot-fixture-a','human','Fixture sponsor A'),
                      ('dot-fixture-b','human','Fixture sponsor B');
                    insert into public.memory_item(organization_tenant_id,kind,statement,scope,owner_actor_id,observed_by_actor_id)
                    select 'dot-fixture','fact','Synthetic review fixture','personal',id,id
                      from public.actor where slug in ('dot-fixture-a','dot-fixture-b');
                """)


    @classmethod
    def run_pg(cls, args, fixture=None):
        fixture = fixture or cls.fixture
        result = fixture.run([str(x) for x in args], capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("disposable PostgreSQL setup failed")

    def connect(self):
        return psycopg.connect(host="127.0.0.1", port=self.port, user="dot_reader",
                               password=self.password, dbname=self.owner_args["dbname"], autocommit=True)

    def test_reads_all_sponsors_and_personal_scope(self):
        with self.connect() as dot:
            self.assertEqual(dot.execute("select session_user,current_user").fetchone(), ("dot_reader", "dot_reader"))
            dot.execute("set test.sponsor = 'sponsor_a'")
            for table in ("public.dot_fixture", "ops.dot_fixture", "public.dot_fixture_view"):
                with self.subTest(table=table):
                    rows = dot.execute(sql.SQL("select sponsor,scope from {} order by id").format(sql.Identifier(*table.split(".")))).fetchall()
                    self.assertEqual(rows, [("sponsor_a", "shared"), ("sponsor_a", "personal"), ("sponsor_b", "personal")])
            self.assertEqual(dot.execute("select id from public.dot_future").fetchall(), [(9,)])
            self.assertEqual(dot.execute("select id from ops.dot_future").fetchall(), [(10,)])
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                self.assertEqual(dot.execute("""select a.slug from public.memory_item m
                    join public.actor a on a.id=m.owner_actor_id
                    where m.organization_tenant_id='dot-fixture' and m.scope='personal' order by a.slug""").fetchall(),
                    [("dot-fixture-a",), ("dot-fixture-b",)])

    def test_write_and_ddl_denied_even_if_read_only_setting_disabled(self):
        statements = [
            "insert into public.dot_fixture values (5,'sponsor_a','shared')",
            "update public.dot_fixture set scope='shared'",
            "delete from public.dot_fixture", "truncate public.dot_fixture",
            "copy public.dot_fixture from stdin", "create table public.dot_nope (id int)",
            "create table ops.dot_nope (id int)", "create temp table dot_nope (id int)",
            "create schema dot_nope", "create database dot_nope", "create role dot_nope",
            "alter table public.dot_fixture add column nope int", "drop table public.dot_fixture",
            "create function public.dot_nope() returns int language sql as 'select 1'",
            "set role carr_writer", "select nextval('public.dot_fixture_seq')",
            "select public.dot_fixture_mutation()",
            "select ops.dot_future_mutation()",
        ]
        with self.connect() as dot:
            dot.execute("set default_transaction_read_only=off")
            for statement in statements:
                with self.subTest(statement=statement):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        dot.execute(statement)
            for call in (
                "select ops.engineering_register_slice_plan(null,null,null,null)",
                "select * from ops.issue_execution_envelope_v1(null,null,null)",
            ):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    dot.execute(call)
            # PostgreSQL reports a warning for a GRANT without grant option;
            # it must confer no privilege, even when the statement returns.
            dot.execute("grant select on public.dot_fixture to app_reader")
        with psycopg.connect(**self.owner_args) as owner:
            self.assertFalse(owner.execute("select has_table_privilege('app_reader','public.dot_fixture','SELECT')").fetchone()[0])

    def test_current_views_execute_with_all_tenant_reviewer_behavior(self):
        if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
            with psycopg.connect(**self.owner_args, autocommit=True) as owner:
                sys.path.insert(0, str(ROOT / "ops"))
                spec = importlib.util.spec_from_file_location("dot_completion_seed", ROOT / "ops/completion-register-schema-local-pg-gate.py")
                seed = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(seed)
                for tenant in ("tenant-a", "tenant-b"):
                    owner.execute("select set_config('carr.organization_tenant_id',%s,false)", (tenant,))
                    with owner.cursor() as cur:
                        cur.execute("""insert into ops.completion_policy
                            (policy_key,policy_version,capability_class,required_dimensions,
                             default_freshness,state_precedence,effective_at,policy_digest)
                            values (%s,1,'fixture',%s,interval '1 day',%s,
                                    now()-interval '1 day',null) returning organization_tenant_id""",
                            ("dot-policy-" + tenant, list(seed.DIMENSIONS), list(seed.PRECEDENCE)))
                        if cur.fetchone() != (tenant,):
                            raise AssertionError("completion fixture must derive the requested tenant")
                        seed.complete_subject(cur, "dot-review-" + tenant)
        with psycopg.connect(**self.owner_args) as owner:
            views = owner.execute("""select n.nspname,c.relname from pg_class c
                join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('public','ops') and c.relkind='v' order by 1,2""").fetchall()
        with self.connect() as dot:
            for schema, view in views:
                with self.subTest(view=f"{schema}.{view}"):
                    dot.execute(sql.SQL("select * from {}.{} limit 1").format(sql.Identifier(schema), sql.Identifier(view))).fetchall()
            for tenant in (None, "tenant-a"):
                if tenant:
                    dot.execute("set carr.organization_tenant_id='tenant-a'")
                for view in ("completion_current_observation", "completion_dimension_matrix", "completion_projection"):
                    self.assertEqual(dot.execute(sql.SQL("select distinct organization_tenant_id from ops.{} order by 1").format(sql.Identifier(view))).fetchall(), [("tenant-a",), ("tenant-b",)])
        # Existing callers retain the server-derived tenant requirement.
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute("grant usage on schema ops to app_reader")
            owner.execute("grant execute on function ops.completion_runtime_tenant() to app_reader")
            owner.execute("grant select on ops.completion_current_observation to app_reader")
            owner.execute("set session authorization app_reader")
            try:
                with self.assertRaisesRegex(psycopg.Error, "server-derived tenant"):
                    owner.execute("select ops.completion_runtime_tenant()")
                owner.execute("set carr.organization_tenant_id='tenant-a'")
                self.assertEqual(owner.execute("select distinct organization_tenant_id from ops.completion_current_observation").fetchall(), [("tenant-a",)])
            finally:
                owner.execute("reset session authorization")
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                # Only the disposable fixture administrator bypasses immutable
                # row triggers to remove its synthetic data after the read test.
                owner.execute("set session_replication_role=replica")
                try:
                    for table in ("completion_observation", "completion_receipt", "completion_subject", "completion_policy"):
                        owner.execute(sql.SQL("delete from ops.{} where organization_tenant_id in ('tenant-a','tenant-b')").format(sql.Identifier(table)))
                finally:
                    owner.execute("set session_replication_role=origin")

    def test_limits_isolation_and_full_catalog_coverage(self):
        with self.connect() as dot:
            self.assertEqual(dot.execute("show statement_timeout").fetchone(), ("30s",))
            with self.connect():
                with self.assertRaises(psycopg.OperationalError):
                    self.connect()
        with psycopg.connect(**self.owner_args) as owner:
            self.assertEqual(owner.execute("select rolcanlogin,rolconnlimit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls from pg_roles where rolname='dot_reader'").fetchone(), (True, 2, False, False, False, False, False))
            self.assertEqual(owner.execute("select member::regrole::text,admin_option,inherit_option,set_option from pg_auth_members where roleid='dot_reader'::regrole").fetchall(), [("neondb_owner", True, False, False)])
            self.assertEqual(owner.execute("select count(*) from pg_auth_members where member='dot_reader'::regrole").fetchone(), (0,))
            self.assertEqual(owner.execute("select has_database_privilege('app_reader',current_database(),'TEMP')").fetchone(), (False,))
            self.assertEqual(owner.execute("""select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('public','ops') and c.relkind in ('r','p','v','m','f')
                  and not has_table_privilege('dot_reader',c.oid,'SELECT')""").fetchone(), (0,))
            self.assertEqual(owner.execute("""select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('public','ops') and c.relkind in ('r','p') and c.relrowsecurity
                  and not exists(select 1 from pg_policy p where p.polrelid=c.oid and p.polname='dot_reader_full_read'
                    and p.polcmd='*' and p.polpermissive and 'dot_reader'::regrole=any(p.polroles)
                  and pg_get_expr(p.polqual,p.polrelid)='true')""").fetchone(), (0,))

    def test_ambient_column_sequence_and_grant_option_drift_refused(self):
        tree = ast.parse((ROOT / "tools/dot-reader-access.py").read_text())
        queries = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                   and isinstance(node.value, str) and "effective" not in node.value
                   and "not has_database_privilege" in node.value]
        self.assertEqual(len(queries), 1)
        proof = MIGRATION.read_text().split("do $dot_reader_proof$", 1)[1]
        proof = "do $dot_reader_proof$" + proof
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            for grant, revoke, mutation in (
                ("grant update(scope) on public.dot_fixture to public",
                 "revoke update(scope) on public.dot_fixture from public",
                 "update public.dot_fixture set scope=scope where id=1"),
                ("grant usage on sequence public.dot_fixture_seq to public",
                 "revoke usage on sequence public.dot_fixture_seq from public",
                 "select nextval('public.dot_fixture_seq')"),
                ("grant update on sequence public.dot_fixture_seq to public",
                 "revoke update on sequence public.dot_fixture_seq from public",
                 "select setval('public.dot_fixture_seq',50)"),
                ("grant select on public.dot_fixture to dot_reader with grant option",
                 "revoke grant option for select on public.dot_fixture from dot_reader", None),
                ("grant select(scope) on public.dot_fixture to dot_reader with grant option",
                 "revoke select(scope) on public.dot_fixture from dot_reader", None),
                ("grant select on sequence public.dot_fixture_seq to dot_reader with grant option",
                 "revoke grant option for select on sequence public.dot_fixture_seq from dot_reader", None),
            ):
                with self.subTest(grant=grant):
                    try:
                        owner.execute(grant)
                        if mutation:
                            with self.connect() as dot:
                                dot.execute(mutation)
                        self.assertEqual(owner.execute(queries[0]).fetchone(), (False,))
                        with self.assertRaisesRegex(psycopg.Error, "effective privilege boundary failed"):
                            owner.execute(proof)
                    finally:
                        owner.execute(revoke)
            # The migration must also refuse pre-existing PUBLIC privileges,
            # rather than silently stripping unrelated callers' authorization.
            owner.execute("alter role dot_reader rename to dot_existing_fixture")
            try:
                for grant, revoke in (
                    ("grant update(scope) on public.dot_fixture to public",
                     "revoke update(scope) on public.dot_fixture from public"),
                    ("grant usage,update on sequence public.dot_fixture_seq to public",
                     "revoke usage,update on sequence public.dot_fixture_seq from public"),
                ):
                    try:
                        owner.execute(grant)
                        with self.assertRaisesRegex(psycopg.Error, "effective privilege boundary failed"):
                            with owner.transaction():
                                for schema, table in owner.execute("""select n.nspname,c.relname
                                    from pg_policy p join pg_class c on c.oid=p.polrelid
                                    join pg_namespace n on n.oid=c.relnamespace
                                    where p.polname='dot_reader_full_read'""").fetchall():
                                    owner.execute(sql.SQL("drop policy dot_reader_full_read on {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
                                owner.execute(MIGRATION.read_text())
                    finally:
                        owner.execute(revoke)
            finally:
                owner.execute("alter role dot_existing_fixture rename to dot_reader")

    def test_provision_resume_and_revoke_on_disposable_database(self):
        spec = importlib.util.spec_from_file_location("dot_access", ROOT / "tools/dot-reader-access.py")
        access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(access)
        owner_password = secrets.token_urlsafe(32)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute(sql.SQL("alter role carr_ci password {}").format(sql.Literal(owner_password)))
        owner_uri = urlunsplit(("postgresql", f"carr_ci:{quote(owner_password)}@127.0.0.1:{self.port}", "/" + self.owner_args["dbname"], "sslmode=require", ""))
        original_connect = psycopg.connect

        def local_connect(uri, **kwargs):
            # Only this test changes TLS: the disposable cluster has no TLS.
            parts = urlsplit(uri)
            if parts.hostname != "127.0.0.1" or parts.port != self.port:
                raise AssertionError("credential fixture must stay on its owned loopback port")
            return original_connect(uri.replace("sslmode=require", "sslmode=disable"), **kwargs)

        with tempfile.TemporaryDirectory(prefix="dot-credential-test-") as directory:
            destination = Path(directory) / "dot-reader.connection"
            output = io.StringIO()
            with patch.dict(os.environ, {"DATABASE_URL": owner_uri}), \
                 patch.object(access.credential, "ENV_PATH", str(Path(directory) / "db.env")), \
                 patch.object(access, "DOT_CONNECTION_PATH", str(destination)), \
                 patch.object(access, "read_env", return_value={"CARR_DB_EXPORTER_URL": owner_uri}), \
                 patch.object(psycopg, "connect", side_effect=local_connect), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with patch.object(access, "publish_dot_connection", side_effect=OSError()):
                    self.assertEqual(access.dot_reader_action(revoke=False), 1)
                pending = access.read_dot_pending()
                with patch.object(access, "new_password", side_effect=AssertionError("must resume")):
                    self.assertEqual(access.dot_reader_action(revoke=False), 0)
                self.assertTrue(destination.read_text() == pending + "\n")
                self.assertNotIn(pending, output.getvalue())
                with local_connect(pending, autocommit=True) as dot:
                    self.assertEqual(access.dot_reader_action(revoke=True), 0)
                    with self.assertRaises(psycopg.Error):
                        dot.execute("select 1")
                with self.assertRaises(psycopg.OperationalError):
                    local_connect(pending)
                self.assertEqual(destination.read_text(), "REVOKED\n")
                with self.assertRaises(SystemExit):
                    access.dot_reader_action(revoke=False)
        # Restore the shared fixture login for the remaining read tests.
        with original_connect(**self.owner_args, autocommit=True) as owner:
            owner.execute(sql.SQL("alter role dot_reader login password {}").format(sql.Literal(self.password)))

    def test_documented_future_write_upgrade_supports_generated_ids(self):
        spec = importlib.util.spec_from_file_location("dot_upgrade", ROOT / "tools/dot-reader-access.py")
        access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(access)
        upgrade = re.findall(r"^  (GRANT .*;)$", access.__doc__, re.M)
        self.assertTrue(upgrade, "the documented future transition must be executable")
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            if os.environ.get("CARR_DOT_MANAGED_OWNER") == "1":
                owner.execute("set role neondb_owner")
            owner.execute("create table public.dot_generated_id (id serial primary key)")
            owner.execute("reset role")
            try:
                for statement in upgrade:
                    owner.execute(statement)
                with self.connect() as dot:
                    dot.execute("begin")
                    try:
                        self.assertEqual(dot.execute("insert into public.dot_generated_id default values returning id").fetchone(), (1,))
                        for schema in ("public", "ops"):
                            dot.execute(sql.SQL("insert into {}.dot_fixture values (99,'sponsor_b','personal')").format(sql.Identifier(schema)))
                            dot.execute(sql.SQL("update {}.dot_fixture set scope='shared' where id=99").format(sql.Identifier(schema)))
                            dot.execute(sql.SQL("delete from {}.dot_fixture where id=99").format(sql.Identifier(schema)))
                    finally:
                        dot.execute("rollback")
            finally:
                owner.execute("revoke insert,update,delete on all tables in schema public,ops from dot_reader")
                owner.execute("revoke usage,update on all sequences in schema public,ops from dot_reader")
                owner.execute("drop table public.dot_generated_id")
        with self.connect() as dot:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                dot.execute("delete from public.dot_fixture")


    def test_release_abandon_fixture_isolates_cluster_roles(self):
        spec = importlib.util.spec_from_file_location("release_abandon", ROOT / "ops/release-abandon-selftest.py")
        abandon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(abandon)
        password = secrets.token_urlsafe(32)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute(sql.SQL("alter role carr_ci password {}").format(sql.Literal(password)))
        base = psycopg.conninfo.make_conninfo(host="127.0.0.1", port=self.port,
            user="carr_ci", password=password, dbname=self.owner_args["dbname"])
        with abandon.isolated_ci_database(base) as isolated:
            with psycopg.connect(isolated, autocommit=True) as fixture:
                self.assertEqual(fixture.execute("select count(*) from pg_roles where rolname='dot_reader'").fetchone(), (0,),
                                 "sibling databases share roles; the release fixture requires its own cluster")
                fixture.execute("create role release_abandon_role_isolation_probe")
            with psycopg.connect(**self.owner_args, autocommit=True) as base_db:
                self.assertEqual(base_db.execute("select count(*) from pg_roles where rolname='release_abandon_role_isolation_probe'").fetchone(), (0,))
        with psycopg.connect(**self.owner_args, autocommit=True) as base_db:
            self.assertEqual(base_db.execute("select rolcanlogin from pg_roles where rolname='dot_reader'").fetchone(), (True,))

    def test_snapshot_role_preamble_reconstructs_passwordless_login(self):
        exporter = (ROOT / "bin/schema-snapshot.sh").read_text()
        blocks = []
        for marker in ("DOT_READER_ROLES",):
            match = re.search(r"cat >> \"\$TMP\" <<'" + marker + r"'\n(.*?)\n" + marker, exporter, re.S)
            self.assertIsNotNone(match, "snapshot must reconstruct the released Dot role")
            blocks.append(match.group(1))
        self.assertIn('if [ "$DOT_READER_APPLIED" = t ]; then', exporter)
        self.assertIn("filename='0756_dot_reader.sql'", exporter)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute("alter role dot_reader rename to dot_existing_fixture")
            try:
                for block in blocks:
                    owner.execute(block)
                self.assertEqual(owner.execute("select rolpassword is null,rolcanlogin,rolconnlimit,rolinherit,rolbypassrls from pg_authid where rolname='dot_reader'").fetchone(), (True, True, 2, False, False))
            finally:
                owner.execute("reset role")
                owner.execute("drop owned by dot_reader; drop role dot_reader")
                owner.execute("alter role dot_existing_fixture rename to dot_reader")

    @unittest.skipUnless(os.environ.get("CARR_DOT_REPO_SCHEMA") == "1", "requires complete snapshot source")
    def test_complete_post_release_snapshot_passes_grant_validator(self):
        candidate = self.complete_snapshot()
        result = subprocess.run([sys.executable, str(ROOT / "tools/test-schema-snapshot-grants.py"),
            "--snapshot", str(candidate)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-1000:])
        original = candidate.read_text()
        membership = "grant dot_reader to neondb_owner with admin true, inherit false, set false;"
        self.assertIn(membership, original)
        for unsafe in (
            membership.replace("inherit false", "inherit true"),
            membership.replace("set false", "set true"),
            membership.replace("neondb_owner", "app_reader"),
            "grant select on table public.actor to neondb_owner;",
        ):
            with self.subTest(unsafe=unsafe):
                altered = Path(self.tmp.name) / "unsafe-membership.sql"
                altered.write_text(original.replace(membership, unsafe))
                result = subprocess.run([sys.executable, str(ROOT / "tools/test-schema-snapshot-grants.py"),
                    "--snapshot", str(altered)], capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0, "widening guard must retain unsafe shape refusal")

    def complete_snapshot(self):
        candidate = Path(self.tmp.name) / "post-release.sql"
        if candidate.exists():
            return candidate
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute("create table public.dot_narrow_acl(id int)")
            owner.execute("revoke select on public.dot_narrow_acl from dot_reader")
            owner.execute("alter default privileges in schema ops revoke select on tables from dot_reader")
            owner.execute("alter default privileges in schema ops revoke select on sequences from dot_reader")
            self.assertEqual(owner.execute("select has_table_privilege('dot_reader','public.dot_narrow_acl','SELECT')").fetchone(), (False,))
            try:
                result = subprocess.run([str(ROOT / "bin/schema-snapshot.sh"),
                    "--from-disposable-local", f"postgres://carr_ci@127.0.0.1:{self.port}/carr_ci",
                    "--output-candidate", str(candidate)], capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout[-1000:] + result.stderr[-3000:])
            finally:
                owner.execute("grant select on public.dot_narrow_acl to dot_reader")
                owner.execute("alter default privileges in schema ops grant select on tables to dot_reader")
                owner.execute("alter default privileges in schema ops grant select on sequences to dot_reader")
        return candidate

    @unittest.skipUnless(os.environ.get("CARR_DOT_REPO_SCHEMA") == "1", "requires complete snapshot source")
    def test_complete_restore_preserves_narrowed_acl_and_defaults(self):
        candidate = self.complete_snapshot()
        with DisposablePostgres("dot-independent-restore-", self.bin.pg_ctl) as fixture:
            directory = str(fixture.root)

            def run_pg(args):
                self.run_pg(args, fixture=fixture)

            data = Path(directory) / "data"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1",0))
                port = probe.getsockname()[1]
            run_pg([self.bin.initdb, "-D", data, "-U", "carr_ci", "--auth-local=trust",
                         "--auth-host=scram-sha-256", "--encoding=UTF8", "--no-locale"])
            run_pg([self.bin.pg_ctl, "-D", data, "-l", Path(directory)/"pg.log", "-o",
                         f"-h 127.0.0.1 -k {directory} -p {port} -c fsync=off -c synchronous_commit=off -c full_page_writes=off", "-w", "start"])
            args = dict(host=directory, port=port, user="carr_ci", dbname="postgres")
            with psycopg.connect(**args, autocommit=True) as owner:
                owner.execute("create role neondb_owner")
            run_pg([self.bin.psql, "-h", directory, "-p", str(port), "-U", "carr_ci",
                         "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-1", "-f", candidate])
            with psycopg.connect(**args, autocommit=True) as owner:
                self.assertEqual(owner.execute("select rolpassword is null,rolcanlogin,rolconnlimit from pg_authid where rolname='dot_reader'").fetchone(), (True, True, 2))
                self.assertEqual(owner.execute("select has_table_privilege('dot_reader','public.dot_narrow_acl','SELECT')").fetchone(), (False,))
                owner.execute("create table ops.dot_restored_future(id int); create sequence ops.dot_restored_seq")
                owner.execute("create table public.dot_restored_future(id int)")
                self.assertEqual(owner.execute("select has_table_privilege('dot_reader','ops.dot_restored_future','SELECT'),has_sequence_privilege('dot_reader','ops.dot_restored_seq','SELECT'),has_table_privilege('dot_reader','public.dot_restored_future','SELECT')").fetchone(), (False, False, True))
            with psycopg.connect(**{**args,"user":"dot_reader"}, autocommit=True) as dot:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    dot.execute("create temp table dot_restore_nope(id int)")
                for table in ("public.dot_narrow_acl", "ops.dot_restored_future"):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        dot.execute(sql.SQL("select * from {}").format(sql.Identifier(*table.split('.'))))
                dot.execute("select * from public.dot_restored_future").fetchall()

    @unittest.skipUnless(os.environ.get("CARR_DOT_REPO_SCHEMA") == "1", "requires assurance functions from the full schema")
    def test_assurance_gate_accepts_only_dot_select_exception(self):
        # Execute the gate's SQL itself against adversarial grants. This does
        # not keep a second privilege predicate that could agree with itself.
        tree = ast.parse((ROOT / "ops/assurance-evidence-acceptance-local-pg-gate.py").read_text())
        queries = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                   and isinstance(node.value, str) and "acl.grantee<>c.relowner" in node.value]
        self.assertEqual(len(queries), 1)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute("create table ops.assurance_dot_acl_fixture (id int)")
            try:
                self.assertEqual(owner.execute(queries[0]).fetchone(), (True,))
                owner.execute("alter role dot_reader rename to dot_existing_fixture")
                try:
                    self.assertEqual(owner.execute(queries[0]).fetchone(), (False,))
                finally:
                    owner.execute("alter role dot_existing_fixture rename to dot_reader")
                for grant, revoke in (
                    ("grant insert on ops.assurance_dot_acl_fixture to dot_reader", "revoke insert on ops.assurance_dot_acl_fixture from dot_reader"),
                    ("grant select on ops.assurance_dot_acl_fixture to carr_writer", "revoke select on ops.assurance_dot_acl_fixture from carr_writer"),
                    ("grant select on ops.assurance_dot_acl_fixture to dot_reader with grant option", "revoke grant option for select on ops.assurance_dot_acl_fixture from dot_reader"),
                ):
                    try:
                        owner.execute(grant)
                        self.assertEqual(owner.execute(queries[0]).fetchone(), (False,))
                    finally:
                        owner.execute(revoke)
                self.assertEqual(owner.execute(queries[0]).fetchone(), (True,))
            finally:
                owner.execute("drop table ops.assurance_dot_acl_fixture")


class ReleaseAbandonFixture(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("release_abandon", ROOT / "ops/release-abandon-selftest.py")
        self.abandon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.abandon)

    def test_prepared_database_clone_preserves_schema_but_isolates_rows(self):
        with self.abandon.isolated_ci_database("host=127.0.0.1") as source:
            with psycopg.connect(source) as connection:
                connection.execute("create table public.fixture_probe (value text)")
                connection.execute("insert into public.fixture_probe values ('pristine')")
            clone = self.abandon.clone_fixture_database(source, "abandon_legacy")
            with psycopg.connect(clone) as connection:
                self.assertEqual(connection.execute("select value from public.fixture_probe").fetchone(), ("pristine",))
                connection.execute("update public.fixture_probe set value='legacy' where value='pristine'")
            with psycopg.connect(source) as connection:
                self.assertEqual(connection.execute("select value from public.fixture_probe").fetchone(), ("pristine",))

    def test_tcp_fixture_accepts_spaced_and_long_temporary_roots(self):
        for prefix in ("review space ", "review-" + "x" * 100):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory(prefix=prefix, dir="/tmp") as parent:
                with patch.object(tempfile, "tempdir", parent):
                    with self.abandon.isolated_ci_database("host=127.0.0.1") as dsn:
                        with psycopg.connect(dsn) as connection:
                            self.assertEqual(connection.execute("select 1").fetchone(), (1,))
                            data = Path(connection.execute("show data_directory").fetchone()[0])
                            self.assertEqual(connection.execute("show unix_socket_directories").fetchone(), ("",))
                        self.assertEqual(data.parent.parent.resolve(), Path(parent).resolve())
                    self.assertFalse(data.parent.exists(), "verified shutdown removes the cluster")

    def test_failed_shutdown_retains_live_cluster_and_reports_location(self):
        run = subprocess.run
        for failure in ("nonzero", "timeout", "success_but_live"):
            with self.subTest(failure=failure):
                observed = {}

                def fail_stop(args, **kwargs):
                    if Path(args[0]).name == "pg_ctl" and args[-1] == "stop":
                        data = Path(args[args.index("-D") + 1])
                        observed.update(data=data, pid=int((data / "postmaster.pid").read_text().splitlines()[0]), args=args, kwargs=kwargs)
                        if failure == "timeout":
                            raise subprocess.TimeoutExpired(args, 60)
                        return subprocess.CompletedProcess(args, 0 if failure == "success_but_live" else 1,
                                                           "", "injected stop failure")
                    return run(args, **kwargs)

                try:
                    with patch.object(tempfile, "tempdir", "/tmp"), patch.object(subprocess, "run", side_effect=fail_stop):
                        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)) as raised:
                            with self.abandon.isolated_ci_database("host=127.0.0.1") as dsn:
                                with psycopg.connect(dsn) as connection:
                                    self.assertEqual(connection.execute("select 1").fetchone(), (1,))
                    os.kill(observed["pid"], 0)
                    self.assertTrue(observed["data"].exists(), "failed shutdown must retain a live cluster's data")
                    self.assertTrue((observed["data"].parent / "postgres.log").exists())
                    self.assertIn(str(observed["data"].parent), str(raised.exception))
                finally:
                    if observed:
                        # Only this test's freshly observed disposable postmaster.
                        if observed["data"].exists():
                            stopped = run(observed["args"], **observed["kwargs"])
                            self.assertEqual(stopped.returncode, 0, stopped.stderr)
                        else:
                            os.kill(observed["pid"], signal.SIGINT)
                        deadline = time.monotonic() + 10
                        while True:
                            try:
                                os.kill(observed["pid"], 0)
                            except ProcessLookupError:
                                break
                            self.assertLess(time.monotonic(), deadline, "test postmaster did not stop")
                            time.sleep(0.05)
                        if observed["data"].parent.exists():
                            shutil.rmtree(observed["data"].parent)

    def test_body_exception_propagates_after_verified_shutdown(self):
        error = ValueError("fixture body failure")
        with self.assertRaises(ValueError) as raised:
            with self.abandon.isolated_ci_database("host=127.0.0.1") as dsn:
                with psycopg.connect(dsn) as connection:
                    data = Path(connection.execute("show data_directory").fetchone()[0])
                raise error
        self.assertIs(raised.exception, error)
        self.assertFalse(data.parent.exists())

    def test_cli_reports_retained_cluster_after_failed_shutdown(self):
        run = subprocess.run
        observed = {}
        stderr = io.StringIO()

        def fail_stop(args, **kwargs):
            if Path(args[0]).name == "pg_ctl" and args[-1] == "stop":
                observed.update(data=Path(args[args.index("-D") + 1]), args=args, kwargs=kwargs)
                return subprocess.CompletedProcess(args, 1, "", "injected stop failure")
            return run(args, **kwargs)

        try:
            with patch.dict(os.environ, {"CARR_CI_DATABASE_URL": "host=127.0.0.1"}), \
                 patch.object(tempfile, "tempdir", "/tmp"), \
                 patch.object(subprocess, "run", side_effect=fail_stop), \
                 patch.object(self.abandon, "psql", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(self.abandon, "clone_fixture_database", return_value="host=127.0.0.1"), \
                 patch.object(self.abandon, "legacy_approval_receipt_refusal"), \
                 patch.object(self.abandon, "run_cases"), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                self.assertEqual(self.abandon.main(), 1)
            self.assertTrue(observed["data"].exists())
            self.assertIn(str(observed["data"].parent), stderr.getvalue())
            self.assertIn(str(observed["data"].parent / "postgres.log"), stderr.getvalue())
        finally:
            if observed:
                stopped = run(observed["args"], **observed["kwargs"])
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                shutil.rmtree(observed["data"].parent)


if __name__ == "__main__":
    unittest.main()
