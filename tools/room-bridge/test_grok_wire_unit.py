"""Provider metadata, refusal, desk/bridge/auth integration without model spend."""

import copy
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auth_control
import bridge
import desks
import dispatch
import grok_desk
import grok_wire
import kanban_adapter
import queue_dispatch
from test_queue_dispatch_unit import FakeAdapter

ENTRY = {"kind": "grok-cli", "model": "grok-4.7", "effort": "high",
         "sandbox": "read-only", "cwd": "/tmp"}
END = {"type": "end", "stopReason": "end_turn", "requestId": "provider-request",
       "sessionId": "provider-session", "total_cost_usd": 0.01,
       "modelUsage": {"grok-4.7-build": {"modelCalls": 1}}}


def output(end=None, text="Safe Methods", before=None):
    return "\n".join(json.dumps(e) for e in [*(before or []),
                    {"type": "text", "data": text}, END if end is None else end])


class GrokTests(unittest.TestCase):
    def test_default_desk_refuses_unsafe_urls_without_provider_invocation(self):
        for url in ("https://fixture-user:fixture-secret@example.com/post",
                    "https://example.com/post?access_token=fixture-secret",
                    "http://127.0.0.1/source", "http://[::1]/source",
                    "http://service.internal/source"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as td:
                reg = desks.Registry(Path(td) / "desks.json")
                grok_desk.install(reg, td)
                provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0, output(), ""))
                execute = grok_wire.run_task
                with mock.patch.object(grok_wire, "run_task", side_effect=
                        lambda e, t: execute(e, t, run=provider)):
                    result = dispatch.dispatch("grok-desk", "Explain " + url,
                        registry=reg, results_path=Path(td) / "results.jsonl")
                self.assertEqual((result["status"], result["code"], result.get("detail")),
                                 ("failed", 6, "invalid_retrieval_url"))
                provider.assert_not_called()
                self.assertNotIn("fixture-user", json.dumps(result["receipt"]))
                self.assertNotIn("fixture-secret", json.dumps(result["receipt"]))

    def test_linked_explanation_remains_prose_through_model_room(self):
        with tempfile.TemporaryDirectory() as td:
            reg = desks.Registry(Path(td) / "desks.json")
            entry = grok_desk.install(reg, td)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0,
                output(text="409 means conflict."), ""))
            turns = []
            execute = grok_wire.run_task
            with mock.patch.object(grok_wire, "run_task", side_effect=
                    lambda e, t: execute(e, t, run=provider)):
                result = bridge.deliver("grok-desk", entry, "grok", {
                    "body": "Explain HTTP 409; see https://www.rfc-editor.org/rfc/rfc9110",
                    "seat": "claude", "msg_id": "explanation", "seq": 43}, state={},
                    registry=reg, results_path=Path(td) / "results.jsonl",
                    add_room_turn=lambda **kw: turns.append(kw))
            self.assertEqual(result["outcome"], "replied_sync")
            self.assertEqual(turns[-1]["body"], "409 means conflict.")

    def test_markdown_and_uppercase_urls_preserve_balanced_path(self):
        self.assertEqual(grok_wire.requested_urls(
            "Read `https://example.com/post` and **https://example.com/a**! "
            "and https://en.wikipedia.org/wiki/Foo_(bar) and HTTPS://EXAMPLE.COM/x"),
            ["https://example.com/post", "https://example.com/a",
             "https://en.wikipedia.org/wiki/Foo_(bar)", "HTTPS://EXAMPLE.COM/x"])

    def test_fenced_and_normalized_source_evidence_is_accepted(self):
        url = "https://example.com"
        for fence in (False, True):
            text = json.dumps({"retrieval": {"requested_urls": [url + "/"],
                "source_urls": [url + "/"], "sources": [{"url": url + "/", "text": "Source"}],
                "unresolved_portions": [], "status": "complete"}})
            if fence:
                text = "```json\n" + text + "\n```"
            result = grok_wire.parse_result(output(text=text), 0, urls=[url])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["retrieval"]["status"], "complete")

    def test_unrelated_or_self_written_artifact_never_counts_as_source(self):
        url = "https://example.com/source"
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "README.md").write_text("unrelated repo readme")
            text = json.dumps({"retrieval": {"requested_urls": [url], "source_urls": [url],
                "sources": [{"url": url, "artifact": "README.md"}],
                "unresolved_portions": [], "status": "complete"}})
            result = grok_wire.parse_result(output(text=text), 0, urls=[url])
            self.assertEqual(result["detail"], "unusable_retrieval")

    def test_query_credentials_and_nonpublic_hosts_are_refused(self):
        for url in ("https://example.com/?access_token=SECRET",
                    "https://bucket.s3.amazonaws.com/f?X-Amz-Signature=abc",
                    "https://example.com/?api%5fkey=SECRET", "http://localhost/source",
                    "http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data",
                    "http://[::1]/x", "http://10.0.0.1/x"):
            with self.subTest(url=url):
                self.assertFalse(grok_wire.public_url(url))
                self.assertEqual(grok_wire.retrieval_request_error([url])["detail"],
                                 "invalid_retrieval_url")

    def test_parser_retains_exit_code_for_every_failure(self):
        for raw, expected in ((output(), 0), (output(end={**END, "stopReason": "cancelled"}), 4),
                (output(end={**END, "modelUsage": {"wrong": {"modelCalls": 1}}}), 5),
                (output(text="Acknowledged"), 6)):
            result = grok_wire.parse_result(raw, 0, urls=["https://example.com"] if expected == 6 else None)
            self.assertEqual(result["code"], expected)

    def test_dispatch_explicitly_opts_into_retrieval_without_receipt_env_coupling(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "HOME": td, "GROK_RUN_RECEIPT": str(Path(td) / "runner.json")}):
            reg = desks.Registry(Path(td) / "desks.json")
            grok_desk.install(reg, td)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0,
                output(text=self.source_result()), ""))
            execute = grok_wire.run_task
            with mock.patch.object(grok_wire, "run_task", side_effect=
                    lambda e, t, **kw: execute(e, t, run=provider, **kw)):
                result = dispatch.dispatch("grok-desk", "Read https://example.com/post",
                    registry=reg, results_path=Path(td) / "results.jsonl", retrieval=True)
            self.assertEqual(result["retrieval"]["status"], "complete")
            self.assertFalse((Path(td) / "runner.json").exists())
            self.assertTrue(Path(result["diagnostic_path"]).is_file())

    def test_receipt_never_copies_structured_provider_diagnostics(self):
        private = {"diagnostic": "fixture-private-token"}
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"HOME": td}):
            result = grok_wire.run_task(ENTRY, "Read https://example.com/post", retrieval=True,
                run=lambda *a, **kw: subprocess.CompletedProcess([], 0,
                    output(end={**END, "total_cost_usd": private, "num_turns": private},
                           text=self.source_result()), ""))
            receipt = Path(result["diagnostic_path"]).read_text()
            self.assertNotIn("fixture-private-token", receipt)
            self.assertIsNone(json.loads(receipt)["cost_usd"])


    def test_credential_url_is_refused_without_invocation_or_secret_diagnostics(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "HOME": td}):
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0,
                output(text="No further action."), ""))
            result = grok_wire.run_task(ENTRY,
                "Retrieve https://fixture-user:fixture-secret@example.com/post", retrieval=True, run=provider)
            self.assertEqual(result["detail"], "invalid_retrieval_url")
            provider.assert_not_called()
            diagnostics = json.dumps(result) + Path(result["diagnostic_path"]).read_text()
            self.assertNotIn("fixture-user", diagnostics)
            self.assertNotIn("fixture-secret", diagnostics)

    def retrieval(self, text, *, end=None):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "HOME": td}):
            (Path(td) / "post.txt").write_text("Original post text")
            result = grok_wire.run_task({**ENTRY, "cwd": td}, "Retrieve post, thread and quoted source: https://example.com/post",
                run=lambda *a, **k: subprocess.CompletedProcess([], 0, output(end=end, text=text),
                    "Authorization: Bearer private-token"), retrieval=True)
            receipt = json.loads(Path(result["diagnostic_path"]).read_text())
            self.assertEqual(Path(result["diagnostic_path"]).stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-token", json.dumps(receipt))
        self.assertEqual(receipt["sandbox"], "read-only")
        self.assertEqual(receipt["effort"], "high")
        self.assertEqual(len(receipt["task_sha256"]), 64)
        return result, receipt

    def source_result(self, **changes):
        url = "https://example.com/post"
        return json.dumps({"retrieval": {"requested_urls": [url], "source_urls": [url],
            "sources": [{"url": url, "text": "Original post text", "artifact": "post.txt"}],
            "unresolved_portions": [], "status": "complete", **changes}})

    def test_completed_acknowledgment_is_unusable_retrieval(self):
        result, receipt = self.retrieval("No further action.")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["detail"], "unusable_retrieval")
        self.assertEqual(result["retrieval"]["status"], "unusable_retrieval")
        self.assertIn("canonical", result["next_route"])
        self.assertEqual(result["provider_metadata"]["actual_model"], "grok-4.7-build")
        self.assertEqual(receipt["detail"], "unusable_retrieval")

    def test_partial_thread_stays_partial_and_retains_evidence(self):
        result, receipt = self.retrieval(self.source_result(unresolved_portions=["quoted source unavailable"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["retrieval"]["status"], "partial")
        self.assertEqual(result["retrieval"]["unresolved_portions"], ["quoted source unavailable"])
        self.assertEqual(receipt["retrieval_status"], "partial")

    def test_valid_source_identifies_artifacts_and_urls(self):
        result, _ = self.retrieval(self.source_result())
        self.assertEqual(result["retrieval"]["status"], "complete")
        self.assertEqual(result["retrieval"]["sources"][0]["text"], "Original post text")
        self.assertNotIn("artifact", result["retrieval"]["sources"][0])
        self.assertEqual(result["retrieval"]["source_urls"], ["https://example.com/post"])

    def test_nonempty_prose_unrelated_source_and_bad_shape_are_unusable(self):
        for text in ("I retrieved everything at https://example.com/post", self.source_result(sources=[]),
                self.source_result(requested_urls=["https://example.com"]),
                self.source_result(sources=[{"url": "https://example.com", "text": "Other source"}]),
                self.source_result(sources=[{"url": "https://example.com/post"}]),
                self.source_result(source_urls="https://example.com/post"),
                self.source_result(status=[])):
            with self.subTest(text=text):
                result, _ = self.retrieval(text)
                self.assertEqual(result["detail"], "unusable_retrieval")

    def test_receipt_write_failure_refuses_completion_without_echoing_error(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "HOME": td}), mock.patch.object(grok_wire, "write_receipt", side_effect=OSError("private-token")):
            result = grok_wire.run_task(ENTRY, "Retrieve https://example.com/post",
                run=lambda *a, **k: subprocess.CompletedProcess([], 0, output(text=self.source_result()), ""), retrieval=True)
        self.assertEqual(result["detail"], "grok_receipt_unavailable")
        self.assertEqual(result["status"], "failed")

    def test_retrieval_contract_never_overrides_provider_failure(self):
        result, _ = self.retrieval(self.source_result(), end={**END, "modelUsage": {"grok-4.6": {"modelCalls": 1}}})
        self.assertEqual(result["detail"], "grok_provider_model_mismatch")
        self.assertEqual(result["status"], "failed")

    def test_artifact_only_source_is_always_refused(self):
        url = "https://example.com/post"
        result, _ = self.retrieval(self.source_result(sources=[{"url": url, "artifact": "post.txt"}]))
        self.assertEqual(result["detail"], "unusable_retrieval")
        for artifact in ("missing.txt", "../outside.txt", "/etc/hosts"):
            result, _ = self.retrieval(self.source_result(sources=[{"url": url, "artifact": artifact}]))
            self.assertEqual(result["detail"], "unusable_retrieval")

    def test_partial_multiple_urls_marks_unretrieved_requested_source(self):
        urls = ["https://example.com/post", "https://example.com/article"]
        text = self.source_result(requested_urls=urls, sources=[{"url": urls[0], "text": "Source"}])
        result = grok_wire.parse_result(output(text=text), 0, urls=urls)
        self.assertEqual(result["retrieval"]["status"], "partial")
        self.assertEqual(result["retrieval"]["unresolved_portions"], [urls[1]])

    def test_transport_failures_preserve_safe_receipts(self):
        for error, detail in ((FileNotFoundError("private-token"), "grok_cli_unavailable"),
                (subprocess.TimeoutExpired("private-token", 180), "grok_cli_timeout")):
            with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                    "HOME": td}):
                provider = mock.Mock(side_effect=error)
                result = grok_wire.run_task(ENTRY, "Retrieve https://example.com/source", retrieval=True, run=provider)
                receipt = Path(result["diagnostic_path"]).read_text()
            self.assertEqual(result["detail"], detail)
            self.assertNotIn("private-token", receipt)

    def test_model_room_failure_publishes_repair_route_and_diagnostic_path(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "HOME": td}):
            reg = desks.Registry(Path(td) / "desks.json")
            entry = grok_desk.install(reg, td)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0,
                output(text="No further action."), "private-token"))
            execute = grok_wire.run_task
            turns = []
            with mock.patch.object(grok_wire, "run_task", side_effect=lambda e, t: execute(e, t, retrieval=True, run=provider)):
                result = bridge.deliver("grok-desk", entry, "grok", {
                    "body": "Retrieve https://example.com/post", "seat": "claude",
                    "msg_id": "source-message", "seq": 42}, state={}, registry=reg,
                    results_path=Path(td) / "results.jsonl", add_room_turn=lambda **kw: turns.append(kw))
            self.assertEqual(result["outcome"], "failed:failed")
            receipt = json.loads(turns[-1]["body"])
            self.assertEqual(receipt["detail"], "unusable_retrieval")
            self.assertIn("canonical", receipt["next_route"])
            self.assertTrue(Path(receipt["diagnostic_path"]).is_file())
            self.assertNotIn("private-token", json.dumps(turns))

    def _run_queued_grok(self, *, fail_post=False, recover_transition=False, retrieval=False):
        answer = "Safe methods: https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1"
        url = "https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1"
        if retrieval:
            answer = json.dumps({"retrieval": {"requested_urls": [url],
                "sources": [{"url": url, "text": "Safe Methods"}], "source_urls": [url],
                "unresolved_portions": [], "status": "complete"}})
        protocol = 'CARR_QUEUE_RESULT {"v":1,"task_id":"t_grok0001","outcome":"success","summary":"Retrieved RFC."}'
        meta = {"v": 1, "target": "grok", "cap": "read", "finish": "done",
                "source_seq": 42, "source_msg_id": "source-message"}
        card = {"id": "t_grok0001", "status": "ready", "assignee": "desk:grok-desk",
                "created_at": 1, "title": "Retrieve RFC",
                "body": f"[CARR_QUEUE_META {json.dumps(meta)}]\nRetrieve safe methods." + (" " + url if retrieval else "")}
        catalog = kanban_adapter.load_catalog()
        adapter = FakeAdapter([card])
        adapter.reconcile_disabled_targets = lambda _catalog: {
            "scanned": 0, "blocked": [], "diagnostics": []}
        executor = queue_dispatch.QueueDeskExecutor(catalog=catalog, adapter=adapter)
        service = kanban_adapter.QueueService(catalog=catalog, adapter=adapter)
        posted = []
        completion_bodies = {}

        if recover_transition:
            complete = adapter.complete

            def crash_once(*args):
                adapter.complete = complete
                raise OSError("terminal transition unavailable")
            adapter.complete = crash_once

        def post(**kw):
            if "queue_completion" in json.loads(kw["body"]):
                self.assertEqual(adapter.status[card["id"]], "running")
                if fail_post:
                    raise RuntimeError("room publication unavailable")
                key = kw["idempotency_key"]
                if key in completion_bodies:
                    if completion_bodies[key] != kw["body"]:
                        raise RuntimeError("key_reuse")
                    return {"ok": True}
                completion_bodies[key] = kw["body"]
            posted.append(kw)
            return {"ok": True}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = desks.Registry(root / "desks.json")
            grok_desk.install(reg, str(root))
            # Exercise the real parser, dispatch, executor and bridge. Only the
            # provider process and external room/Hermes boundaries are fake.
            provider = mock.Mock(return_value=subprocess.CompletedProcess(
                [], 0, output(text=answer + "\n" + protocol), ""))
            run_task = grok_wire.run_task
            with mock.patch.object(grok_wire, "run_task", side_effect=
                    lambda entry, task: run_task(entry, task, run=provider)), \
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError("live process denied")), \
                    mock.patch.object(socket.socket, "connect", side_effect=AssertionError("live network denied")), \
                    mock.patch.dict(os.environ, {"CARR_ENGINEERING_DISPATCH_ENABLED": "false",
                        "GROK_RUN_RECEIPT": str(root / "retrieval-receipt.json")}):
                def cycle(executor):
                    return bridge.run_once(
                        registry=reg, state_path=root / "state.json", results_path=root / "results.jsonl",
                        desk_state_dir=root / "desk-state", read_room=lambda *_a, **_k: {"turns": []},
                        add_room_turn=post, queue_service=service, queue_executor=executor,
                        queue_projector=lambda **_k: [], probe_auth=lambda _e: True,
                        session_probe=lambda *_a, **_k: False, host="test-host",
                        read_profiles=lambda: [], log=lambda _m: None)
                if recover_transition:
                    with self.assertRaisesRegex(OSError, "terminal transition unavailable"):
                        cycle(executor)
                    self.assertEqual(adapter.status[card["id"]], "running")
                    # Hermes expires the workerless claim. A new process must
                    # finish the published execution without another model call.
                    adapter.status[card["id"]] = "ready"
                    executor = queue_dispatch.QueueDeskExecutor(catalog=catalog, adapter=adapter)
                summary = cycle(executor)
            row = json.loads((root / "results.jsonl").read_text().splitlines()[0])
        self.assertEqual(provider.call_count, 1)
        return summary, row, adapter, posted, answer

    def test_queued_result_is_published_with_provider_binding_before_done(self):
        summary, row, adapter, posted, answer = self._run_queued_grok()
        self.assertEqual(summary["errors"], [])
        completions = [p for p in posted if "queue_completion" in json.loads(p["body"])]
        self.assertEqual(len(completions), 1)
        callback = json.loads(completions[0]["body"])["queue_completion"]
        self.assertEqual(callback["reply"], answer)
        self.assertEqual(callback["provider_metadata"], {
            "requested_model": "grok-4.7", "actual_model": "grok-4.7-build",
            "effort": "high", "request_id": "provider-request",
            "session_id": "provider-session", "model_calls": 1,
            "cost_usd": 0.01, "stop_reason": "end_turn"})
        self.assertEqual(callback["dispatch_msg_id"], row["msg_id"])
        self.assertEqual((callback["source_msg_id"], callback["source_seq"]), ("source-message", 42))
        self.assertEqual(completions[0]["idempotency_key"], "queue-completion:t_grok0001")
        self.assertEqual(adapter.status["t_grok0001"], "done")

    def test_queued_url_retrieval_preserves_existing_completion_protocol(self):
        summary, _row, adapter, posted, _answer = self._run_queued_grok(retrieval=True)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(adapter.status["t_grok0001"], "done")
        callback = next(json.loads(p["body"])["queue_completion"] for p in posted
            if "queue_completion" in json.loads(p["body"]))
        self.assertEqual(json.loads(callback["reply"])["retrieval"]["status"], "complete")

    def test_queued_publication_failure_keeps_the_claim_nonterminal(self):
        summary, _row, adapter, posted, _answer = self._run_queued_grok(fail_post=True)
        self.assertEqual(summary["errors"], [{
            "desk": "grok-desk", "error": "queue_completion_post_failed",
            "detail": "room publication unavailable"}])
        self.assertEqual(adapter.status["t_grok0001"], "running")
        self.assertFalse(any(c[0] in {"complete", "block", "request_review"} for c in adapter.calls))
        self.assertFalse(any("queue_completion" in json.loads(p["body"]) for p in posted))
        self.assertTrue(any("heartbeat" in json.loads(p["body"]) for p in posted))

    def test_published_completion_survives_terminal_failure_and_bridge_restart(self):
        summary, row, adapter, posted, answer = self._run_queued_grok(recover_transition=True)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(adapter.status["t_grok0001"], "done")
        completions = [json.loads(p["body"])["queue_completion"] for p in posted
                       if "queue_completion" in json.loads(p["body"])]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]["reply"], answer)
        self.assertEqual(completions[0]["dispatch_msg_id"], row["msg_id"])
        self.assertEqual(completions[0]["provider_metadata"], row["provider_metadata"])

    def test_actual_model_comes_from_provider_and_text_is_joined(self):
        result = grok_wire.parse_result(output(before=[{"type": "text", "data": "RFC "}]), 0)
        self.assertEqual(result["result"], "RFC Safe Methods")
        self.assertEqual(result["provider_metadata"]["actual_model"], "grok-4.7-build")
        self.assertEqual(result["provider_metadata"]["request_id"], "provider-request")

    def test_recorded_turns_return_only_the_final_assistant_message(self):
        fixture = Path(__file__).resolve().parents[2] / "ops/fixtures/grok-run/live-ok.ndjson"
        result = grok_wire.parse_result(fixture.read_text(), 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], "OK")

    def test_final_message_keeps_chunks_and_intentional_repeated_text(self):
        raw = output(text="Final Final", before=[
            {"type": "text", "data": "Earlier answer"},
            {"type": "usage", "usage": {"output_tokens": 2}},
            {"type": "available_commands", "tools": []},
            {"type": "thought", "data": "Private reasoning"},
            {"type": "text", "data": "Final chunk: "},
        ])
        result = grok_wire.parse_result(raw, 0)
        self.assertEqual(result["result"], "Final chunk: Final Final")

    def test_usage_after_final_text_does_not_erase_the_answer(self):
        raw = "\n".join(json.dumps(e) for e in [
            {"type": "text", "data": "Final answer"},
            {"type": "usage", "usage": {"output_tokens": 2}}, END])
        self.assertEqual(grok_wire.parse_result(raw, 0)["result"], "Final answer")

    def test_accounting_after_response_boundary_preserves_completed_answer(self):
        for boundary in ({"messageId": "response", "stopReason": "end_turn"}, {}):
            with self.subTest(boundary=boundary):
                raw = "\n".join(json.dumps(e) for e in [
                    {"type": "text", "data": "Final answer"},
                    {"type": "usage", **boundary, "usage": {"output_tokens": 2}},
                    {"type": "usage", "usage": {"output_tokens": 2}},
                    {"type": "usage", "usage": {"output_tokens": 2}}, END])
                parsed = grok_wire.parse_stream(raw.splitlines())
                self.assertEqual(parsed["code"], 0)
                self.assertEqual(parsed["text"], "Final answer")
                result = grok_wire.parse_result(raw, 0)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["result"], "Final answer")

    def test_explicit_empty_response_does_not_reuse_an_earlier_answer(self):
        raw = "\n".join(json.dumps(e) for e in [
            {"type": "text", "data": "Earlier answer"},
            {"type": "usage", "messageId": "earlier"},
            {"type": "usage", "messageId": "empty-final", "stopReason": "end_turn"},
            {"type": "usage", "usage": {"output_tokens": 0}}, END])
        result = grok_wire.parse_result(raw, 0)
        self.assertEqual((result["status"], result["detail"], result["code"]),
                         ("failed", "grok_empty_result", 4))

    def test_refuses_absent_mismatched_mixed_and_incomplete_metadata(self):
        for models in (None, {}, {"grok-4.6": {"modelCalls": 1}},
                       {**END["modelUsage"], "grok-4.7-build-fast": {"modelCalls": 1}}):
            with self.subTest(models=models):
                self.assertEqual(grok_wire.parse_result(output({**END, "modelUsage": models}), 0)["status"], "failed")
        for changes in ({"stopReason": "max_turns"}, {"requestId": ""},
                        {"modelUsage": {"grok-4.7-build": {}}}):
            self.assertEqual(grok_wire.parse_result(output({**END, **changes}), 0)["status"], "failed")
        for raw, code in ((output(text=""), 0), (output(), 1), ('{"type":"text","data":"I am Grok 4.7"}', 0),
                          (output(before=[{"type": "error"}]), 0), (output()+ '\n{"type":"text","data":"late"}', 0)):
            self.assertEqual(grok_wire.parse_result(raw, code)["status"], "failed")

    def test_cli_posture_and_failure_have_no_raw_diagnostics(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, output(), "ignored private diagnostic")
        result = grok_wire.run_task(ENTRY, "read public source", run=run)
        argv, options = calls[0]
        self.assertEqual(argv[argv.index("--model")+1], "grok-4.7")
        self.assertIn("--always-approve", argv)
        self.assertEqual(argv[argv.index("--sandbox")+1], "read-only")
        self.assertEqual(argv[argv.index("--output-format")+1], "streaming-json")
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["timeout"], 180)
        self.assertNotIn("private-token", json.dumps(result))
        for error, status in ((FileNotFoundError(), "failed"), (subprocess.TimeoutExpired("grok", 180), "timed_out")):
            def fail(*a, **k):
                raise error
            self.assertEqual(grok_wire.run_task(ENTRY, "test", run=fail)["status"], status)

    def test_installer_registry_dispatch_and_bridge_keep_provider_binding(self):
        with tempfile.TemporaryDirectory() as td:
            reg = desks.Registry(Path(td)/"desks.json")
            reg.register("other-desk", "flash-local")
            other = copy.deepcopy(reg.entries()["other-desk"])
            entry = grok_desk.install(reg, "/tmp")
            self.assertEqual(reg.entries()["other-desk"], other)
            self.assertEqual(entry["room_listen"], "mention")
            result = grok_wire.parse_result(output(), 0)
            turns = []
            def post(**kw):
                turns.append(kw)
                return {"ok": True}
            with mock.patch.object(grok_wire, "run_task", return_value=result) as run:
                delivered = bridge.deliver("grok-desk", entry, "grok", {"body":"@grok retrieve", "seat":"claude",
                    "msg_id":"source-message", "seq":42}, state={}, registry=reg,
                    results_path=Path(td)/"results.jsonl", add_room_turn=post)
            self.assertEqual(delivered["outcome"], "replied_sync")
            self.assertEqual(run.call_count, 1)
            receipt = json.loads(turns[0]["body"])["grok_execution"]
            self.assertEqual(receipt["source_msg_id"], "source-message")
            self.assertEqual(receipt["source_seq"], 42)
            self.assertEqual(receipt["actual_model"], "grok-4.7-build")
            self.assertEqual(turns[1]["body"], "Safe Methods")
            row = json.loads((Path(td)/"results.jsonl").read_text())
            self.assertEqual(row["msg_id"], receipt["dispatch_msg_id"])
            heartbeat = json.loads(bridge.heartbeat_body(reg.entries(), 42, "now"))["heartbeat"]
            grok = next(e for e in heartbeat["desks"] if e["name"] == "grok-desk")
            self.assertEqual((grok["model"],grok["effort"]), ("grok-4.7","high"))

    def test_registry_refuses_widened_or_stale_grok_even_if_hand_edited(self):
        with tempfile.TemporaryDirectory() as td:
            reg = desks.Registry(Path(td)/"desks.json")
            for changes in ({"model":"grok-4.6"}, {"effort":"low"}, {"sandbox":"workspace"}):
                e = {**ENTRY, **changes}
                with self.assertRaises(ValueError):
                    grok_wire.run_task(e, "test", run=lambda *a,**k: self.fail("must not execute"))
                reg.path.write_text(json.dumps({"desks":{"grok-desk":e}}))
                with self.assertRaises(desks.DeskError):
                    reg.resolve("grok-desk")

    def test_auth_probe_uses_existing_login_and_no_login_launcher(self):
        def run(argv, **kw):
            self.assertEqual(argv, ["grok","models"])
            return subprocess.CompletedProcess(argv, 0, "You are logged in with grok.com.", "")
        self.assertTrue(auth_control.probe_auth(ENTRY, run=run))
        self.assertNotIn("grok-cli", auth_control.AUTH_LOGIN_COMMANDS)


if __name__ == "__main__":
    unittest.main()
