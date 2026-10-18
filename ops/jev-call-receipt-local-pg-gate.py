#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-PostgreSQL acceptance for the server-side Jev call log
(migration 0587: ops.jev_call_receipt, its write door
ops.record_jev_call_receipt, its read door ops.read_jev_call_receipts and its
integrity audit ops.jev_call_receipt_integrity).

WHY THIS EXISTS. mcp-server/test/jev-call-receipt.test.mjs exercises the verb
handlers against a JS fake of the doors. A fake cannot catch what only a real
catalog exposes (migration 0580's unqualified digest() under a pinned
search_path shipped exactly that way), and it cannot prove the properties the
Jev gates lean on. The store is DETECTABLE, NOT PREVENTED against its owner
(see the migration header); this gate proves the detection half:

  1. The write door works for carr_writer, stamps recorded_at from the server
     clock and records the server-derived actor (a slug that does not match
     the actor id is refused).
  2. The read door credits a receipt only when public.tool_call holds the
     matching ask-jev row (same key, verb, actor, and receipt_id in the
     stored response); a receipt inserted with no tool_call row is not
     returned, and neither is another actor's receipt.
  3. The read door returns the OLDEST `limit` rows from `since`, oldest
     first, with truncated=true when more exist.
  4. The integrity audit flags the uncredited receipt and reports the
     append-only triggers enabled, then reports them disabled after an owner
     disables one.
  5. No app role can touch the table directly; UPDATE and DELETE are refused
     by trigger even for the owner; prompt_sha256 is required exactly when
     purpose = 'build_advisory', at the door and by the table CHECK.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

H_STATE = "a" * 64
H_QUESTIONS = "b" * 64
H_ANSWERS = "c" * 64
H_PROMPT = "d" * 64
SESSION = f"jev-gate-{uuid.uuid4()}"
ANSWERS = {"q1": {"noul": 0.8}}


def fail(message: str) -> int:
    print(f"jev-call-receipt-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def make_actor(cur: psycopg.Cursor[Any], slug: str) -> str:
    row = cur.execute(
        "insert into public.actor (slug, kind, display_name) values (%s, 'automation', %s) returning id",
        (slug, slug),
    ).fetchone()
    if row is None:
        raise RuntimeError("actor insert returned no row")
    return str(row[0])


def record(cur: psycopg.Cursor[Any], actor: tuple[str, str], **overrides: Any) -> tuple[Any, ...]:
    args: dict[str, Any] = {
        "session_id": SESSION,
        "purpose": "call",
        "question_ids": ["q1"],
        "facets": ["diagnosis"],
        "model_requested": "jev-latest",
        "model_answered": "jev-1.14.0",
        "state_sha256": H_STATE,
        "questions_sha256": H_QUESTIONS,
        "answers_sha256": H_ANSWERS,
        "prompt_sha256": None,
        "answers": Jsonb(ANSWERS),
        "usage": Jsonb({"input_tokens": 3}),
        "actor_id": actor[0],
        "actor_slug": actor[1],
        "idempotency_key": str(uuid.uuid4()),
    }
    args.update(overrides)
    row = cur.execute(
        """select receipt_id, recorded_at, replayed from ops.record_jev_call_receipt(
             %(session_id)s, %(purpose)s, %(question_ids)s, %(facets)s,
             %(model_requested)s, %(model_answered)s, %(state_sha256)s,
             %(questions_sha256)s, %(answers_sha256)s, %(prompt_sha256)s,
             %(answers)s, %(usage)s, %(actor_id)s, %(actor_slug)s, %(idempotency_key)s)""",
        args,
    ).fetchone()
    if row is None:
        raise RuntimeError("record_jev_call_receipt returned no row")
    return (*row, args["idempotency_key"])


def ledger(cur: psycopg.Cursor[Any], actor_id: str, key: str, receipt_id: Any,
           cache_hit: bool = False) -> None:
    """The envelope row the Worker writes in the same transaction (as owner here)."""
    cur.execute(
        """insert into public.tool_call (idempotency_key, verb, actor_id, request_hash, response)
           values (%s, 'ask-jev', %s, 'gate', %s)""",
        (key, actor_id, Jsonb({"ok": True, "receipt_id": str(receipt_id),
                              "cache_hit": cache_hit})),
    )


def read(cur: psycopg.Cursor[Any], slug: str, since: str | None = None, limit: int = 200) -> dict[str, Any]:
    row = cur.execute(
        "select ops.read_jev_call_receipts(%s, %s::timestamptz, %s, %s)", (SESSION, since, limit, slug)
    ).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RuntimeError("read_jev_call_receipts returned no object")
    return row[0]


def integrity(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    row = cur.execute("select ops.jev_call_receipt_integrity()").fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RuntimeError("jev_call_receipt_integrity returned no object")
    return row[0]


def expect_refusal(
    cur: psycopg.Cursor[Any],
    fn: Callable[[], object],
    errors: tuple[type[BaseException], ...],
    expected_substring: str,
    label: str,
) -> None:
    """Run `fn` expecting one of `errors` mentioning `expected_substring`.

    Each expected refusal gets its own savepoint: a failed statement poisons
    the rest of a real PostgreSQL transaction until it is rolled back.
    """
    cur.execute(f"savepoint {label}")
    try:
        fn()
    except errors as exc:
        cur.execute(f"rollback to savepoint {label}")
        if expected_substring not in str(exc):
            raise RuntimeError(f"{label} refused with the wrong reason: {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {label}")
    raise RuntimeError(f"{label} was accepted; expected a refusal")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, "carr_writer", "carr_reader")
            me_slug = f"jev-gate-{uuid.uuid4().hex[:12]}"
            other_slug = f"jev-gate-{uuid.uuid4().hex[:12]}"
            me = (make_actor(cur, me_slug), me_slug)
            other = (make_actor(cur, other_slug), other_slug)
            baseline = integrity(cur)

            # A pre-call attempt remains visible as unknown spend until its
            # exact receipt is paired. A replay or another actor cannot settle it.
            set_local_role(cur, "carr_writer")
            attempt_key = f"jev-attempt:{uuid.uuid4()}"
            attempt_receipt = record(cur, me, idempotency_key=attempt_key,
                                     session_id=SESSION + "-attempt",
                                     model_answered="jev-attempt-pending", answers=Jsonb({}), usage=None)
            cur.execute("""insert into public.tool_call
                 (idempotency_key, verb, actor_id, request_hash, response)
                 values (%s, 'ask-jev-attempt', %s, %s, %s)""",
                (attempt_key, me[0], H_STATE,
                 Jsonb({"receipt_id": str(attempt_receipt[0]), "cache_hit": False})))
            cur.execute("reset role")
            pending_audit = integrity(cur)
            if pending_audit["receipts_without_tool_call"] != baseline["receipts_without_tool_call"]:
                raise RuntimeError("linked pending attempt was flagged as an integrity defect")
            pending = pending_audit["daily_usage"]
            if pending["unknown"] != baseline["daily_usage"]["unknown"] + 1 or \
                    pending["pending_attempts"] != baseline["daily_usage"]["pending_attempts"] + 1:
                raise RuntimeError(f"unsettled Jev attempt was not visible as unknown spend: {pending}")
            set_local_role(cur, "carr_writer")
            completed_receipt = record(cur, me, session_id=SESSION + "-attempt")
            ledger(cur, me[0], completed_receipt[3], completed_receipt[0])
            cur.execute("""update public.tool_call set response = %s
                 where idempotency_key = %s and verb = 'ask-jev-attempt'
                   and actor_id = %s and response->>'receipt_id' = %s
                   and response->>'cache_hit' = 'false' returning idempotency_key""",
                (Jsonb({"receipt_id": str(attempt_receipt[0]), "cache_hit": True,
                        "settled_by": str(completed_receipt[0])}),
                 attempt_key, me[0], str(attempt_receipt[0])))
            if cur.fetchone() != (attempt_key,):
                raise RuntimeError("pending attempt did not settle through the writer ledger")
            cur.execute("reset role")
            settled = integrity(cur)["daily_usage"]
            if settled["unknown"] != baseline["daily_usage"]["unknown"] or \
                    settled["pending_attempts"] != baseline["daily_usage"]["pending_attempts"]:
                raise RuntimeError(f"settled Jev attempt still counted as unknown: {settled}")
            # The extra receipt is not part of the historical gate assertions.
            baseline = integrity(cur)

            # 1. The write door, as carr_writer, with the server clock and actor.
            set_local_role(cur, "carr_writer")
            before = cur.execute("select clock_timestamp()").fetchone()
            first = record(cur, me)
            advisory_key = str(uuid.uuid4())
            second = record(cur, me, purpose="build_advisory", prompt_sha256=H_PROMPT,
                            facets=["architecture_or_design"], idempotency_key=advisory_key)
            replay = record(cur, me, purpose="build_advisory", prompt_sha256=H_PROMPT,
                            facets=["architecture_or_design"], idempotency_key=advisory_key)
            third = record(cur, me, usage=None)  # a cache hit has no vendor charge
            forged = record(cur, me)  # never gets a tool_call partner
            theirs = record(cur, other)
            expect_refusal(
                cur, lambda: record(cur, (me[0], other_slug)),
                (psycopg.errors.RaiseException,), "jev_call_receipt_actor_unresolved", "actor_slug_mismatch",
            )
            cur.execute("reset role")
            if before is None or not (first[1] >= before[0] and second[1] >= first[1]):
                raise RuntimeError(f"recorded_at is not the server clock in order: {before} {first} {second}")
            if first[2] is not False or second[2] is not False or replay[2] is not True or replay[0] != second[0]:
                raise RuntimeError(f"idempotent replay misbehaved: {first} {second} {replay}")
            for receipt, owner in ((first, me), (second, me), (third, me), (theirs, other)):
                ledger(cur, owner[0], receipt[3], receipt[0], cache_hit=receipt == third)

            metered = integrity(cur).get("daily_usage")
            if not isinstance(metered, dict) or metered.get("input_tokens") != baseline["daily_usage"]["input_tokens"] + 12 \
                    or metered.get("calls") != baseline["daily_usage"]["calls"] + 4 \
                    or metered.get("unknown") != baseline["daily_usage"]["unknown"]:
                raise RuntimeError(f"daily Worker usage omitted calls or counted a cache hit: {metered}")

            # 2/3. The read door, as carr_reader: credited rows only, mine only.
            set_local_role(cur, "carr_reader")
            payload = read(cur, me_slug)
            limited = read(cur, me_slug, limit=2)
            after_first = read(cur, me_slug, since=first[1].isoformat())
            other_view = read(cur, other_slug)
            cur.execute("reset role")
            receipts = payload["receipts"]
            ids = [r["receipt_id"] for r in receipts]
            if ids != [str(first[0]), str(second[0]), str(third[0])] or payload.get("truncated") is not False:
                raise RuntimeError(f"read door did not return exactly the credited receipts, oldest first: {payload}")
            if str(forged[0]) in ids or str(theirs[0]) in ids:
                raise RuntimeError("an uncredited or another actor's receipt was returned")
            if not payload.get("server_now") or not payload.get("since"):
                raise RuntimeError(f"read door omitted server_now/since: {payload}")
            call, advisory = receipts[0], receipts[1]
            expected_keys = {
                "receipt_id", "recorded_at", "purpose", "question_ids", "facets", "model",
                "state_sha256", "prompt_sha256", "answers",
            }
            if set(call) != expected_keys:
                raise RuntimeError(f"receipt shape drifted: {sorted(call)}")
            if call["purpose"] != "call" or call["answers"] is not None or call["prompt_sha256"] is not None:
                raise RuntimeError(f"a call receipt leaked answers or a prompt digest: {call}")
            if advisory["purpose"] != "build_advisory" or advisory["answers"] != ANSWERS:
                raise RuntimeError(f"a build_advisory receipt did not carry its answers: {advisory}")
            if advisory["prompt_sha256"] != H_PROMPT or advisory["model"] != "jev-1.14.0":
                raise RuntimeError(f"build_advisory receipt fields drifted: {advisory}")
            if [r["receipt_id"] for r in limited["receipts"]] != [str(first[0]), str(second[0])] \
                    or limited.get("truncated") is not True:
                raise RuntimeError(f"limit did not keep the OLDEST rows with truncated=true: {limited}")
            if len(after_first["receipts"]) != 3:
                raise RuntimeError(f"since is not inclusive: {after_first}")
            if [r["receipt_id"] for r in other_view["receipts"]] != [str(theirs[0])]:
                raise RuntimeError(f"the other actor's view is wrong: {other_view}")

            # 4. The integrity audit: the forged row is flagged; triggers enabled,
            # then disabled once the owner disables one.
            set_local_role(cur, "carr_reader")
            audit = integrity(cur)
            cur.execute("reset role")
            orphans = audit["receipts_without_tool_call"]
            if audit["receipts_total"] != baseline["receipts_total"] + 5:
                raise RuntimeError(f"receipts_total is wrong: {audit}")
            if orphans["count"] != baseline["receipts_without_tool_call"]["count"] + 1 \
                    or str(forged[0]) not in orphans["receipt_ids"]:
                raise RuntimeError(f"the uncredited receipt was not flagged: {audit}")
            if audit["trigger_enabled"] is not True or len(audit["triggers"]) != 2 or not audit.get("checked_at"):
                raise RuntimeError(f"triggers not reported enabled: {audit}")
            cur.execute("savepoint trigger_disabled")
            cur.execute("alter table ops.jev_call_receipt disable trigger jev_call_receipt_append_only")
            disabled = integrity(cur)
            cur.execute("rollback to savepoint trigger_disabled")
            if disabled["trigger_enabled"] is not False:
                raise RuntimeError(f"a disabled trigger was not reported: {disabled}")
            by_name = {t["name"]: t for t in disabled["triggers"]}
            if by_name["jev_call_receipt_append_only"]["tgenabled"] != "D":
                raise RuntimeError(f"tgenabled not reported: {disabled}")

            # 5a. No direct table privilege for any app role.
            for role in ("carr_writer", "carr_reader"):
                set_local_role(cur, role)
                for label, statement in (
                    ("select", "select count(*) from ops.jev_call_receipt"),
                    ("update", "update ops.jev_call_receipt set model_answered = 'x'"),
                    ("delete", "delete from ops.jev_call_receipt"),
                ):

                    def direct(statement: str = statement) -> object:
                        return cur.execute(statement)

                    expect_refusal(cur, direct, (psycopg.errors.InsufficientPrivilege,),
                                   "permission denied", f"{role}_direct_{label}")
                cur.execute("reset role")
            set_local_role(cur, "carr_reader")
            expect_refusal(cur, lambda: record(cur, me), (psycopg.errors.InsufficientPrivilege,),
                           "permission denied", "reader_cannot_write")
            cur.execute("reset role")

            # 5b. Append-only for everyone, the owner included.
            expect_refusal(
                cur,
                lambda: cur.execute(
                    "update ops.jev_call_receipt set model_answered = 'forged' where session_id = %s",
                    (SESSION,),
                ),
                (psycopg.errors.RaiseException,), "append-only", "owner_update_refused",
            )
            expect_refusal(
                cur,
                lambda: cur.execute("delete from ops.jev_call_receipt where session_id = %s", (SESSION,)),
                (psycopg.errors.RaiseException,), "append-only", "owner_delete_refused",
            )

            # 5c. prompt_sha256 iff build_advisory, at the door and in the table.
            set_local_role(cur, "carr_writer")
            expect_refusal(cur, lambda: record(cur, me, purpose="build_advisory", prompt_sha256=None),
                           (psycopg.errors.RaiseException,),
                           "jev_call_receipt_prompt_sha256_iff_build_advisory", "advisory_without_prompt")
            expect_refusal(cur, lambda: record(cur, me, purpose="call", prompt_sha256=H_PROMPT),
                           (psycopg.errors.RaiseException,),
                           "jev_call_receipt_prompt_sha256_iff_build_advisory", "call_with_prompt")
            expect_refusal(cur, lambda: record(cur, me, state_sha256="NOT-HEX"),
                           (psycopg.errors.CheckViolation,), "state_sha256", "bad_digest")
            cur.execute("reset role")
            expect_refusal(
                cur,
                lambda: cur.execute(
                    """insert into ops.jev_call_receipt (session_id, purpose, question_ids,
                         model_requested, model_answered, state_sha256, questions_sha256,
                         answers_sha256, prompt_sha256, answers, actor_id, actor_slug, idempotency_key)
                       values (%s, 'build_advisory', array['q1'], 'm', 'm', %s, %s, %s, null,
                         '{}'::jsonb, %s, %s, %s)""",
                    (SESSION, H_STATE, H_QUESTIONS, H_ANSWERS, me[0], me[1], str(uuid.uuid4())),
                ),
                (psycopg.errors.CheckViolation,), "jev_call_receipt_prompt_iff_build_advisory",
                "table_check_prompt_iff",
            )

        print(
            "PASS: jev-call-receipt real-PostgreSQL doors, tool_call-credited and actor-scoped reads, "
            "oldest-first truncation, integrity audit (uncredited row, trigger state), no direct grants, "
            "append-only trigger, prompt_sha256-iff-build_advisory: "
            + json.dumps({"credited": 3, "uncredited_flagged": 1})
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - gate contract is a printed failure, not a traceback
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
