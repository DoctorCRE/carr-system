#!/usr/bin/env python3
"""Executable DB contract for Program 5 typed staging recovery evidence."""

# ci: db-gate

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import threading
import uuid
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:
    sys.exit("staging-release-readback-gate: psycopg not installed")

# The postgres CLIENT lookup, shared with ops/p1-rebuild-gate.py. Loading the
# module by path is how every ops gate reaches tools/db-tap.py, whose filename
# has a hyphen and cannot be imported normally.
_REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("db_tap", _REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("staging-release-readback-gate: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)


PASSES: list[str] = []
FAILURES: list[str] = []
CURRENT_SHA = "a" * 40
PRIOR_SHA = "b" * 40
CURRENT_PROVIDER_VERSION = "10000000-0000-4000-8000-000000000001"
PRIOR_PROVIDER_VERSION = "20000000-0000-4000-8000-000000000002"
PLAN_HASH = "sha256:" + "c" * 64
ROLLBACK_PLAN = "runbooks/rollback-worker.md"
# Bound from the disposable database before each fixture is seeded.  The
# checked-in migration surface advances independently of this historical gate.
SCHEMA_HIGHEST_MIGRATION = "0202_staging_release_readback_receipt.sql"
SCHEMA_APPLIED_COUNT = 202
SCHEMA_LEDGER_SHA256 = "sha256:" + "7" * 64


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def refuses(cur, sql: str, params: tuple = ()) -> bool:
    cur.execute("savepoint p5_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint p5_refusal")
        return True
    cur.execute("rollback to savepoint p5_refusal")
    return False


def one(cur) -> tuple[Any, ...]:
    row = cur.fetchone()
    if row is None:
        raise AssertionError("database returned no row")
    return row


def authority(cur, principal: str) -> None:
    cur.execute(f"set session authorization {principal}")


def owner(cur) -> None:
    cur.execute("reset session authorization")


def ensure_authority_roles(cur) -> None:
    cur.execute("""do $$ begin
      if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then
        create role carr_authority_joe login;
      end if;
      if not exists(select 1 from pg_roles where rolname='carr_authority_dell') then
        create role carr_authority_dell login;
      end if;
    end $$""")
    cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")


def bind_live_schema_identity(cur) -> None:
    """Use this disposable database's exact schema ledger for fixture facts."""
    cur.execute("""select (max(filename collate \"C\") collate \"default\"),
                        count(*)::integer,
                        'sha256:' || encode(public.digest(
                          coalesce(string_agg(
                            convert_to(filename, 'UTF8') || decode('00', 'hex') ||
                            convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
                            ''::bytea order by filename collate \"C\"), ''::bytea),
                          'sha256'), 'hex')
                   from public.schema_migrations""")
    highest, count, digest = one(cur)
    if not highest or not count or not digest:
        raise AssertionError("disposable schema ledger is empty or unreadable")
    global SCHEMA_HIGHEST_MIGRATION, SCHEMA_APPLIED_COUNT, SCHEMA_LEDGER_SHA256
    SCHEMA_HIGHEST_MIGRATION = highest
    SCHEMA_APPLIED_COUNT = count
    SCHEMA_LEDGER_SHA256 = digest


def worker_release_identity_query() -> str:
    """The exact schema-identity SQL the deployed Worker runs for /release.

    Extracted from mcp-server/src/release.js rather than copied, so this gate
    executes the same query text production will run (rule a8c55a47). The
    Worker's unit suite mocks the `sql` tag, so no PostgreSQL parser sees that
    text before deploy — this execution is the parse-and-digest check. The
    2026-08-26 staging rehearsal caught exactly this: a misnested
    encode/digest/coalesce shipped to origin/main and every staging readback
    came back "schema identity is invalid".
    """
    source = (_REPO / "mcp-server" / "src" / "release.js").read_text()
    found = re.search(
        r"await sql`\s*(select count\(\*\)::int as applied_count.*?from v_schema_ledger)`",
        source, re.S)
    if not found:
        raise AssertionError(
            "mcp-server/src/release.js no longer contains the v_schema_ledger identity query")
    return found.group(1)


def check_worker_release_identity(cur) -> None:
    """Run the Worker's /release identity SQL and demand digest equality."""
    cur.execute("savepoint worker_release_identity")
    try:
        cur.execute(worker_release_identity_query())
        row = one(cur)
        ok = (row[0] == SCHEMA_APPLIED_COUNT
              and row[1] == SCHEMA_HIGHEST_MIGRATION
              and row[2] == SCHEMA_LEDGER_SHA256)
        detail = "" if ok else (
            f"worker query returned {row!r}, reference is "
            f"({SCHEMA_APPLIED_COUNT!r}, {SCHEMA_HIGHEST_MIGRATION!r}, {SCHEMA_LEDGER_SHA256!r})")
    except Exception as exc:
        cur.execute("rollback to savepoint worker_release_identity")
        ok, detail = False, f"worker /release identity query failed to execute: {exc}"
    cur.execute("release savepoint worker_release_identity")
    check("worker /release schema-identity SQL executes and matches the reference digest",
          ok, detail)


def check_worker_release_lettered_highwater(cur) -> None:
    """Exercise the extracted Worker query against an ordinary/lettered pair.

    The explicit C collation makes the result independent of the database's
    default locale: bytewise `9999a...` follows `9999_...`.
    """
    query = worker_release_identity_query()
    check("worker /release pins highest migration to C collation",
          '(max(filename collate "C") collate "default")' in " ".join(query.split()))
    cur.execute("savepoint worker_release_lettered_highwater")
    try:
        cur.execute("""insert into public.schema_migrations(filename,sha256) values
                       ('9999_ordinary_highwater.sql', repeat('1',64)),
                       ('9999a_lettered_highwater.sql', repeat('2',64))""")
        cur.execute("""select count(*)::int,
                              (max(filename collate "C") collate "default"),
                              'sha256:' || encode(public.digest(coalesce(string_agg(
                                convert_to(filename, 'UTF8') || decode('00', 'hex') ||
                                convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
                                ''::bytea order by filename collate "C"), ''::bytea),
                                'sha256'), 'hex')
                         from public.schema_migrations""")
        expected = one(cur)
        cur.execute(query)
        observed = one(cur)
        check("worker /release query returns the C-collated optional-letter highwater",
              observed == expected and observed[1] == '9999a_lettered_highwater.sql',
              f"worker query returned {observed!r}, C reference is {expected!r}")
    except Exception as exc:
        cur.execute("rollback to savepoint worker_release_lettered_highwater")
        check("worker /release query returns the C-collated optional-letter highwater",
              False, f"worker /release optional-letter highwater query failed: {exc}")
    else:
        # The mixed highwater rows prove only the extracted query; later gate
        # fixtures must observe the real migration ledger unchanged.
        cur.execute("rollback to savepoint worker_release_lettered_highwater")
    cur.execute("release savepoint worker_release_lettered_highwater")

def seed_fixture(cur, prefix: str) -> dict:
    bind_live_schema_identity(cur)
    now = datetime.now(timezone.utc)
    service_key = f"p5-readback-{prefix}-{uuid.uuid4()}"
    cur.execute("""insert into ops.service(key,name,family,criticality,owner_actor)
                   values(%s,'P5 typed readback gate','Platform','critical','joe') returning id""",
                (service_key,))
    service_id = cur.fetchone()[0]
    current_key = f"p5-current-{prefix}-{uuid.uuid4()}"
    prior_key = f"p5-prior-{prefix}-{uuid.uuid4()}"

    # The prior row represents already-completed historical Production truth.
    # Disable triggers only while constructing that fixture in the disposable
    # transaction; all checks remain active and the new runtime path is tested
    # below with triggers enabled.
    cur.execute("set local session_replication_role=replica")
    cur.execute("""insert into ops.release(
      correlation_id,release_key,service_id,environment,state,git_sha,provider,
      provider_version_id,performance_budget_ref,performance_budget_ms,
      recovery_strategy,artifact_digest,dependency_lock_digest,maker_actor,
      maker_verification_ref,test_evidence_ref,security_evidence_ref,verifier_actor,
      verifier_evidence_ref,rollback_ready,rollback_plan_ref,plan_hash,
      migration_set,schema_highest_migration,schema_applied_count,schema_ledger_sha256,
      approved_by_actor,approved_at,approval_expires_at,source_kind,source_ref,
      observed_at,expires_at,ended_at)
      values(%s,%s,%s,'production','complete',%s,'cloudflare-workers',%s,
      'budget:prior',250,'rollback',%s,%s,'maker','maker-proof','tests','security',
      'verifier','verify-proof',true,%s,%s,%s,%s,%s,%s,'joe',%s,%s,'wrapper','gate',%s,%s,%s)
      returning id""",
      (uuid.uuid4(),prior_key,service_id,PRIOR_SHA,PRIOR_PROVIDER_VERSION,
       "sha256:"+"d"*64,"sha256:"+"e"*64,ROLLBACK_PLAN,"sha256:"+"f"*64,
       ["0201_previous.sql"],"0201_previous.sql",SCHEMA_APPLIED_COUNT,SCHEMA_LEDGER_SHA256,
       now-timedelta(days=2),now+timedelta(days=2),now-timedelta(days=2),
       now+timedelta(days=20),now-timedelta(days=2)))
    prior_id = cur.fetchone()[0]
    cur.execute("""insert into ops.deployment(
      correlation_id,service_id,environment,state,git_sha,provider,provider_version_id,
      release_id,deployed_by_actor,verb_count,schema_highest_migration,
      doctrine_generation,started_at,ended_at,read_back_at,
      verification_evidence_ref,source_kind,source_ref,observed_at)
      values(%s,%s,'production','complete',%s,'cloudflare-workers',%s,%s,
      'historical',200,'0201_previous.sql',169,%s,%s,%s,'prior:/release',
      'wrapper','gate',%s)""",
      (uuid.uuid4(),service_id,PRIOR_SHA,PRIOR_PROVIDER_VERSION,prior_id,
       now-timedelta(days=2),now-timedelta(days=2),now-timedelta(days=2),
       now-timedelta(days=2)))
    cur.execute("set local session_replication_role=origin")

    cur.execute("""insert into ops.release(
      correlation_id,release_key,service_id,environment,state,git_sha,provider,
      provider_version_id,performance_budget_ref,performance_budget_ms,
      recovery_strategy,artifact_digest,dependency_lock_digest,maker_actor,
      maker_verification_ref,test_evidence_ref,security_evidence_ref,verifier_actor,
      verifier_evidence_ref,rollback_ready,rollback_plan_ref,plan_hash,
      migration_set,schema_highest_migration,schema_applied_count,schema_ledger_sha256,
      source_kind,source_ref,observed_at,expires_at)
      values(%s,%s,%s,'production','candidate',%s,'cloudflare-workers',%s,
      'budget:current',250,'rollback',%s,%s,'maker','maker-proof','tests','security',
      'verifier','verify-proof',true,%s,%s,%s,%s,%s,%s,'wrapper','gate',%s,%s) returning id""",
      (uuid.uuid4(),current_key,service_id,CURRENT_SHA,CURRENT_PROVIDER_VERSION,
       "sha256:"+"1"*64,"sha256:"+"2"*64,ROLLBACK_PLAN,PLAN_HASH,
       ["0202_staging_release_readback_receipt.sql"],
       SCHEMA_HIGHEST_MIGRATION,SCHEMA_APPLIED_COUNT,
       SCHEMA_LEDGER_SHA256,now,
       now+timedelta(days=2)))
    current_id = cur.fetchone()[0]
    return {"service_id":service_id,"current_id":current_id,"prior_id":prior_id,
            "current_key":current_key,"prior_key":prior_key}


def seed_staging_candidate(cur, fixture: dict) -> tuple[str, uuid.UUID]:
    key = f"p6-staging-{uuid.uuid4()}"
    cur.execute("""insert into ops.release(
      correlation_id,release_key,service_id,environment,state,git_sha,provider,provider_version_id,
      performance_budget_ref,performance_budget_ms,recovery_strategy,artifact_digest,
      dependency_lock_digest,maker_actor,maker_verification_ref,test_evidence_ref,
      security_evidence_ref,rollback_ready,rollback_plan_ref,plan_hash,migration_set,
      schema_highest_migration,schema_applied_count,schema_ledger_sha256,source_kind,source_ref,
      observed_at,expires_at)
      values(%s,%s,%s,'staging','candidate',%s,'cloudflare-workers',%s,
      'budget:staging',250,'rollback',%s,%s,'maker','maker-proof','tests','security',true,%s,%s,
      %s,%s,%s,%s,'wrapper','gate',now(),now()+interval '2 days') returning id""",
      (uuid.uuid4(),key,fixture["service_id"],CURRENT_SHA,uuid.uuid4(),"sha256:"+"4"*64,
       "sha256:"+"5"*64,ROLLBACK_PLAN,PLAN_HASH,["0219_staging_release_approval_receipt.sql"],
       SCHEMA_HIGHEST_MIGRATION,SCHEMA_APPLIED_COUNT,SCHEMA_LEDGER_SHA256))
    return key,cur.fetchone()[0]


def record_sql() -> str:
    return """select ops.record_staging_release_readback(
      %s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s)"""


def legacy_prior_record_sql() -> str:
    return """select ops.record_staging_release_readback(
      %s::uuid,%s::uuid,%s,%s,%s,%s,%s)"""


def prepare_sql() -> str:
    return """select ops.prepare_staging_deployment_attempt(
      %s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s)"""


def claim_sql() -> str:
    return "select ops.claim_staging_deployment_attempt(%s::uuid)"


def restore_prepare_sql() -> str:
    return """select ops.prepare_staging_restore_only_attempt(
      %s::uuid,%s::uuid,%s,%s,%s::uuid,%s)"""


def restore_prepare_params(fixture: dict, attempt: uuid.UUID, idem: uuid.UUID) -> tuple:
    return (idem, attempt, fixture["current_key"], fixture["prior_key"], attempt, CURRENT_SHA)


def record_params(fixture: dict, attempt: uuid.UUID, step: str, idem: uuid.UUID,
                  version: uuid.UUID, *, verb_count: int = 211,
                  program6_actions_enabled: bool = False) -> tuple:
    return (idem,version,f"carr-staging-{idem.hex}",verb_count,
            SCHEMA_HIGHEST_MIGRATION,SCHEMA_APPLIED_COUNT,170,
            program6_actions_enabled)


def legacy_prior_record_params(fixture: dict, attempt: uuid.UUID, step: str,
                               idem: uuid.UUID, version: uuid.UUID, *,
                               verb_count: int = 211) -> tuple:
    return (idem,version,f"carr-staging-{idem.hex}",verb_count,
            SCHEMA_HIGHEST_MIGRATION,SCHEMA_APPLIED_COUNT,170)


def prepare_params(fixture: dict, attempt: uuid.UUID, step: str,
                   idem: uuid.UUID) -> tuple:
    sha = PRIOR_SHA if step == "prior" else CURRENT_SHA
    if step == "standalone":
        return (idem,attempt,fixture["current_key"],None,None,step,sha)
    return (idem,attempt,fixture["current_key"],fixture["prior_key"],attempt,step,sha)


def prepare_and_claim(cur, fixture: dict, attempt: uuid.UUID,
                      step: str, idem: uuid.UUID) -> None:
    cur.execute(prepare_sql(), prepare_params(fixture, attempt, step, idem))
    one(cur)
    cur.execute(claim_sql(), (idem,))
    claimed = one(cur)[0]
    if claimed["deploy_allowed"] is not True:
        raise AssertionError("new staging attempt was not exclusively claimed")


def make_typed_bundle(cur, fixture: dict) -> None:
    """Create the exact staging recovery evidence an approval must bind."""
    attempt = uuid.uuid4()
    authority(cur, "carr_jobs")
    for step in ("current_before", "prior", "current_after"):
        idem, version = uuid.uuid4(), uuid.uuid4()
        prepare_and_claim(cur, fixture, attempt, step, idem)
        cur.execute(record_sql(), record_params(fixture, attempt, step, idem, version))
        one(cur)
    owner(cur)


def require_loopback(dsn: str) -> None:
    try:
        conninfo = conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise RuntimeError("raw migration fixture requires valid explicit conninfo") from exc
    if conninfo.get("service") or conninfo.get("servicefile"):
        raise RuntimeError("raw migration fixture refuses libpq service indirection")
    hosts: list[str] = []
    for key in ("host", "hostaddr"):
        value = str(conninfo.get(key) or "")
        if not value:
            continue
        if "," in value:
            raise RuntimeError("raw migration fixture refuses multi-host conninfo")
        hosts.append(value)
    if not hosts or "," in str(conninfo.get("port") or ""):
        raise RuntimeError("raw migration fixture requires one explicit loopback target")
    for host in hosts:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise RuntimeError("raw migration fixture refuses every non-loopback DATABASE_URL")


def raw_0222_capture_fixture(dsn: str) -> None:
    """Prove 0222's own INSERT captures only pre-boundary prepared truth."""
    require_loopback(dsn)
    name = f"p5_0222_capture_{uuid.uuid4().hex}"
    isolated = make_conninfo(dsn, dbname=name)
    admin = make_conninfo(dsn, dbname="postgres")
    repo = pathlib.Path(__file__).resolve().parent.parent
    # Not the bare name: with no client installed that failed as FileNotFoundError
    # from inside subprocess, naming neither the missing dependency nor the fix.
    psql = db_tap.psql_bin()
    try:
        with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        subprocess.run([psql, "-v", "ON_ERROR_STOP=1", "-q", "-d", isolated,
                        "-f", str(repo / "db/schema.sql")], check=True, capture_output=True, text=True)
        with psycopg.connect(isolated, autocommit=False) as conn, conn.cursor() as cur:
            # A regenerated snapshot can already contain 0222.  Reconstruct
            # exactly its predecessor surface before seeding so the raw file is
            # exercised, while remaining harmless for a pre-0222 snapshot.
            cur.execute("""delete from public.schema_migrations
                           where filename='0222_legacy_prior_staging_readback.sql';
              drop function if exists ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint);
              drop table if exists ops.legacy_prior_staging_readback_allowlist;
              do $$ begin
                if to_regprocedure('ops.record_staging_release_readback_program6(uuid,uuid,text,integer,text,integer,bigint,boolean)') is not null then
                  drop function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean);
                  alter function ops.record_staging_release_readback_program6(uuid,uuid,text,integer,text,integer,bigint,boolean)
                    rename to record_staging_release_readback;
                end if;
              end $$;
              revoke all on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)
                from public,carr_reader,carr_writer,carr_authority;
              grant execute on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean) to carr_jobs;""")
            ensure_authority_roles(cur)
            fixture = seed_fixture(cur, "raw0222")
            pre_attempt, pre_idem = uuid.uuid4(), uuid.uuid4()
            authority(cur, "carr_jobs")
            cur.execute(prepare_sql(), prepare_params(fixture, pre_attempt, "prior", pre_idem)); one(cur)
            owner(cur)
            conn.commit()
        # Apply the raw historical migration itself. A current snapshot can
        # legitimately contain later ledger rows, and the production runner
        # correctly refuses an artificial earlier hole; this fixture is about
        # 0222's migration-time capture, not bypassing ledger ordering.
        subprocess.run(
            [psql, "-v", "ON_ERROR_STOP=1", "-q", "-d", isolated,
             "-f", str(repo / "migrations/0222_legacy_prior_staging_readback.sql")],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        with psycopg.connect(isolated, autocommit=False) as conn, conn.cursor() as cur:
            cur.execute("select deployment_attempt_id from ops.legacy_prior_staging_readback_allowlist where idempotency_key=%s", (pre_idem,))
            captured = one(cur)[0] is not None
            post_attempt, post_idem = uuid.uuid4(), uuid.uuid4()
            authority(cur, "carr_jobs")
            cur.execute(prepare_sql(), prepare_params(fixture, post_attempt, "prior", post_idem)); one(cur)
            owner(cur)
            cur.execute("select count(*) from ops.legacy_prior_staging_readback_allowlist where idempotency_key=%s", (post_idem,))
            post_absent = one(cur)[0] == 0
            check("raw 0222 migration captures only the pre-boundary eligible prior attempt", captured and post_absent)
            for role in ("carr_reader", "carr_writer", "carr_authority"):
                cur.execute("select has_table_privilege(%s,'ops.legacy_prior_staging_readback_allowlist','insert,update,delete')", (role,))
                check(f"{role} has no legacy allowlist DML", one(cur)[0] is False)
            conn.commit()
    finally:
        with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s and pid<>pg_backend_pid()", (name,))
            cur.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("staging-release-readback-gate: DATABASE_URL is not set")
    raw_0222_capture_fixture(dsn)
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        ensure_authority_roles(cur)
        fixture = seed_fixture(cur, "main")
        check_worker_release_identity(cur)
        check_worker_release_lettered_highwater(cur)
        attempt = uuid.uuid4()
        ids = [uuid.uuid4(),uuid.uuid4(),uuid.uuid4()]
        versions = [uuid.uuid4(),uuid.uuid4(),uuid.uuid4()]

        # SET ROLE is deliberately insufficient: a pooled owner connection may
        # not impersonate the jobs writer. The authenticated session_user is the
        # boundary, not current_user or caller actor text.
        cur.execute("set local role carr_jobs")
        check("pooled SET ROLE cannot prepare an attempt", refuses(cur, prepare_sql(),
              prepare_params(fixture,attempt,"current_before",ids[0])))
        cur.execute("reset role")

        authority(cur,"carr_jobs")
        restore_attempt = uuid.uuid4(); restore_idem = uuid.uuid4()
        cur.execute(restore_prepare_sql(), restore_prepare_params(fixture, restore_attempt, restore_idem))
        restore_prepared = one(cur)[0]
        cur.execute("select target_provider_version_id::text from ops.staging_restore_only_attempt where idempotency_key=%s",
                    (restore_idem,))
        check("restore-only prepare explicitly casts text provider version into UUID evidence",
              restore_prepared["state"] == "prepared" and one(cur)[0] == CURRENT_PROVIDER_VERSION)
        cur.execute(restore_prepare_sql(), restore_prepare_params(fixture, restore_attempt, restore_idem))
        restore_replay = one(cur)[0]
        cur.execute("select ops.claim_staging_restore_only_attempt(%s::uuid)", (restore_idem,))
        restore_claim = one(cur)[0]
        check("restore-only UUID preparation replays and claims through the jobs writer",
              restore_replay["replayed"] is True and restore_claim["mutation_allowed"] is True)
        results = []
        for step, idem, version in zip(("current_before","prior","current_after"),ids,versions):
            prepare_and_claim(cur,fixture,attempt,step,idem)
            cur.execute(record_sql(),record_params(fixture,attempt,step,idem,version))
            results.append(one(cur)[0])
        check("current-prior-current creates one typed bundle and run",
              results[-1]["bundle_id"] is not None and results[-1]["recovery_run_id"] is not None)
        cur.execute(record_sql(),record_params(fixture,attempt,"current_after",ids[-1],versions[-1]))
        replay = one(cur)[0]
        check("exact replay returns the durable receipt", replay["replayed"] is True
              and replay["receipt_id"]==results[-1]["receipt_id"])
        cur.execute(prepare_sql(),prepare_params(fixture,attempt,"current_after",ids[-1]))
        prepared_replay=one(cur)[0]
        check("commit-response loss resumes from durable observed attempt",
              prepared_replay["state"]=="observed"
              and prepared_replay["receipt_ref"]==replay["receipt_ref"])
        cur.execute(claim_sql(),(ids[-1],))
        check("observed attempt can never authorize a redeploy",one(cur)[0]["deploy_allowed"] is False)
        check("mutated replay is refused", refuses(cur,record_sql(),
              record_params(fixture,attempt,"current_after",ids[-1],versions[-1],verb_count=212)))
        check("Program 6 posture change on an exact replay is refused", refuses(cur,record_sql(),
              record_params(fixture,attempt,"current_after",ids[-1],versions[-1],
                            program6_actions_enabled=True)))
        owner(cur)

        # An enabled receipt is a different immutable fact from an otherwise
        # identical disabled one.  The column remains nullable solely for
        # receipts written before 0218, whose hashes cannot be retrofitted.
        enabled_attempt=uuid.uuid4(); enabled_idem=uuid.uuid4(); enabled_version=uuid.uuid4()
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,enabled_attempt,"standalone",enabled_idem)
        cur.execute(record_sql(),record_params(fixture,enabled_attempt,"standalone",enabled_idem,
                                               enabled_version,program6_actions_enabled=True))
        enabled=one(cur)[0]
        cur.execute("select program6_actions_enabled,projection_sha256 from ops.staging_release_readback_receipt where id=%s",
                    (enabled["receipt_id"],))
        enabled_posture,enabled_hash=one(cur)
        cur.execute("select projection_sha256 from ops.staging_release_readback_receipt where id=%s",(results[-1]["receipt_id"],))
        disabled_hash=one(cur)[0]
        check("enabled readback stores posture and binds a distinct receipt hash",
              enabled_posture is True and enabled_hash != disabled_hash)
        cur.execute(record_sql(),record_params(fixture,enabled_attempt,"standalone",enabled_idem,
                                               enabled_version,program6_actions_enabled=True))
        check("enabled readback exact replay is idempotent",one(cur)[0]["replayed"] is True)
        check("enabled receipt refuses a changed disabled replay",refuses(cur,record_sql(),
              record_params(fixture,enabled_attempt,"standalone",enabled_idem,enabled_version,
                            program6_actions_enabled=False)))
        # Simulate a receipt that predated this column.  Its existing hash and
        # append-only fact survive; an upgraded caller may resume its common
        # typed input but cannot claim the historical hash bound the new field.
        owner(cur)
        cur.execute("set local session_replication_role=replica")
        cur.execute("update ops.staging_release_readback_receipt set program6_actions_enabled=null where id=%s",
                    (enabled["receipt_id"],))
        cur.execute("set local session_replication_role=origin")
        authority(cur,"carr_jobs")
        check("Program 6 recorder refuses a simulated legacy NULL-posture receipt",refuses(
            cur,record_sql(),record_params(fixture,enabled_attempt,"standalone",enabled_idem,
                                            enabled_version,program6_actions_enabled=False)))
        owner(cur)
        cur.execute("""select is_nullable='YES' from information_schema.columns
                       where table_schema='ops' and table_name='staging_release_readback_receipt'
                         and column_name='program6_actions_enabled'""")
        check("legacy receipts remain structurally representable with NULL posture",one(cur)[0] is True)
        cur.execute("""select to_regprocedure('ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)') is null,
                              has_function_privilege('carr_jobs','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)'::regprocedure,'execute'),
                              has_function_privilege('carr_reader','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)'::regprocedure,'execute'),
                              has_function_privilege('carr_writer','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)'::regprocedure,'execute'),
                              has_function_privilege('carr_authority','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)'::regprocedure,'execute')""")
        check("only carr_jobs retains the new eight-argument recorder",one(cur)==(False,True,False,False,False))

        # Compatibility is intentionally narrower than the pre-0218 writer:
        # only the prior observation of a typed recovery may retain its
        # historical NULL posture projection.
        legacy_attempt=uuid.uuid4(); legacy_before_idem=uuid.uuid4(); legacy_idem=uuid.uuid4()
        legacy_before_version=uuid.uuid4(); legacy_version=uuid.uuid4()
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,legacy_attempt,"current_before",legacy_before_idem)
        cur.execute(record_sql(),record_params(fixture,legacy_attempt,"current_before",
                                               legacy_before_idem,legacy_before_version))
        one(cur)
        cur.execute(prepare_sql(),prepare_params(fixture,legacy_attempt,"prior",legacy_idem))
        one(cur)
        # Simulate the one-time migration-time capture of an already prepared
        # historical prior attempt; runtime roles cannot add this row.
        owner(cur)
        cur.execute("""insert into ops.legacy_prior_staging_readback_allowlist(
                         idempotency_key,deployment_attempt_id)
                       select idempotency_key,id from ops.staging_deployment_attempt
                       where idempotency_key=%s""",(legacy_idem,))
        authority(cur,"carr_jobs")
        check("runtime carr_jobs cannot extend the legacy compatibility allowlist",refuses(
            cur,"""insert into ops.legacy_prior_staging_readback_allowlist(
                     idempotency_key,deployment_attempt_id)
                   select idempotency_key,id from ops.staging_deployment_attempt
                   where idempotency_key=%s""",(legacy_idem,)))
        check("allowlisted legacy prior still refuses before its normal claim",refuses(
            cur,legacy_prior_record_sql(),legacy_prior_record_params(
                fixture,legacy_attempt,"prior",legacy_idem,legacy_version)))
        cur.execute(claim_sql(),(legacy_idem,))
        check("allowlisted legacy prior receives its normal exclusive claim",
              one(cur)[0]["deploy_allowed"] is True)
        cur.execute(legacy_prior_record_sql(),legacy_prior_record_params(
            fixture,legacy_attempt,"prior",legacy_idem,legacy_version))
        legacy_result=one(cur)[0]
        cur.execute("""select r.program6_actions_enabled is null, r.observed_release_id=%s,
          r.projection_sha256='sha256:'||encode(public.digest(jsonb_build_object(
            'deployment_attempt_id',a.id,'correlation_id',a.correlation_id,'recovery_attempt_id',a.recovery_attempt_id,
            'recovery_step',a.recovery_step,'rehearsal_release_id',r.rehearsal_release_id,'observed_release_id',r.observed_release_id,
            'prior_release_id',a.prior_release_id,'service_id',r.service_id,'environment','staging','git_sha',a.git_sha,
            'provider','cloudflare-workers','provider_version_id',r.provider_version_id,'provider_tag',r.provider_tag,
            'verb_count',r.verb_count,'schema_highest_migration',r.schema_highest_migration,'schema_applied_count',r.schema_applied_count,
            'declared_migration_set_sha256',a.declared_migration_set_sha256,'declared_migration_count',a.declared_migration_count,
            'declared_schema_applied_count',a.declared_schema_applied_count,'declared_schema_ledger_sha256',a.declared_schema_ledger_sha256,
            'doctrine_generation',r.doctrine_generation)::text,'sha256'),'hex')
          from ops.staging_release_readback_receipt r
          join ops.staging_deployment_attempt a on a.id=r.deployment_attempt_id
          where r.id=%s""",
                    (fixture["prior_id"],legacy_result["receipt_id"]))
        check("legacy seven-argument prior writes NULL posture and exact pre-Program-6 projection for the completed Production release",
              one(cur)==(True,True,True))
        cur.execute(legacy_prior_record_sql(),legacy_prior_record_params(
            fixture,legacy_attempt,"prior",legacy_idem,legacy_version))
        check("legacy seven-argument prior exact replay is idempotent",
              one(cur)[0]["replayed"] is True)
        check("legacy seven-argument prior changed input is refused",refuses(
            cur,legacy_prior_record_sql(),legacy_prior_record_params(
                fixture,legacy_attempt,"prior",legacy_idem,legacy_version,verb_count=212)))
        check("Program 6 recorder refuses a NULL-posture legacy replay",refuses(
            cur,record_sql(),record_params(fixture,legacy_attempt,"prior",legacy_idem,
                                            legacy_version,program6_actions_enabled=False)))
        legacy_after_idem=uuid.uuid4(); legacy_after_version=uuid.uuid4()
        prepare_and_claim(cur,fixture,legacy_attempt,"current_after",legacy_after_idem)
        cur.execute(record_sql(),record_params(fixture,legacy_attempt,"current_after",
                                               legacy_after_idem,legacy_after_version))
        legacy_after=one(cur)[0]
        check("legacy prior participates in the exact typed recovery bundle",
              legacy_after["bundle_id"] is not None and legacy_after["recovery_run_id"] is not None)
        for rejected_step in ("standalone","current_before","current_after"):
            rejected_attempt=uuid.uuid4(); rejected_idem=uuid.uuid4()
            prepare_and_claim(cur,fixture,rejected_attempt,rejected_step,rejected_idem)
            check(f"legacy seven-argument writer refuses {rejected_step}",refuses(
                cur,legacy_prior_record_sql(),legacy_prior_record_params(
                    fixture,rejected_attempt,rejected_step,rejected_idem,uuid.uuid4())))
        owner(cur)
        cur.execute("""select to_regprocedure('ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)') is not null,
                              has_function_privilege('carr_jobs','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure,'execute'),
                              has_function_privilege('carr_reader','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure,'execute'),
                              has_function_privilege('carr_writer','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure,'execute'),
                              has_function_privilege('carr_authority','ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure,'execute')""")
        check("only carr_jobs receives the constrained legacy seven-argument recorder",
              one(cur)==(True,True,False,False,False))

        crash_attempt=uuid.uuid4(); crash_id=uuid.uuid4(); crash_version=uuid.uuid4()
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,crash_attempt,"current_before",crash_id)
        cur.execute(prepare_sql(),prepare_params(fixture,crash_attempt,"current_before",crash_id))
        crash_resume=one(cur)[0]
        check("provider-success client-crash resumes a claimed prepared attempt",
              crash_resume["state"]=="prepared" and crash_resume["deploy_claimed"] is True)
        cur.execute(claim_sql(),(crash_id,))
        check("claimed attempt refuses blind redeploy",one(cur)[0]["deploy_allowed"] is False)
        cur.execute(record_sql(),record_params(fixture,crash_attempt,"current_before",crash_id,crash_version))
        crash_receipt=one(cur)[0]
        cur.execute(record_sql(),record_params(fixture,crash_attempt,"current_before",crash_id,crash_version))
        check("provider-success recovery records then replays one receipt",
              crash_receipt["replayed"] is False and one(cur)[0]["replayed"] is True)
        changed_version=uuid.uuid4()
        check("recreated tag with changed provider UUID is refused",refuses(cur,record_sql(),
              record_params(fixture,crash_attempt,"current_before",crash_id,changed_version)))
        owner(cur)

        cur.execute("""select count(*),count(distinct deployment_id),count(distinct provider_version_id)
                       from ops.staging_release_readback_receipt where recovery_attempt_id=%s""",(attempt,))
        check("exactly three immutable deployment observations are retained",cur.fetchone()==(3,3,3))
        cur.execute("""select count(*) from ops.run r join ops.staging_recovery_rehearsal_bundle b
          on b.id=r.recovery_rehearsal_bundle_id where b.recovery_attempt_id=%s
          and r.evidence_ref=b.evidence_ref and r.release_id=b.current_release_id""",(attempt,))
        check("run resolves the exact bundle rather than free text",one(cur)[0]==1)

        # Caller-forged free text cannot recreate or unlock the typed edge.
        check("arbitrary succeeded recovery run is refused", refuses(cur,"""insert into ops.run(
          correlation_id,kind,service_id,release_id,environment,run_key,state,
          started_at,ended_at,source_kind,source_ref,evidence_ref,recovery_strategy,recovery_plan_ref)
          values(%s,'check',%s,%s,'staging','recovery.rehearsal.forged','succeeded',
          now(),now(),'operator','attacker','attacker:any','rollback',%s)""",
          (uuid.uuid4(),fixture["service_id"],fixture["current_id"],ROLLBACK_PLAN)))

        # Out-of-order observations can be retained as failed-attempt facts but
        # can never be reduced into a successful bundle/run.
        bad_attempt = uuid.uuid4(); bad_ids=[uuid.uuid4() for _ in range(3)]
        bad_versions=[uuid.uuid4() for _ in range(3)]
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,bad_attempt,"prior",bad_ids[0])
        cur.execute(record_sql(),record_params(fixture,bad_attempt,"prior",bad_ids[0],bad_versions[0]))
        prepare_and_claim(cur,fixture,bad_attempt,"current_before",bad_ids[1])
        cur.execute(record_sql(),record_params(fixture,bad_attempt,"current_before",bad_ids[1],bad_versions[1]))
        prepare_and_claim(cur,fixture,bad_attempt,"current_after",bad_ids[2])
        check("out-of-order recovery cannot create a bundle",refuses(cur,record_sql(),
              record_params(fixture,bad_attempt,"current_after",bad_ids[2],bad_versions[2])))
        owner(cur)

        cross_fixture=seed_fixture(cur,"cross")
        cross_attempt=uuid.uuid4(); cross_ids=[uuid.uuid4() for _ in range(3)]
        cross_versions=[uuid.uuid4() for _ in range(3)]
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,cross_attempt,"current_before",cross_ids[0])
        cur.execute(record_sql(),record_params(fixture,cross_attempt,"current_before",cross_ids[0],cross_versions[0]))
        prepare_and_claim(cur,cross_fixture,cross_attempt,"prior",cross_ids[1])
        cur.execute(record_sql(),record_params(cross_fixture,cross_attempt,"prior",cross_ids[1],cross_versions[1]))
        prepare_and_claim(cur,fixture,cross_attempt,"current_after",cross_ids[2])
        check("cross-release correlation reuse cannot create a bundle",refuses(cur,record_sql(),
              record_params(fixture,cross_attempt,"current_after",cross_ids[2],cross_versions[2])))
        owner(cur)

        stale_attempt=uuid.uuid4(); stale_id=uuid.uuid4(); stale_version=uuid.uuid4()
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,stale_attempt,"current_before",stale_id)
        stale=list(record_params(fixture,stale_attempt,"current_before",stale_id,stale_version))
        stale[4]="0201_stale_but_valid.sql"
        check("valid-shaped stale schema is refused",refuses(cur,record_sql(),tuple(stale)))
        stale_count=list(record_params(
            fixture,stale_attempt,"current_before",stale_id,stale_version))
        stale_count[5]=SCHEMA_APPLIED_COUNT-1
        check("highest-correct valid-shaped stale applied count is refused",
              refuses(cur,record_sql(),tuple(stale_count)))
        owner(cur)

        # Direct DML is absent and append-only triggers also protect owner paths.
        for role in ("carr_jobs","carr_writer","carr_authority"):
            for table in ("ops.staging_deployment_attempt","ops.staging_deployment_claim",
                          "ops.staging_release_readback_receipt"):
                cur.execute("select has_table_privilege(%s,%s,'insert,update,delete')",(role,table))
                check(f"{role} has no direct {table} DML",one(cur)[0] is False)
        check("receipt UPDATE is structurally refused",refuses(cur,
              "update ops.staging_release_readback_receipt set verb_count=999 where recovery_attempt_id=%s",(attempt,)))
        check("bundle DELETE is structurally refused",refuses(cur,
              "delete from ops.staging_recovery_rehearsal_bundle where recovery_attempt_id=%s",(attempt,)))
        check("source deployment rewrite is structurally refused",refuses(cur,"""update ops.deployment
              set verification_evidence_ref='attacker' where id=(select deployment_id
              from ops.staging_release_readback_receipt where recovery_attempt_id=%s limit 1)""",(attempt,)))
        check("candidate cannot skip Joe approval into deploying",refuses(cur,
              "update ops.release set state='deploying',approved_by_actor='joe',approved_at=now(),approval_expires_at=now()+interval '1 hour' where id=%s",
              (fixture["current_id"],)))

        # Build a deliberately stale-but-valid typed bundle fixture under the
        # disposable owner and prove that even Joe cannot approve through it.
        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.staging_recovery_rehearsal_bundle
          set declared_schema_highest_migration='0201_stale_but_valid.sql'
          where current_release_id=%s""",(fixture["current_id"],))
        cur.execute("set local session_replication_role=origin")
        authority(cur,"carr_authority_joe")
        check("Joe approval rejects a typed bundle for a stale valid-shaped schema",refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12)",
              (fixture["current_key"],PLAN_HASH,uuid.uuid4())))
        owner(cur)
        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.staging_recovery_rehearsal_bundle
          set declared_schema_highest_migration=%s,
              declared_schema_applied_count=%s
          where current_release_id=%s""",
          (SCHEMA_HIGHEST_MIGRATION,SCHEMA_APPLIED_COUNT-1,fixture["current_id"]))
        cur.execute("set local session_replication_role=origin")
        authority(cur,"carr_authority_joe")
        check("Joe approval rejects a typed bundle for a stale applied count",refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12)",
              (fixture["current_key"],PLAN_HASH,uuid.uuid4())))
        owner(cur)
        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.staging_recovery_rehearsal_bundle
          set declared_schema_applied_count=%s
          where current_release_id=%s""",(SCHEMA_APPLIED_COUNT,fixture["current_id"]))
        cur.execute("set local session_replication_role=origin")

        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.staging_recovery_rehearsal_bundle
          set declared_schema_ledger_sha256=%s
          where current_release_id=%s""",("sha256:"+"8"*64,fixture["current_id"]))
        cur.execute("set local session_replication_role=origin")
        authority(cur,"carr_authority_joe")
        check("Joe approval rejects a typed bundle for a stale ledger digest",refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12)",
              (fixture["current_key"],PLAN_HASH,uuid.uuid4())))
        owner(cur)
        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.staging_recovery_rehearsal_bundle
          set declared_schema_ledger_sha256=%s
          where current_release_id=%s""",(SCHEMA_LEDGER_SHA256,fixture["current_id"]))
        cur.execute("set local session_replication_role=origin")

        # Dell retains authority capability generally but is never an approval
        # gate or substitute for Joe on this system promotion.
        approval_idem=uuid.uuid4()
        authority(cur,"carr_authority_dell")
        check("Dell cannot replace Joe for Program 5 approval",refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12)",
              (fixture["current_key"],PLAN_HASH,approval_idem)))
        owner(cur)
        authority(cur,"carr_authority_joe")
        check("six-argument approval refuses a partial verifier pair", refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12,%s,%s)",
              (fixture["current_key"], PLAN_HASH, uuid.uuid4(), "verifier", None)))
        check("six-argument approval refuses whitespace verifier evidence", refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12,%s,%s)",
              (fixture["current_key"], PLAN_HASH, uuid.uuid4(), "   ", "   ")))
        check("six-argument approval refuses a maker normalized as verifier", refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12,%s,%s)",
              (fixture["current_key"], PLAN_HASH, uuid.uuid4(), " MAKER ", "proof")))
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12)",
                    (fixture["current_key"],PLAN_HASH,approval_idem))
        approval=one(cur)[0]
        owner(cur)
        cur.execute("select state,approved_by_actor,approval_receipt_id from ops.release where id=%s",
                    (fixture["current_id"],))
        state,actor,approval_receipt_id=one(cur)
        check("Joe approves atomically through a typed receipt",state=="approved" and actor=="joe"
              and str(approval_receipt_id)==approval["approval_receipt_id"])
        check("approved projection cannot be rewritten",refuses(cur,
              "update ops.release set approval_expires_at=approval_expires_at+interval '1 minute' where id=%s",
              (fixture["current_id"],)))
        check("approved verifier fields cannot be rewritten", refuses(cur,
              "update ops.release set verifier_actor='other' where id=%s",
              (fixture["current_id"],)))
        authority(cur,"carr_jobs")
        check("routine writer cannot rewrite an approved verifier", refuses(cur,
              "update ops.release set verifier_actor='other' where id=%s",
              (fixture["current_id"],)))
        owner(cur)
        cur.execute("""select a.verifier_actor,a.verifier_evidence_ref,r.verifier_actor,
                       r.verifier_evidence_ref,a.approval_sha256='sha256:'||encode(public.digest(
                       jsonb_build_object('release_id',r.id,'plan_hash',r.plan_hash,
                       'recovery_run_id',a.recovery_run_id,'recovery_bundle_id',a.recovery_bundle_id,
                       'approved_by_actor',a.approved_by_actor,'approved_at',a.approved_at,
                       'approval_expires_at',a.approval_expires_at,'verifier_actor',a.verifier_actor,
                       'verifier_evidence_ref',a.verifier_evidence_ref)::text,'sha256'),'hex')
                       from ops.release r join ops.release_approval_receipt a on a.id=r.approval_receipt_id
                       where r.id=%s""", (fixture["current_id"],))
        check("receipt and approval hash bind the canonical verifier pair", one(cur)==
              ("verifier","verify-proof","verifier","verify-proof",True))
        authority(cur,"carr_authority_joe")
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12)",
                    (fixture["current_key"],PLAN_HASH,approval_idem))
        check("Joe approval exact replay is idempotent",one(cur)[0]["replayed"] is True)
        check("Joe approval key rejects a changed expiry",refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,11)",
              (fixture["current_key"],PLAN_HASH,approval_idem)))
        owner(cur)

        # This is the production failure path: completion is an invoker-rights
        # trigger fired by carr_jobs, and its exact typed-bundle comparison
        # invokes program5_migration_set_sha256().  Seed only the serving and
        # performance facts under the disposable owner; the state transition
        # itself must succeed as the routine role.
        completion_fixture = seed_fixture(cur, "completion")
        make_typed_bundle(cur, completion_fixture)
        authority(cur, "carr_authority_joe")
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12)",
                    (completion_fixture["current_key"], PLAN_HASH, uuid.uuid4()))
        one(cur)
        owner(cur)
        completion_correlation = uuid.uuid4()
        completion_now = datetime.now(timezone.utc)
        completion_end = completion_now + timedelta(milliseconds=100)
        cur.execute("""insert into ops.deployment(
          correlation_id,service_id,environment,state,git_sha,provider,provider_version_id,
          release_id,deployed_by_actor,verb_count,schema_highest_migration,
          doctrine_generation,started_at,ended_at,read_back_at,
          verification_evidence_ref,source_kind,source_ref,observed_at)
          values(%s,%s,'production','complete',%s,'cloudflare-workers',%s,%s,
          'jobs',211,%s,170,%s,%s,%s,'production:/readback','wrapper','gate',%s)""",
          (completion_correlation,completion_fixture["service_id"],CURRENT_SHA,CURRENT_PROVIDER_VERSION,
           completion_fixture["current_id"],SCHEMA_HIGHEST_MIGRATION,
           completion_now,completion_now,completion_now,completion_now))
        cur.execute("""insert into ops.run(
          correlation_id,kind,service_id,environment,run_key,state,started_at,ended_at,
          source_kind,source_ref,observed_at,evidence_ref,release_id,budget_ms)
          values(%s,'check',%s,'production','performance.release','succeeded',
          %s,%s,'wrapper','gate',%s,'performance:/receipt',%s,250)""",
          (completion_correlation,completion_fixture["service_id"],completion_now,completion_end,
           completion_now,completion_fixture["current_id"]))
        check("only carr_jobs receives the completion-trigger hash helper",
              _has_function(cur,"carr_jobs","ops.program5_migration_set_sha256(text[])")
              and not any(_has_function(cur,role,"ops.program5_migration_set_sha256(text[])")
                          for role in ("carr_reader","carr_writer","carr_authority")))
        authority(cur,"carr_jobs")
        cur.execute("update ops.release set state='complete',ended_at=clock_timestamp() where id=%s",
                    (completion_fixture["current_id"],))
        owner(cur)
        cur.execute("select state from ops.release where id=%s",(completion_fixture["current_id"],))
        check("carr_jobs completes an approved release through the exact assurance trigger",
              one(cur)[0]=="complete")

        revised_plan="sha256:"+"9"*64
        cur.execute("update ops.release set plan_hash=%s where id=%s",
                    (revised_plan,fixture["current_id"]))
        cur.execute("select state,approval_receipt_id from ops.release where id=%s",
                    (fixture["current_id"],))
        revised_state,revised_pointer=one(cur)
        cur.execute("select count(*) from ops.release_approval_receipt where release_id=%s",
                    (fixture["current_id"],))
        check("plan revision clears only the current approval pointer",
              revised_state=="candidate" and revised_pointer is None and one(cur)[0]==1)

        late_fixture=seed_fixture(cur,"late")
        make_typed_bundle(cur,late_fixture)
        cur.execute("update ops.release set verifier_actor=null,verifier_evidence_ref=null where id=%s",
                    (late_fixture["current_id"],))
        late_idem=uuid.uuid4()
        authority(cur,"carr_authority_joe")
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12,%s,%s)",
                    (late_fixture["current_key"],PLAN_HASH,late_idem," Late-Checker "," late:proof "))
        late_approval=one(cur)[0]
        check("six-argument late verifier approval succeeds canonically",
              late_approval["replayed"] is False)
        cur.execute("select ops.approve_program5_release(%s,%s,%s::uuid,12,%s,%s)",
                    (late_fixture["current_key"],PLAN_HASH,late_idem,"late-checker","late:proof"))
        check("six-argument canonical replay is idempotent",one(cur)[0]["replayed"] is True)
        check("six-argument replay refuses changed verifier input",refuses(cur,
              "select ops.approve_program5_release(%s,%s,%s::uuid,12,%s,%s)",
              (late_fixture["current_key"],PLAN_HASH,late_idem,"other","late:proof")))
        owner(cur)

        check("routine writer cannot call Joe approval",not _has_function(cur,"carr_jobs",
              "ops.approve_program5_release(text,text,uuid,integer)"))
        check("routine writer cannot call six-argument Joe approval",not _has_function(cur,"carr_jobs",
              "ops.approve_program5_release(text,text,uuid,integer,text,text)"))

        staging_key,staging_id=seed_staging_candidate(cur,fixture)
        staging_idem=uuid.uuid4()
        check("candidate cannot become staging-approved without a typed receipt",refuses(cur,
              "update ops.release set state='approved',approved_by_actor='joe',approved_at=now(),approval_expires_at=now()+interval '1 hour' where id=%s",
              (staging_id,)))
        authority(cur,"carr_authority_dell")
        check("Dell cannot approve a staging candidate",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,staging_idem,"verifier","staging:proof")))
        owner(cur)
        authority(cur,"carr_authority_joe")
        check("staging approval refuses maker as verifier",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,staging_idem,"maker","staging:proof")))
        cur.execute("select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
                    (staging_key,PLAN_HASH,staging_idem,"verifier","staging:proof"))
        staging_approval=one(cur)[0]
        owner(cur)
        cur.execute("select environment,state,approved_by_actor,plan_hash,staging_approval_receipt_id from ops.release where id=%s",(staging_id,))
        staging_row=one(cur)
        check("Joe approves the exact rollback-ready staging candidate through its receipt",
              staging_row[:4]==("staging","approved","joe",PLAN_HASH)
              and str(staging_row[4])==staging_approval["approval_receipt_id"]
              and staging_approval["replayed"] is False)
        authority(cur,"carr_authority_joe")
        cur.execute("select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
                    (staging_key,PLAN_HASH,staging_idem,"verifier","staging:proof"))
        check("staging approval exact replay is idempotent",one(cur)[0]["replayed"] is True)
        check("staging approval key refuses a changed verifier",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,staging_idem,"other","staging:proof")))
        check("staging approval key refuses a changed plan",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,"sha256:"+"8"*64,staging_idem,"verifier","staging:proof")))
        check("staging approval key refuses changed expiry",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,11,%s,%s)",
              (staging_key,PLAN_HASH,staging_idem,"verifier","staging:proof")))
        check("staging approval key refuses changed verifier evidence",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,staging_idem,"verifier","other:proof")))
        check("staging approval refuses partial verifier pair",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,uuid.uuid4(),"verifier",None)))
        check("staging approval refuses whitespace verifier",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,uuid.uuid4(),"   ","   ")))
        owner(cur)
        cur.execute("set local role carr_authority")
        check("generic authority SET ROLE is not Joe authority",refuses(cur,
              "select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
              (staging_key,PLAN_HASH,uuid.uuid4(),"verifier","staging:proof")))
        cur.execute("reset role")
        check("routine writer cannot call staging approval",not _has_function(cur,"carr_jobs",
              "ops.approve_staging_release(text,text,uuid,integer,text,text)"))
        cur.execute("select has_table_privilege('carr_authority','ops.staging_release_approval_receipt','insert,update,delete')")
        check("staging approval receipt has no direct runtime DML",one(cur)[0] is False)
        check("staging approved projection rejects direct rewrite",refuses(cur,
              "update ops.release set verifier_actor='other' where id=%s",(staging_id,)))
        check("staging approval receipt is append-only",refuses(cur,
              "update ops.staging_release_approval_receipt set verifier_actor='other' where id=%s",
              (staging_approval["approval_receipt_id"],)))
        check("promoted staging release cannot retarget to Production",refuses(cur,
              "update ops.release set environment='production' where id=%s",(staging_id,)))
        cur.execute("update ops.release set plan_hash=%s where id=%s",("sha256:"+"9"*64,staging_id))
        cur.execute("select state,staging_approval_receipt_id from ops.release where id=%s",(staging_id,))
        check("staging plan revision clears typed approval pointer",one(cur)==("candidate",None))
        revised_plan="sha256:"+"9"*64; revised_idem=uuid.uuid4()
        authority(cur,"carr_authority_joe")
        cur.execute("select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
                    (staging_key,revised_plan,revised_idem,"verifier","revised:proof"))
        revised_approval=one(cur)[0]
        owner(cur)
        cur.execute("select count(*),staging_approval_receipt_id::text from ops.staging_release_approval_receipt a join ops.release r on r.id=a.release_id where r.id=%s group by r.staging_approval_receipt_id",(staging_id,))
        check("revised staging plan reapproves with a new pointer and retains old receipt",
              one(cur)==(2,revised_approval["approval_receipt_id"]))
        # A completed staging release is terminal.  Build that otherwise-valid
        # typed projection under replica mode because this DB gate does not
        # manufacture a real staging deployment completion.
        cur.execute("set local session_replication_role=replica")
        cur.execute("update ops.release set state='complete',ended_at=clock_timestamp() where id=%s",
                    (staging_id,))
        cur.execute("set local session_replication_role=origin")
        cur.execute("select state,plan_hash,staging_approval_receipt_id::text from ops.release where id=%s",(staging_id,))
        complete_before=one(cur)
        check("complete staging release rejects a plan revision",refuses(cur,
              "update ops.release set plan_hash=%s where id=%s",("sha256:"+"a"*64,staging_id)))
        cur.execute("select state,plan_hash,staging_approval_receipt_id::text from ops.release where id=%s",(staging_id,))
        check("complete staging release retains terminal state, plan, and approval pointer",one(cur)==complete_before)
        conn.rollback()

    concurrency_check(dsn)
    print(f"\nstaging-release-readback-gate: {len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("  failed assertions: " + "; ".join(FAILURES))
    return 1 if FAILURES else 0


def _has_function(cur, role: str, signature: str) -> bool:
    cur.execute("select has_function_privilege(%s,%s,'execute')",(role,signature))
    return bool(one(cur)[0])


def concurrency_check(dsn: str) -> None:
    if os.environ.get("CARR_CI_DATABASE_URL") != dsn:
        print("  skip  concurrent replay is restricted to the explicit disposable CI database")
        return
    with psycopg.connect(dsn,autocommit=False) as setup, setup.cursor() as cur:
        ensure_authority_roles(cur)
        fixture=seed_fixture(cur,"race")
        mutation_fixture=seed_fixture(cur,"prepare-mutation")
        setup.commit()

    # This is deliberately a second race seam: staging approval uses Joe's
    # authenticated session rather than the routine-writer session used above.
    approval_fixture: dict
    with psycopg.connect(dsn,autocommit=False) as setup, setup.cursor() as cur:
        approval_fixture=seed_fixture(cur,"staging-approval-race")
        approval_key,approval_release_id=seed_staging_candidate(cur,approval_fixture)
        setup.commit()
    def race(statement: str, params: list[tuple]) -> tuple[list[dict[str,Any]],list[str]]:
        barrier=threading.Barrier(2); results: list[dict[str,Any]]=[]; errors: list[str]=[]
        def worker(call_params: tuple) -> None:
            try:
                with psycopg.connect(dsn,autocommit=False) as conn, conn.cursor() as cur:
                    authority(cur,"carr_jobs")
                    barrier.wait(timeout=10)
                    cur.execute(statement,call_params)
                    results.append(one(cur)[0])
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        threads=[threading.Thread(target=worker,args=(call_params,)) for call_params in params]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=20)
        return results,errors

    attempt=uuid.uuid4(); idem=uuid.uuid4(); version=uuid.uuid4()
    exact_prepare=prepare_params(fixture,attempt,"current_before",idem)
    prepare_results,prepare_errors=race(prepare_sql(),[exact_prepare,exact_prepare])
    check("concurrent exact preparation yields one insert and one replay",
          not prepare_errors and len(prepare_results)==2
          and sorted(x["replayed"] for x in prepare_results)==[False,True],
          "; ".join(prepare_errors))
    with psycopg.connect(dsn,autocommit=False) as conn, conn.cursor() as cur:
        authority(cur,"carr_jobs")
        cur.execute(claim_sql(),(idem,))
        check("prepared concurrent attempt has one provider claim",
              one(cur)[0]["deploy_allowed"] is True)
        conn.commit()
    exact_params=record_params(fixture,attempt,"current_before",idem,version)
    results,errors=race(record_sql(),[exact_params,exact_params])
    check("concurrent exact replay yields one insert and one replay",
          not errors and len(results)==2 and sorted(x["replayed"] for x in results)==[False,True],
          "; ".join(errors))
    with psycopg.connect(dsn,autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from ops.staging_release_readback_receipt where idempotency_key=%s",(idem,))
        check("concurrent exact replay leaves one durable receipt",one(cur)[0]==1)

    prepare_mutation_idem=uuid.uuid4()
    prepare_a=prepare_params(fixture,uuid.uuid4(),"current_before",prepare_mutation_idem)
    prepare_b=prepare_params(mutation_fixture,uuid.uuid4(),"current_before",prepare_mutation_idem)
    prepare_mutation_results,prepare_mutation_errors=race(prepare_sql(),[prepare_a,prepare_b])
    check("concurrent changed preparation has one winner and one refusal",
          len(prepare_mutation_results)==1 and len(prepare_mutation_errors)==1
          and "changed input" in prepare_mutation_errors[0],
          "; ".join(prepare_mutation_errors))

    mutation_attempt=uuid.uuid4(); mutation_idem=uuid.uuid4(); mutation_version=uuid.uuid4()
    mutation_a=record_params(fixture,mutation_attempt,"current_before",mutation_idem,mutation_version)
    mutation_b=record_params(fixture,mutation_attempt,"current_before",mutation_idem,mutation_version,verb_count=212)
    with psycopg.connect(dsn,autocommit=False) as conn, conn.cursor() as cur:
        authority(cur,"carr_jobs")
        prepare_and_claim(cur,fixture,mutation_attempt,"current_before",mutation_idem)
        conn.commit()
    mutation_results,mutation_errors=race(record_sql(),[mutation_a,mutation_b])
    check("concurrent changed-input replay has one winner and one refusal",
          len(mutation_results)==1 and len(mutation_errors)==1 and "changed input" in mutation_errors[0],
          "; ".join(mutation_errors))
    with psycopg.connect(dsn,autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from ops.staging_release_readback_receipt where idempotency_key=%s",
                    (mutation_idem,))
        check("concurrent changed-input replay leaves one durable receipt",one(cur)[0]==1)

    approval_idem=uuid.uuid4()
    approval_barrier=threading.Barrier(2); approval_results: list[dict[str,Any]]=[]; approval_errors: list[str]=[]
    def approve_worker() -> None:
        try:
            with psycopg.connect(dsn,autocommit=False) as conn, conn.cursor() as cur:
                authority(cur,"carr_authority_joe")
                approval_barrier.wait(timeout=10)
                cur.execute("select ops.approve_staging_release(%s,%s,%s::uuid,12,%s,%s)",
                            (approval_key,PLAN_HASH,approval_idem,"verifier","race:proof"))
                approval_results.append(one(cur)[0]); conn.commit()
        except Exception as exc:  # noqa: BLE001
            approval_errors.append(str(exc))
    threads=[threading.Thread(target=approve_worker) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    check("concurrent same-key staging approval yields one receipt and one replay",
          not approval_errors and len(approval_results)==2
          and sorted(x["replayed"] for x in approval_results)==[False,True],"; ".join(approval_errors))
    with psycopg.connect(dsn,autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""select count(*), exists(select 1 from ops.release r
                         join ops.staging_release_approval_receipt a on a.id=r.staging_approval_receipt_id
                         where r.id=%s and a.release_id=r.id)
                       from ops.staging_release_approval_receipt a where a.release_id=%s""",
                    (approval_release_id,approval_release_id))
        check("concurrent staging approval leaves one receipt bound by the release pointer",one(cur)==(1,True))

        # This is the exact non-Production require predicate.  A historical row
        # marked approved before 0219 has no typed pointer and must not unlock a
        # new staging deployment.
    with psycopg.connect(dsn,autocommit=False) as conn, conn.cursor() as cur:
        legacy_fixture=seed_fixture(cur,"legacy-staging")
        legacy_key,legacy_id=seed_staging_candidate(cur,legacy_fixture)
        cur.execute("set local session_replication_role=replica")
        cur.execute("""update ops.release set state='approved',approved_by_actor='joe',
          approved_at=now(),approval_expires_at=now()+interval '1 hour',
          verifier_actor='legacy-verifier',verifier_evidence_ref='legacy:proof',
          staging_approval_receipt_id=null where id=%s""",(legacy_id,))
        cur.execute("set local session_replication_role=origin")
        cur.execute("select state,staging_approval_receipt_id from ops.release where id=%s",(legacy_id,))
        check("synthetic legacy staging fixture is approved with a null pointer",one(cur)==("approved",None))
        cur.execute("""select count(*) from ops.release r where r.release_key=%s and r.environment='staging'
          and r.state in ('approved','deploying','verifying') and r.approval_expires_at>now()
          and exists(select 1 from ops.staging_release_approval_receipt a
            where a.id=r.staging_approval_receipt_id and a.release_id=r.id and a.plan_hash=r.plan_hash
              and a.approved_by_actor='joe' and a.approved_at=r.approved_at
              and a.approval_expires_at=r.approval_expires_at)""",(legacy_key,))
        check("legacy approved staging row without pointer cannot satisfy require predicate",one(cur)[0]==0)
        conn.rollback()


if __name__ == "__main__":
    raise SystemExit(main())
