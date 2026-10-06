#!/usr/bin/env python3
"""Superuser-only, rollback-only positive acceptance for the Joe rule lifecycle.

This is deliberately not a marked portable DB gate: managed database owners
cannot impersonate externally provisioned authority LOGIN principals. The
local PostgreSQL runner supplies a fresh loopback superuser DSN; every role,
rule and receipt created here is rolled back.
"""
from __future__ import annotations

import os
import uuid
from typing import NoReturn
from urllib.parse import urlparse

import psycopg


def refuse(message: str) -> NoReturn:
    raise RuntimeError(message)


def install_pre_trigger_rule_shape(cur, statement: str, params: tuple) -> None:
    """Reconstruct a pre-admission-trigger rule without owner DDL.

    The local superuser uses a savepoint-scoped replication role so synthetic
    historical rows do not create deferred events or conflict with pending
    events from the real lifecycle probes around them.
    """
    cur.execute("savepoint pre_trigger_rule_shape")
    try:
        cur.execute("set local session_replication_role=replica")
        cur.execute(statement, params)
        cur.execute("set local session_replication_role=origin")
    except psycopg.Error:
        cur.execute("rollback to savepoint pre_trigger_rule_shape")
        raise
    cur.execute("release savepoint pre_trigger_rule_shape")


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "127.0.0.1", "localhost", "::1",
    }:
        refuse("atomic rule acceptance requires an explicit loopback CARR_LOCAL_PG_DSN")

    with psycopg.connect(dsn) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("begin")
                cur.execute("select rolsuper from pg_roles where rolname=current_user")
                row = cur.fetchone()
                if row is None or row[0] is not True:
                    refuse("atomic rule acceptance requires a disposable local superuser")
                cur.execute(
                    """do $$
                       begin
                         if not exists (
                           select 1 from pg_roles where rolname='carr_authority_joe'
                         ) then
                           create role carr_authority_joe login;
                         elsif not (
                           select rolcanlogin from pg_roles where rolname='carr_authority_joe'
                         ) then
                           raise exception 'carr_authority_joe must be an exact LOGIN identity';
                         end if;
                       end $$"""
                )
                cur.execute("grant carr_authority to carr_authority_joe")
                # carr_authority_dell: a role that DOES carry EXECUTE on the
                # amendment function (member of carr_authority) but must still
                # be refused by ops.amend_rule_statement's OWN internal check
                # -- proving the Joe-only guard is not merely the GRANT.
                cur.execute(
                    """do $$
                       begin
                         if not exists (
                           select 1 from pg_roles where rolname='carr_authority_dell'
                         ) then
                           create role carr_authority_dell login;
                         elsif not (
                           select rolcanlogin from pg_roles where rolname='carr_authority_dell'
                         ) then
                           raise exception 'carr_authority_dell must be an exact LOGIN identity';
                         end if;
                       end $$"""
                )
                cur.execute("grant carr_authority to carr_authority_dell")
                cur.execute("grant carr_writer to current_user")
                cur.execute("select id from actor where slug='joe' and kind='human' and active")
                actor = cur.fetchone()
                if actor is None:
                    refuse("Joe actor fixture is missing")
                rule_id = uuid.uuid4()
                control_key = f"local-rule-acceptance-{uuid.uuid4()}"
                approve_key = f"local-approve-{uuid.uuid4()}"
                retire_key = f"local-retire-{uuid.uuid4()}"
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local exact approval fixture','Joe approved the fixture',%s,'proposed')""",
                    (rule_id, actor[0]),
                )
                cur.execute(
                    """insert into ops.enforcement_control_catalog
                         (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                       values (%s,'local-pg-acceptance','ops/atomic-rule-approval-local-pg-acceptance.py',
                               'transactional_schema',true,now())""",
                    (control_key,),
                )
                # The SIEP-12 epoch snapshot is intentionally fail-closed when
                # an active rule has no delivery layer. This fixture installs a
                # real transactional control below, so declare that exact
                # control-layer delivery before activation; otherwise the
                # acceptance itself creates an untagged rule and can no longer
                # settle its deferred epoch triggers.
                cur.execute(
                    """insert into ops.rule_load_layer
                         (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                       values (%s,left(%s::text,8),'control','{}'::text[],'shared',
                               'local atomic acceptance transactional control',
                               'ops/atomic-rule-approval-local-pg-acceptance.py',
                               repeat('0',64))""",
                    (rule_id, rule_id),
                )
                cur.execute(
                    """insert into ops.rule_control_binding
                         (rule_id,control_key,statement_hash,binding_contract)
                       select id,%s,encode(digest(statement,'sha256'),'hex'),
                              '{"fixture":"local atomic approval"}'::jsonb
                         from rule where id=%s""",
                    (control_key, rule_id),
                )

                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array[%s],%s,%s)",
                    (rule_id, control_key, approve_key, "Joe local authority acceptance"),
                )
                approved = cur.fetchone()
                if approved is None or approved[0].get("policy_status") != "active" \
                        or approved[0].get("replayed") is not False:
                    refuse("atomic approve did not activate exact enforcement")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array[%s],%s,%s)",
                    (rule_id, control_key, approve_key, "Joe local authority acceptance"),
                )
                replay = cur.fetchone()
                if replay is None or replay[0].get("replayed") is not True:
                    refuse("atomic approval exact replay failed")
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (rule_id,),
                )
                applicable = cur.fetchone()
                if applicable is None or applicable[0] != 1:
                    refuse("receipt-bound policy compiler omitted the approved rule")
                cur.execute(
                    """select count(*) from ops.applicable_rules(%s,%s,%s)
                        where rule_id=%s""",
                    ("finite-workflow", "finite-surface", "finite-tier", rule_id),
                )
                finite_applicable = cur.fetchone()
                if finite_applicable is None or finite_applicable[0] != 1:
                    refuse("global approved rule did not apply to a finite context")

                # ---- the REAL eval_receipt control (migration 0753) ----
                # Rule 6cbaa63a names 'eval_receipt CI check' as its carrying
                # control. Unlike the fixtures above, this does NOT insert a
                # catalog row: it proves the row the migration set seeded is
                # one approve-rule accepts, binding it from the catalog with no
                # pre-written ops.rule_control_binding. Savepoint-scoped so the
                # extra active rule leaves no deferred epoch work behind.
                cur.execute("reset session authorization")
                cur.execute("savepoint eval_receipt_control")
                eval_rule_id = uuid.uuid4()
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local eval-receipt control fixture',
                               'Joe approved the eval receipt fixture',%s,'proposed')""",
                    (eval_rule_id, actor[0]),
                )
                cur.execute(
                    """insert into ops.rule_load_layer
                         (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                       values (%s,left(%s::text,8),'control','{}'::text[],'shared',
                               'local eval_receipt control acceptance',
                               'ops/atomic-rule-approval-local-pg-acceptance.py',
                               repeat('0',64))""",
                    (eval_rule_id, eval_rule_id),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array['eval_receipt'],%s,%s)",
                    (eval_rule_id, f"local-eval-receipt-{uuid.uuid4()}",
                     "Joe local eval_receipt control acceptance"),
                )
                eval_approved = cur.fetchone()
                if eval_approved is None or eval_approved[0].get("policy_status") != "active":
                    refuse("approve-rule did not accept the seeded eval_receipt control")
                cur.execute("reset session authorization")
                cur.execute(
                    """select binding_contract->>'implementation_ref'
                         from ops.rule_control_binding
                        where rule_id=%s and control_key='eval_receipt'""",
                    (eval_rule_id,),
                )
                eval_binding = cur.fetchone()
                if eval_binding is None or "ops/check-eval-receipt.py" not in (eval_binding[0] or ""):
                    refuse("eval_receipt binding does not name ops/check-eval-receipt.py")
                cur.execute("rollback to savepoint eval_receipt_control")
                cur.execute("release savepoint eval_receipt_control")
                cur.execute("set session authorization carr_authority_joe")

                # ---- versioned amendment (WR-000019 slice S10) ----
                # A SEPARATE rule/control fixture, deliberately never retired in
                # this script: ops.retire_rule's own approval-receipt lookup
                # still requires an EXACT statement-hash match (slice S10 only
                # taught ops.applicable_rules() to follow an amendment chain,
                # by design -- ops.approve_rule and ops.retire_rule are
                # untouched), so amending then retiring the rule_id fixture
                # above would conflate two different questions in one fixture.
                amend_rule_id = uuid.uuid4()
                amend_control_key = f"local-amend-acceptance-{uuid.uuid4()}"
                amend_pack = f"local-amend-pack-{uuid.uuid4()}"
                amend_approve_key = f"local-amend-approve-{uuid.uuid4()}"
                amend_key = f"local-amend-{uuid.uuid4()}"
                original_statement = "local exact amendment fixture, before wording fix"
                # Back to the disposable superuser for fixture setup, same as
                # the rule_id fixture above did BEFORE its own "set session
                # authorization carr_authority_joe" -- carr_authority_joe has
                # no direct grant on `rule`/`ops.enforcement_control_catalog`/
                # `ops.rule_control_binding`; it only ever writes through the
                # SECURITY DEFINER authority functions.
                cur.execute("reset session authorization")
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,%s,'Joe approved the amendment fixture',%s,'proposed')""",
                    (amend_rule_id, original_statement, actor[0]),
                )
                cur.execute(
                    """insert into ops.enforcement_control_catalog
                         (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                       values (%s,'local-pg-acceptance','ops/atomic-rule-approval-local-pg-acceptance.py',
                               'transactional_schema',true,now())""",
                    (amend_control_key,),
                )
                # This second fixture intentionally remains active after the
                # first rule is retired. Make it a real pack-backed rule so
                # SIEP-12 can settle the final deferred policy epoch with one
                # non-empty pack and no untagged/orphaned delivery rows. The
                # acceptance therefore proves retirement cleanup without
                # weakening the live snapshot invariant or manufacturing a
                # separate rule solely to satisfy the health check.
                cur.execute(
                    """insert into ops.rule_pack
                         (pack,title,description,triggers,source)
                       values (%s,'Local amendment acceptance',
                               'Rollback-only active anchor for atomic lifecycle acceptance',
                               array['local-amendment-acceptance'],
                               'ops/atomic-rule-approval-local-pg-acceptance.py')""",
                    (amend_pack,),
                )
                cur.execute(
                    """insert into ops.rule_load_layer
                         (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                       values (%s,left(%s::text,8),'pack',array[%s],'shared',
                               'local amendment acceptance remains active after retirement test',
                               'ops/atomic-rule-approval-local-pg-acceptance.py',
                               repeat('0',64))""",
                    (amend_rule_id, amend_rule_id, amend_pack),
                )
                cur.execute(
                    """insert into ops.rule_control_binding
                         (rule_id,control_key,statement_hash,binding_contract)
                       select id,%s,encode(digest(statement,'sha256'),'hex'),
                              '{"fixture":"local amendment"}'::jsonb
                         from rule where id=%s""",
                    (amend_control_key, amend_rule_id),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array[%s],%s,%s)",
                    (amend_rule_id, amend_control_key, amend_approve_key, "Joe local authority acceptance"),
                )
                amend_approved = cur.fetchone()
                if amend_approved is None or amend_approved[0].get("policy_status") != "active":
                    refuse("amendment fixture did not activate")
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (amend_rule_id,),
                )
                before_amend = cur.fetchone()
                if before_amend is None or before_amend[0] != 1:
                    refuse("amendment fixture was not applicable before amendment")

                amended_statement = "local exact amendment fixture, AFTER wording fix"
                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (amend_rule_id, amended_statement, amend_key, "fixing the wording, same meaning"),
                )
                amended = cur.fetchone()
                if amended is None or amended[0].get("ok") is not True or amended[0].get("replayed") is not False:
                    refuse("amend_rule_statement did not report a fresh success")
                amendment_receipt_id = amended[0].get("amendment_receipt_id")
                if not amendment_receipt_id:
                    refuse("amend_rule_statement did not return a receipt id")

                # RECEIPT EXISTS, STATEMENT UPDATED, PRIOR HASH CHAINS -- the
                # exact round trip WR-000019 slice S10 names.
                cur.execute(
                    """select r.statement=%s and r.version=ar.rule_version_after
                         and ar.prior_statement_hash=encode(digest(%s,'sha256'),'hex')
                         and ar.new_statement=%s
                         and ar.new_statement_hash=encode(digest(r.statement,'sha256'),'hex')
                         and ar.rationale='fixing the wording, same meaning'
                         and ar.id=%s
                         from rule r join ops.rule_amendment_receipt ar on ar.rule_id=r.id
                        where r.id=%s""",
                    (amended_statement, original_statement, amended_statement,
                     amendment_receipt_id, amend_rule_id),
                )
                amendment_binding = cur.fetchone()
                if amendment_binding is None or amendment_binding[0] is not True:
                    refuse("amendment receipt is not exactly bound to the amended statement")

                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (amend_rule_id, amended_statement, amend_key, "fixing the wording, same meaning"),
                )
                amend_replay = cur.fetchone()
                if amend_replay is None or amend_replay[0].get("replayed") is not True:
                    refuse("amendment exact replay failed")

                # THE PART THAT IS EASY TO MISS: an amended ACTIVE rule must
                # keep reciting under its old approval, not silently vanish
                # from ops.applicable_rules() the moment its wording changed.
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (amend_rule_id,),
                )
                after_amend = cur.fetchone()
                if after_amend is None or after_amend[0] != 1:
                    refuse("amended active rule dropped out of applicable_rules -- "
                           "it must keep reciting under its old approval")

                # Dell HAS execute (carr_authority_dell is a carr_authority
                # member) but ops.amend_rule_statement's own body must still
                # refuse -- this is the check the GRANT alone cannot prove.
                cur.execute("reset session authorization")
                cur.execute("savepoint amend_requires_joe_not_just_authority")
                cur.execute("set session authorization carr_authority_dell")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (amend_rule_id, "a Dell-authored rewrite",
                         f"local-amend-dell-{uuid.uuid4()}", "Dell tries to amend"),
                    )
                    refuse("amend_rule_statement succeeded for Dell, not Joe")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_requires_joe_not_just_authority")
                    if "joe authority" not in str(exc).lower():
                        refuse(f"Dell amendment refusal was for the wrong reason: {exc}")
                finally:
                    cur.execute("reset session authorization")
                cur.execute("release savepoint amend_requires_joe_not_just_authority")

                # A no-op "amendment" (new text hashes identically to the
                # current text) is refused, not silently accepted with a
                # receipt that would claim a correction never made.
                cur.execute("set session authorization carr_authority_joe")
                cur.execute("savepoint amend_refuses_noop")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (amend_rule_id, amended_statement,
                         f"local-amend-noop-{uuid.uuid4()}", "no actual change"),
                    )
                    refuse("amend_rule_statement accepted a no-op amendment")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_refuses_noop")
                    if "no-op" not in str(exc).lower():
                        refuse(f"no-op amendment refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint amend_refuses_noop")

                # A RETIRED rule's words are history; amending a tombstone is
                # exactly what the retirement receipt already refuses to be
                # reopened from -- ops.amend_rule_statement must refuse it
                # directly, at the database layer, not merely because the MCP
                # handler happens to check status first.
                retired_rule_id = uuid.uuid4()
                retire_for_amend_key = f"local-retire-for-amend-{uuid.uuid4()}"
                cur.execute("reset session authorization")
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local exact retired-amendment fixture','Joe said retire it',%s,'proposed')""",
                    (retired_rule_id, actor[0]),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (retired_rule_id, "never needed", retire_for_amend_key),
                )
                retired_for_amend = cur.fetchone()
                if retired_for_amend is None or retired_for_amend[0].get("status") != "retired":
                    refuse("retired-amendment fixture did not actually retire")
                cur.execute("savepoint amend_refuses_retired")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (retired_rule_id, "reopening a tombstone",
                         f"local-amend-retired-{uuid.uuid4()}", "try to reopen"),
                    )
                    refuse("amend_rule_statement succeeded on a retired rule")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_refuses_retired")
                    # Deliberately the SPECIFIC retired-rule message, not the
                    # broader "expected proposed or active" one a few lines
                    # below it in the function also happens to fire for a
                    # retired rule (it excludes retired same as superseded) --
                    # a generic substring match on "retired" would still pass
                    # with the specific check deleted entirely, since that
                    # message also interpolates the status word "retired".
                    if "stays as written" not in str(exc).lower():
                        refuse(f"retired-rule amendment refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint amend_refuses_retired")
                cur.execute("reset session authorization")

                # THE TRIGGER'S OWN GUARD, independent of the function: even a
                # local superuser writing `update rule set statement=...`
                # directly (never touching ops.amend_rule_statement, so no
                # matching ops.rule_amendment_receipt row exists) must be
                # refused by ops.require_rule_admission -- the receipted
                # function is not a courtesy path, it is the ONLY path.
                cur.execute("savepoint amend_trigger_refuses_bypass")
                try:
                    cur.execute(
                        "update rule set statement=%s where id=%s",
                        ("a direct bypass with no amendment receipt", amend_rule_id),
                    )
                    refuse("a direct UPDATE changed an active rule's statement with no amendment receipt")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_trigger_refuses_bypass")
                    if "immutable" not in str(exc).lower():
                        refuse(f"direct-bypass refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint amend_trigger_refuses_bypass")

                cur.execute("savepoint amend_requires_authority")
                cur.execute("set local role carr_writer")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (amend_rule_id, "an unauthorized rewrite",
                         f"local-amend-unauth-{uuid.uuid4()}", "no authority"),
                    )
                    refuse("amend_rule_statement succeeded for a non-authority role")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_requires_authority")
                    # Two valid refusal shapes, both proving the same boundary:
                    # carr_writer has no EXECUTE grant on the function at all
                    # (revoke all from public + grant to carr_authority only),
                    # so Postgres refuses before the function body ever runs;
                    # a role that DID have execute (e.g. carr_authority_dell)
                    # would instead reach ops.authority_actor_slug()'s own
                    # "is not an admitted human authority principal" check.
                    msg = str(exc).lower()
                    if "authority" not in msg and "permission denied" not in msg:
                        refuse(f"amendment authority refusal was for the wrong reason: {exc}")
                finally:
                    cur.execute("reset role")
                cur.execute("release savepoint amend_requires_authority")
                # Restore the session authorization the ORIGINAL rule_id
                # fixture's own retire_rule call below still expects.
                cur.execute("set session authorization carr_authority_joe")

                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (rule_id, "Joe local authority retirement", retire_key),
                )
                retired = cur.fetchone()
                if retired is None or retired[0].get("status") != "retired" \
                        or retired[0].get("replayed") is not False:
                    refuse("Joe authority retirement did not complete")
                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (rule_id, "Joe local authority retirement", retire_key),
                )
                retire_replay = cur.fetchone()
                if retire_replay is None or retire_replay[0].get("replayed") is not True:
                    refuse("Joe authority retirement exact replay failed")
                cur.execute("reset session authorization")
                for label, mutation in (
                    ("retired statement", "update rule set statement=statement||' drift' where id=%s"),
                    ("retired scope", "update rule set scope='{\"workflows\":[\"drift\"]}'::jsonb where id=%s"),
                    ("retired actor", "update rule set retired_by=null where id=%s"),
                    ("retired timestamp", "update rule set retired_at=now() where id=%s"),
                    ("retired revival", "update rule set status='proposed' where id=%s"),
                ):
                    cur.execute("savepoint retired_tombstone_mutation")
                    try:
                        cur.execute("set local role carr_writer")
                        cur.execute(mutation, (rule_id,))
                        refuse(f"retired tombstone mutation refused: {label} unexpectedly succeeded")
                    except psycopg.Error:
                        cur.execute("rollback to savepoint retired_tombstone_mutation")
                    finally:
                        cur.execute("reset role")
                # A local superuser can bypass row triggers; deliberately do so
                # only inside this rollback-only acceptance to prove replay is
                # not fooled by a corrupted tombstone it did not create.
                # Settle pending deferred events before owner-only DDL, then
                # restore deferred mode before the legacy-shape fixtures below.
                cur.execute("set constraints all immediate")
                cur.execute("savepoint altered_retirement_replay")
                try:
                    cur.execute("alter table rule disable trigger rule_activation_requires_admission")
                    cur.execute("update rule set statement=statement||' altered' where id=%s", (rule_id,))
                    cur.execute("alter table rule enable trigger rule_activation_requires_admission")
                    cur.execute("set session authorization carr_authority_joe")
                    cur.execute(
                        "select ops.retire_rule(%s,%s,null,%s)",
                        (rule_id, "Joe local authority retirement", retire_key),
                    )
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint altered_retirement_replay")
                    cur.execute("reset session authorization")
                    if "current retired rule no longer matches the immutable retirement" not in str(exc).lower():
                        refuse(f"altered retirement replay refused for the wrong reason: {exc}")
                else:
                    cur.execute("rollback to savepoint altered_retirement_replay")
                    cur.execute("reset session authorization")
                    refuse("altered retirement replay was accepted")
                cur.execute("release savepoint altered_retirement_replay")
                cur.execute(
                    """select r.status='retired' and r.retired_by=rr.actor_id
                         and r.retired_at=rr.retired_at
                         and r.version=rr.rule_version_after
                         and encode(digest(r.statement,'sha256'),'hex')=rr.statement_hash
                         from rule r join ops.rule_retirement_receipt rr on rr.rule_id=r.id
                        where r.id=%s""",
                    (rule_id,),
                )
                retirement_binding = cur.fetchone()
                if retirement_binding is None or retirement_binding[0] is not True:
                    refuse("retirement receipt is not exactly bound to the retired tombstone")
                cur.execute("set constraints all deferred")

                # A receipted retirement's row must NEVER read as legacy --
                # migration 0351 must not have relaxed anything for a rule
                # that DOES carry an exact approval receipt.
                cur.execute(
                    """select legacy_admission is null and approval_receipt_id is not null
                         from ops.rule_retirement_receipt where rule_id=%s""",
                    (rule_id,),
                )
                receipted_retirement_not_legacy = cur.fetchone()
                if receipted_retirement_not_legacy is None or receipted_retirement_not_legacy[0] is not True:
                    refuse("a receipted retirement was recorded as legacy, or lost its approval_receipt_id")

                # The mutual-exclusion CHECK itself, independent of any
                # function -- a row claiming BOTH an approval receipt AND a
                # legacy admission is refused at the database layer even for
                # a direct superuser insert, not merely something ops.retire_rule
                # happens never to construct.
                cur.execute(
                    """select rule_version_before,rule_version_after,statement_hash,previous_status,
                              actor_id,reason,superseded_by,approval_receipt_id,contract_hash,retired_at
                         from ops.rule_retirement_receipt where rule_id=%s""",
                    (rule_id,),
                )
                base_row = cur.fetchone()
                if base_row is None:
                    refuse("could not read back the base retirement receipt row for the exclusivity probe")
                cur.execute("savepoint retirement_legacy_excludes_receipt_probe")
                try:
                    cur.execute(
                        """insert into ops.rule_retirement_receipt
                             (idempotency_key,rule_id,rule_version_before,rule_version_after,statement_hash,
                              previous_status,actor_id,reason,superseded_by,approval_receipt_id,
                              legacy_admission,contract_hash,retired_at)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (f"local-exclusivity-probe-{uuid.uuid4()}", rule_id, *base_row[:8],
                         "legacy_admission: should never coexist with a real receipt", base_row[8], base_row[9]),
                    )
                    refuse("a retirement receipt row claiming BOTH an approval receipt and a "
                           "legacy_admission marker was accepted")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint retirement_legacy_excludes_receipt_probe")
                    if "retirement_legacy_excludes_receipt" not in str(exc):
                        refuse(f"exclusivity probe refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint retirement_legacy_excludes_receipt_probe")

                # ---- legacy rule lifecycle (WR-000019 follow-up, migration 0351) ----
                # 217 of 219 active rules today have no ops.rule_approval_receipt
                # row: they were taught and activated by hand before the receipt
                # system (migration 0228 and friends) existed. The trigger that
                # enforces admission on activation (ops.require_rule_admission)
                # did not exist when they were activated either, so the ONLY
                # honest way to build a rollback-only fixture that reproduces
                # that historical shape is the same trigger-disable technique
                # already used above for "altered retirement replay" -- this is
                # not bypassing a live guard, it is reconstructing a row shape
                # that predates the guard's own existence.
                cur.execute("reset session authorization")

                legacy_retire_id = uuid.uuid4()
                legacy_retire_key = f"local-legacy-retire-{uuid.uuid4()}"
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local legacy active fixture, pre-receipt era',
                               'Joe taught this before receipts existed',%s,'proposed')""",
                    (legacy_retire_id, actor[0]),
                )
                install_pre_trigger_rule_shape(
                    cur,
                    """update rule set status='active', activated_by=%s,
                           activated_at='2020-01-01T00:00:00+00' where id=%s""",
                    (actor[0], legacy_retire_id),
                )
                cur.execute(
                    "select count(*) from ops.rule_approval_receipt where rule_id=%s",
                    (legacy_retire_id,),
                )
                legacy_fixture_receipt_count = cur.fetchone()
                if legacy_fixture_receipt_count is None or legacy_fixture_receipt_count[0] != 0:
                    refuse("legacy retirement fixture unexpectedly has an approval receipt")

                # Non-authority is refused on the legacy path exactly as on the
                # receipted path -- the Joe-authority check runs before any
                # legacy-vs-receipted branching.
                cur.execute("savepoint legacy_retire_requires_joe")
                cur.execute("set session authorization carr_authority_dell")
                try:
                    cur.execute(
                        "select ops.retire_rule(%s,%s,null,%s)",
                        (legacy_retire_id, "Dell tries to retire a legacy rule",
                         f"local-legacy-retire-dell-{uuid.uuid4()}"),
                    )
                    refuse("retire_rule succeeded for Dell on a legacy rule")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint legacy_retire_requires_joe")
                    if "joe authority" not in str(exc).lower():
                        refuse(f"Dell legacy-retirement refusal was for the wrong reason: {exc}")
                finally:
                    cur.execute("reset session authorization")
                cur.execute("release savepoint legacy_retire_requires_joe")

                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (legacy_retire_id, "legacy retirement acceptance (0351)", legacy_retire_key),
                )
                legacy_retired = cur.fetchone()
                if legacy_retired is None or legacy_retired[0].get("ok") is not True \
                        or legacy_retired[0].get("replayed") is not False \
                        or legacy_retired[0].get("status") != "retired":
                    refuse("legacy retire_rule did not report a fresh success")
                legacy_note = legacy_retired[0].get("legacy_admission")
                if not legacy_note or "legacy_admission" not in legacy_note:
                    refuse("legacy retirement did not report a legacy_admission marker")

                cur.execute(
                    """select legacy_admission is not null and approval_receipt_id is null
                         from ops.rule_retirement_receipt where rule_id=%s""",
                    (legacy_retire_id,),
                )
                legacy_row = cur.fetchone()
                if legacy_row is None or legacy_row[0] is not True:
                    refuse("legacy retirement receipt did not record legacy_admission with a null approval_receipt_id")

                # Exact replay of the legacy retirement still works.
                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (legacy_retire_id, "legacy retirement acceptance (0351)", legacy_retire_key),
                )
                legacy_replay = cur.fetchone()
                if legacy_replay is None or legacy_replay[0].get("replayed") is not True \
                        or legacy_replay[0].get("legacy_admission") != legacy_note:
                    refuse("legacy retirement exact replay failed")

                # A receiptless ACTIVE rule that does NOT predate the receipt
                # cutover is a real defect, not history -- it must keep being
                # refused exactly as before 0351. Activated "now", well after
                # the earliest receipt already written above.
                cur.execute("reset session authorization")
                broken_rule_id = uuid.uuid4()
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local receiptless-but-not-legacy fixture',
                               'this one should still be refused',%s,'proposed')""",
                    (broken_rule_id, actor[0]),
                )
                install_pre_trigger_rule_shape(
                    cur,
                    "update rule set status='active', activated_by=%s, activated_at=now() where id=%s",
                    (actor[0], broken_rule_id),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute("savepoint non_legacy_receiptless_still_refused")
                try:
                    cur.execute(
                        "select ops.retire_rule(%s,%s,null,%s)",
                        (broken_rule_id, "should still be refused",
                         f"local-broken-retire-{uuid.uuid4()}"),
                    )
                    refuse("retire_rule accepted a receiptless active rule that postdates the cutover")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint non_legacy_receiptless_still_refused")
                    if "lacks its exact approval receipt" not in str(exc).lower():
                        refuse(f"non-legacy receiptless refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint non_legacy_receiptless_still_refused")
                cur.execute("reset session authorization")

                # A still-PROPOSED rule has no receipt either, but that is the
                # ordinary case ops.legacy_rule_admission_note must never call
                # "legacy" -- only an ACTIVE receiptless rule can be. Proves
                # the predicate's status guard, not just its timestamp check.
                proposed_amend_id = uuid.uuid4()
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local still-proposed amendment fixture','Joe said this, still proposed',%s,'proposed')""",
                    (proposed_amend_id, actor[0]),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (proposed_amend_id, "local still-proposed amendment fixture, reworded",
                     f"local-proposed-amend-{uuid.uuid4()}", "wording pass while still proposed"),
                )
                proposed_amended = cur.fetchone()
                if proposed_amended is None or proposed_amended[0].get("ok") is not True:
                    refuse("amendment of a still-proposed rule did not report success")
                if proposed_amended[0].get("legacy_admission") is not None:
                    refuse("a still-proposed rule's amendment was incorrectly marked legacy_admission")
                cur.execute("reset session authorization")

                # ---- legacy amendment ----
                legacy_amend_id = uuid.uuid4()
                legacy_amend_key = f"local-legacy-amend-{uuid.uuid4()}"
                legacy_original_statement = "local legacy active amendment fixture, before wording fix"
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,%s,'Joe taught this before receipts existed',%s,'proposed')""",
                    (legacy_amend_id, legacy_original_statement, actor[0]),
                )
                install_pre_trigger_rule_shape(
                    cur,
                    """update rule set status='active', activated_by=%s,
                           activated_at='2020-01-01T00:00:00+00' where id=%s""",
                    (actor[0], legacy_amend_id),
                )

                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (legacy_amend_id,),
                )
                legacy_applicable_before = cur.fetchone()
                if legacy_applicable_before is None or legacy_applicable_before[0] != 0:
                    refuse("a legacy rule with no approval receipt was somehow returned by applicable_rules "
                           "before any amendment -- the receipt-bound compiler must never see it")

                legacy_amended_statement = "local legacy active amendment fixture, AFTER wording fix"
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (legacy_amend_id, legacy_amended_statement, legacy_amend_key,
                     "fixing a legacy rule's wording (0351)"),
                )
                legacy_amended = cur.fetchone()
                if legacy_amended is None or legacy_amended[0].get("ok") is not True \
                        or legacy_amended[0].get("replayed") is not False:
                    refuse("legacy amend_rule_statement did not report a fresh success")
                legacy_amend_note = legacy_amended[0].get("legacy_admission")
                if not legacy_amend_note or "legacy_admission" not in legacy_amend_note:
                    refuse("legacy amendment did not report a legacy_admission marker")

                cur.execute(
                    """select r.statement=%s and legacy_admission is not null
                         from rule r join ops.rule_amendment_receipt ar on ar.rule_id=r.id
                        where r.id=%s and ar.idempotency_key=%s""",
                    (legacy_amended_statement, legacy_amend_id, legacy_amend_key),
                )
                legacy_amend_row = cur.fetchone()
                if legacy_amend_row is None or legacy_amend_row[0] is not True:
                    refuse("legacy amendment receipt did not record the new statement with legacy_admission set")

                # Still excluded from applicable_rules() after amendment too --
                # the legacy path never joins into the receipt-bound compiler,
                # so amending it changes nothing about that exclusion.
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (legacy_amend_id,),
                )
                legacy_applicable_after = cur.fetchone()
                if legacy_applicable_after is None or legacy_applicable_after[0] != 0:
                    refuse("an amended legacy rule appeared in applicable_rules -- it must still be delivered "
                           "only through ops.rule_delivery_plan, never the receipt-bound compiler")

                # Non-authority is refused on the legacy amendment path too.
                cur.execute("savepoint legacy_amend_requires_joe")
                cur.execute("reset session authorization")
                cur.execute("set session authorization carr_authority_dell")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (legacy_amend_id, "a Dell-authored legacy rewrite",
                         f"local-legacy-amend-dell-{uuid.uuid4()}", "Dell tries to amend a legacy rule"),
                    )
                    refuse("amend_rule_statement succeeded for Dell on a legacy rule")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint legacy_amend_requires_joe")
                    if "joe authority" not in str(exc).lower():
                        refuse(f"Dell legacy-amendment refusal was for the wrong reason: {exc}")
                finally:
                    cur.execute("reset session authorization")
                cur.execute("release savepoint legacy_amend_requires_joe")

                # Exact replay of the legacy amendment still works.
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (legacy_amend_id, legacy_amended_statement, legacy_amend_key,
                     "fixing a legacy rule's wording (0351)"),
                )
                legacy_amend_replay = cur.fetchone()
                if legacy_amend_replay is None or legacy_amend_replay[0].get("replayed") is not True \
                        or legacy_amend_replay[0].get("legacy_admission") != legacy_amend_note:
                    refuse("legacy amendment exact replay failed")
                cur.execute("reset session authorization")

                conn.rollback()
                print("PASS: atomic Joe rule approval, amendment, replay, retirement, "
                      "legacy lifecycle (0351) and rollback")
                return 0
        finally:
            conn.rollback()


if __name__ == "__main__":
    raise SystemExit(main())
