"""Synthetic record replies exercise ambiguous board writes without a service."""
import copy
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
        elif verb == "publish-census-cache-page":
            pass
        elif verb == "ask-board-question":
            self.questions = [{"question_id": args["question_id"], "revision": 1,
                               **{key: args[key] for key in ("prompt", "choices", "allow_free_text", "default_answer", "asker_ref")}}]
        else:
            raise AssertionError(verb)
        if len(self.writes) == 1:
            return record.VerbResult(verb, record.TRANSIENT, detail="synthetic-private-payload")
        return record.VerbResult(verb, record.OK, {"ok": True})


class BoardRecoveryTests(unittest.TestCase):
    def test_transient_census_page_write_retries_the_same_key_and_payload(self):
        fixture = RecordFixture("uncommitted")
        digest = "0" * 64
        with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
            board.publish_census_page("external", digest, {"items": ["fixture"]})
        self.assertEqual(len(fixture.writes), 2)
        self.assertEqual(fixture.writes[0], fixture.writes[1])
        self.assertEqual(fixture.reads, 0)
        self.assertEqual(str(uuid.UUID(fixture.writes[0]["idempotency_key"])), fixture.writes[0]["idempotency_key"])

    def test_census_page_key_changes_daily_and_not_between_ticks(self):
        digest = "1" * 64
        keys = []
        for day in (21, 21, 22):
            fixture = RecordFixture("committed")
            moment = board.datetime(2026, 10, day, 12, tzinfo=board.timezone.utc)
            with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture), \
                    patch.object(board, "now_utc", return_value=moment):
                board.publish_census_page("snapshot", digest, {"content": "fixture"})
            keys.append(fixture.writes[0]["idempotency_key"])
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[1], keys[2])

    def test_second_transient_census_page_write_fails_closed(self):
        class Always:
            def __init__(self): self.calls = 0
            def __call__(self, verb, args, **kwargs):
                self.calls += 1
                return board.repo_lib("record_call").VerbResult(verb, board.repo_lib("record_call").TRANSIENT)
        fixture = Always()
        with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
            with self.assertRaises(RuntimeError):
                board.publish_census_page("external", "2" * 64, {"items": []})
        self.assertEqual(fixture.calls, 2)

    def test_conflicting_or_unreadable_readback_never_resends(self):
        for mode in ("conflict", "unreadable"):
            with self.subTest(mode=mode):
                fixture = RecordFixture(mode)
                args = {"board_id": "synthetic", "base_version": 0, "snapshot": {"notes": ["fixture"]}}
                args["idempotency_key"] = board.stable_key("publish-board-snapshot", args)
                with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
                    with self.assertRaises(RuntimeError):
                        board.call_verb("publish-board-snapshot", args)
                self.assertEqual(len(fixture.writes), 1)
                self.assertGreaterEqual(fixture.reads, 1)

    def test_committed_question_requires_exact_revision_and_fields(self):
        fixture = RecordFixture("committed")
        args = {"board_id": "synthetic", "question_id": "decision", "base_version": 0,
                "prompt": "Choose a fixture", "choices": ["one", "two"], "allow_free_text": False,
                "default_answer": "one", "asker_ref": "orchestrator:synthetic"}
        args["idempotency_key"] = board.stable_key("ask-board-question", args)
        with patch.object(board.repo_lib("record_call"), "call_verb", side_effect=fixture):
            self.assertTrue(board.call_verb("ask-board-question", args)["ok"])
        self.assertEqual(len(fixture.writes), 1)

    def test_transport_diagnostics_exclude_reply_payload(self):
        record = board.repo_lib("record_call")
        result = record.VerbResult("read-progress-board", record.TRANSIENT, detail="synthetic-private-payload")
        with patch.object(record, "call_verb", return_value=result):
            with self.assertRaises(RuntimeError) as raised:
                board.call_verb("read-progress-board", {"board_id": "synthetic"})
        self.assertNotIn("synthetic-private-payload", str(raised.exception))
        self.assertIn("elapsed=", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
