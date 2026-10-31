"""Synthetic record replies exercise ambiguous board writes without a service."""
import copy
import json
import tempfile
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

import progress_board as board


class RecordFixture:
    def __init__(self, mode):
        self.mode = mode
        self.snapshot = None
        self.questions = []
        self.writes = []
        self.reads = 0

    def __call__(self, verb, args, **kwargs):
        record = board.repo_lib("record_call")
        if verb == "read-progress-board":
            self.reads += 1
            if self.mode == "unreadable" and self.writes:
                return record.VerbResult(verb, record.TRANSIENT)
            return record.VerbResult(verb, record.OK, {"ok": True, "snapshot": copy.deepcopy(self.snapshot), "questions": self.questions})
        self.writes.append(copy.deepcopy(args))
        if verb == "publish-board-snapshot":
            if self.mode != "uncommitted" or len(self.writes) > 1:
                self.snapshot = {"version": args["base_version"] + 1, "snapshot_json": copy.deepcopy(args["snapshot"])}
            if self.mode == "conflict":
                self.snapshot = {"version": 2, "snapshot_json": {"notes": ["someone else"]}}
        elif verb == "ask-board-question":
            self.questions = [{"question_id": args["question_id"], "revision": 1,
                               **{key: args[key] for key in ("prompt", "choices", "allow_free_text", "default_answer", "asker_ref")}}]
        else:
            raise AssertionError(verb)
        if len(self.writes) == 1:
            return record.VerbResult(verb, record.TRANSIENT, detail="synthetic-private-payload")
        return record.VerbResult(verb, record.OK, {"ok": True})


class BoardRecoveryTests(unittest.TestCase):
    def test_committed_write_is_reconciled_without_resending(self):
        fixture = RecordFixture("committed")
        with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
            self.assertEqual(board.publish_immutable_page("synthetic-page", {"notes": ["fixture"]}), 1)
        self.assertEqual(len(fixture.writes), 1)

    def test_uncommitted_write_retries_identical_version_payload_and_key(self):
        fixture = RecordFixture("uncommitted")
        with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
            self.assertEqual(board.publish_immutable_page("synthetic-page", {"notes": ["fixture"]}), 1)
        self.assertEqual(len(fixture.writes), 2)
        self.assertEqual(fixture.writes[0], fixture.writes[1])
        self.assertEqual(str(uuid.UUID(fixture.writes[0]["idempotency_key"])), fixture.writes[0]["idempotency_key"])

    def test_conflicting_or_unreadable_readback_never_resends(self):
        for mode in ("conflict", "unreadable"):
            with self.subTest(mode=mode):
                fixture = RecordFixture(mode)
                with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
                    with self.assertRaises(RuntimeError):
                        board.publish_immutable_page("synthetic-page", {"notes": ["fixture"]})
                self.assertEqual(len(fixture.writes), 1)
                self.assertGreaterEqual(fixture.reads, 2)

    def test_committed_question_requires_exact_revision_and_fields(self):
        fixture = RecordFixture("committed")
        args = {"board_id": "synthetic", "question_id": "decision", "base_version": 0,
                "prompt": "Choose a fixture", "choices": ["one", "two"], "allow_free_text": False,
                "default_answer": "one", "asker_ref": "orchestrator:synthetic"}
        args["idempotency_key"] = board.stable_key("ask-board-question", args)
        with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
            self.assertTrue(board.board_reply("ask-board-question", args)["ok"])
        self.assertEqual(len(fixture.writes), 1)

    def test_large_inventory_reaches_local_transport_without_argv_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preload = root / "fixture.mjs"
            preload.write_text("""globalThis.fetch = async (url, options) => {
  if (url !== 'http://fixture.invalid/never-contacted') throw Error('unexpected destination');
  const call = JSON.parse(options.body);
  if (call.params.arguments.payload.items[0].length !== 2000000) throw Error('truncated payload');
  return {ok:true, status:200, text:async () => JSON.stringify({jsonrpc:'2.0', id:call.id,
    result:{content:[{type:'text',text:JSON.stringify({ok:true})}]}})};
};
""")
            fixture_env = {"HOME": directory, "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
                           "CARR_MCP_LOCAL_TOKEN": "synthetic-fixture-bearer",
                           "CARR_MCP_ENV": str(root / "absent-file"),
                           "CARR_MCP_URL": "http://fixture.invalid/never-contacted",
                           "NODE_OPTIONS": "--import=" + str(preload)}
            with patch.dict(board.os.environ, fixture_env, clear=True):
                result = board.publish_external_inventory({"items": ["x" * 2000000]})
            self.assertEqual(result["item_count"], 1)

    def test_transport_diagnostics_exclude_reply_payload(self):
        record = board.repo_lib("record_call")
        result = record.VerbResult("read-progress-board", record.TRANSIENT, detail="synthetic-private-payload")
        with patch.object(record, "call_verb", return_value=result):
            with self.assertRaises(RuntimeError) as raised:
                board.board_reply("read-progress-board", {"board_id": "synthetic"})
        self.assertNotIn("synthetic-private-payload", str(raised.exception))
        self.assertIn("elapsed=", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
