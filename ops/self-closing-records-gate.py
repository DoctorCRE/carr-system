#!/usr/bin/env python3
# ci: db-gate
# doctrine: engineering-workflow-sop
"""Rollback-only acceptance gate for 0870 self-closing records.

Proves, on the real schema and as the runtime role that fires each door:

* a sourced Work Request reaches its done state (an accepted outcome-feedback
  receipt) without a human call only when every acceptance criterion carries a
  bound receipt the database resolves, and the receipt says so;
* binding refuses a criterion that is not the request's, a receipt that does
  not resolve, and a second different receipt for a bound criterion;
* both doors replay exactly on their idempotency key;
* the human acceptance path is untouched and still stamps human_acceptance;
* defect_resolution and work_request_criterion_evidence are append-only.

The Work Request fixture chain is ops/program6-outcome-feedback-gate.py's own
helpers, loaded rather than copied: one fixture shape, one place to correct.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

from psycopg.types.json import Jsonb
from gate_runtime_role import rollback_only_connection, set_local_role

REPO = Path(__file__).resolve().parent.parent


def fail(message: str) -> int:
    print(f"self-closing-records-gate: FAIL — {message}", file=sys.stderr)
    return 1


def program6():
    spec = importlib.util.spec_from_file_location(
        "carr_p6_outcome_gate", REPO / "ops/program6-outcome-feedback-gate.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the Program 6 outcome fixture helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ready_request(p6, cur, joe_id, title: str, criteria: list[dict]):
    """Capture, triage, plan and accept one sourced Work Request to ready."""
    section, revision, origin_ref, runbook_ref = p6.fixture(cur, joe_id)
    set_local_role(cur, "carr_writer")
    request_id, ref, _, captured_version, *_ = p6.one(cur, """select * from ops.capture_sourced_work_request(
      %s,%s,%s,%s,%s,%s,%s)""", (origin_ref, title, "Observe bounded results", Jsonb(criteria),
      section, revision, uuid.uuid4()))
    cur.execute("reset role")
    triaged = p6.as_authority(cur, "dell",
      "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
      (ref, captured_version, uuid.uuid4()))
    set_local_role(cur, "carr_writer")
    plan = p6.one(cur, """select * from ops.propose_sourced_work_request_plan(
      %s,%s,%s,%s,%s,%s,%s,%s,%s)""", (ref, triaged[3], "Observe bounded results",
      runbook_ref, Jsonb([]), "safe:recovery:stop", "safe:observability:record",
      Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4()))
    cur.execute("reset role")
    ready = p6.as_authority(cur, "dell",
      "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
      (ref, triaged[3], plan[2], uuid.uuid4()))
    if ready[2] != "ready":
        raise RuntimeError(f"ready plan fixture failed: {ready}")
    return request_id, ref, ready[3], plan[2]


def propose_all(cur, ref, version, plan_hash, criterion_ids, outcome="met"):
    results = Jsonb([{"id": cid, "result": outcome} for cid in criterion_ids])
    blocker = "none" if outcome == "met" else "criterion_not_met"
    return cur.execute("""select * from ops.propose_sourced_work_request_outcome_feedback(
      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (ref, version, plan_hash, results, Jsonb(["safe:evidence:observed-result"]), blocker,
       "Observed the bounded results", 12, "mcp", False, 0, uuid.uuid4())).fetchone()


def act_as(cur, slug: str | None) -> None:
    cur.execute("select set_config('carr.acting_actor_slug', %s, true)", (slug or "",))


def bind(cur, ref, criterion, receipt, key=None):
    return cur.execute("select * from ops.bind_work_request_criterion_evidence(%s,%s,%s,%s)",
                       (ref, criterion, receipt, key or uuid.uuid4())).fetchone()


def complete(cur, ref, key=None):
    return cur.execute("select * from ops.complete_sourced_work_request_on_bound_evidence(%s,%s)",
                       (ref, key or uuid.uuid4())).fetchone()


def resolve_defect(cur, joe_id, defect_class: str) -> str:
    """One defect and its resolution receipt, written as carr_writer (close-defect's role)."""
    defect_id = cur.execute("""insert into public.defect (defect_class, claimed, actual, detected_by, created_by)
      values (%s,'a fixture claim','a fixture result','self',%s) returning id""",
      (defect_class, joe_id)).fetchone()[0]
    set_local_role(cur, "carr_writer")
    resolution_id = cur.execute("""insert into public.defect_resolution
      (defect_id, evidence_kind, evidence_ref, idempotency_key, resolved_by)
      values (%s,'commit','abc1234',%s,%s) returning id""",
      (defect_id, str(uuid.uuid4()), joe_id)).fetchone()[0]
    readback = cur.execute("select resolution_kind, resolution_ref from public.v_defect where id=%s",
                           (defect_id,)).fetchone()
    cur.execute("reset role")
    if readback != ("commit", "abc1234"):
        raise RuntimeError(f"v_defect does not show the resolution receipt: {readback}")
    return f"defect-resolution:{resolution_id}"


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        p6 = program6()
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            p6.authority_roles(cur)
            joe_id = p6.one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
            criteria = [{"id": "ALPHA", "text": "The first outcome has a receipt"},
                        {"id": "BETA", "text": "The second outcome has a receipt"}]
            request_id, ref, version, plan_hash = ready_request(p6, cur, joe_id, "Self-closing", criteria)
            receipt_a = resolve_defect(cur, joe_id, "self-closing-gate-a")
            receipt_b = resolve_defect(cur, joe_id, "self-closing-gate-b")

            set_local_role(cur, "carr_writer")
            act_as(cur, None)
            p6.refusal(cur, "select * from ops.bind_work_request_criterion_evidence(%s,'ALPHA',%s,%s)",
                       (ref, receipt_a, uuid.uuid4()), "binding without a server-derived actor")
            act_as(cur, "claude")
            p6.refusal(cur, "select * from ops.bind_work_request_criterion_evidence(%s,'GAMMA',%s,%s)",
                       (ref, receipt_a, uuid.uuid4()), "binding a criterion the request does not have")
            p6.refusal(cur, "select * from ops.bind_work_request_criterion_evidence(%s,'ALPHA',%s,%s)",
                       (ref, f"ops.run:{uuid.uuid4()}", uuid.uuid4()), "binding a run that does not exist")
            p6.refusal(cur, "select * from ops.bind_work_request_criterion_evidence(%s,'ALPHA','fixed it',%s)",
                       (ref, uuid.uuid4()), "binding a caller's own words")

            # No proposal yet: nothing completes, nothing is written.
            early = complete(cur, ref)
            if early[4] is not False or early[5] != ["criteria_met_outcome_proposal"]:
                raise RuntimeError(f"completion fired before any outcome proposal: {early}")

            alpha_key = uuid.uuid4()
            alpha = bind(cur, ref, "ALPHA", receipt_a, alpha_key)
            alpha_replay = bind(cur, ref, "ALPHA", receipt_a, alpha_key)
            if alpha[-1] is not False or alpha[5] != "defect_resolution" or alpha[6] != "claude":
                raise RuntimeError(f"binding readback is wrong: {alpha}")
            if alpha_replay[:-1] != alpha[:-1] or alpha_replay[-1] is not True:
                raise RuntimeError("binding did not replay exactly on its idempotency key")
            p6.refusal(cur, "select * from ops.bind_work_request_criterion_evidence(%s,'ALPHA',%s,%s)",
                       (ref, receipt_b, uuid.uuid4()), "a second, different receipt for a bound criterion")
            p6.refusal(cur, "update ops.work_request_criterion_evidence set receipt_ref='x' where id=%s",
                       (alpha[0],), "raw writer rewrite of a bound receipt")
            p6.refusal(cur, "insert into ops.work_request_criterion_evidence default values",
                       label="raw writer criterion evidence insert")

            # A criteria_met proposal with one criterion unbound must not complete.
            propose_all(cur, ref, version, plan_hash, ["ALPHA", "BETA"])
            partial = complete(cur, ref)
            if partial[4] is not False or partial[5] != ["BETA"] or partial[6] is not None:
                raise RuntimeError(f"completion fired with BETA unbound: {partial}")
            cur.execute("reset role")
            if cur.execute("""select count(*) from ops.sourced_work_request_outcome_feedback_acceptance_receipt
                               where work_request_id=%s""", (request_id,)).fetchone()[0] != 0:
                raise RuntimeError("an incomplete completion wrote an acceptance receipt")

            set_local_role(cur, "carr_writer")
            bind(cur, ref, "BETA", receipt_b)
            done_key = uuid.uuid4()
            done = complete(cur, ref, done_key)
            done_replay = complete(cur, ref, done_key)
            cur.execute("reset role")
            if done[4] is not True or done[9] != "completed_on_bound_evidence" or done[11] != "claude":
                raise RuntimeError(f"completion did not fire on complete bound evidence: {done}")
            if [r["receipt_ref"] for r in done[10]] != [receipt_a, receipt_b]:
                raise RuntimeError(f"completion receipt does not list its bound receipts: {done[10]}")
            if done_replay[:-1] != done[:-1] or done_replay[-1] is not True:
                raise RuntimeError("completion did not replay exactly on its idempotency key")
            stored = cur.execute("""select acceptance_basis, bound_receipt_refs
                from ops.sourced_work_request_outcome_feedback_acceptance_receipt where id=%s""",
                (done[6],)).fetchone()
            if stored != ("completed_on_bound_evidence", done[10]):
                raise RuntimeError(f"stored receipt disagrees with the readback: {stored}")
            if p6.pending(cur, ref) != []:
                raise RuntimeError("a completed request still offers pending outcome feedback")
            offered = [row[0] for row in cur.execute(
                "select ref from ops.current_sourced_work_requests('carr-internal')").fetchall()]
            if ref in offered:
                raise RuntimeError("a request completed on bound evidence is still offered as work")
            set_local_role(cur, "carr_writer")
            act_as(cur, "claude")
            p6.refusal(cur, "select * from ops.complete_sourced_work_request_on_bound_evidence(%s,%s)",
                       ("WR-999999999", done_key), "a completion key replayed for another request")
            cur.execute("reset role")

            # The human path is untouched: no bindings, a partner accepts, the
            # receipt says human_acceptance and carries no bound receipts.
            _, ref2, version2, plan_hash2 = ready_request(
                p6, cur, joe_id, "Human accepted", [{"id": "OBSERVED", "text": "One observed result"}])
            set_local_role(cur, "carr_writer")
            act_as(cur, "claude")
            proposal2 = propose_all(cur, ref2, version2, plan_hash2, ["OBSERVED"])
            unbound = complete(cur, ref2)
            cur.execute("reset role")
            if unbound[4] is not False or unbound[5] != ["OBSERVED"]:
                raise RuntimeError(f"completion fired on a request with no bindings: {unbound}")
            accepted2 = p6.accept(cur, ref2, version2, proposal2[2], uuid.uuid4(), "dell")
            human = cur.execute("""select acceptance_basis, bound_receipt_refs
                from ops.sourced_work_request_outcome_feedback_acceptance_receipt where feedback_id=%s""",
                (accepted2[4],)).fetchone()
            if human != ("human_acceptance", None):
                raise RuntimeError(f"the human acceptance receipt changed shape: {human}")

            # Append-only, even for the owner.
            p6.refusal(cur, "delete from public.defect_resolution where id=%s",
                       (receipt_a.split(":", 1)[1],), "deleting a defect resolution")
            p6.refusal(cur, "update public.defect_resolution set evidence_ref='def5678' where id=%s",
                       (receipt_a.split(":", 1)[1],), "rewriting a defect resolution")
            p6.refusal(cur, "delete from ops.work_request_criterion_evidence where id=%s",
                       (alpha[0],), "deleting a bound criterion receipt")
    except Exception as exc:
        return fail(str(exc))
    print("self-closing-records-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
