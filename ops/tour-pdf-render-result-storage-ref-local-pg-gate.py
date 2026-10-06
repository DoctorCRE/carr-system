#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only PostgreSQL acceptance for ops.record_tour_pdf_render_result's
storage_ref bound (migration 0583).

WHY THIS EXISTS. Render job b87f59da-a915-4d5d-97fd-ad4171db0938 (2026-09-24)
failed every hosted Tour PDF render after PR #1220 restored the database
connection. Migration 0430 validated storage_ref against
'^tour-pdf/[A-Za-z0-9._/-]{16,400}\\.pdf$' -- both in
ops.record_tour_pdf_render_result's own guard and in the table's CHECK
constraint. PostgreSQL's regex engine caps a repetition count at 255
(RE_DUP_MAX); {16,400} exceeds it, so the pattern itself fails to compile and
PostgreSQL raises SQLSTATE 2201B (invalid_regular_expression) for ANY real
(non-null) storage_ref, independent of the value's actual length. A NULL
storage_ref (the "failed" status path) never reaches the regex and kept
working, which is why failure receipts wrote successfully while every real
render result raised. This gate calls the function exactly as production's
runTourPdfRender (mcp-server/src/tour-runtime.js) does and fails loudly if the
{16,400} defect ever returns.
"""

from __future__ import annotations

import os
import sys
import uuid

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

import psycopg
from psycopg.types.json import Jsonb


def fail(message: str) -> int:
    print(f"tour-pdf-render-result-storage-ref-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"fixture row was not returned: {query[:100]}")
    return row


def digest(byte: str) -> str:
    return "sha256:" + byte * 64


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    tenant = f"tour-pdf-storage-ref-gate-{uuid.uuid4().hex[:8]}"
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            actor_id = one(cur, """insert into actor(slug,kind,display_name)
              values (%s,'automation','Tour PDF storage_ref gate fixture')
              returning id""", (f"tour-pdf-gate-{uuid.uuid4().hex[:8]}",))[0]

            # This gate exercises only ops.record_tour_pdf_render_result's own
            # storage_ref guard, not the whole tour/projection/seal lifecycle
            # that would ordinarily produce a job row. Dropping the job's FK to
            # a real projection keeps the fixture to exactly what the function
            # under test reads: the job row it locks `for share`, and the
            # values the caller passes. The surrounding rollback restores the
            # constraint along with every other fixture change.
            cur.execute(
                "alter table ops.tour_pdf_render_job "
                "drop constraint tour_pdf_render_job_organization_tenant_id_projection_id_fkey")

            job_id = uuid.uuid4()
            second_job_id = uuid.uuid4()
            for one_job_id in (job_id, second_job_id):
                cur.execute("""insert into ops.tour_pdf_render_job
                  (id,organization_tenant_id,projection_id,requested_by_actor_id,request,
                   projection_digest,packet_digest,template_digest,renderer_digest,qc_ruleset_digest,
                   expected_property_count)
                  values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (one_job_id, tenant, uuid.uuid4(), f"actor:{actor_id}", Jsonb({"fixture": True}),
                   digest("1"), digest("2"), digest("3"), digest("4"), digest("5"), 1))

            grant_settable_runtime_roles(cur, "carr_authority")
            set_local_role(cur, "carr_authority")

            # A real storage_ref, exactly the shape
            # mcp-server/src/tour-pdf-service.js builds:
            # tour-pdf/<tenant>/<uuid>/<sha256-hex>.pdf. Pre-migration-0583 this
            # raised SQLSTATE 2201B from the regex engine itself, before the
            # function's own validation ever ran -- not the "tour PDF render
            # result is invalid" exception the function raises for a genuinely
            # invalid value.
            storage_ref = f"tour-pdf/{tenant}/{job_id}/{'7' * 64}.pdf"
            result_id = one(cur, """select ops.record_tour_pdf_render_result(
                %s,%s,'review_ready',%s,%s,%s,12345,1,0,%s,%s)""",
              (tenant, job_id, f"artifact:tour-pdf:{job_id.hex}", digest("7"), storage_ref,
               digest("8"), f"actor:{actor_id}"))[0]
            if not result_id:
                raise RuntimeError("record_tour_pdf_render_result returned no id for a valid storage_ref")

            # A storage_ref past the new 255 ceiling must be REJECTED as an
            # ordinary invalid value (the function's own raise), never as a
            # regex-engine crash -- proves 255 is a real, enforced boundary and
            # not just a number that happens to compile.
            oversize_ref = f"tour-pdf/{tenant}/{second_job_id}/{'a' * 400}.pdf"
            rejected_as_invalid_value = False
            cur.execute("savepoint before_oversize_call")
            try:
                cur.execute("""select ops.record_tour_pdf_render_result(
                    %s,%s,'review_ready',%s,%s,%s,12345,1,0,%s,%s)""",
                  (tenant, second_job_id, f"artifact:tour-pdf:{second_job_id.hex}", digest("7"), oversize_ref,
                   digest("8"), f"actor:{actor_id}"))
            except psycopg.errors.RaiseException:
                # The function's own "tour PDF render result is invalid" raise
                # (SQLSTATE P0001) -- the expected, ordinary rejection. Roll
                # back to the savepoint so the aborted statement does not
                # abort the whole rollback-only transaction.
                cur.execute("rollback to savepoint before_oversize_call")
                rejected_as_invalid_value = True
            except Exception as exc:
                # Any other class -- especially SQLSTATE 2201B -- means the
                # regex engine crashed again rather than validating.
                cur.execute("rollback to savepoint before_oversize_call")
                raise RuntimeError(f"over-length storage_ref was not cleanly rejected: {exc!r}") from exc
            if not rejected_as_invalid_value:
                raise RuntimeError("an over-length storage_ref was accepted")

            cur.execute("reset role")
            stored = one(cur, "select storage_ref from ops.tour_pdf_render_result where id=%s", (result_id,))[0]
            if stored != storage_ref:
                raise RuntimeError(f"stored storage_ref drifted: {stored!r} != {storage_ref!r}")
        print("PASS: ops.record_tour_pdf_render_result accepts a realistic storage_ref and "
              "rejects an over-255 one without a regex engine crash")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
