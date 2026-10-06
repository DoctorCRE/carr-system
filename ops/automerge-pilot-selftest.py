#!/usr/bin/env python3
"""Regression tests for the narrow overnight merge pilot."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("automerge_pilot.py")
WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "automerge-pilot.yml"
SPEC = importlib.util.spec_from_file_location("automerge_pilot", MODULE_PATH)
assert SPEC and SPEC.loader
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
BASE = "b" * 40
MERGE = "d" * 40


def policy_dict():
    return {
        "schema_version": 1,
        "policy_id": "carr-overnight-automerge-pilot-v1",
        "status": "experimental",
        "owner": "jbookout",
        "repository": "jbookout/carr-system",
        "base_branch": "main",
        "merge_method": "squash",
        "commit_title_prefix": "[carr-overnight-automerge-pilot-v1]",
        "required_label": "carr-automerge-pilot",
        "allowed_approvers": ["jbookout"],
        "required_check": {
            "name": "ops/ci.sh --strict",
            "app_slug": "github-actions",
            "workflow_file": "ci.yml",
            "workflow_name": "CI",
            "max_age_hours": 12,
        },
        "required_repository_control": {
            "ruleset_id": 20824501,
            "strict_required_status_checks_policy": True,
        },
        "pilot": {
            "not_before": "2026-08-14T12:00:00Z",
            "expires_at": "2026-08-21T12:00:00Z",
            "max_total_merges": 3,
            "max_candidates_per_run": 1,
        },
        "change_limits": {
            "max_files": 8,
            "max_changed_lines": 250,
            "allowed_prefixes": ["mcp-server/test/", "control-room/fixtures/", "workspace/fixtures/"],
            "allowed_exact": [],
            "allowed_ops_selftests": False,
            "forbidden_prefixes": [
                ".github/", "hooks/", "ops/config/", "ops/githooks/", "bin/",
                "migrations/", "db/",
            ],
            "forbidden_exact": [
                "requirements.txt", "requirements.lock", "package.json", "package-lock.json",
                "mcp-server/wrangler.toml", "ops/automerge_pilot.py",
                "ops/automerge-pilot-selftest.py",
            ],
        },
        "post_merge": {"timeout_seconds": 1200, "poll_seconds": 30},
    }


def snapshot(**overrides):
    value = {
        "number": 81,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "base": {"ref": "main", "sha": BASE},
        "head": {"sha": HEAD},
        "test_merge_sha": MERGE,
        "labels": ["carr-automerge-pilot"],
        "review_comments": 0,
        "files": [{
            "filename": "control-room/fixtures/example.v1.json",
            "status": "modified",
            "additions": 4,
            "deletions": 1,
            "patch": "@@ -1 +1 @@",
        }],
        "reviews": [],
        "approval_comments": [{
            "user": {"login": "jbookout"},
            "author_association": "OWNER",
            "body": f"/carr-automerge {HEAD}",
            "created_at": "2026-08-14T11:50:00Z",
            "html_url": "https://github.example/pull/81#issuecomment-1",
        }],
        "check_runs": [{
            "name": "ops/ci.sh --strict",
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-08-14T11:45:00Z",
            "html_url": "https://github.example/check/1",
            "app": {"slug": "github-actions"},
        }],
        "workflow_runs": [{
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-08-14T11:45:00Z",
            "html_url": "https://github.example/actions/runs/1",
        }],
    }
    value.update(overrides)
    return value


class PolicyTests(unittest.TestCase):
    def load(self, value):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            json.dump(value, fh)
            fh.flush()
            return pilot.load_policy(Path(fh.name), now=NOW)

    def test_valid_policy_loads_with_stable_digest(self):
        first = self.load(policy_dict())
        second = self.load(policy_dict())
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(len(first["digest"]), 64)

    def test_expired_policy_refuses_to_load_as_active(self):
        value = policy_dict()
        value["pilot"]["not_before"] = "2026-08-13T12:00:00Z"
        value["pilot"]["expires_at"] = "2026-08-14T11:59:59Z"
        with self.assertRaisesRegex(pilot.PolicyError, "expired"):
            self.load(value)

    def test_policy_cannot_allow_a_forbidden_prefix(self):
        value = policy_dict()
        value["change_limits"]["allowed_prefixes"].append("hooks/")
        with self.assertRaisesRegex(pilot.PolicyError, "overlaps forbidden"):
            self.load(value)

    def test_policy_rejects_more_than_five_business_day_window(self):
        value = policy_dict()
        value["pilot"]["expires_at"] = "2026-08-24T00:00:01Z"
        with self.assertRaisesRegex(pilot.PolicyError, "five business days"):
            self.load(value)

    def test_policy_cannot_reenable_ops_selftest_wildcard(self):
        value = policy_dict()
        value["change_limits"]["allowed_ops_selftests"] = True
        with self.assertRaisesRegex(pilot.PolicyError, "ops selftest wildcard"):
            self.load(value)


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy_dict()

    def decide(self, candidate=None, **kwargs):
        return pilot.evaluate_candidate(
            self.policy,
            candidate or snapshot(),
            now=NOW,
            prior_merges=kwargs.pop("prior_merges", []),
            enabled=kwargs.pop("enabled", True),
            **kwargs,
        )

    def test_exact_sha_approved_fixture_change_is_eligible(self):
        decision = self.decide()
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reason_codes"], [])

    def test_missing_remote_enable_is_disabled(self):
        decision = self.decide(enabled=False)
        self.assertFalse(decision["eligible"])
        self.assertIn("pilot_disabled", decision["reason_codes"])

    def test_approval_for_old_sha_is_refused(self):
        candidate = snapshot()
        candidate["approval_comments"][0]["body"] = "/carr-automerge " + "c" * 40
        decision = self.decide(candidate)
        self.assertIn("exact_sha_approval_missing", decision["reason_codes"])

    def test_wrong_approver_is_refused(self):
        candidate = snapshot()
        candidate["approval_comments"][0]["user"]["login"] = "someone-else"
        decision = self.decide(candidate)
        self.assertIn("exact_sha_approval_missing", decision["reason_codes"])

    def test_approval_before_ci_or_without_owner_association_is_refused(self):
        candidate = snapshot()
        candidate["approval_comments"][0]["created_at"] = "2026-08-14T11:40:00Z"
        self.assertIn("exact_sha_approval_missing", self.decide(candidate)["reason_codes"])
        candidate = snapshot()
        candidate["approval_comments"][0]["author_association"] = "COLLABORATOR"
        self.assertIn("exact_sha_approval_missing", self.decide(candidate)["reason_codes"])

    def test_failed_or_stale_required_check_is_refused(self):
        candidate = snapshot()
        candidate["check_runs"][0]["conclusion"] = "failure"
        self.assertIn("required_check_not_successful", self.decide(candidate)["reason_codes"])
        candidate = snapshot()
        candidate["check_runs"][0]["completed_at"] = "2026-08-13T20:00:00Z"
        self.assertIn("required_check_stale", self.decide(candidate)["reason_codes"])

    def test_forbidden_or_runtime_path_is_refused(self):
        for filename in [".github/workflows/ci.yml", "hooks/guard-unattended.py", "mcp-server/src/tools.js"]:
            candidate = snapshot()
            candidate["files"][0]["filename"] = filename
            with self.subTest(filename=filename):
                self.assertIn("changed_path_not_allowed", self.decide(candidate)["reason_codes"])

    def test_arbitrary_ops_selftest_is_not_an_allowed_change(self):
        candidate = snapshot()
        candidate["files"][0]["filename"] = "ops/new-authority-selftest.py"
        self.assertIn("changed_path_not_allowed", self.decide(candidate)["reason_codes"])

    def test_same_named_check_without_canonical_ci_workflow_is_refused(self):
        candidate = snapshot()
        candidate["workflow_runs"][0]["path"] = ".github/workflows/lookalike.yml"
        self.assertIn("required_workflow_run_missing", self.decide(candidate)["reason_codes"])

    def test_rename_delete_binary_and_size_are_refused(self):
        for status, patch, reason in [
            ("renamed", "@@", "unsupported_file_status"),
            ("removed", "@@", "unsupported_file_status"),
            ("modified", None, "binary_or_uninspectable_change"),
        ]:
            candidate = snapshot()
            candidate["files"][0].update(status=status, patch=patch)
            with self.subTest(status=status):
                self.assertIn(reason, self.decide(candidate)["reason_codes"])
        candidate = snapshot()
        candidate["files"][0].update(additions=251, deletions=0)
        self.assertIn("changed_lines_limit_exceeded", self.decide(candidate)["reason_codes"])

    def test_review_comment_draft_conflict_or_unknown_is_refused(self):
        cases = [
            (snapshot(review_comments=1), "review_comments_present"),
            (snapshot(draft=True), "draft_pr"),
            (snapshot(mergeable=False), "not_mergeable"),
            (snapshot(mergeable=None), "mergeability_unknown"),
        ]
        for candidate, reason in cases:
            with self.subTest(reason=reason):
                self.assertIn(reason, self.decide(candidate)["reason_codes"])

    def test_prior_failed_merge_stops_lane_and_total_cap_holds(self):
        failed = [{"number": 70, "merge_commit_sha": "d" * 40, "ci_conclusion": "failure"}]
        self.assertIn("prior_pilot_merge_unhealthy", self.decide(prior_merges=failed)["reason_codes"])
        good = [
            {"number": n, "merge_commit_sha": str(n) * 40, "ci_conclusion": "success"}
            for n in (1, 2, 3)
        ]
        self.assertIn("pilot_merge_cap_reached", self.decide(prior_merges=good)["reason_codes"])

    def test_candidate_selection_refuses_ambiguity(self):
        decisions = [self.decide(snapshot(number=81)), self.decide(snapshot(number=82))]
        selected = pilot.select_candidate(decisions, max_candidates=1)
        self.assertFalse(selected["eligible"])
        self.assertIn("eligible_candidate_count_exceeded", selected["reason_codes"])


class TransactionTests(unittest.TestCase):
    def test_checkout_parents_must_match_planned_base_and_head(self):
        pilot.verify_checkout_parents(["m" * 40, BASE, HEAD], BASE, HEAD)
        with self.assertRaisesRegex(pilot.PilotRefusal, "checkout_parent_mismatch"):
            pilot.verify_checkout_parents(["m" * 40, BASE, "c" * 40], BASE, HEAD)

    def test_conditional_merge_payload_is_sha_bound_and_squash_only(self):
        payload = pilot.merge_payload(HEAD, "squash", "[carr-overnight-automerge-pilot-v1]", 81)
        self.assertEqual(payload, {
            "sha": HEAD,
            "merge_method": "squash",
            "commit_title": "[carr-overnight-automerge-pilot-v1] PR #81",
        })
        with self.assertRaisesRegex(pilot.PolicyError, "squash"):
            pilot.merge_payload(HEAD, "merge", "[carr-overnight-automerge-pilot-v1]", 81)

    def test_race_refuses_before_merge_adapter_is_called(self):
        class FakeApi:
            merge_calls = []

            def get_snapshot(self, _number):
                return snapshot(head={"sha": "c" * 40})

            def merge(self, number, payload):
                self.merge_calls.append((number, payload))

            def assert_repository_control(self, _policy):
                return None

        api = FakeApi()
        with self.assertRaisesRegex(pilot.PilotRefusal, "head_sha_changed"):
            pilot.execute_conditional_merge(api, 81, HEAD, BASE, MERGE, policy_dict(), NOW)
        self.assertEqual(api.merge_calls, [])

    def test_changed_rehearsal_merge_sha_refuses_before_merge(self):
        class FakeApi:
            merge_calls = []

            def get_snapshot(self, _number):
                return snapshot(test_merge_sha="e" * 40)

            def merge(self, number, payload):
                self.merge_calls.append((number, payload))

            def assert_repository_control(self, _policy):
                return None

        api = FakeApi()
        with self.assertRaisesRegex(pilot.PilotRefusal, "test_merge_sha_changed"):
            pilot.execute_conditional_merge(api, 81, HEAD, BASE, MERGE, policy_dict(), NOW)
        self.assertEqual(api.merge_calls, [])

    def test_post_merge_ci_is_explicitly_dispatched_on_main(self):
        api = object.__new__(pilot.GitHubApi)
        api.repo = "jbookout/carr-system"
        calls = []
        api.request = lambda method, path, data=None: calls.append((method, path, data)) or {}
        api.dispatch_workflow("ci.yml", "main")
        self.assertEqual(calls, [(
            "POST",
            "/repos/jbookout/carr-system/actions/workflows/ci.yml/dispatches",
            {"ref": "main"},
        )])

    def test_live_repository_control_requires_strict_ci_and_no_bypass(self):
        api = object.__new__(pilot.GitHubApi)
        api.repo = "jbookout/carr-system"
        good = {
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "bypass_actors": [],
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "required_status_checks", "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "ops/ci.sh --strict"}],
                }},
            ],
        }
        api.get = lambda _path: good
        api.assert_repository_control(policy_dict())
        bad = json.loads(json.dumps(good))
        bad["rules"][2]["parameters"]["strict_required_status_checks_policy"] = False
        api.get = lambda _path: bad
        with self.assertRaisesRegex(pilot.PilotRefusal, "ruleset_strict_update_missing"):
            api.assert_repository_control(policy_dict())

    def test_history_paginates_instead_of_trusting_page_one(self):
        api = object.__new__(pilot.GitHubApi)
        api.get = lambda path: ([{"number": n} for n in range(100)] if path.endswith("page=1") else [{"number": 100}])
        items = api.get_all("/repos/jbookout/carr-system/pulls?state=closed")
        self.assertEqual(len(items), 101)

    def test_prior_pilot_merge_uses_immutable_commit_marker_not_label(self):
        api = object.__new__(pilot.GitHubApi)
        api.repo = "jbookout/carr-system"
        api.get_all = lambda _path: [{
            "number": 70,
            "labels": [],
            "merged_at": "2026-08-14T13:00:00Z",
            "merge_commit_sha": "7" * 40,
        }]
        api.get = lambda _path: {"commit": {"message": "[carr-overnight-automerge-pilot-v1] PR #70\n\nbody"}}
        api.get_workflow_runs = lambda _sha, _workflow="ci.yml": [{
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "head_sha": "7" * 40,
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-08-14T10:30:00Z",
        }]
        prior = api.prior_merges(policy_dict())
        self.assertEqual(prior, [{
            "number": 70,
            "merge_commit_sha": "7" * 40,
            "ci_conclusion": "success",
        }])


class WorkflowBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW_PATH.read_text()

    def test_pilot_strict_verify_job_has_current_runtime_headroom(self):
        # The pilot runs the whole strict suite and keeps its 30-minute floor.
        # CI matrix budgets and their wiring belong to ops/ci-selftest.py.
        pilot_verify = self.workflow.split(
            "\n  verify:\n", 1)[1].split("\n    permissions:\n", 1)[0]
        timeout_line = next(
            line for line in pilot_verify.splitlines()
            if "timeout-minutes:" in line)
        self.assertGreaterEqual(int(timeout_line.split(":", 1)[1]), 30)

    def test_untrusted_pull_request_code_never_gets_write_permissions(self):
        verify_block = self.workflow.split("\n  verify:\n", 1)[1].split("\n  merge:\n", 1)[0]
        self.assertIn("contents: read", verify_block)
        self.assertNotIn("contents: write", verify_block)
        self.assertNotIn("pull-requests: write", verify_block)

    def test_merge_capable_job_checks_out_protected_main(self):
        merge_block = self.workflow.split("\n  merge:\n", 1)[1]
        self.assertIn("contents: write", merge_block)
        self.assertIn("pull-requests: write", merge_block)
        self.assertIn("actions: write", merge_block)
        self.assertIn("ref: main", merge_block)
        self.assertNotIn("merge_ref", merge_block)

    def test_workflow_has_no_pull_request_target_and_pins_actions(self):
        self.assertNotIn("pull_request_target", self.workflow)
        action_lines = [line.strip() for line in self.workflow.splitlines() if "uses: actions/" in line]
        self.assertTrue(action_lines)
        for line in action_lines:
            self.assertRegex(line, r"uses: actions/[a-z-]+@[0-9a-f]{40}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
