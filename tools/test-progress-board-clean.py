#!/usr/bin/env python3
"""Board cleanup previews, activity counts, and V1 publication behavior."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import progress_board as board

AT = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
REPO = board.DEFAULT_PR_REPO


def pr(number, state="OPEN", title="Synthetic work", **extra):
    return {"number": number, "state": state, "title": title, "isDraft": False,
            "headRefOid": "a" * 40, "headRefName": "codex/example", "author": {"login": "demo"},
            "comments": [], "statusCheckRollup": [], "reviewDecision": "", "mergeable": "MERGEABLE",
            "mergeCommit": {"oid": "b" * 40} if state == "MERGED" else None,
            "updatedAt": AT.isoformat(), "createdAt": AT.isoformat(), **extra}


class CleanupTests(unittest.TestCase):
    def test_reconcile_preserves_open_vendor_fetch_error_and_removes_watchdog_card(self):
        watchdog = board.repo_lib("job_watchdog")
        config = watchdog.load_config(board.REPO_ROOT / "ops/config/job-watchdog.json")
        config["board"] = "test"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "watchdog.json"
            config_path.write_text(json.dumps({**config, "board": "default-board"}))
            with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(root / "out"),
                                         "PROGRESS_BOARD_LOCAL_ONLY": "1",
                                         "CARR_JOB_BOARD": "test",
                                         "CARR_WATCHDOG_CONFIG": str(config_path)}):
                incident = watchdog.finding("vendor_release_fetch_error", "vendor:https://vendor.example/docs",
                                            "Vendor unavailable", config, url="https://vendor.example/docs")
                effects = watchdog.Effects(root, config)
                with patch.object(effects, "_record", return_value={"ok": True, "loop_id": "vendor-loop"}):
                    watchdog.reconcile(root, config, [incident], effects, 100)
                vendor_card = effects.card(incident)
                state = board.read_state("test")
                vendor_task = copy.deepcopy(state["tasks"][vendor_card])
                state["tasks"]["wd-ordinary"] = {"status": "blocked", "title": "Watchdog noise"}
                board.write_json(state)
                with patch.object(board, "discover_v1", return_value={}), \
                        patch.object(board, "publish_board"), contextlib.redirect_stdout(io.StringIO()):
                    board.main(["reconcile", "test", "--apply"])
                result = board.read_state("test")
                self.assertEqual(result["tasks"], {vendor_card: vendor_task})
                self.assertEqual(set(result["reconcile_archive"][-1]["tasks"]), {"wd-ordinary"})
                stored = watchdog.read_latest(root / config["paths"]["findings"])[incident["key"]]
                self.assertEqual(stored["loop_id"], "vendor-loop")
                self.assertIsNone(stored["cleared_at"])
                watchdog.append(root / config["paths"]["findings"],
                                {"key": incident["key"], "cleared_at": AT.isoformat(), "recovery_reported": True})
                with patch.object(board, "discover_v1", return_value={}), \
                        patch.object(board, "publish_board"), contextlib.redirect_stdout(io.StringIO()):
                    board.main(["reconcile", "test", "--apply"])
                recovered = board.read_state("test")
                self.assertEqual(recovered["tasks"], {})
                self.assertEqual(set(recovered["reconcile_archive"][-1]["tasks"]), {vendor_card})

    def test_preview_removes_watchdog_folds_duplicates_and_retires_terminal_prs(self):
        state = {"project": "test", "tasks": {
            "wd-0123456789abcdef": {"status": "blocked"},
            "pr-1": {"title": "Meaningful title", "status": "blocked", "pr": 1},
            "pr-carr-system-1": {"title": "pr-carr-system-1", "status": "blocked", "pr": 1, "evidence": "preserve"},
            "pr-2": {"status": "blocked", "pr": 2},
            "pr-3": {"status": "blocked", "pr": 3},
        }}
        original = copy.deepcopy(state)
        facts = {(REPO, 1): (pr(1, "MERGED"), None), (REPO, 2): (pr(2, "CLOSED"), None),
                 (REPO, 3): (pr(3, mergeable="CONFLICTING"), None)}
        with patch.object(board, "now_utc", return_value=AT):
            result, report = board.reconcile_state(state, facts, AT.isoformat())
        self.assertEqual(state, original)
        self.assertEqual(set(result["tasks"]), {"pr-1", "pr-2", "pr-3"})
        self.assertEqual(result["tasks"]["pr-1"]["stage"], "merged")
        self.assertEqual(result["tasks"]["pr-1"]["status"], "done")
        self.assertEqual(result["tasks"]["pr-1"]["evidence"], "preserve")
        self.assertIn("pr-2", board.board_snapshot(result)["history"])
        self.assertTrue(report["check_passed"])
        self.assertEqual((report["blocked"], report["unexplained_blocked_prs"]), (1, []))
        with patch.object(board, "now_utc", return_value=AT):
            again, _ = board.reconcile_state(result, facts, AT.isoformat())
        self.assertEqual(again, result)

    def test_reconcile_applies_the_scheduled_sync_rule_to_every_pr_card(self):
        curated = {"title": "Curated: invoices rework", "status": "blocked", "pr": 3, "pr_head": "a" * 40}
        tasks = {"pr-3": curated, "pr-4": {"title": "Old attempt", "status": "superseded", "pr": 4}}
        facts = {(REPO, 3): (pr(3), None), (REPO, 4): (pr(4), None)}
        expected = copy.deepcopy({"tasks": tasks})
        with patch.object(board, "now_utc", return_value=AT), patch.dict(os.environ, {"PROGRESS_BOARD_SKIP_GH": "1"}):
            board.apply_sync(expected, facts)
            result, report = board.reconcile_state({"tasks": tasks}, facts, AT.isoformat())
        self.assertEqual(result["tasks"], expected["tasks"])
        self.assertEqual(result["tasks"]["pr-3"]["title"], "Curated: invoices rework")
        self.assertEqual(result["tasks"]["pr-4"]["status"], expected["tasks"]["pr-4"]["status"])
        self.assertTrue(report["check_passed"])

    def test_blocked_job_cards_do_not_fail_the_check(self):
        _, report = board.reconcile_state({"tasks": {"nightly-job": {"status": "blocked"}}}, {}, AT.isoformat())
        self.assertTrue(report["check_passed"])

    def test_blocked_pr_card_must_be_an_open_github_blocked_pr(self):
        tasks = {"pr-5": {"status": "blocked", "pr": 5, "blocked_reason": "stale", "blocked_source": "github"}}
        with patch.dict(os.environ, {"PROGRESS_BOARD_SKIP_GH": "1"}):
            _, report = board.reconcile_state({"tasks": tasks}, {(REPO, 5): (pr(5, mergeable="CONFLICTING"), None)},
                                              AT.isoformat())
        self.assertTrue(report["check_passed"])
        manual = {"pr-6": {"status": "blocked", "pr": 6, "pr_head": "a" * 40, "blocked_reason": "Hold",
                           "blocked_source": "manual"}}
        with patch.dict(os.environ, {"PROGRESS_BOARD_SKIP_GH": "1"}):
            _, report = board.reconcile_state({"tasks": manual}, {(REPO, 6): (pr(6), None)}, AT.isoformat())
        self.assertEqual(report["unexplained_blocked_prs"], [])
        self.assertTrue(report["check_passed"])

    def test_same_number_in_different_repositories_does_not_fold(self):
        state = {"tasks": {"pr-1": {"status": "review", "pr": 1},
                           "pr-doctorcre-app-1": {"status": "review", "pr": 1, "repo": "jbookout/doctorcre-app"}}}
        facts = {(REPO, 1): (pr(1), None), ("jbookout/doctorcre-app", 1): (pr(1), None)}
        result, _ = board.reconcile_state(state, facts, AT.isoformat())
        self.assertEqual(set(result["tasks"]), {"pr-1", "app-pr-1"})

    def test_read_failure_keeps_card_and_fails_check(self):
        task = {"status": "blocked", "pr": 1, "title": "Keep"}
        result, report = board.reconcile_state({"tasks": {"pr-1": task}}, {(REPO, 1): (None, "offline")}, AT.isoformat())
        self.assertEqual(result["tasks"]["pr-1"], task)
        self.assertFalse(report["check_passed"])

    def test_missing_discovered_slice_fails_reconcile_and_scheduled_sync(self):
        facts = {(REPO, 15): (None, "GitHub unavailable")}
        state = {"tasks": {}}
        _, report = board.reconcile_state(state, facts, AT.isoformat())
        self.assertFalse(report["check_passed"])
        with patch.dict(os.environ, {"PROGRESS_BOARD_SKIP_GH": ""}):
            board.apply_sync(state, facts)
        self.assertTrue(state["github_sync"]["stale"])
        self.assertEqual(len(state["github_sync"]["failed"]), 1)
        self.assertEqual(state["github_sync"]["failed"][0]["repo"], REPO)

    def test_cli_defaults_to_dry_run_and_does_not_write_or_publish(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": root}):
            path = Path(root) / "boards/test.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"project": "test", "tasks": {"wd-any": {"status": "blocked"}}}))
            before = path.read_bytes()
            with patch.object(board, "discover_v1", return_value={}), patch.object(board, "publish_board") as publish:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    board.main(["reconcile", "test"])
                self.assertIn("DRY RUN", output.getvalue())
                self.assertIn("CHECK PASS", output.getvalue())
                publish.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["test.json"])

    def test_live_apply_is_refused_before_reads(self):
        with patch.object(board, "call_verb") as call:
            with self.assertRaises(SystemExit):
                board.main(["reconcile", "test", "--live", "--apply"])
            call.assert_not_called()

    def test_stale_has_its_own_count_and_keeps_work_status(self):
        tasks = {status: {"status": status, "updated_at": "2026-09-21T12:00:00Z"}
                 for status in ("queued", "review", "running", "blocked")}
        counts = board.activity_counts(tasks, AT)
        self.assertEqual((counts["stale"], counts["queued"], counts["review"], counts["running"], counts["blocked"]), (3, 0, 0, 0, 1))
        with patch.object(board, "now_utc", return_value=AT):
            snapshot = board.board_snapshot({"project": "test", "tasks": tasks})
        self.assertEqual(snapshot["tasks"]["running"]["status"], "running")
        self.assertEqual(snapshot["tasks"]["running"]["activity_status"], "stale")

    def test_counts_cover_trimmed_cards_and_follow_the_published_stale_rule(self):
        state = {"project": "test", "tasks": {
            "a": {"status": "done", "title": "x" * 4000, "updated_at": "2026-10-05T11:00:00Z"},
            "b": {"status": "running", "updated_at": "2026-09-01T00:00:00Z"},
        }}
        with patch.object(board, "SNAPSHOT_BUDGET", 2500), patch.object(board, "now_utc", return_value=AT), \
                patch.object(board, "STALE_AFTER", board.timedelta(days=60)):
            snapshot = board.board_snapshot(state, costs={})
        self.assertEqual(set(snapshot["tasks"]), {"b"})
        self.assertEqual(snapshot["omitted"]["live"], 1)
        self.assertEqual(snapshot["task_counts"], {**{s: 0 for s in (*board.STATUSES, "stale")}, "done": 1, "running": 1})
        self.assertEqual(snapshot["stale_policy"]["after_days"], 60)

    def test_milestone_completion_remains_visible_under_payload_trimming(self):
        state = {"project": "test", "tasks": {
            "v1": {"status": "done", "stage": "live", "milestone": "V1", "evidence": "release", "title": "V1 slice"},
            "old": {"status": "done", "title": "x" * 4000},
        }}
        with patch.object(board, "SNAPSHOT_BUDGET", 2000):
            snapshot = board.board_snapshot(state, costs={})
        self.assertLessEqual(board.snapshot_size(snapshot), 2000)
        self.assertEqual(set(snapshot["tasks"]), {"v1"})
        self.assertEqual(snapshot["milestones"]["V1"]["tasks"], ["v1"])
        self.assertEqual(snapshot["omitted"]["live"], 1)

    def test_milestone_tags_follow_current_github_evidence(self):
        task = {"pr": 15, "status": "review", "milestone": "V1", "slice": "W15",
                "milestone_source": "pr_title_prefix"}
        info = pr(15, title="Routine fix")
        board.sync_pr_task(task, info, AT.isoformat())
        self.assertNotIn("milestone", task)
        self.assertNotIn("slice", task)
        task.update(milestone="V1", slice="W15")
        state, _ = board.reconcile_state({"tasks": {"pr-15": task}}, {(REPO, 15): (info, None)}, AT.isoformat())
        self.assertNotIn("milestone", state["tasks"]["pr-15"])

    def test_v1_uses_prefix_label_or_milestone_and_discovers_missing_slices(self):
        for title in ("W15: Invoice lifecycle", "W3b: Leads workspace", "W3c: Lead undo"):
            self.assertEqual(board.v1_metadata({"title": title})["milestone"], "V1")
        self.assertEqual(board.v1_metadata({"title": "Fix W15 thing"}), {})
        self.assertEqual(board.v1_metadata({"labels": [{"name": "V1"}]})["milestone_source"], "pr_label")
        self.assertEqual(board.v1_metadata({"milestone": {"title": "V1"}})["milestone_source"], "github_milestone")
        facts = {(REPO, 15): (pr(15, "MERGED", "W15: Invoices"), None),
                 (REPO, 3): (pr(3, title="W3c: Undo"), None)}
        result, _ = board.reconcile_state({"project": "test", "tasks": {}}, facts, AT.isoformat())
        groups = board.board_snapshot(result)["milestones"]
        self.assertEqual(groups["V1"]["counts"], {"waiting_on_release": 1, "review": 1})
        self.assertEqual(set(groups["V1"]["tasks"]), {"pr-3", "pr-15"})
        result["tasks"]["pr-15"].update(stage="live", evidence="Verified release")
        self.assertEqual(board.board_snapshot(result)["milestones"]["V1"]["counts"]["live"], 1)


class BoardFileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": self.tmp.name})
        self.env.start()
        os.environ.pop("PROGRESS_BOARD_SKIP_GH", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class DiscoveryRenderTests(BoardFileCase):
    PRIOR_VERIFIED = "2026-10-01T00:00:00Z"

    def render(self, discover):
        board.write_json({"project": board.LAUNCHD_BOARD, "tasks": {},
                          "github_sync": {"last_verified_at": self.PRIOR_VERIFIED, "failed": []}})
        release = (Path(self.tmp.name), "d" * 40, "https://release.example")
        with patch.object(board, "discover_v1", discover), patch.object(board, "latest_release", return_value=None), \
                patch.object(board, "deployed_release", return_value=release), \
                patch.object(board, "deployment_evidence", side_effect=lambda info, _: f"verified #{info['number']}"):
            board.render(board.LAUNCHD_BOARD, discover=True)
        return board.read_state(board.LAUNCHD_BOARD)

    def test_discovered_slices_become_cards_with_their_delivery_target_and_release_evidence(self):
        app = "jbookout/doctorcre-app"
        tasks = self.render(lambda: {(REPO, 15): (pr(15, "MERGED", "W15: Invoices"), None),
                                     (app, 3): (pr(3, title="W3c: Undo"), None)})["tasks"]
        self.assertEqual(set(tasks), {"pr-15", "app-pr-3"})
        self.assertEqual(tasks["pr-15"]["delivery_target"], board.AUTOMATIC_DELIVERY_TARGETS[REPO])
        self.assertEqual(tasks["app-pr-3"]["delivery_target"], board.AUTOMATIC_DELIVERY_TARGETS[app])
        self.assertEqual((tasks["pr-15"]["stage"], tasks["pr-15"]["evidence"]), ("live", "verified #15"))
        self.assertEqual(tasks["app-pr-3"]["milestone"], "V1")

    def test_discovery_failure_marks_sync_stale_and_keeps_last_verified(self):
        def unavailable():
            raise RuntimeError("REST unavailable")
        state = self.render(unavailable)
        self.assertEqual(state["tasks"], {})
        self.assertTrue(state["github_sync"]["stale"])
        self.assertEqual(state["github_sync"]["last_verified_at"], self.PRIOR_VERIFIED)
        self.assertIn({"card": "V1 discovery", "error": "REST unavailable"}, state["github_sync"]["failed"])


class ReconcileApplyTests(BoardFileCase):
    def setUp(self):
        super().setUp()
        board.write_json({"project": "test", "tasks": {
            "wd-0123456789abcdef": {"status": "blocked", "title": "Watchdog overlay", "executor": "orchestrator"},
            "pr-1": {"status": "review", "pr": 1, "title": "Keep me", "executor": "codex"}}})

    def apply(self, fetch):
        with patch.object(board, "discover_v1", return_value={}), patch.object(board, "fetch_pr", fetch), \
                patch.object(board, "publish_board") as publish, contextlib.redirect_stdout(io.StringIO()):
            board.main(["reconcile", "test", "--apply"])
        return publish

    def test_apply_writes_archives_and_publishes(self):
        publish = self.apply(lambda number, repo: (pr(number), None))
        publish.assert_called_once_with("test", refresh_inventory=False)
        state = board.read_state("test")
        self.assertEqual(set(state["tasks"]), {"pr-1"})
        self.assertEqual(state["tasks"]["pr-1"]["title"], "Keep me")
        self.assertIn("wd-0123456789abcdef", state["reconcile_archive"][-1]["tasks"])

    def test_apply_refuses_when_the_board_changes_during_reconcile(self):
        def concurrent_write(number, repo):
            board.write_json({**board.read_state("test"), "notes": ["written meanwhile"]})
            return pr(number), None
        with self.assertRaisesRegex(SystemExit, "Board changed during reconcile"):
            self.apply(concurrent_write)
        state = board.read_state("test")
        self.assertIn("wd-0123456789abcdef", state["tasks"])
        self.assertEqual(state["notes"], ["written meanwhile"])


if __name__ == "__main__":
    unittest.main()
