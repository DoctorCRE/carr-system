#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_wire  # noqa: E402
import write_ownership  # noqa: E402
from test_codex_live_unit import FakeAppServer  # noqa: E402


class DeadlineInterruptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-deadline-")
        self.root = Path(self.tmp.name)
        self.servers: list[FakeAppServer] = []

    def tearDown(self):
        for srv in self.servers:
            srv.close()
        self.tmp.cleanup()

    def serve(self, name: str, **kw) -> tuple[str, FakeAppServer]:
        path = str(self.root / name)
        srv = FakeAppServer(path, silent_turn=True, **kw)
        self.servers.append(srv)
        return path, srv

    def run_deadline(self, path: str) -> dict:
        with patch.object(codex_wire, "CANCEL_TIMEOUT_S", 0.2, create=True):
            return codex_wire.run_turn(path, "build", deadline_s=0.2)

    def test_deadline_interrupts_the_exact_active_turn(self):
        path, srv = self.serve("exact.sock")
        row = self.run_deadline(path)
        self.assertEqual(row["status"], "timed_out", row)
        interrupts = [m for m in srv.seen if m.get("method") == "turn/interrupt"]
        self.assertEqual(len(interrupts), 1, srv.methods())
        self.assertEqual(interrupts[0]["params"],
                         {"threadId": "thread-live-0001", "turnId": "turn-1"})
        self.assertEqual(row["turn_id"], "turn-1")

    def test_interrupt_acknowledgement_alone_does_not_confirm_termination(self):
        path, _ = self.serve("ack.sock")
        row = self.run_deadline(path)
        self.assertIs(row["termination_confirmed"], False, row)
        self.assertIn("unconfirmed", row["detail"])

    def test_terminal_notification_confirms_termination(self):
        path, _ = self.serve("confirm.sock", finish_on_interrupt=True)
        row = self.run_deadline(path)
        self.assertEqual(row["status"], "timed_out", row)
        self.assertIs(row["termination_confirmed"], True, row)

    def test_a_different_turn_terminating_does_not_confirm(self):
        message = {"method": "turn/completed", "params": {
            "threadId": "thread-live-0001",
            "turn": {"id": "other-turn", "status": "interrupted"}}}
        self.assertFalse(codex_wire._terminal(message, "thread-live-0001", "turn-1"))
        message["params"]["turn"]["id"] = "turn-1"
        self.assertTrue(codex_wire._terminal(message, "thread-live-0001", "turn-1"))

    def test_write_ownership_is_released_only_on_terminal_confirmation(self):
        path, srv = self.serve("claim.sock")
        row = {"launch_marker": "m", "ownership_state": "held",
               "executor": {"kind": "codex_turn", "socket": path,
                            "thread_id": "thread-live-0001", "turn_id": "turn-1"}}
        self.assertIsNone(write_ownership.termination_evidence(row))
        srv.silent_turn = False
        self.assertEqual(write_ownership.termination_evidence(row),
                         "Codex turn terminated (thread/read)")
        row["executor"]["turn_id"] = "some-other-turn"
        self.assertIsNone(write_ownership.termination_evidence(row))


if __name__ == "__main__":
    unittest.main()
