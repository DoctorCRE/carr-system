#!/usr/bin/env python3
"""Static contract: approval is impossible without immediate enforcement."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration



REPO = Path(__file__).resolve().parent.parent
LEGACY_MIGRATION = REPO / "migrations" / "0194_atomic_rule_approval.sql"
MIGRATION = REPO / "migrations" / "0228_atomic_rule_lifecycle_forward_upgrade.sql"
SCOPE_FIX = REPO / "migrations" / "0247_system_rule_scope_binding.sql"
BINDING_REPAIR = REPO / "migrations" / "0479_rule_control_binding_writer.sql"
DELIVERY_REPAIR = REPO / "migrations" / "0482_rule_delivery_binding_writer.sql"
LEGACY_ADOPTION = REPO / "migrations" / "0867_adopt_active_legacy_rule_approval.sql"
ADOPTION_PROOF = REPO / "mcp-server" / "test" / "approve-rule-legacy-adoption-postgres.test.mjs"
CI = REPO / "ops" / "ci.sh"
TOOLS = REPO / "mcp-server" / "src" / "tools.js"
DB_GATE = REPO / "ops" / "control-plane-db-gate.py"
LOCAL_ACCEPTANCE = REPO / "ops" / "atomic-rule-approval-local-pg-acceptance.py"


def main() -> int:
    forward_sql = read_migration(MIGRATION.parent.parent, MIGRATION.name).lower()
    scope_fix = read_migration(SCOPE_FIX.parent.parent, SCOPE_FIX.name).lower() if SCOPE_FIX.name in migration_names(SCOPE_FIX.parent.parent) else ""
    catalog_seed = forward_sql.split("-- two joe-approved system rules", 1)[0]
    binding_repair = read_migration(BINDING_REPAIR.parent.parent, BINDING_REPAIR.name).lower() if BINDING_REPAIR.name in migration_names(BINDING_REPAIR.parent.parent) else ""
    delivery_repair = read_migration(DELIVERY_REPAIR.parent.parent, DELIVERY_REPAIR.name).lower() if DELIVERY_REPAIR.name in migration_names(DELIVERY_REPAIR.parent.parent) else ""
    sql = (read_migration(LEGACY_MIGRATION.parent.parent, LEGACY_MIGRATION.name).lower()
           + forward_sql + binding_repair + delivery_repair)
    tools = TOOLS.read_text(encoding="utf-8")
    gate = DB_GATE.read_text(encoding="utf-8")
    acceptance = LOCAL_ACCEPTANCE.read_text(encoding="utf-8")
    adoption = read_migration(LEGACY_ADOPTION.parent.parent, LEGACY_ADOPTION.name).lower() if LEGACY_ADOPTION.name in migration_names(LEGACY_ADOPTION.parent.parent) else ""
    adoption_proof = ADOPTION_PROOF.read_text(encoding="utf-8") if ADOPTION_PROOF.exists() else ""
    ci = CI.read_text(encoding="utf-8")
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    check("one authority function owns approval",
          "function ops.approve_rule(" in sql and "ops.authority_actor_slug()" in sql)
    check("Joe is the sole required system-rule authority",
          "if v_actor_slug <> 'joe' then" in sql
          and "cannot replace Joe approval".lower() in sql)
    check("approval means active and enforced in one transaction",
          "'policy_status','active'" in sql
          and "'enforcement_status',v_status" in sql
          and "update rule" in sql)
    check("missing controls refuse approval instead of creating a waiting approval",
          "rule approval refused: exact enforcement is not installed" in sql
          and "rule_enforcement_backlog" not in sql
          and "finalize_rule_approval" not in sql)
    check("active transition requires immutable enforced approval receipt",
          "rule_approval_receipt_append_only" in sql
          and "immutable enforced approval receipt is missing" in sql
          and "active requires installed enforcement" in sql)
    check("active rule preimage is frozen under its exact approval",
          "approved rule % is immutable except through exact joe approval or retirement" in sql
          and "before insert or update on rule" in sql
          and "unreceipted_deactivation_refusal" in gate
          and "approved_rule_noop_update_refusal" in gate
          # WR-000019 slice S10: the blanket "active_rule_approval_frozen"
          # refusal was replaced by a narrower pair -- scope and quote stay
          # frozen exactly as before, but the statement now has its own
          # sanctioned, receipted exception path (migration 0349,
          # ops.amend_rule_statement) rather than being frozen solid. The
          # PREIMAGE this check is about is still locked; only the axis
          # count changed.
          and "active_rule_scope_frozen" in tools
          and "active_rule_quote_frozen" in tools
          and "ops.amend_rule_statement" in tools)
    check("approved admission and control rows are immutable",
          "approved_rule_admission_immutable" in sql
          and "approved_rule_enforcement_point_immutable" in sql
          and "approved_rule_control_binding_immutable" in sql
          and "active_approved_control_immutable" in sql)
    check("policy compiler delivers only current receipt-bound enforcement",
          "ar.rule_version=r.version" in sql
          and "language sql stable security definer" in sql
          and "grant execute on function ops.applicable_rules(text,text,text)" in sql
          and "ar.normalized_contract->'applicability'=a.applicability" in sql
          and "a.applicability->'workflows' ? '*'" in sql
          and "a.applicability->'surfaces' ? '*'" in sql
          and "a.applicability->'tiers' ? '*'" in sql
          and "auth.contract_hash=ar.contract_hash" in sql
          and "not exists (" in sql)
    check("callers name registered controls instead of implementation prose",
          "enforcement_control_catalog" in sql
          and "and c.control_key=any(v_requested)" in sql
          and "p_implementation_ref" not in sql)
    check("a real control cannot be claimed for an unrelated rule or statement",
          "rule_control_binding" in sql
          and "b.rule_id=p_rule_id" in sql
          and "b.statement_hash=encode(digest(v_rule.statement,'sha256'),'hex')" in sql)
    check("a private database primitive writes exact rule-control bindings",
          bool(binding_repair)
          and "function ops.bind_rule_controls(" in binding_repair
          and "ops.authority_actor_slug()" in binding_repair
          and "insert into ops.rule_control_binding" in binding_repair
          and "encode(digest(v_rule.statement,'sha256'),'hex')" in binding_repair
          and "from ops.enforcement_control_catalog" in binding_repair
          and "c.installed and c.verified_at is not null" in binding_repair
          and "p_implementation_ref" not in binding_repair
          and "revoke all on function ops.bind_rule_controls" in binding_repair
          and "from public,carr_reader,carr_writer,carr_jobs,carr_authority" in binding_repair)
    check("approval repairs missing exact bindings inside its atomic transaction",
          "perform ops.bind_rule_controls(" in binding_repair
          and "return ops.approve_rule_receipt_activation_v1(" in binding_repair
          and binding_repair.index("perform ops.bind_rule_controls(")
              < binding_repair.index("return ops.approve_rule_receipt_activation_v1("))
    check("a private database primitive writes the admitted delivery contract",
          bool(delivery_repair)
          and "function ops.bind_rule_delivery(" in delivery_repair
          and "ops.authority_actor_slug()" in delivery_repair
          and "from ops.rule_admission" in delivery_repair
          and "projection->'delivery'" in delivery_repair
          and "insert into ops.rule_load_layer" in delivery_repair
          and "from public,carr_reader,carr_writer,carr_jobs,carr_authority" in delivery_repair)
    check("the delivery writer preserves verified owner-prebound compatibility",
          "a durable row can predate this writer" in delivery_repair
          and "select * into v_existing from ops.rule_load_layer" in delivery_repair
          and "delivery binding no longer matches its durable identity or activation contract" in delivery_repair)
    check("approval binds delivery before controls and activation",
          "perform ops.bind_rule_delivery(" in delivery_repair
          and "perform ops.bind_rule_controls(" in delivery_repair
          and "return ops.approve_rule_receipt_activation_v1(" in delivery_repair
          and delivery_repair.index("perform ops.bind_rule_delivery(")
              < delivery_repair.index("perform ops.bind_rule_controls(")
              < delivery_repair.index("return ops.approve_rule_receipt_activation_v1("))
    check("cost control is registered with implementation and tests",
          "platform_metering_pre_dispatch" in sql
          and "ops/platform-metering-gate-selftest.py" in sql)
    check("forward catalog repair is additive and rejects drift before sync",
          "insert into ops.enforcement_control_catalog" in catalog_seed
          and "on conflict (control_key) do nothing" in catalog_seed
          and "do update set" not in catalog_seed
          and "migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js" in catalog_seed
          and "lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py" in catalog_seed
          and "0228 failed: control catalog does not match the two exact reviewed controls" in catalog_seed
          and catalog_seed.index("on conflict (control_key) do nothing")
              < catalog_seed.index("0228 failed: control catalog does not match the two exact reviewed controls")
          and catalog_seed.index("0228 failed: control catalog does not match the two exact reviewed controls")
              < forward_sql.index("create or replace function ops.sync_system_rule_control_bindings()")
          and "forward control catalog restoration" in gate)
    check("Joe's existing governance and cost rules bind to distinct exact controls",
          "ae44e0c0-e773-456c-a85b-2dc4cf4dd49e" in sql
          and "9e02f7eee01220fd604ba97d605830ea903d3266f95b626a5ca5d9a73567c8f9" in sql
          and "4a0e59ce-728a-49b5-a055-116156e9470e" in sql
          and "human_authority_runtime" in sql
          and "a57d981a-8f6d-4c18-95ee-0e63a5a90b89" in sql
          and "c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668" in sql
          and "8b31938a-e2f2-4b8f-9c29-187efa5c1650" in sql
          and "platform_metering_pre_dispatch" in sql
          and "function ops.sync_system_rule_control_bindings()" in sql
          and "select ops.sync_system_rule_control_bindings()" in sql
          and "does not match joe-approved preimage" in sql
          and "lacks its exact joe decision evidence" in sql
          and "must retain shared system-wide scope" in sql
          and "narrowed_system_rule_scope" in gate
          and "personal_system_rule_audience" in gate)
    check("forward scope repair pins both system rules without rewriting their records",
          bool(scope_fix)
          and "create or replace function ops.sync_system_rule_control_bindings()" in scope_fix
          and "v_rule.scope is distinct from v_expected.rule_scope" in scope_fix
          and "'{}'::jsonb" in scope_fix
          and "github" in scope_fix and "neon" in scope_fix and "cloudflare" in scope_fix
          and "anthropic" in scope_fix and "openai" in scope_fix and "google" in scope_fix
          and "healthchecks" in scope_fix and "blotato" in scope_fix and "make" in scope_fix
          and "select ops.sync_system_rule_control_bindings()" in scope_fix
          and "update rule" not in scope_fix
          and "from public,carr_reader,carr_writer,carr_jobs,carr_authority" in scope_fix)
    check("retired cost restriction stays an exact inert tombstone in both sync passes",
          "legacy retired system cost rule does not match its exact retirement tombstone preimage" in forward_sql
          and "legacy retired system cost rule does not match its exact retirement tombstone preimage" in scope_fix
          and "34f34e23-225b-4d0f-946f-478b59fbce63" in forward_sql
          and "34f34e23-225b-4d0f-946f-478b59fbce63" in scope_fix
          and "82cf84d571cbe49eb61bf9570e2c8f86a114fa216e9ab1b3799181045c881137" in forward_sql
          and "82cf84d571cbe49eb61bf9570e2c8f86a114fa216e9ab1b3799181045c881137" in scope_fix
          and "continue;" in forward_sql and "continue;" in scope_fix)
    check("routine roles cannot approve rules",
          "from public,carr_reader,carr_writer,carr_jobs" in sql
          and "grant execute on function ops.approve_rule" in sql
          and "to carr_authority" in sql)
    check("approval replay is input-bound",
          "is distinct from p_rule_id" in sql
          and "is distinct from v_requested" in sql
          and "idempotency key was reused with different input" in sql)
    check("approval replay revalidates current policy and enforcement",
          "current active rule no longer matches the immutable approval" in sql
          and "exact installed enforcement or authority evidence is stale" in sql
          and "v_rule.version+1" in sql)
    check("deployed 0194 pre-activation receipts receive only verified immutable anchors",
          "rule_approval_lifecycle_anchor" in sql
          and "ar.rule_version+1=r.version" in sql
          and "legacy.approval_receipt_id=ar.id" in sql
          and "legacy.rule_version_after=r.version" in sql
          and "function ops.require_rule_approval_lifecycle_anchor()" in sql
          and "before insert on ops.rule_approval_lifecycle_anchor" in sql
          and "ar.rule_id=new.rule_id" in sql
          and "ar.rule_version+1=new.rule_version_after" in sql
          and "joe.slug='joe' and joe.kind='human' and joe.active" in sql
          and "a.state='admitted' and a.admitted_by=ar.actor_id" in sql
          and "r.enforcement=(case when ar.enforcement_status='hard_enforced'" in sql
          and "legacy approval anchor requires an exact active joe-approved 0194 receipt chain" in sql
          and "rule_approval_lifecycle_anchor_append_only" in sql
          and "anything that fails that proof" in sql
          and "fresh_receipt_anchor_refusal" in gate
          and "mismatched_anchor_refusal" in gate)
    check("the exact atomic activation is allowed through the preimage freeze",
          "old.status='proposed' and new.status='active'" in sql
          and "enforcement label does not match approval" in sql)
    check("rule retirement is a separate Joe-authority receipt",
          "function ops.retire_rule(" in sql
          and "rule_retirement_receipt" in sql
          and "cannot retire without an exact joe authority receipt" in sql
          and "routine writer may retire rules" in sql)
    check("retired rules are immutable tombstones and retirement replay proves the tombstone",
          "retired rule % is immutable" in sql
          and "rule_version_after" in sql
          and "current retired rule no longer matches the immutable retirement" in sql
          and "retired_rule_mutation_refusal" in gate
          and "retired_rule_revival_refusal" in gate
          and "retired tombstone mutation refused" in acceptance
          and "altered retirement replay was accepted" in acceptance)
    check("local acceptance reuses an exact authority login created by earlier DB gates",
          "if not exists (" in acceptance
          and "select 1 from pg_roles where rolname='carr_authority_joe'" in acceptance
          and "select rolcanlogin from pg_roles where rolname='carr_authority_joe'" in acceptance
          and "carr_authority_joe must be an exact LOGIN identity" in acceptance)
    check("advisory prose cannot be approved as an unbreakable rule",
          "advisory guidance is not an unbreakable rule" in sql
          and "standing_context_runtime" in sql
          and "mislabeled as unbreakable enforcement" in sql)
    check("historical migration invariants run before their commit",
          forward_sql.rfind("do $$") < forward_sql.rfind("commit;"))
    check("binding repair leaves transaction ownership to the migration runner",
          not binding_repair.startswith("begin;")
          and not binding_repair.endswith("commit;"))
    check("delivery repair leaves transaction ownership to the migration runner",
          not delivery_repair.startswith("begin;")
          and not delivery_repair.endswith("commit;"))
    # WR-000262 (0867): an ACTIVE legacy admission is adopted, not refused,
    # and only that rule; every other active rule stays verify-only.
    adoption_body = adoption.split("create function ops.adopt_legacy_active_rule_v1(", 1)[-1]
    check("only a legacy active admission is adopted; other active rules stay verify-only",
          bool(adoption)
          and "ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at)" in adoption
          and "if v_legacy_note is null then\n      if exists (" in adoption
          and "active rule % lacks its immutable exact control binding" in adoption
          and "active rule % is not a legacy admission; it cannot be adopted" in adoption
          and "return ops.adopt_legacy_active_rule_v1(" in adoption
          and adoption.index("perform ops.bind_rule_delivery(")
              < adoption.rindex("perform ops.bind_rule_controls(")
              < adoption.rindex("return ops.adopt_legacy_active_rule_v1(")
              < adoption.rindex("return ops.approve_rule_receipt_activation_v1("))
    check("adoption keeps the rule row and admitted contract and receipts the current version",
          bool(adoption)
          and "update public.rule" not in adoption_body
          and "update rule" not in adoption_body
          and "values (p_idempotency_key,p_rule_id,v_rule.version,v_statement_hash" in adoption_body
          and "'binding_moment',v_admission.binding_moment" in adoption_body
          and "'projection',v_admission.projection" in adoption_body
          and "if v_rule.activated_by is distinct from v_actor_id then" in adoption_body
          and "if v_admission.enforcement_class is distinct from p_policy_kind then" in adoption_body
          and "rule approval refused: exact enforcement is not installed" in adoption_body
          and "'approval:'||p_idempotency_key,'activation'" in adoption_body
          and "revoke all on function ops.adopt_legacy_active_rule_v1" in adoption
          and "from public,carr_reader,carr_writer,carr_jobs,carr_authority" in adoption
          and not adoption.lstrip().startswith("begin;"))
    check("the adoption proof runs on real PostgreSQL in the migration class",
          "approve-rule-legacy-adoption-postgres" in ci
          and "CARR_APPROVE_RULE_ADOPTION_DB_REQUIRED=1" in ci
          and "AC-ADOPT" in adoption_proof and "AC-REFUSE" in adoption_proof
          and "lacks its immutable exact control binding" in adoption_proof)

    total = 35
    print()
    print(f"atomic-rule-approval-selftest: {total-len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
