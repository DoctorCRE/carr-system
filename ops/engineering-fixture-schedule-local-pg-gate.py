#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Exercise the claim fixture's actual job INSERT with colliding UUID prefixes."""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import sys

from gate_runtime_role import rollback_only_connection


def assert_unique_schedules(cur):
    source = Path(__file__).with_name("engineering-claim-local-pg-gate.py")
    spec = importlib.util.spec_from_file_location("claim_fixture_schedule", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = next(node for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.FunctionDef) and node.name == "fixture")
    insert = next(node for node in fixture.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "job_id"
                          for target in node.targets))
    code = compile(ast.Module(body=[insert], type_ignores=[]), str(source), "exec")
    cur.execute("""create temporary table schedule_fixture_job (
        id bigint generated always as identity, definition_key text,
        definition_version integer, idempotency_key text, scheduled_for timestamptz,
        max_attempts integer, timeout_seconds integer, mode text, payload jsonb,
        unique (definition_key, definition_version, scheduled_for, mode))""")

    def insert_fixture(cursor, query, params):
        # Redirect only the shipped fixture INSERT into the minimal constraint
        # reproduction. No canonical job row or runtime policy is modified.
        assert "insert into ops.job" in query
        return cursor.execute(query.replace("insert into ops.job", "insert into schedule_fixture_job"), params).fetchone()

    namespace = dict(vars(module), cur=cur, one=insert_fixture,
                     slice_ref="schedule-regression", plan_digest="sha256:" + "a" * 64)
    # Both old modulo offsets equal zero; all other token bits are distinct.
    tokens = ["00000000" + "0" * 23 + "1", "000f4240" + "0" * 23 + "2"]
    tokens += ["00000000" + f"{n:024x}" for n in range(3, 131)]
    for token in tokens:
        namespace["token"] = token
        exec(code, namespace)
    count, distinct, due = cur.execute("""select count(*), count(distinct scheduled_for),
        bool_and(scheduled_for < now() and scheduled_for > now()-interval '11 minutes')
        from schedule_fixture_job""").fetchone()
    assert count == distinct == len(tokens) and due


def main():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("CARR_LOCAL_PG_DSN")
    if not dsn:
        print("engineering-fixture-schedule: local database DSN required", file=sys.stderr)
        return 1
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        assert_unique_schedules(cur)
    print("db-gate-proof: engineering fixture schedules — distinct due slots survive colliding UUID prefixes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
