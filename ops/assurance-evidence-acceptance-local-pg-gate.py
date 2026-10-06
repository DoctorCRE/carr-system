#!/usr/bin/env python3
# ci: runs-outside-ci — invoked by ops/local-pg-ci.py after the A2 ownership gate
# doctrine: runbook
"""Disposable-Postgres acceptance for immutable A3a assurance persistence."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Barrier, Lock, Thread
import time
from typing import TypedDict
import uuid

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0
FAILED_LABELS: list[str] = []
REFUSALS: dict[str, int] = {}
SECRET_TOKENS: set[str] = set()
RAW_RESULTS: list[object] = []
RAW_ERRORS: list[str] = []
RAW_LOCK = Lock()
class IssuerConfig(TypedDict, total=False):
    dsn: str
    tenant: str
    runtime_session: str
    host: str
    job_token: uuid.UUID
    ownership_session_ref: str


A3A_ISSUER: IssuerConfig = {}
EXPECTED_A3A_TABLES = [
    "ops.assurance_evidence_extension",
    "ops.assurance_execution_manifest",
    "ops.assurance_health_evidence",
    "ops.assurance_owner_acceptance_fact",
    "ops.assurance_review_extension",
]
EXPECTED_A3A_FUNCTIONS = sorted([
    "ops.assurance_all_tokens_absent(jsonb)",
    "ops.assurance_digest(jsonb)",
    "ops.assurance_exact_object(jsonb,text[])",
    "ops.assurance_health_basis(text)",
    "ops.assurance_health_evidence_immutable()",
    "ops.assurance_health_exact_keys(jsonb,text[])",
    "ops.assurance_health_instant(text)",
    "ops.assurance_health_label(text,integer,text,jsonb)",
    "ops.assurance_health_layers()",
    "ops.assurance_health_refs_valid(jsonb)",
    "ops.assurance_health_stage(text[])",
    "ops.assurance_identifier_valid(text)",
    "ops.assurance_lease_lineage_current(uuid,timestamp with time zone)",
    "ops.assurance_append_lineage_current(uuid,uuid,timestamp with time zone,uuid,bigint)",
    "ops.assurance_terminal_evidence_lineage_current(uuid,uuid,timestamp with time zone,uuid,bigint)",
    "ops.assurance_terminal_receipt_lineage_current(uuid,uuid,timestamp with time zone)",
    "ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid)",
    "ops.assurance_normalized_set(jsonb)",
    "ops.assurance_pinned_pointer(text)",
    "ops.assurance_pointer_value(jsonb,text)",
    "ops.assurance_refusal(text,text,jsonb,jsonb)",
    "ops.assurance_sorted_strings(jsonb)",
    "ops.assurance_text_token_absent(text,uuid)",
    "ops.assurance_timestamp_valid(text)",
    "ops.assurance_token_absent(jsonb,uuid)",
    "ops.assurance_unique_array(jsonb)",
    "ops.assurance_validate_compiler_input(uuid,jsonb,jsonb)",
    "ops.record_assurance_evidence_extension(uuid,uuid,uuid,jsonb,text,uuid)",
    "ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)",
    "ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid)",
    "ops.record_assurance_review_extension(uuid,uuid,uuid,jsonb,text,uuid)",
    "ops.refuse_assurance_persistence_rewrite()",
])
EXPECTED_A3A_FUNCTION_POSTURE = {
    "ops.assurance_all_tokens_absent(jsonb)": (True, "s", "search_path=pg_catalog, ops"),
    "ops.assurance_digest(jsonb)": (False, "i", "search_path=pg_catalog, ops, public"),
    "ops.assurance_exact_object(jsonb,text[])": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_health_basis(text)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_health_evidence_immutable()": (False, "v", "search_path=pg_catalog"),
    "ops.assurance_health_exact_keys(jsonb,text[])": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_health_layers()": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_health_refs_valid(jsonb)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_health_instant(text)": (False, "s", "search_path=pg_catalog"),
    "ops.assurance_health_label(text,integer,text,jsonb)": (False, "s", "search_path=pg_catalog, public, ops"),
    "ops.assurance_health_stage(text[])": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_identifier_valid(text)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_lease_lineage_current(uuid,timestamp with time zone)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.assurance_append_lineage_current(uuid,uuid,timestamp with time zone,uuid,bigint)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.assurance_terminal_evidence_lineage_current(uuid,uuid,timestamp with time zone,uuid,bigint)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.assurance_terminal_receipt_lineage_current(uuid,uuid,timestamp with time zone)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.assurance_normalized_set(jsonb)":
        (False, "i", "search_path=pg_catalog, ops, public"),
    "ops.assurance_pinned_pointer(text)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_pointer_value(jsonb,text)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_refusal(text,text,jsonb,jsonb)":
        (False, "i", "search_path=pg_catalog, ops"),
    "ops.assurance_sorted_strings(jsonb)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_text_token_absent(text,uuid)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_timestamp_valid(text)": (False, "i", "search_path=pg_catalog"),
    "ops.assurance_token_absent(jsonb,uuid)":
        (False, "i", "search_path=pg_catalog, ops"),
    "ops.assurance_unique_array(jsonb)": (False, "i", "search_path=pg_catalog, ops"),
    "ops.assurance_validate_compiler_input(uuid,jsonb,jsonb)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.record_assurance_evidence_extension(uuid,uuid,uuid,jsonb,text,uuid)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.record_assurance_review_extension(uuid,uuid,uuid,jsonb,text,uuid)":
        (True, "v", "search_path=pg_catalog, ops, public"),
    "ops.refuse_assurance_persistence_rewrite()":
        (False, "v", "search_path=pg_catalog, ops"),
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    module_dir = str(path.parent)
    added_to_path = module_dir not in sys.path
    if added_to_path:
        sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if added_to_path:
            sys.path.remove(module_dir)
    return module


a2 = load_module("a3a_a2_fixture", ROOT / "ops/canonical-ownership-lease-local-pg-gate.py")
cc = a2.cc


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def token_spellings(token: object) -> set[str]:
    canonical = str(token)
    return {
        canonical,
        canonical.upper(),
        canonical.replace("-", ""),
        "{" + canonical.upper() + "}",
    }


def set_json_pointer(value: dict, pointer: str, replacement: object, *, remove: bool = False) -> None:
    parts = pointer.removeprefix("/").split("/")
    target: object = value
    for part in parts[:-1]:
        if not isinstance(target, dict):
            raise RuntimeError(f"fixture pointer is not object-backed: {pointer}")
        target = target[part]
    if not isinstance(target, dict):
        raise RuntimeError(f"fixture pointer parent is not an object: {pointer}")
    if remove:
        del target[parts[-1]]
    else:
        target[parts[-1]] = replacement


def schema_fingerprint(cur) -> str:
    value = one(cur, """select jsonb_build_object(
      'relations',(select jsonb_agg(value order by value->>'name') from (
        select jsonb_build_object(
          'name',n.nspname||'.'||c.relname,'kind',c.relkind,
          'owner',pg_get_userbyid(c.relowner),'acl',coalesce(c.relacl::text,'<default>'),
          'columns',(select jsonb_agg(jsonb_build_object(
            'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
            'not_null',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid)) order by a.attnum)
            from pg_attribute a left join pg_attrdef d
              on d.adrelid=a.attrelid and d.adnum=a.attnum
            where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped),
          'constraints',(select coalesce(jsonb_agg(jsonb_build_object(
            'name',con.conname,'type',con.contype,'definition',pg_get_constraintdef(con.oid,true))
            order by con.conname),'[]'::jsonb) from pg_constraint con where con.conrelid=c.oid),
          'indexes',(select coalesce(jsonb_agg(pg_get_indexdef(i.indexrelid) order by i.indexrelid::regclass::text),'[]'::jsonb)
            from pg_index i where i.indrelid=c.oid),
          'triggers',(select coalesce(jsonb_agg(pg_get_triggerdef(t.oid,true) order by t.tgname),'[]'::jsonb)
            from pg_trigger t where t.tgrelid=c.oid and not t.tgisinternal)) value
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='ops' and c.relname=any(array[
          'assurance_execution_manifest','assurance_evidence_extension',
          'assurance_review_extension','assurance_owner_acceptance_fact'])) rows),
      'functions',(select jsonb_agg(value order by value->>'identity') from (
        select jsonb_build_object(
          'identity',p.oid::regprocedure::text,'owner',pg_get_userbyid(p.proowner),
          'security_definer',p.prosecdef,'volatility',p.provolatile,
          'config',coalesce(to_jsonb(p.proconfig),'null'::jsonb),
          'acl',coalesce(p.proacl::text,'<default>')) value
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='ops' and (p.proname like 'assurance_%%'
          or p.proname='record_assurance_execution_manifest'
          or p.proname='record_assurance_evidence_extension'
          or p.proname='record_assurance_review_extension'
          or p.proname='record_assurance_owner_acceptance'
          or p.proname='refuse_assurance_persistence_rewrite')) rows))""")[0]
    return digest(value)


def one(cur, query: str, args: tuple = ()):
    row = cur.execute(query, args).fetchone()
    if row is None:
        raise RuntimeError(f"expected one row: {query[:120]}")
    return row


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        FAILED_LABELS.append(label)
        print(f"  FAIL  {label}  {detail}")


def assert_secret_absent(value: object, label: str) -> None:
    rendered = json.dumps(value, default=str, sort_keys=True)
    for token in SECRET_TOKENS:
        if token in rendered:
            raise RuntimeError(f"{label}: lease token escaped before redaction")


def observe(value: object, label: str) -> object:
    assert_secret_absent(value, label)
    with RAW_LOCK:
        RAW_RESULTS.append(value)
    return value


def refusal(label: str, value: dict, code: str, causal_object: str) -> None:
    actual = value.get("refusal", {}).get("code") if isinstance(value, dict) else None
    shape = set(value.get("refusal", {})) if isinstance(value, dict) else set()
    matches = value.get("ok") is False and actual == code and shape == {
        "code", "causal_object", "expected", "actual"
    } and value["refusal"]["causal_object"] == causal_object
    check(label, matches,
        f"actual={actual} causal={value.get('refusal', {}).get('causal_object')}")
    if not matches:
        FAILED_LABELS[-1] += (
            f" [actual={actual}; causal={value.get('refusal', {}).get('causal_object')}]")
    REFUSALS[code] = REFUSALS.get(code, 0) + 1


def safe(value: object) -> str:
    rendered = json.dumps(value, default=str, sort_keys=True)
    for token in SECRET_TOKENS:
        rendered = rendered.replace(token, "<redacted-token>")
    return rendered[:1000]


def call(cur, signature: str, args: tuple):
    placeholders = ",".join(["%s"] * len(args))
    try:
        return observe(one(cur, f"select {signature}({placeholders})", args)[0], signature)
    except BaseException as exc:
        assert_secret_absent(str(exc), f"{signature} exception")
        with RAW_LOCK:
            RAW_ERRORS.append(str(exc))
        raise


def set_context(cur, tenant: str, actor: str, session: str, host: str) -> None:
    for key, value in {
        "carr.organization_tenant_id": tenant,
        "carr.acting_actor_slug": actor,
        "carr.ownership_session_id": session,
        "carr.execution_host_id": host,
    }.items():
        one(cur, "select set_config(%s,%s,false)", (key, value))


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_rules(seed: str = "a") -> dict:
    value = {
        "schema_version": "applicable-rule-snapshot.v1",
        "snapshot_ref": f"rules:a3a:{seed}",
        "rules": [
            {"rule_ref": "rule:independent-review", "revision": 1,
             "digest": "sha256:" + seed * 64},
        ],
    }
    value["snapshot_digest"] = digest({"snapshot_ref": value["snapshot_ref"], "rules": value["rules"]})
    return value


def make_coord(cur, lease: dict, session: str, host: str, *, seconds: int = 300) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    leases = one(cur, """select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
      from (select jsonb_build_object(
        'lease_id','lease:'||l.id::text,'state','active',
        'holder_session_id',l.holder_session_ref,'holder_host_id',l.holder_host_ref,
        'expires_at',to_char(l.expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'fencing_generation',l.fencing_generation,
        'claims',(select coalesce(jsonb_agg(jsonb_build_object(
          'path',claim_value,'mode',claim_mode,'operation',operation)
          order by ops.assurance_digest(jsonb_build_object(
          'path',claim_value,'mode',claim_mode,'operation',operation))),'[]'::jsonb)
          from ops.canonical_ownership_claim c where c.lease_id=l.id and c.claim_kind='path')) value
        from ops.canonical_ownership_lease l
       where l.organization_tenant_id=(select organization_tenant_id
         from ops.canonical_ownership_lease where id=%s)
         and l.state='active' and l.expires_at>%s) rows""",
      (lease["lease_id"], now))[0]
    dependencies = one(cur, """select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
      from (select jsonb_build_object('slice_ref',d.dependency_slice_ref,
        'state',d.required_state,'evidence_digest',
        case when d.required_state='independently_verified'
          then ops.assurance_digest(f.fact) else r.receipt_digest end) value
        from ops.canonical_ownership_dependency d
        join ops.engineering_slice_receipt r on r.id=d.observed_receipt_id
        left join ops.engineering_reviewer_fact f on f.id=d.observed_reviewer_fact_id
        where d.lease_id=%s) rows""", (lease["lease_id"],))[0]
    subject = next(row for row in leases
                   if row["lease_id"] == f"lease:{lease['lease_id']}")
    earliest_expiry = min(datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                          for row in leases)
    valid_until = min(now + timedelta(seconds=seconds), earliest_expiry - timedelta(seconds=1))
    value = {
        "schema_version": "assurance-coordination-snapshot.v1",
        "as_of": iso(now),
        "valid_until": iso(valid_until),
        "manifest_phase": "baseline",
        "requesting_session_id": session,
        "requesting_host_id": host,
        "leases": leases,
        "dependencies": dependencies,
    }
    if subject["holder_session_id"] != session or subject["holder_host_id"] != host:
        raise RuntimeError("subject coordination identity drifted before compilation")
    value["snapshot_digest"] = digest(value)
    return value


# A snapshot minted for a negative case has to be genuinely expired by the time
# the assertion runs, and "sleep a bit longer than the window" is not a way to
# know that. It assumes the client and server clocks agree and that no time was
# lost between minting the snapshot and reaching the wait -- neither of which a
# loaded runner honours. Ask the server how much of ITS window is left, sleep
# exactly that, then read it back and refuse to continue if the snapshot is
# somehow still live. A negative test that runs against a still-valid snapshot
# does not fail; it passes for the wrong reason, which is worse.
def wait_out_snapshot(cur, coord: dict, *, margin: float = 0.5) -> float:
    remaining = float(one(cur, """select extract(epoch from
      (%s::timestamptz - clock_timestamp()))""", (coord["valid_until"],))[0])
    if remaining > 0:
        one(cur, "select pg_sleep(%s)", (remaining + margin,))
    left = float(one(cur, """select extract(epoch from
      (%s::timestamptz - clock_timestamp()))""", (coord["valid_until"],))[0])
    if left > 0:
        raise RuntimeError(
            f"coordination snapshot {coord['valid_until']} still valid after "
            f"waiting it out: {left:.3f}s remain, so the expiry assertion below "
            "would have proved nothing")
    return remaining


def compiler_fixture() -> dict:
    return json.loads((ROOT / "control-room/contracts/fixtures/execution-fabric/"
                       "assurance-compiler.valid.v1.json").read_text())


def multi_evidence_requirements() -> list[dict]:
    requirements = copy.deepcopy(
        compiler_fixture()["assurance_slice"]["evidence_requirements"])
    second = copy.deepcopy(requirements[0])
    second.update({
        "evidence_ref": "evidence:a3a-secondary-output",
        "artifact_kind": "artifact:a3a-secondary-output",
        "required_fields": ["stderr_digest", "stdout_digest"],
    })
    requirements.append(second)
    return sorted(requirements, key=digest)


def compile_canonical(value: dict) -> dict:
    compiler = load_module(f"a3a_compiler_{uuid.uuid4().hex}",
                           ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    compiled = compiler.compile_assurance_slice(copy.deepcopy(value))
    if compiled.get("ok") is not True:
        raise RuntimeError(f"final canonical A1a input did not compile: {compiled}")
    return compiled["manifest"]


def normalize_contract(value: dict) -> None:
    for field in ("path_claims", "forbidden_paths", "dependencies", "required_tests",
                  "evidence_requirements", "unfinished_work"):
        value[field] = sorted(value[field], key=digest)
    for required_test in value["required_tests"]:
        required_test["evidence_fields"] = sorted(required_test["evidence_fields"])
    compiler = load_module(f"a3a_contract_{uuid.uuid4().hex}",
                           ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    ownership_preimage = {
        key: copy.deepcopy(item) for key, item in value.items()
        if key not in {"contract_digest", "ownership_contract_digest", "lease_binding"}
    }
    value["ownership_contract_digest"] = compiler.compile_ownership_contract_digest(
        ownership_preimage)
    value["contract_digest"] = digest({k: v for k, v in value.items()
                                       if k != "contract_digest"})


def redigest_contract(value: dict) -> None:
    preimage = {
        key: copy.deepcopy(item) for key, item in value.items()
        if key not in {"contract_digest", "ownership_contract_digest", "lease_binding"}
    }
    value["ownership_contract_digest"] = digest(preimage)
    value["contract_digest"] = digest({k: v for k, v in value.items()
                                       if k != "contract_digest"})


def compiler_and_sql_parity(cur, lease: dict, base_input: dict, base_manifest: dict,
                            label: str, mutate, expected_ok: bool) -> None:
    candidate = copy.deepcopy(base_input)
    mutate(candidate["assurance_slice"])
    redigest_contract(candidate["assurance_slice"])
    compiler = load_module(f"a3a_parity_{uuid.uuid4().hex}",
                           ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    try:
        compiled = compiler.compile_assurance_slice(copy.deepcopy(candidate))
    except ValueError:
        compiled = {"ok": False}
    compiler_ok = compiled.get("ok") is True
    manifest = compiled.get("manifest", base_manifest)
    sql_result = call(cur, "ops.assurance_validate_compiler_input", (
        lease["lease_id"], Jsonb(candidate), Jsonb(manifest),
    ))
    sql_ok = sql_result.get("ok") is True
    check(f"A1a parity: {label}",
          compiler_ok is expected_ok and sql_ok is compiler_ok,
          f"compiler_ok={compiler_ok} sql_ok={sql_ok} "
          f"sql_causal={sql_result.get('refusal', {}).get('causal_object')}")


def compiler_input_and_sql_parity(cur, lease: dict, base_input: dict,
                                  base_manifest: dict, label: str, mutate,
                                  expected_ok: bool) -> None:
    candidate = copy.deepcopy(base_input)
    mutate(candidate)
    compiler = load_module(f"a3a_input_parity_{uuid.uuid4().hex}",
                           ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    try:
        compiled = compiler.compile_assurance_slice(copy.deepcopy(candidate))
    except ValueError:
        compiled = {"ok": False}
    compiler_ok = compiled.get("ok") is True
    sql_result = call(cur, "ops.assurance_validate_compiler_input", (
        lease["lease_id"], Jsonb(candidate),
        Jsonb(compiled.get("manifest", base_manifest)),
    ))
    sql_ok = sql_result.get("ok") is True
    check(f"A1a parity: {label}",
          compiler_ok is expected_ok and sql_ok is compiler_ok,
          f"compiler_ok={compiler_ok} sql_ok={sql_ok} "
          f"sql_causal={sql_result.get('refusal', {}).get('causal_object')}")


def ownership_contract_digest(cur, plan: dict, rules: dict, session: str, host: str,
                              slice_ref: str, claims: list[dict],
                              dependencies: list[dict], *,
                              minimum_independent_reviewers: int = 1,
                              evidence_requirements: list[dict] | None = None) -> str:
    """Build the exact A1a prelease contract and return its canonical digest."""
    template = compiler_fixture()
    contract = copy.deepcopy(template["assurance_slice"])
    selected = next(row for row in plan["slices"] if row["slice_ref"] == slice_ref)
    repository = {"repository_id": "repo:jbookout-carr-system",
                  "commit_sha": "a" * 40, "tree_sha": "b" * 40}
    contract.update({
        "work_request": copy.deepcopy(plan["work_request"]),
        "accepted_plan_revision": copy.deepcopy(plan["accepted_plan_revision"]),
        "engineering_slice_plan_digest": plan["plan_digest"],
        "slice_ref": slice_ref,
        "risk": {"risk_class": selected["risk_class"],
                 "summary": "A3a immutable persistence"},
        "path_claims": copy.deepcopy(claims),
        "dependencies": copy.deepcopy(dependencies),
        "repository_binding": repository,
        "rule_snapshot_binding": {"snapshot_ref": rules["snapshot_ref"],
                                  "snapshot_digest": rules["snapshot_digest"]},
        "executor_identity": {"actor_ref": "actor:codex",
                              "session_ref": session, "host_ref": host},
        "reviewer_policy": {"minimum_independent_reviewers":
                                minimum_independent_reviewers,
                            "executor_actor_ref": "actor:codex",
                            "executor_session_ref": session,
                            "owner_acceptance_is_review": False,
                            "distinct_actor_and_session": True},
    })
    if evidence_requirements is not None:
        contract["evidence_requirements"] = copy.deepcopy(evidence_requirements)
    planned_check = selected["planned_checks"][0]
    contract["required_tests"][0]["check_ref"] = planned_check["check_ref"]
    contract["required_tests"][0]["causal_failure"]["object"] = planned_check["check_ref"]
    contract["required_tests"][0]["planned_check_digest"] = digest(planned_check)
    contract["required_tests"][0]["causal_failure"]["expected"] = planned_check["failure_condition"]
    normalize_contract(contract)
    return contract["ownership_contract_digest"]


def compile_input(cur, lease: dict, plan: dict, rules: dict, coord: dict,
                  session: str, host: str, *, commit: str = "a" * 40,
                  tree: str = "b" * 40,
                  minimum_independent_reviewers: int = 1,
                  evidence_requirements: list[dict] | None = None) -> tuple[dict, dict]:
    compiler = load_module("a3a_compiler", ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    template = compiler_fixture()
    contract = copy.deepcopy(template["assurance_slice"])
    selected = next(row for row in plan["slices"] if row["slice_ref"] == lease["slice_ref"])
    claims = next(row["claims"] for row in coord["leases"]
                  if row["lease_id"] == f"lease:{lease['lease_id']}")
    dependencies = one(cur, """select coalesce(jsonb_agg(jsonb_build_object(
      'slice_ref',dependency_slice_ref,'required_state',required_state)
      order by ops.assurance_digest(jsonb_build_object(
      'slice_ref',dependency_slice_ref,'required_state',required_state))),'[]'::jsonb)
      from ops.canonical_ownership_dependency where lease_id=%s""",
      (lease["lease_id"],))[0]
    repository = {"repository_id": "repo:jbookout-carr-system",
                  "commit_sha": commit, "tree_sha": tree}
    contract.update({
        "work_request": copy.deepcopy(plan["work_request"]),
        "accepted_plan_revision": copy.deepcopy(plan["accepted_plan_revision"]),
        "engineering_slice_plan_digest": plan["plan_digest"],
        "slice_ref": lease["slice_ref"],
        "risk": {"risk_class": selected["risk_class"], "summary": "A3a immutable persistence"},
        "path_claims": claims,
        "dependencies": dependencies,
        "repository_binding": repository,
        "rule_snapshot_binding": {"snapshot_ref": rules["snapshot_ref"],
                                  "snapshot_digest": rules["snapshot_digest"]},
        "lease_binding": {"lease_id": f"lease:{lease['lease_id']}",
                          "fencing_generation": lease["fencing_generation"],
                          "holder_session_id": session, "holder_host_id": host},
        "executor_identity": {"actor_ref": "actor:codex",
                              "session_ref": session, "host_ref": host},
        "reviewer_policy": {"minimum_independent_reviewers":
                                minimum_independent_reviewers,
                            "executor_actor_ref": "actor:codex",
                            "executor_session_ref": session,
                            "owner_acceptance_is_review": False,
                            "distinct_actor_and_session": True},
    })
    if evidence_requirements is not None:
        contract["evidence_requirements"] = copy.deepcopy(evidence_requirements)
    planned_check = selected["planned_checks"][0]
    contract["required_tests"][0]["check_ref"] = planned_check["check_ref"]
    contract["required_tests"][0]["causal_failure"]["object"] = planned_check["check_ref"]
    contract["required_tests"][0]["planned_check_digest"] = digest(planned_check)
    contract["required_tests"][0]["causal_failure"]["expected"] = planned_check["failure_condition"]
    normalize_contract(contract)
    value = {
        "schema_version": "assurance-compiler-input.v1",
        "work_request": copy.deepcopy(plan["work_request"]),
        "accepted_plan_revision": copy.deepcopy(plan["accepted_plan_revision"]),
        "engineering_slice_plan": copy.deepcopy(plan),
        "assurance_slice": contract,
        "repository": repository,
        "applicable_rules": copy.deepcopy(rules),
        "coordination_snapshot": copy.deepcopy(coord),
        "declared_evaluation_time": coord["as_of"],
    }
    return value, compile_canonical(value)


def issuer_context(cur, tenant: str, runtime_session: str, host: str,
                   job_token: uuid.UUID, ownership_session_ref: str) -> None:
    """Bind a NOINHERIT issuer connection to one live A2 runtime."""
    cur.execute("set local role carr_ownership_issuer")
    for key, value in {
        "carr.organization_tenant_id": tenant,
        "carr.acting_actor_slug": "codex",
        "carr.receipt_session_ref": runtime_session,
        "carr.execution_host_id": host,
        "carr.engineering_job_lease_token": str(job_token),
        "carr.ownership_session_id": ownership_session_ref,
    }.items():
        one(cur, "select set_config(%s,%s,true)", (key, value))


def issuer_manifest_context(cur) -> None:
    issuer_context(cur, A3A_ISSUER["tenant"], A3A_ISSUER["runtime_session"],
                   A3A_ISSUER["host"], A3A_ISSUER["job_token"],
                   A3A_ISSUER["ownership_session_ref"])


def record_manifest(cur, lease: dict, stage: str, compiler_input: dict,
                    manifest: dict, rules: dict, coord: dict, key: uuid.UUID):
    # Keep the generic fixture cursor as a data builder.  Only a NOINHERIT
    # issuer login with the current runtime may call the guarded A3a door.
    # Publish fixture rows before the issuer opens its separate transaction;
    # otherwise its lease/plan read can wait on this connection's own writes.
    cur.connection.commit()
    with psycopg.connect(A3A_ISSUER["dsn"], user="carr_ownership_issuer_g1") as issuer:
        with issuer.cursor() as issuer_cur:
            issuer_manifest_context(issuer_cur)
            return call(issuer_cur, "ops.record_assurance_execution_manifest", (
                lease["lease_id"], lease["lease_token"], lease["fencing_generation"], stage,
                Jsonb(compiler_input), Jsonb(manifest), Jsonb(rules), Jsonb(coord), key,
            ))


def issuer_lifecycle(request: dict) -> dict:
    with psycopg.connect(A3A_ISSUER["dsn"], user="carr_ownership_issuer_g1") as issuer:
        with issuer.cursor() as issuer_cur:
            issuer_manifest_context(issuer_cur)
            # A2 lifecycle may legitimately return the lease token.  The raw
            # nondisclosure ledger below covers A3a results, not A2 issuance.
            return one(issuer_cur, "select ops.canonical_ownership_lifecycle(%s)",
                       (Jsonb(request),))[0]


def issuer_call(signature: str, args: tuple) -> dict:
    """Run a read through the same real issuer runtime as manifest admission."""
    with psycopg.connect(A3A_ISSUER["dsn"], user="carr_ownership_issuer_g1") as issuer:
        with issuer.cursor() as issuer_cur:
            issuer_manifest_context(issuer_cur)
            return call(issuer_cur, signature, args)


def race_calls(dsn: str, setups: list, signature: str, args: tuple,
               *, user: str | None = None) -> list[dict]:
    barrier = Barrier(len(setups))
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = Lock()

    def run(setup) -> None:
        try:
            with psycopg.connect(dsn, user=user) as peer:
                with peer.cursor() as cur:
                    setup(cur)
                    barrier.wait(timeout=15)
                    value = call(cur, signature, args)
                peer.commit()
            with lock:
                results.append(value)
        except BaseException as exc:
            assert_secret_absent(str(exc), f"{signature} race exception")
            with lock:
                errors.append(exc)

    threads = [Thread(target=run, args=(setup,), daemon=True) for setup in setups]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    if errors or any(thread.is_alive() for thread in threads):
        raise RuntimeError(f"{signature} race failed without raw leakage: {len(errors)} errors")
    return results


def race_arg_calls(dsn: str, setups: list, signature: str,
                   args_list: list[tuple], *, user: str | None = None) -> list[dict]:
    barrier = Barrier(len(setups))
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = Lock()

    def run(setup, args: tuple) -> None:
        try:
            with psycopg.connect(dsn, user=user) as peer:
                with peer.cursor() as cur:
                    setup(cur)
                    barrier.wait(timeout=15)
                    value = call(cur, signature, args)
                peer.commit()
            with lock:
                results.append(value)
        except BaseException as exc:
            assert_secret_absent(str(exc), f"{signature} conflicting race exception")
            with lock:
                errors.append(exc)

    threads = [Thread(target=run, args=(setup, args), daemon=True)
               for setup, args in zip(setups, args_list, strict=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    if errors or any(thread.is_alive() for thread in threads):
        raise RuntimeError(
            f"{signature} conflicting race failed without raw leakage: {len(errors)} errors")
    return results


def race_mixed_calls(dsn: str, calls: list[tuple]) -> list[dict]:
    barrier = Barrier(len(calls))
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = Lock()

    def run(setup, signature: str, args: tuple) -> None:
        try:
            with psycopg.connect(dsn) as peer:
                with peer.cursor() as cur:
                    setup(cur)
                    barrier.wait(timeout=15)
                    value = call(cur, signature, args)
                peer.commit()
            with lock:
                results.append(value)
        except BaseException as exc:
            assert_secret_absent(str(exc), f"{signature} mixed race exception")
            with lock:
                errors.append(exc)

    threads = [Thread(target=run, args=item, daemon=True) for item in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    if errors or any(thread.is_alive() for thread in threads):
        raise RuntimeError(
            f"mixed race failed without raw leakage: {len(errors)} errors")
    return results


def manifest_id(value: dict) -> uuid.UUID:
    # Lead with the fields that decide what to do next. The caller in
    # ops/local-pg-ci.py caps each reported line at 240 characters, and the raw
    # payload spends its first 200 on schema noise -- so hosted run 33710296533
    # printed `actual` and `causal_object` and was cut off before `expected`,
    # which was the one number that would have identified the window at fault.
    if value.get("ok") is not True:
        refusal_detail = value.get("refusal") or {}
        summary = " ".join(
            f"{field}={refusal_detail[field]}"
            for field in ("code", "causal_object", "expected", "actual")
            if field in refusal_detail)
        raise RuntimeError(
            f"manifest insert failed: {summary or '(no refusal block)'} "
            f":: {safe(value)}")
    return uuid.UUID(str(value["manifest_id"]))


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    if not dsn.startswith(("postgres://", "postgresql://")):
        print("assurance A3a gate: CARR_LOCAL_PG_DSN must name disposable PostgreSQL", file=sys.stderr)
        return 2
    tenant = "carr-internal"
    host = "cloudflare-workers:a3a-disposable-pg"
    dependency_ref, subject_ref = "slice:a3a-dependency", "slice:a3a-subject"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            schema_before = schema_fingerprint(cur)
            review_rows_before = one(cur,
                "select count(*) from ops.assurance_review_extension")[0]
            owner_rows_before = one(cur,
                "select count(*) from ops.assurance_owner_acceptance_fact")[0]
        # This is a nondisclosure canary, not authority.  It is registered as
        # a released foreign-tenant lease after the activated lease exists:
        # token suppression scans the canonical lease registry, so a bare UUID
        # would not exercise the condition.
        foreign_token = uuid.uuid4()
        foreign_token_lease_id = uuid.uuid4()
        SECRET_TOKENS.update(token_spellings(foreign_token))
        conn.commit()
        with conn.cursor() as cur:
            a2.context(cur, tenant)
            dependency = a2.fixture(cur, slice_refs=[dependency_ref, subject_ref],
                slice_dependencies={subject_ref: [dependency_ref]},
                planned_check_refs={subject_ref: "check:compiler"},
                source_merge_paths=[
                    "ops/a3a-fixture.sql",
                    "ops/a3a-rename-destination.sql",
                    "ops/a3a-rename-source.sql",
                ])
        conn.commit()
        dependency_claim = cc.claim_one(conn, dependency[0], "a3a-dependency")
        with conn.cursor() as cur:
            cc.set_jobs(cur)
            dependency_receipt_id = cc.receipt(cur, dependency, dependency_claim, "claimed_complete")
            cc.reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            a2.insert_review(cur, dependency, dependency_receipt_id)
        conn.commit()
        fixture = cc.create_dag_b_after_exact_review(conn, dependency, subject_ref)
        with conn.cursor() as cur:
            session = one(cur, "select envelope#>>'{agent_session,id}' from ops.engineering_execution_envelope where id=%s", (fixture[1],))[0]
            set_context(cur, tenant, "codex", session, host)
            bound = a2.binding(cur, fixture[1])
            plan = one(cur, "select plan from ops.engineering_slice_plan where id=%s", (bound[5],))[0]
            rules = make_rules("a")
            path_claims = [
                {"path": "ops/a3a-fixture.sql", "mode": "file", "operation": "write"},
                {"path": "ops/a3a-rename-source.sql", "mode": "file", "operation": "write"},
                {"path": "ops/a3a-rename-destination.sql", "mode": "file", "operation": "write"},
            ]
            dependencies = [{"slice_ref": dependency_ref,
                             "required_state": "independently_verified"}]
            contract_evidence_requirements = multi_evidence_requirements()
        conn.commit()

        claim = cc.claim_one(conn, fixture[0], "a3a-controller")
        with conn.cursor() as cur:
            expires_at = one(cur, """select least(j.leased_until,
              s.lease_expires_at,e.expires_at)-interval '5 seconds'
              from ops.engineering_execution_envelope e
              join ops.job j on j.id=e.job_id
              join ops.capability_agent_session s on s.id=e.agent_session_id
              where e.id=%s""", (fixture[1],))[0]
            runtime_probe = one(cur, """select jsonb_build_object(
              'currentness',ops.engineering_envelope_currentness(e.id,e.job_id),
              'job_state',j.state,'job_attempt',j.attempt,
              'session_state',s.state,'session_lease_live',s.lease_expires_at>clock_timestamp(),
              'work_state',w.state,'work_version_matches',e.state_version=w.version,
              'plan_matches',e.accepted_plan_id=p.id)
              from ops.engineering_execution_envelope e
              join ops.job j on j.id=e.job_id
              join ops.capability_agent_session s on s.id=e.agent_session_id
              join ops.work_request w on w.id=e.work_request_id
              join ops.sourced_work_request_plan p on p.id=e.accepted_plan_id
              where e.id=%s""", (fixture[1],))[0]
            if runtime_probe["currentness"].get("eligible") is not True:
                raise RuntimeError(f"A3a fixture runtime subject is ineligible: {safe(runtime_probe)}")
        conn.commit()
        # Claim first, then let a real issuer login mint and consume the exact
        # running-job capability.  The lifecycle derives the accepted scope
        # digest itself; the compiler digest stays an assurance preimage.
        with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer:
            with issuer.cursor() as issuer_cur:
                issuer_context(issuer_cur, tenant, session, host, claim[1], "")
                runtime = call(issuer_cur, "ops.mint_canonical_ownership_runtime_session", (
                    bound[0], bound[3], fixture[1], claim[2], session, host,
                    expires_at, uuid.uuid4()))
                if runtime.get("ok") is not True:
                    raise RuntimeError(f"A3a issuer runtime mint failed: {safe(runtime)}")
                issuer_context(issuer_cur, tenant, session, host, claim[1],
                               runtime["ownership_session_ref"])
                lease = one(issuer_cur, "select ops.canonical_ownership_lifecycle(%s)", (Jsonb({
                    "operation": "acquire", "idempotency_key": str(uuid.uuid4()),
                    "ttl_seconds": 900,
                }),))[0]
        if lease.get("ok") is not True:
            raise RuntimeError(f"A3a issuer lifecycle acquire failed: {safe(lease)}")
        A3A_ISSUER.update({
            "dsn": dsn, "tenant": tenant, "runtime_session": session, "host": host,
            "job_token": claim[1], "ownership_session_ref": runtime["ownership_session_ref"],
        })
        # Assurance binds to the issuer-minted ownership session. The original
        # engineering session remains bound separately in A3A_ISSUER.
        session = runtime["ownership_session_ref"]
        with conn.cursor() as cur:
            scope_digest = one(cur, """select scope.scope_digest
              from ops.source_merge_plan_scope scope
              join ops.sourced_work_request_plan_acceptance_receipt acceptance
                on acceptance.id=scope.acceptance_receipt_id
              join ops.engineering_execution_envelope envelope on envelope.id=%s
              where scope.organization_tenant_id=%s and scope.work_request_id=%s
                and scope.accepted_plan_id=%s
                and acceptance.result_version=envelope.state_version""",
              (fixture[1], tenant, bound[0], bound[3]))[0]
            stored_scope_digest = one(cur,
                "select contract_digest from ops.canonical_ownership_lease where id=%s",
                (lease["lease_id"],))[0]
            if stored_scope_digest != scope_digest:
                raise RuntimeError("A3a lifecycle did not bind the current accepted scope digest")
            lease["slice_ref"] = subject_ref
            SECRET_TOKENS.update(token_spellings(lease["lease_token"]))
            cur.execute("""insert into ops.canonical_ownership_lease(
              id,organization_tenant_id,holder_actor_id,holder_actor_slug,
              holder_session_ref,holder_host_ref,lease_token,fencing_generation,
              work_request_id,work_request_version,work_request_digest,
              accepted_plan_id,accepted_plan_digest,slice_plan_id,slice_plan_digest,
              slice_ref,subject_envelope_id,contract_digest,state,acquired_at,
              expires_at,released_at,created_at,updated_at)
              select %s,%s,holder_actor_id,holder_actor_slug,holder_session_ref,
                holder_host_ref,%s,nextval('ops.canonical_ownership_fencing_generation'),
                work_request_id,work_request_version,work_request_digest,
                accepted_plan_id,accepted_plan_digest,slice_plan_id,slice_plan_digest,
                slice_ref,subject_envelope_id,contract_digest,'released',
                clock_timestamp()-interval '2 hours',clock_timestamp()-interval '1 hour',
                clock_timestamp()-interval '1 hour',clock_timestamp(),clock_timestamp()
              from ops.canonical_ownership_lease where id=%s""", (
                foreign_token_lease_id, tenant + ":foreign-token-canary", foreign_token,
                lease["lease_id"],
            ))
            check("foreign token canary is registered in the lease catalog", one(cur,
                "select exists(select 1 from ops.canonical_ownership_lease where id=%s "
                "and lease_token=%s and state='released')",
                (foreign_token_lease_id, foreign_token))[0])
        with conn.cursor() as cur:
            set_context(cur, tenant, "codex", session, host)
            coord = make_coord(cur, lease, session, host)
            compiler_input, post_manifest = compile_input(
                cur, lease, plan, rules, coord, session, host,
                evidence_requirements=contract_evidence_requirements)
            compiler = load_module(f"a3a_digest_{uuid.uuid4().hex}",
                                   ROOT / "tools/room-bridge/assurance_slice_compiler.py")
            compiler_digest = compiler.compile_ownership_contract_digest({
                key: copy.deepcopy(item)
                for key, item in compiler_input["assurance_slice"].items()
                if key not in {"contract_digest", "ownership_contract_digest", "lease_binding"}
            })
            stored_contract_digest = one(cur,
                "select contract_digest from ops.canonical_ownership_lease where id=%s",
                (lease["lease_id"],))[0]
            if compiler_input["assurance_slice"]["ownership_contract_digest"] != compiler_digest:
                raise RuntimeError(
                    "compiler ownership digest fixture drift: "
                    f"preimage={compiler_digest} "
                    f"compiled={compiler_input['assurance_slice']['ownership_contract_digest']}"
                )
            if compiler_digest == stored_contract_digest:
                raise RuntimeError("A3a fixture collapsed compiler and accepted-scope digests")
            sql_contract_digest = one(cur, """select ops.assurance_digest(
              %s::jsonb-array['contract_digest','ownership_contract_digest','lease_binding'])""",
              (Jsonb(compiler_input["assurance_slice"]),))[0]
            if sql_contract_digest != compiler_digest:
                raise RuntimeError(
                    "ownership digest SQL recomputation drift: "
                    f"compiled={compiler_digest} sql={sql_contract_digest}"
                )
            post_key = uuid.uuid4()
            post = record_manifest(cur, lease, "post_commit", compiler_input,
                                   post_manifest, rules, coord, post_key)
            post_id = manifest_id(post)
            check("post-commit manifest persists", post.get("replayed") is False)

            blocked_coord = make_coord(cur, lease, session, host, seconds=2)
            blocked_input, blocked_manifest = compile_input(
                cur, lease, plan, rules, blocked_coord, session, host,
                evidence_requirements=contract_evidence_requirements)
            blocked_key = uuid.uuid4()
            blocked_results: list[dict] = []
            blocked_errors: list[BaseException] = []
            blocked_pids: list[int] = []

            def run_manifest_behind_a2_renew() -> None:
                try:
                    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as peer, peer.cursor() as race_cur:
                        issuer_manifest_context(race_cur)
                        with RAW_LOCK:
                            blocked_pids.append(one(race_cur, "select pg_backend_pid()")[0])
                        blocked_results.append(call(
                            race_cur, "ops.record_assurance_execution_manifest", (
                                lease["lease_id"], lease["lease_token"], lease["fencing_generation"],
                                "push", Jsonb(blocked_input), Jsonb(blocked_manifest),
                                Jsonb(rules), Jsonb(blocked_coord), blocked_key)))
                except BaseException as exc:
                    assert_secret_absent(str(exc), "A2-renew/A3a-manifest race exception")
                    blocked_errors.append(exc)

            conn.commit()
            with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as blocker, blocker.cursor() as blocker_cur:
                issuer_manifest_context(blocker_cur)
                one(blocker_cur, "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"canonical-ownership:{tenant}",))
                blocked_thread = Thread(target=run_manifest_behind_a2_renew, daemon=True)
                blocked_thread.start()
                deadline = time.monotonic() + 5
                waiting_on_a2 = False
                blocker_pid = one(blocker_cur, "select pg_backend_pid()")[0]
                while time.monotonic() < deadline:
                    with RAW_LOCK:
                        peer_pid = blocked_pids[0] if blocked_pids else None
                    if peer_pid is not None:
                        waiting_on_a2 = bool(one(blocker_cur,
                            "select %s=any(pg_blocking_pids(%s))",
                            (blocker_pid, peer_pid))[0])
                        if waiting_on_a2:
                            break
                    time.sleep(0.05)
                time.sleep(2.1)
                renewal_during_block = one(blocker_cur,
                    "select ops.canonical_ownership_lifecycle(%s)", (Jsonb({
                        "operation": "renew", "idempotency_key": str(uuid.uuid4()),
                        "lease_id": str(lease["lease_id"]),
                        "lease_token": str(lease["lease_token"]),
                        "fencing_generation": lease["fencing_generation"],
                        "ttl_seconds": 900,
                    }),))[0]
                blocker.commit()
            blocked_thread.join(timeout=15)
            check("A3a waits behind canonical A2 authority before lease-table SHARE",
                  waiting_on_a2 and renewal_during_block.get("ok") is True
                  and not blocked_thread.is_alive() and not blocked_errors)
            refusal("snapshot expiring behind canonical A2 renew cannot append manifest",
                    blocked_results[0] if blocked_results else {},
                    "ASSURANCE_SNAPSHOT_EXPIRED", "coordination_snapshot.valid_until")
            check("expired blocked manifest leaves no durable row", one(cur,
                "select not exists(select 1 from ops.assurance_execution_manifest where idempotency_key=%s)",
                (blocked_key,))[0])

            coord = make_coord(cur, lease, session, host)
            compiler_input, post_manifest = compile_input(
                cur, lease, plan, rules, coord, session, host,
                evidence_requirements=contract_evidence_requirements)
            post_key = uuid.uuid4()
            post = record_manifest(cur, lease, "post_commit", compiler_input,
                                   post_manifest, rules, coord, post_key)
            post_id = manifest_id(post)
            check("post-renewal compiler snapshot persists",
                  post.get("replayed") is False)

            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "unsorted evidence required_fields remains valid",
                lambda contract: (
                    contract["evidence_requirements"][0]["required_fields"].reverse(),
                    contract["evidence_requirements"].sort(key=digest),
                ),
                True)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "duplicate evidence_ref is rejected",
                lambda contract: contract["evidence_requirements"].append({
                    **copy.deepcopy(contract["evidence_requirements"][0]),
                    "artifact_kind": "artifact:duplicate-ref"}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "empty required_fields is rejected",
                lambda contract: contract["evidence_requirements"][0].update(
                    {"required_fields": []}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "legacy delete operation is rejected",
                lambda contract: contract["path_claims"][0].update({"operation": "delete"}),
                False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "forbidden path case alias is rejected",
                lambda contract: contract["forbidden_paths"].append({
                    "path": contract["path_claims"][0]["path"].upper(), "mode": "file"}),
                False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "extra contract key is rejected",
                lambda contract: contract.update({"unexpected": True}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "nested environment alias is rejected",
                lambda contract: contract["required_tests"][0]["environment"]
                    ["version_source"].update({"unexpected": True}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "scalar path_claims is rejected",
                lambda contract: contract.update({"path_claims": "ops/a3a-fixture.sql"}),
                False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "empty path_claims is rejected",
                lambda contract: contract.update({"path_claims": []}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "empty forbidden_paths is rejected",
                lambda contract: contract.update({"forbidden_paths": []}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "rollback cwd dot is rejected",
                lambda contract: contract["rollback"].update({"cwd": "."}), False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "JSON null release_class is rejected",
                lambda contract: contract.update({"release_class": None}), False)
            for label, mutate in (
                ("JSON null path claim mode", lambda contract:
                    contract["path_claims"][0].update({"mode": None})),
                ("JSON null path claim operation", lambda contract:
                    contract["path_claims"][0].update({"operation": None})),
                ("JSON null forbidden mode", lambda contract:
                    contract["forbidden_paths"][0].update({"mode": None})),
                ("JSON null dependency state", lambda contract:
                    contract["dependencies"][0].update({"required_state": None})),
            ):
                compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                    f"{label} is rejected", mutate, False)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "percent in a literal sibling path does not alias",
                lambda contract: contract["forbidden_paths"][0].update({
                    "path": contract["path_claims"][0]["path"].split("/")[0][:-1] + "%",
                    "mode": "tree",
                }), True)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "underscore in a literal sibling path does not alias",
                lambda contract: contract["forbidden_paths"][0].update({
                    "path": contract["path_claims"][0]["path"].split("/")[0][:-1] + "_",
                    "mode": "tree",
                }), True)
            compiler_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "true component ancestry collision is rejected",
                lambda contract: contract["forbidden_paths"][0].update({
                    "path": contract["path_claims"][0]["path"].split("/")[0],
                    "mode": "tree",
                }), False)
            def omit_subject_lease(candidate: dict) -> None:
                snapshot = candidate["coordination_snapshot"]
                snapshot["leases"] = [row for row in snapshot["leases"]
                    if row["lease_id"] != f"lease:{lease['lease_id']}"]
                snapshot["snapshot_digest"] = digest({
                    key: value for key, value in snapshot.items()
                    if key != "snapshot_digest"
                })
            compiler_input_and_sql_parity(cur, lease, compiler_input, post_manifest,
                "coordination snapshot cannot omit the subject live lease",
                omit_subject_lease, False)
            risk_results = []
            for risk_class in ("R1", "R2", "R3", "R4", "R5", "R6"):
                risk_input = copy.deepcopy(compiler_input)
                selected_risk = next(row for row in risk_input["engineering_slice_plan"]["slices"]
                                     if row["slice_ref"] == lease["slice_ref"])
                selected_risk["risk_class"] = risk_class
                risk_input["engineering_slice_plan"]["plan_digest"] = digest({
                    key: value for key, value in risk_input["engineering_slice_plan"].items()
                    if key != "plan_digest"
                })
                risk_input["assurance_slice"]["engineering_slice_plan_digest"] = (
                    risk_input["engineering_slice_plan"]["plan_digest"])
                risk_input["assurance_slice"]["risk"]["risk_class"] = risk_class
                redigest_contract(risk_input["assurance_slice"])
                compiler = load_module(f"a3a_risk_{risk_class}_{uuid.uuid4().hex}",
                    ROOT / "tools/room-bridge/assurance_slice_compiler.py")
                risk_compiled = compiler.compile_assurance_slice(risk_input)
                risk_results.append(
                    risk_compiled.get("ok") is True
                    and risk_compiled.get("manifest", {}).get("authority_state") ==
                        "compiled_not_authorized"
                    and risk_compiled.get("manifest", {}).get("slice", {}).get("risk", {})
                        .get("risk_class") == risk_class)
            check("R1-R6 real-compiler matrix remains non-authorizing", all(risk_results))
            replay = record_manifest(cur, lease, "post_commit", compiler_input,
                                     post_manifest, rules, coord, post_key)
            check("manifest exact replay is idempotent", replay.get("ok") is True and replay.get("replayed") is True and replay.get("manifest_id") == post.get("manifest_id"))
            refusal("manifest key reuse with changed stage refuses",
                record_manifest(cur, lease, "push", compiler_input, post_manifest,
                                rules, coord, post_key),
                "IDEMPOTENCY_CONFLICT", "assurance_execution_manifest.idempotency_key")
            refusal("identical manifest hash cannot be relabeled to another stage",
                record_manifest(cur, lease, "review", compiler_input, post_manifest,
                                rules, coord, uuid.uuid4()),
                "ASSURANCE_STAGE_MISMATCH", "manifest.manifest_hash")
            for ordinal, spelling in enumerate(sorted(token_spellings(lease["lease_token"]))):
                token_input = copy.deepcopy(compiler_input)
                token_input["assurance_slice"]["outcome"] = spelling
                refusal(f"manifest token spelling {ordinal + 1} refuses", record_manifest(
                    cur, lease, "post_commit", token_input, post_manifest,
                    rules, coord, uuid.uuid4()),
                    "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            refusal("manifest idempotency key cannot equal lease token", record_manifest(
                cur, lease, "post_commit", compiler_input, post_manifest, rules, coord,
                uuid.UUID(str(lease["lease_token"]))),
                "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            foreign_input = copy.deepcopy(compiler_input)
            foreign_input["assurance_slice"]["outcome"] = str(foreign_token)
            normalize_contract(foreign_input["assurance_slice"])
            foreign_manifest = compile_canonical(foreign_input)
            refusal("foreign registered token refuses in manifest", record_manifest(
                cur, lease, "post_commit", foreign_input, foreign_manifest,
                rules, coord, uuid.uuid4()),
                "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            unsupported_input = copy.deepcopy(compiler_input)
            unsupported_input["assurance_slice"]["reviewer_policy"]["minimum_independent_reviewers"] = 2
            normalize_contract(unsupported_input["assurance_slice"])
            unsupported = compile_canonical(unsupported_input)
            refusal("unsupported reviewer policy reaches the manifest policy refusal",
                record_manifest(cur, lease, "post_commit", unsupported_input, unsupported,
                                rules, coord, uuid.uuid4()),
                "REVIEWER_POLICY_UNSUPPORTED",
                "manifest.slice.reviewer_policy")
            bad_hash = copy.deepcopy(post_manifest); bad_hash["manifest_hash"] = "sha256:" + "0" * 64
            refusal("manifest output forgery is causal", record_manifest(
                cur, lease, "post_commit", compiler_input, bad_hash, rules, coord, uuid.uuid4()),
                "ASSURANCE_INPUT_INVALID", "manifest.compiler_output")
            bad_fence = dict(lease); bad_fence["fencing_generation"] += 1
            stale = record_manifest(cur, bad_fence, "post_commit", compiler_input,
                                    post_manifest, rules, coord, uuid.uuid4())
            refusal("A2 stale fence refusal is preserved", stale,
                    "FENCING_GENERATION_STALE", "lease.fencing_generation")
            check("lease token is never returned", str(lease["lease_token"]) not in safe(post) and str(lease["lease_token"]) not in safe(stale))

            current = issuer_call("ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40,
                rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            ))
            check("exact post-commit currentness is non-authorizing", current.get("ok") is True and current.get("authorizes_action") is False)
            refusal("stage mismatch refuses", issuer_call("ops.assurance_manifest_currentness", (
                post_id, "push", "a" * 40, "b" * 40, rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_STAGE_MISMATCH", "manifest.repository_stage")
            refusal("resulting commit makes old manifest stale", issuer_call("ops.assurance_manifest_currentness", (
                post_id, "post_commit", "c" * 40, "b" * 40, rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_BINDING_STALE", "manifest.repository")
            refusal("rule snapshot drift refuses", issuer_call("ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40, "sha256:" + "f" * 64, coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_RULE_SNAPSHOT_STALE", "manifest.applicable_rule_snapshot_digest")
            refusal("coordination snapshot drift refuses", issuer_call("ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40, rules["snapshot_digest"], "sha256:" + "f" * 64, lease["lease_token"],
            )), "ASSURANCE_COORDINATION_SNAPSHOT_STALE", "manifest.coordination_snapshot_digest")
            currentness_args = [
                "post_commit", "a" * 40, "b" * 40,
                rules["snapshot_digest"], coord["snapshot_digest"],
            ]
            for field_index, field_name in enumerate((
                "required_stage", "observed_commit_sha", "observed_tree_sha",
                "observed_rule_snapshot_digest", "observed_coordination_snapshot_digest",
            )):
                for ordinal, spelling in enumerate(sorted(token_spellings(lease["lease_token"]))):
                    token_args = copy.deepcopy(currentness_args)
                    token_args[field_index] = spelling
                    refusal(
                        f"currentness {field_name} token spelling {ordinal + 1} refuses",
                        issuer_call("ops.assurance_manifest_currentness", (
                            post_id, *token_args, lease["lease_token"],
                        )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")

            # Publish the manifest, then hold an uncommitted foreign-token mint.
            # The A3a door must wait for the token catalog and scan the committed
            # token rather than reading a race-prone snapshot.
            conn.commit()
            minted_token = uuid.uuid4()
            manual_release_id = uuid.uuid4()
            SECRET_TOKENS.update(token_spellings(minted_token))
            mint_results: list[dict] = []
            mint_errors: list[BaseException] = []
            with psycopg.connect(dsn) as mint_conn, mint_conn.cursor() as mint_cur:
                mint_cur.execute("""insert into ops.canonical_ownership_lease(
                  id,organization_tenant_id,holder_actor_id,holder_actor_slug,
                  holder_session_ref,holder_host_ref,lease_token,fencing_generation,
                  work_request_id,work_request_version,work_request_digest,
                  accepted_plan_id,accepted_plan_digest,slice_plan_id,slice_plan_digest,
                  slice_ref,subject_envelope_id,contract_digest,state,acquired_at,
                  expires_at,released_at,created_at,updated_at)
                  select %s,%s,holder_actor_id,holder_actor_slug,holder_session_ref,
                    holder_host_ref,%s,nextval('ops.canonical_ownership_fencing_generation'),
                    work_request_id,work_request_version,work_request_digest,
                    accepted_plan_id,accepted_plan_digest,slice_plan_id,slice_plan_digest,
                    slice_ref,subject_envelope_id,contract_digest,'released',
                    clock_timestamp()-interval '2 hours',clock_timestamp()-interval '1 hour',
                    clock_timestamp()-interval '1 hour',clock_timestamp(),clock_timestamp()
                  from ops.canonical_ownership_lease where id=%s""", (
                    manual_release_id, tenant + ":foreign-token-mint", minted_token,
                    lease["lease_id"],
                ))

                def currentness_during_mint() -> None:
                    try:
                        with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as race_conn, race_conn.cursor() as race_cur:
                            issuer_manifest_context(race_cur)
                            mint_results.append(call(
                                race_cur, "ops.assurance_manifest_currentness", (
                                    post_id, str(minted_token), "a" * 40, "b" * 40,
                                    rules["snapshot_digest"], coord["snapshot_digest"],
                                    lease["lease_token"],
                                )))
                    except BaseException as exc:
                        mint_errors.append(exc)

                mint_thread = Thread(target=currentness_during_mint, daemon=True)
                mint_thread.start()
                mint_conn.commit()
                mint_thread.join(timeout=15)
            check("concurrent foreign token mint is visible to the currentness scan",
                  not mint_thread.is_alive() and not mint_errors
                  and len(mint_results) == 1
                  and mint_results[0].get("refusal", {}).get("code") ==
                      "ASSURANCE_INPUT_INVALID"
                  and mint_results[0].get("refusal", {}).get("causal_object") ==
                      "assurance.token_nondisclosure")

            # THIS SNAPSHOT HAS TWO JOBS THAT PULL IN OPPOSITE DIRECTIONS, and
            # for a long time it did both on a one-second window. The manifest
            # insert immediately below must land INSIDE the window -- it is a
            # positive insert whose id the rest of the block depends on. The
            # currentness call further down must run OUTSIDE it, because that is
            # the assertion proving an expired snapshot refuses.
            #
            # seconds=1 could not honestly serve the first job. make_coord
            # truncates `now` to whole seconds and iso() truncates valid_until
            # the same way, so a one-second window carries somewhere between
            # zero and one second of REAL headroom -- and the insert has to fit
            # a compile_input and a record_manifest round trip inside it. That
            # is close to a coin flip on a fast machine and a lost bet on a slow
            # one. Hosted run 33710296533 refused this exact insert 27ms past
            # the boundary, and it surfaced as ASSURANCE_SNAPSHOT_EXPIRED on a
            # branch whose diff could not reach assurance at all, so a harness
            # race was read as a data defect. Second occurrence of the family.
            #
            # So the window is now sized for the insert rather than for the
            # expiry, and the expiry is reached by MEASURING the server clock
            # instead of outrunning it with a fixed sleep. Five seconds is not a
            # magic number: the insert costs tens of milliseconds locally, so
            # this is roughly two orders of magnitude of headroom, and the wait
            # afterwards costs the same five seconds of CI wall clock whatever
            # the runner's speed, because it sleeps the REMAINDER of the window
            # and not a constant.
            #
            # What is deliberately NOT changed: the seconds=2 snapshot further
            # up, whose insert is expected to refuse, and this block's own
            # refusal assertion below. Both still prove exactly what they proved
            # before -- see the mutation test in the commit that introduced this.
            EXPIRING_SNAPSHOT_WINDOW_SECONDS = 5
            expired_coord = make_coord(cur, lease, session, host,
                                       seconds=EXPIRING_SNAPSHOT_WINDOW_SECONDS)
            expired_input, expired_manifest = compile_input(
                cur, lease, plan, rules, expired_coord, session, host,
                evidence_requirements=contract_evidence_requirements)
            expired_row = record_manifest(cur, lease, "write", expired_input,
                expired_manifest, rules, expired_coord, uuid.uuid4())
            expired_id = manifest_id(expired_row)
            wait_out_snapshot(cur, expired_coord)
            renewed = issuer_lifecycle({
                "operation": "renew", "idempotency_key": str(uuid.uuid4()),
                "lease_id": str(lease["lease_id"]),
                "lease_token": str(lease["lease_token"]),
                "fencing_generation": lease["fencing_generation"],
                "ttl_seconds": 900,
            })
            check("A2 lease renews after manifest snapshot", renewed.get("ok") is True)
            refusal("expired snapshot refuses despite renewed A2 lease", issuer_call("ops.assurance_manifest_currentness", (
                expired_id, "write", "1" * 40, "b" * 40, rules["snapshot_digest"], expired_coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_SNAPSHOT_EXPIRED", "manifest.snapshot_valid_until")
            non_post_manifest_ids = {"write": expired_id}
            for non_post_stage in ("run_check", "push", "pull_request", "merge"):
                one(cur, "select pg_sleep(1.1)")
                stage_coord = make_coord(cur, lease, session, host, seconds=240)
                stage_input, stage_manifest = compile_input(
                    cur, lease, plan, rules, stage_coord, session, host,
                    evidence_requirements=contract_evidence_requirements)
                stage_row = record_manifest(
                    cur, lease, non_post_stage, stage_input, stage_manifest,
                    rules, stage_coord, uuid.uuid4())
                non_post_manifest_ids[non_post_stage] = manifest_id(stage_row)

            # Every positive manifest must be admitted while the issuer runtime
            # and A2 lease are live.  0532a releases both before the terminal
            # receipt, so review-stage manifests are immutable pre-terminal facts.
            #
            # This window backs the fresh two-connection review race below,
            # where the blocked racer's wait for pg_advisory_xact_lock is not
            # bounded by anything the harness controls -- only by however long
            # the winner's own round trip takes on whatever runner this test
            # is on. 240s is already ample against this Mac; it is not against
            # a contended CI runner, and this manifest also has to survive
            # every owner-acceptance call made against it further down.  Sized
            # generously rather than close to the metal, same principle as the
            # EXPIRING_SNAPSHOT_WINDOW_SECONDS comment above: insert-critical
            # windows get headroom, only the dedicated expiry tests stay tight.
            one(cur, "select pg_sleep(1.1)")
            review_coord = make_coord(cur, lease, session, host, seconds=600)
            review_input, review_manifest = compile_input(
                cur, lease, plan, rules, review_coord, session, host, commit="a" * 40,
                evidence_requirements=contract_evidence_requirements)
            review_row = record_manifest(cur, lease, "review", review_input,
                review_manifest, rules, review_coord, uuid.uuid4())
            review_manifest_id = manifest_id(review_row)
            one(cur, "select pg_sleep(1.1)")
            owner_only_coord = make_coord(cur, lease, session, host, seconds=600)
            owner_only_input, owner_only_manifest = compile_input(
                cur, lease, plan, rules, owner_only_coord, session, host,
                evidence_requirements=contract_evidence_requirements)
            owner_only_row = record_manifest(cur, lease, "review", owner_only_input,
                owner_only_manifest, rules, owner_only_coord, uuid.uuid4())
            owner_only_manifest_id = manifest_id(owner_only_row)

            cur.execute("""insert into ops.canonical_ownership_claim(
              lease_id,organization_tenant_id,claim_kind,claim_value,claim_mode,operation)
              values(%s,%s,'resource','resource:a3a-unexpected','resource','claim')""",
                (lease["lease_id"], tenant))
            refusal("activated unregistered resource claim makes compiler scope stale", call(
                cur, "ops.assurance_validate_compiler_input", (
                    lease["lease_id"], Jsonb(compiler_input), Jsonb(post_manifest),
                )), "ASSURANCE_BINDING_STALE", "compiler_input.assurance_slice.scope")

            # The append records start only after the exact 0532a transition.
            # Their evidence timestamp is captured before release and must not
            # extend the old live authority window.
            terminal_evidence_time = one(
                cur, "select date_trunc('second',clock_timestamp())")[0]
            cc.set_jobs(cur)
            receipt_id = cc.receipt(cur, fixture, claim, "claimed_complete")
            cc.reset_role(cur)
            a2.insert_review(cur, fixture, receipt_id)
            released_at = one(cur, "select released_at from ops.canonical_ownership_lease where id=%s",
                              (lease["lease_id"],))[0]
            check("0532a releases the A2 lease before terminal assurance append",
                  released_at is not None and terminal_evidence_time <= released_at)
            conn.commit()
            refusal("post-terminal manifest creation is refused", record_manifest(
                cur, lease, "post_commit", compiler_input, post_manifest, rules, coord,
                uuid.uuid4()), "IDENTITY_CONTEXT_INVALID", "identity_context")
            forged_terminal = one(cur, "select ops.assurance_terminal_evidence_lineage_current(%s,%s,clock_timestamp(),%s,%s)",
                (lease["lease_id"], dependency_receipt_id, lease["lease_token"],
                 lease["fencing_generation"]))[0]
            refusal("forged terminal receipt cannot authorize assurance append", forged_terminal,
                    "ASSURANCE_BINDING_STALE", "lease.terminal_lineage")
            manual_terminal = one(cur, "select ops.assurance_terminal_receipt_lineage_current(%s,%s,clock_timestamp())",
                (manual_release_id, receipt_id))[0]
            refusal("manual release cannot masquerade as the 0532a transition", manual_terminal,
                    "ASSURANCE_BINDING_STALE", "lease.terminal_lineage")
            expired_terminal_id = uuid.uuid4()
            cur.execute("""insert into ops.canonical_ownership_lease(
              id,organization_tenant_id,holder_actor_id,holder_actor_slug,holder_session_ref,
              holder_host_ref,lease_token,fencing_generation,work_request_id,work_request_version,
              work_request_digest,accepted_plan_id,accepted_plan_digest,slice_plan_id,slice_plan_digest,
              slice_ref,subject_envelope_id,contract_digest,state,acquired_at,expires_at,released_at,
              created_at,updated_at)
              select %s,organization_tenant_id,holder_actor_id,holder_actor_slug,holder_session_ref,
                holder_host_ref,%s,nextval('ops.canonical_ownership_fencing_generation'),work_request_id,
                work_request_version,work_request_digest,accepted_plan_id,accepted_plan_digest,slice_plan_id,
                slice_plan_digest,slice_ref,subject_envelope_id,contract_digest,'expired',
                clock_timestamp()-interval '2 hours',clock_timestamp()-interval '1 hour',null,
                clock_timestamp(),clock_timestamp() from ops.canonical_ownership_lease where id=%s""",
                (expired_terminal_id, uuid.uuid4(), lease["lease_id"]))
            expired_terminal = one(cur, "select ops.assurance_append_lineage_current(%s,%s,clock_timestamp(),%s,%s)",
                (expired_terminal_id, receipt_id, lease["lease_token"],
                 lease["fencing_generation"]))[0]
            refusal("expired lease cannot authorize terminal assurance append", expired_terminal,
                    "ASSURANCE_BINDING_STALE", "lease.currentness")

            # Evidence has one canonical home for each observed field. Requirement
            # coverage points at those homes through pinned JSON pointers.
            manifest_requirements = post_manifest["slice"]["evidence_requirements"]
            artifact_kind = manifest_requirements[0]["artifact_kind"]
            required_fields = manifest_requirements[0]["required_fields"]
            pinned = {
                "argv": "/command/argv", "cwd": "/command/cwd",
                "commit_sha": "/repository/commit_sha", "tree_sha": "/repository/tree_sha",
                "environment": "/environment", "toolchain": "/toolchain",
                "output": "/output", "timestamps": "/timestamps", "artifacts": "/artifacts",
                "exit_code": "/output/exit_code", "stdout_digest": "/output/stdout_digest",
                "stderr_digest": "/output/stderr_digest",
            }
            evidence_artifacts = []
            evidence_requirements = []
            for ordinal, requirement in enumerate(manifest_requirements, start=1):
                artifact_ref = f"artifact:a3a-test-receipt-{ordinal}"
                evidence_artifacts.append({
                    "artifact_ref": artifact_ref,
                    "path": f"out/a3a-test-receipt-{ordinal}.json",
                    "digest": "sha256:" + str(ordinal + 4) * 64,
                    "artifact_kind": requirement["artifact_kind"],
                })
                evidence_requirements.append({
                    "evidence_ref": requirement["evidence_ref"],
                    "artifact_kind": requirement["artifact_kind"],
                    "field_bindings": {
                        field: pinned[field] for field in requirement["required_fields"]
                    },
                    "artifact_refs": [artifact_ref],
                })
            evidence = {
                "schema_version": "assurance-evidence.v1",
                "manifest_hash": post_manifest["manifest_hash"],
                "engineering_receipt_digest": one(cur, "select receipt_digest from ops.engineering_slice_receipt where id=%s", (receipt_id,))[0],
                "repository": {"commit_sha": "a" * 40, "tree_sha": "b" * 40, "stage": "post_commit"},
                "command": {"argv": ["python3", "-m", "pytest", "-q"], "cwd": "."},
                "environment": {"environment_ref": "environment:repository-python-lock",
                                "network_access": False},
                "toolchain": {"runtime": "python3", "runtime_version": "3.12",
                              "database": "postgresql", "database_version": "17"},
                "output": {"exit_code": 0, "stdout_digest": "sha256:" + "3" * 64, "stderr_digest": "sha256:" + "4" * 64},
                "timestamps": {"started_at": iso(terminal_evidence_time),
                               "finished_at": iso(terminal_evidence_time)},
                "artifacts": evidence_artifacts,
                "requirements": evidence_requirements,
                "fencing_generation": lease["fencing_generation"],
            }
            for non_post_stage, non_post_id in sorted(non_post_manifest_ids.items()):
                refusal(f"non-post-commit {non_post_stage} evidence refuses", call(
                    cur, "ops.record_assurance_evidence_extension", (
                        receipt_id, non_post_id, lease["lease_token"], Jsonb(evidence),
                        digest(evidence), uuid.uuid4(),
                    )), "EVIDENCE_STAGE_UNSUPPORTED", "manifest.repository_stage")
            evidence_key = uuid.uuid4(); evidence_digest = digest(evidence)
            conn.commit()
            evidence_setup = lambda race_cur: set_context(
                race_cur, tenant, "codex", session, host)
            fresh_evidence_race = race_calls(
                dsn, [evidence_setup, evidence_setup],
                "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                    evidence_digest, evidence_key,
                ))
            check("fresh evidence insert race is serialized",
                  len(fresh_evidence_race) == 2
                  and all(row.get("ok") is True for row in fresh_evidence_race)
                  and len({row.get("evidence_id") for row in fresh_evidence_race}) == 1
                  and sum(row.get("replayed") is False for row in fresh_evidence_race) == 1
                  and sum(row.get("replayed") is True for row in fresh_evidence_race) == 1,
                  safe(fresh_evidence_race))
            ev = next(row for row in fresh_evidence_race if row.get("replayed") is False)
            check("one post-commit evidence extension persists", ev.get("ok") is True)
            ev_id = uuid.UUID(str(ev["evidence_id"]))
            check("valid multi-requirement evidence persists exactly", one(cur, """select
              jsonb_array_length(evidence->'requirements')=2
              and jsonb_array_length(evidence->'artifacts')=2
              from ops.assurance_evidence_extension where id=%s""", (ev_id,))[0])
            ev_replay = call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence), evidence_digest, evidence_key,
            ))
            check("evidence exact replay is idempotent", ev_replay.get("replayed") is True and ev_replay.get("evidence_id") == ev.get("evidence_id"))
            changed_ev = copy.deepcopy(evidence); changed_ev["output"]["exit_code"] = 9
            refusal("evidence key reuse with changed content refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(changed_ev), digest(changed_ev), evidence_key,
            )), "IDEMPOTENCY_CONFLICT", "assurance_evidence_extension.idempotency_key")
            refusal("second evidence for one receipt refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence), evidence_digest, uuid.uuid4(),
            )), "ASSURANCE_BINDING_STALE", "assurance_evidence_extension.one_to_one")
            bad_pointer = copy.deepcopy(evidence); bad_pointer["requirements"][0]["field_bindings"][required_fields[0]] = "/output"
            refusal("unpinned evidence pointer refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(bad_pointer), digest(bad_pointer), uuid.uuid4(),
            )), "EVIDENCE_POINTER_INVALID", "evidence.field_bindings." + required_fields[0])
            bad_kind = copy.deepcopy(evidence); bad_kind["requirements"][0]["artifact_kind"] = "artifact:wrong"
            refusal("requirement artifact-kind drift refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(bad_kind), digest(bad_kind), uuid.uuid4(),
            )), "EVIDENCE_REQUIREMENT_MISMATCH",
                "evidence.requirements." + manifest_requirements[0]["evidence_ref"])
            missing_requirement = copy.deepcopy(evidence)
            missing_requirement["requirements"].pop()
            refusal("missing manifest evidence requirement refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(missing_requirement),
                    digest(missing_requirement), uuid.uuid4(),
                )), "EVIDENCE_REQUIREMENT_MISMATCH", "evidence.requirements")
            extra_requirement = copy.deepcopy(evidence)
            extra_result = copy.deepcopy(extra_requirement["requirements"][0])
            extra_result["evidence_ref"] = "evidence:a3a-unexpected"
            extra_requirement["requirements"].append(extra_result)
            refusal("extra manifest evidence requirement refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(extra_requirement),
                    digest(extra_requirement), uuid.uuid4(),
                )), "EVIDENCE_REQUIREMENT_MISMATCH", "evidence.requirements")
            missing_binding = copy.deepcopy(evidence)
            del missing_binding["requirements"][0]["field_bindings"][required_fields[0]]
            refusal("missing required field binding refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(missing_binding),
                    digest(missing_binding), uuid.uuid4(),
                )), "EVIDENCE_REQUIREMENT_MISMATCH",
                "evidence.requirements.field_bindings")
            extra_binding = copy.deepcopy(evidence)
            extra_binding["requirements"][0]["field_bindings"]["unexpected"] = "/output"
            refusal("extra required field binding refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(extra_binding),
                    digest(extra_binding), uuid.uuid4(),
                )), "EVIDENCE_REQUIREMENT_MISMATCH",
                "evidence.requirements.field_bindings")
            for field in ("evidence_ref", "artifact_kind"):
                for type_label, value in (("JSON null", None), ("numeric", 7)):
                    typed_requirement = copy.deepcopy(evidence)
                    typed_requirement["requirements"][0][field] = value
                    refusal(f"requirement {type_label} {field} refuses", call(
                        cur, "ops.record_assurance_evidence_extension", (
                            receipt_id, post_id, lease["lease_token"],
                            Jsonb(typed_requirement), digest(typed_requirement), uuid.uuid4(),
                        )), "EVIDENCE_REQUIREMENT_MISMATCH",
                        "evidence.requirements." + manifest_requirements[0]["evidence_ref"])
            for type_label, value in (("JSON null", None), ("numeric", 7)):
                typed_artifact = copy.deepcopy(evidence)
                typed_artifact["artifacts"][0]["artifact_kind"] = value
                refusal(f"artifact {type_label} artifact_kind refuses", call(
                    cur, "ops.record_assurance_evidence_extension", (
                        receipt_id, post_id, lease["lease_token"], Jsonb(typed_artifact),
                        digest(typed_artifact), uuid.uuid4(),
                    )), "EVIDENCE_ARTIFACT_MISMATCH", "evidence.artifacts")
            refusal("wrong evidence digest refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                    "sha256:" + "f" * 64, uuid.uuid4(),
                )), "ASSURANCE_DIGEST_MISMATCH", "evidence_digest")
            wrong_repository = copy.deepcopy(evidence)
            wrong_repository["repository"]["commit_sha"] = "f" * 40
            refusal("wrong repository binding refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(wrong_repository),
                    digest(wrong_repository), uuid.uuid4(),
                )), "ASSURANCE_BINDING_STALE", "evidence.lineage")
            wrong_receipt = copy.deepcopy(evidence)
            wrong_receipt["engineering_receipt_digest"] = "sha256:" + "f" * 64
            refusal("wrong receipt binding refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(wrong_receipt),
                    digest(wrong_receipt), uuid.uuid4(),
                )), "ASSURANCE_BINDING_STALE", "evidence.lineage")
            wrong_fence = copy.deepcopy(evidence)
            wrong_fence["fencing_generation"] = lease["fencing_generation"] + 1
            refusal("wrong fencing generation refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(wrong_fence),
                    digest(wrong_fence), uuid.uuid4(),
                )), "ASSURANCE_BINDING_STALE", "evidence.lineage")
            reversed_timestamps = copy.deepcopy(evidence)
            reversed_timestamps["timestamps"]["finished_at"] = iso(
                terminal_evidence_time - timedelta(seconds=1))
            refusal("reversed evidence timestamps refuse", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(reversed_timestamps),
                    digest(reversed_timestamps), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence.timestamps")
            future_timestamps = copy.deepcopy(evidence)
            future_timestamps["timestamps"]["started_at"] = iso(
                terminal_evidence_time + timedelta(days=1))
            future_timestamps["timestamps"]["finished_at"] = (
                future_timestamps["timestamps"]["started_at"])
            refusal("future evidence timestamps refuse", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(future_timestamps),
                    digest(future_timestamps), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence.timestamps")
            refusal("cross-lineage receipt refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    dependency_receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                    evidence_digest, uuid.uuid4(),
                )), "ASSURANCE_BINDING_STALE", "lease.terminal_lineage")
            for ordinal, spelling in enumerate(sorted(token_spellings(lease["lease_token"]))):
                token_evidence = copy.deepcopy(evidence)
                token_evidence["toolchain"]["runtime_version"] = spelling
                refusal(f"evidence token spelling {ordinal + 1} refuses", call(
                    cur, "ops.record_assurance_evidence_extension", (
                        receipt_id, post_id, lease["lease_token"], Jsonb(token_evidence),
                        digest(token_evidence), uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
                refusal(f"evidence digest token spelling {ordinal + 1} refuses", call(
                    cur, "ops.record_assurance_evidence_extension", (
                        receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                        spelling, uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            refusal("evidence idempotency key cannot equal lease token", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                    evidence_digest, uuid.UUID(str(lease["lease_token"])),
                )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            foreign_evidence = copy.deepcopy(evidence)
            foreign_evidence["toolchain"]["runtime_version"] = str(foreign_token)
            refusal("foreign minted token refuses in evidence", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(foreign_evidence),
                    digest(foreign_evidence), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            string_fence = copy.deepcopy(evidence)
            string_fence["fencing_generation"] = str(lease["fencing_generation"])
            refusal("string evidence fence refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(string_fence),
                    digest(string_fence), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence")
            duplicate_artifact_ref = copy.deepcopy(evidence)
            duplicate_artifact_ref["requirements"][0]["artifact_refs"].append(
                duplicate_artifact_ref["requirements"][0]["artifact_refs"][0])
            refusal("duplicate requirement artifact_refs refuse", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(duplicate_artifact_ref),
                    digest(duplicate_artifact_ref), uuid.uuid4(),
                )), "EVIDENCE_REQUIREMENT_MISMATCH",
                "evidence.requirements." +
                post_manifest["slice"]["evidence_requirements"][0]["evidence_ref"])
            extra_evidence = copy.deepcopy(evidence); extra_evidence["unexpected"] = True
            refusal("extra evidence field refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(extra_evidence),
                    digest(extra_evidence), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence")
            missing_evidence = copy.deepcopy(evidence); del missing_evidence["output"]
            refusal("absent pinned evidence output refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(missing_evidence),
                    digest(missing_evidence), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence")
            null_evidence = copy.deepcopy(evidence); null_evidence["output"] = None
            refusal("JSON null pinned evidence output refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(null_evidence),
                    digest(null_evidence), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence")
            object_artifacts = copy.deepcopy(evidence)
            object_artifacts["artifacts"] = object_artifacts["artifacts"][0]
            refusal("object evidence artifacts refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(object_artifacts),
                    digest(object_artifacts), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "evidence")
            unreferenced_artifact = copy.deepcopy(evidence)
            unreferenced_artifact["artifacts"].append({
                "artifact_ref": "artifact:a3a-unreferenced",
                "path": "out/a3a-unreferenced.json",
                "digest": "sha256:" + "6" * 64,
                "artifact_kind": artifact_kind,
            })
            refusal("unreferenced evidence artifact refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, post_id, lease["lease_token"], Jsonb(unreferenced_artifact),
                    digest(unreferenced_artifact), uuid.uuid4(),
                )), "EVIDENCE_ARTIFACT_MISMATCH", "evidence.artifacts")
            for label, path, evidence_value in (
                ("null repository stage", ("repository", "stage"), None),
                ("numeric repository commit", ("repository", "commit_sha"), 7),
                ("null command cwd", ("command", "cwd"), None),
                ("null stdout digest", ("output", "stdout_digest"), None),
                ("string exit code", ("output", "exit_code"), "0"),
            ):
                typed_evidence = copy.deepcopy(evidence)
                typed_evidence[path[0]][path[1]] = evidence_value
                refusal(f"evidence {label} refuses", call(
                    cur, "ops.record_assurance_evidence_extension", (
                        receipt_id, post_id, lease["lease_token"], Jsonb(typed_evidence),
                        digest(typed_evidence), uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "evidence")

            refusal("non-post-commit review evidence refuses", call(
                cur, "ops.record_assurance_evidence_extension", (
                    receipt_id, review_manifest_id, lease["lease_token"], Jsonb(evidence),
                    digest(evidence), uuid.uuid4(),
                )), "EVIDENCE_STAGE_UNSUPPORTED", "manifest.repository_stage")
            reviewer_fact_id, reviewer_session, reviewer_fact, reviewer_created_at = one(cur,
                """select id,reviewer_session_ref,fact,date_trunc('second',created_at)
                   from ops.engineering_reviewer_fact where receipt_id=%s""",
                (receipt_id,))
            check("Passport engineering-review.v1 fact is exact", one(cur, """select
              contract_version='engineering-review.v1'
              and (select array_agg(key order by key) from jsonb_object_keys(fact) key)=array[
                'attempt_id','evidence_refs','is_independent','resolved_deviation_refs',
                'reviewed_deviation_refs','reviewer_ref','session_ref','slice_ref','state']::text[]
              from ops.engineering_reviewer_fact where id=%s""", (reviewer_fact_id,))[0])
            review_host = "host:a3a-independent-review"
            set_context(cur, tenant, "joe", reviewer_session, review_host)
            review = {
                "schema_version": "assurance-review.v1", "manifest_hash": review_manifest["manifest_hash"],
                "evidence_digest": evidence_digest, "state": "passed", "self_issued": False,
                "owner_acceptance": False, "reviewer_actor_ref": "actor:joe",
                "reviewer_session_ref": reviewer_session, "reviewer_host_ref": review_host,
                "evidence_refs": reviewer_fact["evidence_refs"],
                "reviewed_deviation_refs": reviewer_fact["reviewed_deviation_refs"],
                "resolved_deviation_refs": reviewer_fact["resolved_deviation_refs"],
                "reviewed_at": iso(reviewer_created_at),
            }
            review_key = uuid.uuid4(); review_digest = digest(review)
            conn.commit()
            fresh_review_setup = lambda race_cur: set_context(
                race_cur, tenant, "joe", reviewer_session, review_host)
            fresh_review_race = race_calls(
                dsn, [fresh_review_setup, fresh_review_setup],
                "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review),
                    review_digest, review_key,
                ))
            # This must be a check(), not a raise. Two connections are racing
            # an identical idempotency key behind the function's own
            # pg_advisory_xact_lock, so the blocked racer's wait is bounded
            # only by however long the winner's own round trip takes -- and on
            # a contended runner that wait is not bounded by anything this
            # harness controls. A RuntimeError here used to abort the whole
            # 200+ check suite on that one anomaly, discarding every
            # diagnostic the rest of the file would otherwise have produced.
            # Every sibling fresh-insert race in this file (see "owner
            # conflicting fresh insert race is atomic" below) already reports
            # through check() instead; this one is brought in line with that.
            review_serialized = (
                len(fresh_review_race) == 2
                and all(row.get("ok") is True for row in fresh_review_race)
                and len({row.get("review_id") for row in fresh_review_race}) == 1
                and sum(row.get("replayed") is False for row in fresh_review_race) == 1
                and sum(row.get("replayed") is True for row in fresh_review_race) == 1)
            check("fresh review insert race is serialized", review_serialized,
                  safe(fresh_review_race))
            review_result = next(
                (row for row in fresh_review_race if row.get("replayed") is False), {})
            check("independent review extends existing Passport fact", review_result.get("ok") is True)
            review_replay = call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review), review_digest, review_key,
            ))
            check("review exact replay is idempotent", review_replay.get("replayed") is True)
            null_review = copy.deepcopy(review); null_review["reviewer_host_ref"] = None
            refusal("null reviewer identity refuses", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(null_review),
                    digest(null_review), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "review")
            for label, field, review_value in (
                ("numeric manifest hash", "manifest_hash", 7),
                ("null evidence digest", "evidence_digest", None),
                ("null state", "state", None),
                ("string self-issued flag", "self_issued", "false"),
                ("scalar evidence refs", "evidence_refs", "evidence:scalar"),
            ):
                typed_review = copy.deepcopy(review)
                typed_review[field] = review_value
                refusal(f"review {label} refuses", call(
                    cur, "ops.record_assurance_review_extension", (
                        reviewer_fact_id, review_manifest_id, ev_id, Jsonb(typed_review),
                        digest(typed_review), uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "review")
            extra_review = copy.deepcopy(review); extra_review["unexpected"] = True
            refusal("extra review field refuses", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(extra_review),
                    digest(extra_review), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "review")
            refusal("cross-stage review lineage refuses", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, post_id, ev_id, Jsonb(review),
                    review_digest, uuid.uuid4(),
                )), "ASSURANCE_BINDING_STALE", "review.lineage")
            for ordinal, spelling in enumerate(sorted(token_spellings(lease["lease_token"]))):
                token_review = copy.deepcopy(review)
                token_review["evidence_refs"] = [spelling]
                refusal(f"review token spelling {ordinal + 1} refuses", call(
                    cur, "ops.record_assurance_review_extension", (
                        reviewer_fact_id, review_manifest_id, ev_id, Jsonb(token_review),
                        digest(token_review), uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
                refusal(f"review digest token spelling {ordinal + 1} refuses", call(
                    cur, "ops.record_assurance_review_extension", (
                        reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review),
                        spelling, uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            refusal("review idempotency key cannot equal lease token", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review),
                    review_digest, uuid.UUID(str(lease["lease_token"])),
                )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            foreign_review = copy.deepcopy(review)
            foreign_review["evidence_refs"] = [str(foreign_token)]
            refusal("foreign minted token refuses in review", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(foreign_review),
                    digest(foreign_review), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            changed_review = copy.deepcopy(review); changed_review["reviewed_at"] = "2099-01-01T00:00:00Z"
            refusal("review key reuse with changed content refuses", call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(changed_review), digest(changed_review), review_key,
            )), "ASSURANCE_INPUT_INVALID", "review.reviewed_at")
            backdated_review = copy.deepcopy(review)
            backdated_review["reviewed_at"] = iso(reviewer_created_at - timedelta(seconds=1))
            refusal("backdated review cannot predate its Passport fact", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(backdated_review),
                    digest(backdated_review), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "review.reviewed_at")
            reviewer_actor_id, executor_actor_id, executor_session = one(cur, """select
              f.reviewer_actor_id,r.executor_actor_id,r.receipt#>>'{attribution,session_ref}'
              from ops.engineering_reviewer_fact f
              join ops.engineering_slice_receipt r on r.id=f.receipt_id
              where f.id=%s""", (reviewer_fact_id,))
            cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
            cur.execute("""update ops.engineering_reviewer_fact set
              reviewer_actor_id=%s,
              fact=jsonb_set(fact,'{reviewer_ref}',to_jsonb(%s::text),false)
              where id=%s""", (executor_actor_id, "actor:codex", reviewer_fact_id))
            cur.execute("alter table ops.engineering_reviewer_fact enable trigger user")
            set_context(cur, tenant, "codex", reviewer_session, review_host)
            actor_self_review = copy.deepcopy(review)
            actor_self_review["reviewer_actor_ref"] = "actor:codex"
            refusal("executor actor self-review refuses independently", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(actor_self_review),
                    digest(actor_self_review), uuid.uuid4(),
                )), "ASSURANCE_SELF_REVIEW", "review.reviewer")
            cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
            cur.execute("""update ops.engineering_reviewer_fact set
              reviewer_actor_id=%s,
              reviewer_session_ref=%s,
              fact=jsonb_set(
                jsonb_set(fact,'{reviewer_ref}',to_jsonb(%s::text),false),
                '{session_ref}',to_jsonb(%s::text),false)
              where id=%s""", (
                  reviewer_actor_id, executor_session, "actor:joe", executor_session,
                  reviewer_fact_id))
            cur.execute("alter table ops.engineering_reviewer_fact enable trigger user")
            set_context(cur, tenant, "joe", executor_session, review_host)
            same_session_review = copy.deepcopy(review)
            same_session_review["reviewer_session_ref"] = executor_session
            refusal("executor session self-review refuses independently", call(
                cur, "ops.record_assurance_review_extension", (
                    reviewer_fact_id, review_manifest_id, ev_id, Jsonb(same_session_review),
                    digest(same_session_review), uuid.uuid4(),
                )), "ASSURANCE_SELF_REVIEW", "review.reviewer")
            cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
            cur.execute("""update ops.engineering_reviewer_fact set
              reviewer_session_ref=%s,
              fact=jsonb_set(fact,'{session_ref}',to_jsonb(%s::text),false)
              where id=%s""", (reviewer_session, reviewer_session, reviewer_fact_id))
            cur.execute("alter table ops.engineering_reviewer_fact enable trigger user")
            set_context(cur, tenant, "joe", reviewer_session, review_host)
        conn.commit()

        # Owner authority is session_user-derived. For this disposable database
        # only, a test login assumes the migration-owner role while retaining
        # carr_authority_joe as session_user; no production role is created.
        with conn.cursor() as cur:
            set_context(cur, tenant, "codex", session, host)
            owner_role = one(cur, "select current_user")[0]
            cur.execute("savepoint non_owner_authority")
            try:
                call(cur, "ops.record_assurance_owner_acceptance", (
                    review_manifest_id, ev_id, "accept", Jsonb({}), digest({}),
                    uuid.uuid4(),
                ))
            except psycopg.Error as exc:
                cur.execute("rollback to savepoint non_owner_authority")
                check("non-Joe/Dell session authority refuses",
                      "not an admitted human authority principal" in str(exc))
            else:
                cur.execute("rollback to savepoint non_owner_authority")
                check("non-Joe/Dell session authority refuses", False,
                      "unadmitted session_user reached owner door")
            cur.execute("""do $$ begin
              if not exists(select 1 from pg_roles where rolname='carr_authority_joe')
                then create role carr_authority_joe login; end if;
              if not exists(select 1 from pg_roles where rolname='carr_authority_dell')
                then create role carr_authority_dell login; end if;
            end $$""")
            cur.execute(f"grant {psycopg.sql.Identifier(owner_role).as_string(cur)} to carr_authority_joe")
            cur.execute(f"grant {psycopg.sql.Identifier(owner_role).as_string(cur)} to carr_authority_dell")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("set session authorization carr_authority_joe")
            cur.execute(psycopg.sql.SQL("set role {}").format(psycopg.sql.Identifier(owner_role)))
            set_context(cur, tenant, "dell", "session:a3a:owner", host)
            acceptance = {
                "schema_version": "assurance-owner-acceptance.v1",
                "manifest_hash": review_manifest["manifest_hash"], "evidence_digest": evidence_digest,
                "decision": "accept", "owner_acceptance": True, "independent_review": False,
                "actor_ref": "actor:joe", "session_ref": "session:a3a:owner", "host_ref": host,
                "reason": "fixture accepted",
                "decided_at": iso(one(cur, "select date_trunc('second',clock_timestamp())")[0]),
            }
            mismatch = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), uuid.uuid4(),
            ))
            refusal("Joe authority cannot use Dell trusted context", mismatch,
                    "OWNER_IDENTITY_MISMATCH", "owner.identity")
            set_context(cur, tenant, "joe", "session:a3a:owner", host)
            acceptance["decided_at"] = iso(one(
                cur, "select date_trunc('second',clock_timestamp())")[0])
            owner_key = uuid.uuid4()
            owner = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), owner_key,
            ))
            check("Joe owner acceptance persists separately", owner.get("ok") is True and owner.get("decision") == "accept")
            owner_only_acceptance = copy.deepcopy(acceptance)
            owner_only_acceptance.update({
                "manifest_hash": owner_only_manifest["manifest_hash"],
                "decision": "hold",
                "reason": "owner fact without assurance review row",
                "decided_at": iso(one(
                    cur, "select date_trunc('second',clock_timestamp())")[0]),
            })
            owner_without_review = call(cur, "ops.record_assurance_owner_acceptance", (
                owner_only_manifest_id, ev_id, "hold", Jsonb(owner_only_acceptance),
                digest(owner_only_acceptance), uuid.uuid4(),
            ))
            check("owner fact persists without an assurance-review row",
                  owner_without_review.get("ok") is True
                  and owner_without_review.get("decision") == "hold")
            owner_replay = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), owner_key,
            ))
            check("owner exact replay is idempotent", owner_replay.get("replayed") is True)
            for ordinal, spelling in enumerate(sorted(token_spellings(lease["lease_token"]))):
                token_owner = copy.deepcopy(acceptance)
                token_owner["reason"] = spelling
                refusal(f"owner token spelling {ordinal + 1} refuses", call(
                    cur, "ops.record_assurance_owner_acceptance", (
                        review_manifest_id, ev_id, "accept", Jsonb(token_owner),
                        digest(token_owner), uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
                refusal(f"acceptance digest token spelling {ordinal + 1} refuses", call(
                    cur, "ops.record_assurance_owner_acceptance", (
                        review_manifest_id, ev_id, "accept", Jsonb(acceptance),
                        spelling, uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            refusal("owner idempotency key cannot equal lease token", call(
                cur, "ops.record_assurance_owner_acceptance", (
                    review_manifest_id, ev_id, "accept", Jsonb(acceptance),
                    digest(acceptance), uuid.UUID(str(lease["lease_token"])),
                )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            foreign_owner = copy.deepcopy(acceptance)
            foreign_owner["reason"] = str(foreign_token)
            refusal("foreign minted token refuses in owner fact", call(
                cur, "ops.record_assurance_owner_acceptance", (
                    review_manifest_id, ev_id, "accept", Jsonb(foreign_owner),
                    digest(foreign_owner), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            numeric_reason = copy.deepcopy(acceptance); numeric_reason["reason"] = 7
            refusal("numeric owner reason refuses", call(
                cur, "ops.record_assurance_owner_acceptance", (
                    review_manifest_id, ev_id, "accept", Jsonb(numeric_reason),
                    digest(numeric_reason), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "owner_acceptance")
            for label, field, acceptance_value in (
                ("numeric manifest hash", "manifest_hash", 7),
                ("null evidence digest", "evidence_digest", None),
                ("null decision", "decision", None),
                ("string owner flag", "owner_acceptance", "true"),
                ("numeric actor ref", "actor_ref", 7),
            ):
                typed_acceptance = copy.deepcopy(acceptance)
                typed_acceptance[field] = acceptance_value
                refusal(f"owner {label} refuses", call(
                    cur, "ops.record_assurance_owner_acceptance", (
                        review_manifest_id, ev_id, "accept", Jsonb(typed_acceptance),
                        digest(typed_acceptance), uuid.uuid4(),
                    )), "ASSURANCE_INPUT_INVALID", "owner_acceptance")
            backdated_acceptance = copy.deepcopy(acceptance)
            backdated_acceptance["decided_at"] = iso(one(
                cur, "select date_trunc('second',clock_timestamp())-interval '31 seconds'")[0])
            refusal("backdated owner decision refuses", call(
                cur, "ops.record_assurance_owner_acceptance", (
                    review_manifest_id, ev_id, "accept", Jsonb(backdated_acceptance),
                    digest(backdated_acceptance), uuid.uuid4(),
                )), "ASSURANCE_INPUT_INVALID", "owner_acceptance.decided_at")
            changed_owner = copy.deepcopy(acceptance); changed_owner["decision"] = "hold"
            changed_owner["decided_at"] = iso(one(
                cur, "select date_trunc('second',clock_timestamp())")[0])
            refusal("owner key reuse with changed content refuses", call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "hold", Jsonb(changed_owner), digest(changed_owner), owner_key,
            )), "IDEMPOTENCY_CONFLICT",
                "assurance_owner_acceptance_fact.idempotency_key")
            cur.execute("reset role")
            cur.execute("reset session authorization")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("set session authorization carr_authority_dell")
            cur.execute(psycopg.sql.SQL("set role {}").format(psycopg.sql.Identifier(owner_role)))
            set_context(cur, tenant, "dell", "session:a3a:dell-owner", host)
            for decision in ("hold", "reject"):
                dell_acceptance = copy.deepcopy(acceptance)
                dell_acceptance.update({
                    "decision": decision,
                    "actor_ref": "actor:dell",
                    "session_ref": "session:a3a:dell-owner",
                    "reason": f"Dell fixture {decision}",
                    "decided_at": iso(one(
                        cur, "select date_trunc('second',clock_timestamp())")[0]),
                })
                dell = call(cur, "ops.record_assurance_owner_acceptance", (
                    review_manifest_id, ev_id, decision, Jsonb(dell_acceptance),
                    digest(dell_acceptance), uuid.uuid4(),
                ))
                check(f"Dell owner {decision} persists separately",
                      dell.get("ok") is True and dell.get("decision") == decision)
            cur.execute("reset role")
            cur.execute("reset session authorization")
        conn.commit()

        setup_codex = lambda cur: set_context(cur, tenant, "codex", session, host)
        evidence_race = race_calls(dsn, [setup_codex, setup_codex],
            "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                evidence_digest, evidence_key))
        check("evidence two-connection exact replay is serialized",
              len(evidence_race) == 2 and all(row.get("replayed") is True for row in evidence_race))
        setup_review = lambda cur: set_context(cur, tenant, "joe", reviewer_session, review_host)
        review_race = race_calls(dsn, [setup_review, setup_review],
            "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review),
                review_digest, review_key))
        check("review two-connection exact replay is serialized",
              len(review_race) == 2 and all(row.get("replayed") is True for row in review_race))
        def setup_owner(cur):
            cur.execute("set session authorization carr_authority_joe")
            cur.execute(psycopg.sql.SQL("set role {}").format(
                psycopg.sql.Identifier(owner_role)))
            set_context(cur, tenant, "joe", "session:a3a:owner", host)
        owner_race = race_calls(dsn, [setup_owner, setup_owner],
            "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance),
                digest(acceptance), owner_key))
        check("owner two-connection exact replay is serialized",
              len(owner_race) == 2 and all(row.get("replayed") is True for row in owner_race))
        owner_conflict_key = uuid.uuid4()
        owner_accept_new = copy.deepcopy(acceptance)
        owner_accept_new.update({"reason": "conflicting owner race accept",
                                 "decided_at": iso(datetime.now(timezone.utc))})
        owner_hold = copy.deepcopy(acceptance)
        owner_hold.update({"decision": "hold", "reason": "conflicting owner race",
                           "decided_at": iso(datetime.now(timezone.utc))})
        owner_conflict = race_arg_calls(dsn, [setup_owner, setup_owner],
            "ops.record_assurance_owner_acceptance", [
                (review_manifest_id, ev_id, "accept", Jsonb(owner_accept_new),
                 digest(owner_accept_new), owner_conflict_key),
                (review_manifest_id, ev_id, "hold", Jsonb(owner_hold),
                 digest(owner_hold), owner_conflict_key),
            ])
        check("owner conflicting fresh insert race is atomic",
              len(owner_conflict) == 2
              and sum(row.get("ok") is True for row in owner_conflict) == 1
              and sum(row.get("refusal", {}).get("code") == "IDEMPOTENCY_CONFLICT"
                      for row in owner_conflict) == 1)

        with conn.cursor() as cur:
            cur.execute("""update ops.job_attempt set lease_token=%s
              where id=(select job_attempt_id from ops.engineering_slice_receipt where id=%s)""",
                (uuid.uuid4(), receipt_id))
            attempt_hash_mismatch = one(cur, """select
              ops.assurance_terminal_evidence_lineage_current(
                %s,%s,clock_timestamp(),%s,%s)""", (
                lease["lease_id"], receipt_id, lease["lease_token"],
                lease["fencing_generation"],))[0]
            refusal("terminal append refuses a mismatched job-attempt capability hash",
                    attempt_hash_mismatch, "ASSURANCE_BINDING_STALE",
                    "lease.terminal_lineage")
        conn.commit()

        with conn.cursor() as cur:
            persisted = one(cur, """select jsonb_build_object(
              'manifest',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_execution_manifest t),'[]'::jsonb),
              'evidence',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_evidence_extension t),'[]'::jsonb),
              'review',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_review_extension t),'[]'::jsonb),
              'owner',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_owner_acceptance_fact t),'[]'::jsonb))""")[0]
            assert_secret_absent(persisted, "all persisted A3a JSON/text columns")
            check("raw A3a results and exceptions contain no registered token",
                  all(token not in json.dumps(RAW_RESULTS, default=str)
                      and all(token not in error for error in RAW_ERRORS)
                      for token in SECRET_TOKENS))
            check("owner acceptance cannot satisfy review structurally", one(cur,
                "select count(*) from ops.assurance_review_extension")[0]
                == review_rows_before + 1 and one(cur,
                "select count(*) from ops.assurance_owner_acceptance_fact")[0]
                == owner_rows_before + 5)
            check("owner-only manifest has no assurance-review extension", one(cur, """
              select not exists(select 1 from ops.assurance_review_extension
                where review_manifest_id=%s)""", (owner_only_manifest_id,))[0])
            check("owner table has no review row or foreign-key dependency", one(cur, """
              select not exists(select 1 from pg_attribute
                where attrelid='ops.assurance_owner_acceptance_fact'::regclass
                  and attname='review_id' and not attisdropped)
                and not exists(select 1 from pg_constraint
                  where conrelid='ops.assurance_owner_acceptance_fact'::regclass
                    and confrelid='ops.assurance_review_extension'::regclass)""")[0])
            check("owner door has no assurance-review row dependency", one(cur, """
              select position('assurance_review_extension' in
                pg_get_functiondef('ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid)'::regprocedure))=0""")[0])
            for table in ["assurance_execution_manifest", "assurance_evidence_extension",
                          "assurance_review_extension", "assurance_owner_acceptance_fact"]:
                for verb in ("update", "delete"):
                    cur.execute("savepoint append_only")
                    try:
                        statement = ("update ops.{} set created_at=created_at where true"
                                     if verb == "update" else "delete from ops.{} where true")
                        cur.execute(psycopg.sql.SQL(statement).format(
                            psycopg.sql.Identifier(table)))
                    except psycopg.Error as exc:
                        cur.execute("rollback to savepoint append_only")
                        check(f"{table} rejects {verb}", "append-only" in str(exc))
                    else:
                        cur.execute("rollback to savepoint append_only")
                        check(f"{table} rejects {verb}", False, f"{verb} succeeded")
            actual_tables = one(cur, """select array_agg(c.oid::regclass::text
              order by c.oid::regclass::text) from pg_class c
              join pg_namespace n on n.oid=c.relnamespace
              where n.nspname='ops' and c.relkind='r'
                and c.relname like 'assurance_%%'""")[0]
            actual_functions = one(cur, """select array_agg(p.oid::regprocedure::text
              order by p.oid::regprocedure::text) from pg_proc p
              join pg_namespace n on n.oid=p.pronamespace
              where n.nspname='ops' and (p.proname like 'assurance_%%'
                or p.proname=any(array['record_assurance_execution_manifest',
                  'record_assurance_evidence_extension','record_assurance_review_extension',
                  'record_assurance_owner_acceptance','refuse_assurance_persistence_rewrite']))""")[0]
            check("exact A3a table catalog is pinned",
                  actual_tables == EXPECTED_A3A_TABLES)
            check("exact A3a function catalog is pinned",
                  actual_functions == EXPECTED_A3A_FUNCTIONS)
            no_external_acl = one(cur, """select
              not exists(select 1 from pg_class c
                join pg_namespace n on n.oid=c.relnamespace cross join lateral
                aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
                where n.nspname='ops' and c.relname like 'assurance_%%'
                  and c.relkind='r' and acl.grantee<>c.relowner
                  -- The dedicated review login reads all business tables.
                  -- Only its non-grantable SELECT is authorized here.
                  and not coalesce((acl.grantee=to_regrole('dot_reader')
                    and acl.privilege_type='SELECT' and not acl.is_grantable),false))
              and not exists(select 1 from pg_proc p
                join pg_namespace n on n.oid=p.pronamespace cross join lateral
                aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
                where n.nspname='ops' and (p.proname like 'assurance_%%'
                  or p.proname=any(array['record_assurance_execution_manifest',
                    'record_assurance_evidence_extension','record_assurance_review_extension',
                    'record_assurance_owner_acceptance','refuse_assurance_persistence_rewrite']))
                  and acl.grantee<>p.proowner
                  and not (p.oid=any(array[
                    'ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)'::regprocedure,
                    'ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid)'::regprocedure])
                    and acl.grantee='carr_ownership_issuer'::regrole))""")[0]
            check("A3a tables allow only owner authority and Dot SELECT; functions retain exact callers", no_external_acl)
            issuer_functions = one(cur, """select coalesce(array_agg(p.oid::regprocedure::text
              order by p.oid::regprocedure::text),'{}'::text[]) from pg_proc p cross join lateral
              aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
              join pg_roles role on role.oid=acl.grantee
              where p.oid=any(array[
                'ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)'::regprocedure,
                'ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid)'::regprocedure])
                and acl.privilege_type='EXECUTE'
                and acl.grantee='carr_ownership_issuer'::regrole""")[0]
            check("A3a issuer can call only manifest admission and currentness",
                  issuer_functions == [
                      "ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid)",
                      "ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)",
                  ])
            posture_rows = cur.execute("""select
              p.oid::regprocedure::text,p.prosecdef,p.provolatile,
              coalesce(p.proconfig[1],'')
              from pg_proc p join pg_namespace n on n.oid=p.pronamespace
              where n.nspname='ops' and (p.proname like 'assurance_%%'
                or p.proname=any(array['record_assurance_execution_manifest',
                  'record_assurance_evidence_extension','record_assurance_review_extension',
                  'record_assurance_owner_acceptance','refuse_assurance_persistence_rewrite']))
              order by p.oid::regprocedure::text""").fetchall()
            actual_function_posture = {
                signature: (security_definer, volatility, config)
                for signature, security_definer, volatility, config in posture_rows
            }
            check("exact assurance posture is pinned for all 32 functions",
                  actual_function_posture == EXPECTED_A3A_FUNCTION_POSTURE,
                  f"actual={safe(actual_function_posture)}")
            check("A3a schema fingerprint is invariant across all tests",
                  schema_fingerprint(cur) == schema_before)

    summary = {
        "contract": "assurance-evidence-acceptance-local-pg.v1",
        "passed": PASS, "failed": FAIL, "refusals": dict(sorted(REFUSALS.items())),
        "token_nondisclosure": all(token not in json.dumps(REFUSALS) for token in SECRET_TOKENS),
        "provider": "disposable_loopback_postgresql",
    }
    print(json.dumps(summary, sort_keys=True))
    if FAILED_LABELS:
        print("assurance A3a failed labels: " + " | ".join(FAILED_LABELS),
              file=sys.stderr)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        failure_tb = exc.__traceback__
        while failure_tb is not None and failure_tb.tb_next is not None:
            failure_tb = failure_tb.tb_next
        failure_line = failure_tb.tb_lineno if failure_tb is not None else "unknown"
        print(
            f"assurance A3a gate failed at line {failure_line}: "
            f"{type(exc).__name__}: {safe(exc)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
