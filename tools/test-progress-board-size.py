#!/usr/bin/env python3
"""Regression for the live board's duplicated watchdog command diagnostics."""
import copy
import importlib.util
import unittest
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("board_size", ROOT / "tools/progress_board.py")
assert spec is not None and spec.loader is not None
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


class CostSnapshotTests(unittest.TestCase):
    def test_board_includes_validated_provider_days_and_jev_call_sites(self):
        now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        value = {"schema": "carr-system-costs.v1", "observed_at": "2026-10-05T11:00:00Z",
                 "month": "2026-10", "through": "2026-10-04", "state": "ready",
                 "providers": [{"provider": "jev", "label": "Jev", "plan": "Per call", "state": "ready", "reason": None,
                                "mtd_usd": 2.5, "projection_usd": 19.375, "budget_usd": 50,
                                "daily": [{"day": "2026-10-04", "usd": 2.5, "drivers": {"hook:route": 2.5}}],
                                "call_sites": {"hook:route": 2.5}}],
                 "months": [{"month": "2026-10", "usd": 2.5, "providers": {"jev": 2.5}}],
                 "alerts": [], "action": "Finance Ops: inspect provider and driver; verify next day; auto-clear after thresholds pass.",
                 "billing_response": {"card_number": "never-return-fixture"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            (root / "out/system-costs.json").write_text(json.dumps(value))
            with patch.object(board, "REPO_ROOT", root), patch.object(board, "now_utc", return_value=now):
                costs = board.board_snapshot({"project": "system-costs", "tasks": {}})["costs"]
            self.assertEqual(costs["state"], "ready")
            self.assertEqual(costs["providers"][0]["daily"][0]["usd"], 2.5)
            self.assertEqual(costs["providers"][0]["call_sites"], {"hook:route": 2.5})
            self.assertEqual(costs["months"][0]["usd"], 2.5)
            self.assertNotIn("never-return-fixture", json.dumps(costs))
            value["state"] = "partial"
            value["providers"][0]["state"] = "partial"
            value["providers"][0]["reason"] = "billing estimate"
            (root / "out/system-costs.json").write_text(json.dumps(value))
            with patch.object(board, "REPO_ROOT", root), patch.object(board, "now_utc", return_value=now):
                partial = board.board_snapshot({"project": "system-costs", "tasks": {}})["costs"]
            self.assertEqual(partial["state"], "partial")
            self.assertEqual(partial["providers"][0]["reason"], "billing estimate")
            value["observed_at"] = "2026-10-03T00:00:00Z"
            (root / "out/system-costs.json").write_text(json.dumps(value))
            with patch.object(board, "REPO_ROOT", root), patch.object(board, "now_utc", return_value=now):
                stale = board.board_snapshot({"project": "system-costs", "tasks": {}})["costs"]
            self.assertEqual(stale["state"], "unavailable")
            self.assertEqual(stale["providers"], [])

    def test_board_reports_missing_costs_without_network_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(board, "REPO_ROOT", root), patch.object(board, "urlopen", side_effect=AssertionError("network read")):
                costs = board.board_snapshot({"project": "fixture", "tasks": {}})["costs"]
            self.assertEqual(costs["state"], "unavailable")
            self.assertEqual(costs["providers"], [])
            self.assertRegex(costs["action"], "owner orchestrator.*verify.*auto-clear")


class SnapshotSizeTests(unittest.TestCase):
    def test_app_evidence_and_inline_question_survive_projection(self):
        task = {"title": "Review", "status": "review", "stage": "review", "pr": 1553,
                "repo": "jbookout/carr-system", "pr_head": "a" * 40, "pr_checks": "failure",
                "pr_links": [{"repo": "jbookout/doctorcre-app", "number": 42, "head_sha": "b" * 40}],
                "question": "Which delivery target applies?"}
        projected = board.board_snapshot({"project": "fixture", "tasks": {"review": task}})["tasks"]["review"]
        for field in ("pr_head", "pr_checks", "pr_links", "question"):
            with self.subTest(field=field):
                self.assertEqual(projected.get(field), task[field])

    def test_watchdog_diagnostics_fit_with_every_open_card_and_headroom(self):
        # Live census: 867 tasks, 337 in flight, 29 diagnostic cards around 28 KB
        # per duplicated field. No business text is copied into this fixture.
        tasks = {}
        for n in range(867):
            stage = "review" if n < 337 else "live"
            reason = "GitHub read failed: " + ("diagnostic line\n" * 1800 if n < 29 else "retry the source read")
            tasks[f"task-{n}"] = {
                "title": f"Scheduled task {n}", "status": "blocked" if n < 297 else "running" if n < 337 else "done",
                "stage": stage, "executor": "orchestrator", "repo": "jbookout/carr-system",
                "created_at": "2026-09-29T00:00:00Z", "updated_at": "2026-10-05T00:00:00Z",
                "stage_entered_at": "2026-09-29T00:00:00Z",
                "stage_history": [{"stage": stage, "entered_at": "2026-09-29T00:00:00Z"}],
                "blocked_reason": reason if n < 297 else None, "note": reason,
                "next_action": "Read the job log, restore the source, then retry.",
                "pr_head": "f" * 40, "pr_checks": "1 pass · 0 pending · 1 fail",
            }
        state = {"project": "carr-v5", "tasks": tasks}
        before = copy.deepcopy(state)
        snapshot = board.board_snapshot(state)
        self.assertLessEqual(board.snapshot_size(snapshot), board.SNAPSHOT_LIMIT * 9 // 10)
        self.assertTrue(set(f"task-{n}" for n in range(337)) <= snapshot["tasks"].keys())
        for n in range(337):
            card = snapshot["tasks"][f"task-{n}"]
            self.assertEqual(card["title"], tasks[f"task-{n}"]["title"])
            self.assertEqual(card["next_action"], tasks[f"task-{n}"]["next_action"])
            self.assertEqual(card["stage_history"], tasks[f"task-{n}"]["stage_history"])
            self.assertEqual(card["pr_head"], tasks[f"task-{n}"]["pr_head"])
            self.assertEqual(card["pr_checks"], tasks[f"task-{n}"]["pr_checks"])
        self.assertTrue(snapshot["tasks"]["task-0"]["blocked_reason"].startswith("GitHub read failed:"))
        self.assertTrue(snapshot["tasks"]["task-0"]["blocked_reason"].endswith("diagnostic line\n"))
        self.assertNotIn("note", snapshot["tasks"]["task-0"])
        self.assertEqual(state, before, "publishing must preserve full local diagnostics")


if __name__ == "__main__":
    unittest.main()
