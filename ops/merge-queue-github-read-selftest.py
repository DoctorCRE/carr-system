#!/usr/bin/env python3
"""Synthetic gh replies only: no GitHub, credentials, queue writes or model calls."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.github_reader import GitHubReader  # noqa: E402

spec = importlib.util.spec_from_file_location("queue_github_read", REPO / "ops/merge-queue-github-read.py")
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

APPROVED = "a" * 40
CURRENT = "b" * 40
ITEM = {"repository": "synthetic/fixture", "number": 7, "approved_head": APPROVED}
OPEN = {"state": "open", "merged": False, "head": {"sha": APPROVED}, "mergeable_state": "clean"}


class FakeGh:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return subprocess.CompletedProcess(argv, *reply)


def fixture(*replies, env=None):
    gh = FakeGh(*replies)
    sleeps = []
    reader = GitHubReader(gh="gh", runner=gh, sleep=sleeps.append, env=env or {})
    return reader, gh, sleeps


def success(data=OPEN):
    return (0, json.dumps(data), "")


class Contract(unittest.TestCase):
    def read(self, reader):
        result = helper.read_result(ITEM["repository"], ITEM["number"], APPROVED, reader=reader)
        self.assertEqual(result["pending"], ITEM)
        return result

    def assert_read_only(self, gh):
        for argv, kwargs in gh.calls:
            self.assertEqual(argv, ["gh", "api", "repos/synthetic/fixture/pulls/7", "--include"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 30)

    def test_normal_open_closed_and_merged_reads(self):
        for state, merged in [("open", False), ("closed", False), ("closed", True)]:
            with self.subTest(state=state, merged=merged):
                reader, gh, sleeps = fixture(success({**OPEN, "state": state, "merged": merged}))
                result = self.read(reader)
                self.assertTrue(result["ok"])
                self.assertIsNone(result["blocker"])
                self.assertEqual(result["pull_request"]["state"], state)
                self.assertEqual(result["pull_request"]["merged"], merged)
                self.assert_read_only(gh)
                self.assertEqual(sleeps, [])

    def test_changed_current_head_does_not_replace_approved_head(self):
        reader, gh, _ = fixture(success({**OPEN, "head": {"sha": CURRENT}, "body": "ignored"}))
        result = self.read(reader)
        self.assertEqual(result["pull_request"]["head"], CURRENT)
        self.assertNotIn("body", result["pull_request"])
        self.assertEqual(result["pending"]["approved_head"], APPROVED)
        self.assert_read_only(gh)

    def test_authentication_stops_after_one_read_and_redacts(self):
        canary = "synthetic-secret-canary-123456789"
        for error in [f"Authorization: token {canary}\nHTTP 401: Bad credentials\n",
                      "Please run gh auth login\n"]:
            with self.subTest(error=error.splitlines()[-1]):
                reader, gh, sleeps = fixture((1, "", error), env={"GH_TOKEN": canary})
                result = self.read(reader)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["pull_request"])
                self.assertEqual(result["blocker"]["kind"], "authentication")
                self.assertFalse(result["blocker"]["retryable"])
                self.assertEqual(result["blocker"]["attempts"], 1)
                self.assertNotIn(canary, json.dumps(result))
                self.assertEqual(len(gh.calls), 1)
                self.assertEqual(sleeps, [])
                self.assert_read_only(gh)

    def test_authentication_before_long_wrapper_tail_stops_and_redacts(self):
        canary = "synthetic-secret-canary-123456789"
        error = f"HTTP 401: Bad credentials\nAuthorization: token {canary}\n" + "wrapper diagnostic " * 30
        reader, gh, sleeps = fixture(*[(1, "", error)] * 3, env={"GH_TOKEN": canary})
        result = self.read(reader)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["pull_request"])
        self.assertEqual(result["blocker"]["kind"], "authentication")
        self.assertFalse(result["blocker"]["retryable"])
        self.assertEqual(result["blocker"]["attempts"], 1)
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(sleeps, [])
        self.assertNotIn(canary, json.dumps(result))
        self.assertLess(len(result["blocker"]["detail"]), 400)
        self.assert_read_only(gh)

    def test_non_json_after_retry_reports_actual_attempts(self):
        reader, gh, sleeps = fixture((1, "", "HTTP 502: Bad Gateway\n"), (0, "not-json", ""))
        result = self.read(reader)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["pull_request"])
        self.assertFalse(result["blocker"]["retryable"])
        self.assertEqual(result["blocker"]["attempts"], 2)
        self.assertEqual(len(gh.calls), 2)
        self.assertEqual(sleeps, [5])
        self.assert_read_only(gh)

    def test_502_and_timeout_keep_existing_bounded_retry(self):
        for error in [(1, "", "HTTP 502: Bad Gateway\n"), subprocess.TimeoutExpired("gh", 30)]:
            with self.subTest(error=type(error).__name__):
                reader, gh, sleeps = fixture(error, error, error)
                result = self.read(reader)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["pull_request"])
                self.assertTrue(result["blocker"]["retryable"])
                self.assertEqual(result["blocker"]["attempts"], 3)
                self.assertEqual(sleeps, [5, 15])
                self.assert_read_only(gh)

    def test_transient_recovery_returns_real_snapshot(self):
        reader, gh, sleeps = fixture((1, "", "HTTP 502: Bad Gateway\n"), success())
        result = self.read(reader)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pull_request"]["head"], APPROVED)
        self.assertEqual(sleeps, [5])
        self.assert_read_only(gh)

    def test_rate_limits_do_not_retry_before_provider_reset(self):
        for error in ["HTTP 403: API rate limit exceeded\n", "HTTP 429: rate limit exceeded\n"]:
            with self.subTest(error=error):
                reader, gh, sleeps = fixture(*[(1, "", error)] * 3)
                result = self.read(reader)
                self.assertFalse(result["ok"])
                self.assertEqual(result["blocker"]["kind"], "rate_limit")
                self.assertTrue(result["blocker"]["retryable"])
                self.assertEqual(result["blocker"]["attempts"], 1)
                self.assertEqual(len(gh.calls), 1)
                self.assertEqual(sleeps, [])
                self.assert_read_only(gh)

    def test_permanent_read_failure_is_never_closed_or_empty_success(self):
        for error in ["HTTP 403: Forbidden\n", "HTTP 404: Not Found\n"]:
            with self.subTest(error=error):
                reader, gh, sleeps = fixture((1, "", error))
                result = self.read(reader)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["pull_request"])
                self.assertFalse(result["blocker"]["retryable"])
                self.assertEqual(len(gh.calls), 1)
                self.assertEqual(sleeps, [])
                self.assert_read_only(gh)

    def test_malformed_or_incomplete_success_is_blocked(self):
        replies = [(0, "not-json", ""), success({}), success([]),
                   success({**OPEN, "merged": None}), success({**OPEN, "state": "unknown"}),
                   success({**OPEN, "state": []}), success({**OPEN, "state": {}}),
                   success({**OPEN, "head": None}), success({**OPEN, "head": {"sha": ""}}),
                   success({**OPEN, "mergeable_state": 1})]
        for reply in replies:
            with self.subTest(reply=reply[1]):
                reader, gh, _ = fixture(reply)
                result = self.read(reader)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["pull_request"])
                self.assertFalse(result["blocker"]["retryable"])
                self.assert_read_only(gh)

    def test_invalid_snapshot_after_recovery_does_not_claim_attempt_count(self):
        reader, gh, sleeps = fixture((1, "", "HTTP 502: Bad Gateway\n"), success({}))
        result = self.read(reader)
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocker"]["kind"], "invalid_response")
        self.assertIsNone(result["blocker"]["attempts"])
        self.assertEqual(len(gh.calls), 2)
        self.assertEqual(sleeps, [5])
        self.assert_read_only(gh)

    def test_invalid_queue_identity_makes_no_read(self):
        for repository, number, head in [("owner/repo?x=1", 7, APPROVED),
                                         ("owner/..", 7, APPROVED),
                                         (ITEM["repository"], 0, APPROVED),
                                         (ITEM["repository"], True, APPROVED),
                                         (ITEM["repository"], 7, "short")]:
            reader, gh, _ = fixture()
            with self.subTest(repository=repository, number=number, head=head):
                with self.assertRaises(ValueError):
                    helper.read_result(repository, number, head, reader=reader)
                self.assertEqual(gh.calls, [])

    def test_cli_has_machine_readable_success_and_blocker_exit(self):
        for reply, expected in [(success(), 0), ((1, "", "HTTP 401: Bad credentials\n"), 1)]:
            with self.subTest(expected=expected):
                reader, gh, _ = fixture(reply)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = helper.main([ITEM["repository"], "7", APPROVED], reader=reader)
                result = json.loads(stdout.getvalue())
                self.assertEqual(code, expected)
                self.assertEqual(result["ok"], expected == 0)
                self.assertEqual(result["pending"], ITEM)
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                self.assert_read_only(gh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
