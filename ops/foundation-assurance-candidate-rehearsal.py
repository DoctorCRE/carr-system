#!/usr/bin/env python3
"""WR95 staging containment rehearsal around the existing deploy wrapper.

The script is deliberately narrow: exact current/prior commits, staging only,
the existing typed recovery step implementation, live readback, and immutable
before/after state. It never accepts evidence, pass state, provider IDs, or
measurements from its caller.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_PATH = ROOT / "tools" / "staging-recovery-rehearsal.py"
PROVISION_PATH = ROOT / "tools" / "provision-staging-app-writer.py"
EXACT_SHA = set("0123456789abcdef")
CHECKS = (
    "ops/ci.sh --strict",
    "local-db-ci --class migration",
    "main canary",
)
HTTP_USER_AGENT = "DoctorCRE-WR95-Rehearsal/1.0"


class RehearsalError(RuntimeError):
    pass


def load_recovery_module():
    spec = importlib.util.spec_from_file_location("carr_staging_recovery", RECOVERY_PATH)
    if spec is None or spec.loader is None:
        raise RehearsalError("staging recovery controller is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def staging_host() -> str:
    return subprocess.check_output(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "tools/ops-record.py"),
         "staging-target", "--field", "host"], cwd=ROOT, text=True
    ).strip()


def request_json(url: str, *, token: str | None = None,
                 payload: dict | None = None) -> dict:
    headers = {"accept": "application/json", "user-agent": HTTP_USER_AGENT}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers=headers), timeout=30
        ) as response:
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise RehearsalError(f"staging request failed: {type(exc).__name__}") from exc


def release_readback(origin: str, sha: str) -> dict:
    body = request_json(origin + "/release")
    if (body.get("git_sha") or {}).get("value") != sha \
            or (body.get("env") or {}).get("value") != "staging":
        raise RehearsalError("staging release readback differs from the exact rehearsal step")
    provider = (body.get("worker_version") or {}).get("id")
    if not isinstance(provider, str) or len(provider) != 36:
        raise RehearsalError("staging release readback has no immutable provider UUID")
    return {"git_sha": sha, "provider_version": provider}


def mcp_call(origin: str, token: str, name: str) -> dict:
    return request_json(origin + "/mcp", token=token, payload={
        "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    })


def assert_prior_containment(origin: str, token: str) -> dict:
    old = mcp_call(origin, token, "standing-context")
    if old.get("error") or (old.get("result") or {}).get("isError"):
        raise RehearsalError("prior Worker failed an existing read-only verb")
    refused = mcp_call(origin, token, "record-foundation-assurance-minimum-outcome")
    text = json.dumps(refused).lower()
    if not (refused.get("error") or (refused.get("result") or {}).get("isError")) \
            or "unknown" not in text:
        raise RehearsalError("WR95 issuance was reachable on the prior Worker")
    return {"old_verb": "pass", "wr95_issuance": "unreachable"}


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


def staging_snapshot(snapshot_dsn: str, *, connect=psycopg.connect) -> dict:
    sql = """select jsonb_build_object(
 'gate_zero_outcomes',(select count(*) from ops.gate_zero_read_only_outcome),
 'foundation_evidence',(select count(*) from ops.foundation_assurance_evidence),
 'foundation_production',(select count(*) from ops.foundation_assurance_production),
 'j1_clocks',(select count(*) from ops.j1_clock),
 'j1_revisions',(select count(*) from ops.j1_clock_revision),
 'j1_inventory',(select count(*) from ops.j1_minimum_inventory),
 'j1_admissions',(select count(*) from ops.j1_minimum_admission));
"""
    try:
        conn = connect(snapshot_dsn)
        try:
            cur = conn.cursor()
            cur.execute("begin transaction read only")
            cur.execute("select session_user,current_user")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner"):
                raise RehearsalError("candidate snapshot owner identity differs")
            cur.execute(sql)
            row = cur.fetchone()
            conn.rollback()
        finally:
            conn.close()
    except RehearsalError:
        raise
    except Exception as exc:
        raise RehearsalError("staging invariant snapshot was unavailable") from exc
    if not row or not isinstance(row[0], dict):
        raise RehearsalError("staging invariant snapshot was unavailable")
    return row[0]


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


def hosted_checks(sha: str) -> list[dict]:
    raw = subprocess.check_output(
        ["gh", "api", "--paginate", f"repos/jbookout/carr-system/commits/{sha}/check-runs"],
        cwd=ROOT, text=True
    )
    pages = [json.loads(line) for line in raw.splitlines() if line.strip()]
    runs = [row for page in pages for row in page.get("check_runs", [])]
    result = []
    for name in CHECKS:
        matches = [row for row in runs if row.get("name") == name
                   and row.get("head_sha") == sha and row.get("status") == "completed"]
        selected = max(
            matches,
            key=lambda row: (row.get("completed_at") or "", int(row.get("id") or 0)),
            default=None,
        )
        if selected is None or selected.get("conclusion") != "success":
            raise RehearsalError(f"hosted check unavailable: {name}")
        result.append({"name": name, "run_id": selected.get("id"), "conclusion": "success"})
    return result


def compact(receipt: dict) -> dict:
    return {key: receipt.get(key) for key in
            ("step", "sha", "idempotency_key", "exit_code") if key in receipt}


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-key")
    parser.add_argument("--prior-release-key")
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--prior-sha")
    parser.add_argument("--candidate-operation-id", required=True)
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--read-foundation-facts", action="store_true")
    args = parser.parse_args(argv)
    if args.environment != "staging":
        raise RehearsalError("candidate rehearsal is structurally staging-only")
    current = exact_sha(args.current_sha)
    if args.read_foundation_facts:
        if args.execute or args.release_key or args.prior_release_key:
            raise RehearsalError("foundation facts mode accepts only immutable staging bindings")
        provision = load_provision_module()
        snapshot_dsn = candidate_snapshot_dsn(
            provision, args.candidate_operation_id, args.receipt_id, current)
        print(json.dumps(foundation_assurance_facts(snapshot_dsn), sort_keys=True))
        return 0
    if not args.release_key or not args.prior_release_key or not args.prior_sha:
        raise RehearsalError("candidate rehearsal requires current/prior release keys and prior SHA")
    prior = exact_sha(args.prior_sha)
    if not args.execute:
        print(json.dumps({"ok": True, "planned": True, "environment": "staging",
                          "current_sha": current, "prior_sha": prior,
                          "steps": ["current_before", "prior", "current_after"]}))
        return 0
    token = os.environ.get("CARR_WR95_STAGING_REVIEW_TOKEN", "")
    if not token:
        raise RehearsalError("CARR_WR95_STAGING_REVIEW_TOKEN is required")
    recovery = load_recovery_module()
    provision = load_provision_module()
    snapshot_dsn = candidate_snapshot_dsn(
        provision, args.candidate_operation_id, args.receipt_id, current)
    origin = "https://" + staging_host()
    before = staging_snapshot(snapshot_dsn)
    checks = hosted_checks(current)
    attempt = str(uuid.uuid4())
    receipts: list[dict] = []
    restored = False
    try:
        for step, sha in (("current_before", current), ("prior", prior),
                          ("current_after", current)):
            receipt = recovery.execute_step(step, sha, args, attempt, str(uuid.uuid4()))
            receipts.append(compact(receipt))
            if receipt.get("exit_code"):
                raise RehearsalError(f"typed staging step failed: {step}")
            release_readback(origin, sha)
            if step == "prior":
                containment = assert_prior_containment(origin, token)
            if step == "current_after":
                restored = True
        final = release_readback(origin, current)
        after = staging_snapshot(snapshot_dsn)
        if after != before:
            raise RehearsalError("Gate Zero, foundation, or Journey One state changed during rehearsal")
    except Exception:
        if not restored:
            repair = recovery.execute_step(
                "restore_only", current, args, attempt, str(uuid.uuid4()))
            receipts.append(compact(repair))
        raise
    print(json.dumps({"ok": True, "environment": "staging",
                      "recovery_attempt_id": attempt, "hosted_checks": checks,
                      "containment": containment, "invariants": after,
                      "staging_provider_version": final["provider_version"],
                      "receipts": receipts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(sys.argv[1:]))
    except RehearsalError as exc:
        print(f"foundation-assurance-candidate-rehearsal: {exc}", file=sys.stderr)
        raise SystemExit(1)
