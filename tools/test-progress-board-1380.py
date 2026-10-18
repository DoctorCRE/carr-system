#!/usr/bin/env python3
"""Pins the two P1 findings of the post-merge BLOCK on PR #1380.

Both were fixed by PR #1381 (f5477435). These tests replay the exact
reproductions the BLOCK reported, so a regression in either fails here:

1. Review readiness: an approval from an untrusted commenter never makes a
   card Ready to merge, and a later trusted BLOCK decides over it.
2. Live: a legacy `measured` (or `live`) record with no evidence never maps
   to Live, so it never counts as completed.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("board_1380", ROOT / "tools/progress_board.py")
assert spec is not None and spec.loader is not None
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)

HEAD = "a" * 40
APPROVE = "APPROVE\nReviewed-SHA: " + HEAD + "\n"
BLOCK = "BLOCK\nReviewed-SHA: " + HEAD + "\nNeeds a fix\n"


def comment(login, association, body, created):
    return {"author": {"login": login}, "authorAssociation": association, "body": body, "createdAt": created}


def open_pr(comments):
    return {"state": "OPEN", "isDraft": False, "headRefOid": HEAD, "author": {"login": "builder"},
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}], "comments": comments}


class UntrustedApprovalIsNotReadiness(unittest.TestCase):
    def test_untrusted_approval_alone_awaits_review(self):
        payload = open_pr([comment("outsider", "NONE", APPROVE, "2026-09-28T10:00:00Z")])
        self.assertEqual(board.review_verdict(payload), "Not recorded")
        self.assertEqual(board.derived_pr_state(payload), ("review", "review", "Awaiting review"))

    def test_untrusted_approval_then_trusted_block_is_blocked(self):
        payload = open_pr([comment("outsider", "NONE", APPROVE, "2026-09-28T10:00:00Z"),
                           comment("reviewer", "COLLABORATOR", BLOCK, "2026-09-28T11:00:00Z")])
        self.assertEqual(board.review_verdict(payload), "BLOCK")
        self.assertEqual(board.derived_pr_state(payload), ("blocked", "review", "Review blocked"))

    def test_later_untrusted_approval_cannot_override_trusted_block(self):
        payload = open_pr([comment("reviewer", "COLLABORATOR", BLOCK, "2026-09-28T10:00:00Z"),
                           comment("outsider", "NONE", APPROVE, "2026-09-28T11:00:00Z")])
        self.assertEqual(board.derived_pr_state(payload), ("blocked", "review", "Review blocked"))


class EvidenceFreeLegacyRecordIsNotLive(unittest.TestCase):
    CASES = [
        {"status": "done", "pr": 42, "stage": "measured"},
        {"status": "done", "pr": 42, "stage": "live"},
        {"status": "running", "pr": 42, "stage": "measured"},
        {"status": "measured", "pr": 42, "stage": "measured"},
        {"status": "done", "pr": 42, "stage": "measured", "evidence": "   "},
    ]

    def test_no_evidence_never_maps_to_live_or_completes(self):
        for case in self.CASES:
            task = {**case, "updated_at": "2026-09-28T10:00:00+00:00",
                    "completed_at": "2026-09-28T10:00:00+00:00"}
            with self.subTest(task=case):
                self.assertNotEqual(board.task_stage(task), "live")
                self.assertIsNone(board.completed_at(task))

    def test_legacy_measured_with_evidence_is_still_live(self):
        task = {"status": "done", "pr": 42, "stage": "measured", "evidence": "Readback observed",
                "updated_at": "2026-09-28T10:00:00+00:00"}
        self.assertEqual(board.task_stage(task), "live")
        self.assertIsNotNone(board.completed_at(task))


if __name__ == "__main__":
    unittest.main()
