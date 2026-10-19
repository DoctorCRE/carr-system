#!/usr/bin/env python3
"""Pin the bounded execution seeds carried by bin/schema-snapshot.sh.

0309 and 0310 are already in the snapshot ledger, so a fresh load must carry
the protected Hermes provider/conformance/lifecycle stream and the existing
engineering-slice job definition.  This guard intentionally checks only the
repository-declared identity and the generated snapshot markers; it never
permits arbitrary production execution rows into db/schema.sql.
"""
from pathlib import Path
import re
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "bin" / "schema-snapshot.sh"
SNAPSHOT = ROOT / "db" / "schema.sql"


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL':4} {label}")
    if not condition:
        raise AssertionError(label)


def main() -> int:
    generator = GENERATOR.read_text()
    snapshot = SNAPSHOT.read_text()
    check("generator declares the bounded execution seed block",
          "CARR GOVERNED EXECUTION SEEDS" in generator)
    for value in (
        "'hermes-local',1,'built_in','local'",
        "03090000-0000-4000-8000-000000000001",
        "03090000-0000-4000-8000-000000000002",
        "03090000-0000-4000-8000-000000000008",
    ):
        check(f"generator pins {value}", value in generator)
    declarations = json.loads((ROOT / "ops/config/snapshot-seed-coverage.json").read_text())["renders"]
    check("reviewed selectors carry both job contracts",
          {row["where"] for row in declarations if row["table"] == "ops.job_definition"} ==
          {"key='engineering-slice' and version=1", "key='renewal-radar-source-daily' and version=1"})
    check("snapshot carries the governed execution seed block",
          "CARR GOVERNED EXECUTION SEEDS" in snapshot)
    check("snapshot carries the protected Hermes provider",
          re.search(r"provider_key.*hermes-local|hermes-local.*provider", snapshot) is not None)
    check("snapshot carries the Engineering Passport job contract",
          "engineering-slice" in snapshot and "engineering-runtime.js" in snapshot)
    check("snapshot ledger includes 0309 and 0310",
          "0309_governed_execution_environment_providers.sql" in snapshot
          and "0310_engineering_execution_fabric.sql" in snapshot)
    print("schema snapshot execution-seed selftest: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError) as exc:
        print(f"schema snapshot execution-seed selftest: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
