#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate for Program 6 sourced ready plans."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"program6-ready-plan-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refusal(cur, sql: str, params: tuple = (), label: str = "unsafe operation") -> None:
    cur.execute("savepoint program6_ready_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint program6_ready_refusal")
        return
    cur.execute("rollback to savepoint program6_ready_refusal")
    raise RuntimeError(f"{label} was accepted")


def one(cur, sql: str, params: tuple = ()):
    row = cur.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("required gate fixture row was not returned")
    return row


def ensure_authority_roles(cur) -> None:
    cur.execute(
        """do $$ begin
          if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
            create role carr_authority_joe login;
          end if;
          if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
            create role carr_authority_dell login;
          end if;
        end $$"""
    )
    cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
    grant_settable_runtime_roles(
        cur, "carr_authority_joe", "carr_authority_dell", "carr_writer", "carr_reader", "carr_jobs"
    )


def assert_wr122_route_is_fenced(cur) -> None:
    """The abandoned 0535/0536 WR122 route remains history, never execution."""
    expected = {
        ("PLAN-954b03dd464d-v1", "sha256:954b03dd464dab9986bc705d01fdb197edd29b23ad10ca673da53ad0ac03c27d", "0535_ready_plan_amendment.sql"),
        ("PLAN-954b03dd464d-v1", "sha256:954b03dd464dab9986bc705d01fdb197edd29b23ad10ca673da53ad0ac03c27d", "0536_ready_plan_amendment_scac_successor.sql"),
    }
    rows = set(cur.execute("""select plan_ref,plan_hash,unnest(migration_filenames)
                              from ops.engineering_stale_contract_fence
                             where work_request_ref='WR-000122'""").fetchall())
    if rows != expected:
        raise RuntimeError(f"WR122 stale route fence drifted: {rows!r}")
    refusal(cur, "insert into public.schema_migrations(filename,sha256) values (%s,%s)",
            ("0536_ready_plan_amendment_scac_successor.sql", "0" * 64),
            "stale WR122 migration route")


def doctrine_fixture(cur, actor_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID, uuid.UUID, str]:
    token = uuid.uuid4().hex
    source_doc = cur.execute(
        """insert into doctrine_document
             (slug,title,content_class,visibility,created_by)
           values (%s,'Program 6 ready source','reference','shared',%s)
           returning id""",
        (f"program6-ready-{token}", actor_id),
    ).fetchone()[0]
    source_section = cur.execute(
        """insert into doctrine_section
             (document_id,section_key,title,ordinal,status,current_version)
           values (%s,'source','Program 6 ready source',1,'active',1)
           returning id""",
        (source_doc,),
    ).fetchone()[0]
    source_text = "A sourced problem is triaged before a bounded plan is accepted."
    source_revision = cur.execute(
        """insert into doctrine_revision
             (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
           values (%s,1,%s,%s,%s,%s,'Program 6 ready source fixture')
           returning id""",
        (source_section, actor_id, Jsonb({"text": source_text}), source_text,
         hashlib.sha256(source_text.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (source_revision, source_section))

    runbook_doc = cur.execute(
        "select id from doctrine_document where slug='runbook' and visibility='shared'"
    ).fetchone()
    if runbook_doc:
        runbook_doc_id = runbook_doc[0]
    else:
        runbook_doc_id = cur.execute(
            """insert into doctrine_document
                 (slug,title,content_class,visibility,created_by)
               values ('runbook','Runbook','reference','shared',%s) returning id""",
            (actor_id,),
        ).fetchone()[0]
    runbook_key = f"program6-ready-{token}"
    runbook_section = cur.execute(
        """insert into doctrine_section
             (document_id,section_key,title,ordinal,status,current_version)
           values (%s,%s,'Bounded ready plan',999,'active',1) returning id""",
        (runbook_doc_id, runbook_key),
    ).fetchone()[0]
    runbook_text = "Inspect the named evidence, record the result, and stop within the declared caps."
    runbook_hash = hashlib.sha256(runbook_text.encode()).hexdigest()
    runbook_revision = cur.execute(
        """insert into doctrine_revision
             (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
           values (%s,1,%s,%s,%s,%s,'Program 6 ready runbook fixture')
           returning id""",
        (runbook_section, actor_id, Jsonb({"text": runbook_text}), runbook_text, runbook_hash),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (runbook_revision, runbook_section))
    return (
        source_section, source_revision, f"doctrine:program6-ready-{token}#source",
        runbook_section, runbook_revision, f"doctrine:runbook#{runbook_key}",
    )


def capture(cur, section_id, revision_id, origin_ref, suffix: str, *, title=None, desired=None, criteria=None):
    return cur.execute(
        """select id,ref,state,version from ops.capture_sourced_work_request(
             %s,%s,%s,%s,%s,%s,%s)""",
        (origin_ref, title or f"Ready plan {suffix}", desired or "Freeze one bounded, reviewable plan",
         Jsonb(criteria or [{"id": "READY-PLAN", "text": "The exact runbook plan is accepted by a human"}]),
         section_id, revision_id, uuid.uuid4()),
    ).fetchone()


def triage(cur, ref: str, version: int, actor: str):
    key = uuid.uuid4()
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return cur.execute(
            "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
            (ref, version, key),
        ).fetchone()
    finally:
        cur.execute("reset session authorization")


def propose(cur, ref: str, version: int, runbook_ref: str, key: uuid.UUID, scope: str = "Inspect evidence and record a bounded result"):
    return cur.execute(
        """select * from ops.propose_sourced_work_request_plan(
             %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ref, version, scope, runbook_ref, Jsonb(["safe:dependency:record-layer"]),
         "safe:recovery:stop-no-change", "safe:observability:ops-run",
         Jsonb({"max_steps": 3, "max_duration_minutes": 15}), key),
    ).fetchone()


def accept(cur, ref: str, version: int, plan_hash: str, key: uuid.UUID, actor: str):
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return cur.execute(
            "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
            (ref, version, plan_hash, key),
        ).fetchone()
    finally:
        cur.execute("reset session authorization")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            ensure_authority_roles(cur)
            assert_wr122_route_is_fenced(cur)
            joe_id = one(cur,
                "select id from actor where slug='joe' and active and kind='human'"
            )[0]
            dell_id = one(cur,
                "select id from actor where slug='dell' and active and kind='human'"
            )[0]
            source_section, source_revision, origin_ref, runbook_section, runbook_revision, runbook_ref = doctrine_fixture(cur, joe_id)

            # 0177 must preserve the 0175 captured -> triaged path.
            set_local_role(cur, "carr_writer")
            request_id, ref, state, captured_version = capture(
                cur, source_section, source_revision, origin_ref, "Dell"
            )
            cur.execute("reset role")
            triaged = triage(cur, ref, captured_version, "dell")
            if triaged[2] != "triaged" or triaged[3] != captured_version + 1 or triaged[5] != "dell":
                raise RuntimeError(f"the prior receipt-backed triage path regressed: {triaged}")
            triaged_version = triaged[3]

            before = cur.execute(
                """select state,version,shape_disposition,shape_fixed_surface_ref,
                          shape_rationale,shape_decided_by_actor_id,shape_decided_at
                     from ops.work_request where id=%s""",
                (request_id,),
            ).fetchone()
            proposal_key = uuid.uuid4()
            set_local_role(cur, "carr_writer")
            proposal = propose(cur, ref, triaged_version, runbook_ref, proposal_key)
            replay = propose(cur, ref, triaged_version, runbook_ref, proposal_key)
            refusal(
                cur,
                """select * from ops.propose_sourced_work_request_plan(
                     %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (ref, triaged_version, "changed scope", runbook_ref,
                 Jsonb(["safe:dependency:record-layer"]), "safe:recovery:stop-no-change",
                 "safe:observability:ops-run", Jsonb({"max_steps": 3, "max_duration_minutes": 15}),
                 proposal_key),
                "changed proposal replay",
            )
            refusal(cur, "insert into ops.sourced_work_request_plan default values", label="raw writer proposal insert")
            refusal(cur, "insert into ops.sourced_work_request_plan_acceptance_receipt default values", label="raw writer acceptance insert")
            cur.execute("reset role")
            if proposal[-1] is not False or replay[:-1] != proposal[:-1] or replay[-1] is not True:
                raise RuntimeError("proposal replay did not return the exact immutable plan")
            # A later proposal is allowed, but accepting the first plan must
            # keep every ready readback bound to the accepted one.
            set_local_role(cur, "carr_writer")
            later_proposal = propose(
                cur, ref, triaged_version, runbook_ref, uuid.uuid4(),
                "A later alternative scope that remains unaccepted",
            )
            cur.execute("reset role")
            if later_proposal[0] == proposal[0] or later_proposal[2] == proposal[2]:
                raise RuntimeError("a materially different proposal did not create a new immutable version")
            after = cur.execute(
                """select state,version,shape_disposition,shape_fixed_surface_ref,
                          shape_rationale,shape_decided_by_actor_id,shape_decided_at
                     from ops.work_request where id=%s""",
                (request_id,),
            ).fetchone()
            if before != after:
                raise RuntimeError(f"plan proposal mutated the Work Request: {before} -> {after}")
            plan_id, plan_ref, plan_hash = proposal[:3]
            if not plan_hash.startswith("sha256:") or proposal[5] != "triaged":
                raise RuntimeError(f"proposal did not return exact triaged plan readback: {proposal}")

            # A correct-looking direct update is still refused without the private receipt.
            set_local_role(cur, "carr_writer")
            refusal(
                cur,
                """update ops.work_request set state='ready',version=version+1,
                     shape_disposition='not_required',
                     shape_fixed_surface_ref=%s,shape_rationale=%s,
                     shape_decided_by_actor_id=%s,shape_decided_at=now(),updated_at=now()
                     where id=%s""",
                (f"sourced-plan:{plan_ref}#{plan_hash}",
                 f"Accepted immutable plan {plan_ref} for {runbook_ref} at {proposal[9]}",
                 dell_id, request_id),
                "direct ready transition",
            )
            cur.execute("reset role")

            acceptance_key = uuid.uuid4()
            accepted = accept(cur, ref, triaged_version, plan_hash, acceptance_key, "dell")
            accepted_replay = accept(cur, ref, triaged_version, plan_hash, acceptance_key, "dell")
            if accepted[2] != "ready" or accepted[3] != triaged_version + 1 or accepted[5] != plan_ref:
                raise RuntimeError(f"Dell authority did not accept the exact plan: {accepted}")
            if accepted[7] != "dell" or accepted[9] != "not_required" or accepted[-1] is not False:
                raise RuntimeError(f"accepted plan lost human or shape readback: {accepted}")
            if accepted_replay[:-1] != accepted[:-1] or accepted_replay[-1] is not True:
                raise RuntimeError("exact acceptance replay did not return the persisted receipt")
            set_local_role(cur, "carr_writer")
            proposal_after_accept = propose(cur, ref, triaged_version, runbook_ref, proposal_key)
            cur.execute("reset role")
            if proposal_after_accept[5] != "triaged" or proposal_after_accept[6] != triaged_version or proposal_after_accept[-1] is not True:
                raise RuntimeError("proposal replay rewrote its historical triaged state/version after acceptance")
            cur.execute("set session authorization carr_authority_joe")
            refusal(
                cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (ref, triaged_version, plan_hash, acceptance_key), "cross-human acceptance replay",
            )
            cur.execute("reset session authorization")
            refusal(
                cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (ref, triaged_version, "sha256:" + "0" * 64, uuid.uuid4()),
                "second acceptance with a different plan",
            )
            if one(cur,
                "select count(*) from ops.sourced_work_request_plan_acceptance_receipt where work_request_id=%s",
                (request_id,),
            )[0] != 1:
                raise RuntimeError("acceptance created more than one receipt")

            set_local_role(cur, "carr_reader")
            card = cur.execute("select * from ops.work_request_card(%s,'carr-internal')", (ref,)).fetchone()
            cur.execute("reset role")
            if not card or card[2] != "ready" or card[11:14] != ("operational", "dell", triaged[6]):
                raise RuntimeError(f"ready card lost durable triage readback: {card}")
            if card[14] != plan_ref or card[15] != plan_hash or card[16] != proposal[10] or card[17] != runbook_ref:
                raise RuntimeError(f"ready card lost exact plan/runbook readback: {card}")
            if card[24] != "dell" or card[25] != accepted[8]:
                raise RuntimeError(f"ready card is not bound to the accepting human/receipt: {card}")
            if card[26] != "not_required" or card[27] != accepted[10]:
                raise RuntimeError(f"ready card lost fixed-surface readback: {card}")
            set_local_role(cur, "carr_writer")
            refusal(cur, "update ops.work_request set title='changed' where id=%s", (request_id,), "post-ready mutation")
            cur.execute("reset role")
            refusal(cur, "update ops.sourced_work_request_plan set scope_summary='changed' where id=%s", (plan_id,), "plan mutation")
            refusal(cur, "delete from ops.sourced_work_request_plan_acceptance_receipt where work_request_id=%s", (request_id,), "receipt deletion")

            # A changed runbook revision invalidates the proposal and demands a new one.
            set_local_role(cur, "carr_writer")
            stale_id, stale_ref, _, stale_captured_version = capture(
                cur, source_section, source_revision, origin_ref, "stale runbook"
            )
            cur.execute("reset role")
            stale_triaged = triage(cur, stale_ref, stale_captured_version, "joe")
            set_local_role(cur, "carr_writer")
            stale_plan = propose(cur, stale_ref, stale_triaged[3], runbook_ref, uuid.uuid4())
            cur.execute("reset role")
            cur.execute("savepoint stale_runbook")
            changed_text = "A changed runbook requires a new plan proposal."
            changed_revision = one(cur,
                """insert into doctrine_revision
                     (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                   values (%s,2,%s,%s,%s,%s,'change runbook for refusal') returning id""",
                (runbook_section, joe_id, Jsonb({"text": changed_text}), changed_text,
                 hashlib.sha256(changed_text.encode()).hexdigest()),
            )[0]
            cur.execute(
                "update doctrine_section set current_revision_id=%s,current_version=2 where id=%s",
                (changed_revision, runbook_section),
            )
            cur.execute("set session authorization carr_authority_joe")
            refusal(
                cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (stale_ref, stale_triaged[3], stale_plan[2], uuid.uuid4()), "stale runbook acceptance",
            )
            cur.execute("reset session authorization")
            cur.execute("rollback to savepoint stale_runbook")

            # A direct writer mutation cannot preserve an old trusted hash by
            # changing the revision payload while leaving content_hash alone.
            cur.execute("savepoint mutated_runbook_payload")
            set_local_role(cur, "carr_writer")
            cur.execute(
                "update doctrine_revision set body=%s,plain_text=%s where id=%s",
                (Jsonb({"text": "mutated without hash"}), "mutated without hash", runbook_revision),
            )
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            refusal(
                cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (stale_ref, stale_triaged[3], stale_plan[2], uuid.uuid4()),
                "runbook payload mutation with preserved hash",
            )
            cur.execute("reset session authorization")
            cur.execute("rollback to savepoint mutated_runbook_payload")

            # The original sourced answer must remain exact/current too.
            cur.execute("savepoint stale_source")
            new_source_text = "A newer source revision invalidates the old request preimage."
            new_source_revision = one(
                cur,
                """insert into doctrine_revision
                     (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                   values (%s,2,%s,%s,%s,%s,'change source for refusal') returning id""",
                (source_section, joe_id, Jsonb({"text": new_source_text}), new_source_text,
                 hashlib.sha256(new_source_text.encode()).hexdigest()),
            )[0]
            cur.execute(
                "update doctrine_section set current_revision_id=%s,current_version=2 where id=%s",
                (new_source_revision, source_section),
            )
            cur.execute("set session authorization carr_authority_joe")
            refusal(
                cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (stale_ref, stale_triaged[3], stale_plan[2], uuid.uuid4()),
                "stale original source acceptance",
            )
            cur.execute("reset session authorization")
            cur.execute("rollback to savepoint stale_source")

            # A prior shape decision or revision cannot be silently overwritten.
            set_local_role(cur, "carr_writer")
            shaped_id, shaped_ref, _, shaped_captured_version = capture(
                cur, source_section, source_revision, origin_ref, "pre-shaped"
            )
            cur.execute("reset role")
            shaped_triaged = triage(cur, shaped_ref, shaped_captured_version, "dell")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute(
                """update ops.work_request set shape_disposition='not_required',
                     shape_fixed_surface_ref='safe:prior-surface',shape_rationale='Prior decision',
                     shape_decided_by_actor_id=%s,shape_decided_at=now() where id=%s""",
                (dell_id, shaped_id),
            )
            cur.execute("alter table ops.work_request enable trigger sourced_work_request_is_immutable")
            set_local_role(cur, "carr_writer")
            refusal(
                cur, """select * from ops.propose_sourced_work_request_plan(
                     %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (shaped_ref, shaped_triaged[3], "scope", runbook_ref, Jsonb([]),
                 "safe:recovery:stop", "safe:observability:run",
                 Jsonb({"max_steps": 1, "max_duration_minutes": 1}), uuid.uuid4()),
                "proposal over prior shape decision",
            )
            cur.execute("reset role")

            set_local_role(cur, "carr_writer")
            revision_id, revision_ref, _, revision_captured_version = capture(
                cur, source_section, source_revision, origin_ref, "shape revision"
            )
            cur.execute("reset role")
            revision_triaged = triage(cur, revision_ref, revision_captured_version, "joe")
            cur.execute(
                """insert into ops.work_shape_revision
                   (work_request_id,work_request_version,version,trinity,hidden_assumption,
                    repo_searches,maintained_repos,archetypes,chosen_key,mind_changing_fact,
                    builder_brief,created_by_actor_id)
                   values (%s,%s,1,%s,'assumption',%s,%s,%s,'fixed','fact',%s,%s)""",
                (revision_id, revision_triaged[3], Jsonb({}), Jsonb([]), Jsonb([]),
                 Jsonb([]), Jsonb({}), joe_id),
            )
            set_local_role(cur, "carr_writer")
            refusal(
                cur, """select * from ops.propose_sourced_work_request_plan(
                     %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (revision_ref, revision_triaged[3], "scope", runbook_ref, Jsonb([]),
                 "safe:recovery:stop", "safe:observability:run",
                 Jsonb({"max_steps": 1, "max_duration_minutes": 1}), uuid.uuid4()),
                "proposal over prior shape revision",
            )
            cur.execute("reset role")

            set_local_role(cur, "carr_writer")
            bounded_id, bounded_ref, _, bounded_version = capture(
                cur, source_section, source_revision, origin_ref, "agent learning",
                title="Build a new governed agent learning system",
                desired="Implement the complete multi-surface capability",
                criteria=[{"id": f"CAP-{i}", "text": f"Capability condition {i} is verified"} for i in range(1, 6)],
            )
            cur.execute("reset role")
            bounded_triaged = triage(cur, bounded_ref, bounded_version, "joe")
            set_local_role(cur, "carr_writer")
            disposition = one(cur,
                "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,'not_required',%s,%s,%s,%s)",
                (bounded_ref, bounded_triaged[3], "safe:fixed-surface", "The implementation is already fixed.", joe_id, uuid.uuid4()))
            plan = one(cur, "select * from ops.propose_sourced_work_request_plan(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (bounded_ref, disposition[3], "Build the capability end to end", runbook_ref,
                 Jsonb([]), "safe:recovery:stop", "safe:observability:record",
                 Jsonb({"max_steps": 20, "max_duration_minutes": 120}), uuid.uuid4()))
            cur.execute("reset role")
            ready = accept(cur, bounded_ref, disposition[3], plan[2], uuid.uuid4(), "joe")
            if ready[2] != "ready" or ready[9] != "not_required":
                raise RuntimeError("bounded plan still requires the retired admission lifecycle")

            cur.execute("select set_config('carr.acting_actor_slug','joe',true), set_config('carr.verified_human_actor_slug','joe',true)")
            set_local_role(cur, "carr_writer")
            amendment_sql = "select ops.propose_ready_plan_amendment(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            amendment_args = (bounded_ref, ready[3], plan[2], "Replace the accepted plan with a bounded successor",
                runbook_ref, Jsonb([]), "safe:recovery:stop", "safe:observability:record",
                Jsonb({"max_steps": 20, "max_duration_minutes": 120}), joe_id, uuid.uuid4())
            successor = one(cur, amendment_sql, amendment_args)[0]
            replayed = one(cur, amendment_sql, amendment_args)[0]
            if not successor.get('ok') or not replayed.get('replayed') or 'build_admission' in successor:
                raise RuntimeError(f"successor plan still requires admission or lost replay: {successor}")
            conflict = one(cur, amendment_sql, (*amendment_args[:3], "Changed scope", *amendment_args[4:]))[0]
            if conflict.get('reason_id') != 'ready_plan_amendment_idempotency_conflict':
                raise RuntimeError(f"changed successor replay was accepted: {conflict}")
            stale = one(cur, amendment_sql, (*amendment_args[:2], 'sha256:'+'0'*64, *amendment_args[3:-1], uuid.uuid4()))[0]
            if stale.get('reason_id') != 'ready_plan_amendment_predecessor_stale':
                raise RuntimeError(f"unbound successor predecessor was accepted: {stale}")
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            accepted_successor = one(cur, "select ops.accept_ready_plan_amendment(%s,%s,%s,%s)",
                (bounded_ref, ready[3], successor['plan']['hash'], uuid.uuid4()))[0]
            cur.execute("reset session authorization")
            if not accepted_successor.get('ok'):
                raise RuntimeError(f"successor acceptance still requires the retired review: {accepted_successor}")
            if one(cur, "select count(*) from pg_proc where pronamespace='ops'::regnamespace and (proname like 'heavy_build_%%' or proname='classify_sourced_work_request_build')")[0]:
                raise RuntimeError("retired callable admission machinery is still present")

            privileges = cur.execute(
                """select
                  has_table_privilege('carr_writer','ops.sourced_work_request_plan','INSERT'),
                  has_table_privilege('carr_authority','ops.sourced_work_request_plan_acceptance_receipt','INSERT'),
                  has_function_privilege('carr_writer','ops.accept_sourced_work_request_plan(text,integer,text,uuid)','EXECUTE'),
                  has_function_privilege('carr_jobs','ops.accept_sourced_work_request_plan(text,integer,text,uuid)','EXECUTE'),
                  has_table_privilege('carr_writer','ops.retired_build_admission_revision','INSERT'),
                  has_table_privilege('carr_authority','ops.retired_build_plan_review','INSERT')"""
            ).fetchone()
            if privileges != (False, False, False, False, False, False):
                raise RuntimeError(f"raw plan/acceptance authority leaked: {privileges}")

        print("PASS: immutable bounded plans and successor acceptance preserve authority and replay without retired admission")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
