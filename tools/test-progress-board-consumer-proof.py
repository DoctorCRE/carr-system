#!/usr/bin/env python3
"""Completion requires an exercise receipt for the delivered capability."""

import importlib.util
import copy
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("board", Path(__file__).with_name("progress_board.py"))
assert SPEC is not None and SPEC.loader is not None
board = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(board)


class ConsumerCompletion(unittest.TestCase):
    def proven_task(self):
        return {"repo": "jbookout/doctorcre-app", "pr": 42, "status": "done",
                "stage": "live", "pr_phase": "Merged", "merge_sha": "a" * 40,
                "delivery_target": "app", "delivery_workflow": "open synthetic deal",
                "evidence": "Synthetic deal opened on delivered source",
                "consumer_proof": {"schema": "carr-consumer-proof.v1", "outcome": "pass",
                                   "repo": "jbookout/doctorcre-app", "source_sha": "a" * 40,
                                   "target": "https://app.doctorcre.com", "workflow": "open synthetic deal",
                                   "evidence_ref": "synthetic-exercise-42", "observed_at": "2026-10-01T12:00:00Z"}}

    def test_merged_source_with_prose_only_stays_pending(self):
        task = {"repo": "jbookout/doctorcre-app", "pr": 42, "status": "done",
                "stage": "live", "pr_phase": "Merged", "merge_sha": "a" * 40,
                "evidence": "Release readback contains the merged source"}
        self.assertEqual(board.task_stage(task), "merged")
        self.assertIsNone(board.completed_at(task))

    def test_only_a_passing_exercise_matching_every_binding_is_live(self):
        task = self.proven_task()
        self.assertEqual(board.task_stage(task), "live")
        for field, value in {"schema": "other.v1", "outcome": "fail", "repo": "jbookout/carr-system",
                             "source_sha": "b" * 40, "target": "https://staging.doctorcre.com",
                             "workflow": "different workflow", "evidence_ref": " ", "observed_at": "not a date"}.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(task)
                changed["consumer_proof"][field] = value
                self.assertEqual(board.task_stage(changed), "merged")
        for field in ("delivery_target", "delivery_workflow", "merge_sha"):
            with self.subTest(missing=field):
                changed = copy.deepcopy(task)
                del changed[field]
                self.assertEqual(board.task_stage(changed), "merged")

    def test_release_consumers_keep_unexercised_source_pending(self):
        release = {"sha": "a" * 40, "lane": "app", "ts": "synthetic", "source": "fixture"}
        with patch.object(board, "latest_release", return_value=release):
            for proven in (False, True):
                for consumer in ("all-repos", "project"):
                    with self.subTest(proven=proven, consumer=consumer):
                        task = self.proven_task()
                        task.update(stage="merged", status="review")
                        if not proven:
                            del task["consumer_proof"]
                        if consumer == "all-repos":
                            board.auto_live(task, task["repo"], "synthetic", ["js/feature.js"])
                        else:
                            board.delivered_live(task, task["repo"], "synthetic", "Released source observed")
                        self.assertEqual(task["stage"], "live" if proven else "merged")
                        if not proven:
                            self.assertIn("consumer", task["release_wait"])

    def test_old_and_failed_claims_are_downgraded_in_the_app_snapshot(self):
        for no_pr in (False, True):
            with self.subTest(no_pr=no_pr):
                task = self.proven_task()
                task["consumer_proof"]["outcome"] = "fail"
                if no_pr:
                    task.pop("pr")
                board.normalize_task(task)
                snapshot = board.board_snapshot({"project": "synthetic", "tasks": {"feature": task}}, costs={})
                card = snapshot["tasks"]["feature"]
                self.assertNotEqual(card["stage"], "live")
                self.assertNotEqual(card["status"], "done")
                self.assertNotIn("completed_at", card)
                self.assertIn("consumer", card["release_wait"])

    def test_command_records_a_failed_receipt_without_completing(self):
        task = self.proven_task()
        task["consumer_proof"]["outcome"] = "fail"
        state = {"project": "synthetic", "tasks": {"feature": task}}
        args = board.parser().parse_args(["task", "synthetic", "feature", "--consumer-proof",
                                         json.dumps(task["consumer_proof"])])
        board.update_task(state, args)
        self.assertEqual(state["tasks"]["feature"]["stage"], "merged")
        self.assertEqual(state["tasks"]["feature"]["consumer_proof"]["outcome"], "fail")

    def test_cli_persists_an_exercised_outcome_and_then_revokes_it_on_failure(self):
        proof = self.proven_task()["consumer_proof"]
        with tempfile.TemporaryDirectory() as root:
            env = {**os.environ, "PROGRESS_BOARD_ROOT": root, "PROGRESS_BOARD_LOCAL_ONLY": "1",
                   "PROGRESS_BOARD_SKIP_GH": "1", "PROGRESS_BOARD_SKIP_PROBE": "1"}
            def run(*args):
                return subprocess.run([sys.executable, str(Path(board.__file__)), *args],
                                      env=env, text=True, capture_output=True, check=True)
            run("init", "synthetic", "--title", "Synthetic exercise")
            run("task", "synthetic", "feature", "--title", "Synthetic usable outcome", "--status", "done",
                "--executor", "Codex", "--repo", proof["repo"], "--delivery-target", "app",
                "--delivery-workflow", proof["workflow"], "--delivery-source-sha", proof["source_sha"],
                "--consumer-proof", json.dumps(proof))
            path = Path(root) / "boards/synthetic.json"
            stored = json.loads(path.read_text())["tasks"]["feature"]
            self.assertEqual((stored["stage"], stored["status"]), ("live", "done"))
            self.assertIn(proof["workflow"], stored["evidence"])
            self.assertIn(proof["evidence_ref"], stored["evidence"])
            proof["outcome"] = "fail"
            run("task", "synthetic", "feature", "--consumer-proof", json.dumps(proof))
            stored = json.loads(path.read_text())["tasks"]["feature"]
            self.assertEqual((stored["stage"], stored["status"]), ("review", "review"))
            self.assertNotIn("completed_at", stored)

    def test_refresh_preserves_proof_and_invalidates_it_when_source_changes(self):
        task = self.proven_task()
        task.update(title="Synthetic outcome", completed_at="2026-10-01T12:00:00Z")
        key = "doctorcre-app-42"
        info = {"number": 42, "mergeCommit": {"oid": "a" * 40}, "state": "MERGED"}
        with patch.object(board, "latest_release", return_value=None):
            refreshed = board.assemble_all_repos({"tasks": {key: task}}, {task["repo"]: ([], [info])})
            card = refreshed["tasks"][key]
            self.assertEqual(board.task_stage(card), "live")
            self.assertEqual(card["consumer_proof"], task["consumer_proof"])
            info["mergeCommit"]["oid"] = "b" * 40
            refreshed = board.assemble_all_repos(refreshed, {task["repo"]: ([], [info])})
            self.assertEqual(board.task_stage(refreshed["tasks"][key]), "merged")

    def cli_observations(self, observations):
        proof = self.proven_task()["consumer_proof"]
        with tempfile.TemporaryDirectory() as root:
            env = {**os.environ, "PROGRESS_BOARD_ROOT": root, "PROGRESS_BOARD_LOCAL_ONLY": "1",
                   "PROGRESS_BOARD_SKIP_GH": "1", "PROGRESS_BOARD_SKIP_PROBE": "1"}

            def run(*args):
                subprocess.run([sys.executable, str(Path(board.__file__)), *args],
                               env=env, text=True, capture_output=True, check=True)

            run("init", "synthetic", "--title", "Synthetic chronology")
            run("task", "synthetic", "feature", "--title", "Synthetic workflow A", "--status", "done",
                "--executor", "Codex", "--repo", proof["repo"], "--delivery-target", "app",
                "--delivery-workflow", proof["workflow"], "--delivery-source-sha", proof["source_sha"])
            states = []
            for observation in observations:
                run("task", "synthetic", "feature", "--status", "done",
                    "--consumer-proof", json.dumps(observation))
                states.append(json.loads((Path(root) / "boards/synthetic.json").read_text())["tasks"]["feature"])
            return states

    def test_cli_unrelated_workflow_cannot_erase_failure_before_stale_pass(self):
        proof = self.proven_task()["consumer_proof"]
        failed = {**proof, "outcome": "fail", "observed_at": "2026-10-03T12:00:00Z"}
        unrelated = {**failed, "workflow": "workflow B", "observed_at": "2026-10-04T12:00:00Z"}
        replay = {**proof, "observed_at": "2026-10-02T12:00:00Z"}
        states = self.cli_observations([failed, unrelated, replay])
        self.assertTrue(all(task["stage"] != "live" for task in states))
        self.assertEqual(states[-1]["consumer_proof"], failed)

    def test_cli_newer_matching_pass_restores_completion(self):
        proof = self.proven_task()["consumer_proof"]
        failed = {**proof, "outcome": "fail", "observed_at": "2026-10-03T12:00:00Z"}
        passed = {**proof, "observed_at": "2026-10-05T12:00:00Z"}
        before, after = self.cli_observations([failed, passed])
        self.assertNotEqual(before["stage"], "live")
        self.assertEqual((after["stage"], after["status"]), ("live", "done"))

    def test_cli_unrelated_failure_preserves_passing_workflow(self):
        proof = self.proven_task()["consumer_proof"]
        passed = {**proof, "observed_at": "2026-10-03T12:00:00Z"}
        unrelated = {**passed, "workflow": "workflow B", "outcome": "fail",
                     "observed_at": "2026-10-04T12:00:00Z"}
        states = self.cli_observations([passed, unrelated])
        self.assertTrue(all(task["stage"] == "live" for task in states))
        self.assertEqual(states[-1]["consumer_proof"], passed)

    def test_cli_rejects_malformed_observation(self):
        proof = self.proven_task()["consumer_proof"]
        with self.assertRaises(subprocess.CalledProcessError):
            self.cli_observations([{**proof, "observed_at": "not a timestamp"}])

    def test_refresh_keeps_each_bindings_latest_observation(self):
        task = self.proven_task()
        proof = task["consumer_proof"]
        state = {"tasks": {"feature": task}}
        failed = {**proof, "outcome": "fail", "observed_at": "2026-10-03T12:00:00Z"}
        for observation in (failed, {**failed, "workflow": "workflow B", "observed_at": "2026-10-04T12:00:00Z"}):
            args = board.parser().parse_args(["task", "synthetic", "feature", "--consumer-proof", json.dumps(observation)])
            board.update_task(state, args)
        key = "doctorcre-app-42"
        info = {"number": 42, "mergeCommit": {"oid": "a" * 40}, "state": "MERGED"}
        with patch.object(board, "latest_release", return_value=None):
            refreshed = board.assemble_all_repos({"tasks": {key: state["tasks"]["feature"]}}, {task["repo"]: ([], [info])})
        args = board.parser().parse_args(["task", "synthetic", key, "--consumer-proof", json.dumps(proof)])
        board.update_task(refreshed, args)
        self.assertEqual(refreshed["tasks"][key]["consumer_proof"], failed)
        self.assertEqual(board.task_stage(refreshed["tasks"][key]), "merged")
        snapshot = board.board_snapshot(refreshed, costs={})["tasks"][key]
        self.assertNotIn("consumer_proofs", snapshot)

    def test_receipt_replays_cannot_erase_a_newer_observation(self):
        task = self.proven_task()
        old_pass = copy.deepcopy(task["consumer_proof"])
        failed = {**old_pass, "outcome": "fail", "observed_at": "2026-10-02T12:00:00Z"}
        new_pass = {**old_pass, "observed_at": "2026-10-03T12:00:00Z"}
        state = {"tasks": {"feature": task}}
        release = {"sha": "a" * 40, "lane": "app", "ts": "synthetic", "source": "fixture"}

        def record(proof, *extra):
            args = board.parser().parse_args(["task", "synthetic", "feature", "--consumer-proof", json.dumps(proof), *extra])
            board.update_task(state, args)
            with patch.object(board, "latest_release", return_value=release):
                board.auto_live(state["tasks"]["feature"], task["repo"], "2026-10-04T12:00:00Z", ["js/feature.js"])
            return board.task_stage(state["tasks"]["feature"])

        self.assertEqual(record(failed), "merged")
        self.assertEqual(record(old_pass), "merged", "an old pass cannot erase the newer failure")
        self.assertEqual(state["tasks"]["feature"]["consumer_proof"], failed)
        self.assertEqual(record(new_pass), "live", "a newer exercised pass restores completion")
        self.assertEqual(record(failed), "live", "an old failure cannot erase the newer pass")
        self.assertEqual(record({**new_pass, "outcome": "fail"}), "merged")
        self.assertEqual(record(new_pass), "merged", "a tied pass cannot erase a failure")
        other_workflow = {**old_pass, "workflow": "another synthetic workflow"}
        self.assertEqual(record(other_workflow, "--delivery-workflow", other_workflow["workflow"]), "live")
        self.assertEqual(record(old_pass, "--delivery-workflow", old_pass["workflow"]), "merged")
        self.assertEqual(record({**new_pass, "observed_at": "2026-10-05T12:00:00Z"}), "live")

    def test_malformed_receipts_and_retired_tasks_cannot_complete(self):
        for proof in (None, [], "pass", {"outcome": "pass"}):
            task = self.proven_task()
            task["consumer_proof"] = proof
            self.assertEqual(board.task_stage(task), "merged")
        for status in ("failed", "superseded"):
            task = self.proven_task()
            task["status"] = status
            board.normalize_task(task)
            self.assertEqual(task["status"], status)
            self.assertNotEqual(board.task_stage(task), "live")


if __name__ == "__main__":
    unittest.main()
