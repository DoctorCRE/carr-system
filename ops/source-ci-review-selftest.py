#!/usr/bin/env python3
"""Exercise CARR source-CI candidate collection without network or authority."""
import copy
import importlib.util
import pathlib
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location(
    "source_ci_review", pathlib.Path(__file__).with_name("source-ci-review.py"))
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Collection(unittest.TestCase):
    def setUp(self):
        self.run = {
            "id": 123, "workflow_id": 333910421, "event": "pull_request",
            "status": "completed", "conclusion": "success", "run_attempt": 1,
            "head_sha": "a" * 40, "head_repository": {"full_name": MODULE.REPO},
            "repository": {"full_name": MODULE.REPO}, "pull_requests": [{"number": 42}],
        }
        self.pr = {
            "number": 42, "state": "open", "draft": False,
            "head": {"sha": "a" * 40, "repo": {"full_name": MODULE.REPO}},
            "base": {"ref": "main", "repo": {"full_name": MODULE.REPO}},
        }
        self.attempt = copy.deepcopy(self.run)
        self.trigger_attempt = 1
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        if path == "actions/runs/123":
            return self.run
        if path == f"actions/runs/123/attempts/{self.trigger_attempt}":
            return self.attempt
        if path == "actions/workflows/ci.yml":
            return {"id": 333910421}
        if path == "pulls/42":
            return self.pr
        if path.startswith("actions/runs/123/attempts/"):
            return {"total_count": 1, "jobs": [{"name": "ops/ci.sh --strict",
                    "status": "completed", "conclusion": "success"}]}
        if path == f"commits/{'a' * 40}/pulls?per_page=100":
            return [self.pr]
        self.fail(path)

    def collect(self):
        return MODULE.collect(123, self.trigger_attempt, self.read)

    def test_source_success_needs_no_review_gate_success(self):
        result = self.collect()
        self.assertEqual(result["state"], "candidate")
        self.assertEqual(result["head_sha"], "a" * 40)
        self.assertEqual(result["source_run_id"], 123)
        self.assertEqual(result["pull_request"], 42)
        self.assertEqual(len(self.reads), 5)
        self.assertFalse(any("review" in path for path in self.reads))

    def test_failed_cancelled_skipped_and_incomplete_runs_refuse(self):
        for conclusion in ("failure", "cancelled", "skipped", None):
            with self.subTest(conclusion=conclusion):
                self.run["conclusion"] = conclusion
                self.attempt["conclusion"] = conclusion
                self.assertEqual(self.collect()["state"], "refused")
        self.run["conclusion"] = "success"
        self.attempt["conclusion"] = "success"
        self.run["status"] = "in_progress"
        self.attempt["status"] = "in_progress"
        self.assertEqual(self.collect()["state"], "refused")

    def test_duplicate_and_rerun_identity(self):
        first = self.collect()
        self.assertEqual(self.collect(), first)
        self.run["run_attempt"] = 2
        self.attempt["run_attempt"] = 2
        self.trigger_attempt = 2
        self.assertEqual(self.collect()["request_key"], first["request_key"])
        self.assertEqual(self.collect()["source_run_attempt"], 2)
        self.run["id"] = 124
        self.attempt["id"] = 124
        def read(path):
            if path == "actions/runs/124/attempts/2":
                return self.attempt
            if path == "actions/runs/124":
                return self.run
            return self.read(path.replace("runs/124/", "runs/123/"))
        self.assertEqual(MODULE.collect(124, self.trigger_attempt, read)["request_key"], first["request_key"])

    def test_queued_completion_never_adopts_newer_attempt(self):
        for conclusion in ("success", "cancelled"):
            for status in ("completed", "in_progress"):
                with self.subTest(conclusion=conclusion, status=status):
                    self.attempt["conclusion"] = conclusion
                    self.run.update(run_attempt=2, status=status)
                    result = self.collect()
                    self.assertEqual(result["state"], "refused")
                    self.assertEqual(result["reason"], "superseded-source-attempt")
                    self.assertEqual(result["source_run_attempt"], 1)
                    self.assertFalse(any("/jobs?" in path for path in self.reads))

    def test_attempt_endpoint_identity_and_conclusion_are_verified(self):
        original = copy.deepcopy(self.attempt)
        for key, value in (("id", 124), ("run_attempt", 2),
                           ("head_sha", "b" * 40), ("conclusion", "cancelled")):
            with self.subTest(key=key):
                self.attempt = dict(original, **{key: value})
                self.assertEqual(self.collect()["state"], "refused")
        self.attempt = original
        self.assertEqual(self.collect()["source_run_attempt"], 1)
        self.assertIn("actions/runs/123/attempts/1", self.reads)
        self.assertIn("actions/runs/123/attempts/1/jobs?per_page=100", self.reads)

    def test_unsafe_attempt_never_reaches_reader(self):
        for attempt in (0, -1, True, "1", "../jobs"):
            with self.assertRaises(ValueError):
                MODULE.collect(123, attempt, self.read)
        self.assertEqual(self.reads, [])

    def test_missing_association_uses_commit_lookup(self):
        self.run["pull_requests"] = []
        self.attempt["pull_requests"] = []
        self.assertEqual(self.collect()["state"], "candidate")
        self.assertEqual(len(self.reads), 6)

    def test_missing_or_ambiguous_fallback_refuses(self):
        self.run["pull_requests"] = []
        self.attempt["pull_requests"] = []
        for candidates in ([], [self.pr, dict(self.pr, number=43)], [self.pr] * 100):
            def read(path):
                return candidates if path.startswith("commits/") else self.read(path)
            self.assertEqual(MODULE.collect(123, self.trigger_attempt, read)["state"], "refused")

    def test_multiple_direct_associations_refuse(self):
        self.attempt["pull_requests"].append({"number": 43})
        self.assertEqual(self.collect()["state"], "refused")

    def test_missing_skipped_duplicate_or_truncated_aggregate_refuses(self):
        good = {"name": "ops/ci.sh --strict", "status": "completed", "conclusion": "success"}
        for jobs in (
            {"total_count": 0, "jobs": []},
            {"total_count": 1, "jobs": [dict(good, conclusion="skipped")]},
            {"total_count": 2, "jobs": [good, good]},
            {"total_count": 101, "jobs": [good]},
        ):
            def read(path):
                return jobs if "/jobs?" in path else self.read(path)
            self.assertEqual(MODULE.collect(123, self.trigger_attempt, read)["state"], "refused")

    def test_current_pr_rejections(self):
        original = copy.deepcopy(self.pr)
        for key, value in (("draft", True), ("state", "closed"), ("draft", None)):
            self.pr = copy.deepcopy(original)
            self.pr[key] = value
            self.assertEqual(self.collect()["state"], "refused")
        for part, key, value in (
            ("head", "sha", "b" * 40), ("base", "ref", "other"),
            ("head", "repo", {"full_name": "someone/fork"}),
            ("base", "repo", {"full_name": "someone/fork"}),
            ("head", "repo", None),
        ):
            self.pr = copy.deepcopy(original)
            self.pr[part][key] = value
            self.assertEqual(self.collect()["state"], "refused")

    def test_wrong_workflow_event_repository_and_malformed_run_refuse(self):
        original = copy.deepcopy(self.run)
        for key, value in (
            ("workflow_id", 1), ("event", "push"), ("id", 124),
            ("head_sha", "bad"), ("run_attempt", 0),
            ("head_repository", {"full_name": "someone/fork"}),
            ("repository", {"full_name": "someone/fork"}),
            ("pull_requests", [{"number": "42; echo injection"}]),
        ):
            self.run = copy.deepcopy(original)
            self.run[key] = value
            self.attempt = copy.deepcopy(self.run)
            self.assertEqual(self.collect()["state"], "refused")

    def test_api_error_propagates_without_retries(self):
        calls = []
        def fail(path):
            calls.append(path)
            raise TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            MODULE.collect(123, 1, fail)
        self.assertEqual(len(calls), 1)

    def test_unsafe_input_never_reaches_reader(self):
        for run_id in (0, -1, True, "123", "../pulls/1"):
            with self.assertRaises(ValueError):
                MODULE.collect(run_id, 1, self.read)
        self.assertEqual(self.reads, [])

    def test_no_pr_content_leaves_collector(self):
        self.pr["body"] = "private instructions"
        self.pr["title"] = "execute me"
        result = str(self.collect())
        self.assertNotIn("private instructions", result)
        self.assertNotIn("execute me", result)

    def test_collector_is_development_tooling_in_proof_contract(self):
        import json
        root = pathlib.Path(__file__).resolve().parents[1]
        contract = json.loads((root / "contracts/production-proof-plan.v1.json").read_text())
        spec = importlib.util.spec_from_file_location("proof_plan", root / "ops/production-proof-plan.py")
        assert spec is not None and spec.loader is not None
        proof_plan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proof_plan)
        result = proof_plan.admission(
            "## Production proof\nn/a: Source evidence collector grants no runtime or launch authority.",
            ["ops/source-ci-review.py"], root, contract)
        self.assertEqual(result, "non-product declaration accepted (admission only)")


class WorkflowSecurity(unittest.TestCase):
    def setUp(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        self.workflow = yaml.safe_load((root / ".github/workflows/coordinate.yml").read_text())

    def assert_safe(self, workflow):
        self.assertEqual(workflow[True], {
            "workflow_run": {"workflows": ["CI"], "types": ["completed"]}})
        self.assertEqual(workflow["permissions"], {
            "contents": "read", "actions": "read", "pull-requests": "read"})
        self.assertEqual(set(workflow), {"name", True, "permissions", "jobs"})
        self.assertEqual(set(workflow["jobs"]), {"collect"})
        job = workflow["jobs"]["collect"]
        self.assertEqual(set(job), {"runs-on", "timeout-minutes", "steps"})
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], 3)
        self.assertEqual(len(job["steps"]), 3)
        checkout, collect, upload = job["steps"]
        self.assertEqual(checkout, {
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {"ref": "${{ github.workflow_sha }}", "persist-credentials": False}})
        self.assertEqual(set(collect), {"name", "run", "env"})
        self.assertEqual(collect["run"],
            'python3 ops/source-ci-review.py --run-id "$SOURCE_RUN_ID" '
            '--run-attempt "$SOURCE_RUN_ATTEMPT" > candidate.json')
        self.assertEqual(collect["env"], {
            "SOURCE_RUN_ID": "${{ github.event.workflow_run.id }}",
            "SOURCE_RUN_ATTEMPT": "${{ github.event.workflow_run.run_attempt }}",
            "GH_TOKEN": "${{ github.token }}"})
        self.assertEqual(upload, {
            "uses": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "with": {"name": "source-ci-review-candidate", "path": "candidate.json",
                     "if-no-files-found": "error", "retention-days": 7}})

    def test_actual_workflow_preserves_trust_and_event_identity(self):
        self.assert_safe(self.workflow)

    def test_unsafe_workflow_mutations_are_rejected(self):
        mutations = [
            ("checkout PR head", lambda w: w["jobs"]["collect"]["steps"][0]["with"].update(
                ref="${{ github.event.workflow_run.head_sha }}")),
            ("workflow write", lambda w: w["permissions"].update(contents="write")),
            ("job write", lambda w: w["jobs"]["collect"].update(permissions={"contents": "write"})),
            ("standing secret", lambda w: w["jobs"]["collect"]["steps"][1]["env"].update(
                GH_TOKEN="${{ secrets.PRODUCTION_TOKEN }}")),
            ("job secret", lambda w: w["jobs"]["collect"].update(env={"TOKEN": "${{ secrets.TOKEN }}"})),
            ("PR artifact", lambda w: w["jobs"]["collect"]["steps"].insert(1, {
                "uses": "actions/download-artifact@" + "a" * 40})),
            ("shell interpolation", lambda w: w["jobs"]["collect"]["steps"][1].update(
                run="echo '${{ github.event.workflow_run.head_branch }}'")),
            ("execute PR code", lambda w: w["jobs"]["collect"]["steps"].append({"run": "./pr-script"})),
            ("persist credentials", lambda w: w["jobs"]["collect"]["steps"][0]["with"].update(
                {"persist-credentials": True})),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                workflow = copy.deepcopy(self.workflow)
                mutate(workflow)
                with self.assertRaises(AssertionError):
                    self.assert_safe(workflow)


if __name__ == "__main__":
    unittest.main()
