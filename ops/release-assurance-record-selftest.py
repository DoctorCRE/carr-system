#!/usr/bin/env python3
"""Hermetic checks for release-bound assurance run receipts."""

from __future__ import annotations

import sys
from pathlib import Path
import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

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
        self.deployment_params = ()
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
        elif normalized.startswith("insert into ops.deployment"):
            self.deployment_params = params
            self.one = ("deployment-1",)
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

    for number, command in enumerate((
        "release", "staging-target", "staging-attempt", "staging-restore-only",
        "staging-forward-fix", "staging-readback-verify", "staging-provider-version",
    ), start=6):
        label = f"{number}. the retired {command} command is rejected by the CLI"
        with patch.object(sys, "argv", ["ops-record", command, "--help"]), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            try:
                module.main()
            except SystemExit as exc:
                refused = exc.code == 2 and f"invalid choice: '{command}'" in error.getvalue()
            else:
                refused = False
        check(label, refused)

    with patch.object(sys, "argv", [
        "ops-record", "deployment", "--service", "carr-mcp", "--environment", "local",
        "--state", "planned", "--git-sha", "a" * 40,
    ]), redirect_stdout(io.StringIO()) as output:
        recorded = module.main()
    check("13. generic deployment still records through the CLI",
          recorded == 0 and output.getvalue().strip().endswith(" deployment-1")
          and cursor.deployment_params[1:5] == ("service-1", "local", "planned", "a" * 40))
    check("14. a deployment records the current recorder as its default source",
          cursor.deployment_params[18:20] == ("wrapper", "tools/ops-record.py"))

    if FAILURES:
        print(f"release-assurance-record-selftest: {len(FAILURES)} FAILED")
        return 1
    print("release-assurance-record-selftest: exact assurance receipts hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
