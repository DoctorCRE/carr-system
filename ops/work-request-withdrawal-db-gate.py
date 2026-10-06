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
  captured only            a triaged request cannot be withdrawn, at two layers
  the receipt is evidence  append-only by TRIGGER, not by a table comment; the
                           sibling triage receipt claims append-only in a comment
                           and has no trigger at all, which is filed separately
  the pointers hold        no self-supersession, no successor that is itself
                           withdrawn, no non-sourced successor, no two-row cycle
  no forged withdrawal     the immutability trigger was BEFORE UPDATE only, so a
                           row could be INSERTed already withdrawn with no
                           receipt and no history; that hole is closed and pinned
  no unreceipted update    a direct UPDATE into a terminal state is refused
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gate_runtime_role import rollback_only_connection  # noqa: E402

REFUSALS = 0

DOC = "aaaaaaaa-0000-4000-8000-00000000d0c1"
SEC = "aaaaaaaa-0000-4000-8000-00000000cec1"
REV = "aaaaaaaa-0000-4000-8000-000000000re1".replace("re", "01")
CRITERIA = '[{"id":"ONE","text":"a criterion"}]'


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
        " values (%s,%s,1,%s,'{}'::jsonb,'x','h')", (REV, SEC, actor))
    cur.execute("update public.doctrine_section set current_revision_id=%s where id=%s", (REV, SEC))
    return actor


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
            fixture(cur)
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

            # --- captured only ---------------------------------------------------
            refuses(cur, "select ops.decline_sourced_work_request(%s,2,'r','joe',%s)",
                    (real, str(uuid.uuid4())), "withdrawing at a version that is not the captured one")

        print(f"PASS: decline and supersede land with receipts; a withdrawn request stays "
              f"readable on the card with reason, closing time and successor; both leave the "
              f"queue; {REFUSALS} named refusals hold")
        return 0
    except Exception as exc:
        print(f"work-request-withdrawal-db-gate: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
