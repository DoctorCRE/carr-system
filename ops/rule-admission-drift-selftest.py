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
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_GRANT = re.compile(r"\bgrant\s+select\s+on\s+(?:table\s+)?([^;]+?)\s+to\s+([^;]+?);",
                    re.I | re.S)
_REVOKE = re.compile(r"\brevoke\s+(?:select|all)\s+on\s+(?:table\s+)?([^;]+?)\s+from\s+([^;]+?);",
                     re.I | re.S)
_RELATION = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)", re.I)


def qualified(name: str) -> str:
    name = name.strip().strip('"').lower()
    return name if "." in name else f"public.{name}"


def reader_relations() -> set[str]:
    """Relations carr_reader may SELECT whole, replayed from the migrations in order.

    The preflight is documented to run on DATABASE_URL_READER, a login granted
    carr_reader, which is views-only by design (migration 0188). Column grants
    (`grant select (a, b) on ...`) do not match and are not counted.
    """
    granted: set[str] = set()
    for path in sorted((REPO / "migrations").glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text())
        events = [(m.start(), True, m) for m in _GRANT.finditer(sql)]
        events += [(m.start(), False, m) for m in _REVOKE.finditer(sql)]
        for _, grant, match in sorted(events, key=lambda e: e[0]):
            roles = {r.strip().lower() for r in match.group(2).split(",")}
            if "carr_reader" not in roles:
                continue
            for relation in match.group(1).split(","):
                (granted.add if grant else granted.discard)(qualified(relation))
    return granted


def relations_read(sql: str) -> set[str]:
    return {qualified(name) for name in _RELATION.findall(sql)}


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
        """Answers the preflight's two reads. `rule_status=None` is a rule id
        the store does not hold; `admission=None` is a rule with no admission."""
        def __init__(self, definition=installed, admission=None, rule_status="proposed"):
            self.definition, self.admission = definition, admission
            self.rule_status, self.statements = rule_status, []

        def execute(self, sql, params=()):
            self.sql = sql
            self.statements.append(sql)
            if not sql.lstrip().lower().startswith("select"):
                raise AssertionError("preflight attempted a write")

        def fetchone(self):
            if "pg_get_functiondef" in self.sql:
                return (self.definition,)
            if self.rule_status is None:
                return None
            return ({"rule_status": self.rule_status, "admission": self.admission},)

    def admission(delivery):
        return {"rule_id": rule_id, "state": "admitted",
                "projection": {"delivery": delivery}}

    # WR-000222: the preflight is documented to run on the read credential, and
    # carr_reader cannot read the base table `rule` (views only, migration
    # 0188). Every relation the preflight reads must be one carr_reader holds.
    readable = reader_relations()
    check("carr_reader still cannot read the base rule table (the boundary holds)",
          "public.rule" not in readable and "ops.rule_admission" in readable)
    probe = PreflightCursor(admission=admission(delivery))
    audit.preflight(probe, rule_id)
    read = set().union(*(relations_read(sql) for sql in probe.statements))
    check("every relation the preflight reads is granted to carr_reader "
          f"(reads {sorted(read)}, ungranted {sorted(read - readable)})",
          bool(read) and read <= readable)

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
    unprepared = audit.preflight(PreflightCursor(), rule_id)
    check("a rule with no prepared admission is a failed readback that still names its status",
          unprepared["status"] == "admission_missing"
          and unprepared.get("rule_status") == "proposed")
    check("a rule id the store does not hold is its own failed readback",
          audit.preflight(PreflightCursor(rule_status=None), rule_id)["status"]
          == "rule_missing"
          and "rule_missing" in audit.FAILED_READBACKS)
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

    def run_main(argv, cursor, env=None):
        conn = Conn(cursor)
        conn.dsn = None
        def connect(dsn):
            conn.dsn = dsn
            return conn
        saved = (sys.argv, audit.psycopg.connect)
        sys.argv, audit.psycopg.connect = ["rule-admission-audit.py", *argv], connect
        env = {"DATABASE_URL": "postgresql://reader@example/db"} if env is None else env
        for name in ("DATABASE_URL", "DATABASE_URL_READER"):
            os.environ.pop(name, None)
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = audit.main()
        except SystemExit as exc:
            code = exc.code
        finally:
            sys.argv, audit.psycopg.connect = saved[0], saved[1]
            for name in ("DATABASE_URL", "DATABASE_URL_READER"):
                os.environ.pop(name, None)
        return code, conn.statements, conn.dsn

    code, statements, _ = run_main(["--preflight", "--rule-id", rule_id], PreflightCursor())
    check("a failed readback exits nonzero from the command line", code == 1)
    check("the preflight runs inside a read-only transaction",
          statements[:1] == ["set transaction read only"])
    code, _, _ = run_main(["--preflight", "--rule-id", rule_id],
                          PreflightCursor(admission=admission(delivery)))
    check("a ready readback exits zero", code == 0)
    code, _, _ = run_main(["--rule-id", rule_id], PreflightCursor())
    check("--rule-id without --preflight is refused", code == 2)

    # The documented command needs no hand-exported DSN: the preflight connects
    # as the read credential by name (environment first, then db.env).
    reader = "postgresql://app_reader@example/db"
    code, _, dsn = run_main(["--preflight", "--rule-id", rule_id],
                            PreflightCursor(admission=admission(delivery)),
                            env={"DATABASE_URL_READER": reader})
    check("the preflight connects as DATABASE_URL_READER when no DSN is exported",
          code == 0 and dsn == reader)
    saved_home = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as empty_home:
        os.environ["HOME"] = empty_home
        try:
            code, _, dsn = run_main(["--preflight"], PreflightCursor(), env={})
        finally:
            os.environ["HOME"] = saved_home or ""
    check("no credential anywhere is a configuration error, not a connection",
          code == 2 and dsn is None)

    print(f"\nrule-admission-drift-selftest: {len(ran)-len(failures)}/{len(ran)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
