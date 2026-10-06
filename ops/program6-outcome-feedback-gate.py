#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate for Program 6 sourced outcome feedback."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"program6-outcome-feedback-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, sql: str, params: tuple = ()):
    row = cur.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("required gate fixture row was not returned")
    return row


def refusal(cur, sql: str, params: tuple = (), label: str = "unsafe operation") -> None:
    cur.execute("savepoint program6_outcome_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint program6_outcome_refusal")
        return
    cur.execute("rollback to savepoint program6_outcome_refusal")
    raise RuntimeError(f"{label} was accepted")


def authority_roles(cur) -> None:
    cur.execute("""do $$ begin
      if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
        create role carr_authority_joe login;
      end if;
      if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
        create role carr_authority_dell login;
      end if;
    end $$""")
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


def fixture(cur, actor_id: uuid.UUID):
    token = uuid.uuid4().hex
    source_doc = one(cur, """insert into doctrine_document
      (slug,title,content_class,visibility,created_by)
      values (%s,'Outcome feedback source','reference','shared',%s) returning id""",
      (f"program6-outcome-{token}", actor_id))[0]
    source_section = one(cur, """insert into doctrine_section
      (document_id,section_key,title,ordinal,status,current_version)
      values (%s,'source','Outcome feedback source',1,'active',1) returning id""", (source_doc,))[0]
    source_text = "An observed outcome is evidence, not authority to execute another step."
    source_rev = one(cur, """insert into doctrine_revision
      (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
      values (%s,1,%s,%s,%s,%s,'Program 6 feedback source fixture') returning id""",
      (source_section, actor_id, Jsonb({"text": source_text}), source_text,
       hashlib.sha256(source_text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (source_rev, source_section))
    runbook_doc = cur.execute("select id from doctrine_document where slug='runbook' and visibility='shared'").fetchone()
    if not runbook_doc:
        runbook_doc = one(cur, """insert into doctrine_document
          (slug,title,content_class,visibility,created_by)
          values ('runbook','Runbook','reference','shared',%s) returning id""", (actor_id,))
    runbook_key = f"program6-outcome-{token}"
    runbook_section = one(cur, """insert into doctrine_section
      (document_id,section_key,title,ordinal,status,current_version)
      values (%s,%s,'Outcome feedback runbook',999,'active',1) returning id""",
      (runbook_doc[0], runbook_key))[0]
    runbook_text = "Observe the named criterion, preserve evidence references, and stop."
    runbook_rev = one(cur, """insert into doctrine_revision
      (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
      values (%s,1,%s,%s,%s,%s,'Program 6 feedback runbook fixture') returning id""",
      (runbook_section, actor_id, Jsonb({"text": runbook_text}), runbook_text,
       hashlib.sha256(runbook_text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (runbook_rev, runbook_section))
    return source_section, source_rev, f"doctrine:program6-outcome-{token}#source", f"doctrine:runbook#{runbook_key}"


def as_authority(cur, actor: str, sql: str, params: tuple):
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return cur.execute(sql, params).fetchone()
    finally:
        cur.execute("reset session authorization")


def propose(cur, ref: str, version: int, plan_hash: str, key: uuid.UUID, outcome: str = "met", summary: str = "Observed the bounded result"):
    result = Jsonb([{"id": "OBSERVED", "result": outcome}])
    return cur.execute("""select * from ops.propose_sourced_work_request_outcome_feedback(
      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (ref, version, plan_hash, result, Jsonb(["safe:evidence:observed-result"]),
       "none" if outcome == "met" else "criterion_not_met", summary,
       12, "mcp", False, 0, key)).fetchone()


def accept(cur, ref: str, version: int, feedback_hash: str, key: uuid.UUID, actor: str):
    return as_authority(cur, actor,
      "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
      (ref, version, feedback_hash, key))


def pending(cur, ref: str, tenant: str = "carr-internal"):
    return cur.execute(
      "select * from ops.pending_sourced_work_request_outcome_feedback(%s,%s)",
      (ref, tenant)).fetchall()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            authority_roles(cur)
            assert_wr122_route_is_fenced(cur)
            joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
            source_section, source_rev, origin_ref, runbook_ref = fixture(cur, joe_id)
            set_local_role(cur, "carr_writer")
            request_id, ref, _, captured_version, *_ = one(cur, """select * from ops.capture_sourced_work_request(
              %s,%s,%s,%s,%s,%s,%s)""", (origin_ref, "Outcome feedback", "Observe one result",
              Jsonb([{"id": "OBSERVED", "text": "The stated outcome has an evidence reference"}]),
              source_section, source_rev, uuid.uuid4()))
            cur.execute("reset role")
            triaged = as_authority(cur, "dell", "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)", (ref, captured_version, uuid.uuid4()))
            triaged_version = triaged[3]
            set_local_role(cur, "carr_writer")
            plan = one(cur, """select * from ops.propose_sourced_work_request_plan(
              %s,%s,%s,%s,%s,%s,%s,%s,%s)""", (ref, triaged_version, "Observe one bounded result",
              runbook_ref, Jsonb([]), "safe:recovery:stop", "safe:observability:record",
              Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4()))
            cur.execute("reset role")
            ready = as_authority(cur, "dell", "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
              (ref, triaged_version, plan[2], uuid.uuid4()))
            ready_version, plan_hash = ready[3], plan[2]
            if ready[2] != "ready":
                raise RuntimeError(f"ready plan fixture failed: {ready}")

            # Routine writer may append a proposal but cannot write private rows or move work.
            proposal_a_key = uuid.uuid4()
            set_local_role(cur, "carr_writer")
            proposal_a = propose(cur, ref, ready_version, plan_hash, proposal_a_key)
            replay_a = propose(cur, ref, ready_version, plan_hash, proposal_a_key)
            refusal(cur, "insert into ops.sourced_work_request_outcome_feedback default values", label="raw writer feedback proposal insert")
            refusal(cur, "insert into ops.sourced_work_request_outcome_feedback_acceptance_receipt default values", label="raw writer feedback receipt insert")
            refusal(cur, "update ops.work_request set state='claimed' where id=%s", (request_id,), "writer lifecycle transition")
            refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, ready_version, plan_hash, Jsonb([]), Jsonb(["safe:evidence:observed-result"]), "none",
               "bad criterion set", 12, "mcp", False, 0, uuid.uuid4()), "partial criterion result set")
            refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, ready_version, plan_hash, Jsonb([{"id":"OBSERVED","result":"met"}]),
               Jsonb(["safe:evidence:observed-result"]), "system_error", "inconsistent blocker",
               12, "mcp", False, 0, uuid.uuid4()), "inconsistent blocker")
            cur.execute("reset role")
            if proposal_a[-1] is not False or replay_a[:-1] != proposal_a[:-1] or replay_a[-1] is not True:
                raise RuntimeError("proposal did not preserve exact idempotent readback")
            if proposal_a[5:7] != ("ready", ready_version):
                raise RuntimeError(f"proposal changed or misreported Work Request state: {proposal_a}")

            card = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            if card[28] is not None or card[29] != [] or card[30] != 0:
                raise RuntimeError(f"pending outcome proposal leaked into card: {card[28:31]}")
            pending_a = pending(cur, ref)
            if (len(pending_a) != 1
                    or pending_a[0][0:6] != (ref, "ready", ready_version, plan[1], plan_hash, proposal_a[1])
                    or pending_a[0][6] != proposal_a[2]
                    or pending_a[0][-1] != "pending_human_acceptance"):
                raise RuntimeError(f"pending outcome proposal readback is not exact: {pending_a}")
            if pending(cur, ref) != pending_a:
                raise RuntimeError("pending outcome proposal readback is not stable after reload")
            if pending(cur, ref, "other-tenant") != []:
                raise RuntimeError("pending outcome feedback crossed its tenant boundary")
            # The ready-plan accepter may also accept outcome feedback: no invented two-human gate.
            accepted_a_key = uuid.uuid4()
            accepted_a = accept(cur, ref, ready_version, proposal_a[2], accepted_a_key, "dell")
            replay_accepted_a = accept(cur, ref, ready_version, proposal_a[2], accepted_a_key, "dell")
            if accepted_a[2:4] != ("ready", ready_version) or accepted_a[-1] is not False:
                raise RuntimeError(f"acceptance advanced a ready Work Request: {accepted_a}")
            if replay_accepted_a[:-1] != accepted_a[:-1] or replay_accepted_a[-1] is not True:
                raise RuntimeError("acceptance did not preserve exact idempotent readback")
            if pending(cur, ref) != []:
                raise RuntimeError("accepted outcome feedback remained in the pending readback")
            refusal(cur, "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
              (ref, ready_version, proposal_a[2], accepted_a_key), "cross-human acceptance replay")

            set_local_role(cur, "carr_writer")
            proposal_b = propose(cur, ref, ready_version, plan_hash, uuid.uuid4(), summary="A later observed trial")
            cur.execute("reset role")
            card_pending_b = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            if card_pending_b[28]["feedback_ref"] != proposal_a[1] or card_pending_b[29] != [card_pending_b[28]] or card_pending_b[30] != 1:
                raise RuntimeError(f"pending B leaked or accepted history is wrong: {card_pending_b[28:31]}")
            pending_b = pending(cur, ref)
            if len(pending_b) != 1 or pending_b[0][5:7] != (proposal_b[1], proposal_b[2]):
                raise RuntimeError(f"latest pending outcome feedback is not B: {pending_b}")
            accepted_b = accept(cur, ref, ready_version, proposal_b[2], uuid.uuid4(), "dell")
            if pending(cur, ref) != []:
                raise RuntimeError("accepted B remained in the pending readback")
            card_b = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            history = card_b[29]
            if card_b[28]["feedback_ref"] != proposal_b[1] or card_b[30] != 2 or [x["feedback_ref"] for x in history] != [proposal_a[1], proposal_b[1]]:
                raise RuntimeError(f"accepted history is not deterministic A then B: {card_b[28:31]}")

            # A reload must recover the newest of more than one still-pending
            # proposal, while the card remains exclusively about accepted facts.
            set_local_role(cur, "carr_writer")
            proposal_c = propose(cur, ref, ready_version, plan_hash, uuid.uuid4(), summary="An earlier pending observation")
            proposal_d = propose(cur, ref, ready_version, plan_hash, uuid.uuid4(), summary="The latest pending observation")
            cur.execute("reset role")
            latest_pending = pending(cur, ref)
            if len(latest_pending) != 1 or latest_pending[0][5:7] != (proposal_d[1], proposal_d[2]):
                raise RuntimeError(f"pending readback did not select its latest proposal: {latest_pending}")
            card_with_pending = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            if card_with_pending[28:31] != card_b[28:31]:
                raise RuntimeError("pending proposals altered accepted-only card history")
            accept(cur, ref, ready_version, proposal_d[2], uuid.uuid4(), "dell")
            older_pending = pending(cur, ref)
            if len(older_pending) != 1 or older_pending[0][5:7] != (proposal_c[1], proposal_c[2]):
                raise RuntimeError(f"pending readback did not recover the remaining exact proposal: {older_pending}")
            accept(cur, ref, ready_version, proposal_c[2], uuid.uuid4(), "dell")
            if pending(cur, ref) != []:
                raise RuntimeError("no pending proposal should remain after both acceptances")

            # Replays are facts about the original accepted proposal/receipt, not a claim that a later lifecycle remains ready.
            cur.execute("savepoint historical_replay")
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='claimed',version=version+1 where id=%s", (request_id,))
            cur.execute("alter table ops.work_request enable trigger sourced_work_request_is_immutable")
            historical_proposal = propose(cur, ref, ready_version, plan_hash, proposal_a_key)
            historical_acceptance = accept(cur, ref, ready_version, proposal_a[2], accepted_a_key, "dell")
            refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, ready_version, plan_hash, Jsonb([{"id":"OBSERVED","result":"met"}]),
               Jsonb(["safe:evidence:observed-result"]), "none", "changed replay", 12, "mcp", False, 0, proposal_a_key),
              "changed historical proposal replay")
            if historical_proposal[5:7] != ("ready", ready_version) or historical_acceptance[2:4] != ("ready", ready_version):
                raise RuntimeError("historical replay did not preserve the original ready version")
            cur.execute("rollback to savepoint historical_replay")

            # A plan accepted under a REQUIRED Work Shape must still take outcome
            # feedback. Regression for incident INC-20260827-04: 0306's accept
            # stamps the receipt's shape_fixed_surface_ref while its
            # preserve-shape branch leaves the work request's field null, and
            # the pre-0333 guards required the two to be equal — refusing every
            # shape-governed closeout.
            source_section2, source_rev2, origin_ref2, runbook_ref2 = fixture(cur, joe_id)
            set_local_role(cur, "carr_writer")
            request2_id, ref2, _, captured2_version, *_ = one(cur, """select * from ops.capture_sourced_work_request(
              %s,%s,%s,%s,%s,%s,%s)""", (origin_ref2, "Shaped outcome feedback", "Observe one shaped result",
              Jsonb([{"id": "OBSERVED", "text": "The stated outcome has an evidence reference"}]),
              source_section2, source_rev2, uuid.uuid4()))
            cur.execute("reset role")
            triaged2 = as_authority(cur, "dell", "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)", (ref2, captured2_version, uuid.uuid4()))
            set_local_role(cur, "carr_writer")
            shaped = one(cur, """select * from ops.set_sourced_work_request_shape_disposition(
              %s,%s,'required',null,'Shape analysis required by the fixture',%s,%s)""",
              (ref2, triaged2[3], joe_id, uuid.uuid4()))
            shaped_version = shaped[3]
            cur.execute("""insert into ops.work_shape_revision
              (work_request_id, work_request_version, version, trinity, hidden_assumption,
               repo_searches, maintained_repos, archetypes, chosen_key, mind_changing_fact,
               builder_brief, source_url, created_by_actor_id)
              values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,null,%s)""",
              (request2_id, shaped_version,
               Jsonb({"workflow_trigger": "fixture", "output_user": "fixture", "runtime": "fixture"}),
               "The fixture assumes nothing hidden",
               Jsonb(["search one", "search two"]),
               Jsonb([{"url": f"https://github.com/example/repo-{n}", "maintenance_evidence": "active"} for n in range(5)]),
               Jsonb([{"key": k, "label": k, "core_assumption": f"assumption {k}",
                       "scores": {"trinity_fit": 3, "useful_v1_effort": 3, "extension_effort": 3}}
                      for k in ("a", "b", "c")]),
               "a", "A measured fact would change this choice",
               Jsonb({"chosen_shape": "fixture", "repo_url": "https://github.com/example/repo-0",
                      "trinity": {"workflow_trigger": "fixture", "output_user": "fixture", "runtime": "fixture"},
                      "must_have_integrations": ["one"], "v1_non_goals": ["none"],
                      "text": "fixture brief"}),
               joe_id))
            plan2 = one(cur, """select * from ops.propose_sourced_work_request_plan(
              %s,%s,%s,%s,%s,%s,%s,%s,%s)""", (ref2, shaped_version, "Observe one shaped bounded result",
              runbook_ref2, Jsonb([]), "safe:recovery:stop", "safe:observability:record",
              Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4()))
            cur.execute("reset role")
            ready2 = as_authority(cur, "dell", "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
              (ref2, shaped_version, plan2[2], uuid.uuid4()))
            ready2_version, plan2_hash = ready2[3], plan2[2]
            if ready2[2] != "ready":
                raise RuntimeError(f"shape-required ready plan fixture failed: {ready2}")
            shape_state = one(cur, "select shape_disposition, shape_fixed_surface_ref from ops.work_request where id=%s", (request2_id,))
            if shape_state != ("required", None):
                raise RuntimeError(f"preserve-shape acceptance did not keep the shape state: {shape_state}")
            binding = cur.execute("""select sb.disposition, sb.fixed_surface_ref
              from ops.sourced_work_request_plan_shape_binding_receipt sb
              join ops.sourced_work_request_plan_acceptance_receipt ar on ar.id=sb.plan_acceptance_receipt_id
             where ar.work_request_id=%s""", (request2_id,)).fetchone()
            if binding != ("required", None):
                raise RuntimeError(f"acceptance did not freeze the shape state in a binding receipt: {binding}")
            set_local_role(cur, "carr_writer")
            shaped_proposal = propose(cur, ref2, ready2_version, plan2_hash, uuid.uuid4(), summary="Shaped observed result")
            cur.execute("reset role")
            if shaped_proposal[5:7] != ("ready", ready2_version):
                raise RuntimeError(f"shape-required outcome proposal was refused or misreported: {shaped_proposal}")
            shaped_accepted = accept(cur, ref2, ready2_version, shaped_proposal[2], uuid.uuid4(), "dell")
            if shaped_accepted[2:4] != ("ready", ready2_version):
                raise RuntimeError(f"shape-required outcome acceptance failed: {shaped_accepted}")
            if pending(cur, ref2) != []:
                raise RuntimeError("accepted shape-required outcome feedback remained pending")
    except Exception as exc:
        return fail(str(exc))
    print("program6-outcome-feedback-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
