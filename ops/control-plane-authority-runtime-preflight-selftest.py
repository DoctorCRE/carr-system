#!/usr/bin/env python3
"""Hermetic tests for the external Joe/Dell authority-runtime probe."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "ops" / "control-plane-authority-runtime-preflight.py"
FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail_on_ok=True, failures=FAILED)


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("control_plane_authority_runtime_preflight", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("authority runtime preflight module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self, *, login: str, memberships: dict[str, bool] | None = None,
                 missing_function: str | None = None,
                 mutation_privileges: list[tuple[str, str]] | None = None,
                 read_only: str = "on") -> None:
        self.login = login
        self.memberships = memberships or {
            "carr_authority": True,
            "carr_writer": False,
            "carr_jobs": False,
            "carr_reader": False,
            "carr_exporter": False,
            "carr_backup": False,
            "carr_device_evidence": False,
        }
        self.missing_function = missing_function
        self.mutation_privileges = mutation_privileges or [
            ("public.event", "INSERT"),
            ("public.tool_call", "INSERT"),
        ]
        self.read_only = read_only
        self.queries: list[tuple[str, object | None]] = []
        self.result: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: object | None = None) -> None:
        compact = " ".join(query.lower().split())
        self.queries.append((compact, params))
        if compact == "begin transaction read only":
            self.result = []
        elif "select session_user,current_user,current_setting('transaction_read_only')" in compact:
            self.result = [(self.login, self.login, self.read_only)]
        elif compact == "select ops.authority_actor_slug()":
            self.result = [("joe" if self.login == "carr_authority_joe" else "dell",)]
        elif "from pg_roles where rolname=current_user" in compact:
            self.result = [(self.login, False, True, False, False, True, False, False)]
        elif "pg_has_role(current_user,role_name,'member')" in compact:
            self.result = [(name, self.memberships.get(name, False)) for name in params[0]]  # type: ignore[index]
        elif "has_function_privilege(current_user,signature,'execute')" in compact:
            self.result = [(name, name != self.missing_function) for name in params[0]]  # type: ignore[index]
        elif "has_table_privilege(current_user,c.oid,privilege_name)" in compact:
            self.result = list(self.mutation_privileges)
        elif compact == "rollback":
            self.result = []
        else:
            raise AssertionError(f"unregistered SQL: {compact}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.result)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.fake_cursor

    def close(self) -> None:
        self.closed = True


def main() -> int:
    try:
        probe = load_module()
    except Exception as exc:
        check("implementation module exists", False, str(exc))
        return 1

    joe_dsn = "postgresql://carr_authority_joe:synthetic-only@invalid.invalid/carr"  # ci-secret-scan: allow
    dell_dsn = "postgresql://carr_authority_dell:synthetic-only@invalid.invalid/carr"  # ci-secret-scan: allow
    joe_tls_dsn = joe_dsn + "?sslmode=require"
    dell_tls_dsn = dell_dsn + "?sslmode=require&channel_binding=require"
    check("direct authority URI admits canonical Neon TLS forms",
          probe.is_direct_authority_uri(joe_tls_dsn, "carr_authority_joe")
          and probe.is_direct_authority_uri(dell_tls_dsn, "carr_authority_dell"))
    invalid_dsns = [
        "postgresql://carr_writer:synthetic-only@invalid.invalid/carr",  # ci-secret-scan: allow
        "postgresql://carr_authority_joe@invalid.invalid/carr",
        "postgresql://carr_authority_joe:synthetic-only@invalid.invalid/carr?service=alternate",  # ci-secret-scan: allow
        "postgresql://carr_authority_joe:synthetic-only@invalid.invalid/carr?sslmode=disable",  # ci-secret-scan: allow
        "postgresql://carr_authority_joe:synthetic-only@invalid.invalid/carr?channel_binding=require",  # ci-secret-scan: allow
        "postgresql://carr_authority_joe:synthetic-only@invalid.invalid/carr?sslmode=require&sslmode=require",  # ci-secret-scan: allow
        "postgresql://carr_authority_joe:synthetic-only@invalid.invalid/carr#fragment",  # ci-secret-scan: allow
        "user=carr_authority_joe password=synthetic-only host=invalid.invalid dbname=carr",  # ci-secret-scan: allow
    ]
    check("fallback, broad, incomplete, noncanonical TLS query, fragment, and keyword DSNs refuse",
          all(not probe.is_direct_authority_uri(value, "carr_authority_joe") for value in invalid_dsns))

    connector_calls: list[str] = []

    def connector(dsn: str) -> FakeConnection:
        connector_calls.append(dsn)
        login = "carr_authority_joe" if "_joe:" in dsn else "carr_authority_dell"
        return FakeConnection(FakeCursor(login=login))

    broad_envs = [
        {"DATABASE_URL": "present"},
        {"CARR_DB_WRITER_URL": "present"},
        {"CARR_DB_JOBS_URL": "present"},
        {"PGPASSFILE": "/synthetic"},
        {"PGSSLMODE": "require"},
    ]
    for inherited in broad_envs:
        before = len(connector_calls)
        report = probe.collect_runtime(inherited, connector)
        check(f"broad environment refuses before connect ({next(iter(inherited))})",
              report["error"] == "broad_environment_refused" and len(connector_calls) == before)

    env = {
        "CARR_DB_AUTHORITY_JOE_URL": joe_tls_dsn,
        "CARR_DB_AUTHORITY_DELL_URL": dell_tls_dsn,
    }
    report = probe.collect_runtime(env, connector)
    check("Joe authority identity is the required rollout proof",
          report["required_authority_identities_verified"] is True
          and report["authority_runtime_identities_verified"] is True)
    check("Dell authority identity is verified when available without becoming required",
          report["optional_authority_identities_verified"] == {"dell": True})
    check("runtime report never authenticates phase exit", report["phase_exit_authorized"] is False)
    check("runtime report contains no credential material",
          "synthetic-only" not in json.dumps(report) and "invalid.invalid" not in json.dumps(report))
    check("partner-specific credentials are both exercised", len(connector_calls) == 2)

    joe_only = probe.collect_runtime({"CARR_DB_AUTHORITY_JOE_URL": joe_tls_dsn}, connector)
    check("Joe-only authority is rollout-ready and Dell remains optional",
          joe_only["required_authority_identities_verified"] is True
          and joe_only["authority_runtime_identities_verified"] is False
          and joe_only["principals"]["dell"]["credential_present"] is False
          and joe_only["optional_authority_identities_verified"] == {"dell": False}
          and probe.system_rollout_ready(joe_only) is True)

    dell_only = probe.collect_runtime({"CARR_DB_AUTHORITY_DELL_URL": dell_tls_dsn}, connector)
    check("Dell-only authority preserves Dell capability but cannot replace Joe's ownership",
          dell_only["required_authority_identities_verified"] is False
          and dell_only["authority_runtime_identities_verified"] is False
          and dell_only["optional_authority_identities_verified"] == {"dell": True}
          and probe.system_rollout_ready(dell_only) is False)

    joe_conn = connector(joe_tls_dsn)
    joe = probe.probe_principal("joe", "carr_authority_joe", joe_tls_dsn, lambda _dsn: joe_conn)
    queries = [query for query, _ in joe_conn.fake_cursor.queries]
    check("read-only transaction begins before identity or catalog reads",
          queries[0] == "begin transaction read only"
          and "select session_user" in queries[1]
          and queries[2] == "select ops.authority_actor_slug()")
    check("probe rolls back and closes the authority connection",
          queries[-1] == "rollback" and joe_conn.closed)
    check("Joe result is exact and least-privilege", joe["verified"] is True)
    check("Joe actor mapping is exercised, not inferred", joe["actor_mapping_matches"] is True)

    mismatch = FakeConnection(FakeCursor(login="carr_writer"))
    mismatch_report = probe.probe_principal("joe", "carr_authority_joe", joe_tls_dsn, lambda _dsn: mismatch)
    check("post-connect identity mismatch refuses", mismatch_report["verified"] is False)

    broad_member = FakeConnection(FakeCursor(login="carr_authority_joe",
        memberships={"carr_authority": True, "carr_writer": True}))
    broad_member_report = probe.probe_principal("joe", "carr_authority_joe", joe_tls_dsn,
                                                lambda _dsn: broad_member)
    check("writer membership refuses", broad_member_report["verified"] is False)

    extra_write = FakeConnection(FakeCursor(login="carr_authority_joe",
        mutation_privileges=[("ops.job", "UPDATE")]))
    extra_write_report = probe.probe_principal("joe", "carr_authority_joe", joe_tls_dsn,
                                               lambda _dsn: extra_write)
    check("unexpected direct table mutation authority refuses", extra_write_report["verified"] is False)

    missing_function = probe.REQUIRED_AUTHORITY_FUNCTIONS[0]
    missing = FakeConnection(FakeCursor(login="carr_authority_joe", missing_function=missing_function))
    missing_report = probe.probe_principal("joe", "carr_authority_joe", joe_tls_dsn,
                                          lambda _dsn: missing)
    check("missing authority function reach refuses", missing_report["verified"] is False)

    absent = probe.collect_runtime({}, connector)
    check("missing credentials stay unproven without connecting",
          absent["authority_runtime_identities_verified"] is False
          and absent["principals"]["joe"]["credential_present"] is False
          and absent["principals"]["dell"]["credential_present"] is False)

    print(f"control-plane authority runtime preflight selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
