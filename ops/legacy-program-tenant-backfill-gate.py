#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Prove the one-time legacy capability-program tenant repair is exact.

The migration class runs this gate only after applying pending migrations to a
fresh loopback ``carr_ci`` database.  The gate creates one closed synthetic
legacy-program row inside a savepoint so it can exercise the trigger's narrow
NULL-to-``carr-internal`` exception, then rolls the schema and row back.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "0314_doctorcre_home_legacy_program_tenant.sql"
PROGRAM = "carr-ai-engineering-suite-v1"


def refuse(statement: str, cur, sql: str, params: tuple[object, ...] = ()) -> None:
    cur.execute("savepoint refused_change")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint refused_change")
        return
    cur.execute("rollback to savepoint refused_change")
    raise RuntimeError(statement)


def require_disposable(dsn: str) -> None:
    info = conninfo_to_dict(dsn)
    if info.get("host") not in {"localhost", "127.0.0.1"} or info.get("dbname") != "carr_ci":
        raise RuntimeError("legacy tenant backfill gate refuses every database except loopback carr_ci")


def main() -> int:
    if not MIGRATION.exists():
        raise SystemExit(f"legacy tenant backfill gate: missing {MIGRATION.name}")
    sql = MIGRATION.read_text(encoding="utf-8")
    required = (
        "carr.legacy_program_tenant_backfill",
        "to_jsonb(new) - 'organization_tenant_id'",
        "to_jsonb(old) - 'organization_tenant_id'",
        "legacy_program_tenant_backfill_before",
        "tenant repair changed immutable program evidence",
        "validate constraint work_request_sourced_capture_shape",
        "set local carr.legacy_program_tenant_backfill = 'on'",
        "set local carr.legacy_program_tenant_backfill = 'off'",
    )
    missing = [token for token in required if token not in sql.lower()]
    if missing:
        raise RuntimeError(f"legacy tenant backfill migration is missing exact guards: {missing}")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("legacy tenant backfill gate: DATABASE_URL is not set")
    require_disposable(dsn)

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(
            """select count(*),
                      count(*) filter (where organization_tenant_id='carr-internal'),
                      count(*) filter (where requester_actor='joe' and owner_actor='joe'
                        and program_ordinal > 0 and capture_idempotency_key is null
                        and doctrine_section_id is null and doctrine_revision_id is null
                        and sourced_capture_sequence is null and triage_classification is null
                        and triaged_by_actor_id is null and triaged_at is null)
                 from ops.work_request where program_key=%s""",
            (PROGRAM,),
        )
        summary = cur.fetchone()
        if summary is None:
            raise RuntimeError("legacy program repair summary returned no row")
        total, tenant_bound, exact_shape = summary
        if (tenant_bound, exact_shape) != (total, total):
            raise RuntimeError(
                f"legacy program repair is incomplete or widened: total={total}, "
                f"tenant_bound={tenant_bound}, exact_shape={exact_shape}"
            )

        cur.execute(
            """select convalidated from pg_constraint
                 where conrelid='ops.work_request'::regclass
                   and conname='work_request_sourced_capture_shape'"""
        )
        if cur.fetchone() != (True,):
            raise RuntimeError("work_request_sourced_capture_shape is absent or unvalidated")

        cur.execute("select pg_get_functiondef('ops.capability_program_closed_immutable()'::regprocedure)")
        function_row = cur.fetchone()
        if function_row is None:
            raise RuntimeError("closed-row immutability function is absent")
        function_sql = function_row[0].lower()
        for token in required[:3]:
            if token not in function_sql:
                raise RuntimeError(f"closed-row function lost guard {token!r}")

        # A normal historical row remains legal and tenant-null.
        cur.execute("savepoint ordinary_historical")
        cur.execute(
            """insert into ops.work_request(ref,state,title,requester_actor,owner_actor)
                 values (%s,'captured','ordinary historical fixture','joe','joe')
                 returning organization_tenant_id,program_key,program_ordinal""",
            (f"WR-LEGACY-{uuid.uuid4().hex[:16]}",),
        )
        if cur.fetchone() != (None, None, None):
            raise RuntimeError("ordinary historical fixture was silently tenant-bound")
        cur.execute("rollback to savepoint ordinary_historical")

        # A forged or differently-owned program row cannot enter the repaired branch.
        refuse(
            "wrong-actor exact-program row passed the shape constraint",
            cur,
            """insert into ops.work_request
                 (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id)
                 values (%s,'captured','forged program fixture','system','joe',%s,32000,
                         'carr-internal')""",
            (f"WR-FORGED-{uuid.uuid4().hex[:16]}", PROGRAM),
        )
        refuse(
            "wrong-tenant exact-program row passed the shape constraint",
            cur,
            """insert into ops.work_request
                 (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id)
                 values (%s,'captured','wrong tenant fixture','joe','joe',%s,32000,
                         'different-tenant')""",
            (f"WR-WRONG-TENANT-{uuid.uuid4().hex[:12]}", PROGRAM),
        )
        refuse(
            "NULL-tenant exact-program row passed the sourced-capture shape constraint",
            cur,
            """insert into ops.work_request
                   (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id)
                 values (%s,'captured','missing tenant fixture','joe','joe',%s,32000,
                         null)""",
            (f"WR-MISSING-TENANT-{uuid.uuid4().hex[:12]}", PROGRAM),
        )
        refuse(
            "wrong-program tenant-bound row passed the shape constraint",
            cur,
            """insert into ops.work_request
                 (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id)
                 values (%s,'captured','wrong program fixture','joe','joe',%s,32000,
                         'carr-internal')""",
            (f"WR-WRONG-PROGRAM-{uuid.uuid4().hex[:12]}", "other-program-v1"),
        )
        refuse(
            "NULL-program tenant-bound row passed the sourced-capture shape constraint",
            cur,
            """insert into ops.work_request
                   (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id)
                 values (%s,'captured','missing program fixture','joe','joe',null,null,
                         'carr-internal')""",
            (f"WR-MISSING-PROGRAM-{uuid.uuid4().hex[:12]}",),
        )
        # Remove the older travel-together guard inside a savepoint so this
        # probe proves the new provenance constraint itself is two-valued.
        cur.execute("savepoint missing_ordinal_shape")
        cur.execute(
            """alter table ops.work_request
                 drop constraint work_request_program_fields_travel_together"""
        )
        refuse(
            "missing-ordinal exact-program row passed the sourced-capture shape constraint",
            cur,
            """insert into ops.work_request
                   (ref,state,title,requester_actor,owner_actor,program_key,
                    organization_tenant_id)
                 values (%s,'captured','missing ordinal fixture','joe','joe',%s,
                         'carr-internal')""",
            (f"WR-MISSING-ORDINAL-{uuid.uuid4().hex[:10]}", PROGRAM),
        )
        cur.execute("rollback to savepoint missing_ordinal_shape")
        refuse(
            "NULL-owner exact-program row passed the sourced-capture shape constraint",
            cur,
            """insert into ops.work_request
                   (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id)
                 values (%s,'captured','missing owner fixture','joe',null,%s,32000,
                         'carr-internal')""",
            (f"WR-MISSING-OWNER-{uuid.uuid4().hex[:12]}", PROGRAM),
        )
        refuse(
            "sourced-field forged exact-program row passed the shape constraint",
            cur,
            """insert into ops.work_request
                 (ref,state,title,requester_actor,owner_actor,program_key,program_ordinal,
                    organization_tenant_id,capture_idempotency_key)
                 values (%s,'captured','forged sourced fixture','joe','joe',%s,32000,
                         'carr-internal',%s)""",
            (f"WR-FORGED-SOURCED-{uuid.uuid4().hex[:10]}", PROGRAM, uuid.uuid4()),
        )

        # Exercise the generic row trigger against a disposable composite with
        # the exact columns it reads. This avoids disabling any real table gate
        # merely to manufacture the pre-migration NULL shape.
        cur.execute(
            """create temporary table legacy_closed_trigger_fixture (
                   id uuid primary key,
                   program_key text,
                   state text,
                   organization_tenant_id text,
                   title text
                 ) on commit drop"""
        )
        cur.execute(
            """create trigger legacy_closed_trigger_fixture_immutable
                 before update on legacy_closed_trigger_fixture
                 for each row execute function ops.capability_program_closed_immutable()"""
        )
        fixture_id = uuid.uuid4()
        cur.execute(
            """insert into legacy_closed_trigger_fixture
                   (id,program_key,state,organization_tenant_id,title)
                 values (%s,%s,'confirmed_closed',null,'closed legacy fixture')""",
            (fixture_id, PROGRAM),
        )

        refuse(
            "closed exact-program tenant repair succeeded without the local GUC",
            cur,
            """update legacy_closed_trigger_fixture
                   set organization_tenant_id='carr-internal' where id=%s""",
            (fixture_id,),
        )
        cur.execute("set local carr.legacy_program_tenant_backfill = 'on'")
        refuse(
            "GUC allowed a tenant repair plus a second field change",
            cur,
            """update legacy_closed_trigger_fixture
                   set organization_tenant_id='carr-internal', title=title || ' forged'
                 where id=%s""",
            (fixture_id,),
        )
        cur.execute(
            """update legacy_closed_trigger_fixture
                   set organization_tenant_id='carr-internal' where id=%s""",
            (fixture_id,),
        )
        cur.execute("set local carr.legacy_program_tenant_backfill = 'off'")
        cur.execute(
            """select organization_tenant_id,title
                 from legacy_closed_trigger_fixture where id=%s""",
            (fixture_id,),
        )
        if cur.fetchone() != ("carr-internal", "closed legacy fixture"):
            raise RuntimeError("exact tenant-only closed-row repair changed another field")

        # A rerun's data statement is a no-op after every exact row is repaired.
        cur.execute("set local carr.legacy_program_tenant_backfill = 'on'")
        cur.execute(
            """update ops.work_request set organization_tenant_id='carr-internal'
                 where program_key=%s and organization_tenant_id is null""",
            (PROGRAM,),
        )
        if cur.rowcount != 0:
            raise RuntimeError(f"idempotent tenant repair unexpectedly changed {cur.rowcount} rows")
        cur.execute("set local carr.legacy_program_tenant_backfill = 'off'")
        conn.rollback()

    print("legacy program tenant backfill gate: PASS — exact, fail-closed, idempotent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
