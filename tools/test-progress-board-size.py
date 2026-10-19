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
from progress_board_test_support import exercise_fields


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
    def test_trimming_removes_defaults_no_remaining_card_inherits(self):
        for field, value in (("title", "X" * 240000),
                             ("stage_history", [{"stage": "merged", "entered_at": "2026-10-18T12:00:00Z"}] * 5000)):
            with self.subTest(field=field):
                merged = {"title": "Merged task", "stage": "merged", "status": "review", "pr_phase": "Merged",
                          "updated_at": "2026-10-17T12:00:00Z", field: value}
                newer = {"title": "Newer merged task", "stage": "merged", "status": "review", "pr_phase": "Merged",
                         "updated_at": "2026-10-18T12:00:00Z"}
                task = {"title": "Open task", "stage": "build", "status": "running", "updated_at": "2026-10-19T12:00:00Z"}
                if field == "stage_history":
                    task[field] = [{"stage": "build", "entered_at": "2026-10-18T12:00:00Z"}]
                    newer[field] = [{"stage": "merged", "entered_at": "2026-10-18T12:00:00Z"}]
                state = {"project": "fixture", "tasks": {"merged-a": copy.deepcopy(merged),
                                                         "merged-b": copy.deepcopy(merged), "merged-new": newer, "open": task}}
                before = copy.deepcopy(state)

                snapshot = board.board_snapshot(state, costs={"state": "unavailable", "providers": []})

                self.assertLessEqual(board.snapshot_size(snapshot), board.SNAPSHOT_BUDGET)
                self.assertEqual(set(snapshot["tasks"]), {"open", "merged-new"})
                self.assertEqual(snapshot["omitted"]["merged"], 2)
                defaults = snapshot.get("task_defaults", {})
                self.assertNotIn(field, defaults)
                for default in defaults:
                    self.assertTrue(any(default not in card for card in snapshot["tasks"].values()))
                for task_id, expected_task in (("open", task), ("merged-new", newer)):
                    restored = {**defaults, **snapshot["tasks"][task_id]}
                    for key, expected in expected_task.items():
                        self.assertEqual(restored[key], expected)
                self.assertEqual(state, before)

    def test_projection_preserves_custom_waits_and_stage_time_precision_and_fallbacks(self):
        utc = "2026-10-18T12:34:56.123456+00:00"
        offset = "2026-10-18T18:04:56.123456+05:30"
        tasks = {
            "missing-history": {"title": "Missing history", "status": "running", "stage": "build",
                                "updated_at": utc, "stage_entered_at": utc,
                                "release_wait": "Wait for the operator's custom delivery check."},
            "offset-history": {"title": "Non-UTC history", "status": "running", "stage": "build",
                               "updated_at": offset, "stage_entered_at": offset,
                               "stage_history": [{"stage": "build", "entered_at": offset}]},
            "updated-fallback": {"title": "Updated fallback", "status": "queued", "stage": "queued", "updated_at": utc},
            "explicit-only": {"title": "Explicit stage time", "status": "queued", "stage": "queued", "stage_entered_at": utc},
        }
        state = {"project": "fixture", "tasks": tasks}
        before = copy.deepcopy(state)

        snapshot = board.board_snapshot(state, costs={"state": "unavailable", "providers": []})

        self.assertEqual(snapshot["schema"], "carr-progress-board.v2")
        self.assertNotIn("task_defaults", snapshot)
        cards = snapshot["tasks"]
        self.assertEqual(cards["missing-history"]["release_wait"], tasks["missing-history"]["release_wait"])
        for task_id in ("missing-history", "offset-history", "updated-fallback"):
            with self.subTest(task_id=task_id):
                card = cards[task_id]
                self.assertEqual(len(card["stage_history"]), 1)
                self.assertEqual(datetime.fromisoformat(board.stage_entered_at(card)), datetime.fromisoformat(utc))
                self.assertNotIn("stage_entered_at", card)
        self.assertEqual(cards["offset-history"]["updated_at"], offset)
        self.assertEqual(cards["offset-history"]["stage_history"][0]["entered_at"], offset)
        self.assertEqual(datetime.fromisoformat(cards["explicit-only"]["stage_entered_at"]), datetime.fromisoformat(utc))
        self.assertEqual(state, before)

    def test_consumer_proof_backlog_fits_without_losing_open_cards_or_stage_events(self):
        tasks = {}
        expected_histories = {}
        for n in range(605):
            stage = "live" if n < 278 else "review" if n < 282 else "build" if n < 305 else "queued" if n < 310 else "merged"
            status = "done" if n < 278 else "review" if n < 282 else "running" if n < 305 else "queued" if n < 310 else "review"
            if n >= 492:
                status = "superseded"
            event_count = 186 if n == 0 else 3 if n <= 275 else 2
            if n < 278:
                event_count -= 1
            history = [{"stage": "queued" if i % 2 == 0 else "build",
                        "entered_at": f"2026-10-18T{(i // 60):02d}:{(i % 60):02d}:00+00:00"}
                       for i in range(event_count)]
            history[-1]["stage"] = stage
            task = {"title": f"Synthetic publication task {n:03d} with representative board title",
                    "status": status, "stage": stage, "executor": "codex",
                    "provider": "openai", "model": "gpt-6", "effort": "high",
                    "repo": "jbookout/carr-system", "pr": 2000 + n,
                    "pr_head": f"{n:040x}", "pr_checks": "1 pass · 0 pending · 0 fail",
                    "pr_url": f"https://github.com/jbookout/carr-system/pull/{2000 + n}",
                    "created_at": "2026-10-18T00:00:00+00:00", "updated_at": "2026-10-18T04:00:00+00:00",
                    "stage_entered_at": history[-1]["entered_at"], "stage_history": history}
            if n < 30:
                task["summary"] = "Keep board publication reliable while retaining delivery evidence."
            tasks[f"task-{n}"] = task
            expected_histories[f"task-{n}"] = [*history]
            if n < 278:
                expected_histories[f"task-{n}"].append({"stage": "review", "entered_at": task["updated_at"]})
        state = {"project": "carr-v5", "tasks": tasks}
        before = copy.deepcopy(state)
        costs = {"state": "ready", "providers": [
            {"provider": f"synthetic-{provider}", "daily": [
                {"day": f"2026-10-{day:02d}", "usd": 1.25,
                 "drivers": {f"synthetic-driver-{driver:02d}": 0.05 for driver in range(16)}}
                for day in range(1, 32)]}
            for provider in range(3)]}

        snapshot = board.board_snapshot(state, costs=costs)

        self.assertLessEqual(board.snapshot_size(snapshot), board.SNAPSHOT_BUDGET)
        self.assertEqual(snapshot["schema"], "carr-progress-board.v3")
        self.assertTrue(snapshot["task_defaults"])
        self.assertNotIn("summary", snapshot["task_defaults"])
        self.assertEqual(snapshot["costs"], costs)
        self.assertTrue({f"task-{n}" for n in range(310)} <= snapshot["tasks"].keys())
        for task_id, card in snapshot["tasks"].items():
            card = {**snapshot.get("task_defaults", {}), **card}
            task = tasks[task_id]
            with self.subTest(task_id=task_id):
                for field in ("title", "summary", "executor", "provider", "model", "effort", "repo", "pr", "pr_head", "pr_checks", "pr_url"):
                    self.assertEqual(card.get(field), task.get(field))
                self.assertEqual(card["stage"], expected_histories[task_id][-1]["stage"])
                self.assertEqual([(entry["stage"], datetime.fromisoformat(entry["entered_at"])) for entry in card["stage_history"]],
                                 [(entry["stage"], datetime.fromisoformat(entry["entered_at"])) for entry in expected_histories[task_id]])
                self.assertEqual(datetime.fromisoformat(board.stage_entered_at(card)),
                                 datetime.fromisoformat(expected_histories[task_id][-1]["entered_at"]))
        self.assertEqual(state, before, "publication must preserve full local source records")
    def test_exact_work_lineage_survives_projection_and_title_changes(self):
        refs = {"work_request_ref": "WR-900", "plan_ref": "plan:synthetic-v2",
                "slice_ref": "slice:seven", "task_ref": "task:linked-card", "job_ref": "job:synthetic-build"}
        task = {"title": "Synthetic card", "status": "review", "pr": 73, **refs}
        state = {"project": "fixture", "tasks": {"app-pr-73": task}}
        for title in ("Synthetic card", "Renamed card"):
            task["title"] = title
            projected = board.board_snapshot(state, costs={"state": "unavailable", "providers": []})["tasks"]["app-pr-73"]
            self.assertEqual({key: projected.get(key) for key in refs}, refs)
        del task["plan_ref"]
        projected = board.board_snapshot(state, costs={"state": "unavailable", "providers": []})["tasks"]["app-pr-73"]
        self.assertNotIn("plan_ref", projected)

    def test_app_evidence_and_inline_question_survive_projection(self):
        task = {"title": "Review", "status": "review", "stage": "review", "pr": 1553,
                "repo": "jbookout/carr-system", "pr_head": "a" * 40, "pr_checks": "failure",
                "pr_links": [{"repo": "jbookout/doctorcre-app", "number": 42, "head_sha": "b" * 40}],
                "question": "Which delivery target applies?"}
        projected = board.board_snapshot({"project": "fixture", "tasks": {"review": task}},
                                         costs={"state": "unavailable", "providers": []})["tasks"]["review"]
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
        for n in range(337, 867):
            tasks[f"task-{n}"].update(exercise_fields())
        state = {"project": "carr-v5", "tasks": tasks}
        before = copy.deepcopy(state)
        snapshot = board.board_snapshot(state, costs={"state": "unavailable", "providers": []})
        self.assertLessEqual(board.snapshot_size(snapshot), board.SNAPSHOT_LIMIT * 9 // 10)
        self.assertTrue(set(f"task-{n}" for n in range(337)) <= snapshot["tasks"].keys())
        for n in range(337):
            card = {**snapshot.get("task_defaults", {}), **snapshot["tasks"][f"task-{n}"]}
            self.assertEqual(card["title"], tasks[f"task-{n}"]["title"])
            self.assertEqual(card["next_action"], tasks[f"task-{n}"]["next_action"])
            self.assertEqual(card["stage_history"], tasks[f"task-{n}"]["stage_history"])
            self.assertEqual(card["pr_head"], tasks[f"task-{n}"]["pr_head"])
            self.assertEqual(card["pr_checks"], tasks[f"task-{n}"]["pr_checks"])
        diagnostic = {**snapshot.get("task_defaults", {}), **snapshot["tasks"]["task-0"]}
        self.assertTrue(diagnostic["blocked_reason"].startswith("GitHub read failed:"))
        self.assertTrue(diagnostic["blocked_reason"].endswith("diagnostic line\n"))
        self.assertNotIn("note", snapshot["tasks"]["task-0"])
        self.assertEqual(state, before, "publishing must preserve full local diagnostics")


if __name__ == "__main__":
    unittest.main()
