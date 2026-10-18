#!/usr/bin/env python3
"""Static contract checks for migration 0301's release schema provenance gate."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration



REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "0301_release_schema_provenance_gate.sql"
COLLATION_REPAIR = REPO / "migrations" / "0302_release_schema_provenance_collation.sql"
CANARY = REPO / "ops" / "release-schema-provenance-canary.sql"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("release-schema-provenance-selftest: 0301 database gate")
    check("1. migration exists", MIGRATION.name in migration_names(MIGRATION.parent.parent))
    sql = read_migration(MIGRATION.parent.parent, MIGRATION.name) if MIGRATION.name in migration_names(MIGRATION.parent.parent) else ""

    check("2. derived provenance view exposes declared and live evidence",
          "ops.v_release_schema_provenance" in sql
          and "schema_declaration_matches_live" in sql
          and "schema_status" in sql
          and "schema_evidence" in sql
          and "public.schema_migrations" in sql
          and "r.schema_ledger_sha256" in sql
          and "live.ledger_sha256" in sql)
    check("3. Production approval compares exact highest migration and count",
          "new.environment = 'production' and new.state = 'approved'" in sql
          and "new.schema_highest_migration is distinct from live_highest" in sql
          and "new.schema_applied_count <> live_count" in sql
          and "new.schema_ledger_sha256 is distinct from live_digest" in sql
          and "schema_ledger_sha256 on ops.release" in sql
          and "raise exception" in sql)
    check("4. mismatch error carries source-linked evidence",
          "'evidence_source', 'public.schema_migrations'" in sql
          and "'declared_schema_highest_migration'" in sql
          and "'live_schema_highest_migration'" in sql
          and "'declared_schema_ledger_sha256'" in sql
          and "'live_schema_ledger_sha256'" in sql
          and "'Production release % schema declaration does not match live ops schema truth',\n        new.release_key" in sql)
    check("4a. same highest/count but a different digest is refused",
          "new.schema_applied_count <> live_count\n       or new.schema_ledger_sha256 is distinct from live_digest" in sql)
    check("4b. the Python NUL/newline ledger preimage is reproduced as bytea",
          "decode('00', 'hex')" in sql
          and "decode('0a', 'hex')" in sql
          and "''::bytea order by filename" in sql
          and "chr(0)" not in sql)
    check("5. completed releases are append-only on update and delete",
          "ops.completed_release_append_only()" in sql
          and "ops.completed_release_delete_refused()" in sql
          and "old.state = 'complete'" in sql
          and "before update on ops.release" in sql
          and "before delete on ops.release" in sql)
    check("6. migration contains proof that all gate objects exist",
          "0301 FAILED" in sql
          and "to_regclass('ops.v_release_schema_provenance')" in sql
          and "to_regprocedure('ops.release_schema_declaration_matches_live()')" in sql)
    check("7. canonical generated snapshot is not hand-edited by this migration",
          "db/schema.sql" not in sql)
    check("8. provenance view is reader-only",
          "grant select on ops.v_release_schema_provenance to carr_reader;" in sql
          and "carr_writer, carr_jobs" not in sql)
    canary = CANARY.read_text(encoding="utf-8") if CANARY.is_file() else ""
    check("9. transactional live canary proves exact match and digest refusal",
          CANARY.is_file()
          and "create temp table release_schema_provenance_probe" in canary
          and "'ca06-match', 'production', 'approved'" in canary
          and "'ca06-mismatch', 'production', 'approved'" in canary
          and "digest mismatch was accepted" in canary
          and "'ca06-highest-mismatch', 'production', 'approved'" in canary
          and "highest-migration mismatch was accepted" in canary
          and "'ca06-count-mismatch', 'production', 'approved'" in canary
          and "applied-count mismatch was accepted" in canary
          and "'ca06-update-mismatch', 'production', 'candidate'" in canary
          and "UPDATE approval mismatch was accepted" in canary
          and "completed release UPDATE was accepted" in canary
          and "completed release DELETE was accepted" in canary
          and "ops.v_release_schema_provenance" in canary
          and "rollback;" in canary)
    repair = (read_migration(COLLATION_REPAIR.parent.parent, COLLATION_REPAIR.name)
              if COLLATION_REPAIR.is_file() else "")
    check("10. forward-only repair makes Python/SQL ordering locale-stable",
          COLLATION_REPAIR.is_file()
          and 'max(filename collate "C") collate "default"' in repair
          and 'order by filename collate "C"' in repair
          and "pg_get_viewdef" in repair
          and "pg_get_functiondef" in repair
          and "0301 is applied history and is not edited" in repair)

    print()
    if FAILURES:
        print(f"release-schema-provenance-selftest: {len(FAILURES)} FAILED")
        return 1
    print("release-schema-provenance-selftest: 0301 contract holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
