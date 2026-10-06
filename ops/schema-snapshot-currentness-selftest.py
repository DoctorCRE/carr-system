#!/usr/bin/env python3
"""Exact boundary tests for schema-snapshot production-currentness comparison."""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = ROOT / "ops" / "schema-snapshot-currentness.py"
GENERATOR = ROOT / "bin" / "schema-snapshot.sh"
EXACT_77 = "select pg_catalog.setval('ops.work_request_ref_seq', 77, true);"
EXACT_78 = "select pg_catalog.setval('ops.work_request_ref_seq', 78, true);"

SPEC = importlib.util.spec_from_file_location("schema_snapshot_currentness", COMPARATOR)
assert SPEC and SPEC.loader
currentness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(currentness)


def compare(left: str, right: str) -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        expected = root / "expected.sql"
        observed = root / "observed.sql"
        expected.write_text(left, encoding="utf-8")
        observed.write_text(right, encoding="utf-8")
        return 0 if currentness.snapshots_match(expected, observed) else 1


def snapshot(line: str = EXACT_77, suffix: str = "") -> str:
    return f"CREATE SCHEMA ops;\n{line}\nCOMMIT;\n{suffix}"


assert compare(snapshot(), snapshot(EXACT_78)) == 0, (
    "only the exact work-request sequence's numeric current value may drift"
)
assert compare(snapshot(), snapshot()) == 0

refusals = (
    (snapshot(), snapshot(EXACT_78, "-- unrelated drift\n")),
    (snapshot(), snapshot("select pg_catalog.setval('ops.other_seq', 78, true);")),
    (snapshot(), snapshot("select pg_catalog.setval('ops.work_request_ref_seq', 78, false);")),
    (snapshot(), snapshot("SELECT pg_catalog.setval('ops.work_request_ref_seq', 78, true);")),
    (snapshot(), snapshot("select pg_catalog.setval('ops.work_request_ref_seq',78,true);")),
    (snapshot(), "CREATE SCHEMA ops;\nCOMMIT;\n"),
    ("CREATE SCHEMA ops;\nCOMMIT;\n", "CREATE SCHEMA ops;\nCOMMIT;\n"),
    (snapshot() + EXACT_77 + "\n", snapshot() + EXACT_78 + "\n"),
    (
        snapshot() + "SELECT pg_catalog.setval('ops.work_request_ref_seq', 90, true);\n",
        snapshot(EXACT_78) + "SELECT pg_catalog.setval('ops.work_request_ref_seq', 90, true);\n",
    ),
    (
        snapshot() + "select  pg_catalog.setval( 'ops.work_request_ref_seq' , 90 , true );\n",
        snapshot(EXACT_78) + "select  pg_catalog.setval( 'ops.work_request_ref_seq' , 90 , true );\n",
    ),
)
for expected, observed in refusals:
    assert compare(expected, observed) == 1, (expected, observed)

generator = GENERATOR.read_text(encoding="utf-8")
assert 'CURRENTNESS_PY="$REPO/ops/schema-snapshot-currentness.py"' in generator
assert 'module.snapshots_match(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))' in generator

print("schema snapshot currentness selftest: exact volatile-value boundary pinned")
