#!/usr/bin/env python3
# ci: db-gate
"""Exercise rule admission, approval and retirement in a rollback-only transaction."""
from __future__ import annotations
import os
import sys
from typing import Any, NoReturn
import psycopg
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> NoReturn:
    print(f"rule-admission-db-gate FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetchone_required(row: tuple[Any, ...] | None, context: str) -> tuple[Any, ...]:
    """Turn an unexpected empty SELECT into the gate's normal failure path."""
    if row is None:
        fail(f"expected one row for {context}")
        raise AssertionError("fail exits")
    return row


def main() -> int:
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        fail('DATABASE_URL is required')
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        # A new rule cannot activate on prose alone.
        cur.execute("select id from actor where slug='joe'")
        actor = fetchone_required(cur.fetchone(), "Joe actor")[0]
        # A snapshot carries 0194 in its migration ledger but historically
        # omitted its mutable catalog seeds. 0228 must restore exactly the
        # two reviewed global controls before attempting semantic binding.
        cur.execute("""
            select count(*) from ops.enforcement_control_catalog
             where (control_key='human_authority_runtime'
                    and implementation_ref='migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js'
                    and test_ref='mcp-server/test/control-plane-authority-boundary.test.mjs; ops/control-plane-authority-runtime-preflight-selftest.py'
                    and enforcement_class='transactional_schema'
                    and installed and verified_at is not null)
                or (control_key='platform_metering_pre_dispatch'
                    and implementation_ref='lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py'
                    and test_ref='ops/platform-metering-gate-selftest.py; ops/platform-metering-policy-selftest.py; ops/guard-selftest.py'
                    and enforcement_class='deny_gate'
                    and installed and verified_at is not null)
        """)
        if fetchone_required(cur.fetchone(), "forward control catalog restoration")[0] != 2:
            fail("0228 did not restore the two exact reviewed control catalog rows")
        # The exact spending rule and decision were captured before the
        # atomic approval architecture existed. Deployment must bind their
        # pinned preimages to the cost gate; a familiar UUID with different
        # words or governance decision must never be blessed.
        cur.execute("""
            insert into rule(id,statement,human_quote,taught_by,status,scope)
            values ('a57d981a-8f6d-4c18-95ee-0e63a5a90b89',
                    'Every metered CARR execution must pass a machine-enforced pre-dispatch budget gate; prose or registry-only guidance does not count as enforcement, and Joe alone may approve exceeding a cap, buying usage credits, or enabling paid overage.',
                    'fixture',%s,'proposed',
                    '{"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}'::jsonb)
        """, (actor,))
        cur.execute("""
            insert into event
              (id,occurred_at,actor_id,verb,subject_type,subject_id,new_value,
               cause,human_quote,agent_rationale,idempotency_key)
            values
              ('f7ea060c-268b-47f1-8a17-7168841b77e0',now(),%s,
               'log-decision','decision','8b31938a-e2f2-4b8f-9c29-187efa5c1650',
               jsonb_build_object(
                 'title','Make cost discipline permanent; expire only the temporary emergency restriction',
                 'quote_absent',false,'provenance','rollback DB gate fixture'),
               'human_stated',
               'But also, we want a budget rule in affect going forward not just expiring in September. We need to operate the system with cost in mind. Not to the point where it limits the system but just to the point where excessive spending is avoided',
               'exact pinned decision fixture','db-gate-cost-decision')
        """, (actor,))
        cur.execute("""
            insert into record_source(entity_type,entity_id,source_system,external_key)
            values ('event','f7ea060c-268b-47f1-8a17-7168841b77e0',
                    'decision-history','fixture#db-gate-cost-binding')
        """)
        cur.execute("select ops.sync_system_rule_control_bindings()")
        if fetchone_required(cur.fetchone(), "system-rule binding sync")[0] != 1:
            fail("existing spending rule did not receive its exact installed-control binding")
        cur.execute("""
            select count(*)
              from ops.rule_control_binding b
              join rule r on r.id=b.rule_id
             where b.rule_id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'
               and b.control_key='platform_metering_pre_dispatch'
               and b.statement_hash=encode(digest(r.statement,'sha256'),'hex')
               and b.binding_contract->>'durable_decision_ref'=
                   '8b31938a-e2f2-4b8f-9c29-187efa5c1650'
               and b.binding_contract->>'decision_event_ref'=
                   'f7ea060c-268b-47f1-8a17-7168841b77e0'
        """)
        if fetchone_required(cur.fetchone(), "system-rule binding readback")[0] != 1:
            fail("spending rule binding does not match the exact statement and decision")
        for savepoint, mutation, params, message in (
            ("narrowed_system_rule_scope",
             "update rule set scope='{\"workflows\":[\"one-workflow\"]}'::jsonb "
             "where id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'",
             (), "system-rule sync accepted narrowed applicability"),
            ("personal_system_rule_audience",
             "update rule set personal_to=%s "
             "where id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'",
             (actor,), "system-rule sync accepted a personal audience"),
        ):
            cur.execute(f"savepoint {savepoint}")
            try:
                cur.execute(mutation, params)
                cur.execute("select ops.sync_system_rule_control_bindings()")
                fail(message)
            except psycopg.Error:
                cur.execute(f"rollback to savepoint {savepoint}")
        cur.execute("savepoint wrong_system_rule_preimage")
        try:
            cur.execute("""
                insert into rule(id,statement,human_quote,taught_by,status)
                values ('ae44e0c0-e773-456c-a85b-2dc4cf4dd49e',
                        'wrong governance statement','fixture',%s,'proposed')
            """, (actor,))
            cur.execute("select ops.sync_system_rule_control_bindings()")
            fail("system-rule sync accepted a known UUID with the wrong statement")
        except psycopg.Error:
            cur.execute("rollback to savepoint wrong_system_rule_preimage")
        grant_settable_runtime_roles(cur, "carr_writer")
        set_local_role(cur, "carr_writer")
        cur.execute("select has_function_privilege(current_user,%s,'execute')",
                    ("ops.sync_system_rule_control_bindings()",))
        if fetchone_required(cur.fetchone(), "system-rule binding ACL")[0] is not False:
            fail("routine writer can install semantic rule bindings")
        cur.execute("reset role")
        cur.execute("""
            insert into rule(statement,human_quote,taught_by,status)
            values ('control-plane fixture','fixture',%s,'proposed') returning id
        """, (actor,))
        rule_id = fetchone_required(cur.fetchone(), "fixture rule")[0]
        cur.execute("savepoint admission_refusal")
        try:
            cur.execute("update rule set status='active',activated_by=%s,activated_at=now() where id=%s",
                        (actor, rule_id))
            fail("rule activated without an admitted contract")
        except psycopg.Error:
            cur.execute("rollback to savepoint admission_refusal")
        cur.execute("savepoint admission_insert_refusal")
        try:
            cur.execute("""
                insert into rule(statement,human_quote,taught_by,status,activated_by,activated_at)
                values ('direct active fixture','fixture',%s,'active',%s,now())
            """, (actor,actor))
            fail("rule inserted active without an admitted contract")
        except psycopg.Error:
            cur.execute("rollback to savepoint admission_insert_refusal")

        cur.execute("""
            insert into ops.guidance_intake
              (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
            values ('rule','human','db-gate','control-plane fixture','normalized',
                    '{"enforcement_class":"machine_enforceable"}',%s)
            returning id
        """, (actor,))
        intake_id = fetchone_required(cur.fetchone(), "fixture guidance intake")[0]
        cur.execute("""
            insert into ops.rule_admission
              (rule_id,guidance_intake_id,enforcement_class,enforcement_status,binding_moment,
               applicability,projection,reachability,input_contract,fixture_refs,
               state,admitted_by,admitted_at)
            values (%s,%s,'machine_enforceable','hard_enforced','before fixture action',
                    '{"workflows":["db-gate"]}',
                    '{"targets":["db-gate"]}', '{"paths":["database"]}',
                    '{"type":"object"}',
                    array['ops/rule-admission-db-gate.py'],'admitted',%s,now())
        """, (rule_id, intake_id, actor))
        cur.execute("""
            insert into ops.enforcement_control_catalog
              (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
            values ('db-gate-fixture','migration:0194',
                    'ops/rule-admission-db-gate.py','transactional_schema',true,now())
            on conflict (control_key) do update set installed=true,verified_at=now()
        """)
        cur.execute("""
            insert into ops.rule_control_binding
              (rule_id,control_key,statement_hash,binding_contract)
            select id,'db-gate-fixture',encode(digest(statement,'sha256'),'hex'),
                   '{"fixture":"rule-admission-db-gate"}'::jsonb
              from rule where id=%s
        """, (rule_id,))
        cur.execute("""
            insert into ops.rule_enforcement_point
              (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
            values (%s,'db-gate-fixture','migration:0148',
                    'ops/rule-admission-db-gate.py','transactional_schema',true,now())
        """, (rule_id,))
        cur.execute("""
            with approved as (
              select r.id,r.version,encode(digest(r.statement,'sha256'),'hex') statement_hash,
                     jsonb_build_object(
                       'fixture','control-plane-db-gate',
                       'binding_moment','before fixture action',
                       'applicability','{"workflows":["db-gate"]}'::jsonb,
                       'projection','{"targets":["db-gate"]}'::jsonb,
                       'reachability','{"paths":["database"]}'::jsonb,
                       'input_contract','{"type":"object"}'::jsonb) contract
                from rule r where r.id=%s
            )
            insert into ops.rule_approval_receipt
              (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
               enforcement_status,requested_control_keys,installed_control_keys,reason,
               normalized_contract,contract_hash,evidence_refs)
            select 'db-gate-approval:'||id::text,id,version+1,statement_hash,%s,
                   'machine_enforceable','hard_enforced',array['db-gate-fixture'],
                   array['db-gate-fixture'],'rollback-only enforced activation fixture',
                   contract,encode(digest(contract::text,'sha256'),'hex'),
                   array['ops/rule-admission-db-gate.py']
              from approved
        """, (rule_id, actor))
        cur.execute("""
            insert into ops.authority_receipt
              (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
               contract_hash,evidence_refs)
            select 'approval:'||ar.idempotency_key,'activation','rule',ar.rule_id,
                   ar.actor_id,'rollback-only exact approval fixture',
                   ar.contract_hash,ar.evidence_refs
              from ops.rule_approval_receipt ar where ar.rule_id=%s
        """, (rule_id,))
        # Exercise the trigger as its real firing role, not as owner. A
        # grant that exists only for the migration actor is not a control.
        # The Neon owner credential is intentionally not standing SET-role
        # enabled for runtime bundles. Enable it inside this transaction
        # only, switch to the firing role, then the final rollback erases
        # the temporary membership option along with every fixture.
        grant_settable_runtime_roles(cur, "carr_writer", "carr_jobs")
        set_local_role(cur, "carr_writer")
        cur.execute("update rule set status='active',activated_by=%s,activated_at=now(),enforcement='gate' where id=%s",
                    (actor, rule_id))
        cur.execute("reset role")
        cur.execute("select status from rule where id=%s", (rule_id,))
        if fetchone_required(cur.fetchone(), "activated fixture rule")[0] != "active":
            fail("admitted rule did not activate")
        cur.execute("savepoint active_rule_drift_refusal")
        try:
            set_local_role(cur, "carr_writer")
            cur.execute("update rule set statement=statement||' drift' where id=%s", (rule_id,))
            fail("active rule statement changed under an old approval receipt")
        except psycopg.Error:
            cur.execute("rollback to savepoint active_rule_drift_refusal")
        finally:
            cur.execute("reset role")
        cur.execute("""
            select r.version=ar.rule_version
              and encode(digest(r.statement,'sha256'),'hex')=ar.statement_hash
              from rule r join ops.rule_approval_receipt ar on ar.rule_id=r.id
             where r.id=%s
        """, (rule_id,))
        if fetchone_required(cur.fetchone(), "active rule immutable preimage")[0] is not True:
            fail("active rule version/hash no longer matches its approval receipt")
        cur.execute("select count(*) from ops.applicable_rules('db-gate',null,null) where rule_id=%s",
                    (rule_id,))
        if fetchone_required(cur.fetchone(), "receipt-bound applicable rule")[0] != 1:
            fail("exact active enforced rule is absent from the policy compiler")
        # 0228 may preserve an OLD 0194 pre-activation receipt only through
        # its exact migration-time anchor. A fresh post-0228 approval is
        # already post-version and must never be made to look legacy; nor
        # may a caller claim a matching receipt with a substituted hash.
        for savepoint, sql_text, message in (
            ("fresh_receipt_anchor_refusal", """
                insert into ops.rule_approval_lifecycle_anchor
                  (approval_receipt_id,rule_id,rule_version_after,statement_hash)
                select ar.id,ar.rule_id,ar.rule_version,ar.statement_hash
                  from ops.rule_approval_receipt ar where ar.rule_id=%s
            """, "fresh post-version approval was accepted as a legacy anchor"),
            ("mismatched_anchor_refusal", """
                insert into ops.rule_approval_lifecycle_anchor
                  (approval_receipt_id,rule_id,rule_version_after,statement_hash)
                select ar.id,ar.rule_id,ar.rule_version+1,repeat('0',64)
                  from ops.rule_approval_receipt ar where ar.rule_id=%s
            """, "mismatched legacy anchor was accepted"),
        ):
            cur.execute(f"savepoint {savepoint}")
            try:
                cur.execute(sql_text, (rule_id,))
                fail(message)
            except psycopg.Error:
                cur.execute(f"rollback to savepoint {savepoint}")
        for savepoint, sql_text, message in (
            ("active_admission_drift_refusal",
             "update ops.rule_admission set applicability='{}'::jsonb where rule_id=%s",
             "active rule admission changed under an old approval receipt"),
            ("active_control_removal_refusal",
             "update ops.rule_enforcement_point set installed=false where rule_id=%s",
             "active rule enforcement point was removed under an old approval receipt"),
            ("retirement_preimage_drift_refusal",
             "update rule set status='retired',statement=statement||' drift' where id=%s",
             "approved rule substance changed during retirement"),
            ("unreceipted_retirement_refusal",
             "update rule set status='retired' where id=%s",
             "routine writer retired an approved rule without Joe authority"),
            ("unreceipted_deactivation_refusal",
             "update rule set status='proposed' where id=%s",
             "routine writer deactivated an approved rule without Joe authority"),
            ("approved_rule_noop_update_refusal",
             "update rule set statement=statement where id=%s",
             "routine writer invalidated an approved rule through a no-op version bump"),
        ):
            cur.execute(f"savepoint {savepoint}")
            try:
                set_local_role(cur, "carr_writer")
                cur.execute(sql_text, (rule_id,))
                fail(message)
            except psycopg.Error:
                cur.execute(f"rollback to savepoint {savepoint}")
            finally:
                cur.execute("reset role")

        # A proposed rule has no approval receipt, but its retirement is
        # still a permanent tombstone. Build one through the same receipt
        # precondition then prove a routine writer cannot alter any
        # tombstone field or revive it.
        tombstone_rule = fetchone_required(cur.execute("""
            insert into rule(statement,human_quote,taught_by,status)
            values ('retired rule fixture','fixture',%s,'proposed') returning id
        """, (actor,)).fetchone(), "retired proposed fixture")[0]
        tombstone_at = fetchone_required(cur.execute("""
            insert into ops.rule_retirement_receipt
              (idempotency_key,rule_id,rule_version_before,rule_version_after,
               statement_hash,previous_status,actor_id,reason,contract_hash,retired_at)
            select 'db-gate-retirement:'||id::text,id,version,version+1,
                   encode(digest(statement,'sha256'),'hex'),'proposed',%s,
                   'rollback-only retired tombstone fixture',
                   encode(digest('{}'::text,'sha256'),'hex'),now()
              from rule where id=%s returning retired_at
        """, (actor, tombstone_rule)).fetchone(), "retired proposed receipt")[0]
        cur.execute("""update rule set status='retired',retired_by=%s,retired_at=%s
                       where id=%s""", (actor, tombstone_at, tombstone_rule))
        for savepoint, sql_text, message in (
            ("retired_rule_mutation_refusal",
             "update rule set statement=statement||' drift' where id=%s",
             "routine writer changed retired rule statement"),
            ("retired_rule_scope_mutation_refusal",
             "update rule set scope='{\"workflows\":[\"drift\"]}'::jsonb where id=%s",
             "routine writer changed retired rule scope"),
            ("retired_rule_actor_mutation_refusal",
             "update rule set retired_by=null where id=%s",
             "routine writer changed retired rule actor"),
            ("retired_rule_timestamp_mutation_refusal",
             "update rule set retired_at=now() where id=%s",
             "routine writer changed retired rule timestamp"),
            ("retired_rule_revival_refusal",
             "update rule set status='proposed' where id=%s",
             "routine writer revived a retired rule"),
        ):
            cur.execute(f"savepoint {savepoint}")
            try:
                set_local_role(cur, "carr_writer")
                cur.execute(sql_text, (tombstone_rule,))
                fail(message)
            except psycopg.Error:
                cur.execute(f"rollback to savepoint {savepoint}")
            finally:
                cur.execute("reset role")

    print('rule-admission-db-gate passed: admission, approval and retirement refusals exercised')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
