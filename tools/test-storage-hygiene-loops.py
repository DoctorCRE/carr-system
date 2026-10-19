#!/usr/bin/env python3
"""Pins PR 1664's two post-merge findings in tools/storage_hygiene.py.

P1: every finding was filed with owner=orchestrator, which add-loop refuses as
unknown_owner. The accepted set is read from LOOP_OWNERS in
mcp-server/src/tools.js, not copied here.
P2: consecutive findings reused the episode idempotency key while the body
changed (key_reuse). The creation payload is now immutable per episode and the
changed facts travel through update-loop with a fresh key.
"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "storage_hygiene", ROOT / "tools" / "storage_hygiene.py")
assert SPEC is not None and SPEC.loader is not None
sh = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sh
SPEC.loader.exec_module(sh)


def loop_owners() -> set[str]:
    text = (ROOT / "mcp-server" / "src" / "tools.js").read_text(encoding="utf-8")
    match = re.search(r"const LOOP_OWNERS = Object\.freeze\(\[([^\]]*)\]\)", text)
    assert match, "LOOP_OWNERS not found in mcp-server/src/tools.js"
    owners = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert owners, "LOOP_OWNERS is empty"
    return owners


def plan(removable: int = 1, clones: int = 1) -> Any:
    p = sh.CleanupPlan()
    p.removable = [sh.Candidate("clone", Path(f"/nonexistent/c{i}"), 1.0)
                   for i in range(removable)]
    p.clone_count = clones
    return p


class StorageHygieneLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sh-loops-")
        self.ledger = Path(self.temp.name) / "ledger.jsonl"
        self.creates: list[dict] = []
        self.updates: list[tuple[dict, str]] = []

    def tearDown(self):
        self.temp.cleanup()

    def record(self, payload):
        self.creates.append(payload)
        return {"ok": True, "loop_id": "loop-1"}

    def update(self, loop, body):
        self.updates.append((loop, body))
        return {"ok": True}

    def run_once(self, p, disk_used=None):
        return sh.apply_plan(p, dry_run=False, ledger_path=self.ledger,
                             record_finding=self.record, update_finding=self.update,
                             disk_used=disk_used, is_open=lambda path: True,
                             is_locked=lambda path: True)

    def test_every_finding_owner_is_accepted_by_add_loop(self):
        accepted = loop_owners()
        for removable, disk in ((1, None), (3, sh.DATA_THRESHOLD_BYTES + 1)):
            sh.apply_plan(plan(removable), dry_run=False, ledger_path=self.ledger,
                          record_finding=self.record, disk_used=disk,
                          is_open=lambda path: True, is_locked=lambda path: True)
        self.assertTrue(self.creates)
        for payload in self.creates:
            self.assertIn(payload["owner"], accepted, payload)
        self.assertNotIn("orchestrator", accepted)

    def test_finding_payload_owner_is_accepted_directly(self):
        self.assertIn(sh._finding_payload(plan(), episode_key="k")["owner"], loop_owners())

    def test_consecutive_findings_replay_one_immutable_creation_payload(self):
        self.run_once(plan(removable=1), disk_used=1)
        self.run_once(plan(removable=4), disk_used=2)
        self.run_once(plan(removable=9), disk_used=3)
        self.assertEqual(len(self.creates), 3)
        self.assertEqual(self.creates[0], self.creates[1])
        self.assertEqual(self.creates[1], self.creates[2])
        self.assertEqual(len({p["idempotency_key"] for p in self.creates}), 1)
        self.assertEqual(len({p["body"] for p in self.creates}), 1)

    def test_changed_facts_go_through_update_not_a_reused_key(self):
        self.run_once(plan(removable=1), disk_used=1)
        self.assertEqual(self.updates, [])
        self.run_once(plan(removable=4), disk_used=2)
        self.assertEqual(len(self.updates), 1)
        loop, body = self.updates[0]
        self.assertEqual(loop["loop_id"], "loop-1")
        self.assertIn('"removable": 4', body)
        self.assertNotEqual(body, self.creates[0]["body"])

    def test_update_loop_call_uses_a_fresh_key_and_the_read_version(self):
        calls = []

        class Result:
            def __init__(self, reply=None, ok=True):
                self.reply, self.ok, self.kind = reply, ok, "ok"

            def describe(self):
                return "ok"

        def fake_call(verb, args, timeout=None):
            calls.append((verb, dict(args)))
            return Result({"loop": {"loop_id": "loop-1", "version": 7},
                           "amended": False, "amendments": []}
                          if verb == "read-loop" else {})

        import lib.record_call as rc
        original = rc.call_verb
        rc.call_verb = fake_call
        try:
            first = sh._update_finding({"loop_id": "loop-1"}, "b1")
            second = sh._update_finding({"loop_id": "loop-1"}, "b2")
        finally:
            rc.call_verb = original
        self.assertTrue(first["ok"] and second["ok"])
        updates = [a for v, a in calls if v == "update-loop"]
        self.assertEqual([u["base_version"] for u in updates], [7, 7])
        self.assertNotEqual(updates[0]["idempotency_key"], updates[1]["idempotency_key"])

    def test_refresh_refuses_malformed_read_before_writing(self):
        from lib.record_call import VerbResult
        for reply in (None, {}, {"version": 7}, {"loop": None},
                      {"loop": {"version": "7"}}, {"loop": {"version": True}}):
            with self.subTest(reply=reply), patch('lib.record_call.call_verb',
                    return_value=VerbResult('read-loop', 'ok', reply)) as call:
                answer = sh._update_finding({"loop_id": "loop-1"}, "new facts")
                self.assertFalse(answer["ok"])
                self.assertEqual(answer["status"], "refused")
                self.assertIn('loop.version', answer["detail"])
                call.assert_called_once_with('read-loop', {"loop_id": "loop-1"}, timeout=30)

    def test_refresh_preserves_read_failure_without_writing(self):
        from lib.record_call import VerbResult
        with patch('lib.record_call.call_verb', return_value=VerbResult(
                'read-loop', 'transient', detail='unavailable')) as call:
            answer = sh._update_finding({"loop_id": "loop-1"}, "new facts")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], "transient")
        self.assertIn('unavailable', answer["detail"])
        self.assertEqual(call.call_count, 1)

    def test_ledger_carries_the_creation_payload_across_runs(self):
        self.run_once(plan(removable=1))
        self.run_once(plan(removable=2))
        rows = [json.loads(line) for line in self.ledger.read_text().splitlines()]
        self.assertEqual(rows[0]["finding_creation"], rows[1]["finding_creation"])
        self.assertEqual(rows[0]["finding_episode"], rows[1]["finding_episode"])

    def test_failed_update_marks_the_run_record_failed(self):
        self.update = lambda loop, body: {"ok": False}
        self.run_once(plan(removable=1))
        result = self.run_once(plan(removable=2))
        self.assertEqual(result.record_status, "record-failed")


if __name__ == "__main__":
    unittest.main()
