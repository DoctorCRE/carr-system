#!/usr/bin/env python3
"""Hermetic contract tests for the measured situation-retrieval collector."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import uuid
from types import SimpleNamespace


REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "ops" / "collect-situation-retrieval-observation.py"
SPEC = importlib.util.spec_from_file_location("situation_observation", MODULE_PATH)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(ok="  ok  {label}", detail=None, raises="{label}")


class FakeCursor:
    def __init__(self, rows_by_policy: dict[str, list[dict[str, object]]]) -> None:
        self.rows_by_policy = rows_by_policy
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.rows: list[dict[str, object]] = []
        self.source_status = "active"
        self.migration_present = True

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: tuple[object, ...]) -> "FakeCursor":
        self.calls.append((statement, params))
        if "schema_migrations" in statement:
            self.rows = ([{"filename": collector.RETRIEVAL_MIGRATION, "sha256": "a" * 64,
                           "applied_at": "2026-08-16T00:00:00Z"}]
                         if self.migration_present else [])
        elif "pg_get_functiondef" in statement:
            self.rows = [{"definition": "CREATE FUNCTION search_doctrine_situations() RETURNS void"}]
        elif "retrieval_ranking_policy" in statement:
            self.rows = [{"version": 7, "formula": "fixture", "config": {"formula": "fixture", "policy": params[0]},
                          "status": "active", "is_default": params[0] == "coequal-normalized-v1",
                          "golden_suite_digest": "b" * 64}]
        elif "from doctrine_section" in statement:
            section_ids = params[0]
            assert isinstance(section_ids, list)
            self.rows = [{"section_id": section_id, "status": self.source_status, "current_revision_id": "revision-1",
                          "revision_id": "revision-1", "document_slug": "runbook", "visibility": "shared"}
                         for section_id in section_ids]
        else:
            assert "search_doctrine_situations" in statement
            self.rows = self.rows_by_policy[str(params[-1])]
        return self

    def fetchone(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class FakeConnection:
    def __init__(self, rows_by_policy: dict[str, list[dict[str, object]]]) -> None:
        self.cursor_instance = FakeCursor(rows_by_policy)
        self.transaction_statements: list[str] = []
        self.rollback_count = 0

    def execute(self, statement: str) -> None:
        self.transaction_statements.append(statement)

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rollback_count += 1


def row(policy: str) -> dict[str, object]:
    return {
        "section_id": str(uuid.uuid4()),
        "doc_slug": "runbook",
        "section_key": "diagnosis-checklist",
        "lexical_score": 0.4,
        "concept_score": 0.8,
        "final_score": 0.9,
        "provenance": {
            "complete": True,
            "policy_id": policy,
            "policy_version": 7,
            "lexical_score": 0.4,
            "concept_score": 0.8,
            "final_score": 0.9,
            "phrase_ids": ["phrase-1"],
            "concept_ids": ["concept-1"],
            "mapping_ids": ["mapping-1"],
        },
    }


suite = {
    "schema_version": collector.SUITE_SCHEMA,
    "suite_id": "selftest-suite",
    "data_class": "D2_internal_metadata",
    "scope_ref": "carr-internal",
    "cases": [{"id": "RET-SELF", "required_targets": ["runbook#diagnosis-checklist"], "top_k": 3}],
}
golden = {
    "schema_version": collector.GOLDEN_SCHEMA,
    "data_class": "D2_internal_metadata",
    "cases": [{"id": "RET-SELF", "query": "already versioned fixture query", "engines": {
        "doctrine_postgres_fts": {"required_targets": ["runbook#diagnosis-checklist"], "top_k": 3},
    }}],
}
queries = collector.bind_case_queries(suite, golden)
rows = {policy: [row(policy)] for policy in collector.POLICIES}
connection = FakeConnection(rows)
observations = collector.collect_rollback_only(
    connection, suite, queries, environment="staging",
    fixture_digests={"situation_suite": collector.canonical_digest(suite), "golden_queries": collector.canonical_digest(golden)},
    collector_sha="c" * 40,
    observed_at="2026-08-16T00:00:00+00:00",
)

check("collector starts exactly one explicit repeatable-read transaction",
      connection.transaction_statements == ["begin transaction isolation level repeatable read"])
check("collector always rolls back canonical query-log writes on success", connection.rollback_count == 1)
check("collector emits both shipped policies", set(observations) == set(collector.POLICIES))
for policy, observation in observations.items():
    hit = observation["cases"]["RET-SELF"][0]
    check(f"{policy} is measured only after checks", observation["status"] == "measured")
    check(f"{policy} binds canonical source", observation["source"] == "search_doctrine_situations")
    check(f"{policy} binds current source", hit["current"] is True and hit["target"] == "runbook#diagnosis-checklist")
    check(f"{policy} carries complete provenance", hit["provenance"]["complete"] is True)
    check(f"{policy} binds provenance section ID", hit["provenance"]["section_id"] == hit["source"]["section_id"])
    check(f"{policy} binds policy metadata", observation["policy_version"] == 7 and len(observation["policy_config_digest"]) == 64
          and observation["policy_formula"] == "fixture" and observation["policy_status"] == "active")
    check(f"{policy} binds both D2 fixture digests", set(observation["fixture_digests"]) == {"situation_suite", "golden_queries"})
    check(f"{policy} binds applied migration and exact ranker body", observation["database_evidence"]["schema_migration"]["filename"] == collector.RETRIEVAL_MIGRATION
          and len(observation["database_evidence"]["ranker_definition_sha256"]) == 64)
    check(f"{policy} binds immutable collector revision", observation["collector_git_sha"] == "c" * 40)
    check(f"{policy} artifact does not disclose query text", "already versioned fixture query" not in json.dumps(observation))

calls = connection.cursor_instance.calls
search_calls = [(sql, params) for sql, params in calls if "search_doctrine_situations" in sql]
check("collector invokes canonical ranker twice for every policy/case", len(search_calls) == len(collector.POLICIES) * 2)
check("collector binds each shipped policy explicitly", {params[-1] for _, params in search_calls} == set(collector.POLICIES))

bad_rows = {policy: [row(policy)] for policy in collector.POLICIES}
bad_provenance = bad_rows["lexical-dominant-v1"][0]["provenance"]
assert isinstance(bad_provenance, dict)
bad_provenance["complete"] = False
failed_connection = FakeConnection(bad_rows)
try:
    collector.collect_rollback_only(
        failed_connection, suite, queries, environment="staging",
        fixture_digests={"situation_suite": "a" * 64, "golden_queries": "b" * 64},
        collector_sha="c" * 40,
    )
    incomplete_provenance_refused = False
except collector.CollectorRefusal:
    incomplete_provenance_refused = True
check("incomplete canonical provenance refuses measured status", incomplete_provenance_refused)
check("collector rolls back canonical query-log writes on error", failed_connection.rollback_count == 1)

noncurrent_connection = FakeConnection({policy: [row(policy)] for policy in collector.POLICIES})
noncurrent_connection.cursor_instance.source_status = "retired"
try:
    collector.collect_rollback_only(
        noncurrent_connection, suite, queries, environment="staging",
        fixture_digests={"situation_suite": "a" * 64, "golden_queries": "b" * 64}, collector_sha="c" * 40,
    )
    noncurrent_refused = False
except collector.CollectorRefusal:
    noncurrent_refused = True
check("retired or non-current source refuses measured status", noncurrent_refused)
check("non-current source error also rolls back", noncurrent_connection.rollback_count == 1)

missing_migration_connection = FakeConnection({policy: [row(policy)] for policy in collector.POLICIES})
missing_migration_connection.cursor_instance.migration_present = False
try:
    collector.collect_rollback_only(
        missing_migration_connection, suite, queries, environment="staging",
        fixture_digests={"situation_suite": "a" * 64, "golden_queries": "b" * 64}, collector_sha="c" * 40,
    )
    missing_migration_refused = False
except collector.CollectorRefusal:
    missing_migration_refused = True
check("missing 0135 ledger evidence refuses measured status", missing_migration_refused)

for environment, dsn, env in (("production", "postgresql://safe.example/db", {}),
                              ("explicit", "postgresql://safe.example/db", {}),
                              ("staging", "postgresql://production.example/db", {}),
                              ("staging", "postgresql://safe.example/db", {"CARR_ENV": "production"})):
    try:
        collector.refuse_unsafe_target(environment, dsn, env)
        refused = False
    except collector.CollectorRefusal:
        refused = True
    check(f"unsafe target {environment}/{dsn} is refused", refused)

bad_suite = {**suite, "cases": [{"id": "RET-SELF", "required_targets": ["wrong"], "top_k": 3}]}
try:
    collector.bind_case_queries(bad_suite, golden)
    mismatch_refused = False
except ValueError:
    mismatch_refused = True
check("collector refuses a situation fixture whose expectations drift from golden", mismatch_refused)

with tempfile.TemporaryDirectory() as tmp:
    paths = collector.write_observations(pathlib.Path(tmp), observations)
    check("writer emits immutable timestamped SHA-bound observation artifacts",
          len(paths) == 2 and all(path.exists() and ".20260816T000000Z." in path.name and ("c" * 40) in path.name for path in paths))
    check("writer keeps version identity in payload, not forbidden filename tokens",
          all("-v1.measured" not in path.name for path in paths))
    try:
        collector.write_observations(pathlib.Path(tmp), observations)
        overwrite_refused = False
    except FileExistsError:
        overwrite_refused = True
    check("writer refuses evidence overwrite", overwrite_refused)

with tempfile.TemporaryDirectory() as tmp:
    for invalid in ("not-a-sha", "A" * 40):
        try:
            collector.write_observations(pathlib.Path(tmp), {
                "lexical-dominant-v1": {**observations["lexical-dominant-v1"], "collector_git_sha": invalid},
            })
            sha_refused = False
        except collector.CollectorRefusal:
            sha_refused = True
check("writer refuses invalid collector SHA", sha_refused)


git_calls: list[list[str]] = []


def clean_git(_repo: pathlib.Path, args: list[str]) -> SimpleNamespace:
    git_calls.append(args)
    if args[:2] == ["rev-parse", "HEAD"]:
        return SimpleNamespace(returncode=0, stdout="d" * 40 + "\n")
    return SimpleNamespace(returncode=0, stdout="")


check("clean HEAD identity is accepted", collector.collector_git_sha(pathlib.Path("/isolated"), clean_git) == "d" * 40)
check("identity check scopes git only to collector and default fixtures",
      all("status" not in args for args in git_calls)
      and {args[-1] for args in git_calls if args[:2] == ["diff", "--quiet"]}
      == {path.as_posix() for path in collector.IDENTITY_PATHS})


def dirty_scoped_git(_repo: pathlib.Path, args: list[str]) -> SimpleNamespace:
    if args[:2] == ["rev-parse", "HEAD"]:
        return SimpleNamespace(returncode=0, stdout="d" * 40 + "\n")
    if args[:2] == ["diff", "--quiet"] and args[-1] == collector.COLLECTOR_REL_PATH.as_posix():
        return SimpleNamespace(returncode=1, stdout="")
    return SimpleNamespace(returncode=0, stdout="")


try:
    collector.collector_git_sha(pathlib.Path("/isolated"), dirty_scoped_git)
    dirty_scope_refused = False
except collector.CollectorRefusal:
    dirty_scope_refused = True
check("dirty collector scope refuses a misleading HEAD claim", dirty_scope_refused)


def untracked_fixture_git(_repo: pathlib.Path, args: list[str]) -> SimpleNamespace:
    if args[:2] == ["rev-parse", "HEAD"]:
        return SimpleNamespace(returncode=0, stdout="d" * 40 + "\n")
    if args[:2] == ["cat-file", "-e"] and args[-1].endswith(collector.DEFAULT_SUITE_REL_PATH.as_posix()):
        return SimpleNamespace(returncode=1, stdout="")
    return SimpleNamespace(returncode=0, stdout="")


try:
    collector.collector_git_sha(pathlib.Path("/isolated"), untracked_fixture_git)
    untracked_fixture_refused = False
except collector.CollectorRefusal:
    untracked_fixture_refused = True
check("untracked default fixture refuses a misleading HEAD claim", untracked_fixture_refused)

print("PASS: measured situation retrieval observation self-test")
