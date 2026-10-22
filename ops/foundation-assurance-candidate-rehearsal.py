#!/usr/bin/env python3
"""Read-only foundation assurance evidence from an exact receipted staging replacement."""

from __future__ import annotations
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import psycopg


ROOT = Path(__file__).resolve().parents[1]
PROVISION_PATH = ROOT / "lib" / "foundation_assurance_binding.py"
EXACT_SHA = set("0123456789abcdef")


class RehearsalError(RuntimeError):
    pass


def load_provision_module():
    spec = importlib.util.spec_from_file_location("carr_staging_provision", PROVISION_PATH)
    if spec is None or spec.loader is None:
        raise RehearsalError("staging candidate binding is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def exact_sha(value: str) -> str:
    if len(value) != 40 or any(ch not in EXACT_SHA for ch in value):
        raise RehearsalError("candidate rehearsal requires exact lowercase commit SHAs")
    try:
        resolved = subprocess.check_output(
            ["git", "rev-parse", "--verify", value + "^{commit}"], cwd=ROOT,
            text=True, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise RehearsalError("candidate rehearsal commit is unavailable") from exc
    if resolved != value:
        raise RehearsalError("candidate rehearsal commit did not resolve exactly")
    return value


def candidate_snapshot_dsn(provision, operation_id: str, receipt_id: str, sha: str) -> str:
    try:
        target = provision.replacement_target(operation_id, receipt_id, sha)
        binding = provision.resolve_replacement_binding(target, environ=os.environ)
    except Exception as exc:
        raise RehearsalError(
            "exact staging replacement snapshot binding refused; output suppressed"
        ) from exc
    if (
        binding.owner.role_name != provision.replacement.OWNER_ROLE
        or binding.owner.endpoint != binding.candidate.endpoint_host
        or binding.owner.database != "neondb"
    ):
        raise RehearsalError("exact staging replacement snapshot scope differs")
    return binding.owner.value


def foundation_assurance_facts(snapshot_dsn: str, *, connect=psycopg.connect) -> dict:
    """Acquire the WR95 database comparators from the receipted replacement."""
    sql = """select jsonb_build_object(
 'migration',(select max(filename collate "C") from public.schema_migrations),
 'registry_current',ops.scac_mutation_catalog_v35_current(),
 'oracle_role',exists(select 1 from pg_roles where rolname='carr_foundation_assurance_oracle'
   and rolcanlogin and not rolsuper and not rolcreaterole and not rolcreatedb and not rolbypassrls),
 'oracle_functions',(select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname='ops' and p.proname in ('foundation_assurance_store_evidence',
     'foundation_assurance_producer_material','foundation_assurance_record_production')),
 'journey_one_outcomes',(select count(*) from ops.foundation_assurance_production
   where kind='minimum_outcome'),
 'raw_table_write',has_table_privilege('carr_foundation_assurance_oracle',
   'ops.foundation_assurance_evidence','insert,update,delete,truncate'));
"""
    try:
        conn = connect(snapshot_dsn)
        try:
            cur = conn.cursor()
            cur.execute("begin transaction read only")
            cur.execute("select session_user,current_user")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner"):
                raise RehearsalError("candidate facts owner identity differs")
            cur.execute(sql)
            row = cur.fetchone()
            conn.rollback()
        finally:
            conn.close()
    except RehearsalError:
        raise
    except Exception as exc:
        raise RehearsalError("staging database evidence was unavailable") from exc
    if not row or not isinstance(row[0], dict):
        raise RehearsalError("staging database evidence was unavailable")
    return row[0]




def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Read foundation facts from an exact receipted staging replacement")
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--candidate-operation-id", required=True)
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--environment", choices=["staging"], default="staging")
    parser.add_argument("--read-foundation-facts", action="store_true", required=True)
    args = parser.parse_args(argv)
    current = exact_sha(args.current_sha)
    provision = load_provision_module()
    snapshot_dsn = candidate_snapshot_dsn(
        provision, args.candidate_operation_id, args.receipt_id, current)
    print(json.dumps(foundation_assurance_facts(snapshot_dsn), sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(run(sys.argv[1:]))
    except RehearsalError as exc:
        print(f"foundation-assurance-candidate-rehearsal: {exc}", file=sys.stderr)
        raise SystemExit(1)
