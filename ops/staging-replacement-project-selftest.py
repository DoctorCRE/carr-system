#!/usr/bin/env python3
"""Static fail-closed checks for the clean staging replacement DB contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = tuple((ROOT / "migrations").glob("*_clean_staging_replacement_contract.sql"))
if len(MIGRATIONS) != 1:
    raise SystemExit("expected exactly one allocated clean staging replacement migration")
MIGRATION = MIGRATIONS[0]
GATE = ROOT / "ops" / "staging-replacement-project-local-pg-gate.py"

sql = MIGRATION.read_text()
gate = GATE.read_text()
checks = {
    "compact public signatures": all(text in sql for text in (
        "ops.prepare_staging_replacement_project(\n  p_idempotency_key uuid, p_contract jsonb",
        "ops.record_staging_replacement_project(\n  p_idempotency_key uuid, p_observation jsonb",
        "ops.read_staging_replacement_project_receipt(\n  p_receipt_id uuid",
    )),
    "strict full-tree contract": "tree_mode='full'" in sql
        and "missing or unknown keys" in sql
        and "held_back" not in sql,
    "allocated filename is not contract data": "ledger ? '" not in sql
        and "migration_highest'] !=" not in gate,
    "exact source tree identity": all(field in sql for field in (
        "source_tree_oid", "source_tree_sha256", "source_tree_entry_count",
    )),
    "server-owned Production boundary": "steep-field-48688294" in sql
        and "production_project_id" not in gate.split("contract = {", 1)[1].split("}", 1)[0],
    "complete live ledger comparison": "from public.schema_migrations" in sql
        and "live_ledger is distinct from contract.migration_ledger" in sql
        and "live_count is distinct from contract.migration_count" in sql
        and "live_highest is distinct from contract.migration_highest" in sql,
    "synthetic count is server-derived": all(table in sql for table in (
        "select count(*) from public.party", "select count(*) from public.client",
        "select count(*) from public.deal", "select count(*) from public.lead",
        "select count(*) from public.vendor",
    )),
    "cross-project boundary is explicit": "governed G1 table-ID comparison" in sql,
    "append-only evidence": sql.count("ops.refuse_program5_evidence_mutation()") >= 2,
    "least privilege and strict sessions": all(text in sql for text in (
        "session_user<>'carr_jobs'",
        "session_user<>'carr_program5_forward_fix_verifier'",
        "carr_program5_forward_fix_verifiers",
        "revoke all on ops.staging_replacement_project_contract",
    )),
    "DB gate covers ledger holes extras and drift": all(text in gate for text in (
        "extra live migration was accepted", "changed live migration content was accepted",
        "missing live migration was accepted",
    )),
    "merged migrations are consumed from the full live ledger":
        "select filename,sha256 from public.schema_migrations" in gate
        and "highest = rows[-1][0]" in gate
        and "migration_count\": len(rows)" in gate,
    "DB gate covers isolation and idempotency": all(text in gate for text in (
        "nonzero Production overlap was accepted", "bounded migration tree was accepted",
        "prepare is not exact-replay idempotent", "record is not exact-replay idempotent",
    )),
}

failures = [name for name, passed in checks.items() if not passed]
for name, passed in checks.items():
    print(f"{'ok' if passed else 'FAIL'}  {name}")
if failures:
    raise SystemExit("staging replacement selftest failed: " + ", ".join(failures))
print("staging-replacement-project-selftest: PASS")
