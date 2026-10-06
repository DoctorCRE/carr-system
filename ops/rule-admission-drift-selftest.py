#!/usr/bin/env python3
"""Contract tests for the nightly rule-admission drift watch.

The watch reads Production under a routine role, so what is worth pinning is
what it refuses to connect as, and that the numbers it prints are judged by the
same predicate the audit itself uses. Both are checked without a database.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "ops" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[str] = []
    ran: list[str] = []

    def check(name, condition):
        ran.append(name)
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    drift = load("rule_admission_drift", "rule-admission-drift.py")
    audit = load("rule_admission_audit", "rule-admission-audit.py")

    os.environ.pop("CARR_DB_JOBS_URL", None)
    check("no routine credential is a SKIP, not a failed night",
          drift.main() == drift.EX_CONFIG)

    os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_owner:x@example/db"
    try:
        drift.routine_dsn()
        refused = False
    except SystemExit as exc:
        refused = exc.code == 1
    check("an owner login is refused before any connection is opened", refused)

    os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:x@example/db"
    check("a jobs login is accepted", drift.routine_dsn() is not None)
    os.environ.pop("CARR_DB_JOBS_URL", None)

    clean = {"total": 218, "admitted": 218, "needs_revision": 0, "missing": 0, "incomplete": 0}
    check("a fully admitted store is not a finding", audit.failing(clean) is False)
    check("a rule with no contract is a finding",
          audit.failing({**clean, "admitted": 217, "missing": 1}) is True)
    check("a contract admitted against an uninstalled control is a finding",
          audit.failing({**clean, "admitted": 217, "needs_revision": 1}) is True)
    check("an admitted contract missing its four dimensions is a finding",
          audit.failing({**clean, "incomplete": 1}) is True)
    # The empty store is the shape a sanitized rehearsal database has, and it
    # must stay a finding by default: the rollback gate opts into it explicitly.
    empty = {"total": 0, "admitted": 0, "needs_revision": 0, "missing": 0, "incomplete": 0}
    check("an empty store is a finding unless explicitly allowed",
          audit.failing(empty) is True
          and audit.failing(empty, allow_empty_store=True) is False)
    check("the rendered line names all five numbers",
          all(k in audit.render(clean) for k in
              ("total=", "admitted=", "needs_revision=", "missing=", "incomplete=")))

    # The installed writer as 0482 ships it; the preflight derives the keys an
    # admission must carry from whatever body is installed, never a copy.
    installed = (REPO / "migrations/0482_rule_delivery_binding_writer.sql").read_text()
    rule_id = "22222222-2222-4222-8222-222222222222"
    delivery = {"load_layer": "layer0", "packs": [], "why": "always"}

    class PreflightCursor:
        def __init__(self, definition=installed, admission=None):
            self.definition, self.admission = definition, admission

        def execute(self, sql, params=()):
            self.sql = sql
            if not sql.lstrip().lower().startswith("select"):
                raise AssertionError("preflight attempted a write")

        def fetchone(self):
            if "pg_get_functiondef" in self.sql:
                return (self.definition,)
            return None if self.admission is None else (self.admission,)

    def admission(delivery):
        return {"rule_id": rule_id, "state": "admitted", "rule_status": "proposed",
                "projection": {"delivery": delivery}}

    ready = audit.preflight(PreflightCursor(admission=admission(delivery)), rule_id)
    check("a prepared admission carrying every key the installed writer reads is ready",
          ready["status"] == "ready"
          and ready["delivery_contract"]["required_keys"] == ["load_layer", "packs", "why"]
          and ready["prepared_admission"]["rule_status"] == "proposed")
    check("the contract-only read names the required keys and passes",
          audit.preflight(PreflightCursor())["status"] == "contract_read")
    check("an uninstalled writer is a failed readback",
          audit.preflight(PreflightCursor(definition=None))["status"]
          == "delivery_contract_missing")
    check("a writer body that never reads projection.delivery is a failed readback",
          audit.preflight(PreflightCursor(definition="begin return null; end"))["status"]
          == "delivery_contract_unrecognised")
    check("a rule with no prepared admission is a failed readback",
          audit.preflight(PreflightCursor(), rule_id)["status"] == "admission_missing")
    short = audit.preflight(PreflightCursor(
        admission=admission({"load_layer": "layer0", "packs": []})), rule_id)
    check("an admission missing a key the writer reads is named, not inferred ready",
          short["status"] == "delivery_projection_incomplete"
          and short["missing_keys"] == ["why"])

    class Conn:
        def __init__(self, cursor):
            self.cur, self.statements = cursor, []
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql): self.statements.append(sql)
        def cursor(self): return contextlib.nullcontext(self.cur)

    def run_main(argv, cursor):
        conn = Conn(cursor)
        saved = (sys.argv, audit.psycopg.connect)
        sys.argv, audit.psycopg.connect = ["rule-admission-audit.py", *argv], lambda dsn: conn
        os.environ["DATABASE_URL"] = "postgresql://reader@example/db"
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = audit.main()
        except SystemExit as exc:
            code = exc.code
        finally:
            sys.argv, audit.psycopg.connect = saved[0], saved[1]
            os.environ.pop("DATABASE_URL", None)
        return code, conn.statements

    code, statements = run_main(["--preflight", "--rule-id", rule_id], PreflightCursor())
    check("a failed readback exits nonzero from the command line", code == 1)
    check("the preflight runs inside a read-only transaction",
          statements[:1] == ["set transaction read only"])
    code, _ = run_main(["--preflight", "--rule-id", rule_id],
                       PreflightCursor(admission=admission(delivery)))
    check("a ready readback exits zero", code == 0)
    code, _ = run_main(["--rule-id", rule_id], PreflightCursor())
    check("--rule-id without --preflight is refused", code == 2)

    print(f"\nrule-admission-drift-selftest: {len(ran)-len(failures)}/{len(ran)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
