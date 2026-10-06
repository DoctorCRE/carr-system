#!/usr/bin/env python3
"""Offline tests for the Dr. CRE voice loop's shadow routing stamp (tools/doc-convo/bin/convo_core.py).

After each brain turn, convo_core records the routing decision ops/jev_model_route.py WOULD have made for the
utterance: one "doc-voice" row in out/model-routes.jsonl, written on a background thread. It is shadow-only: the
model that answers stays DOC_BRAIN_MODEL. These tests hold the properties that make that safe to run live:

  - the row carries a sha256 of the utterance and never the text;
  - a raising or slow router never raises into, or delays, the turn (timed with a router that sleeps);
  - DOC_ROUTE_SHADOW=0 turns it off;
  - the real policy file and the real dispatch() drive the default router, with a fake Jev.

No credential, no network, no `claude` process, no spend.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "doc-convo" / "bin"))
import convo_core  # noqa: E402

UTTERANCE = "What is the rent on the Daphne clinic lease?"
REPLY = "The Daphne clinic rent is on the deal card. Want me to read it?"


class FakeBrain:
    """Stands in for BrainProcess: streams one reply, returns like the real one."""

    def __init__(self, reply=REPLY, returncode=0, session_id="sess-123"):
        self.reply, self.returncode, self.session_id = reply, returncode, session_id

    def ask(self, text, system_prompt, on_text):
        on_text(self.reply)
        return subprocess.CompletedProcess([], self.returncode, self.reply, "")


def routed(route="direct", target="flash", model="haiku", fallback=False, scores=None, jev_error=None):
    return {"route": route, "target": target, "subagent_model": model, "effort": "low",
            "fallback": fallback, "scores": scores or {"code": 0.1, "direct": 0.9}, "jev_error": jev_error}


class ShadowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "out" / "model-routes.jsonl"
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("DOC_ROUTE_SHADOW", None)
        os.environ["CARR_JEV_SEMANTIC_CACHE"] = str(Path(self.tmp.name)/"cache.json")
        self.brain = patch.object(convo_core, "_BRAIN", FakeBrain())
        self.brain.start()

    def tearDown(self):
        self.brain.stop()
        self.env.stop()
        self.tmp.cleanup()

    def install(self, router):
        shadow = convo_core.RouteShadow(router=router, log_path=self.log)
        patcher = patch.object(convo_core, "_SHADOW", shadow)
        patcher.start()
        self.addCleanup(patcher.stop)
        return shadow

    def rows(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def turn(self):
        start = time.perf_counter()
        reply, brain = convo_core.ask_brain_streaming(UTTERANCE, "system", on_sentence=lambda _s: None)
        return reply, brain, time.perf_counter() - start


class RowShape(ShadowCase):
    def test_row_has_the_hash_and_never_the_text(self):
        shadow = self.install(lambda text: routed())
        reply, brain, _ = self.turn()
        self.assertEqual(reply, REPLY)
        self.assertTrue(shadow.drain(5))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "doc-voice")
        self.assertTrue(row["shadow"])
        self.assertEqual(row["utterance_sha256"], hashlib.sha256(UTTERANCE.encode("utf-8")).hexdigest())
        raw = self.log.read_text()
        self.assertNotIn(UTTERANCE, raw)
        self.assertNotIn("Daphne", raw, "no fragment of the utterance or reply may reach the log")
        self.assertNotIn("task", row, "jev_model_route's own rows carry task text; this row must not")

    def test_row_records_route_would_pick_used_model_abstain_scores_and_ids(self):
        shadow = self.install(lambda text: routed(route="escalate", target="claude-desktop", model="opus",
                                                  fallback=False, scores={"beyond": 0.8}))
        self.turn()
        self.assertTrue(shadow.drain(5))
        row = self.rows()[0]
        self.assertEqual(row["route"], "escalate")
        self.assertEqual(row["target"], "claude-desktop")
        self.assertEqual(row["would_model"], "opus")
        self.assertEqual(row["used_model"], convo_core.BRAIN_MODEL)
        self.assertFalse(row["abstained"])
        self.assertEqual(row["scores"], {"beyond": 0.8})
        self.assertEqual(row["session_id"], "sess-123")
        self.assertRegex(row["turn_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(row["brain_returncode"], 0)

    def test_each_turn_gets_its_own_row_and_turn_id(self):
        shadow = self.install(lambda text: routed())
        self.turn()
        self.turn()
        self.assertTrue(shadow.drain(5))
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["turn_id"], rows[1]["turn_id"])

    def test_empty_utterance_records_nothing(self):
        calls = []
        shadow = self.install(lambda text: calls.append(text) or routed())
        convo_core.ask_brain_streaming("   ", "system")
        self.assertTrue(shadow.drain(5))
        self.assertEqual(calls, [])
        self.assertEqual(self.rows(), [])


class NeverOnTheReplyPath(ShadowCase):
    SLEEP = 1.5

    def test_slow_router_adds_no_turn_latency(self):
        # Baseline: today's loop, shadow switched off.
        self.install(lambda text: routed())
        os.environ["DOC_ROUTE_SHADOW"] = "0"
        baseline = min(self.turn()[2] for _ in range(5))
        os.environ.pop("DOC_ROUTE_SHADOW")
        self.assertEqual(self.rows(), [])

        released = threading.Event()

        def slow(text):
            released.wait(self.SLEEP)
            return routed()

        shadow = self.install(slow)
        reply, brain, elapsed = self.turn()
        added = elapsed - baseline
        print(f"\n    doc-voice shadow: turn {elapsed * 1000:.2f} ms with a {self.SLEEP:.1f}s router, "
              f"baseline {baseline * 1000:.2f} ms, added {added * 1000:.2f} ms", file=sys.stderr)
        self.assertEqual(reply, REPLY)
        self.assertEqual(brain.returncode, 0)
        self.assertLess(elapsed, 0.05, "the turn waited on the router")
        released.set()
        self.assertTrue(shadow.drain(5))
        self.assertEqual(len(self.rows()), 1)

    def test_raising_router_never_breaks_the_turn(self):
        def boom(text):
            raise RuntimeError("jev is down")

        shadow = self.install(boom)
        reply, brain, _ = self.turn()
        self.assertEqual(reply, REPLY)
        self.assertEqual(brain.returncode, 0)
        self.assertTrue(shadow.drain(5))
        row = self.rows()[0]
        self.assertIsNone(row["route"])
        self.assertIn("RuntimeError", row["error"])
        self.assertEqual(row["used_model"], convo_core.BRAIN_MODEL)

    def test_a_backed_up_router_drops_rather_than_blocks(self):
        gate = threading.Event()
        shadow = self.install(lambda text: gate.wait(10) and routed())
        start = time.perf_counter()
        for _ in range(convo_core.RouteShadow.QUEUE_MAX + 5):
            self.turn()
        self.assertLess(time.perf_counter() - start, 0.5, "a full queue must drop, never wait")
        gate.set()
        self.assertTrue(shadow.drain(5))
        rows = self.rows()
        self.assertLess(len(rows), convo_core.RouteShadow.QUEUE_MAX + 5)
        self.assertGreater(sum(r.get("dropped_before", 0) for r in rows) + shadow.dropped, 0)

    def test_unwritable_log_is_swallowed(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        shadow = convo_core.RouteShadow(router=lambda text: routed(), log_path=blocker / "sub" / "log.jsonl")
        with patch.object(convo_core, "_SHADOW", shadow):
            reply, brain, _ = self.turn()
        self.assertEqual(reply, REPLY)
        self.assertTrue(shadow.drain(5))


class KillSwitch(ShadowCase):
    def test_zero_disables_it(self):
        calls = []
        shadow = self.install(lambda text: calls.append(text) or routed())
        os.environ["DOC_ROUTE_SHADOW"] = "0"
        reply, _, _ = self.turn()
        self.assertEqual(reply, REPLY)
        self.assertTrue(shadow.drain(5))
        self.assertEqual(calls, [])
        self.assertEqual(self.rows(), [])
        self.assertIsNone(shadow._worker, "disabled means no thread is ever started")

    def test_default_is_on(self):
        shadow = self.install(lambda text: routed())
        self.turn()
        self.assertTrue(shadow.drain(5))
        self.assertEqual(len(self.rows()), 1)

    def test_answering_model_is_unchanged_after_a_disagreeing_stamp(self):
        before = convo_core.BRAIN_MODEL
        other = "opus" if before != "opus" else "haiku"
        shadow = self.install(lambda text: routed(route="escalate", target="claude-desktop", model=other))
        self.turn()
        self.assertTrue(shadow.drain(5))
        self.assertEqual(self.rows()[0]["would_model"], other)
        self.assertEqual(convo_core.BRAIN_MODEL, before)
        # Build the REAL brain command the next turn would run and read its --model.
        captured = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                self.stdin = self.stdout = None
                self.stderr = iter(())

            def poll(self):
                return None

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(convo_core, "SESSION_FILE", Path(tmp) / "none"), \
                patch.object(convo_core.subprocess, "Popen", FakePopen), \
                patch.object(convo_core.credential_env, "claude_child_env", lambda: (dict(os.environ), None)):
            brain = convo_core.BrainProcess()
            brain._start("system")
            brain.process = None
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], before)


class RealPolicyRouter(ShadowCase):
    """The default router runs the real dispatch() over ops/config/model-routes.v1.json; only Jev is faked."""

    class FakeClient:
        @staticmethod
        def noul(instructions, true=None, false=None):
            return {"type": "noul"}

    class FakeJudge:
        def __init__(self, scores=None, error=None):
            self.scores, self.error, self.subjects = scores or {}, error, []

        def _client(self):
            return RealPolicyRouter.FakeClient

        def judge(self, subject, questions, timeout=None, client=None, **kwargs):
            self.subjects.append(subject)
            if self.error:
                raise self.error
            return {"model":"jev-1.13.0", "answers": {k: {"type":"noul","noul": self.scores.get(k, 0.0)} for k in questions}}

    def test_route_and_target_come_from_the_policy_file(self):
        judge = self.FakeJudge({"direct": 0.9})
        shadow = self.install(convo_core.jev_router(judge=judge))
        self.turn()
        self.assertTrue(shadow.drain(10))
        row = self.rows()[0]
        policy_bytes = (REPO / "ops" / "config" / "model-routes.v1.json").read_bytes()
        policy = json.loads(policy_bytes)
        self.assertEqual(row["route"], policy["abstain_route"])
        self.assertEqual(row["target"], policy["queue_targets"]["fallback"])
        self.assertEqual(row["would_model"], policy["dispatch_targets"][row["target"]]["subagent_model"])
        self.assertEqual(row["policy_version"], policy["version"])
        self.assertEqual(row["policy_sha256"], hashlib.sha256(policy_bytes).hexdigest())
        self.assertTrue(row["abstained"])
        self.assertEqual(judge.subjects[0]["task"], UTTERANCE, "Jev scores the utterance itself")

    def test_jev_down_is_the_policy_abstain_route(self):
        judge = self.FakeJudge(error=TimeoutError("jev timed out"))
        shadow = self.install(convo_core.jev_router(judge=judge))
        reply, _, _ = self.turn()
        self.assertEqual(reply, REPLY)
        self.assertTrue(shadow.drain(10))
        row = self.rows()[0]
        policy = json.loads((REPO / "ops" / "config" / "model-routes.v1.json").read_text())
        self.assertEqual(row["route"], policy["abstain_route"])
        self.assertTrue(row["abstained"])
        self.assertIn("TimeoutError", row["jev_error"])

    def test_a_vendor_error_that_echoes_the_request_never_reaches_the_log(self):
        # Review of #1323: jev_error carried up to 300 characters of the vendor's error body.
        echo = RuntimeError(f"TypeSafeError: TypeSafe returned HTTP 422: {{'input': {UTTERANCE!r}}}")
        shadow = self.install(convo_core.jev_router(judge=self.FakeJudge(error=echo)))
        self.turn()
        self.assertTrue(shadow.drain(10))
        raw = self.log.read_text()
        self.assertNotIn(UTTERANCE, raw)
        row = self.rows()[0]
        self.assertIn("HTTP 422", row["jev_error"])
        self.assertIn("TypeSafeError", row["jev_error"])

    def test_a_raising_router_logs_its_class_not_its_message(self):
        def router(text):
            raise ValueError(f"bad input {text}")
        shadow = self.install(router)
        self.turn()
        self.assertTrue(shadow.drain(10))
        self.assertNotIn(UTTERANCE, self.log.read_text())
        self.assertEqual(self.rows()[0]["error"], "ValueError")

    def test_router_never_writes_jev_model_routes_own_text_row(self):
        judge = self.FakeJudge({"direct": 0.9})
        router = convo_core.jev_router(judge=judge)
        with patch("builtins.open", wraps=open) as opened:
            router(UTTERANCE)
        written = [c for c in opened.call_args_list if len(c.args) > 1 and "a" in str(c.args[1])
                   and str(c.args[0]).endswith("model-routes.jsonl")]
        self.assertEqual(written, [], "dispatch() must run with log_path=None: its rows carry task text")


if __name__ == "__main__":
    unittest.main(verbosity=2)
