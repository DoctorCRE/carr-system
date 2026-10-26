#!/usr/bin/env python3
# doctrine: runbook
# ci: db-gate
"""Rollback-only acceptance gate for Program 6 sourced shape disposition."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"program6-sourced-shape-disposition-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refuse(cur, sql: str, params: tuple, label: str) -> None:
    cur.execute("savepoint sourced_shape_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint sourced_shape_refusal")
        return
    cur.execute("rollback to savepoint sourced_shape_refusal")
    raise RuntimeError(f"{label} was accepted")


def doctrine_fixture(cur, actor_id: uuid.UUID):
    key = uuid.uuid4().hex
    document_id = cur.execute(
        """insert into doctrine_document (slug,title,content_class,visibility,created_by)
             values (%s,'Program 6 sourced shape gate','reference','shared',%s) returning id""",
        (f"program6-sourced-shape-{key}", actor_id),
    ).fetchone()[0]
    section_id = cur.execute(
        """insert into doctrine_section (document_id,section_key,title,ordinal,status,current_version)
             values (%s,'source','Program 6 sourced shape source',10,'active',1) returning id""",
        (document_id,),
    ).fetchone()[0]
    body = "A sourced request records an implementation shape without widening its lifecycle."
    revision_id = cur.execute(
        """insert into doctrine_revision (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
             values (%s,1,%s,%s,%s,%s,'Program 6 sourced shape fixture') returning id""",
        (section_id, actor_id, Jsonb({"text": body}), body, hashlib.sha256(body.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (revision_id, section_id))
    return section_id, revision_id, f"doctrine:program6-sourced-shape-{key}#source"


def capture(cur, section_id, revision_id, origin_ref):
    return cur.execute(
        "select id,ref,state,version from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)",
        (origin_ref, "Sourced shape gate", "Record a shape disposition without changing source provenance",
         Jsonb([{"id": "SHAPE", "text": "The implementation shape is receipted"}]),
         section_id, revision_id, uuid.uuid4()),
    ).fetchone()


def check_unsourced_transitions(cur, joe_id):
    for analysis in (None, "missing", "stale"):
        request_id = cur.execute(
            """insert into ops.work_request
               (ref,title,requester_actor,state,version,shape_disposition,shape_rationale,
                shape_decided_by_actor_id,shape_decided_at)
               values (%s,'Shape retirement fixture','joe','captured',2,%s,%s,%s,%s) returning id""",
            (f"WR-SHAPE-{uuid.uuid4().hex}", "required" if analysis else None,
             "Historical analysis" if analysis else None, joe_id if analysis else None,
             "2026-01-01T00:00:00Z" if analysis else None),
        ).fetchone()[0]
        if analysis == "stale":
            cur.execute(
                """insert into ops.work_shape_revision
                   (work_request_id,work_request_version,version,trinity,hidden_assumption,repo_searches,
                    maintained_repos,archetypes,chosen_key,mind_changing_fact,builder_brief,created_by_actor_id)
                   values (%s,1,1,'{}','historical','[]','[]','[]','stored','stored','{}',%s)""",
                (request_id, joe_id),
            )
        cur.execute("update ops.work_request set state='ready',version=version+1 where id=%s", (request_id,))
        if analysis:
            refuse(cur, "update ops.work_request set state='claimed',shape_rationale='rewritten' where id=%s",
                   (request_id,), "historical shape rewrite during claim")
        cur.execute("update ops.work_request set state='claimed',version=version+1 where id=%s", (request_id,))
        if analysis:
            refuse(cur, "update ops.work_request set shape_rationale='rewritten' where id=%s",
                   (request_id,), "historical shape rewrite after claim")
        ready = cur.execute(
            "update ops.work_request set state='ready',version=version+1 where id=%s returning state,shape_disposition",
            (request_id,),
        ).fetchone()
        if ready != ("ready", "required" if analysis else None):
            raise RuntimeError(f"shape history prevented or changed lifecycle transition: {ready}")
    refuse(cur,
           "insert into ops.work_request(ref,title,requester_actor,state) values (%s,'Invalid direct start','joe','claimed')",
           (f"WR-SHAPE-{uuid.uuid4().hex}",), "direct implementation insert")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            joe = cur.execute("select id from actor where slug='joe' and active and kind='human'").fetchone()
            if not joe:
                return fail("seeded active human actor joe is required")
            joe_id = joe[0]
            check_unsourced_transitions(cur, joe_id)
            cur.execute("""do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
            end $$""")
            cur.execute("grant carr_authority to carr_authority_joe")
            grant_settable_runtime_roles(cur, "carr_authority_joe", "carr_writer", "carr_reader", "carr_jobs")
            section_id, revision_id, origin_ref = doctrine_fixture(cur, joe_id)

            set_local_role(cur, "carr_writer")
            request_id, ref, state, version = capture(cur, section_id, revision_id, origin_ref)
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            triaged = cur.execute(
                "select * from ops.triage_sourced_work_request(%s,%s,%s,%s)",
                (ref, version, "operational", uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if not triaged or triaged[2:4] != ("triaged", version + 1):
                return fail(f"fixture did not reach sourced triaged state: {triaged}")
            triaged_version = triaged[3]

            private_privileges = cur.execute(
                """select has_table_privilege('carr_writer','ops.sourced_work_request_shape_disposition_receipt','insert'),
                          has_table_privilege('carr_authority','ops.sourced_work_request_shape_disposition_receipt','insert'),
                          has_table_privilege('carr_jobs','ops.sourced_work_request_shape_disposition_receipt','insert')"""
            ).fetchone()
            if private_privileges != (False, False, False):
                return fail(f"non-owner role can forge a sourced shape receipt: {private_privileges}")

            set_local_role(cur, "carr_writer")
            refuse(cur, "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,%s,%s,%s,%s,%s)",
                   (ref, triaged_version - 1, "required", None, "The surface remains open.", joe_id, uuid.uuid4()),
                   "stale sourced shape disposition")
            refuse(cur,
                   """update ops.work_request set shape_disposition='required',shape_rationale='forged',
                         shape_decided_by_actor_id=%s,shape_decided_at=now(),version=version+1,updated_at=now() where id=%s""",
                   (joe_id, request_id), "direct sourced shape mutation without receipt")
            key = uuid.uuid4()
            shaped = cur.execute(
                "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,%s,%s,%s,%s,%s)",
                (ref, triaged_version, "required", None, "The implementation surface remains open.", joe_id, key),
            ).fetchone()
            if not shaped or shaped[1:6] != (ref, "triaged", triaged_version + 1, "required", None) or shaped[-1] is not False:
                return fail(f"exact sourced shape disposition was not persisted: {shaped}")
            replay = cur.execute(
                "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,%s,%s,%s,%s,%s)",
                (ref, triaged_version, "required", None, "The implementation surface remains open.", joe_id, key),
            ).fetchone()
            if replay[:-1] != shaped[:-1] or replay[-1] is not True:
                return fail("exact sourced shape disposition replay did not return the persisted receipt")
            refuse(cur, "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,%s,%s,%s,%s,%s)",
                   (ref, triaged_version, "not_required", "forged:surface", "changed", joe_id, key),
                   "changed idempotency payload")
            refuse(cur,
                   "update ops.sourced_work_request_shape_disposition_receipt set rationale='tampered' where work_request_id=%s",
                   (request_id,), "shape receipt tamper")
            refuse(cur,
                   """update ops.work_request set shape_disposition='not_required',shape_fixed_surface_ref='forged:surface',
                         shape_rationale='tampered',version=version+1,updated_at=now() where id=%s""",
                   (request_id,), "sourced disposition tamper")

            runbook = cur.execute(
                """select s.section_key from doctrine_document d join doctrine_section s on s.document_id=d.id
                     join doctrine_revision r on r.id=s.current_revision_id and r.section_id=s.id
                    where d.slug='runbook' and d.visibility='shared' and s.status='active'
                    order by s.ordinal limit 1"""
            ).fetchone()
            if not runbook:
                # The tracked schema intentionally excludes doctrine business
                # content.  Seed only the minimal current runbook relation in
                # this rollback-only database so the strict source-current
                # acceptance path, rather than a mocked plan row, is tested.
                runbook_document = cur.execute(
                    """insert into doctrine_document (slug,title,content_class,visibility,created_by)
                         values ('runbook','Program 6 gate runbook','reference','shared',%s) returning id""",
                    (joe_id,),
                ).fetchone()[0]
                runbook_section = cur.execute(
                    """insert into doctrine_section (document_id,section_key,title,ordinal,status,current_version)
                         values (%s,'shape-acceptance','Program 6 shape acceptance',10,'active',1) returning id""",
                    (runbook_document,),
                ).fetchone()[0]
                runbook_body = "Historical shape evidence must survive ready-plan acceptance."
                runbook_revision = cur.execute(
                    """insert into doctrine_revision (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                         values (%s,1,%s,%s,%s,%s,'Program 6 runbook fixture') returning id""",
                    (runbook_section, joe_id, Jsonb({"text": runbook_body}), runbook_body,
                     hashlib.sha256(runbook_body.encode()).hexdigest()),
                ).fetchone()[0]
                cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (runbook_revision, runbook_section))
                runbook = ("shape-acceptance",)
            plan = cur.execute(
                "select * from ops.propose_sourced_work_request_plan(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ref, shaped[3], "Preserve historical shape without requiring an analysis revision.",
                 f"doctrine:runbook#{runbook[0]}", Jsonb([]), "safe:recovery:shape", "safe:observability:shape",
                 Jsonb({"max_steps": 1, "max_duration_minutes": 1}), uuid.uuid4()),
            ).fetchone()
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            accepted = cur.execute(
                "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (ref, shaped[3], plan[2], uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if not accepted or accepted[2:4] != ("ready", shaped[3] + 1) or accepted[9] != "required" or accepted[10] is not None:
                return fail(f"ready-plan acceptance overwrote required shape: {accepted}")
            set_local_role(cur, "carr_writer")
            feedback = cur.execute(
                "select * from ops.propose_sourced_work_request_outcome_feedback(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ref, accepted[3], plan[2], Jsonb([{"id": "SHAPE", "result": "met"}]),
                 Jsonb(["safe:fixture:shape-retirement"]), "none", "Historical shape remains inspectable.",
                 1, "mcp", False, 0, uuid.uuid4()),
            ).fetchone()
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            outcome = cur.execute(
                "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
                (ref, accepted[3], feedback[2], uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if outcome[9] != "criteria_met":
                return fail(f"shape history blocked the sourced outcome: {outcome}")
            refuse(cur,
                   "update ops.sourced_work_request_shape_disposition_receipt set rationale='rewritten' where work_request_id=%s",
                   (request_id,), "historical shape receipt rewrite after outcome")
        print("PASS: readiness and implementation ignore shape requirements; sourced plans/outcomes preserve receipt-backed history")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
