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
  triaged decline          preserves triage and withdrawal receipts; unstarted ready
                           decline preserves plans; claimed or assigned work refuses
  triaged supersession     0888: a triaged request with nothing downstream is
                           superseded, keeps its triage fields, and its triage
                           receipt stays readable through superseded_by; one
                           with a plan is refused by NAME, at the function and
                           again at the trigger for a forged receipt
  the receipts are evidence
                           withdrawal and (since 0888) triage receipts are
                           append-only by TRIGGER, not by a table comment
  the pointers hold        no self-supersession, no successor that is itself
                           withdrawn, no non-sourced successor, no two-row cycle
  no forged withdrawal     the immutability trigger was BEFORE UPDATE only, so a
                           row could be INSERTed already withdrawn with no
                           receipt and no history; that hole is closed and pinned
  no unreceipted update    a direct UPDATE into a terminal state is refused
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection  # noqa: E402

REFUSALS = 0
# The decline verb's subject read, taken from its source so the gate runs the shipped text.
_INTAKE = (pathlib.Path(__file__).resolve().parents[1] / "mcp-server/src/work-request-intake.js").read_text()
_SUBJECT = re.search(r"`(select id, state from ops\.work_request where ref = \$1[^`]*)`", _INTAKE)
if _SUBJECT is None:
    raise SystemExit("work-request-withdrawal-db-gate: decline subject read not found in work-request-intake.js")
SUBJECT_SQL = _SUBJECT.group(1)

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


def fixture(cur, *, slug="wr-withdrawal-gate", doc=DOC, section=SEC, revision=REV):
    """A doctrine origin to capture against, built and rolled back with the gate."""
    actor = cur.execute("select id from public.actor order by slug limit 1").fetchone()[0]
    cur.execute(
        "insert into public.doctrine_document (id,slug,title,content_class,visibility)"
        " values (%s,%s,'Withdrawal gate','reference','shared')", (doc, slug))
    cur.execute(
        "insert into public.doctrine_section (id,document_id,section_key,title,ordinal,status,current_version)"
        " values (%s,%s,'origin','Origin',1,'active',1)", (section, doc))
    cur.execute(
        "insert into public.doctrine_revision (id,section_id,version,actor_id,body,plain_text,content_hash)"
        " values (%s,%s,1,%s,%s,'x',%s)",
        (revision, section, actor, Jsonb({"text": "x"}), hashlib.sha256(b"x").hexdigest()))
    cur.execute("update public.doctrine_section set current_revision_id=%s where id=%s", (revision, section))
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


def capture(cur, title, *, slug="wr-withdrawal-gate", section=SEC, revision=REV):
    return cur.execute(
        "select ref from ops.capture_sourced_work_request("
        "%s,%s,'outcome',%s::jsonb,%s,%s,%s)",
        (f"doctrine:{slug}#origin", title, CRITERIA, section, revision, str(uuid.uuid4()))).fetchone()[0]


def wait_for_blocker(observer, future, waiter_pid, blocker_pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if future.done():
            raise RuntimeError(f"concurrent operation did not wait for its blocker: {future.result()}")
        blockers = observer.execute("select pg_blocking_pids(%s)", (waiter_pid,)).fetchone()[0]
        if blocker_pid in blockers:
            return
        time.sleep(0.02)
    raise RuntimeError("concurrent operation never blocked on the expected backend")


def concurrency_test(dsn):
    # This test commits fixtures. It is only for the disposable loopback database.
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(dsn)
    if info.get("host") not in ("127.0.0.1", "localhost", "::1") or (
            info.get("hostaddr") and info["hostaddr"] not in ("127.0.0.1", "::1")):
        raise RuntimeError("--concurrency-only requires a disposable loopback DATABASE_URL")

    with psycopg.connect(dsn) as setup:
        cur = setup.cursor()
        slug = "wr-concurrency-" + uuid.uuid4().hex
        section, revision = uuid.uuid4(), uuid.uuid4()
        fixture(cur, slug=slug, doc=uuid.uuid4(), section=section, revision=revision)
        refs = [capture(cur, title, slug=slug, section=section, revision=revision)
                for title in ("Concurrent evidence", "Concurrent successor", "Supersession first")]
        retired, successor, reverse_retired = refs
        triage(cur, retired)
        triage(cur, reverse_retired)
        request_id = cur.execute("select id from ops.work_request where ref=%s", (retired,)).fetchone()[0]
    now = datetime.now(timezone.utc)
    scope = {"workflow_key": "wr-withdrawal-concurrency", "workflow_version": 1,
             "work_request_id": retired}
    evidence = {"layer": "controller_assessment", "basis": "controller_observation", "status": "pass",
                "subject_ref": "controller:withdrawal-gate", "evidence_ref": "test:" + str(uuid.uuid4()),
                "evidence_digest": "sha256:" + "a" * 64, "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "detail": {"controller_state": "running", "readback_source": "withdrawal-gate",
                           "readback_at": now.isoformat()}, "incident_refs": [], "recovery_refs": []}
    with psycopg.connect(dsn, autocommit=True) as writer, psycopg.connect(dsn, autocommit=True) as superseder:
        writer.execute("set statement_timeout='10s'")
        superseder.execute("set statement_timeout='10s'")
        # Use the database's declared basis; this test targets serialization.
        evidence["basis"] = writer.execute(
            "select ops.assurance_health_basis('controller_assessment')").fetchone()[0]
        writer_pid = writer.info.backend_pid
        superseder_pid = superseder.info.backend_pid
        writer.execute("begin")
        writer.execute("set local carr.acting_actor_slug='joe'")
        receipt = writer.execute("select ops.record_assurance_health_evidence(%s,%s,%s)",
                                 (Jsonb(scope), Jsonb(evidence), uuid.uuid4())).fetchone()[0]
        if receipt.get("replayed") is not False or not receipt.get("id"):
            raise RuntimeError(f"evidence writer did not insert: {receipt}")
        with ThreadPoolExecutor(max_workers=1) as pool:
            def supersede():
                try:
                    superseder.execute("select ops.supersede_sourced_work_request(%s,2,'carried forward',%s,'joe',%s)",
                                       (retired, successor, uuid.uuid4()))
                    return "accepted"
                except psycopg.Error as exc:
                    return str(exc.diag.message_primary or exc)
            pending = pool.submit(supersede)
            try:
                wait_for_blocker(writer, pending, superseder_pid, writer_pid)
            finally:
                writer.execute("commit")
            message = pending.result(timeout=12)
        if not message.startswith("triaged_work_request_has_downstream_evidence:") or (
                "ops.assurance_health_evidence.work_request_id" not in message):
            raise RuntimeError(f"committed evidence was not named as a strand: {message}")
        state = writer.execute("select state,version from ops.work_request where id=%s", (request_id,)).fetchone()
        if state != ("triaged", 2):
            raise RuntimeError(f"concurrent supersession changed the request: {state}")
        if writer.execute("select work_request_id from ops.assurance_health_evidence where id=%s",
                          (receipt["id"],)).fetchone() != (retired,):
            raise RuntimeError("committed evidence lost its Work Request")
        superseder.execute("begin")
        superseder.execute("select ops.supersede_sourced_work_request(%s,2,'carried forward',%s,'joe',%s)",
                           (reverse_retired, successor, uuid.uuid4()))
        reverse_scope = {**scope, "work_request_id": reverse_retired}
        writer.execute("set carr.acting_actor_slug='joe'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            def write_after_supersession():
                try:
                    writer.execute("select ops.record_assurance_health_evidence(%s,%s,%s)",
                                   (Jsonb(reverse_scope), Jsonb(evidence), uuid.uuid4()))
                    return "accepted"
                except psycopg.Error as exc:
                    return str(exc.diag.message_primary or exc)
            pending = pool.submit(write_after_supersession)
            try:
                wait_for_blocker(superseder, pending, writer_pid, superseder_pid)
            finally:
                superseder.execute("commit")
            message = pending.result(timeout=12)
        if not message.startswith("work_request_evidence_not_live:"):
            raise RuntimeError(f"writer accepted evidence after waiting for supersession: {message}")
        count = writer.execute("select count(*) from ops.assurance_health_evidence where work_request_id=%s",
                               (reverse_retired,)).fetchone()[0]
        if count != 0:
            raise RuntimeError("evidence was committed after the request was superseded")
    print("PASS: evidence-first and supersession-first transactions serialize and refuse stranding")

    with psycopg.connect(dsn) as setup:
        cur = setup.cursor()
        predecessor, middle, final = [
            capture(cur, title, slug=slug, section=section, revision=revision)
            for title in ("Predecessor A", "Successor B", "Successor C")]
        triage(cur, predecessor)
        triage(cur, middle)
    with psycopg.connect(dsn, autocommit=True) as first, psycopg.connect(dsn, autocommit=True) as second:
        first.execute("set statement_timeout='10s'")
        second.execute("set statement_timeout='10s'")
        first.execute("begin")
        row = first.execute(
            "select state,version,superseded_by_ref from ops.supersede_sourced_work_request("
            "%s,2,'next',%s,'joe',%s)", (middle, final, uuid.uuid4())).fetchone()
        if row != ("superseded", 3, final):
            raise RuntimeError(f"B->C did not supersede the middle request: {row}")
        with ThreadPoolExecutor(max_workers=1) as pool:
            def supersede_predecessor():
                try:
                    second.execute("select ops.supersede_sourced_work_request(%s,2,'next',%s,'joe',%s)",
                                   (predecessor, middle, uuid.uuid4()))
                    return "accepted"
                except psycopg.Error as exc:
                    return str(exc.diag.message_primary or exc)
            pending = pool.submit(supersede_predecessor)
            try:
                wait_for_blocker(first, pending, second.info.backend_pid, first.info.backend_pid)
            finally:
                first.execute("commit")
            message = pending.result(timeout=12)
        if not message.startswith("successor_withdrawn:"):
            raise RuntimeError(f"A->B must refuse successor_withdrawn after B->C commits: {message}")
        rows = first.execute(
            "select w.ref,w.state,w.version,s.ref from ops.work_request w"
            " left join ops.work_request s on s.id=w.superseded_by"
            " where w.ref=any(%s) order by w.ref", ([predecessor, middle, final],)).fetchall()
        expected = [(predecessor, "triaged", 2, None), (middle, "superseded", 3, final),
                    (final, "captured", 1, None)]
        if rows != sorted(expected):
            raise RuntimeError(f"concurrent supersessions left an invalid chain: {rows}")
        count = first.execute(
            "select count(*) from ops.work_request_withdrawal_receipt r"
            " join ops.work_request w on w.id=r.work_request_id where w.ref=%s",
            (predecessor,)).fetchone()[0]
        if count != 0:
            raise RuntimeError("refused A->B left a withdrawal receipt")
    print("PASS: A->B waits for B->C and refuses successor_withdrawn after commit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency-only", action="store_true",
                        help="commit fixtures on a disposable loopback database and test two connections")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("work-request-withdrawal-db-gate: DATABASE_URL is not set", file=sys.stderr)
        return 1
    try:
        if args.concurrency_only:
            concurrency_test(dsn)
            return 0
        with rollback_only_connection(dsn) as conn:
            cur = conn.cursor()
            actor = fixture(cur)
            grant_settable_runtime_roles(cur, "carr_writer")
            for state, version in (("captured", 1), ("triaged", 2)):
                request = capture(cur, f"Sponsored {state} decline")
                if state == "triaged":
                    triage(cur, request)
                cur.execute("select set_config('carr.acting_actor_slug','codex',true)")
                cur.execute("set local role carr_writer")
                try:
                    refuses_named(cur,
                        "select ops.decline_sourced_work_request(%s,%s,'r','codex',%s)",
                        (request, version, uuid.uuid4()), "sponsored_actor_required:",
                        f"an unsponsored agent declining a {state} request")
                finally:
                    cur.execute("reset role")
                card = cur.execute("select state,version from ops.work_request_card(%s,'carr-internal')",
                                   (request,)).fetchone()
                if card != (state, version):
                    raise RuntimeError(f"refused {state} decline changed the request: {card}")
                cur.execute("select set_config('carr.acting_actor_slug','joe-local',true),"
                            " set_config('carr.sponsoring_human_slug','joe',true)")
                cur.execute("set local role carr_writer")
                try:
                    row = cur.execute(
                        "select state,version from ops.decline_sourced_work_request(%s,%s,'r','joe-local',%s)",
                        (request, version, uuid.uuid4())).fetchone()
                finally:
                    cur.execute("reset role")
                if row != ("declined", version + 1):
                    raise RuntimeError(f"Joe's sponsored local {state} decline did not land: {row}")
                cur.execute("select set_config('carr.sponsoring_human_slug','',true)")
                print(f"PASS: {state} decline refuses an unsponsored writer and accepts Joe's sponsored local actor")
            cur.execute("select set_config('carr.acting_actor_slug','joe',true),"
                        " set_config('carr.sponsoring_human_slug','',true)")
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

            # --- stale versions cannot withdraw ----------------------------------
            refuses(cur, "select ops.decline_sourced_work_request(%s,2,'r','joe',%s)",
                    (real, str(uuid.uuid4())), "withdrawing at a version that is not the captured one")

            # --- 0888: a triaged request with nothing downstream ------------------
            retired, successor, planned = capture(cur, "Retired"), capture(cur, "Successor"), capture(cur, "Planned")
            for ref in (retired, planned):
                triage(cur, ref)
            declined = capture(cur, "Answered request")
            triage(cur, declined)
            decline_key = uuid.uuid4()
            reason = "already answered; no further work remains"
            row = cur.execute(
                "select state,version,exit_reason from ops.decline_sourced_work_request(%s,2,%s,'joe',%s)",
                (declined, reason, decline_key)).fetchone()
            if row != ("declined", 3, reason):
                raise RuntimeError(f"triaged decline did not land: {row}")
            card = cur.execute(
                "select state,triage_classification,triaged_by_actor_slug,exit_reason,closed_at"
                " from ops.work_request_card(%s,'carr-internal')", (declined,)).fetchone()
            if card[:4] != ("declined", "operational", "joe", reason) or card[4] is None:
                raise RuntimeError(f"triaged decline lost its history on the card: {card}")
            receipt = cur.execute(
                "select r.base_version,r.result_version,r.final_state,r.exit_reason,a.slug"
                " from ops.work_request_withdrawal_receipt r join public.actor a on a.id=r.withdrawn_by_actor_id"
                " where r.idempotency_key=%s", (decline_key,)).fetchone()
            if receipt != (2, 3, "declined", reason, "joe"):
                raise RuntimeError(f"triaged decline lost its withdrawal receipt: {receipt}")
            kept = cur.execute(
                "select r.classification,r.base_version,r.result_version from ops.work_request_triage_receipt r"
                " join ops.work_request w on w.id=r.work_request_id where w.ref=%s", (declined,)).fetchall()
            if kept != [("operational", 1, 2)]:
                raise RuntimeError(f"triaged decline lost its triage receipt: {kept}")
            message = refuses_named(cur,
                "select ops.decline_sourced_work_request(%s,3,'r','joe',%s)",
                (declined, uuid.uuid4()), "work_request_not_declinable:", "declining an already declined request")
            if "state declined" not in message:
                raise RuntimeError(f"decline refusal did not name its state: {message}")
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

            retired_id = cur.execute("select id from ops.work_request where ref=%s", (retired,)).fetchone()[0]
            for table, column, value in (
                    ("public.room_dispatch_link", "work_request_id", retired_id),
                    ("ops.assurance_health_evidence", "work_request_id", retired),
                    ("ops.release", "work_request_ref", retired),
                    ("ops.engineering_stale_contract_fence", "work_request_ref", retired)):
                refuses_named(cur, f"insert into {table} ({column}) values (%s)", (value,),
                              "work_request_evidence_not_live:", f"new evidence on a retired request in {table}")

            # --- 0888: the triage receipt is evidence -----------------------------
            refuses_named(cur, "update ops.work_request_triage_receipt set classification='safety_review'",
                          (), "ops.work_request_triage_receipt is append-only", "rewriting a triage receipt")
            refuses_named(cur, "delete from ops.work_request_triage_receipt", (),
                          "ops.work_request_triage_receipt is append-only", "deleting a triage receipt")

            # --- 0888: a triaged request with a plan is refused by name -----------
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

            row = cur.execute(
                "select state,version from ops.decline_sourced_work_request(%s,2,'answered','joe',%s)",
                (planned, uuid.uuid4())).fetchone()
            if row != ("declined", 3):
                raise RuntimeError(f"a triaged request carrying plan history was not declined: {row}")
            if cur.execute("select count(*) from ops.sourced_work_request_plan p"
                           " join ops.work_request w on w.id=p.work_request_id where w.ref=%s",
                           (planned,)).fetchone()[0] != 1:
                raise RuntimeError("decline discarded the existing plan history")

            ready = capture(cur, "Ready request")
            triage(cur, ready)
            plan_hash = cur.execute(
                "select plan_hash from ops.propose_sourced_work_request_plan(%s,2,'Bounded scope',%s,%s,"
                "'safe:recovery:stop','safe:observability:ops-run',%s,%s)",
                (ready, runbook, Jsonb([]), Jsonb({"max_steps": 2, "max_duration_minutes": 10}),
                 uuid.uuid4())).fetchone()[0]
            cur.execute("set session authorization carr_authority_joe")
            try:
                cur.execute("select * from ops.accept_sourced_work_request_plan(%s,2,%s,%s)",
                            (ready, plan_hash, uuid.uuid4()))
            finally:
                cur.execute("reset session authorization")
            history_sql = """select to_jsonb(p),to_jsonb(a),to_jsonb(t)
                from ops.work_request w
                join ops.sourced_work_request_plan p on p.work_request_id=w.id
                join ops.sourced_work_request_plan_acceptance_receipt a on a.plan_id=p.id
                join ops.work_request_triage_receipt t on t.work_request_id=w.id
                where w.ref=%s"""
            history = cur.execute(history_sql, (ready,)).fetchall()
            if len(history) != 1:
                raise RuntimeError("ready fixture has no accepted plan and triage history")
            cur.execute("savepoint ready_probe")
            row = cur.execute(
                "select state,version from ops.decline_sourced_work_request(%s,3,'obsolete','joe',%s)",
                (ready, uuid.uuid4())).fetchone()
            if row != ("declined", 4):
                raise RuntimeError(f"unstarted ready decline did not land: {row}")
            cur.execute("rollback to savepoint ready_probe")
            for downstream in ("claimed_at", "executor_actor", "started_at", "assignment"):
                cur.execute("savepoint downstream")
                if downstream == "assignment":
                    cur.execute("""insert into ops.work_request_execution_assignment
                        (work_request_id,profile_id,sponsoring_human_id,environment,
                         policy_ref,policy_digest,idempotency_key)
                        select w.id,p.id,%s,'local','test:withdrawal',%s,%s
                        from ops.work_request w cross join public.agent_profile p
                        where w.ref=%s limit 1""", (actor, "sha256:" + "a" * 64, uuid.uuid4(), ready))
                    if cur.rowcount != 1:
                        raise RuntimeError("execution assignment fixture was not inserted")
                else:
                    cur.execute("alter table ops.work_request disable trigger user")
                    value = "'codex'" if downstream == "executor_actor" else "now()"
                    cur.execute(f"update ops.work_request set {downstream}={value} where ref=%s", (ready,))
                    cur.execute("alter table ops.work_request enable trigger user")
                refuses_named(cur,
                    "select ops.decline_sourced_work_request(%s,3,'obsolete','joe',%s)",
                    (ready, uuid.uuid4()), "work_request_not_declinable:",
                    f"declining ready work with {downstream}")
                refuses(cur, """with r as (
                    insert into ops.work_request_withdrawal_receipt
                        (work_request_id,idempotency_key,base_version,result_version,
                         final_state,exit_reason,withdrawn_by_actor_id)
                    select id,gen_random_uuid(),3,4,'declined','forged',%s
                    from ops.work_request where ref=%s returning 1)
                    update ops.work_request set state='declined',exit_reason='forged',
                        closed_at=now(),version=4 where ref=%s and exists(select 1 from r)""",
                    (actor, ready, ready), f"forged ready decline with {downstream}")
                if cur.execute("select state,version from ops.work_request where ref=%s", (ready,)).fetchone() != ("ready", 3):
                    raise RuntimeError("a refused decline changed the ready request")
                cur.execute("rollback to savepoint downstream")
            refuses_named(cur,
                "select ops.decline_sourced_work_request(%s,2,'obsolete','joe',%s)",
                (ready, uuid.uuid4()), "work_request_version_conflict:", "stale ready decline")
            refuses(cur, "update ops.work_request set state='declined',exit_reason='forged',"
                         "closed_at=now(),version=4 where ref=%s", (ready,), "unreceipted ready decline")
            row = cur.execute(
                "select state,version,exit_reason from ops.decline_sourced_work_request(%s,3,'obsolete','joe',%s)",
                (ready, uuid.uuid4())).fetchone()
            if row != ("declined", 4, "obsolete"):
                raise RuntimeError(f"unstarted ready decline did not land: {row}")
            if cur.execute(history_sql, (ready,)).fetchall() != history:
                raise RuntimeError("ready decline changed accepted plan or triage history")
            card = cur.execute("select state,exit_reason,closed_at from ops.work_request_card(%s,'carr-internal')",
                               (ready,)).fetchone()
            if card[:2] != ("declined", "obsolete") or card[2] is None:
                raise RuntimeError(f"declined ready card lost withdrawal history: {card}")

            # --- 0982: unstarted program rows decline through the same door ----
            cur.execute("""insert into ops.work_request
                (ref,title,state,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal)
                values ('WR-AI-999','Gate program row','ready','joe','joe','carr-internal',
                        'carr-ai-engineering-suite-v1',999)""")
            for program_ref in ("WR-AI-999", "WR-SIEP-26"):
                # The verb's own subject read must see the row as carr_writer;
                # a FOR UPDATE there was hidden by RLS for SIEP rows.
                cur.execute("set local role carr_writer")
                try:
                    seen = cur.execute(SUBJECT_SQL.replace("$1", "%s"), (program_ref,)).fetchall()
                finally:
                    cur.execute("reset role")
                if len(seen) != 1:
                    raise RuntimeError(f"decline verb's subject read cannot see {program_ref} as carr_writer")
                version = cur.execute("select version from ops.work_request where ref=%s",
                                      (program_ref,)).fetchone()
                if version is None:
                    raise RuntimeError(f"program fixture {program_ref} is missing")
                version = version[0]
                cur.execute("savepoint program_started")
                cur.execute("alter table ops.work_request disable trigger user")
                cur.execute("update ops.work_request set claimed_at=now() where ref=%s", (program_ref,))
                cur.execute("alter table ops.work_request enable trigger user")
                refuses_named(cur,
                    "select ops.decline_sourced_work_request(%s,%s,'retired','joe',%s)",
                    (program_ref, version, uuid.uuid4()), "work_request_not_declinable:",
                    f"declining claimed program row {program_ref}")
                cur.execute("rollback to savepoint program_started")
                row = cur.execute(
                    "select state,version,exit_reason from ops.decline_sourced_work_request(%s,%s,'retired','joe',%s)",
                    (program_ref, version, uuid.uuid4())).fetchone()
                if row != ("declined", version + 1, "retired"):
                    raise RuntimeError(f"unstarted program decline of {program_ref} did not land: {row}")
                refuses_named(cur,
                    "select ops.decline_sourced_work_request(%s,%s,'again','joe',%s)",
                    (program_ref, version + 1, uuid.uuid4()), "work_request_not_declinable:",
                    f"declining declined program row {program_ref}")
            cur.execute("alter table ops.work_request disable trigger user")
            cur.execute("""insert into ops.work_request
                (ref,title,state,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal)
                values ('WR-AI-998','Gate verifying row','verification','joe','joe','carr-internal',
                        'carr-ai-engineering-suite-v1',998)""")
            cur.execute("alter table ops.work_request enable trigger user")
            refuses_named(cur,
                "select ops.decline_sourced_work_request('WR-AI-998',1,'retired','joe',%s)",
                (uuid.uuid4(),), "work_request_not_declinable:", "declining a verifying program row")
            # 0983: a reused key must not decline a second row without its own receipt,
            # and a captured program row declines like a ready one.
            cur.execute("""insert into ops.work_request
                (ref,title,state,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal)
                values ('WR-AI-997','Gate captured row','captured','joe','joe','carr-internal',
                        'carr-ai-engineering-suite-v1',997),
                       ('WR-AI-996','Gate reused-key row','ready','joe','joe','carr-internal',
                        'carr-ai-engineering-suite-v1',996)""")
            reused = uuid.uuid4()
            row = cur.execute(
                "select state,version from ops.decline_sourced_work_request('WR-AI-997',1,'retired','joe',%s)",
                (reused,)).fetchone()
            if row != ("declined", 2):
                raise RuntimeError(f"captured program decline did not land: {row}")
            refuses_named(cur,
                "select ops.decline_sourced_work_request('WR-AI-996',1,'retired','joe',%s)",
                (reused,), "withdrawal_receipt_conflict:", "declining with another decline's receipt key")
            if cur.execute("select state,version from ops.work_request where ref='WR-AI-996'").fetchone() != ("ready", 1):
                raise RuntimeError("a receipt-key conflict changed the program row")
            print("PASS: unstarted program rows decline with a receipt; claimed, verifying and declined program rows "
                  "refuse; a reused receipt key refuses without declining")


        print(f"PASS: decline and supersede land with receipts; a withdrawn request stays "
              f"readable on the card with reason, closing time and successor; both leave the "
              f"queue; triaged decline preserves intake and plan history, unstarted ready decline preserves accepted plans; claims, starts and assignments refuse; "
              f"a triaged request with nothing downstream is superseded with its triage "
              f"receipt intact, one with a plan is refused by name; {REFUSALS} refusals hold")
        return 0
    except Exception as exc:
        print(f"work-request-withdrawal-db-gate: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
