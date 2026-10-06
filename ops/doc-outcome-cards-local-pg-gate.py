#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only PostgreSQL acceptance for the B09 outcome-card projection."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"doc-outcome-cards-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"fixture row was not returned: {query[:100]}")
    return row


def doctrine(cur, actor_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, str]:
    token = uuid.uuid4().hex
    document = one(cur, """insert into doctrine_document(slug,title,content_class,visibility,created_by)
                          values (%s,'B09 fixture','reference','shared',%s) returning id""",
                   (f"b09-{token}", actor_id))[0]
    section = one(cur, """insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version)
                         values (%s,'fixture','B09 fixture',1,'active',1) returning id""", (document,))[0]
    text = "B09 outcome cards retain the source-bound Work Request evidence."
    revision = one(cur, """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                          values (%s,1,%s,%s,%s,%s,'B09 fixture') returning id""",
                   (section, actor_id, Jsonb({"text": text}), text, hashlib.sha256(text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (revision, section))
    return section, revision, f"doctrine:b09-{token}#fixture"


def capture(cur, section, revision, origin, label: str):
    return one(cur, """select id,ref,version from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)""",
               (origin, f"B09 {label}", "Show the current bounded outcome",
                Jsonb([{"id": "OUTCOME", "text": "A source-bound card is returned."}]),
                section, revision, uuid.uuid4()))


def read(cur, cursor=None, limit=25):
    return one(cur, "select ops.read_doc_outcome_cards_successor(%s,%s)", (cursor, limit))[0]


def projection_lineage(cur, work_request_id, actor_id, *, job_state: str, session_state: str, surface: str):
    """Minimal joined rows for a rolled-back projection-only contradiction case."""
    job_id, session_id, envelope_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = uuid.uuid4()
    running = job_state == "running"
    cur.execute("""insert into ops.job
      (id,definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,
       state,attempt,lease_owner,lease_token,leased_until,started_at)
      values (%s,'engineering-slice',1,%s,now()+(%s * interval '1 microsecond'),2,300,%s,%s,
              case when %s then 'b09' end,case when %s then %s end,
              case when %s then now()+interval '5 minutes' end,case when %s then now() end)""",
      (job_id, f"b09-projection-{job_id}", int(job_id.hex[:8], 16) % 1_000_000, job_state, 1 if running else 0,
       running, running, token, running, running))
    cur.execute("""insert into ops.capability_agent_session
      (id,work_request_id,executor_actor_id,created_by_actor_id,state,source_commit_sha,worktree_ref,scope_ref)
      values (%s,%s,%s,%s,%s,%s,'b09-projection','b09')""",
      (session_id, work_request_id, actor_id, actor_id, session_state, "0" * 40))
    digest = "sha256:" + "a" * 64
    envelope = {
        "work_request_id": f"wr:{work_request_id}",
        "state_binding": {"state_version": "1", "canonical_record_digest": digest},
        "server_binding": {"adapter": {"surface": surface}},
    }
    cur.execute("""insert into ops.engineering_execution_envelope
      (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
       state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
      values (%s,%s,%s,%s,%s,'b09-projection',%s,1,%s,%s,%s,now(),now()+interval '5 minutes')""",
      (envelope_id, job_id, work_request_id, uuid.uuid4(), uuid.uuid4(), session_id,
       digest, "sha256:" + uuid.uuid4().hex * 2, Jsonb(envelope)))
    if running:
        cur.execute("""insert into ops.job_attempt(id,job_id,attempt,lease_owner,lease_token,state)
          values (%s,%s,1,'b09',%s,'running')""", (uuid.uuid4(), job_id, token))
    return job_id, envelope_id, session_id


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            source_actor = one(cur, """insert into actor(slug,kind,display_name)
              values ('mcp-authenticated','automation','B09 MCP fixture')
              on conflict (slug) do update set active=true
              returning id""")[0]
            section, revision, origin = doctrine(cur, source_actor)
            grant_settable_runtime_roles(cur, "carr_writer")
            set_local_role(cur, "carr_writer")
            rows = {name: capture(cur, section, revision, origin, name)
                    for name in ("queued", "active", "waiting", "failed", "unknown", "verified", "fallback", "unsupported")}
            cur.execute("reset role")

            # The source contract is exercised above.  These later states are
            # intentionally constructed only inside this rolled-back projection
            # proof: Program 6 exposes typed captured→triaged→ready transitions,
            # while its later terminal states belong to other bounded domains.
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='triaged' where id=%s", (rows['active'][0],))
            cur.execute("update ops.work_request set state='needs_joe' where id=%s", (rows['waiting'][0],))
            cur.execute("update ops.work_request set state='failed',exit_reason='B09 rollback fixture failure' where id=%s", (rows['failed'][0],))
            cur.execute("""update ops.work_request set shape_disposition='not_required',
              shape_fixed_surface_ref='b09-projection-fixture',
              shape_rationale='Rollback-only outcome-card projection fixture.',
              shape_decided_by_actor_id=%s,shape_decided_at=now() where id=%s""",
                        (source_actor, rows['verified'][0]))
            cur.execute("update ops.work_request set state='released' where id=%s", (rows['verified'][0],))
            cur.execute("update ops.work_request set updated_at=now()-interval '25 hours' where id=%s", (rows['unknown'][0],))
            cur.execute("""update ops.work_request
              set shape_disposition='not_required',shape_fixed_surface_ref='b09-projection-fixture',
                  shape_rationale='Rollback-only outcome-card projection fixture.',
                  shape_decided_by_actor_id=%s,shape_decided_at=now()
              where id = any(%s)""", (source_actor, [rows[name][0] for name in ("active", "fallback", "unsupported")]))

            cur.execute("set local session_replication_role=replica")
            try:
                projection_lineage(cur, rows['active'][0], source_actor,
                                   job_state="running", session_state="claimed", surface="codex_desktop")
                projection_lineage(cur, rows['fallback'][0], source_actor,
                                   job_state="queued", session_state="cancelled", surface="codex_desktop")
                projection_lineage(cur, rows['unsupported'][0], source_actor,
                                   job_state="queued", session_state="cancelled", surface="web")
            finally:
                # The surrounding rollback restores the normal replication role.
                # Do not issue a second statement here: it would hide a fixture
                # constraint failure behind PostgreSQL's aborted-transaction error.
                pass

            cur.execute("select set_config('carr.acting_actor_slug','mcp-authenticated',true)")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            set_local_role(cur, "carr_writer")
            first = read(cur, None, 2)
            if first.get("ok") is not True or len(first["cards"]) != 2 or first.get("more") is not True:
                raise RuntimeError(f"first page was not a bounded page: {first}")
            second = read(cur, first["next_cursor"], 50)
            first_ids = {card["card_id"] for card in first["cards"]}
            second_ids = {card["card_id"] for card in second["cards"]}
            if first_ids & second_ids or len(first_ids | second_ids) != 8:
                raise RuntimeError("cursor page boundary duplicated or skipped a source row")
            all_cards = {card["work_request_ref"]: card for card in first["cards"] + second["cards"]}
            states = {name: all_cards[rows[name][1]]["routing_state"] for name in ("queued", "active", "waiting", "failed", "unknown", "verified")}
            if states != {"queued":"queued", "active":"active", "waiting":"waiting", "failed":"failed", "unknown":"unknown", "verified":"verified"}:
                raise RuntimeError(f"six state mapping drifted: {states}")
            # Only the envelope-bound active fixture has a job and attempt;
            # source rows without that canonical join cannot inherit either.
            unjoined = ("queued", "waiting", "failed", "unknown", "verified")
            if any(all_cards[rows[name][1]]["job_id"]["value"] is not None
                   or all_cards[rows[name][1]]["attempt_id"]["value"] is not None for name in unjoined):
                raise RuntimeError("unjoined job or attempt was projected")
            active_card = all_cards[rows["active"][1]]
            if not (active_card["job_id"]["value"] and active_card["attempt_id"]["value"].startswith("job-attempt:")):
                raise RuntimeError("canonical envelope job-attempt join was unavailable")
            if any(card["native_task_id"]["value"] is not None for card in all_cards.values()):
                raise RuntimeError("native task was invented")
            if all_cards[rows['fallback'][1]]["session_entry"]["fallback"] is None:
                raise RuntimeError("supported host-loss evidence did not expose its bounded fallback")
            if all_cards[rows['unsupported'][1]]["session_entry"]["fallback"] is not None:
                raise RuntimeError("unsupported host exposed a fallback")
            cur.execute("reset role")

            # A different tenant and a different actor each see no cards.
            cur.execute("select set_config('carr.organization_tenant_id','other-tenant',true)")
            set_local_role(cur, "carr_writer")
            if read(cur)["cards"]:
                raise RuntimeError("cross-tenant actor received a card")
            cur.execute("reset role")
            cur.execute("select set_config('carr.organization_tenant_id','carr-internal',true)")
            cur.execute("select set_config('carr.acting_actor_slug','system',true)")
            set_local_role(cur, "carr_writer")
            if read(cur)["cards"]:
                raise RuntimeError("unrelated actor received a card")
        print("PASS: B09 outcome-card PostgreSQL capture, scope, states and cursor proof")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
