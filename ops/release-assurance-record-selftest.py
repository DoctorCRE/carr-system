#!/usr/bin/env python3
"""Hermetic checks for release-bound assurance receipts and candidate intake."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "tools" / "ops-record.py"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


class Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_):
        return False


class Cursor:
    def __init__(self):
        self.one = None
        self.all: list = []
        self.recovery_lookups: list[str] = []
        self.run_sql = ""
        self.run_params = ()
        self.release = ("release-1", "service-1", 1000,
                        "rollback", "runbooks/rollback-worker.md")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql: str, params=()) -> None:
        normalized = " ".join(sql.split())
        if normalized == "select id from ops.service where key = %s":
            self.one = ("service-1",)
        elif normalized.startswith("select id, service_id, performance_budget_ms"):
            self.one = self.release
        elif normalized.startswith("insert into ops.run"):
            self.run_sql = sql
            self.run_params = params
            self.one = ("run-1",)
        elif ("from ops.incident" in normalized
              and "starts_with(signature, %s)" in normalized):
            # Since migration 0293 a SUCCEEDED run asks whether this job has any
            # open incident to clear. These receipts are all successes, so they
            # all ask. The answer here is "none", which is the shape that
            # matters for this file: with no open incident the recovery path
            # reads once and stops, so it can never disturb the receipt these
            # checks are about. ops/incident-fingerprint-selftest.py and
            # ops/incident-recovery-local-pg-acceptance.py own the case where
            # the answer is not empty.
            self.recovery_lookups.append(params[0])
            self.all = []
            self.one = None
        else:
            raise AssertionError(f"unexpected SQL: {normalized[:180]}")

    def fetchall(self):
        return self.all

    def fetchone(self):
        return self.one


class Connection:
    def __init__(self, cursor: Cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def transaction(self):
        return Context(None)

    def cursor(self):
        return self._cursor


class ApprovalCursor(Cursor):
    """Minimal typed-approval cursor: records the exact CLI dispatch."""

    def __init__(self):
        super().__init__()
        self.approval_sql = ""
        self.approval_params = ()

    def execute(self, sql: str, params=()) -> None:
        normalized = " ".join(sql.split())
        if normalized.startswith("select ops.approve_"):
            self.approval_sql = normalized
            self.approval_params = params
            self.one = ({"replayed": False},)
            return
        super().execute(sql, params)


def run_args(**changes):
    values = dict(
        service="carr-mcp", key="performance.release", state="succeeded",
        kind="check", environment="production", failure_class=None,
        exit_code=0, attempt=1, started_at=None, ended_at=None,
        duration_ms=999, release_key="release-key", budget_ms=1000,
        correlation="11111111-1111-4111-8111-111111111111",
        source_kind="wrapper", source_ref="bin/deploy-worker.sh",
        expires_in=None, evidence_ref="ops.run:performance-release", detail="999ms")
    values.update(changes)
    return SimpleNamespace(**values)


def approval_args(**changes):
    values = dict(
        action="staging-approve", key="staging-release-key", environment="staging",
        actor=None, manifest=None, plan_hash="sha256:" + "a" * 64,
        idempotency_key="11111111-1111-4111-8111-111111111111",
        expires_hours=12, verifier="independent-verifier",
        verifier_evidence="evidence:staging-verify",
    )
    values.update(changes)
    return SimpleNamespace(**values)


def main() -> int:
    print("release-assurance-record-selftest: exact measured receipts")
    spec = importlib.util.spec_from_file_location("ops_record_assurance_test", RECORD)
    assert spec and spec.loader
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cursor = Cursor()
    module.connect = lambda _kind: Connection(cursor)
    result = module.cmd_run(run_args())
    check("1. an exact within-budget performance receipt records", result == 0)
    check("1a. run insert placeholders match bound parameters",
          cursor.run_sql.count("%s") == len(cursor.run_params),
          f"sql={cursor.run_sql.count('%s')} params={len(cursor.run_params)}")
    started, ended = cursor.run_params[10], cursor.run_params[11]
    check("1b. measured duration becomes exact stored start/end evidence",
          ended - started == timedelta(milliseconds=999))
    check("1c. run is bound to the exact release and approved budget",
          cursor.run_params[3] == "release-1" and cursor.run_params[12] == 1000)
    # Migration 0293 made a green run ask whether this job has an open incident
    # to clear. It must ask about THIS job and no other: the lookup is a prefix
    # over the fingerprint, so a service or run key that leaked into it would
    # quietly clear a different service's incidents on somebody else's success.
    check("1d. a green run asks about its own job's open incidents, and only its own",
          cursor.recovery_lookups == ["carr-mcp|production|performance.release|"],
          f"lookups={cursor.recovery_lookups}")

    check("2. an over-budget run cannot claim succeeded",
          module.cmd_run(run_args(duration_ms=1001)) == 2)
    check("2a. a zero-duration run cannot claim measured success",
          module.cmd_run(run_args(duration_ms=0)) == 2)
    check("3. performance without evidence is refused before database access",
          module.cmd_run(run_args(evidence_ref=None)) == 2)
    check("4. a recovery claim in Production is refused",
          module.cmd_run(run_args(key="recovery.rehearsal.worker",
                                  budget_ms=None, duration_ms=None)) == 2)
    recovery = module.cmd_run(run_args(
        key="recovery.rehearsal.worker", environment="staging",
        budget_ms=None, duration_ms=None,
        evidence_ref="evidence:rollback-rehearsal"))
    check("4a. recovery receipt records the exact approved strategy and plan",
          recovery == 0 and cursor.run_params[13:15] == (
              "rollback", "runbooks/rollback-worker.md"))
    cursor.release = ("release-1", "service-1", 1000, "forward_fix", None)
    check("4b. recovery without an exact approved plan is refused",
          module.cmd_run(run_args(
              key="recovery.rehearsal.worker", environment="staging",
              budget_ms=None, duration_ms=None,
              evidence_ref="evidence:missing-plan")) == 2)
    cursor.release = ("release-1", "service-1", 1000,
                      "rollback", "runbooks/rollback-worker.md")
    check("5. a budget on an unrelated run is refused",
          module.cmd_run(run_args(key="golden.smoke-reads")) == 2)

    with tempfile.TemporaryDirectory() as raw:
        manifest_path = Path(raw) / "missing-assurance.json"
        manifest_path.write_text(json.dumps({
            "service": "carr-mcp", "environment": "production",
            "provider": "cloudflare-workers",
            "provider_version_id": "11111111-2222-4333-8444-555555555555",
        }))
        candidate = SimpleNamespace(
            action="candidate", key="release-key", manifest=str(manifest_path),
            service="carr-mcp", environment="production", sha=None,
            provider="cloudflare-workers",
            provider_version_id="11111111-2222-4333-8444-555555555555")
        check("6. Production candidate missing assurance is refused before DB",
              module.cmd_release(candidate) == 2)

    # Staging approval is a distinct authority-only action.  These are
    # executable command-boundary checks: the fake records both connection
    # selection and the typed function/arguments without a database credential.
    approval_cursor = ApprovalCursor()
    connections: list[str] = []
    def approval_connect(kind: str) -> Connection:
        connections.append(kind)
        return Connection(approval_cursor)
    module.connect = approval_connect
    staging = approval_args()
    check("6a. staging approval uses the Joe authority connection",
          module.cmd_release(staging) == 0 and connections == ["authority"])
    check("6b. staging approval dispatches its exact typed function and evidence",
          approval_cursor.approval_sql ==
          "select ops.approve_staging_release(%s,%s,%s::uuid,%s,%s,%s)"
          and approval_cursor.approval_params == (
              staging.key, staging.plan_hash, staging.idempotency_key,
              staging.expires_hours, staging.verifier, staging.verifier_evidence))

    production = approval_args(action="approve", environment="production")
    connections.clear()
    approval_cursor.approval_sql = ""
    approval_cursor.approval_params = ()
    check("6c. Production approve retains its Program 5 typed function",
          module.cmd_release(production) == 0 and connections == ["authority"]
          and approval_cursor.approval_sql ==
          "select ops.approve_program5_release(%s,%s,%s::uuid,%s,%s,%s)"
          and approval_cursor.approval_params == (
              production.key, production.plan_hash, production.idempotency_key,
              production.expires_hours, production.verifier,
              production.verifier_evidence))

    for name, bad in (
        ("wrong environment", approval_args(environment="production")),
        ("caller actor", approval_args(actor="attacker")),
        ("missing plan hash", approval_args(plan_hash=None)),
        ("missing idempotency key", approval_args(idempotency_key=None)),
        ("invalid idempotency UUID", approval_args(idempotency_key="not-a-uuid")),
    ):
        connections.clear()
        approval_cursor.approval_sql = ""
        check(f"6d. staging approval {name} is refused before connection",
              module.cmd_release(bad) == 2 and not connections
              and not approval_cursor.approval_sql)

    source = RECORD.read_text(encoding="utf-8")
    check("7. candidate persists all three approval-bound assurance fields",
          all(field in source for field in (
              "performance_budget_ref", "performance_budget_ms", "recovery_strategy")))

    # 8-10 pin the defect found 2026-08-19: `approve` set state='approved' and
    # NEVER wrote verifier_actor or verifier_evidence_ref, while migration 0169
    # requires both for that state. The only writer of those columns was the
    # `complete` branch, which runs after approval and after the deploy — so
    # approve could not succeed for ANY release, on any path, and died on a raw
    # constraint name that told the reader nothing.
    #
    # Source assertions rather than a live database, matching how 7 above works
    # and why: this selftest runs in CI, which must never hold a production
    # credential, and the columns in question are only observable through a
    # write nobody should be doing from a test run.
    # The candidate insert lives in one named statement now: PR 1013's eighth
    # correction withdrew the seventh's SECURITY DEFINER door — it could not be
    # granted under the moratorium, so it stopped the wrapper filing anything —
    # and moved the provenance into migration 0504's trigger and generated column
    # instead. The columns 0169 says a candidate may collect are asserted on that
    # statement and on the arguments this tool passes it.
    candidate_call = source[source.index("CANDIDATE_INSERT = "):]
    candidate_call = candidate_call[:candidate_call.index("row = cur.fetchone()")]
    provenance = (REPO / "migrations" /
                  "0504_release_maker_session_provenance.sql").read_text(encoding="utf-8")
    check("8. the CANDIDATE can collect the verifier, which 0169 says it may",
          "args.verifier, args.verifier_evidence" in candidate_call
          and "verifier_actor, verifier_evidence_ref," in candidate_call
          and "new.maker_session_user := session_user;" in provenance
          and "generated always as (ops.authority_login_slug(maker_session_user) is not null)"
              in provenance)

    approval_migration = (REPO / "migrations" /
                          "0205_program5_approval_verifier.sql").read_text(encoding="utf-8")
    approval_migration_lower = approval_migration.lower()
    check("9. APPROVE accepts a verifier, for the ordinary case where the "
          "verifying run finishes after the candidate was filed",
          "p_verifier_actor text,p_verifier_evidence_ref text" in approval_migration
          and "supplied verifier actor and evidence must be an atomic nonblank pair" in approval_migration
          and "verifier_actor_value:=coalesce(supplied_verifier_actor,candidate_verifier_actor)" in approval_migration)
    check("10. approve REFUSES in words rather than leaving it to the constraint",
          "cannot be approved without an INDEPENDENT VERIFIER" in approval_migration
          and "maker cannot independently verify their own release" in approval_migration)
    check("11. receipt binding preserves 0202 append-only evidence",
          "populated 0202 evidence requires a separate audited versioned conversion"
          in approval_migration
          and "update ops.release_approval_receipt" not in approval_migration_lower)

    if FAILURES:
        print(f"release-assurance-record-selftest: {len(FAILURES)} FAILED")
        return 1
    print("release-assurance-record-selftest: exact assurance receipts hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
