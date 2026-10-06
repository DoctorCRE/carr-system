#!/usr/bin/env python3
"""Hermetic contract tests for Observatory queue Slice 3.

No Hermes task is claimed and no model or named desk is contacted.  Fakes pin
the controller boundary: canonical claim first, one desk at a time, exact
terminal protocol, one terminal transition, and a bounded typed completion
callback rather than raw model prose.
"""

from __future__ import annotations

import json
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import queue_dispatch  # noqa: E402
import bridge  # noqa: E402
import desks  # noqa: E402
import dispatch  # noqa: E402
import flash_wire  # noqa: E402
import kanban_adapter  # noqa: E402
import state as state_mod  # noqa: E402


CATALOG: dict = {
    "v": 1,
    "targets": {
        "sol": {
            "enabled": True,
            "adapter": "desk",
            "assignee": "desk:codex-desk",
            "desk": "codex-desk",
            "effective_model": "gpt-5.6-sol",
            "capabilities": ["read"],
        },
        "claude": {
            "enabled": True,
            "adapter": "desk",
            "assignee": "desk:joe-desk",
            "desk": "joe-desk",
            "effective_model": "claude",
            "capabilities": ["read", "repo-write", "record-write"],
        },
        "grok": {
            "enabled": True,
            "adapter": "hermes",
            "assignee": "default",
            "effective_model": "Grok 4.7",
            "capabilities": ["read"],
        },
        "joe": {
            "enabled": True,
            "adapter": "manual",
            "assignee": "human:joe",
            "effective_model": "Joe manual lane",
            "capabilities": ["merge-approve", "production", "external-send", "destructive", "credential"],
        },
    },
}


def task(task_id: str = "t_queue0001", *, target: str = "sol", created_at: int = 2,
         finish: str = "done", cap: str = "read", body: str = "Inspect locally.") -> dict:
    meta = {
        "v": 1,
        "target": target,
        "cap": cap,
        "source_seq": 81,
        "source_msg_id": "11111111-1111-4111-8111-111111111111",
        "finish": finish,
    }
    entry = CATALOG["targets"][target]
    return {
        "id": task_id,
        "title": "Bounded queue task",
        "body": f"[CARR_QUEUE_META {json.dumps(meta, separators=(',', ':'))}]\n{body}",
        "assignee": entry["assignee"],
        "status": "ready",
        "created_at": created_at,
    }


def result(task_id: str = "t_queue0001", *, outcome: str = "success",
           summary: str = "Local attestation complete.", code: str | None = None,
           record_evidence: dict | None = None) -> str:
    payload = {"v": 1, "task_id": task_id, "outcome": outcome, "summary": summary}
    if code is not None:
        payload["code"] = code
    if record_evidence is not None:
        payload.update(record_evidence)
    return "Human-readable text stays at the desk.\nCARR_QUEUE_RESULT " + json.dumps(payload, separators=(",", ":"))


class FakeAdapter:
    def __init__(self, tasks: list[dict]):
        self.tasks = tasks
        self.calls: list[tuple] = []
        self.status = {row["id"]: row["status"] for row in tasks}

    def ready_for(self, assignee: str) -> list[dict]:
        self.calls.append(("ready_for", assignee))
        return list(self.tasks)

    def claim(self, task_id: str) -> None:
        self.calls.append(("claim", task_id))
        self.status[task_id] = "running"

    def show(self, task_id: str) -> dict:
        self.calls.append(("show", task_id))
        return {"task": {"id": task_id, "status": self.status[task_id]}}

    def comment(self, task_id: str, summary: str) -> None:
        self.calls.append(("comment", task_id, summary))

    def complete(self, task_id: str, summary: str, metadata: dict) -> None:
        self.calls.append(("complete", task_id, summary, metadata))
        self.status[task_id] = "done"

    def request_review(self, task_id: str, summary: str, metadata: dict) -> None:
        self.calls.append(("request_review", task_id, summary, metadata))
        self.status[task_id] = "review"

    def block(self, task_id: str, reason: str, *, kind: str | None = None) -> None:
        self.calls.append(("block", task_id, reason, kind))
        self.status[task_id] = "blocked"

    def retry_attempt(self, task_id: str, _prefix: str) -> tuple[int, int]:
        self.calls.append(("retry_attempt", task_id))
        return (0, 3)

    def reclaim(self, task_id: str, reason: str) -> None:
        self.calls.append(("reclaim", task_id, reason))
        self.status[task_id] = "ready"


class QueueDispatchTests(unittest.TestCase):
    def test_repository_catalog_keeps_profiles_mapped_and_ox_budget_gated(self):
        catalog = kanban_adapter.load_catalog()
        targets = catalog["targets"]
        self.assertEqual(targets["grok"]["assignee"], "desk:grok-desk")
        self.assertEqual(targets["grok"]["adapter"], "desk")
        self.assertEqual(targets["grok"]["desk"], "grok-desk")
        self.assertEqual(targets["kimi"]["assignee"], "designer")
        self.assertEqual(targets["deepseek"]["assignee"], "reviewer")
        self.assertEqual(targets["ox-alpha"]["assignee"], "builder")
        self.assertEqual(targets["ox-alpha"]["model"], "stealth/ox-alpha")
        self.assertEqual(targets["ox-alpha"]["provider"], "openrouter")
        self.assertIs(targets["ox-alpha"]["enabled"], False)
        self.assertIn("operator confirms provider capacity", targets["ox-alpha"]["unavailable_reason"])

    def test_kanban_mutations_use_supported_cli_only(self):
        json_calls: list[list[str]] = []
        mutation_calls: list[list[str]] = []

        def read_runner(argv: list[str]):
            json_calls.append(argv)
            if "list" in argv:
                return []
            return {"task": {"id": "t_queue0001", "status": "running"}}

        adapter = kanban_adapter.KanbanAdapter(
            runner=read_runner,
            command_runner=lambda argv: mutation_calls.append(argv) or "",
        )
        adapter.ready_for("desk:codex-desk")
        adapter.claim("t_queue0001")
        adapter.comment("t_queue0001", "Safe summary.")
        adapter.request_review("t_queue0001", "Safe summary.", {"target": "sol"})
        adapter.complete("t_queue0001", "Safe summary.", {"target": "sol"})
        adapter.block("t_queue0001", "dispatch_failed", kind="transient")
        self.assertIn("--status", json_calls[0])
        claim = mutation_calls[0]
        self.assertEqual(claim[claim.index("--ttl") + 1], "900")
        self.assertEqual([call[4] for call in mutation_calls], [
            "claim", "comment", "request-review", "complete", "block",
        ])
        self.assertTrue(all(call[:4] == ["hermes", "kanban", "--board", "carr-build"]
                            for call in mutation_calls))
        self.assertEqual(mutation_calls[4][4:], [
            "block", "--kind", "transient", "t_queue0001", "dispatch_failed",
        ])

    def test_catalog_maps_native_profiles_without_dispatching_them_as_desks(self):
        queue_dispatch.validate_execution_catalog(CATALOG)
        adapter = FakeAdapter([])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        self.assertEqual(controller.start("grok", dispatch_call=lambda _prompt: None),
                         {"outcome": "not_desk_target", "target": "grok"})
        self.assertEqual(adapter.calls, [])

    def test_manual_human_lane_can_never_be_auto_dispatched(self):
        adapter = FakeAdapter([task(target="joe", cap="merge-approve")])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "joe", dispatch_call=lambda _prompt: self.fail("manual work must never dispatch"))
        self.assertEqual(outcome, {"outcome": "not_desk_target", "target": "joe"})
        self.assertEqual(adapter.calls, [])

    def test_busy_desk_is_not_claimed_or_interrupted(self):
        adapter = FakeAdapter([task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        self.assertEqual(controller.start("sol", dispatch_call=lambda _prompt: None,
                                          desk_busy=True)["outcome"], "desk_busy")
        self.assertEqual(adapter.calls, [])

    def test_oldest_valid_card_is_claimed_before_dispatch(self):
        adapter = FakeAdapter([
            task("t_queue0002", created_at=9), task("t_queue0001", created_at=1),
        ])
        seen: list[str] = []

        def dispatch_call(prompt: str) -> dict:
            seen.append(prompt)
            self.assertEqual(adapter.calls[1], ("claim", "t_queue0001"))
            return {"status": "completed", "result": result("t_queue0001")}

        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=dispatch_call)
        self.assertEqual(outcome["task_id"], "t_queue0001")
        self.assertIn("CARR_QUEUE_RESULT", seen[0])
        self.assertIn('"v":1', seen[0])
        self.assertNotIn("Human-readable text stays at the desk", json.dumps(adapter.calls))

    def test_lost_claim_never_dispatches(self):
        adapter = FakeAdapter([task()])
        adapter.claim = lambda _task_id: (_ for _ in ()).throw(RuntimeError("already claimed"))
        dispatched: list[str] = []
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda prompt: dispatched.append(prompt) or {})
        self.assertEqual(outcome["outcome"], "claim_not_acquired")
        self.assertEqual(dispatched, [])

    def test_async_named_claude_desk_returns_pending_without_transition(self):
        adapter = FakeAdapter([task(target="claude")])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "claude", dispatch_call=lambda _prompt: {
                "status": "delivered", "msg_id": "dispatch-1", "dispatched_at": "2026-08-24T12:00:00+00:00",
            })
        self.assertEqual(outcome["outcome"], "pending")
        self.assertEqual(outcome["pending"]["kanban_task_id"], "t_queue0001")
        self.assertFalse(any(call[0] in {"complete", "request_review", "block"} for call in adapter.calls))

    def test_success_transitions_exactly_once_and_replay_is_noop(self):
        adapter = FakeAdapter([task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        pending = {"kanban_task_id": "t_queue0001", "target": "sol", "finish": "done"}
        first = controller.finish_pending(pending, result())
        second = controller.finish_pending(pending, result())
        self.assertEqual(first["outcome"], "done")
        self.assertEqual(second["outcome"], "already_terminal")
        self.assertEqual(first["completion"], second["completion"])
        self.assertEqual(first["completion"]["queue_completion"]["summary"], "Local attestation complete.")
        self.assertIn("do not merely acknowledge", first["completion"]["queue_completion"]["dispatcher_instruction"])
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 1)
        self.assertEqual(sum(call[0] == "comment" for call in adapter.calls), 0)

    def test_review_finish_uses_review_transition(self):
        adapter = FakeAdapter([task(finish="review")])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(outcome["outcome"], "review")
        self.assertEqual(sum(call[0] == "request_review" for call in adapter.calls), 1)
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 0)

    def test_malformed_or_mismatched_terminal_result_blocks_safely(self):
        for raw in ("no protocol", result("t_wrong0001"), result(outcome="unknown")):
            adapter = FakeAdapter([task()])
            outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
                "sol", dispatch_call=lambda _prompt, value=raw: {"status": "completed", "result": value})
            self.assertEqual(outcome["outcome"], "result_protocol_error")
            block = next(call for call in adapter.calls if call[0] == "block")
            self.assertEqual(block[2], "result_protocol_error")
            self.assertNotIn(raw, json.dumps(adapter.calls))

    def test_real_session_results_with_extra_fields_complete_as_success(self):
        """t_24b0a0c6 (V5-UX-C14) and t_a4765f1b (V5-UX-B04) opened their PRs
        and ended with a valid result line that also carried pr_url, verbs,
        gaps and room_seq.  The exact-field-set check blocked both as
        result_protocol_error (seq 43828, 43860).  Their real final texts,
        read from the transcripts, must now finish as success with their own
        summary, and the extra fields must not leak into the callback."""
        fixture = json.loads((HERE / "testdata" / "claude_desktop_real_capture.json").read_text())
        for task_id, raw in fixture["queue_final_texts"]["texts"].items():
            adapter = FakeAdapter([task(task_id, target="claude", cap="repo-write")])
            pending = {"kanban_task_id": task_id, "target": "claude-desktop", "finish": "done",
                       "cap": "repo-write", "source_seq": 43733, "source_msg_id": "source-message"}
            outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).finish_pending(
                pending, raw)
            self.assertEqual(outcome["outcome"], "done", task_id)
            callback = outcome["completion"]["queue_completion"]
            self.assertEqual(callback["outcome"], "success")
            self.assertTrue(callback["summary"].startswith("Opened"), callback["summary"])
            self.assertNotIn("code", callback)
            for extra in ("pr_url", "verbs", "gaps", "room_seq"):
                self.assertNotIn(extra, callback)
            complete = next(call for call in adapter.calls if call[0] == "complete")
            self.assertEqual(complete[2], callback["summary"])
            self.assertNotIn("pr_url", json.dumps(complete[3]))

    def test_extra_fields_never_excuse_a_broken_result(self):
        base = {"v": 1, "task_id": "t_queue0001", "outcome": "success",
                "summary": "Done.", "pr_url": "https://example.invalid/pr/1"}
        broken = [
            {k: v for k, v in base.items() if k != "summary"},          # required field absent
            {**base, "task_id": "t_other0001"},                          # another task
            {**base, "v": 2},                                             # wrong protocol version
            {**base, "outcome": "done"},                                  # outcome outside the enum
            {**base, "code": "capability_escalation_required"},           # code on a success
            {**base, "outcome": "blocked", "code": "made_up"},            # unknown block code
            {**base, "summary": "x" * 501},                               # oversize summary
        ]
        for payload in broken:
            raw = "prose\nCARR_QUEUE_RESULT " + json.dumps(payload)
            with self.assertRaises(queue_dispatch.QueueDispatchError, msg=payload):
                queue_dispatch.parse_terminal_result(raw, "t_queue0001", "repo-write")
        blocked = queue_dispatch.parse_terminal_result(
            "CARR_QUEUE_RESULT " + json.dumps({**base, "outcome": "blocked",
                                                "summary": "Could not open the PR."}),
            "t_queue0001", "repo-write")
        self.assertEqual(blocked, {"v": 1, "task_id": "t_queue0001", "outcome": "blocked",
                                   "summary": "Could not open the PR."})

    def test_record_write_still_needs_its_evidence_despite_extra_fields(self):
        evidence = {"mcp_verb": "update-lead", "record_id": "lead:123",
                    "readback_verb": "lead-board", "readback_record_id": "lead:123"}
        extra = {"pr_url": "https://example.invalid/pr/1"}
        missing = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        refused = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=missing).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed",
                                                     "result": result(record_evidence=extra)})
        self.assertEqual(refused["outcome"], "record_write_evidence_missing")
        verified = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        ok = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=verified).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed",
                                                     "result": result(record_evidence={**evidence, **extra})})
        self.assertEqual(ok["outcome"], "done")
        self.assertEqual(ok["completion"]["queue_completion"]["record_write"], evidence)

    def test_prompt_spells_out_the_exact_result_shape(self):
        adapter = FakeAdapter([task()])
        seen: list[str] = []
        queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda prompt: seen.append(prompt) or {"status": "completed",
                                                                        "result": result()})
        self.assertIn('CARR_QUEUE_RESULT {"v":1,"task_id":"t_queue0001","outcome":"success",'
                      '"summary":"<one sentence>"}', seen[0])
        self.assertIn("not in extra JSON fields", seen[0])

    def test_quota_failure_reclaims_with_closed_reason_and_bounded_backoff(self):
        adapter = FakeAdapter([task()])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", now="2026-08-24T12:00:00+00:00",
            dispatch_call=lambda _prompt: {"status": "quota_exhausted", "detail": "raw provider reply"},
        )
        self.assertEqual(outcome["outcome"], "retry_scheduled")
        self.assertEqual(outcome["code"], "provider_quota")
        self.assertEqual(outcome["retry_at"], "2026-08-24T12:00:30+00:00")
        self.assertIn(("reclaim", "t_queue0001", "queue_transient:provider_quota"), adapter.calls)
        self.assertFalse(any(call[0] == "block" for call in adapter.calls))
        self.assertNotIn("raw provider reply", json.dumps(adapter.calls))

    def test_retry_bound_is_canonical_and_nth_failure_blocks(self):
        adapter = FakeAdapter([task()])
        adapter.retry_attempt = lambda task_id, _prefix: (2, 3)
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", now="2026-08-24T12:00:00+00:00",
            dispatch_call=lambda _prompt: {"status": "quota_exhausted"},
        )
        self.assertEqual(outcome, {"outcome": "blocked", "task_id": "t_queue0001", "code": "provider_quota"})
        self.assertIn(("block", "t_queue0001", "provider_quota", "transient"), adapter.calls)
        self.assertFalse(any(call[0] == "reclaim" for call in adapter.calls))

    def test_missing_canonical_retry_evidence_fails_closed(self):
        adapter = FakeAdapter([task()])
        adapter.retry_attempt = lambda _task_id, _prefix: (_ for _ in ()).throw(
            kanban_adapter.QueueError("queue_unavailable", "Hermes response malformed"))
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", now="2026-08-24T12:00:00+00:00",
            dispatch_call=lambda _prompt: {"status": "quota_exhausted"},
        )
        self.assertEqual(outcome, {"outcome": "blocked", "task_id": "t_queue0001", "code": "queue_unavailable"})
        self.assertIn(("block", "t_queue0001", "queue_unavailable", "transient"), adapter.calls)

    def test_delayed_retry_does_not_head_of_line_block_later_ready_work(self):
        delayed = task(task_id="t_queue0001", created_at=1)
        ready = task(task_id="t_queue0002", created_at=2)
        adapter = FakeAdapter([delayed, ready])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", now="2026-08-24T12:00:00+00:00",
            retry_at={"t_queue0001": "2026-08-24T12:01:00+00:00"},
            dispatch_call=lambda _prompt: {"status": "completed", "result": result("t_queue0002")},
        )
        self.assertEqual(outcome["task_id"], "t_queue0002")
        self.assertIn(("claim", "t_queue0002"), adapter.calls)
        self.assertNotIn(("claim", "t_queue0001"), adapter.calls)

    def test_malformed_persisted_retry_time_fails_closed_without_dispatch(self):
        adapter = FakeAdapter([task()])
        dispatched = []
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", now="2026-08-24T12:00:00+00:00",
            retry_at={"t_queue0001": "not-a-time"},
            dispatch_call=lambda prompt: dispatched.append(prompt) or {"status": "completed", "result": result()},
        )
        self.assertEqual(outcome, {"outcome": "blocked", "task_id": "t_queue0001", "code": "queue_unavailable"})
        self.assertEqual(dispatched, [])
        self.assertIn(("block", "t_queue0001", "queue_unavailable", "transient"), adapter.calls)

    def test_timeout_uses_the_same_bounded_reclaim_path(self):
        adapter = FakeAdapter([task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        pending = {"kanban_task_id": "t_queue0001"}
        outcome = controller.fail_pending(pending, "desk_result_timeout", now="2026-08-24T12:00:00+00:00")
        self.assertEqual(outcome["outcome"], "retry_scheduled")
        self.assertEqual(outcome["code"], "desk_result_timeout")
        self.assertIn(("reclaim", "t_queue0001", "queue_transient:desk_result_timeout"), adapter.calls)

    def test_nth_timeout_blocks_without_reclaim(self):
        adapter = FakeAdapter([task()])
        adapter.retry_attempt = lambda _task_id, _prefix: (2, 3)
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        outcome = controller.fail_pending({"kanban_task_id": "t_queue0001"}, "desk_result_timeout",
                                          now="2026-08-24T12:00:00+00:00")
        self.assertEqual(outcome, {"outcome": "blocked", "task_id": "t_queue0001", "code": "desk_result_timeout"})
        self.assertIn(("block", "t_queue0001", "desk_result_timeout", "transient"), adapter.calls)
        self.assertFalse(any(call[0] == "reclaim" for call in adapter.calls))

    def test_declared_block_never_falls_back_to_another_target(self):
        adapter = FakeAdapter([task()])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda _prompt: {
                "status": "completed", "result": result(outcome="blocked", summary="Needs owner input."),
            })
        self.assertEqual(outcome["outcome"], "blocked")
        self.assertEqual(sum(call[0] == "block" for call in adapter.calls), 1)
        self.assertFalse(any("claude" in json.dumps(call) or "grok" in json.dumps(call)
                             for call in adapter.calls))

    def test_capability_escalation_blocks_canonically_without_retargeting(self):
        adapter = FakeAdapter([task()])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda _prompt: {
                "status": "completed", "result": result(outcome="blocked", code="capability_escalation_required",
                                                         summary="This needs record-write."),
            })
        self.assertEqual(outcome["outcome"], "blocked")
        self.assertIn(("block", "t_queue0001", "capability_escalation_required", "needs_input"), adapter.calls)
        self.assertFalse(any(call[0] in {"complete", "request_review", "ready_for"} and "joe" in json.dumps(call)
                             for call in adapter.calls))

    def test_record_write_success_needs_bounded_verb_and_readback_evidence(self):
        evidence = {"mcp_verb": "update-lead", "record_id": "lead:123",
                    "readback_verb": "read-lead", "readback_record_id": "lead:123"}
        missing = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        refused = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=missing).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(refused["outcome"], "record_write_evidence_missing")
        self.assertIn(("block", "t_queue0001", "record_write_evidence_missing", "needs_input"), missing.calls)
        review = FakeAdapter([task(target="claude", cap="record-write", finish="review")])
        reviewed = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=review).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(reviewed["outcome"], "review")
        review_call = next(call for call in review.calls if call[0] == "request_review")
        self.assertEqual(review_call[3]["outcome"], "unverified")
        verified = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        completed = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=verified).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result(record_evidence=evidence)})
        self.assertEqual(completed["outcome"], "done")
        complete = next(call for call in verified.calls if call[0] == "complete")
        self.assertEqual(complete[3]["record_write"], evidence)
        mismatch = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        mismatched = {**evidence, "readback_record_id": "lead:other"}
        out = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=mismatch).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result(record_evidence=mismatched)})
        self.assertEqual(out["outcome"], "record_write_evidence_missing")

    def test_queue_pending_result_returns_typed_callback_to_dispatcher_room(self):
        state = state_mod.default_state()
        state_mod.set_pending(
            state, "joe-desk", dispatch_msg_id="dispatch-1", log_offset=0,
            injected_at="2026-08-24T12:00:00+00:00", source_msg_id="queue:t_queue0001",
            source_seq=81, origin_kind="queue", kanban_task_id="t_queue0001",
            target="claude", finish="done",
        )
        finished: list[tuple] = []
        executor = type("Executor", (), {
            "finish_pending": lambda self, pending, raw: finished.append((pending, raw)) or {
                "outcome": "done", "task_id": "t_queue0001",
                "completion": {"queue_completion": {
                    "v": 1, "task_id": "t_queue0001", "target": "claude",
                    "outcome": "success", "summary": "bounded result",
                    "source_seq": 81, "source_msg_id": "source-message",
                }},
            },
        })()
        posted: list[dict] = []
        with tempfile.TemporaryDirectory() as root:
            outcome = bridge.handle_pending(
                "joe-desk", "claude", state, add_room_turn=lambda **kwargs: posted.append(kwargs),
                log_path=Path(root) / "desk.log", pending_timeout_s=60,
                scan=lambda _path, _offset: result(), queue_executor=executor,
            )
        self.assertEqual(outcome["outcome"], "done")
        self.assertEqual(len(finished), 1)
        self.assertEqual(len(posted), 1)
        callback = json.loads(posted[0]["body"])["queue_completion"]
        self.assertEqual(callback["task_id"], "t_queue0001")
        self.assertEqual(callback["summary"], "bounded result")
        self.assertNotIn("Human-readable text", posted[0]["body"])
        self.assertEqual(posted[0]["seat"], "claude")
        self.assertEqual(posted[0]["kind"], "turn")
        self.assertEqual(posted[0]["idempotency_key"], "queue-completion:t_queue0001")
        self.assertIsNone(state_mod.get_pending(state, "joe-desk"))

    def test_queue_timeout_blocks_canonically_without_echo(self):
        state = state_mod.default_state()
        state_mod.set_pending(
            state, "joe-desk", dispatch_msg_id="dispatch-1", log_offset=0,
            injected_at="2026-08-24T10:00:00+00:00", source_msg_id="queue:t_queue0001",
            source_seq=81, origin_kind="queue", kanban_task_id="t_queue0001",
            target="claude", finish="done",
        )
        failed: list[tuple] = []
        executor = type("Executor", (), {
            "fail_pending": lambda self, pending, reason, **_kwargs: failed.append((pending, reason)) or {
                "outcome": reason, "task_id": "t_queue0001"
            },
        })()
        posted: list[dict] = []
        outcome = bridge.handle_pending(
            "joe-desk", "claude", state, add_room_turn=lambda **kwargs: posted.append(kwargs),
            log_path=Path("unused"), pending_timeout_s=10, scan=lambda _path, _offset: None,
            now="2026-08-24T12:00:00+00:00", queue_executor=executor,
        )
        self.assertEqual(outcome["outcome"], "desk_result_timeout")
        self.assertEqual(failed[0][1], "desk_result_timeout")
        self.assertEqual(posted, [])

    def test_bridge_has_a_real_queue_execution_seam_after_room_fifo(self):
        source = inspect.getsource(bridge.run_once)
        self.assertIn("queue_executor.start", source)
        self.assertLess(source.index("state_mod.pop_next_queued"), source.index("queue_executor.start"))

    def test_bridge_posts_flash_locals_synchronous_completion_to_the_room(self):
        """flash-local completes inline in queue_executor.start() rather than through
        the pending/handle_pending path (finding 1): pins that run_once actually posts
        that completion instead of leaving it unposted.
        As of PR #1254 round 2 (finding 3), the post happens INSIDE start(), via the
        post_completion hook, before Hermes is marked terminal — not after start()
        returns — so this pins the wiring that makes that possible instead of a
        post-hoc call."""
        source = inspect.getsource(bridge.run_once)
        self.assertIn('entry.get("kind") == "flash-local"', source)
        self.assertIn('entry.get("kind") in {"flash-local", "grok-cli"}', source)
        self.assertIn("include_reply=publishes_sync_reply", source)
        self.assertIn("def post_sync_completion(completion: dict) -> None:", source)
        self.assertIn("completion, add_room_turn=add_room_turn, seat=post_seat)", source)
        self.assertIn("raise QueueCompletionPostFailed(str(exc)) from exc", source)
        self.assertIn("post_completion=post_sync_completion if publishes_sync_reply else None", source)
        self.assertLess(source.index("def post_sync_completion"),
                        source.index("queue_outcome = queue_executor.start"))

    def test_dead_socket_waits_without_claim_dispatch_or_retry_then_blocks_once(self):
        adapter = FakeAdapter([task()])
        dispatched = []
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        first = controller.start("sol", desk_live=False, now="2026-08-24T12:00:00+00:00",
                                 unavailable_wait_s=60, dispatch_call=lambda p: dispatched.append(p))
        self.assertEqual(first["outcome"], "desk_unavailable_wait")
        self.assertFalse(dispatched or any(c[0] in {"claim", "reclaim"} for c in adapter.calls))
        second = controller.start("sol", desk_live=False,
                                  unavailable_since={first["task_id"]: first["unavailable_since"]},
                                  now="2026-08-24T12:01:00+00:00", unavailable_wait_s=60,
                                  dispatch_call=lambda p: dispatched.append(p))
        self.assertEqual(second, {"outcome": "blocked", "task_id": "t_queue0001", "code": "desk_unavailable"})
        adapter.tasks = []  # Hermes no longer returns a terminal card as ready.
        third = controller.start("sol", desk_live=False,
                                 unavailable_since={first["task_id"]: first["unavailable_since"]},
                                 now="2026-08-24T12:02:00+00:00", unavailable_wait_s=60,
                                 dispatch_call=lambda p: dispatched.append(p))
        self.assertEqual(third["outcome"], "idle")
        self.assertEqual(sum(c[0] == "block" for c in adapter.calls), 1)

    def test_task_timer_does_not_transfer_when_old_card_disappears_and_fifo_recovers(self):
        adapter = FakeAdapter([task(task_id="t_queue0002", created_at=2)])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        out = controller.start("sol", desk_live=False,
                               unavailable_since={"t_queue0001": "2026-08-24T11:00:00+00:00"},
                               now="2026-08-24T12:00:00+00:00", unavailable_wait_s=60,
                               dispatch_call=lambda _p: None)
        self.assertEqual(out["task_id"], "t_queue0002")
        self.assertEqual(out["outcome"], "desk_unavailable_wait")
        recovered = controller.start("sol", desk_live=True, dispatch_call=lambda _p: {
            "status": "completed", "result": result("t_queue0002")})
        self.assertEqual(recovered["outcome"], "done")

    def test_malformed_or_future_unavailable_time_fails_closed(self):
        for value in ("not-a-time", "2026-08-24T13:00:00+00:00"):
            adapter = FakeAdapter([task()])
            out = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
                "sol", desk_live=False, unavailable_since={"t_queue0001": value},
                now="2026-08-24T12:00:00+00:00", dispatch_call=lambda _p: None)
            self.assertEqual(out, {"outcome": "blocked", "task_id": "t_queue0001", "code": "queue_unavailable"})
            self.assertFalse(any(c[0] in {"claim", "reclaim"} for c in adapter.calls))

    def test_codex_session_desk_bypasses_socket_liveness_gate(self):
        adapter = FakeAdapter([task()])
        out = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", desk_live=True, dispatch_call=lambda _p: {
                "status": "completed", "result": result()})
        self.assertEqual(out["outcome"], "done")
        self.assertIn(("claim", "t_queue0001"), adapter.calls)

    def test_busy_desk_preserves_fifo_without_queue_mutation(self):
        adapter = FakeAdapter([task(task_id="t_queue0001", created_at=1),
                               task(task_id="t_queue0002", created_at=2)])
        out = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", desk_busy=True, dispatch_call=lambda _p: None)
        self.assertEqual(out["outcome"], "desk_busy")
        self.assertFalse(any(c[0] in {"claim", "block", "reclaim"} for c in adapter.calls))

    def test_unavailable_timer_persists_across_restart(self):
        state = state_mod.default_state()
        state_mod.set_queue_unavailable_since(state, "t_queue0001", "2026-08-24T12:00:00+00:00")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            state_mod.save_state(path, state)
            restored = state_mod.load_state(path)
        self.assertEqual(restored["queue_unavailable_since"], {
            "t_queue0001": "2026-08-24T12:00:00+00:00"})

    def test_cycle_has_one_liveness_probe_source_and_reuses_result(self):
        source = inspect.getsource(bridge.run_once)
        self.assertEqual(source.count("probe_live("), 1)
        self.assertGreaterEqual(source.count("live_by_desk"), 3)
        self.assertLess(source.index("live_by_desk ="), source.index("queue_executor.start"))

    def test_disappeared_task_timer_is_pruned_only_after_complete_ready_scan(self):
        state = state_mod.default_state()
        state_mod.set_queue_unavailable_since(state, "t_queue0001", "2026-08-24T12:00:00+00:00")
        state_mod.prune_queue_unavailable_since(state, {"t_queue0002"})
        self.assertNotIn("t_queue0001", state["queue_unavailable_since"])

    def test_pruning_requires_reconciliation_and_every_configured_desk_scan(self):
        source = inspect.getsource(bridge.run_once)
        self.assertIn("not queue_reconciliation_failed", source)
        self.assertIn("queue_scanned_desks == required_queue_desks", source)
        self.assertIn("queue_scan_complete = False", source)
        self.assertIn("if name in required_queue_desks", source)
        self.assertIn("and required_queue_desks", source)


class FlashReplyReachesTheRoomTests(unittest.TestCase):
    """PR #1249 review finding 1: flash-local has no MCP tools of its own, so unlike a
    codex-session or claude-session desk (which post their own reply as part of doing
    the task) its answer was never posted anywhere — only the bounded completion summary
    was. These tests drive a realistic Flash reply through dispatch.dispatch (the real
    flash_wire wire, network faked) and queue_dispatch.parse_terminal_result (the real
    protocol parser) and assert the answer text itself reaches the typed room callback."""

    FLASH_CATALOG = {
        "v": 1,
        "targets": {
            "flash": {"enabled": True, "adapter": "desk", "assignee": "desk:flash-model",
                      "desk": "flash-model", "capabilities": ["read"], "effective_model": "flash"},
        },
    }

    @staticmethod
    def _flash_task(task_id: str = "t_queue0001") -> dict:
        meta = {"v": 1, "target": "flash", "cap": "read", "source_seq": 81,
                "source_msg_id": "11111111-1111-4111-8111-111111111111", "finish": "done"}
        return {
            "id": task_id, "title": "Answer this directly", "status": "ready", "created_at": 2,
            "assignee": "desk:flash-model",
            "body": f"[CARR_QUEUE_META {json.dumps(meta, separators=(',', ':'))}]\nWhere is loop 640 blocked?",
        }

    def test_flash_reply_reaches_the_room_as_the_tasks_result(self):
        answer = "Loop 640 is blocked on the Dell SSH alias; see decision 79110363 for the routing ruling."
        protocol_line = "CARR_QUEUE_RESULT " + json.dumps(
            {"v": 1, "task_id": "t_queue0001", "outcome": "success", "summary": "Answered directly."},
            separators=(",", ":"),
        )

        def fake_run_turn(task, **_kwargs):
            return {"status": "completed", "finish": "stop", "result": f"{answer}\n{protocol_line}"}

        real_run_turn = flash_wire.run_turn
        flash_wire.run_turn = fake_run_turn
        try:
            with tempfile.TemporaryDirectory() as tmp:
                registry = desks.Registry(Path(tmp) / "desks.json")
                registry.register("flash-model", "flash-local")

                def dispatch_call(prompt: str) -> dict:
                    # The real dispatch.dispatch(): resolves the registry entry, guards
                    # its named model/effort, and calls flash_wire.run_turn(prompt).
                    return dispatch.dispatch(
                        "flash-model", prompt, registry=registry,
                        results_path=Path(tmp) / "results.jsonl",
                    )

                adapter = FakeAdapter([self._flash_task()])
                controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
                outcome = controller.start(
                    "flash", dispatch_call=dispatch_call, include_reply=True, retry_protocol_errors=True,
                )
        finally:
            flash_wire.run_turn = real_run_turn

        self.assertEqual(outcome["outcome"], "done")
        callback = outcome["completion"]["queue_completion"]
        self.assertEqual(callback["reply"], answer)
        self.assertEqual(callback["summary"], "Answered directly.")
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 1)

    def test_reply_over_the_char_bound_is_truncated_with_a_pointer(self):
        """Fails if queue_dispatch._bounded_reply's 4,000-char truncation is removed
        (PR #1254 round 2, finding 4). Not redaction: the text is Flash's own prose
        verbatim up to the bound, with only the trailing protocol line stripped."""
        long_answer = "x" * 5000
        protocol_line = "CARR_QUEUE_RESULT " + json.dumps(
            {"v": 1, "task_id": "t_queue0001", "outcome": "success", "summary": "Long answer."},
            separators=(",", ":"),
        )
        adapter = FakeAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        outcome = controller.start(
            "flash", dispatch_call=lambda _prompt: {
                "status": "completed", "result": f"{long_answer}\n{protocol_line}",
            },
            include_reply=True,
        )
        reply = outcome["completion"]["queue_completion"]["reply"]
        self.assertLessEqual(len(reply), queue_dispatch.MAX_REPLY_CHARS + 200)
        self.assertLess(len(reply), len(long_answer))
        self.assertTrue(reply.startswith("x" * queue_dispatch.MAX_REPLY_CHARS))
        self.assertIn("truncated", reply)
        self.assertIn(queue_dispatch.REPLY_TRUNCATION_POINTER, reply)
        self.assertNotIn("CARR_QUEUE_RESULT", reply)

    def test_reply_under_the_char_bound_is_not_truncated(self):
        short_answer = "The tenant's option to renew runs through 2028."
        outcome = queue_dispatch.QueueDeskExecutor(
            catalog=self.FLASH_CATALOG, adapter=FakeAdapter([self._flash_task()]),
        ).start(
            "flash", dispatch_call=lambda _prompt: {
                "status": "completed",
                "result": short_answer + "\nCARR_QUEUE_RESULT " + json.dumps(
                    {"v": 1, "task_id": "t_queue0001", "outcome": "success", "summary": "Answered."},
                    separators=(",", ":"),
                ),
            },
            include_reply=True,
        )
        self.assertEqual(outcome["completion"]["queue_completion"]["reply"], short_answer)

    def test_other_desks_never_get_the_include_reply_treatment(self):
        """The 'never return model prose' rule is unchanged for a desk with its own MCP
        tools: without include_reply, no "reply" field is added to the callback."""
        outcome = queue_dispatch.QueueDeskExecutor(
            catalog=CATALOG, adapter=FakeAdapter([task()]),
        ).start("sol", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(outcome["outcome"], "done")
        self.assertNotIn("reply", outcome["completion"]["queue_completion"])

    def test_flash_result_protocol_error_retries_then_blocks_instead_of_blocking_once(self):
        adapter = FakeAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        outcome = controller.start(
            "flash", dispatch_call=lambda _prompt: {"status": "completed", "result": "no protocol line here"},
            retry_protocol_errors=True,
        )
        self.assertEqual(outcome["outcome"], "retry_scheduled")
        self.assertFalse(any(call[0] == "block" for call in adapter.calls))
        self.assertTrue(any(call[0] == "reclaim" for call in adapter.calls))

    def test_without_the_flag_a_protocol_error_still_blocks_once_unchanged(self):
        adapter = FakeAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        outcome = controller.start(
            "flash", dispatch_call=lambda _prompt: {"status": "completed", "result": "no protocol line here"},
        )
        self.assertEqual(outcome["outcome"], "result_protocol_error")
        self.assertEqual(sum(call[0] == "block" for call in adapter.calls), 1)

    def test_no_answer_gets_its_own_diagnosable_code_not_a_generic_one(self):
        adapter = FakeAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        outcome = controller.start(
            "flash", dispatch_call=lambda _prompt: {"status": "failed", "detail": "no_answer"},
        )
        self.assertEqual(outcome["outcome"], "retry_scheduled")
        self.assertEqual(outcome["code"], "no_answer")
        reclaim_reasons = [call[2] if len(call) > 2 else call for call in adapter.calls if call[0] == "reclaim"]
        self.assertTrue(any("no_answer" in str(reason) for reason in reclaim_reasons))

    def test_dispatch_still_routes_flash_local_desks_to_the_flash_wire(self):
        """Pins tools/room-bridge/dispatch.py's flash-local branch: a test that fails if
        it (or its wiring to flash_wire.run_turn) is deleted."""
        calls: list[str] = []

        def fake_run_turn(task, **_kwargs):
            calls.append(task)
            return {"status": "completed", "result": "42"}

        real_run_turn = flash_wire.run_turn
        flash_wire.run_turn = fake_run_turn
        try:
            with tempfile.TemporaryDirectory() as tmp:
                registry = desks.Registry(Path(tmp) / "desks.json")
                registry.register("flash-model", "flash-local")
                row = dispatch.dispatch(
                    "flash-model", "What is the answer?", registry=registry,
                    results_path=Path(tmp) / "results.jsonl",
                )
        finally:
            flash_wire.run_turn = real_run_turn
            self.assertEqual(calls, [desks.DESK_INSTRUCTION + "\n\nWhat is the answer?"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result"], "42")
        self.assertEqual(row["kind"], "flash-local")


class FinalCapabilityRecheckTests(unittest.TestCase):
    """PR #1249 review finding 3: a test that fails if kanban_adapter.py's handle()
    deletes its final capability recheck on the routed target=auto alias — covering the
    disabled-fallback case, where the FALLBACK target itself is also disabled or refuses
    the capability, and the whole command must be rejected rather than created anyway."""

    def test_auto_route_rejected_when_the_fallback_target_is_also_disabled(self):
        catalog = {
            "v": 1,
            "targets": {
                "flash": {"enabled": False, "adapter": "desk", "assignee": "desk:flash-model",
                          "desk": "flash-model", "capabilities": ["read"], "effective_model": "flash",
                          "unavailable_reason": "down for this test"},
                "claude-desktop": {"enabled": False, "adapter": "desk", "assignee": "desk:claude-desktop",
                                   "desk": "claude-desktop", "capabilities": ["read", "repo-write"],
                                   "effective_model": "opus", "unavailable_reason": "down for this test"},
            },
        }

        class FakeRouter:
            def load_policy(self):
                return {"queue_targets": {"direct": "flash", "fallback": "claude-desktop"}}

            def decide(self, title, body, *, flash_free=True, policy=None):
                return {"route": "direct", "model": "flash", "effort": "x", "scores": {},
                        "overflow": False, "jev_error": None}

        class FakeAdapterRefusesCreate:
            def create(self, *_args, **_kwargs):
                raise AssertionError("a rejected auto route must never create a Hermes task")

        service = kanban_adapter.QueueService(
            catalog=catalog, adapter=FakeAdapterRefusesCreate(), router=FakeRouter(),
            flash_up=lambda: True,
        )
        turn = {"body": "@queue enqueue target=auto cap=read :: Shorten this", "msg_id": "m1",
                "seat": "claude", "sponsor": "joe", "seq": 1, "origin_channel": "mcp", "origin_actor": "claude"}
        out = service.handle(turn, room="p")
        self.assertEqual(out["kind"], "rejected")
        self.assertEqual(out["receipt"]["queue_rejected"]["code"], "auto_route_unavailable")

    def test_handle_still_contains_its_final_capability_recheck(self):
        """A narrower pin alongside the behavioural test above: greps the actual guard
        clause so a refactor that quietly drops the recheck (while leaving some OTHER
        code accidentally passing the behavioural test) still turns this test red."""
        source = inspect.getsource(kanban_adapter.QueueService.handle)
        self.assertIn('not entry.get("enabled")', source)
        self.assertIn('command["cap"] not in entry.get("capabilities", [])', source)
        self.assertIn("auto_route_unavailable", source)


class SeatlessQueueDeskTests(unittest.TestCase):
    """PR #1254 round 2, finding 1: a queue-target desk with no room_seat (the
    Studio's real flash-model entry: {kind: flash-local, model: flash, effort:
    minimal}, no room_seat) was skipped ENTIRELY by run_once's per-desk loop
    ("if not seat: continue"), so its ready Hermes tasks were never claimed or
    dispatched. These drive bridge.run_once end to end with a fake queue
    executor standing in for QueueDeskExecutor."""

    @staticmethod
    def _seatless_flash_registry(root: Path):
        return type("Registry", (), {
            "entries": lambda self: {
                "flash-model": {"kind": "flash-local", "model": "flash", "effort": "minimal"},
            },
            "path": root / "desks.json",
        })()

    class FakeQueue:
        catalog = {"targets": {"flash": {"enabled": True, "adapter": "desk", "desk": "flash-model",
                                          "capabilities": ["read"]}}}

        def reconcile_disabled_targets(self):
            return {"scanned": 0, "blocked": [], "diagnostics": []}

    class FakeExecutor:
        def __init__(self):
            self.catalog = {"targets": {"flash": {"enabled": True, "adapter": "desk", "desk": "flash-model",
                                                   "capabilities": ["read"]}}}
            self.start_calls: list[tuple] = []

        def start(self, target_alias, *, post_completion=None, **_kwargs):
            self.start_calls.append(target_alias)
            completion = {"queue_completion": {
                "v": 1, "task_id": "t_flash0001", "target": target_alias, "outcome": "success",
                "summary": "Answered directly.", "reply": "42",
                "source_seq": 5, "source_msg_id": "m-source",
                "dispatcher_instruction": "Continue the originating workflow autonomously.",
            }}
            if post_completion is not None:
                post_completion(completion)
            return {"outcome": "done", "task_id": "t_flash0001", "completion": completion}

    def test_seatless_queue_target_desk_is_claimed_run_and_posted(self):
        """Fails on the pre-fix code: `continue` on a missing room_seat meant
        executor.start() was never called for this desk at all."""
        posted: list[dict] = []

        def add_room_turn(**kwargs):
            posted.append(kwargs)
            return {"seq": 1}

        saved_is_up = flash_wire.is_up
        flash_wire.is_up = lambda *_a, **_k: True
        try:
            with tempfile.TemporaryDirectory() as root:
                executor = self.FakeExecutor()
                summary = bridge.run_once(
                    state_path=Path(root) / "state.json",
                    read_room=lambda *_a, **_k: {"turns": []},
                    add_room_turn=add_room_turn,
                    registry=self._seatless_flash_registry(Path(root)),
                    queue_service=self.FakeQueue(),
                    queue_executor=executor,
                    queue_projector=lambda **_k: [],
                    probe_auth=lambda _entry: True,
                    read_profiles=lambda: [],
                    log=lambda _msg: None,
                )
        finally:
            flash_wire.is_up = saved_is_up

        self.assertEqual(executor.start_calls, ["flash"], summary)
        completions = [
            json.loads(p["body"]) for p in posted
            if p.get("kind") == "turn" and "queue_completion" in json.loads(p["body"])
        ]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]["queue_completion"]["reply"], "42")
        self.assertEqual(completions[0]["queue_completion"]["task_id"], "t_flash0001")

    def test_seatless_desk_never_receives_a_conversational_turn(self):
        """conversational_desk_seats() is the real gate: it must exclude a desk with
        no room_seat, or route_turn could queue a human turn onto it."""
        seats = bridge.conversational_desk_seats({
            "flash-model": {"kind": "flash-local", "model": "flash", "effort": "minimal"},
            "joe-desk": {"kind": "claude-session", "socket": "/tmp/x", "room_seat": "claude"},
        })
        self.assertNotIn("flash-model", seats)
        self.assertEqual(seats, {"joe-desk": "claude"})

    def test_a_conversational_turn_reaching_a_seatless_desk_fails_loudly_not_silently(self):
        """Defense in depth for the invariant above: if route_turn's own guard were
        ever bypassed and a turn DID land in a seatless desk's queue, run_once must
        refuse to answer it — never silently deliver a reply to a human turn from a
        desk that was never given a room identity."""
        posted: list[dict] = []

        def add_room_turn(**kwargs):
            posted.append(kwargs)
            return {"seq": 1}

        with tempfile.TemporaryDirectory() as root:
            state_path = Path(root) / "state.json"
            state = state_mod.default_state()
            state["desks"]["flash-model"] = {
                "delivered": [], "pending": None,
                "queue": [{"msg_id": "m1", "seat": "joe", "body": "What's 6 x 7?", "seq": 1}],
            }
            state_mod.save_state(state_path, state)
            summary = bridge.run_once(
                state_path=state_path,
                read_room=lambda *_a, **_k: {"turns": []},
                add_room_turn=add_room_turn,
                registry=self._seatless_flash_registry(Path(root)),
                queue_service=self.FakeQueue(),
                queue_executor=self.FakeExecutor(),
                queue_projector=lambda **_k: [],
                probe_auth=lambda _entry: True,
                read_profiles=lambda: [],
                log=lambda _msg: None,
            )
        self.assertTrue(
            any(e.get("desk") == "flash-model" for e in summary["errors"]), summary["errors"])
        self.assertFalse(any(p.get("body") not in (None,) and "42" == str(p.get("body")) for p in posted))
        self.assertFalse(any("queue_completion" not in str(p.get("body")) and p.get("seat") != "hermes"
                             for p in posted if p.get("kind") == "turn"))


class SynchronousCompletionPostOrderingTests(unittest.TestCase):
    """PR #1254 round 2, finding 3: on the synchronous flash-local path, the
    completion post used to happen AFTER finish_pending had already marked the
    Hermes task done/blocked — so a post failure lost the reply for good and
    (before this round's RuntimeError handling) aborted the whole bridge cycle."""

    FLASH_CATALOG = FlashReplyReachesTheRoomTests.FLASH_CATALOG
    _flash_task = staticmethod(FlashReplyReachesTheRoomTests._flash_task)

    def test_post_runs_before_the_hermes_task_is_marked_done(self):
        adapter = FakeAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        seen_before_complete: list[bool] = []

        def post_completion(_completion: dict) -> None:
            seen_before_complete.append(
                not any(call[0] == "complete" for call in adapter.calls))

        outcome = controller.start(
            "flash", dispatch_call=lambda _prompt: {"status": "completed", "result": result()},
            include_reply=True, post_completion=post_completion,
        )
        self.assertEqual(outcome["outcome"], "done")
        self.assertEqual(seen_before_complete, [True])

    def test_a_failed_post_never_marks_the_task_done_and_propagates_to_the_caller(self):
        adapter = FakeAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)

        def failing_post(_completion: dict) -> None:
            raise RuntimeError("add-room-turn unreachable")

        with self.assertRaises(RuntimeError):
            controller.start(
                "flash", dispatch_call=lambda _prompt: {"status": "completed", "result": result()},
                include_reply=True, post_completion=failing_post,
            )
        self.assertFalse(any(call[0] == "complete" for call in adapter.calls))
        self.assertFalse(any(call[0] == "block" for call in adapter.calls))

    def test_a_failed_flash_post_does_not_abort_the_cycle_for_other_desks(self):
        """The bridge level of the same fix: run_once must still process a SECOND
        queue-target desk in the same cycle after the FIRST one's completion post
        fails. Two flash-shaped desks share one executor and one catalog — the
        second's start() call proves the exception from the first's post never
        escaped its own per-desk try block (dict iteration order is deterministic:
        flash-model is registered, and therefore processed, before flash-model-2)."""
        posted: list[dict] = []

        class SharedExecutor:
            def __init__(self):
                self.catalog = {"targets": {
                    "flash": {"enabled": True, "adapter": "desk", "desk": "flash-model",
                             "capabilities": ["read"]},
                    "flash2": {"enabled": True, "adapter": "desk", "desk": "flash-model-2",
                              "capabilities": ["read"]},
                }}
                self.start_calls: list[str] = []

            def start(self, target_alias, *, post_completion=None, **_kwargs):
                self.start_calls.append(target_alias)
                if post_completion is not None:
                    post_completion({"queue_completion": {
                        "v": 1, "task_id": f"t_{target_alias}0001", "target": target_alias,
                        "outcome": "success", "summary": "x", "reply": "x",
                        "source_seq": 1, "source_msg_id": "m", "dispatcher_instruction": "x",
                    }})
                return {"outcome": "idle", "target": target_alias}

        class FakeQueue:
            catalog = None  # set below, shared with SharedExecutor

            def reconcile_disabled_targets(self):
                return {"scanned": 0, "blocked": [], "diagnostics": []}

        def failing_add_room_turn(**kwargs):
            key = kwargs.get("idempotency_key") or ""
            # Only the FIRST desk's completion post fails; the second's succeeds —
            # proving the first's RuntimeError was contained to its own desk.
            if key == "queue-completion:t_flash0001":
                raise RuntimeError("add-room-turn unreachable")
            posted.append(kwargs)
            return {"seq": 1}

        saved_is_up = flash_wire.is_up
        flash_wire.is_up = lambda *_a, **_k: True
        try:
            with tempfile.TemporaryDirectory() as root:
                registry = type("Registry", (), {
                    "entries": lambda self: {
                        "flash-model": {"kind": "flash-local", "model": "flash", "effort": "minimal"},
                        "flash-model-2": {"kind": "flash-local", "model": "flash", "effort": "minimal"},
                    },
                    "path": Path(root) / "desks.json",
                })()
                executor = SharedExecutor()
                queue = FakeQueue()
                queue.catalog = executor.catalog
                summary = bridge.run_once(
                    state_path=Path(root) / "state.json",
                    read_room=lambda *_a, **_k: {"turns": []},
                    add_room_turn=failing_add_room_turn,
                    registry=registry,
                    queue_service=queue,
                    queue_executor=executor,
                    queue_projector=lambda **_k: [],
                    probe_auth=lambda _entry: True,
                    read_profiles=lambda: [],
                    log=lambda _msg: None,
                )
        finally:
            flash_wire.is_up = saved_is_up

        self.assertEqual(executor.start_calls, ["flash", "flash2"], summary)
        self.assertTrue(
            any(e.get("desk") == "flash-model" and e.get("error") == "queue_completion_post_failed"
                for e in summary["errors"]), summary["errors"])
        # The SECOND desk's own completion post still landed this same cycle.
        self.assertTrue(any(
            p.get("idempotency_key") == "queue-completion:t_flash20001" for p in posted), posted)


class ReplyWordingTests(unittest.TestCase):
    """PR #1254 round 2, finding 4: _bounded_reply strips and truncates; it does not
    redact (no secret/PII scrubbing happens), and its own docstrings must say so."""

    def test_bounded_reply_docstring_does_not_overclaim_redaction(self):
        source = inspect.getsource(queue_dispatch._bounded_reply)
        # The old wording ("the desk's own prose, redacted of the protocol line")
        # claimed redaction as a positive attribute. A bare "not redaction"/"never
        # redacts" disclaimer is fine — what must never come back is a claim that
        # this function redacts anything.
        self.assertNotIn("prose, redacted", source.lower())
        self.assertNotIn("redacted exception", source.lower())
        self.assertIn("not redaction", source.lower())
        self.assertIn("stripped", source.lower())
        self.assertIn("truncat", source.lower())

    def test_completion_payload_docstring_does_not_overclaim_redaction_either(self):
        source = inspect.getsource(queue_dispatch.QueueDeskExecutor.completion_payload)
        self.assertNotIn("bounded, redacted exception", source.lower())
        self.assertIn("stripped and truncated", source.lower())


class KeyReuseRoom:
    """A fake add_room_turn that ENFORCES the server's idempotency rule
    (mcp-server/src/tools.js withEnvelope): the same idempotency_key with a
    different body raises key_reuse (verb_io surfaces that as RuntimeError), and
    the same key with the same body is a no-op replay. Posts without a key (the
    heartbeat, receipts) are simply recorded."""

    def __init__(self):
        self.by_key: dict[str, str] = {}
        self.turns: list[dict] = []
        self.replays: list[str] = []

    def __call__(self, **kwargs):
        key = kwargs.get("idempotency_key")
        body = kwargs.get("body")
        if key is not None:
            if key in self.by_key:
                if self.by_key[key] != body:
                    raise RuntimeError(f"add-room-turn failed: key_reuse ({key})")
                self.replays.append(key)
                return {"replayed": True}
            self.by_key[key] = body
        self.turns.append(kwargs)
        return {"seq": len(self.turns)}

    def completions(self) -> list[dict]:
        return [json.loads(t["body"])["queue_completion"] for t in self.turns
                if str(t.get("idempotency_key") or "").startswith("queue-completion:")]


class RetryCountingAdapter(FakeAdapter):
    """Hermes retry evidence that grows with each reclaim, like the real board."""

    def __init__(self, tasks: list[dict], *, limit: int = 3):
        super().__init__(tasks)
        self.limit = limit
        self.reclaims = 0

    def retry_attempt(self, task_id: str, _prefix: str) -> tuple[int, int]:
        self.calls.append(("retry_attempt", task_id))
        return (self.reclaims, self.limit)

    def reclaim(self, task_id: str, reason: str) -> None:
        super().reclaim(task_id, reason)
        self.reclaims += 1


class FinalCompletionOnlyTests(unittest.TestCase):
    """PR #1254 round 2 review, finding 1: the flash-local completion used to be
    posted BEFORE the result line was parsed, so a protocol error that scheduled a
    retry had already told the room "blocked: result_protocol_error", and the
    retried attempt's real completion then collided with that post under the one
    key queue-completion:<task_id> (server key_reuse). Only a FINAL completion may
    ever be posted, and still before the Hermes terminal mutation."""

    FLASH_CATALOG = FlashReplyReachesTheRoomTests.FLASH_CATALOG
    _flash_task = staticmethod(FlashReplyReachesTheRoomTests._flash_task)

    @staticmethod
    def _good(answer: str) -> str:
        return answer + "\nCARR_QUEUE_RESULT " + json.dumps(
            {"v": 1, "task_id": "t_queue0001", "outcome": "success", "summary": "Answered."},
            separators=(",", ":"))

    def _post(self, room: KeyReuseRoom):
        return lambda completion: bridge._post_completion_payload(
            completion, add_room_turn=room, seat="hermes")

    def test_protocol_error_retry_then_success_posts_one_final_completion(self):
        room = KeyReuseRoom()
        adapter = RetryCountingAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)

        first = controller.start(
            "flash", dispatch_call=lambda _p: {"status": "completed", "result": "An answer, no result line."},
            include_reply=True, retry_protocol_errors=True, post_completion=self._post(room),
        )
        self.assertEqual(first["outcome"], "retry_scheduled")
        self.assertEqual(room.completions(), [], "a retry must post nothing to the room")

        second = controller.start(
            "flash", dispatch_call=lambda _p: {"status": "completed", "result": self._good("The real answer.")},
            include_reply=True, retry_protocol_errors=True, post_completion=self._post(room),
        )
        self.assertEqual(second["outcome"], "done")
        completions = room.completions()
        self.assertEqual(len(completions), 1, completions)
        self.assertEqual(completions[0]["outcome"], "success")
        self.assertEqual(completions[0]["reply"], "The real answer.")
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 1)
        self.assertFalse(any(c.get("outcome") == "blocked" for c in completions))

    def test_retry_exhausted_block_posts_one_final_blocked_completion_before_the_block(self):
        room = KeyReuseRoom()
        adapter = RetryCountingAdapter([self._flash_task()], limit=3)
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        posted_before_block: list[bool] = []
        post = self._post(room)

        def recording_post(completion: dict) -> None:
            posted_before_block.append(not any(call[0] == "block" for call in adapter.calls))
            post(completion)

        outcomes = []
        # Flash's prose differs run to run; every attempt fumbles the result line.
        for attempt in range(3):
            outcomes.append(controller.start(
                "flash",
                dispatch_call=lambda _p, n=attempt: {"status": "completed", "result": f"Attempt {n} prose."},
                include_reply=True, retry_protocol_errors=True, post_completion=recording_post,
            ))
        self.assertEqual([o["outcome"] for o in outcomes],
                         ["retry_scheduled", "retry_scheduled", "blocked"])
        self.assertEqual(outcomes[-1]["code"], "result_protocol_error")
        completions = room.completions()
        self.assertEqual(len(completions), 1, completions)
        self.assertEqual(completions[0]["outcome"], "blocked")
        self.assertEqual(completions[0]["code"], "result_protocol_error")
        self.assertEqual(completions[0]["reply"], "Attempt 2 prose.")
        self.assertEqual(posted_before_block, [True])
        self.assertEqual(sum(call[0] == "block" for call in adapter.calls), 1)

    def test_a_failed_final_post_on_an_exhausted_retry_leaves_hermes_unblocked(self):
        adapter = RetryCountingAdapter([self._flash_task()], limit=1)
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)

        def failing_post(_completion: dict) -> None:
            raise RuntimeError("add-room-turn unreachable")

        with self.assertRaises(RuntimeError):
            controller.start(
                "flash", dispatch_call=lambda _p: {"status": "completed", "result": "no line"},
                include_reply=True, retry_protocol_errors=True, post_completion=failing_post,
            )
        self.assertFalse(any(call[0] in {"block", "complete"} for call in adapter.calls))

    def test_crash_after_post_replays_the_identical_final_body(self):
        """The post landed but the Hermes transition did not (crash); the same
        final result re-posts the identical body, which the server replays."""
        room = KeyReuseRoom()
        adapter = RetryCountingAdapter([self._flash_task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        real_complete = adapter.complete
        crashes = []

        def crashing_complete(*args):
            if not crashes:
                crashes.append(True)
                raise OSError("bridge killed mid-transition")
            return real_complete(*args)

        adapter.complete = crashing_complete  # type: ignore[method-assign]
        provider = mock.Mock(return_value={"status": "completed", "msg_id": "original-dispatch",
                                          "result": self._good("Same answer.")})
        with tempfile.TemporaryDirectory() as root:
            run = dict(dispatch_call=provider, completion_dir=Path(root),
                       include_reply=True, retry_protocol_errors=True, post_completion=self._post(room))
            with self.assertRaises(OSError):
                controller.start("flash", **run)
            # Restart after Hermes stale-claim recovery; the provider would
            # produce a fresh dispatch UUID if it were called again.
            adapter.status["t_queue0001"] = "ready"
            controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
            provider.return_value = {"status": "completed", "msg_id": "new-dispatch",
                                     "result": self._good("Different answer.")}
            self.assertEqual(controller.start("flash", **run)["outcome"], "done")
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(room.replays, ["queue-completion:t_queue0001"])
        self.assertEqual(len(room.completions()), 1)
        self.assertEqual(room.completions()[0]["dispatch_msg_id"], "original-dispatch")

    def test_recovery_replays_review_block_and_exhausted_protocol_terminal_intent(self):
        for terminal_method, raw, expected in (
                ("request_review", self._good("Review this."), "review"),
                ("block", result(outcome="blocked", summary="Cannot proceed."), "blocked"),
                ("block", "Final malformed reply.", "blocked")):
            with self.subTest(method=terminal_method, raw=raw), tempfile.TemporaryDirectory() as root:
                card = self._flash_task()
                if terminal_method == "request_review":
                    card["body"] = card["body"].replace('"done"', '"review"')
                room = KeyReuseRoom()
                adapter = RetryCountingAdapter([card], limit=1)
                original = getattr(adapter, terminal_method)
                setattr(adapter, terminal_method, mock.Mock(side_effect=OSError("transition failed")))
                controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
                provider = mock.Mock(return_value={"status": "completed", "msg_id": "original",
                                                  "result": raw})
                kwargs = dict(dispatch_call=provider, include_reply=True, retry_protocol_errors=True,
                              completion_dir=Path(root), post_completion=self._post(room))
                with self.assertRaises(OSError):
                    controller.start("flash", **kwargs)
                setattr(adapter, terminal_method, original)
                adapter.status[card["id"]] = "ready"
                controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
                # Recovery must not reconsult a changed protocol retry budget.
                adapter.retry_attempt = mock.Mock(side_effect=AssertionError("retry evidence denied"))
                recovered = controller.start("flash", desk_live=False, **kwargs)
                self.assertEqual(recovered["outcome"], expected)
                self.assertEqual(adapter.status[card["id"]], expected)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(len(room.completions()), 1)
                self.assertEqual(room.replays, ["queue-completion:t_queue0001"])

    def test_uncertain_publication_replays_original_body_without_new_execution(self):
        room = KeyReuseRoom()
        adapter = RetryCountingAdapter([self._flash_task()])
        provider = mock.Mock(return_value={"status": "completed", "msg_id": "original-dispatch",
                                          "result": self._good("Original answer.")})
        def uncertain_post(completion):
            self._post(room)(completion)
            raise RuntimeError("response lost after room accepted")
        with tempfile.TemporaryDirectory() as root:
            controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
            kwargs = dict(dispatch_call=provider, include_reply=True, completion_dir=Path(root))
            with self.assertRaises(RuntimeError):
                controller.start("flash", post_completion=uncertain_post, **kwargs)
            self.assertEqual(adapter.status["t_queue0001"], "running")
            adapter.status["t_queue0001"] = "ready"
            controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
            self.assertEqual(controller.start("flash", post_completion=self._post(room), **kwargs)["outcome"], "done")
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(len(room.completions()), 1)
        self.assertEqual(room.replays, ["queue-completion:t_queue0001"])

    def test_corrupt_or_misbound_final_receipt_refuses_fresh_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            room = KeyReuseRoom()
            card = self._flash_task()
            adapter = RetryCountingAdapter([card])
            adapter.complete = mock.Mock(side_effect=OSError("transition failed"))
            controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
            provider = mock.Mock(return_value={"status": "completed", "msg_id": "original",
                                              "result": self._good("Answer.")})
            kwargs = dict(dispatch_call=provider, include_reply=True, completion_dir=Path(root),
                          post_completion=self._post(room))
            with self.assertRaises(OSError):
                controller.start("flash", **kwargs)
            path = next(Path(root).glob("*.json"))
            original = path.read_text()
            wrong = json.loads(original)
            wrong["binding"]["source_msg_id"] = "another-source"
            for contents in ("not JSON", json.dumps(wrong)):
                path.write_text(contents)
                controller = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
                with self.assertRaises(queue_dispatch.QueueDispatchError):
                    controller.start("flash", **kwargs)
            self.assertEqual(provider.call_count, 1)

    def test_bridge_cycles_protocol_error_then_success_finish_the_task(self):
        """The same defect end to end through run_once: a seatless flash-local desk,
        the real executor, and a room that enforces key_reuse. Two cycles must
        finish the Hermes task with exactly one success completion and no
        queue_completion_post_failed error."""
        room = KeyReuseRoom()
        adapter = RetryCountingAdapter([self._flash_task()])
        executor = queue_dispatch.QueueDeskExecutor(catalog=self.FLASH_CATALOG, adapter=adapter)
        replies = iter(["An answer, no result line.", self._good("The real answer.")])

        class FakeQueue:
            catalog = self.FLASH_CATALOG

            def handle(self, _turn, *, room):
                return {"handled": False}

            def reconcile_disabled_targets(self):
                return {"scanned": 0, "blocked": [], "diagnostics": []}

        def dispatch_fn(name, _prompt, **_kwargs):
            self.assertEqual(name, "flash-model")
            return {"status": "completed", "result": next(replies)}

        saved_is_up = flash_wire.is_up
        flash_wire.is_up = lambda *_a, **_k: True
        summaries = []
        try:
            with tempfile.TemporaryDirectory() as root:
                registry = desks.Registry(Path(root) / "desks.json")
                registry.register("flash-model", "flash-local")
                self.assertFalse(registry.entries()["flash-model"].get("room_seat"))
                for cycle_now in ["2026-09-24T12:00:00+00:00", "2026-09-24T12:10:00+00:00"]:
                    summaries.append(bridge.run_once(
                        state_path=Path(root) / "state.json",
                        read_room=lambda *_a, **_k: {"turns": []},
                        add_room_turn=room, registry=registry,
                        queue_service=FakeQueue(), queue_executor=executor,
                        queue_projector=lambda **_k: [], dispatch_fn=dispatch_fn,
                        desk_state_dir=Path(root) / "desk-state",
                        probe_auth=lambda _entry: True, read_profiles=lambda: [],
                        now_fn=lambda n=cycle_now: n, log=lambda _msg: None,
                    ))
        finally:
            flash_wire.is_up = saved_is_up

        for summary in summaries:
            self.assertFalse(any(e.get("error") == "queue_completion_post_failed"
                                 for e in summary["errors"]), summary["errors"])
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 1, adapter.calls)
        completions = room.completions()
        self.assertEqual([c["outcome"] for c in completions], ["success"])
        self.assertEqual(completions[0]["reply"], "The real answer.")


class ConversationalRuntimeErrorTests(unittest.TestCase):
    """PR #1254 round 2 review, finding 2: the per-desk handler added for the
    flash-local completion post must not swallow any OTHER RuntimeError. A
    conversational reply that cannot be posted must abort the cycle before
    state is saved, so the dequeued turn is retried rather than lost."""

    def test_conversational_delivery_runtime_error_is_not_swallowed_and_turn_is_retried(self):
        class FakeQueue:
            catalog: dict = {"v": 1, "targets": {}}

            def handle(self, _turn, *, room):
                return {"handled": False}

            def reconcile_disabled_targets(self):
                return {"scanned": 0, "blocked": [], "diagnostics": []}

        class FakeExecutor:
            catalog: dict = {"targets": {}}

            def start(self, *_a, **_k):
                raise AssertionError("no queue target is configured")

        turn = {"seq": 7, "msg_id": "22222222-2222-4222-8222-222222222222",
                "seat": "joe", "kind": "turn", "body": "What is the renewal date?"}
        dispatched: list[str] = []

        def dispatch_fn(name, prompt, **_kwargs):
            dispatched.append(name)
            return {"status": "completed", "result": "It renews in 2028."}

        replies: list[dict] = []
        fail = [True]

        def add_room_turn(**kwargs):
            if kwargs.get("seat") == "sol" and fail[0]:
                raise RuntimeError("add-room-turn failed: network")
            replies.append(kwargs)
            return {"seq": 99}

        with tempfile.TemporaryDirectory() as root:
            registry = type("Registry", (), {
                "entries": lambda self: {"codex-desk": {"kind": "codex-session", "room_seat": "sol"}},
                "path": Path(root) / "desks.json",
            })()
            state_path = Path(root) / "state.json"

            def cycle():
                return bridge.run_once(
                    state_path=state_path,
                    read_room=lambda *_a, **_k: {"turns": [turn]},
                    add_room_turn=add_room_turn, registry=registry,
                    queue_service=FakeQueue(), queue_executor=FakeExecutor(),
                    queue_projector=lambda **_k: [], dispatch_fn=dispatch_fn,
                    desk_state_dir=Path(root) / "desk-state",
                    probe_auth=lambda _entry: True, read_profiles=lambda: [],
                    log=lambda _msg: None,
                )

            with self.assertRaises(RuntimeError):
                cycle()
            # Nothing was saved, so the dequeued turn is not recorded as consumed.
            self.assertFalse(state_path.exists())

            fail[0] = False
            summary = cycle()
        self.assertEqual(dispatched, ["codex-desk", "codex-desk"])
        self.assertTrue(any(r.get("seat") == "sol" and r.get("body") == "It renews in 2028."
                            for r in replies), replies)
        self.assertFalse(any(e.get("error") == "queue_completion_post_failed"
                             for e in summary["errors"]), summary["errors"])


def main() -> int:
    result = unittest.main(module=__name__, exit=False)
    return 0 if result.result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
