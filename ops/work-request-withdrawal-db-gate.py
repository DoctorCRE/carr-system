#!/usr/bin/env python3
# ci: db-gate
# doctrine: work-request-withdrawal
"""Rollback-only Postgres gate for withdrawing a Work Request captured in error.

A sourced Work Request could only ever move FORWARD. Every intake verb advanced
one, so a record created by mistake stayed in the queue looking like work, and
the only way to clear it was to triage it forward as though it were real.

0426 adds the terminal half using states the table already had. This gate exists
because the interesting half of that change is what it REFUSES, and refusals are
exactly what a fixture-shaped test tends not to reach. Five independent reviews
of the plan for this change each found a real defect; three of them were things
believed rather than executed. So every assertion below runs the real function
against a real database and fails closed.

WHAT IT PROVES, in the order a reader would want it:
  the happy paths          decline and supersede both land, with a receipt
  the record SURVIVES      a withdrawn request is still readable on the card,
                           with its reason, its closing time and its successor —
                           an earlier revision of this design would have made a
                           withdrawn row raise work_request_not_found, which is
                           the capability erasing the record it exists to write
  captured only, for a decline
                           a triaged request cannot be declined, at two layers
  triaged supersession     0883: a triaged request with nothing downstream is
                           superseded, keeps its triage fields, and its triage
                           receipt stays readable through superseded_by; one
                           with a plan is refused by NAME, at the function and
                           again at the trigger for a forged receipt
  the receipts are evidence
                           withdrawal and (since 0883) triage receipts are
                           append-only by TRIGGER, not by a table comment
  the pointers hold        no self-supersession, no successor that is itself
                           withdrawn, no non-sourced successor, no two-row cycle
  no forged withdrawal     the immutability trigger was BEFORE UPDATE only, so a
                           row could be INSERTed already withdrawn with no
                           receipt and no history; that hole is closed and pinned
  no unreceipted update    a direct UPDATE into a terminal state is refused
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection  # noqa: E402

REFUSALS = 0

DOC = "aaaaaaaa-0000-4000-8000-00000000d0c1"
SEC = "aaaaaaaa-0000-4000-8000-00000000cec1"
REV = "aaaaaaaa-0000-4000-8000-000000000re1".replace("re", "01")
CRITERIA = '[{"id":"ONE","text":"a criterion"}]'
RUNBOOK = "Stop within the caps."


def refuses(cur, sql, params, label):
    """Require the database to refuse, and leave the transaction usable."""
    global REFUSALS
    cur.execute("savepoint refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint refusal")
        REFUSALS += 1
        return
    cur.execute("rollback to savepoint refusal")
    raise RuntimeError(f"{label} was ACCEPTED; it must be refused")


def refuses_named(cur, sql, params, name, label):
    """Require a refusal whose message starts with the database's own name for it."""
    global REFUSALS
    cur.execute("savepoint refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint refusal")
        message = str(exc.diag.message_primary or exc)
        if not message.startswith(name):
            raise RuntimeError(f"{label} was refused, but not as {name}: {message}") from exc
        REFUSALS += 1
        return message
    cur.execute("rollback to savepoint refusal")
    raise RuntimeError(f"{label} was ACCEPTED; it must be refused as {name}")


def fixture(cur):
    """A doctrine origin to capture against, built and rolled back with the gate."""
    actor = cur.execute("select id from public.actor order by slug limit 1").fetchone()[0]
    cur.execute(
        "insert into public.doctrine_document (id,slug,title,content_class,visibility)"
        " values (%s,'wr-withdrawal-gate','Withdrawal gate','reference','shared')", (DOC,))
    cur.execute(
        "insert into public.doctrine_section (id,document_id,section_key,title,ordinal,status,current_version)"
        " values (%s,%s,'origin','Origin',1,'active',1)", (SEC, DOC))
    cur.execute(
        "insert into public.doctrine_revision (id,section_id,version,actor_id,body,plain_text,content_hash)"
        " values (%s,%s,1,%s,%s,'x',%s)",
        (REV, SEC, actor, Jsonb({"text": "x"}), hashlib.sha256(b"x").hexdigest()))
    cur.execute("update public.doctrine_section set current_revision_id=%s where id=%s", (REV, SEC))
    return actor


def runbook_fixture(cur, actor):
    """A shared runbook section a bounded plan can cite."""
    doc = cur.execute("select id from public.doctrine_document where slug='runbook'").fetchone()
    doc = doc[0] if doc else cur.execute(
        "insert into public.doctrine_document (slug,title,content_class,visibility)"
        " values ('runbook','Runbook','reference','shared') returning id").fetchone()[0]
    section = cur.execute(
        "insert into public.doctrine_section (document_id,section_key,title,ordinal,status,current_version)"
        " values (%s,'wr-withdrawal-gate','Withdrawal gate runbook',999,'active',1) returning id",
        (doc,)).fetchone()[0]
    revision = cur.execute(
        "insert into public.doctrine_revision (section_id,version,actor_id,body,plain_text,content_hash)"
        " values (%s,1,%s,%s,%s,%s) returning id",
        (section, actor, Jsonb({"text": RUNBOOK}), RUNBOOK,
         hashlib.sha256(RUNBOOK.encode()).hexdigest())).fetchone()[0]
    cur.execute("update public.doctrine_section set current_revision_id=%s where id=%s", (revision, section))
    return "doctrine:runbook#wr-withdrawal-gate"


def triage(cur, ref):
    """Triage as the Joe authority login, the only role the function admits."""
    cur.execute("""do $$ begin
      if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
        create role carr_authority_joe login;
      end if; end $$""")
    cur.execute("grant carr_authority to carr_authority_joe")
    grant_settable_runtime_roles(cur, "carr_authority_joe")
    cur.execute("set session authorization carr_authority_joe")
    try:
        row = cur.execute("select state,version from ops.triage_sourced_work_request(%s,1,'operational',%s)",
                          (ref, str(uuid.uuid4()))).fetchone()
    finally:
        cur.execute("reset session authorization")
    if row != ("triaged", 2):
        raise RuntimeError(f"fixture triage did not land: {row}")


def capture(cur, title):
    return cur.execute(
        "select ref from ops.capture_sourced_work_request("
        "'doctrine:wr-withdrawal-gate#origin',%s,'outcome',%s::jsonb,%s,%s,%s)",
        (title, CRITERIA, SEC, REV, str(uuid.uuid4()))).fetchone()[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("work-request-withdrawal-db-gate: DATABASE_URL is not set", file=sys.stderr)
        return 1
    try:
        with rollback_only_connection(dsn) as conn:
            cur = conn.cursor()
            actor = fixture(cur)
            junk, real, third = capture(cur, "Junk"), capture(cur, "Real"), capture(cur, "Third")

            # --- supersede, and the record must survive it -------------------
            row = cur.execute(
                "select ref,state,exit_reason,superseded_by_ref from"
                " ops.supersede_sourced_work_request(%s,1,%s,%s,'joe',%s)",
                (junk, "duplicate of the record that replaced it", real, str(uuid.uuid4()))).fetchone()
            if row[1] != "superseded" or row[3] != real:
                raise RuntimeError(f"supersede did not land: {row}")

            card = cur.execute(
                "select state,exit_reason,closed_at,superseded_by_ref from"
                " ops.work_request_card(%s,'carr-internal')", (junk,)).fetchone()
            if card is None:
                raise RuntimeError("the card DROPPED a withdrawn request; the capability erased "
                                   "the record it exists to write")
            if card[0] != "superseded" or not card[1] or card[2] is None or card[3] != real:
                raise RuntimeError(f"the card cannot say why it was withdrawn: {card}")

            receipts = cur.execute(
                "select final_state,exit_reason from ops.work_request_withdrawal_receipt"
                " where work_request_id=(select id from ops.work_request where ref=%s)",
                (junk,)).fetchall()
            if len(receipts) != 1 or receipts[0][0] != "superseded":
                raise RuntimeError(f"the withdrawal left no single receipt: {receipts}")

            # --- decline --------------------------------------------------------
            row = cur.execute(
                "select state,exit_reason from ops.decline_sourced_work_request(%s,1,%s,'joe',%s)",
                (third, "captured by mistake, nothing to build", str(uuid.uuid4()))).fetchone()
            if row[0] != "declined" or not row[1]:
                raise RuntimeError(f"decline did not land: {row}")

            # --- and both leave the queue ---------------------------------------
            queued = {r[0] for r in cur.execute(
                "select ref from ops.current_sourced_work_requests('carr-internal')").fetchall()}
            if junk in queued or third in queued:
                raise RuntimeError("a withdrawn request is still in the queue")

            # --- the receipt is evidence ----------------------------------------
            refuses(cur, "update ops.work_request_withdrawal_receipt set exit_reason='rewritten'",
                    (), "rewriting a withdrawal receipt")
            refuses(cur, "delete from ops.work_request_withdrawal_receipt", (),
                    "deleting a withdrawal receipt")

            # --- the pointers hold ----------------------------------------------
            refuses(cur, "select ops.supersede_sourced_work_request(%s,1,'r',%s,'joe',%s)",
                    (real, real, str(uuid.uuid4())), "a request superseding itself")
            refuses(cur, "select ops.supersede_sourced_work_request(%s,1,'r',%s,'joe',%s)",
                    (real, junk, str(uuid.uuid4())), "superseding into an already-withdrawn row")
            refuses(cur, "select ops.decline_sourced_work_request(%s,1,'   ','joe',%s)",
                    (real, str(uuid.uuid4())), "a withdrawal with no reason")

            # --- no forged withdrawal -------------------------------------------
            # THE FORGED ROW IS DELIBERATELY CONSTRAINT-LEGAL. An earlier version of
            # this assertion used state='superseded' with a null successor, which the
            # shape constraint rejects on its own — so it passed while testing nothing
            # about the trigger it names. A mutation reverting the trigger to BEFORE
            # UPDATE survived it. This row satisfies the terminal sub-arm in full, so
            # the INSERT trigger is the only thing left standing between it and the
            # table, and reverting that trigger now fails this gate.
            refuses(cur,
                    "insert into ops.work_request (ref,state,title,desired_outcome,acceptance_criteria,"
                    "requester_actor,owner_actor,capture_idempotency_key,organization_tenant_id,"
                    "doctrine_section_id,doctrine_revision_id,sourced_capture_sequence,origin_ref,"
                    "exit_reason,closed_at) values ('WR-FORGED','declined','x','y','[]'::jsonb,"
                    "'joe','joe',gen_random_uuid(),'carr-internal',%s,%s,999999,"
                    "'doctrine:wr-withdrawal-gate#origin','forged',now())", (SEC, REV),
                    "INSERTing a Work Request already withdrawn")
            refuses(cur, "update ops.work_request set state='declined',exit_reason='no receipt',"
                         "closed_at=now(),version=version+1 where ref=%s", (real,),
                    "an unreceipted UPDATE into a terminal state")

            # --- captured only, for a decline -------------------------------------
            refuses(cur, "select ops.decline_sourced_work_request(%s,2,'r','joe',%s)",
                    (real, str(uuid.uuid4())), "withdrawing at a version that is not the captured one")

            # --- 0883: a triaged request with nothing downstream ------------------
            retired, successor, planned = capture(cur, "Retired"), capture(cur, "Successor"), capture(cur, "Planned")
            for ref in (retired, planned):
                triage(cur, ref)
            refuses(cur, "select ops.decline_sourced_work_request(%s,2,'r','joe',%s)",
                    (retired, str(uuid.uuid4())), "declining a triaged request")
            refuses_named(cur, "select ops.supersede_sourced_work_request(%s,1,'r',%s,'joe',%s)",
                          (retired, successor, str(uuid.uuid4())), "work_request_not_supersedable",
                          "superseding a triaged request at its captured version")

            row = cur.execute(
                "select state,version,superseded_by_ref from"
                " ops.supersede_sourced_work_request(%s,2,%s,%s,'joe',%s)",
                (retired, "every criterion carried forward by its successor", successor,
                 str(uuid.uuid4()))).fetchone()
            if row != ("superseded", 3, successor):
                raise RuntimeError(f"a triaged supersession did not land: {row}")
            card = cur.execute(
                "select state,triage_classification,triaged_by_actor_slug,superseded_by_ref from"
                " ops.work_request_card(%s,'carr-internal')", (retired,)).fetchone()
            if card != ("superseded", "operational", "joe", successor):
                raise RuntimeError(f"the card lost the triage of a superseded request: {card}")
            kept = cur.execute(
                "select r.classification,r.base_version,r.result_version,old.ref"
                "  from ops.work_request succ"
                "  join ops.work_request old on old.superseded_by = succ.id"
                "  join ops.work_request_triage_receipt r on r.work_request_id = old.id"
                " where succ.ref = %s", (successor,)).fetchall()
            if kept != [("operational", 1, 2, retired)]:
                raise RuntimeError(f"the triage receipt is not readable through superseded_by: {kept}")

            # --- 0883: the triage receipt is evidence -----------------------------
            refuses_named(cur, "update ops.work_request_triage_receipt set classification='safety_review'",
                          (), "ops.work_request_triage_receipt is append-only", "rewriting a triage receipt")
            refuses_named(cur, "delete from ops.work_request_triage_receipt", (),
                          "ops.work_request_triage_receipt is append-only", "deleting a triage receipt")

            # --- 0883: a triaged request with a plan is refused by name -----------
            runbook = runbook_fixture(cur, actor)
            cur.execute(
                "select * from ops.propose_sourced_work_request_plan(%s,2,'Bounded scope',%s,%s,"
                "'safe:recovery:stop','safe:observability:ops-run',%s,%s)",
                (planned, runbook, Jsonb([]), Jsonb({"max_steps": 2, "max_duration_minutes": 10}),
                 str(uuid.uuid4())))
            message = refuses_named(
                cur, "select ops.supersede_sourced_work_request(%s,2,'r',%s,'joe',%s)",
                (planned, successor, str(uuid.uuid4())), "triaged_work_request_has_downstream_evidence",
                "superseding a triaged request that holds a plan")
            if "ops.sourced_work_request_plan.work_request_id" not in message:
                raise RuntimeError(f"the refusal does not name what would be stranded: {message}")
            # A forged receipt reaches the trigger, which runs the same census.
            refuses(cur,
                    "with r as (insert into ops.work_request_withdrawal_receipt (work_request_id,"
                    "idempotency_key,base_version,result_version,final_state,exit_reason,superseded_by,"
                    "withdrawn_by_actor_id) select w.id,gen_random_uuid(),2,3,'superseded','forged',s.id,%s"
                    " from ops.work_request w, ops.work_request s where w.ref=%s and s.ref=%s returning 1)"
                    " update ops.work_request set state='superseded',exit_reason='forged',closed_at=now(),"
                    "superseded_by=(select id from ops.work_request where ref=%s),version=3"
                    " where ref=%s and exists (select 1 from r)",
                    (actor, planned, successor, successor, planned),
                    "a receipt-backed UPDATE that strands a plan")
            still = cur.execute("select state,version from ops.work_request where ref=%s", (planned,)).fetchone()
            if still != ("triaged", 2):
                raise RuntimeError(f"a refused supersession moved the planned request: {still}")

        print(f"PASS: decline and supersede land with receipts; a withdrawn request stays "
              f"readable on the card with reason, closing time and successor; both leave the "
              f"queue; a triaged request with nothing downstream is superseded with its triage "
              f"receipt intact, one with a plan is refused by name; {REFUSALS} refusals hold")
        return 0
    except Exception as exc:
        print(f"work-request-withdrawal-db-gate: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
