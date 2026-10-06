#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate for the typed Guidance Registry.

The gate deliberately uses a single transaction: its proposed revisions,
authority receipts, lifecycle events and situation bridge evidence must never
survive an acceptance run.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from typing import Any, NoReturn

import psycopg
from psycopg.types.json import Jsonb


TABLES = (
    "ops.guidance_registry", "ops.guidance_item", "ops.guidance_revision",
    "ops.guidance_authority_binding", "ops.guidance_lifecycle_event",
    "ops.guidance_situation_mapping", "ops.guidance_registry_event",
    "ops.guidance_import_batch", "ops.guidance_import_entry",
    "ops.guidance_import_apply_event", "ops.guidance_import_mapping_execution",
    "ops.guidance_import_decision_event",
)
FUNCTIONS = (
    "ops.guidance_revision_contract_hash(uuid)",
    "ops.record_guidance_decision(uuid,text,text,text)",
    "ops.propose_guidance_situation_mapping(uuid,uuid,uuid,text)",
    "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
    "ops.assert_guidance_registry_coverage()",
    "ops.standing_guidance(text,text,text,text)",
    "ops.activate_guidance_registry(uuid,text,text,text)",
    "ops.guidance_import_manifest_digest(text)",
    "ops.stage_guidance_import_batch(text,text,uuid,text,text)",
    "ops.apply_guidance_import_batch(uuid,text,text,text)",
    "ops.decide_guidance_import_batch(uuid,text,text,text,text)",
    "ops.deactivate_guidance_registry(uuid,text,text,text)",
)

# These are the reader-facing typed-guidance delivery surfaces.  State and
# event-history views are deliberately not included: deactivation withdraws
# guidance delivery, not the immutable evidence that it was once approved.
READ_SURFACES = (
    ("v_guidance_current", "select count(*) from ops.v_guidance_current"),
    ("v_guidance_constraint", "select count(*) from ops.v_guidance_constraint"),
    ("v_guidance_procedure", "select count(*) from ops.v_guidance_procedure"),
    ("v_guidance_rubric", "select count(*) from ops.v_guidance_rubric"),
    ("v_guidance_preference", "select count(*) from ops.v_guidance_preference"),
    ("v_guidance_precedent", "select count(*) from ops.v_guidance_precedent"),
    ("v_guidance_example", "select count(*) from ops.v_guidance_example"),
    ("v_guidance_situation_mapping_current",
     "select count(*) from ops.v_guidance_situation_mapping_current"),
    ("v_guidance_doctrine_retrieval",
     "select count(*) from ops.v_guidance_doctrine_retrieval"),
    ("v_guidance_projection_summary",
     "select count(*) from ops.v_guidance_projection_summary"),
    ("standing_guidance",
     "select count(*) from ops.standing_guidance('joe',null,null,null)"),
)

READER_GRANTED_SURFACES = frozenset({
    "v_guidance_current", "v_guidance_constraint", "v_guidance_procedure",
    "v_guidance_rubric", "v_guidance_preference", "v_guidance_precedent",
    "v_guidance_example", "v_guidance_doctrine_retrieval",
    "v_guidance_projection_summary", "standing_guidance",
})


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def one(cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...] = ()) -> Any:
    row = cur.execute(query, params).fetchone()
    if row is None:
        fail(f"expected one row: {query}")
    return row[0]


def guidance_read_surface_counts(cur: psycopg.Cursor[Any]) -> dict[str, int]:
    """Return every reader-facing delivery count under the present registry state."""
    return {name: int(one(cur, query)) for name, query in READ_SURFACES}


def assert_guidance_read_surfaces_empty(cur: psycopg.Cursor[Any], label: str) -> None:
    visible = {name: count for name, count in guidance_read_surface_counts(cur).items() if count}
    if visible:
        fail(f"{label} still exposes typed guidance: {visible}")


def refuses(cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...], label: str) -> None:
    cur.execute("savepoint guidance_gate_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint guidance_gate_refusal")
        return
    cur.execute("rollback to savepoint guidance_gate_refusal")
    fail(f"{label} unexpectedly succeeded")


def reader_rows(cur: psycopg.Cursor[Any], query: str,
                params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Execute a successful read through the same role used by the Worker."""
    cur.execute("set local role carr_reader")
    try:
        return cur.execute(query, params).fetchall()
    finally:
        cur.execute("reset role")


def reader_refuses(cur: psycopg.Cursor[Any], query: str,
                   params: tuple[Any, ...], label: str) -> None:
    """Require carr_reader denial without leaving the transaction aborted."""
    cur.execute("savepoint guidance_gate_reader_refusal")
    cur.execute("set local role carr_reader")
    try:
        cur.execute(query, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint guidance_gate_reader_refusal")
        return
    cur.execute("reset role")
    cur.execute("rollback to savepoint guidance_gate_reader_refusal")
    fail(f"{label} unexpectedly succeeded as carr_reader")


def authority_one(cur: psycopg.Cursor[Any], query: str,
                  params: tuple[Any, ...] = (), actor: str = "joe") -> Any:
    """Exercise an authority function with the session_user it authenticates.

    CI's PostgreSQL is disposable and the surrounding transaction rolls back
    the fixture login and membership with every other fixture.
    """
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return one(cur, query, params)
    finally:
        cur.execute("reset session authorization")


def authority_refuses(cur: psycopg.Cursor[Any], query: str,
                      params: tuple[Any, ...], label: str,
                      actor: str = "joe") -> None:
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        refuses(cur, query, params, label)
    finally:
        cur.execute("reset session authorization")


def item(cur: psycopg.Cursor[Any], actor_id: Any, suffix: str) -> Any:
    intake_id = one(cur, """insert into ops.guidance_intake
        (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
        values ('procedure','system',%s,%s,'admitted','{}'::jsonb,%s) returning id""",
                    (f"db-gate:{suffix}", f"DB gate {suffix}", actor_id))
    return one(cur, """insert into ops.guidance_item
        (guidance_intake_id,source_clause,created_by)
        values (%s,%s,%s) returning id""", (intake_id, f"clause {suffix}", actor_id))


def rule_item(cur: psycopg.Cursor[Any], actor_id: Any, suffix: str) -> Any:
    rule_id = active_rule_source(cur, actor_id, suffix)
    return one(cur, """insert into ops.guidance_item
        (source_rule_id,source_clause,created_by)
        values (%s,%s,%s) returning id""", (rule_id, f"clause {suffix}", actor_id))


def activate_enforced_rule_fixture(cur: psycopg.Cursor[Any], rule_id: Any,
                                   intake_id: Any, actor_id: Any, suffix: str) -> None:
    """Activate a rollback-only rule through the exact 0194 receipt boundary."""
    control_key = f"guidance-db-gate:{suffix}"
    cur.execute("""insert into ops.enforcement_control_catalog
        (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
        values (%s,'migration:0194','ops/guidance-registry-db-gate.py',
                'transactional_schema',true,now())
        on conflict (control_key) do update set installed=true,verified_at=now()""",
        (control_key,))
    cur.execute("""insert into ops.rule_control_binding
        (rule_id,control_key,statement_hash,binding_contract)
        select id,%s,encode(digest(statement,'sha256'),'hex'),
               '{"fixture":"guidance-registry-db-gate"}'::jsonb
          from rule where id=%s""", (control_key, rule_id))
    cur.execute("""insert into ops.rule_admission
        (rule_id,guidance_intake_id,enforcement_class,enforcement_status,binding_moment,
         applicability,projection,reachability,input_contract,fixture_refs,
         state,admitted_by,admitted_at,reason)
        values (%s,%s,'machine_enforceable','hard_enforced','database acceptance',
                '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,
                array['ops/guidance-registry-db-gate.py'],'admitted',%s,now(),
                'rollback-only rule-backed guidance fixture')""",
        (rule_id, intake_id, actor_id))
    cur.execute("""insert into ops.rule_enforcement_point
        (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
        values (%s,%s,'migration:0194','ops/guidance-registry-db-gate.py',
                'transactional_schema',true,now())""", (rule_id, control_key))
    cur.execute("""with approved as (
          select r.id,r.version,encode(digest(r.statement,'sha256'),'hex') statement_hash,
                 jsonb_build_object(
                   'fixture','guidance-registry-db-gate',
                   'binding_moment','database acceptance',
                   'applicability','{}'::jsonb,
                   'projection','{}'::jsonb,
                   'reachability','{}'::jsonb,
                   'input_contract','{}'::jsonb) contract
            from rule r where r.id=%s
        )
        insert into ops.rule_approval_receipt
          (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
           enforcement_status,requested_control_keys,installed_control_keys,reason,
           normalized_contract,contract_hash,evidence_refs)
        select %s,id,version+1,statement_hash,%s,'machine_enforceable','hard_enforced',
               array[%s],array[%s],'rollback-only enforced activation fixture',contract,
               encode(digest(contract::text,'sha256'),'hex'),
               array['ops/guidance-registry-db-gate.py']
          from approved""",
        (rule_id, f"guidance-db-gate-approval:{rule_id}", actor_id,
         control_key, control_key))
    cur.execute("""insert into ops.authority_receipt
        (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
         contract_hash,evidence_refs)
        select 'approval:'||idempotency_key,'activation','rule',rule_id,actor_id,
               'rollback-only exact guidance fixture',contract_hash,evidence_refs
          from ops.rule_approval_receipt where rule_id=%s""", (rule_id,))
    cur.execute("""update rule set status='active',activated_by=%s,activated_at=now(),
        enforcement='gate'
        where id=%s""", (actor_id, rule_id))


def active_rule_source(cur: psycopg.Cursor[Any], actor_id: Any, suffix: str) -> Any:
    """Create an admitted active rule without pre-creating Guidance rows."""
    rule_id = one(cur, """insert into rule
        (statement,taught_by,scope,status,enforcement)
        values (%s,%s,'{}'::jsonb,'proposed','prose') returning id""",
                  (f"DB gate rule {suffix}", actor_id))
    intake_id = one(cur, """insert into ops.guidance_intake
        (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
        values ('rule','system',%s,%s,'admitted','{}'::jsonb,%s) returning id""",
        (f"db-gate:rule:{suffix}", f"DB gate rule {suffix}", actor_id))
    activate_enforced_rule_fixture(cur, rule_id, intake_id, actor_id, suffix)
    return rule_id


def revision(cur: psycopg.Cursor[Any], item_id: Any, actor_id: Any, kind: str = "procedure",
             is_constitution: bool = False) -> Any:
    activation: dict[str, Any] = {
        "trigger": "when the acceptance fixture is invoked",
        "entry_condition": "fixture transaction is open",
    }
    verification: dict[str, Any] = {
        "mechanism": "rollback database gate",
        "completion_condition": "fixture assertion passed",
    }
    delivery: dict[str, Any] = {
        "projection": {
            "constraint": "constraint_enforcement",
            "procedure": "procedure_workflow",
            "doctrine": "doctrine_retrieval",
            "rubric": "verification_rubric",
            "preference": "scoped_preference",
            "precedent": "precedent_search",
            "example": "example_retrieval",
        }[kind]
    }
    if kind == "constraint":
        source_rule_id = one(
            cur, "select source_rule_id from ops.guidance_item where id=%s", (item_id,))
        if source_rule_id is None:
            fail("constraint fixture requires a rule-backed guidance item")
        cur.execute("""select control_key,implementation_ref,test_ref
            from ops.rule_enforcement_point
            where rule_id=%s and installed order by control_key limit 1""", (source_rule_id,))
        control_row = cur.fetchone()
        if control_row is None:
            fail("constraint fixture requires its enforcement point before approval")
        control_key, implementation_ref, test_ref = control_row
        delivery.update({
            "enforcement_control": control_key,
            "evidence": [implementation_ref],
            "tests": [test_ref],
        })
    elif kind == "doctrine":
        activation["situation_mappings"] = ["db-gate"]
        delivery["projection"] = "doctrine_retrieval"
    elif kind == "rubric":
        verification.update({
            "verifier": "database acceptance gate",
            "acceptance_criteria": ["fixture reaches the active rubric projection"],
        })
    return one(cur, """insert into ops.guidance_revision
        (guidance_item_id,version,guidance_type,scope,activation,consumer,
         verification,provenance,delivery,is_constitution,classified_by,reason)
        values (%s,1,%s,%s,%s,'database acceptance gate',%s,
                %s,%s,%s,%s,%s) returning id""", (
            item_id, kind, Jsonb({"tenant": "carr", "actor": "joe"}), Jsonb(activation),
            Jsonb(verification), Jsonb({"preserve_source_record": True}), Jsonb(delivery),
            is_constitution, actor_id, "rollback-only fixture"))


def refuses_revision(cur: psycopg.Cursor[Any], item_id: Any, actor_id: Any,
                     kind: str, label: str, *, is_constitution: bool = False) -> None:
    cur.execute("savepoint guidance_gate_revision_refusal")
    try:
        revision(cur, item_id, actor_id, kind, is_constitution)
    except psycopg.Error:
        cur.execute("rollback to savepoint guidance_gate_revision_refusal")
        return
    cur.execute("rollback to savepoint guidance_gate_revision_refusal")
    fail(f"{label} unexpectedly succeeded")


def import_manifest(cur: psycopg.Cursor[Any]) -> tuple[str, str]:
    """Build a complete rollback-only v1 artifact from the fresh rule inventory.

    The fixture intentionally classifies every current rule as a procedure so
    it exercises the import/authority contract without claiming that this is
    the reviewed production classification.  It is rolled back with the gate.
    """
    rule_ids = [str(row[0]) for row in cur.execute(
        "select id from rule where status='active' "
        "and coalesce(scope->>'kind','') <> 'intro_politics' order by id").fetchall()]
    if len(rule_ids) < 5:
        fail("import fixture requires at least five active rules")
    source_rule_ids = {rule_id[:8]: rule_id for rule_id in rule_ids}
    if len(source_rule_ids) != len(rule_ids):
        fail("active rule UUID prefixes are unexpectedly ambiguous")
    entries: list[dict[str, Any]] = []
    for ordinal, short_id in enumerate(sorted(source_rule_ids), start=1):
        rule_id = source_rule_ids[short_id]
        guidance_id = f"db-gate-{short_id}-v1"
        entries.append({
            "ordinal": ordinal,
            "guidance_id": guidance_id,
            "source_rule_id": rule_id,
            "source_clause": "whole",
            "is_primary": True,
            "split_group_key": None,
            "guidance_type": "procedure",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {
                "trigger": "rollback-only import fixture",
                "entry_condition": "database gate transaction is open",
                "situation_mappings": [],
            },
            "consumer": "guidance registry database gate",
            "verification": {
                "mechanism": "rollback-only import fixture",
                "completion_condition": "import gate passed",
            },
            "provenance": {"source": "rule", "preserve_source_record": True},
            "delivery": {"projection": "procedure_workflow", "situation_concepts": []},
            "lifecycle": {"version": 1},
            "is_constitution": ordinal <= 5,
            "reason": "rollback-only exact import lifecycle fixture",
        })
    constitution = [entry["guidance_id"] for entry in entries if entry["is_constitution"]]
    constitution_source_ids = [entry["source_rule_id"] for entry in entries if entry["is_constitution"]]
    payload = {
        "schema": "guidance-activation-manifest/v1",
        "canonicalization": "utf8-json-sort-keys-compact-newline/v1",
        "source_manifest": {
            "path": "audits/guidance-migration-manifest.v1.tsv",
            "sha256": "1" * 64,
            "manifest": "carr-guidance-migration",
            "schema_version": "1.0.0",
            "source_classification": "judgment_ambient",
            "entry_count": 93,
        },
        "base_inventory": {
            "path": "ops/guidance-source-map.json",
            "sha256": "2" * 64,
            "active_source_ids": sorted(source_rule_ids),
            "active_source_count": len(source_rule_ids),
            "source_rule_ids": source_rule_ids,
        },
        "constitution_guidance_ids": constitution,
        "constitution_source_rule_ids": constitution_source_ids,
        "entries": entries,
    }
    artifact = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False) + "\n"
    return artifact, hashlib.sha256(artifact.encode("utf-8")).hexdigest()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("guidance-registry-db-gate: FAIL — DATABASE_URL is required", file=sys.stderr)
        return 1
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for relation in TABLES:
                    if one(cur, "select to_regclass(%s)", (relation,)) is None:
                        fail(f"missing relation {relation}")
                for function in FUNCTIONS:
                    if one(cur, "select to_regprocedure(%s)", (function,)) is None:
                        fail(f"missing function {function}")
                for surface, _ in READ_SURFACES:
                    if surface not in READER_GRANTED_SURFACES:
                        continue
                    if surface == "standing_guidance":
                        if not one(cur, "select has_function_privilege('carr_reader',%s::regprocedure,'execute')",
                                   ("ops.standing_guidance(text,text,text,text)",)):
                            fail("carr_reader cannot execute standing_guidance")
                    elif not one(cur, "select has_table_privilege('carr_reader',%s,'select')",
                                 (f"ops.{surface}",)):
                        fail(f"carr_reader cannot read delivery surface ops.{surface}")

                standing_proc = "ops.standing_guidance(text,text,text,text)"
                function_row = cur.execute(
                    """select p.prosecdef,pg_get_userbyid(p.proowner),coalesce(p.proconfig,'{}'::text[])
                         from pg_proc p where p.oid=%s::regprocedure""",
                    (standing_proc,)).fetchone()
                if function_row is None:
                    fail("standing_guidance catalog row is missing")
                function_security, function_owner, function_config = function_row
                if not function_security:
                    fail("standing_guidance is not SECURITY DEFINER")
                if function_owner != one(cur, "select current_user"):
                    fail(f"standing_guidance owner {function_owner} is not the migration owner")
                normalized_config = {
                    str(entry).replace(" ", "") for entry in function_config
                }
                if "search_path=pg_catalog,ops,public,pg_temp" not in normalized_config:
                    fail(f"standing_guidance search_path is not pinned: {function_config}")
                if one(cur, "select has_function_privilege('public',%s::regprocedure,'execute')",
                       (standing_proc,)):
                    fail("PUBLIC can execute standing_guidance")
                if not one(cur, "select has_function_privilege('carr_writer',%s::regprocedure,'execute')",
                           (standing_proc,)):
                    fail("carr_writer cannot execute standing_guidance")
                if one(cur, "select has_table_privilege('carr_reader','public.rule','select')"):
                    fail("carr_reader has direct public.rule SELECT")
                if one(cur, "select has_table_privilege('carr_reader','public.actor','select')"):
                    fail("carr_reader has direct public.actor SELECT")
                reader_refuses(cur, "select statement from public.rule limit 1", (),
                               "direct public.rule statement read")
                reader_refuses(cur, "select display_name from public.actor limit 1", (),
                               "direct public.actor display_name read")
                for materialization_view in (
                    "ops.v_guidance_materialized_current",
                    "ops.v_guidance_materialized_situation_mapping_current",
                ):
                    if one(cur, "select to_regclass(%s)", (materialization_view,)) is None:
                        fail(f"missing private materialization view {materialization_view}")
                    for role in ("public", "carr_reader", "carr_writer", "carr_authority"):
                        if one(cur, "select has_table_privilege(%s,%s,'select')",
                               (role, materialization_view)):
                            fail(f"{role} can bypass the registry delivery fence via {materialization_view}")

                # A proposal writer can construct drafts, but it may not mint
                # authority evidence, lifecycle state, active mappings, or a
                # registry activation event by writing tables directly.
                for relation in ("ops.guidance_authority_binding", "ops.guidance_lifecycle_event",
                                 "ops.guidance_situation_mapping", "ops.guidance_registry_event"):
                    if one(cur, "select has_table_privilege('carr_writer',%s,'insert')", (relation,)):
                        fail(f"carr_writer can insert {relation} directly")
                for function in ("ops.record_guidance_decision(uuid,text,text,text)",
                                 "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
                                 "ops.activate_guidance_registry(uuid,text,text,text)",
                                 "ops.decide_guidance_import_batch(uuid,text,text,text,text)",
                                 "ops.deactivate_guidance_registry(uuid,text,text,text)",
                                 "ops.assert_guidance_import_materialization(uuid)",
                                 "ops.assert_guidance_registry_coverage()"):
                    if one(cur, "select has_function_privilege('carr_writer',%s::regprocedure,'execute')", (function,)):
                        fail(f"carr_writer can execute authority function {function}")
                    if one(cur, "select has_function_privilege('public',%s::regprocedure,'execute')", (function,)):
                        fail(f"PUBLIC can execute authority function {function}")

                # Security-definer authority functions must pin lookup order;
                # otherwise a caller can shadow objects through search_path.
                for function in ("ops.record_guidance_decision(uuid,text,text,text)",
                                 "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
                                 "ops.activate_guidance_registry(uuid,text,text,text)",
                                 "ops.decide_guidance_import_batch(uuid,text,text,text,text)",
                                 "ops.deactivate_guidance_registry(uuid,text,text,text)"):
                    definition = str(one(cur, "select pg_get_functiondef(%s::regprocedure)", (function,))).lower()
                    if ("security definer" not in definition
                            or "search_path" not in definition
                            or "pg_temp" not in definition):
                        fail(f"{function} is not a pinned security-definer authority boundary")

                for function in ("ops.stage_guidance_import_batch(text,text,uuid,text,text)",
                                 "ops.apply_guidance_import_batch(uuid,text,text,text)"):
                    if not one(cur, "select has_function_privilege('carr_writer',%s::regprocedure,'execute')", (function,)):
                        fail(f"carr_writer cannot execute required import function {function}")
                    if one(cur, "select has_function_privilege('public',%s::regprocedure,'execute')", (function,)):
                        fail(f"PUBLIC can execute import function {function}")
                for function in ("ops.decide_guidance_import_batch(uuid,text,text,text,text)",
                                 "ops.deactivate_guidance_registry(uuid,text,text,text)"):
                    if not one(cur, "select has_function_privilege('carr_authority',%s::regprocedure,'execute')", (function,)):
                        fail(f"carr_authority cannot execute {function}")

                # The real deployment provisions this login externally.  CI's
                # fresh cluster does not, so create it transactionally and use
                # it to prove the session_user-derived human boundary.
                cur.execute("""do $$ begin
                  if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
                    create role carr_authority_joe login;
                  end if;
                  if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
                    create role carr_authority_dell login;
                  end if;
                end $$""")
                cur.execute("grant carr_authority to carr_authority_joe")
                cur.execute("grant carr_authority to carr_authority_dell")
                cur.execute("""do $$ begin
                  execute format('grant carr_authority_joe,carr_authority_dell to %I', current_user);
                end $$""")

                actor_id = one(cur, "select id from actor where slug='joe' and kind='human'")
                codex_actor = one(cur, "select id from actor where slug='codex' and kind='automation'")
                raw_manifest = '{"schema":"guidance-activation-manifest/v1"}\n'
                manifest_digest = hashlib.sha256(raw_manifest.encode("utf-8")).hexdigest()
                if one(cur, "select ops.guidance_import_manifest_digest(%s)", (raw_manifest,)) != manifest_digest:
                    fail("guidance import digest does not bind exact UTF-8 artifact bytes")
                # A supplied digest alone is not an import: the full compiler
                # artifact, source inventory, constitution and entry contracts
                # are mandatory and a human actor cannot stage it.
                refuses(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                         (manifest_digest, raw_manifest, codex_actor,
                          f"db-gate-invalid-import-{uuid.uuid4()}", "invalid fixture"),
                         "incomplete import manifest staging")
                refuses(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                         (manifest_digest, raw_manifest, actor_id,
                          f"db-gate-human-import-{uuid.uuid4()}", "invalid fixture"),
                         "human actor staging import")

                # Full positive lifecycle: the writer stages and applies one
                # complete canonical artifact, then only Joe's authority
                # session can approve its exact revisions, activate the
                # registry, and later deactivate it using the same digest.
                registry_id = one(cur, "select id from ops.guidance_registry where singleton")
                for number in range(5):
                    active_rule_source(cur, actor_id, f"import-source-{number}-{uuid.uuid4().hex}")
                artifact, import_digest = import_manifest(cur)
                # The staged bytes, not merely their parsed JSON value, are
                # the reviewed preimage.  The compiler's named contract is
                # sorted, compact, literal-UTF-8 JSON plus one final LF.
                # These semantically identical alternatives must therefore
                # fail before an import row can be written.
                parsed_artifact = json.loads(artifact)
                pretty_artifact = json.dumps(parsed_artifact, ensure_ascii=False, indent=2) + "\n"
                if pretty_artifact == artifact:
                    fail("noncanonical pretty fixture unexpectedly matched canonical artifact")
                refuses(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                         (hashlib.sha256(pretty_artifact.encode("utf-8")).hexdigest(), pretty_artifact,
                          codex_actor, f"db-gate-import-pretty-{uuid.uuid4()}", "noncanonical pretty fixture"),
                         "pretty or unsorted import artifact staging")
                unicode_artifact_value = json.loads(artifact)
                unicode_artifact_value["entries"][0]["reason"] = "caf\u00e9 canonical fixture"
                unicode_canonical = json.dumps(
                    unicode_artifact_value, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False) + "\n"
                unicode_escaped = json.dumps(
                    unicode_artifact_value, sort_keys=True, ensure_ascii=True,
                    separators=(",", ":"), allow_nan=False) + "\n"
                if unicode_canonical == unicode_escaped:
                    fail("escaped-unicode fixture unexpectedly matched literal UTF-8 artifact")
                if one(cur, "select ops.guidance_import_canonical_json(%s::jsonb)",
                       (unicode_canonical,)) != unicode_canonical[:-1]:
                    fail("database canonical renderer differs from the portable compiler contract")
                refuses(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                         (hashlib.sha256(unicode_escaped.encode("utf-8")).hexdigest(), unicode_escaped,
                          codex_actor, f"db-gate-import-escaped-{uuid.uuid4()}", "escaped unicode fixture"),
                         "escaped-unicode import artifact staging")
                stage_key = f"db-gate-import-stage-{uuid.uuid4()}"
                batch_id = one(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                               (import_digest, artifact, codex_actor, stage_key,
                                "rollback-only complete import stage"))
                if one(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                       (import_digest, artifact, codex_actor, stage_key,
                        "rollback-only complete import stage")) != batch_id:
                    fail("identical guidance import stage replay returned another batch")
                refuses(cur, "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s)",
                         ("f" * 64, artifact, codex_actor, f"db-gate-import-hash-{uuid.uuid4()}",
                          "wrong digest"), "wrong import digest staging")
                apply_key = f"db-gate-import-apply-{uuid.uuid4()}"
                apply_id = one(cur, "select ops.apply_guidance_import_batch(%s,%s,%s,%s)",
                               (batch_id, import_digest, apply_key,
                                "rollback-only complete import apply"))
                if one(cur, "select ops.apply_guidance_import_batch(%s,%s,%s,%s)",
                       (batch_id, import_digest, apply_key,
                        "rollback-only complete import apply")) != apply_id:
                    fail("identical guidance import apply replay returned another event")
                authority_refuses(cur, "select ops.decide_guidance_import_batch(%s,%s,%s,%s,%s)",
                                  (batch_id, import_digest, "active", f"db-gate-import-dell-{uuid.uuid4()}",
                                   "wrong actor"), "Dell authority approved Joe-owned import", actor="dell")
                decision_key = f"db-gate-import-decide-{uuid.uuid4()}"
                decision_id = authority_one(
                    cur, "select ops.decide_guidance_import_batch(%s,%s,%s,%s,%s)",
                    (batch_id, import_digest, "active", decision_key,
                     "Joe approves rollback-only complete import"))
                if authority_one(cur, "select ops.decide_guidance_import_batch(%s,%s,%s,%s,%s)",
                                 (batch_id, import_digest, "active", decision_key,
                                  "Joe approves rollback-only complete import")) != decision_id:
                    fail("identical guidance import decision replay returned another event")
                authority_refuses(cur, "select ops.decide_guidance_import_batch(%s,%s,%s,%s,%s)",
                                  (batch_id, "e" * 64, "active", f"db-gate-import-wrong-hash-{uuid.uuid4()}",
                                   "wrong digest"), "wrong import digest decision")
                activation_key = f"db-gate-import-registry-{uuid.uuid4()}"
                activation_id = authority_one(
                    cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                    (registry_id, import_digest, activation_key,
                     "activate exact rollback-only import"))
                if authority_one(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                 (registry_id, import_digest, activation_key,
                                  "activate exact rollback-only import")) != activation_id:
                    fail("identical exact registry activation replay returned another event")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "0" * 64, activation_key,
                                   "activate exact rollback-only import"),
                                  "registry activation idempotency digest mismatch")
                if not one(cur, "select exists(select 1 from ops.v_guidance_registry_state "
                                "where registry_id=%s and state='active' and manifest_digest=%s)",
                           (registry_id, import_digest)):
                    fail("exact import did not activate the registry")
                active_history_count = one(cur, "select count(*) from ops.v_guidance_revision_state "
                                                "where lifecycle_status='active'")
                if active_history_count == 0:
                    fail("active registry fixture has no immutable active lifecycle history")
                procedure_item = item(cur, actor_id, uuid.uuid4().hex)
                procedure_revision = revision(cur, procedure_item, actor_id)
                contract_hash = one(cur, "select ops.guidance_revision_contract_hash(%s)", (procedure_revision,))
                if not isinstance(contract_hash, str) or len(contract_hash) != 64:
                    fail("revision contract hash is not a sha256 digest")

                # A receipt with a mismatching hash must never bind a revision.
                bad_receipt = one(cur, """insert into ops.authority_receipt
                    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash)
                    values (%s,'activation','guidance',%s,%s,'approved',%s) returning id""",
                    (f"db-gate-bad-{uuid.uuid4()}", procedure_item, actor_id, "0" * 64))
                refuses(cur, """insert into ops.guidance_authority_binding
                    (guidance_revision_id,authority_receipt_id,contract_hash) values (%s,%s,%s)""",
                         (procedure_revision, bad_receipt, contract_hash), "mismatched receipt hash binding")

                lifecycle_id = authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (procedure_revision, f"db-gate-live-{uuid.uuid4()}", "accept fixture"))
                if lifecycle_id is None:
                    fail("human authority path did not return a lifecycle event")
                binding_id = one(cur, """select authority_binding_id from ops.guidance_lifecycle_event
                    where id=%s""", (lifecycle_id,))
                refuses(cur, "update ops.guidance_authority_binding set contract_hash=%s where id=%s",
                         ("0" * 64, binding_id), "append-only authority binding")
                refuses(cur, "update ops.guidance_revision set reason='rewrite' where id=%s",
                         (procedure_revision,), "append-only revision")

                # Constitution and precedent are rule-backed read paths:
                # standing_guidance and v_guidance_precedent resolve rule rows.
                # Intake-only items may not become either kind, so activation
                # cannot count a constitution row that retrieval drops.
                intake_precedent = item(cur, actor_id, uuid.uuid4().hex)
                refuses_revision(cur, intake_precedent, actor_id, "precedent",
                                 "intake-only precedent revision")
                intake_constitution = item(cur, actor_id, uuid.uuid4().hex)
                refuses_revision(cur, intake_constitution, actor_id, "procedure",
                                 "intake-only constitution revision", is_constitution=True)

                precedent_item = rule_item(cur, actor_id, uuid.uuid4().hex)
                precedent_revision = revision(cur, precedent_item, actor_id, "precedent")
                authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (precedent_revision, f"db-gate-precedent-{uuid.uuid4()}", "precedent fixture"))
                if not one(cur, "select exists(select 1 from ops.v_guidance_precedent "
                                "where decision_id=%s)", (precedent_item,)):
                    fail("rule-backed precedent did not reach v_guidance_precedent")

                constitution_item = rule_item(cur, actor_id, uuid.uuid4().hex)
                constitution_revision = revision(
                    cur, constitution_item, actor_id, is_constitution=True)
                authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (constitution_revision, f"db-gate-constitution-{uuid.uuid4()}",
                     "constitution fixture"))
                if not one(cur, "select exists(select 1 from ops.v_guidance_current "
                                "where guidance_revision_id=%s and is_constitution)",
                           (constitution_revision,)):
                    fail("rule-backed constitution did not reach active guidance")

                # Every validator gets a negative fixture; this keeps an
                # accidental weakening visible even when no live classification exists.
                invalid_item = item(cur, actor_id, uuid.uuid4().hex)
                refuses(cur, """insert into ops.guidance_revision
                    (guidance_item_id,version,guidance_type,scope,activation,consumer,verification,
                     provenance,delivery,classified_by,reason)
                    values (%s,1,'procedure',%s,%s,'x',%s,%s,%s,%s,%s,'bad')""", (
                    invalid_item, Jsonb({"tenant":"carr","actor":"joe"}), Jsonb({"trigger":"x"}),
                    Jsonb({"mechanism":"x"}), Jsonb({"preserve_source_record":True}),
                    Jsonb({"projection":"procedure_workflow"}), actor_id), "procedure without entry/completion")
                for kind, projection in (("doctrine", "doctrine_retrieval"), ("rubric", "verification_rubric"),
                                         ("preference", "scoped_preference"), ("constraint", "constraint_enforcement")):
                    bad = item(cur, actor_id, uuid.uuid4().hex)
                    refuses(cur, """insert into ops.guidance_revision
                      (guidance_item_id,version,guidance_type,scope,activation,consumer,verification,
                       provenance,delivery,classified_by,reason)
                      values (%s,1,%s,%s,%s,'x',%s,%s,%s,%s,%s,'bad')""", (
                        bad, kind, Jsonb({"tenant":"carr","actor":"all"}), Jsonb({"trigger":"x"}),
                        Jsonb({"mechanism":"x"}), Jsonb({"preserve_source_record":True}),
                        Jsonb({"projection":projection}), actor_id), f"invalid {kind} revision")

                # Bridge an approved doctrine revision through a real WR-AI-006
                # concept/section mapping and require it to reach the view.
                bridge_suffix = uuid.uuid4().hex
                document_id = one(cur, """insert into doctrine_document
                    (slug,title,content_class,visibility,created_by)
                    values (%s,%s,'sop','shared',%s) returning id""",
                    (f"guidance-db-gate-{bridge_suffix}", "Guidance DB gate", actor_id))
                section_id = one(cur, """insert into doctrine_section
                    (document_id,section_key,title,ordinal,status,current_version)
                    values (%s,'typed-guidance','Typed guidance',1,'active',1) returning id""",
                    (document_id,))
                body = "typed guidance situation bridge acceptance fixture"
                body_hash = hashlib.sha256(body.encode()).hexdigest()
                doctrine_source_revision = one(cur, """insert into doctrine_revision
                    (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                    values (%s,1,%s,%s,%s,%s,'guidance db gate') returning id""",
                    (section_id, actor_id, Jsonb({"text": body}), body, body_hash))
                cur.execute("""update doctrine_section
                    set current_revision_id=%s,body_hash=%s where id=%s""",
                    (doctrine_source_revision, body_hash, section_id))
                concept_id = one(cur, """insert into retrieval_concept
                    (concept_key,label,definition,status,proposer_id,approver_id,approved_at)
                    values (%s,'Typed guidance gate','Rollback-only bridge fixture','approved',%s,%s,now())
                    returning id""", (f"guidance-gate-{bridge_suffix}", actor_id, actor_id))
                cur.execute("""insert into doctrine_concept_mapping
                    (concept_id,section_id,role,rationale,status,proposer_id,approver_id,approved_at)
                    values (%s,%s,'governs','rollback-only bridge fixture','approved',%s,%s,now())""",
                    (concept_id, section_id, actor_id, actor_id))
                concept_section = (concept_id, section_id)
                doctrine_item = item(cur, actor_id, uuid.uuid4().hex)
                doctrine_revision = revision(cur, doctrine_item, actor_id, "doctrine")
                doctrine_lifecycle = authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (doctrine_revision, f"db-gate-doctrine-{uuid.uuid4()}", "bridge fixture"))
                doctrine_binding = one(cur, """select authority_binding_id
                    from ops.guidance_lifecycle_event where id=%s""", (doctrine_lifecycle,))
                mapping_id = one(cur, "select ops.propose_guidance_situation_mapping(%s,%s,%s,%s)",
                    (doctrine_revision, concept_section[0], concept_section[1], "bridge fixture"))
                authority_one(cur, "select ops.activate_guidance_situation_mapping(%s,%s,%s)",
                    (mapping_id, doctrine_binding, "bridge approval"))
                if not one(cur, "select exists(select 1 from ops.v_guidance_doctrine_retrieval where guidance_revision_id=%s)",
                           (doctrine_revision,)):
                    diagnostics = cur.execute("""select
                      (select lifecycle_status from ops.v_guidance_revision_state where id=%s),
                      (select state from ops.v_guidance_situation_mapping_current
                        where guidance_revision_id=%s and concept_id=%s and doctrine_section_id=%s),
                      (select status from retrieval_concept where id=%s),
                      (select status from doctrine_concept_mapping
                        where concept_id=%s and section_id=%s)""", (
                        doctrine_revision, doctrine_revision, concept_section[0], concept_section[1],
                        concept_section[0], concept_section[0], concept_section[1])).fetchone()
                    fail(f"approved doctrine revision did not reach the situation bridge: {diagnostics}")
                standing_rows = reader_rows(
                    cur, "select * from ops.standing_guidance('joe',null,null,null)")
                if not standing_rows:
                    fail("active standing_guidance returned no rows through carr_reader")

                # Populate every remaining typed delivery projection so the
                # deactivation assertion proves a transition from visible to
                # empty, rather than merely observing a view that was already
                # empty in this rollback-only fixture.
                for kind in ("constraint", "rubric", "preference", "example"):
                    typed_revision = revision(
                        cur, rule_item(cur, actor_id, f"active-{kind}-{uuid.uuid4().hex}"),
                        actor_id, kind)
                    authority_one(
                        cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                        (typed_revision, f"db-gate-active-{kind}-{uuid.uuid4()}",
                         f"activate {kind} delivery fixture"))
                inactive_while_active = {
                    name: count for name, count in guidance_read_surface_counts(cur).items()
                    if count == 0
                }
                if inactive_while_active:
                    fail(f"active registry has unexercised delivery surfaces: {inactive_while_active}")

                registry_id = one(cur, "select id from ops.guidance_registry where singleton")
                authority_refuses(cur, "select ops.deactivate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "d" * 64, f"db-gate-inactive-{uuid.uuid4()}",
                                   "must not deactivate an inactive registry"),
                                  "registry deactivation without an active exact digest")
                authority_refuses(cur, "select ops.deactivate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "d" * 64, f"db-gate-dell-deactivate-{uuid.uuid4()}",
                                   "wrong actor"),
                                  "Dell authority deactivated Joe-owned registry", actor="dell")
                constitution_count = one(cur, "select count(*) from ops.v_guidance_current where is_constitution")
                if constitution_count > 10:
                    fail("activation fixture exceeds the ten-row constitution ceiling")
                for number in range(max(0, 5 - constitution_count)):
                    approved_revision = revision(
                        cur, rule_item(cur, actor_id, f"activation-constitution-{uuid.uuid4().hex}"),
                        actor_id, is_constitution=True)
                    authority_one(
                        cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                        (approved_revision, f"db-gate-activation-constitution-{number}-{uuid.uuid4()}",
                         "activation constitution fixture"))
                constitution_count = one(
                    cur, "select count(*) from ops.v_guidance_current where is_constitution")
                if not 5 <= constitution_count <= 10:
                    fail("registry activation fixture is outside the five-to-ten constitution range")

                # Build a complete rollback-only target state through the real
                # lifecycle path.  The positive activation case must pass the
                # production coverage function; replacing or stubbing that gate
                # would prove only that activation works when its guard is gone.
                missing_rules = cur.execute("""select r.id from rule r
                    where r.status='active'
                      and coalesce(r.scope->>'kind','') <> 'intro_politics'
                      and not exists (
                        select 1 from ops.v_guidance_current g
                         where g.source_rule_id=r.id and g.is_primary)
                    order by r.id""").fetchall()
                missing_revisions = []
                for number, (rule_id,) in enumerate(missing_rules):
                    covered_item = one(cur, """insert into ops.guidance_item
                        (source_rule_id,source_clause,created_by)
                        values (%s,%s,%s) returning id""",
                        (rule_id, f"rollback coverage fixture {number}", actor_id))
                    missing_revisions.append(revision(cur, covered_item, actor_id))
                for number, covered_revision in enumerate(missing_revisions):
                    authority_one(
                        cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                        (covered_revision, f"db-gate-coverage-{number}-{uuid.uuid4()}",
                         "rollback-only complete coverage fixture"))
                coverage_failures = cur.execute(
                    "select source_rule_id,issue from ops.assert_guidance_registry_coverage()"
                ).fetchall()
                if coverage_failures:
                    fail(f"complete activation fixture still has coverage failures: {coverage_failures[:5]}")

                activation_key = f"db-gate-registry-active-{uuid.uuid4()}"
                manifest_digest = "b" * 64
                activation_reason = "full reviewed activation fixture"
                # A bare digest is deliberately no longer an activation
                # preimage.  The authority route must name a staged, applied,
                # human-approved compiler artifact with exact materialization.
                receipts_before_manifest_refusal = one(cur, "select count(*) from ops.authority_receipt")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, manifest_digest, activation_key, activation_reason),
                                  "registry activation without an exact staged manifest")
                if one(cur, "select count(*) from ops.authority_receipt") != receipts_before_manifest_refusal:
                    fail("manifest refusal minted a registry authority receipt")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, manifest_digest, f"db-gate-dell-{uuid.uuid4()}",
                                   activation_reason),
                                  "Dell authority activated Joe-owned registry", actor="dell")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "c" * 64, activation_key, activation_reason),
                                  "different manifest digest reused registry activation key")

                uncovered_rule = one(cur, """insert into rule
                    (statement,taught_by,scope,status,enforcement)
                    values (%s,%s,'{}'::jsonb,'proposed','prose') returning id""",
                    (f"DB gate uncovered active rule {uuid.uuid4()}", actor_id))
                uncovered_intake = one(cur, """insert into ops.guidance_intake
                    (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
                    values ('rule','system',%s,'coverage fixture','admitted','{}'::jsonb,%s)
                    returning id""", (f"db-gate:coverage:{uuid.uuid4()}", actor_id))
                activate_enforced_rule_fixture(
                    cur, uncovered_rule, uncovered_intake, actor_id,
                    f"coverage-{uncovered_rule}")
                if one(cur, "select count(*) from ops.assert_guidance_registry_coverage()") == 0:
                    fail("coverage unexpectedly passes with an uncovered active rule")

                # Coverage refusal is atomic: it cannot leave behind a receipt
                # that becomes replayable after coverage is repaired.
                coverage_key = f"db-gate-registry-coverage-{uuid.uuid4()}"
                receipts_before = one(cur, "select count(*) from ops.authority_receipt")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "a" * 64, coverage_key,
                                   "must refuse incomplete registry"),
                                  "registry activation without valid coverage")
                if one(cur, "select count(*) from ops.authority_receipt") != receipts_before:
                    fail("coverage refusal minted a registry authority receipt")

                # Deactivation is a delivery fence, not a history rewrite.
                # Every reader-granted typed-guidance projection must empty,
                # while the active lifecycle evidence and append-only registry
                # events remain available for audit and an identical replay.
                active_history_count = one(cur, "select count(*) from ops.v_guidance_revision_state "
                                                "where lifecycle_status='active'")
                registry_events_before_deactivation = one(
                    cur, "select count(*) from ops.guidance_registry_event where registry_id=%s", (registry_id,))
                deactivation_key = f"db-gate-import-deactivate-{uuid.uuid4()}"
                deactivation_id = authority_one(
                    cur, "select ops.deactivate_guidance_registry(%s,%s,%s,%s)",
                    (registry_id, import_digest, deactivation_key,
                     "deactivate exact rollback-only import"))
                if one(cur, "select state from ops.v_guidance_registry_state where registry_id=%s", (registry_id,)) != "inactive":
                    fail("registry deactivation did not reversibly make the registry inactive")
                assert_guidance_read_surfaces_empty(cur, "registry deactivation")
                if one(cur, "select count(*) from ops.v_guidance_revision_state "
                            "where lifecycle_status='active'") != active_history_count:
                    fail("registry deactivation rewrote immutable active lifecycle history")
                if one(cur, "select count(*) from ops.guidance_registry_event where registry_id=%s", (registry_id,)) != registry_events_before_deactivation + 1:
                    fail("registry deactivation did not append exactly one immutable registry event")
                if authority_one(cur, "select ops.deactivate_guidance_registry(%s,%s,%s,%s)",
                                 (registry_id, import_digest, deactivation_key,
                                  "deactivate exact rollback-only import")) != deactivation_id:
                    fail("registry deactivation replay returned another event after read-surface fence")
                assert_guidance_read_surfaces_empty(cur, "registry deactivation replay")
            conn.rollback()
        print("PASS: guidance registry database gate (catalog, privilege, authority, validators, bridge, rollback)")
        return 0
    except Exception as exc:
        print(f"guidance-registry-db-gate: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
