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


def heavy_contract() -> dict:
    def evidence(suffix: str, source_class: str) -> dict:
        return {
            "source_ref": f"safe:research:{suffix}",
            "source_class": source_class,
            "locator": f"https://example.com/{suffix}",
            "observed_at": "2026-08-25T12:00:00Z",
            "content_digest": "sha256:" + "e" * 64,
            "finding": f"Verified {source_class} evidence for the heavy-build acceptance fixture.",
        }

    return {
        "builder_session_ref": "session:builder:hermes-memory-negative",
        "research_manifest": {
            "primary_sources": [evidence("primary", "primary_source")],
            "maintained_repositories": [
                evidence("repo-one", "maintained_repository"),
                evidence("repo-two", "maintained_repository"),
            ],
            "practitioner_evidence": [evidence("practitioner", "practitioner_evidence")],
            "current_baseline": [evidence("baseline", "current_baseline")],
            "failure_modes": [evidence("failure", "failure_mode")],
            "unresolved_contradictions": [],
            "conclusion": "The evidence supports the chosen architecture and preserves the named falsifier.",
        },
        "master_plan": {
            "product_goal": "Ship the complete governed agent-learning capability rather than only its prerequisite executor repairs.",
            "non_goals": ["Do not substitute Engineering Passport repairs for the requested product."],
            "architecture": ["Heavy-build admission layer", "Bounded execution layer", "Independent verification layer"],
            "authority_boundaries": ["Models supply evidence while the database derives admission and humans retain acceptance."],
            "dependency_dag": [
                {"step_ref": "step:admission", "depends_on": []},
                {"step_ref": "step:execution", "depends_on": ["step:admission"]},
            ],
            "planned_checks": [{
                "artifact": "heavy-build readiness transition",
                "comparator": "typed research, plan, shape, and review receipts",
                "failure_condition": "any required receipt is absent or the latest review failed",
            }],
            "baseline_comparison": "Replay the tour-packet task as the positive path and the Hermes-memory task as the refusal path.",
            "release_strategy": "Release behind the existing human ready-plan acceptance boundary.",
            "rollback_strategy": "Remove the new gate and functions while leaving pre-existing accepted plans untouched.",
            "observability_strategy": "Return classifier reasons and immutable admission and review hashes on every path.",
            "fully_shipped_definition": "No heavy request reaches ready without current shape, research, complete-plan, and review receipts.",
            "prerequisite_policy": "A discovered prerequisite remains a dependency and never replaces the parent product plan.",
        },
    }


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

            # Heavy work is recognized from the actual request and plan, not a
            # caller checkbox. It needs current Work Shape, typed research and
            # complete-plan admission, then the LATEST fresh-context review.
            set_local_role(cur, "carr_writer")
            heavy_id, heavy_ref, _, heavy_captured_version = capture(
                cur, source_section, source_revision, origin_ref, "heavy agent learning",
                title="Build a new governed agent learning system",
                desired="Extend the CARR memory kernel into a complete agent-learning capability",
                criteria=[{"id": f"HEAVY-{i}", "text": f"Heavy-build acceptance condition {i} is independently verified"}
                          for i in range(1, 6)],
            )
            cur.execute("reset role")
            heavy_triaged = triage(cur, heavy_ref, heavy_captured_version, "joe")
            set_local_role(cur, "carr_writer")
            shaped = one(cur,
                "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,'required',null,%s,%s,%s)",
                (heavy_ref, heavy_triaged[3], "Multiple implementation surfaces remain viable.", joe_id, uuid.uuid4()),
            )
            cur.execute("reset role")
            shaped_version = shaped[3]
            cur.execute(
                """insert into ops.work_shape_revision
                   (work_request_id,work_request_version,version,trinity,hidden_assumption,
                    repo_searches,maintained_repos,archetypes,chosen_key,mind_changing_fact,
                    builder_brief,created_by_actor_id)
                   values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (heavy_id, shaped_version,
                 Jsonb({"workflow_trigger": "heavy request", "runtime": "CARR", "output_user": "Joe and Dell"}),
                 "The prerequisite framework may be mistaken for the requested product.",
                 Jsonb(["agent memory architecture", "governed learning engine"]),
                 Jsonb([{"url": f"https://github.com/example/repo-{i}", "maintenance_evidence": "current release"} for i in range(5)]),
                 Jsonb([{"key": "extend", "core_assumption": "extend current kernel"},
                        {"key": "replace", "core_assumption": "replace current kernel"},
                        {"key": "hybrid", "core_assumption": "bind specialized layers"}]),
                 "extend", "A current kernel limitation would falsify extension.",
                 Jsonb({"chosen_shape": "extend", "text": "Build the complete capability from the accepted evidence."}), joe_id),
            )
            heavy_proposal_key = uuid.uuid4()
            set_local_role(cur, "carr_writer")
            heavy_plan = one(cur,
                """select * from ops.propose_sourced_work_request_plan(
                     %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (heavy_ref, shaped_version, "Build a new agent learning system end to end", runbook_ref,
                 Jsonb(["safe:dependency:memory-kernel"]), "safe:recovery:stop-no-change",
                 "safe:observability:heavy-build-receipts", Jsonb({"max_steps": 6, "max_duration_minutes": 90}),
                 heavy_proposal_key),
            )
            classification = one(cur,
                "select * from ops.classify_sourced_work_request_build(%s,%s,%s,%s,%s)",
                (heavy_ref, shaped_version, "Build a new agent learning system end to end",
                 Jsonb(["safe:dependency:memory-kernel"]), Jsonb({"max_steps": 6, "max_duration_minutes": 90})),
            )
            cur.execute("reset role")
            if classification[2] != "heavy" or not classification[3] or classification[5] is not True:
                raise RuntimeError(f"server did not classify the shaped heavy fixture: {classification}")

            cur.execute("set session authorization carr_authority_joe")
            refusal(cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                    (heavy_ref, shaped_version, heavy_plan[2], uuid.uuid4()),
                    "heavy acceptance without research/master-plan admission")
            cur.execute("reset session authorization")

            set_local_role(cur, "carr_writer")
            admission = one(cur,
                """select * from ops.record_sourced_heavy_build_admission(
                     %s,%s,%s,%s,%s,%s,%s)""",
                (heavy_plan[0], heavy_ref, shaped_version, Jsonb(classification[3]),
                 Jsonb(heavy_contract()), joe_id, heavy_proposal_key),
            )
            cur.execute("reset role")
            if admission[6] != "heavy" or admission[8] != "session:builder:hermes-memory-negative":
                raise RuntimeError(f"heavy admission lost classifier or builder context: {admission}")

            cur.execute("set session authorization carr_authority_joe")
            refusal(cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                    (heavy_ref, shaped_version, heavy_plan[2], uuid.uuid4()),
                    "heavy acceptance without independent review")
            cur.execute("reset session authorization")

            set_local_role(cur, "carr_writer")
            refusal(cur,
                """select * from ops.review_sourced_heavy_build_plan(
                     %s,%s,%s,%s,'pass',%s,%s,%s,%s,%s)""",
                (heavy_ref, heavy_plan[2], admission[5], joe_id,
                 "session:builder:hermes-memory-negative", "Builder tried to review its own planning context.",
                 Jsonb(["safe:review:self"]), Jsonb([]), uuid.uuid4()),
                "same-context heavy review")
            failed_review = one(cur,
                """select * from ops.review_sourced_heavy_build_plan(
                     %s,%s,%s,%s,'fail',%s,%s,%s,%s,%s)""",
                (heavy_ref, heavy_plan[2], admission[5], joe_id,
                 "session:reviewer:fresh-sol-one", "Fresh review found that one required comparison was absent.",
                 Jsonb(["safe:review:heavy-negative"]), Jsonb(["The baseline comparison is not yet proven."]), uuid.uuid4()),
            )
            cur.execute("reset role")
            if failed_review[7] != "fail":
                raise RuntimeError(f"failed heavy review was not durable: {failed_review}")
            cur.execute("set session authorization carr_authority_joe")
            refusal(cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                    (heavy_ref, shaped_version, heavy_plan[2], uuid.uuid4()),
                    "heavy acceptance after latest failed review")
            cur.execute("reset session authorization")

            set_local_role(cur, "carr_writer")
            passed_review = one(cur,
                """select * from ops.review_sourced_heavy_build_plan(
                     %s,%s,%s,%s,'pass',%s,%s,%s,%s,%s)""",
                (heavy_ref, heavy_plan[2], admission[5], joe_id,
                 "session:reviewer:fresh-sol-two", "Fresh review checked the actual manifest and plan against every acceptance criterion.",
                 Jsonb(["safe:review:heavy-positive"]), Jsonb([]), uuid.uuid4()),
            )
            cur.execute("reset role")
            if passed_review[7] != "pass":
                raise RuntimeError(f"passing heavy review was not durable: {passed_review}")
            heavy_accepted = accept(cur, heavy_ref, shaped_version, heavy_plan[2], uuid.uuid4(), "joe")
            if heavy_accepted[2] != "ready" or heavy_accepted[9] != "required":
                raise RuntimeError(f"fully admitted heavy plan did not preserve Work Shape into ready: {heavy_accepted}")

            privileges = cur.execute(
                """select
                  has_table_privilege('carr_writer','ops.sourced_work_request_plan','INSERT'),
                  has_table_privilege('carr_authority','ops.sourced_work_request_plan_acceptance_receipt','INSERT'),
                  has_function_privilege('carr_writer','ops.accept_sourced_work_request_plan(text,integer,text,uuid)','EXECUTE'),
                  has_function_privilege('carr_jobs','ops.accept_sourced_work_request_plan(text,integer,text,uuid)','EXECUTE'),
                  has_table_privilege('carr_writer','ops.heavy_build_admission_revision','INSERT'),
                  has_table_privilege('carr_authority','ops.heavy_build_plan_review','INSERT')"""
            ).fetchone()
            if privileges != (False, False, False, False, False, False):
                raise RuntimeError(f"raw plan/acceptance authority leaked: {privileges}")

        print("PASS: Program 6 plans are immutable; heavy plans require shape, research/master-plan admission, and fresh passing review before human acceptance")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
