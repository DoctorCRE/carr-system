#!/usr/bin/env python3
# ci: runs-outside-ci — needs a database still at the pre-0854 ledger (origin/main's db/schema.sql plus 0853 applied); the branch snapshot CI loads has already absorbed 0854-0857, so CI has no pending batch to rehearse
# doctrine: runbook
"""Two-connection lock-contention rehearsal for the 0854/0855/0856/0857 batch.

The definer hardening applies as ONE transaction (tools/migrate.py
ATOMIC_MIGRATION_GROUPS): 0854 rewrites function metadata, 0855 adds view
barriers, 0856 qualifies definer dependencies, 0857 drops and recreates registry constraints and triggers and seals
v116. 0857's ALTER TABLE and DROP TRIGGER take ACCESS EXCLUSIVE locks on the
registry tables, and every lock is held until the whole batch commits.

This rehearsal answers the four production-apply questions on a disposable
loopback database that still has the batch pending:

  1. BOUNDED FAILURE. A second connection holds an ordinary read of
     ops.scac_mutation_registry_version open (the Worker's normal shape). The
     real runner (tools/migrate.py, same lock_timeout/statement_timeout as
     production) must give up within its lock timeout, not queue forever.
  2. FULL ROLLBACK. After that refusal the catalog fingerprint -- every
     public/ops routine's search_path and ACL, every view's options, the
     registry tables' constraints and triggers, the registry version rows and
     the migration ledger -- must equal the fingerprint taken before.
  3. SAFE RETRY. With the blocker gone the same command must apply the batch,
     leave v116 live and its catalog seal current.
  4. APPLY WINDOW. The successful batch is timed, and a concurrent reader of
     the registry measures the longest it was made to wait.

Usage (loopback only):
  CARR_LOCAL_PG_DSN=postgres://carr_ci@127.0.0.1:PORT/carr_ci \\
    .venv/bin/python ops/definer-hardening-lock-rehearsal-gate.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

REPO = Path(__file__).resolve().parents[1]
BATCH = (
    "0854_dot_security_definer_hardening.sql",
    "0855_completion_tenant_security_barriers.sql",
    "0856_qualify_security_definer_dependencies.sql",
    "0857_dot_hardening_scac_successor.sql",
)
PREDECESSOR = '0853_party_identity_scac_successor.sql'
FINGERPRINT = """
select md5(string_agg(line, E'\\n' order by line)) from (
  select 'fn:'||p.oid::regprocedure::text||':'||coalesce(p.proconfig::text,'')||':'||coalesce(p.proacl::text,'')
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','ops')
  union all
  select 'rel:'||c.oid::regclass::text||':'||coalesce(c.reloptions::text,'')||':'||coalesce(c.relacl::text,'')
    from pg_class c join pg_namespace n on n.oid=c.relnamespace
   where n.nspname in ('public','ops') and c.relkind in ('r','v','m')
  union all
  select 'con:'||conrelid::regclass::text||':'||conname||':'||pg_get_constraintdef(oid)
    from pg_constraint where conrelid in ('ops.scac_mutation_registry_version'::regclass,
      'ops.scac_mutation_registry_entry'::regclass,'ops.scac_policy_epoch'::regclass)
  union all
  select 'trg:'||tgrelid::regclass::text||':'||tgname||':'||tgenabled::text
    from pg_trigger where tgrelid in ('ops.scac_mutation_registry_version'::regclass,
      'ops.scac_mutation_registry_entry'::regclass) and not tgisinternal
  union all
  select 'reg:'||registry_version||':'||registry_digest from ops.scac_mutation_registry_version
  union all
  select 'mig:'||filename||':'||sha256 from public.schema_migrations
) catalog(line)
"""


def fail(message: str) -> int:
    print(f"definer-hardening-lock-rehearsal-gate: FAIL — {message}", file=sys.stderr)
    return 1


def fingerprint(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute(FINGERPRINT).fetchall()[0][0]


def run_batch(dsn: str) -> tuple[int, float, str]:
    env = dict(os.environ, DATABASE_URL=dsn)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(REPO / "tools/migrate.py"), "--apply", "--yes", "--through", BATCH[-1]],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=600,
    )
    return result.returncode, time.monotonic() - started, result.stdout + result.stderr


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("CARR_LOCAL_PG_DSN is required (a disposable loopback database at the pre-0854 ledger)")
    info = conninfo_to_dict(dsn)
    if info.get("host") not in ("127.0.0.1", "localhost") or "password" in info:
        return fail("refusing a non-loopback or credentialed DSN; this rehearsal applies migrations")

    with psycopg.connect(dsn) as conn:
        applied = {row[0] for row in conn.execute("select filename from public.schema_migrations")}
        if PREDECESSOR not in applied or applied & set(BATCH):
            return fail(f"database must have {PREDECESSOR} applied and the hardening batch pending")
        # A snapshot-built database has no rules and no policy epochs, so the
        # deferred epoch refresh never runs at commit and 0857's re-validated
        # epoch constraint scans nothing. Production has both. Seed, in this
        # disposable database only, the same one-rule coherent projection
        # mcp-server/test/definer-hardening-catalog-postgres.sql uses, plus a
        # synthetic valid epoch chain, so the batch commits through the real
        # refresh path and validates a production-scale epoch table.
        if conn.execute("select count(*) from ops.scac_policy_epoch").fetchall()[0][0] == 0:
            if conn.execute("select count(*) from public.rule").fetchall()[0][0] == 0:
                conn.execute(
                    """insert into public.actor(id,slug,kind,display_name) values
                         ('31000000-0000-4000-8000-000000000001','rehearsal-fixture','human','Rehearsal fixture')""")
                conn.execute("alter table public.rule disable trigger user")
                conn.execute(
                    """insert into public.rule(id,statement,taught_by,status,activated_by) values
                         ('31000000-0000-4000-8000-000000000002','Synthetic rehearsal rule',
                          '31000000-0000-4000-8000-000000000001','active','31000000-0000-4000-8000-000000000001')""")
                conn.execute("alter table public.rule enable trigger user")
                conn.execute(
                    """insert into ops.rule_pack(pack,title,description,triggers,source) values
                         ('rehearsal-fixture','Rehearsal fixture','Synthetic fixture',array['rehearsal'],
                          'ops/config/rule-enforcement-map.json')""")
                conn.execute(
                    """insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest) values
                         ('31000000-0000-4000-8000-000000000002','31000000','pack',array['rehearsal-fixture'],
                          'shared','Synthetic fixture','ops/config/rule-enforcement-map.json',repeat('0',64))""")
            # Committing the projection lets the real refresh trigger mint a
            # coherent epoch 1; the synthetic chain then repeats epoch 1's
            # content under new epoch numbers, so the next refresh sees an
            # unchanged source and the chain stays valid.
            conn.commit()
            if conn.execute("select count(*) from ops.scac_policy_epoch").fetchall()[0][0] != 1:
                return fail("the fixture projection did not bootstrap exactly one policy epoch")
            conn.execute(
                """insert into ops.scac_policy_epoch(epoch,epoch_digest,previous_epoch,previous_epoch_digest,
                     program_key,tenant_scope,policy_domain,registry_version,registry_digest,
                     doctrine_generation,doctrine_projection_digest,rule_projection_digest,
                     schema_applied_count,schema_highest_migration,schema_ledger_digest,
                     source_digest,source_session_user,source_relation)
                   select e,case when e=1 then f.epoch_digest else 'sha256:'||encode(sha256(('rehearsal-epoch-'||e)::bytea),'hex') end,
                          e-1,case when e=2 then f.epoch_digest else 'sha256:'||encode(sha256(('rehearsal-epoch-'||(e-1))::bytea),'hex') end,
                          f.program_key,f.tenant_scope,f.policy_domain,f.registry_version,f.registry_digest,
                          f.doctrine_generation,f.doctrine_projection_digest,f.rule_projection_digest,
                          f.schema_applied_count,f.schema_highest_migration,f.schema_ledger_digest,
                          f.source_digest,f.source_session_user,f.source_relation
                     from generate_series(2,%s) e cross join ops.scac_policy_epoch f where f.epoch=1""",
                (int(os.environ.get("CARR_REHEARSAL_EPOCHS", "5000")),),
            )
            conn.commit()
        sizes = conn.execute(
            """select (select count(*) from ops.scac_mutation_registry_version),
                      (select count(*) from ops.scac_mutation_registry_entry),
                      (select count(*) from ops.scac_policy_epoch),
                      (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                        where p.prosecdef and n.nspname in ('public','ops'))"""
        ).fetchall()[0]
    print(f"rehearsal scale: {sizes[0]} registry versions, {sizes[1]} registry entries, "
          f"{sizes[2]} policy epochs, {sizes[3]} SECURITY DEFINER routines")

    before = fingerprint(dsn)

    # 1 + 2: a held registry read must make the batch fail fast and leave nothing behind.
    blocker = psycopg.connect(dsn)
    blocker.execute("select count(*) from ops.scac_mutation_registry_version").fetchone()
    code, blocked_seconds, output = run_batch(dsn)
    blocker_alive = blocker.execute("select count(*) from ops.scac_mutation_registry_entry").fetchall()[0][0] == sizes[1]
    blocker.rollback()
    blocker.close()
    if code == 0:
        return fail("the batch applied while a registry read held its lock")
    if "could not acquire its lock" not in output or "ABANDONED" not in output:
        return fail(f"the batch failed for a reason other than the lock timeout:\n{output[-1500:]}")
    if not blocker_alive:
        return fail("the blocked migration disturbed the concurrent reader's transaction")
    after_refusal = fingerprint(dsn)
    if after_refusal != before:
        return fail("the refused batch left catalog state behind (fingerprint changed)")
    print(f"bounded failure: refused after {blocked_seconds:.1f}s under a held registry read; "
          f"catalog fingerprint unchanged ({before}); concurrent reader unharmed")

    # 3 + 4: retry with a concurrent reader probing the registry throughout.
    waits: list[float] = []
    stop = threading.Event()

    def probe() -> None:
        with psycopg.connect(dsn, autocommit=True) as reader:
            while not stop.is_set():
                started = time.monotonic()
                reader.execute("select count(*) from ops.scac_mutation_registry_version").fetchone()
                waits.append(time.monotonic() - started)
                time.sleep(0.05)

    reader = threading.Thread(target=probe, daemon=True)
    reader.start()
    code, applied_seconds, output = run_batch(dsn)
    stop.set()
    reader.join(timeout=30)
    if code != 0:
        return fail(f"retry without a blocker did not apply the batch:\n{output[-1500:]}")
    with psycopg.connect(dsn) as conn:
        ledger = {row[0] for row in conn.execute(
            "select filename from public.schema_migrations where filename = any(%s)", (list(BATCH),))}
        live = conn.execute(
            """select registry_version from ops.scac_mutation_registry_version
                order by regexp_replace(registry_version,'^.*[.]v','','')::integer desc limit 1"""
        ).fetchall()[0][0]
        current = conn.execute("select ops.scac_mutation_catalog_v116_current()").fetchall()[0][0]
        unpinned = conn.execute(
            """select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                where p.prosecdef and n.nspname in ('public','ops')
                  and not exists (select 1 from unnest(p.proconfig) s
                                   where s like 'search_path=%' and s like '%pg_temp')"""
        ).fetchall()[0][0]
    if ledger != set(BATCH) or live != "scac-mutation-registry.v116" or current is not True or unpinned:
        return fail(f"retry left an unexpected state: ledger={sorted(ledger)} live={live} "
                    f"catalog_current={current} unpinned_definers={unpinned}")
    if fingerprint(dsn) == before:
        return fail("retry reported success but the catalog fingerprint did not change")
    longest = max(waits) if waits else 0.0
    print(f"safe retry: batch applied in {applied_seconds:.1f}s end to end (runner start to exit); "
          f"v116 live, catalog seal current, 0 definers without pg_temp")
    print(f"apply window: {len(waits)} concurrent registry reads, longest wait {longest:.2f}s")
    print("db-gate-proof: definer-hardening lock rehearsal refused under contention, rolled back "
          "completely, and applied on retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
