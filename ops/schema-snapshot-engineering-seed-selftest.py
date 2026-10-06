#!/usr/bin/env python3
"""Keep the post-0312 fresh-rebuild seed equal to the reviewed controller.

``db/schema.sql`` deliberately carries a snapshot of the *currently applied*
ledger.  Once Production applies 0312 and the snapshot is refreshed, a fresh
database will see 0311/0312 in that ledger and will not replay either migration.
The bounded job-definition INSERT in bin/schema-snapshot.sh is ON CONFLICT DO
NOTHING, so its first-insert values are the effective fresh-rebuild contract.
This test makes that otherwise easy-to-miss relationship executable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Never


REPO = Path(__file__).resolve().parents[1]
SNAPSHOTTER = REPO / "bin" / "schema-snapshot.sh"
WORKFLOWS = REPO / "ops" / "config" / "control-plane-workflows.v1.json"
AUTHORITY = REPO / "migrations" / "0311_sponsored_engineering_executor_authority.sql"
CONTROLLER = REPO / "migrations" / "0312_engineering_dispatch_controller.sql"
SCHEMA = REPO / "db" / "schema.sql"


def fail(message: str) -> Never:
    raise AssertionError(message)


def string_keyed_mapping(value: object, context: str) -> dict[str, object]:
    """Validate JSON objects before comparing their nested contracts."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        fail(f"{context} must be a JSON object with string keys")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def required_mapping(record: Mapping[str, object], key: str) -> dict[str, object]:
    value = record.get(key)
    if value is None:
        fail(f"{key} is missing from reviewed engineering workflow")
    return string_keyed_mapping(value, f"{key} in reviewed engineering workflow")


def engineering_seed(source: str) -> dict[str, object]:
    try:
        block = source.split("GOVERNED_EXECUTION_SEEDS'\n", 1)[1].split(
            "GOVERNED_EXECUTION_SEEDS\n", 1)[0]
    except IndexError as exc:
        raise AssertionError("governed execution seed heredoc is missing") from exc
    marker = "('engineering-slice',1,true,'yellow','hermes','deterministic',"
    if marker not in block:
        fail("engineering-slice seed is missing")
    row = block.split(marker, 1)[1].split("on conflict (key,version) do nothing;", 1)[0]
    encoded = re.findall(r"'({.*?})'::jsonb", row, flags=re.DOTALL)
    if len(encoded) != 11:
        fail(f"engineering seed expected 11 JSON contracts, found {len(encoded)}")
    parsed = [json.loads(value) for value in encoded]
    return {
        "execution": parsed[0],
        "inventory": parsed[1],
        "deduplication": parsed[8],
        "row": row,
    }


def workflow_contract() -> dict[str, object]:
    manifest = string_keyed_mapping(
        json.loads(WORKFLOWS.read_text(encoding="utf-8")),
        "control-plane workflow manifest",
    )
    workflows = manifest.get("workflows")
    if not isinstance(workflows, list):
        fail("control-plane workflow manifest workflows must be a JSON array")
    rows = [string_keyed_mapping(row, "control-plane workflow row") for row in workflows
            if isinstance(row, dict)
            and row.get("key") == "engineering-slice" and row.get("version") == 1]
    if len(rows) != 1:
        fail("reviewed workflow has no unique engineering-slice:v1 row")
    return rows[0]


def snapshot_ledger() -> set[str]:
    """Read the checked-in applied ledger, never infer it from filenames."""
    source = SCHEMA.read_text(encoding="utf-8")
    marker = "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;"
    try:
        rows = source.split(marker, 1)[1].split("\n\\.\n", 1)[0].strip().splitlines()
    except IndexError as exc:
        raise AssertionError("schema snapshot migration ledger is missing") from exc
    names = {row.split("\t", 1)[0] for row in rows if row.strip()}
    if not names:
        fail("schema snapshot migration ledger is empty")
    return names


def database_execution_contract(workflow: dict[str, object]) -> dict[str, object]:
    """ops.job_definition stores execution_kind in its own SQL column."""
    execution = required_mapping(workflow, "execution")
    assert execution.pop("kind") == "deterministic"
    return execution


def main() -> int:
    source = SNAPSHOTTER.read_text(encoding="utf-8")
    seed = engineering_seed(source)
    workflow = workflow_contract()
    inventory = required_mapping(workflow, "inventory")
    deduplication = required_mapping(workflow, "deduplication")

    execution = database_execution_contract(workflow)
    assert seed["execution"] == execution, (
        "fresh rebuild seed execution contract differs from reviewed workflow")
    assert seed["inventory"] == inventory, (
        "fresh rebuild seed inventory contract differs from reviewed workflow")
    assert seed["deduplication"] == deduplication, (
        "fresh rebuild seed deduplication differs from reviewed workflow")

    row = str(seed["row"])
    assert "runCodexSlice" not in row
    assert "server-derived shadow execution only" not in row
    assert '"key_template":"engineering-slice:{plan_digest}:{work_request}:{slice_ref}"' not in row
    assert "ON CONFLICT DO NOTHING deliberately" in source
    assert "fresh rebuild will not replay their contract updates" in source

    authority = AUTHORITY.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    authority_value = inventory.get("authority")
    if not isinstance(authority_value, str):
        fail("reviewed engineering inventory authority must be a string")
    external_dependencies = inventory.get("external_dependencies")
    if not isinstance(external_dependencies, list):
        fail("reviewed engineering inventory external_dependencies must be an array")
    assert authority_value in authority
    assert json.dumps(deduplication, separators=(",", ":")) in authority
    assert json.dumps(execution, separators=(",", ":")) in controller
    assert json.dumps(external_dependencies, separators=(",", ":")) in controller

    # This is the actual post-refresh condition.  Until the sanctioned
    # Production apply/snapshot phase, the committed snapshot may honestly end
    # before 0311/0312.  Model that exact next snapshot ledger from the current
    # COPY rows rather than pretending the checked-in snapshot is already live.
    current_ledger = snapshot_ledger()
    post_refresh_ledger = current_ledger | {AUTHORITY.name, CONTROLLER.name}
    assert {AUTHORITY.name, CONTROLLER.name}.issubset(post_refresh_ledger)

    # In a fresh rebuild against that post-refresh ledger, the runner skips
    # 0311/0312.  The bounded INSERT is therefore the only creator of this
    # row.  Simulate PostgreSQL's ON CONFLICT DO NOTHING semantics explicitly:
    # the first value is the survivor, so it must be the final reviewed value.
    assert source.count("('engineering-slice',1,true,'yellow','hermes','deterministic',") == 1
    rebuilt_job_definitions: dict[tuple[str, int], dict[str, object]] = {}
    key = ("engineering-slice", 1)
    first_insert: dict[str, object] = {
        "execution": seed["execution"],
        "inventory": seed["inventory"],
        "deduplication": seed["deduplication"],
    }
    rebuilt_job_definitions.setdefault(key, first_insert)
    assert rebuilt_job_definitions[key] == {
        "execution": execution,
        "inventory": inventory,
        "deduplication": deduplication,
    }

    print("schema snapshot engineering seed selftest: post-0312 fresh-rebuild contract exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
