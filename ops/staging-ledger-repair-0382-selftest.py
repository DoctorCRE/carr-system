#!/usr/bin/env python3
"""Static and state-machine checks for the one-time staging 0382 repair."""

import hashlib
import importlib.util
import os
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "staging-ledger-repair-0382.py"
DB_GATE = REPO / "ops" / "staging-ledger-repair-0382-db-gate.py"
spec = importlib.util.spec_from_file_location("staging_repair_0382", TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
gate_spec = importlib.util.spec_from_file_location("staging_repair_0382_db_gate", DB_GATE)
assert gate_spec and gate_spec.loader
gate = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(gate)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


gate.require_disposable_dsn("postgresql://carr_ci@127.0.0.1:55601/carr_ci")
check("explicit carr_ci loopback DSN is accepted", True)
for label, unsafe_dsn in [
    ("remote host refuses", "postgresql://carr_ci@remote.example/carr_ci"),
    ("host query override refuses",
     "postgresql://carr_ci@localhost/carr_ci?host=remote.example"),
    ("hostaddr query override refuses",
     "postgresql://carr_ci@localhost/carr_ci?hostaddr=203.0.113.1"),
    ("database query override refuses",
     "postgresql://carr_ci@localhost/carr_ci?dbname=production"),
]:
    try:
        gate.require_disposable_dsn(unsafe_dsn)
    except RuntimeError:
        check(label, True)
    else:
        check(label, False)

hosted_environment = {
    "GITHUB_ACTIONS": "true",
    "CI": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "GITHUB_REPOSITORY": "jbookout/carr-system",
}
check("local connected identity is disposable",
      gate.connected_identity_is_disposable(
          ("carr_ci", "carr_ci", True, "127.0.0.1", "127.0.0.1"), {}) is True)
check("GitHub Docker bridge identity is disposable only on the pinned hosted runner",
      gate.connected_identity_is_disposable(
          ("carr_ci", "carr_ci", True, "172.18.0.2", "172.18.0.1"),
          hosted_environment) is True)
check("private bridge identity outside pinned hosted CI refuses",
      gate.connected_identity_is_disposable(
          ("carr_ci", "carr_ci", True, "172.18.0.2", "172.18.0.1"), {}) is False)
check("public server identity refuses even in pinned hosted CI",
      gate.connected_identity_is_disposable(
          ("carr_ci", "carr_ci", True, "8.8.8.8", "172.18.0.1"),
          hosted_environment) is False)


migration_bytes = module.MIGRATION.read_bytes()
check(
    "checked-in 0382 matches Production's recorded immutable digest",
    hashlib.sha256(migration_bytes).hexdigest() == module.EXPECTED_SHA256,
)
check("repaired body is the exact 0382 body", module.normalize_sql(module.EXPECTED_REPAIRED_BODY)
      in module.normalize_sql(module.MIGRATION.read_text()))
check("legacy body is the exact 0170 body", module.normalize_sql(module.EXPECTED_LEGACY_BODY)
      in module.normalize_sql((REPO / "migrations/0170_guidance_import_lifecycle.sql").read_text()))
check("exact genuine hole replays and records",
      module.classify_state({module.LATER_NAME: module.LATER_SHA256}, "legacy")
      == "replay_and_record")
check("crash recovery replays immutable bytes before recording",
      module.classify_state({module.LATER_NAME: module.LATER_SHA256}, "repaired")
      == "replay_and_record")
check(
    "already-recorded exact digest is permanently idempotent",
    module.classify_state(
        {
            module.MIGRATION_NAME: module.EXPECTED_SHA256,
            module.LATER_NAME: module.LATER_SHA256,
        },
        "repaired",
    )
    == "already_recorded",
)

for label, ledger, boundary in [
    ("missing later marker refuses", {}, "legacy"),
    ("mismatched later digest refuses", {module.LATER_NAME: "bad"}, "legacy"),
    ("unknown function state refuses", {module.LATER_NAME: module.LATER_SHA256}, "unknown"),
    (
        "mismatched 0382 digest refuses",
        {module.MIGRATION_NAME: "bad", module.LATER_NAME: module.LATER_SHA256},
        "repaired",
    ),
    (
        "recorded digest with legacy boundary refuses",
        {module.MIGRATION_NAME: module.EXPECTED_SHA256,
         module.LATER_NAME: module.LATER_SHA256},
        "legacy",
    ),
    (
        "recorded digest with unknown boundary refuses",
        {module.MIGRATION_NAME: module.EXPECTED_SHA256,
         module.LATER_NAME: module.LATER_SHA256},
        "unknown",
    ),
]:
    try:
        module.classify_state(ledger, boundary)
    except ValueError:
        check(label, True)
    else:
        check(label, False)


def boundary_row(kind: str, **changes: Any) -> tuple[Any, ...]:
    values: dict[str, Any] = {
        "security_definer": kind == "repaired",
        "volatility": "s",
        "config": list(module.EXPECTED_CONFIG) if kind == "repaired" else [],
        "body": (module.EXPECTED_REPAIRED_BODY if kind == "repaired"
                 else module.EXPECTED_LEGACY_BODY),
        "result": module.EXPECTED_RESULT,
        "owner_is_current": True,
        "execute_grantees": list(module.EXPECTED_EXECUTE_GRANTEES),
        "execute_grantable": False,
        "reader_execute": True,
        "writer_execute": True,
        "reader_rule_statement": False,
        "reader_actor_display_name": False,
    }
    values.update(changes)
    return tuple(values[name] for name in (
        "security_definer", "volatility", "config", "body", "result",
        "owner_is_current", "execute_grantees", "execute_grantable", "reader_execute",
        "writer_execute", "reader_rule_statement", "reader_actor_display_name",
    ))


check("exact legacy catalog state is recognized",
      module.classify_boundary_row(boundary_row("legacy")) == "legacy")
check("exact repaired catalog state is recognized",
      module.classify_boundary_row(boundary_row("repaired")) == "repaired")

boundary_failures: list[tuple[str, dict[str, Any]]] = [
    ("wrong owner refuses", {"owner_is_current": False}),
    ("extra function config refuses",
     {"config": [*module.EXPECTED_CONFIG, "statement_timeout=0"]}),
    ("PUBLIC execute refuses",
     {"execute_grantees": ["PUBLIC", *module.EXPECTED_EXECUTE_GRANTEES]}),
    ("altered function body refuses",
     {"body": module.EXPECTED_REPAIRED_BODY + " union all select null"}),
    ("case-changed SQL literal refuses",
     {"body": module.EXPECTED_REPAIRED_BODY.replace("'active'", "'ACTIVE'")}),
    ("execute grant option refuses", {"execute_grantable": True}),
    ("wrong result contract refuses", {"result": "TABLE(source_rule_id uuid)"}),
    ("volatile function refuses", {"volatility": "v"}),
    ("rule statement grant widening refuses", {"reader_rule_statement": True}),
    ("actor display-name grant widening refuses", {"reader_actor_display_name": True}),
]
for label, changes in boundary_failures:
    check(label, module.classify_boundary_row(boundary_row("repaired", **changes))
          == "unknown")


class FakeCursor:
    def __init__(self, row: tuple[Any, ...], reader_succeeds: bool = True) -> None:
        self.row = row
        self.reader_succeeds = reader_succeeds
        self.mode = ""
        self.queries: list[str] = []

    def execute(self, query: str, _params: tuple[Any, ...] = ()) -> "FakeCursor":
        self.queries.append(query)
        if "from pg_catalog.pg_proc" in query:
            self.mode = "catalog"
        elif "select count(*) from ops.standing_guidance" in query:
            self.mode = "reader"
            if not self.reader_succeeds:
                raise module.psycopg.errors.InsufficientPrivilege("denied")
        else:
            self.mode = ""
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [self.row] if self.mode == "catalog" else []

    def fetchone(self) -> tuple[int] | None:
        return (0,) if self.mode == "reader" else None


working_reader = FakeCursor(boundary_row("repaired"))
check("repaired boundary executes through the real reader role",
      module.boundary_state(working_reader) == "repaired"
      and any("set local role carr_reader" in query for query in working_reader.queries))
check("reader execution failure makes the boundary unknown",
      module.boundary_state(FakeCursor(boundary_row("repaired"), False)) == "unknown")


def live_reader_refuses(cur: Any, query: str) -> bool:
    cur.execute("savepoint staging_repair_denial_probe")
    try:
        cur.execute("set local role carr_reader")
        cur.execute(query)
    except module.psycopg.errors.InsufficientPrivilege as exc:
        return exc.sqlstate == "42501"
    finally:
        cur.execute("rollback to savepoint staging_repair_denial_probe")
    return False


dsn = os.environ.get("DATABASE_URL", "")
if dsn:
    with module.psycopg.connect(dsn) as connection:
        live = connection.cursor()
        check("live catalog and carr_reader projection match the exact repaired boundary",
              module.boundary_state(live) == "repaired")
        check("live carr_reader still cannot read rule statements directly",
              live_reader_refuses(live, "select statement from public.rule limit 1"))
        check("live carr_reader still cannot read actor display names directly",
              live_reader_refuses(live, "select display_name from public.actor limit 1"))

source = TOOL.read_text()
replay = source.index("cur.execute(sql_text)")
record = source.index("insert into public.schema_migrations")
check("repair replays exact migration bytes", replay < record)
check("ledger record follows exact repaired-boundary verification",
      source.index("post-replay boundary verification failed") < record)
