#!/usr/bin/env python3
# ci: runs-outside-ci — invoked by ops/local-pg-ci.py after assurance persistence
# doctrine: runbook
"""Rollback-only least-privilege proof for source-merge authority projection."""

from __future__ import annotations

import json
import os
import sys
import uuid

from gate_runtime_role import (
    grant_settable_runtime_roles,
    rollback_only_connection,
    set_local_role,
)


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    if not dsn:
        print("source-merge authority gate requires CARR_LOCAL_PG_DSN", file=sys.stderr)
        return 78
    with rollback_only_connection(dsn) as conn:
        with conn.cursor() as cur:
            row = cur.execute("""select
              has_function_privilege('carr_reader',
                'ops.source_merge_authority_projection(uuid,text,text,integer)','execute'),
              has_table_privilege('carr_reader','ops.source_merge_plan_scope','select'),
              has_table_privilege('carr_reader','ops.assurance_execution_manifest','select'),
              has_table_privilege('carr_reader','ops.assurance_evidence_extension','select'),
              has_table_privilege('carr_reader','ops.assurance_review_extension','select'),
              has_table_privilege('carr_reader','ops.assurance_owner_acceptance_fact','select'),
              has_table_privilege('carr_reader','ops.canonical_ownership_lease','select'),
              p.prosecdef,p.provolatile,p.proconfig,
              has_table_privilege(p.proowner,'ops.assurance_evidence_extension','select'),
              has_table_privilege(p.proowner,'ops.canonical_ownership_claim','select'),
              pg_get_functiondef(p.oid),
              pg_get_functiondef('ops.source_merge_authority_projection_v1(uuid,text,text,integer)'::regprocedure),
              has_function_privilege('carr_reader',
                'ops.source_merge_authority_projection_v1(uuid,text,text,integer)','execute')
              from pg_proc p
              where p.oid='ops.source_merge_authority_projection(uuid,text,text,integer)'::regprocedure""").fetchone()
            if (row is None or row[0] is not True or any(value is not False for value in row[1:7])
                    or row[7] is not True or row[8] != "v"):
                raise RuntimeError(f"source-merge projection privilege posture invalid: {row!r}")
            if row[9] != ["search_path=pg_catalog, ops, public"]:
                raise RuntimeError(f"source-merge projection search_path invalid: {row[9]!r}")
            if row[10] is not True or row[11] is not True:
                raise RuntimeError(f"source-merge projection owner cannot read protected evidence: {row!r}")
            if ("ops.source_merge_authority_projection_v1" not in row[12]
                    or "ops.engineering_contract_stale" not in row[12]):
                raise RuntimeError("source-merge wrapper omitted the stale-plan fence/delegation")
            if ("ops.assurance_evidence_extension" not in row[13]
                    or "ops.canonical_ownership_claim" not in row[13]
                    or "x.result_version=w.version" not in row[13]
                    or "ops.canonical_ownership_merge_binding_valid" not in row[13]
                    or "Gate A2 source-merge authority: " not in row[13]
                    or "lease.state in ('active','released')" not in row[13]):
                raise RuntimeError("source-merge projection omitted protected evidence projection")
            if row[14] is not False:
                raise RuntimeError("private source-merge predecessor remained executable by reader")
            stale = cur.execute(
                "select ops.engineering_contract_stale(%s,%s,null)",
                ("PLAN-954b03dd464d-v1",
                 "sha256:954b03dd464dab9986bc705d01fdb197edd29b23ad10ca673da53ad0ac03c27d"),
            ).fetchone()
            if stale != (True,):
                raise RuntimeError(f"exact stale-plan fence is not durable: {stale!r}")
            grant_settable_runtime_roles(cur, "carr_reader")
            set_local_role(cur, "carr_reader")
            result = cur.execute(
                "select ops.source_merge_authority_projection(%s::uuid,%s,%s,%s)",
                (uuid.uuid4(), None, "0" * 40, 1),
            ).fetchone()
            if result is None or not isinstance(result[0], dict) or result[0].get("ok") is not False:
                raise RuntimeError(f"carr_reader did not receive typed fail-closed projection: {result!r}")
    print(json.dumps({
        "contract": "source-merge-authority-local-pg.v1",
        "reader_execute": True,
        "raw_table_select": False,
        "security_definer_protected_read": True,
        "server_side_candidate_discovery": True,
        "typed_refusal": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
