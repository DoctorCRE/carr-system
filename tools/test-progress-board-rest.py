#!/usr/bin/env python3
"""REST-only PR refresh, terminal retention and recorded GraphQL parity."""
import copy
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("board", Path(__file__).with_name("progress_board.py"))
assert SPEC is not None and SPEC.loader is not None
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
REPO = "jbookout/carr-system"
SHA = "a" * 40


def pull(number=1, **extra):
    return {"number": number, "state": "open", "draft": False, "head": {"sha": SHA, "ref": "fix"},
            "user": {"login": "builder"}, "merge_commit_sha": None, "merged_at": None,
            "mergeable": True, "mergeable_state": "clean", "title": "Fix", "body": "Fix it.",
            "html_url": f"https://github.com/{REPO}/pull/{number}", "changed_files": 1,
            "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-04T00:00:00Z", **extra}


class RestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": self.tmp.name,
                                         "PROGRESS_BOARD_SKIP_PROBE": "1",
                                         "PROGRESS_BOARD_PR_FRESH_SECONDS": "0"})
        self.env.start()
        os.environ.pop("PROGRESS_BOARD_SKIP_GH", None)
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def github(self, args, timeout=30):
        self.calls.append(args)
        self.assertEqual(args[0], "api", "every GitHub read must use REST")
        path = args[1].split("?", 1)[0]
        if path.endswith("/check-runs"):
            return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "success"}]}
        if path.endswith("/statuses") or path.endswith("/comments") or path.endswith("/reviews"):
            return []
        if path.endswith("/files"):
            return [{"filename": "mcp-server/src/index.js"}]
        if "/pulls/" in path:
            return pull(int(path.rsplit("/", 1)[1]))
        raise AssertionError(path)

    def board(self, name, tasks):
        B.write_json({"project": name, "tasks": tasks})

    def test_two_open_twenty_merged_never_query_terminal_or_graphql(self):
        # Authenticate terminal manifests once; subsequent renders use them.
        def github(args, timeout=30):
            path = args[1]
            if "/pulls/" in path and path.rsplit("/", 1)[1].isdigit():
                n = int(path.rsplit("/", 1)[1])
                return pull(n, state="closed", merged_at="2026-10-04T00:00:00Z", merge_commit_sha=f"{n:040x}")
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            for n in range(3, 23):
                self.assertIsNone(B.fetch_pr(n, REPO)[1])
        self.calls.clear()
        tasks = {str(n): {"pr": n, "repo": REPO, "status": "done", "stage": "merged",
                         "pr_phase": "Merged", "merge_sha": f"{n:040x}", "pr_head": SHA}
                 for n in range(3, 23)}
        tasks.update({str(n): {"pr": n, "repo": REPO, "status": "running"} for n in (1, 2)})
        self.board("demo", tasks)
        with patch.object(B, "gh_json", self.github), patch.object(B, "gh_binary", return_value="gh"):
            B.render("demo")
        details = [c[1] for c in self.calls if "/pulls/" in c[1] and c[1].split("?")[0].rsplit("/", 1)[1].isdigit()]
        self.assertEqual(details, [f"repos/{REPO}/pulls/1", f"repos/{REPO}/pulls/2"])
        self.assertFalse(any("graphql" in str(c).lower() for c in self.calls))

    def test_one_hydration_across_boards_and_persisted_unchanged_version(self):
        for name in ("one", "two"):
            self.board(name, {"a": {"pr": 1, "repo": REPO, "status": "running"}})
        with patch.object(B, "gh_json", self.github), patch.object(B, "gh_binary", return_value="gh"):
            with B.github_read_pass():
                B.render("one")
                B.render("two")
            self.assertEqual(len(self.calls), 5)
            self.calls.clear()
            B.render("one")
            self.assertEqual(len(self.calls), 3)
            self.assertTrue(self.calls[0][1].endswith("/pulls/1"))
            self.assertTrue(any("/check-runs?" in c[1] for c in self.calls))
            self.assertTrue(any("/statuses?" in c[1] for c in self.calls))

    def test_watchdog_burst_reuses_fresh_open_pr_reads(self):
        # 2026-10-04: one job-watchdog scan made 100+ board mutations, each
        # re-rendering every open PR, and emptied the 5,000/hr REST pool.
        self.board("demo", {"a": {"pr": 1, "repo": REPO, "status": "running"}})
        with patch.dict(os.environ, {"PROGRESS_BOARD_PR_FRESH_SECONDS": "120"}), \
                patch.object(B, "gh_json", self.github), patch.object(B, "gh_binary", return_value="gh"):
            B.render("demo")
            first = len(self.calls)
            self.assertGreater(first, 0)
            for _ in range(50):
                B.render("demo")
            self.assertEqual(len(self.calls), first, "fresh open-PR reads must not refetch")
            with patch.object(B.time, "time", return_value=B.time.time() + 121):
                B.render("demo")
            self.assertGreater(len(self.calls), first, "a stale read must refetch")

    def test_read_failure_keeps_last_state_and_marks_snapshot_stale(self):
        self.board("demo", {"a": {"pr": 1, "repo": REPO, "status": "running"}})
        with patch.object(B, "gh_json", self.github):
            B.render("demo")
        before = B.read_state("demo")["tasks"]
        with patch.object(B, "gh_json", side_effect=RuntimeError("REST rate limited")):
            B.render("demo")
        snapshot = B.board_snapshot(B.read_state("demo"))
        self.assertEqual(snapshot["tasks"]["a"]["status"], before["a"]["status"])
        self.assertEqual(B.read_state("demo")["tasks"], before)
        self.assertEqual(snapshot["github_sync"]["failed"][0]["card"], "a")
        self.assertIn("REST rate limited", snapshot["github_sync"]["failed"][0]["error"])

    def test_freshness_and_payload_are_one_observation_during_concurrent_refresh(self):
        now = [1000.0]
        failing = False
        def github(args, timeout=30):
            if failing and "/check-runs?" in args[1]:
                return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "failure"}]}
            return self.github(args, timeout)
        with patch.dict(os.environ, {"PROGRESS_BOARD_PR_FRESH_SECONDS": "120"}), \
                patch.object(B.time, "time", side_effect=lambda: now[0]), patch.object(B, "gh_json", github):
            B.GitHubReadPass().read(1, REPO)
            reader = B.GitHubReadPass()
            reconcile = reader.reconcile
            def raced_reconcile():
                nonlocal failing
                reconcile()
                failing = True
                B.GitHubReadPass().read(1, REPO, pull())
            now[0] = 1122.0
            with patch.object(reader, "reconcile", side_effect=raced_reconcile):
                info, error = reader.read(1, REPO)
            self.assertIsNone(error)
            self.assertEqual(B.checks_summary(info), "0 pass · 0 pending · 1 fail")

    def test_losing_writer_cannot_renew_winning_observation_freshness(self):
        now = [1000.0]
        nested = False
        def github(args, timeout=30):
            nonlocal nested
            if "/check-runs?" in args[1] and not nested:
                nested = True
                now[0] = 1122.0
                B.GitHubReadPass().read(1, REPO)
                now[0] = 1245.0
            return self.github(args, timeout)
        with patch.dict(os.environ, {"PROGRESS_BOARD_PR_FRESH_SECONDS": "120"}), \
                patch.object(B.time, "time", side_effect=lambda: now[0]), patch.object(B, "gh_json", github):
            B.GitHubReadPass().read(1, REPO)
            calls = len(self.calls)
            B.GitHubReadPass().read(1, REPO)
            self.assertGreater(len(self.calls), calls, "the winner's 123-second-old evidence must refresh")

    def test_failed_changed_head_refresh_survives_freshness_hits_and_new_passes(self):
        with patch.dict(os.environ, {"PROGRESS_BOARD_PR_FRESH_SECONDS": "120"}), \
                patch.object(B, "gh_json", self.github):
            B.GitHubReadPass().read(1, REPO)
            reader = B.GitHubReadPass()
            with patch.object(B, "gh_json", side_effect=RuntimeError("timeout")):
                changed, error = reader.read(1, REPO, pull(head={"sha": "b" * 40}))
                self.assertEqual(error, "timeout")
                self.assertEqual(reader.read(1, REPO)[1], "timeout")
                self.assertEqual(B.GitHubReadPass().read(1, REPO)[1], "timeout")
            def matching_github(args, timeout=30):
                if args[1].endswith("/pulls/1"):
                    return pull(head={"sha": "b" * 40})
                return self.github(args, timeout)
            with patch.object(B, "gh_json", matching_github):
                recovered, error = B.GitHubReadPass().read(1, REPO)
            self.assertIsNone(error)
            self.assertEqual(recovered["headRefOid"], "b" * 40)

    def test_merged_is_immutable_but_closed_refreshes_once_per_pass(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                raw = pull(40 if state == "MERGED" else 41, state="closed",
                           merged_at="2026-10-04T00:00:00Z" if state == "MERGED" else None,
                           merge_commit_sha="b" * 40 if state == "MERGED" else None)
                def github(args, timeout=30):
                    return raw if args[1].endswith(str(raw["number"])) else self.github(args, timeout)
                with patch.object(B, "gh_json", github):
                    result, error = B.fetch_pr(raw["number"], REPO)
                self.assertIsNone(error)
                self.assertEqual(result["state"], state)
                if state == "MERGED":
                    with patch.object(B, "gh_json", side_effect=AssertionError("terminal refetched")):
                        again, error = B.fetch_pr(raw["number"], REPO)
                else:
                    with patch.object(B, "gh_json", github), B.github_read_pass():
                        again, error = B.fetch_pr(raw["number"], REPO)
                        self.calls.clear()
                        B.fetch_pr(raw["number"], REPO)
                        self.assertEqual(self.calls, [])
                    again = {k: v for k, v in again.items() if k not in {"_observation", "_observed_at"}}
                    result = {k: v for k, v in result.items() if k not in {"_observation", "_observed_at"}}
                self.assertEqual(again, result)
                self.assertIsNone(error)

    def test_all_repos_discovery_uses_rest_and_keeps_terminal_snapshots(self):
        def github(args, timeout=30):
            self.assertEqual(args[0], "api")
            path = args[1]
            if path.startswith("user/repos?"):
                return [{"full_name": REPO, "archived": False}]
            if "/pulls?" in path:
                return [pull()] if REPO in path else []
            if "/issues?" in path:
                return []
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            snapshot = B.build_all_repos()
        self.assertEqual(snapshot["tasks"]["carr-system-1"]["pr_head"], SHA)

    def test_recorded_gh_pr_view_parity_on_the_same_live_head_and_version(self):
        root = Path(__file__).with_name("fixtures") / "progress-board-rest"
        recorded = json.loads((root / "gh-pr-view-open.json").read_text())
        rest = json.loads((root / "rest-open.json").read_text())
        self.assertEqual(recorded["headRefOid"], rest["pull"]["head"]["sha"])
        self.assertEqual(recorded["updatedAt"], rest["pull"]["updated_at"])
        def github(args, timeout=30):
            self.assertEqual(args[0], "api")
            path = args[1].split("?")[0]
            endpoint = path.rsplit("/", 1)[1]
            return rest["pull" if endpoint == "1510" else endpoint]
        with patch.object(B, "gh_json", github):
            observed, error = B.fetch_pr(1510, REPO)
        self.assertIsNone(error)
        for field in ("isDraft", "headRefOid", "headRefName", "updatedAt", "mergeable"):
            self.assertEqual(observed[field], recorded[field], field)
        def rendered_checks(pr):
            return sorted((c.get("name") or c.get("context") or "", c.get("status"),
                           c.get("conclusion") or c.get("state")) for c in pr["statusCheckRollup"])
        self.assertEqual(rendered_checks(observed), rendered_checks(recorded))
        self.assertEqual(B.checks_summary(observed), B.checks_summary(recorded))
        def rendered_comments(pr):
            return [(c["author"]["login"], c["authorAssociation"], c["body"], c["createdAt"]) for c in pr["comments"]]
        self.assertEqual(rendered_comments(observed), rendered_comments(recorded))
        self.assertEqual(B.review_verdict(observed), B.review_verdict(recorded))

    def test_cancelled_pending_legacy_statuses_reviews_and_mergeability_parity(self):
        expected = {"state": "OPEN", "isDraft": False, "headRefOid": SHA,
                    "author": {"login": "builder"}, "mergeCommit": None, "mergeable": "CONFLICTING",
                    "reviewDecision": "CHANGES_REQUESTED", "comments": [],
                    "statusCheckRollup": [{"name": "cancel", "status": "COMPLETED", "conclusion": "CANCELLED"},
                                          {"name": "wait", "status": "QUEUED", "conclusion": None},
                                          {"context": "legacy", "state": "PENDING"}]}
        def github(args, timeout=30):
            endpoint = args[1].split("?")[0].rsplit("/", 1)[1]
            if endpoint == "1":
                return pull(mergeable=False)
            if endpoint == "check-runs":
                return {"check_runs": [{"name": "cancel", "status": "completed", "conclusion": "cancelled"},
                                       {"name": "wait", "status": "queued", "conclusion": None}]}
            if endpoint == "statuses":
                return [{"context": "legacy", "state": "pending"}, {"context": "legacy", "state": "success"}]
            if endpoint == "reviews":
                return [{"id": 1, "user": {"login": "reviewer"}, "author_association": "COLLABORATOR", "state": "CHANGES_REQUESTED"},
                        {"id": 2, "user": {"login": "reviewer"}, "author_association": "COLLABORATOR", "state": "COMMENTED"}]
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            observed, error = B.fetch_pr(1, REPO)
        self.assertIsNone(error)
        for field in ("state", "isDraft", "headRefOid", "author", "mergeCommit", "mergeable", "reviewDecision"):
            self.assertEqual(observed[field], expected[field], field)
        self.assertEqual(B.checks_summary(observed), "0 pass · 2 pending · 1 fail")
        self.assertEqual(B.derived_pr_state(observed), B.derived_pr_state(expected))

    def test_pending_checks_complete_without_a_pr_updated_at_change(self):
        pending = True
        def github(args, timeout=30):
            if "/check-runs?" in args[1]:
                return {"check_runs": [{"name": "CI", "status": "in_progress" if pending else "completed",
                                        "conclusion": None if pending else "success"}]}
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            first, error = B.fetch_pr(1, REPO)
            self.assertIsNone(error)
            self.assertEqual(B.checks_summary(first), "0 pass · 1 pending · 0 fail")
            pending = False
            second, error = B.fetch_pr(1, REPO)
            self.assertIsNone(error)
            self.assertEqual(first["updatedAt"], second["updatedAt"])
            self.assertEqual(B.checks_summary(second), "1 pass · 0 pending · 0 fail")

    def test_all_repository_reads_failing_render_previous_cards_marked_stale(self):
        previous = {"a": {"pr": 1, "repo": REPO, "status": "review", "stage": "review"}}
        self.board("all-repos", previous)
        with patch.object(B, "gh_json", side_effect=RuntimeError("REST unavailable")):
            state = B.build_all_repos()
        self.assertEqual(state["tasks"], previous)
        self.assertTrue(state["github_sync"]["stale"])
        self.assertEqual(len(state["github_sync"]["failed"]), len(B.CORE_REPOS))

    def test_formal_outsider_review_cannot_make_a_pr_approved_or_blocked(self):
        for number, state in enumerate(("APPROVED", "CHANGES_REQUESTED"), 1):
            def github(args, timeout=30):
                if "/reviews?" in args[1]:
                    return [{"id": 1, "user": {"login": "outsider"}, "author_association": "NONE", "state": state}]
                return self.github(args, timeout)
            with self.subTest(state=state), patch.object(B, "gh_json", github):
                observed, error = B.fetch_pr(number, REPO)
                self.assertIsNone(error)
                self.assertEqual(observed["reviewDecision"], "")
                self.assertEqual(B.derived_pr_state(observed), ("review", "review", "Awaiting review"))

    def test_discovered_closure_invalidates_shared_open_result_before_cursor_moves(self):
        merged = False
        def github(args, timeout=30):
            path = args[1]
            if "/pulls?" in path:
                return []
            if "/issues?" in path:
                return [{"number": 1, "state": "closed", "updated_at": "2026-10-04T01:00:00Z",
                         "pull_request": {"url": "pr"}}]
            if path.endswith("/pulls/1") and merged:
                return pull(state="closed", merged_at="2026-10-04T01:00:00Z",
                            updated_at="2026-10-04T01:00:00Z", merge_commit_sha="b" * 40)
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github), B.github_read_pass():
            self.assertEqual(B.fetch_pr(1, REPO)[0]["state"], "OPEN")
            merged = True
            opened, closed = B.read_repository(REPO, "2026-10-01")
            self.assertEqual(opened, [])
            self.assertEqual([p["number"] for p in closed], [1])
            self.assertTrue(B.read_json_file(B.board_dir() / ".github-discovery.json").get(REPO))
        with patch.object(B, "rest_rows", return_value=[]), B.github_read_pass():
            self.assertEqual([p["number"] for p in B.read_repository(REPO, "2026-10-01")[1]], [1])

    def test_unaccounted_closure_never_advances_cursor(self):
        def github(args, timeout=30):
            if "/pulls?" in args[1]:
                return []
            if "/issues?" in args[1]:
                return [{"number": 1, "state": "closed", "updated_at": "2026-10-04T01:00:00Z",
                         "pull_request": {"url": "pr"}}]
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github), B.github_read_pass():
            with self.assertRaisesRegex(RuntimeError, "closed discovery"):
                B.read_repository(REPO, "2026-10-01")
        self.assertNotIn(REPO, B.read_json_file(B.board_dir() / ".github-discovery.json"))

    def test_success_refreshes_after_failure_rerun_or_new_check_on_same_pr_version(self):
        checks = [{"name": "CI", "status": "completed", "conclusion": "success"}]
        def github(args, timeout=30):
            return {"check_runs": checks} if "/check-runs?" in args[1] else self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            B.fetch_pr(1, REPO)
            for checks, summary in [
                ([{"name": "CI", "status": "completed", "conclusion": "failure"}], "0 pass · 0 pending · 1 fail"),
                ([{"name": "CI", "status": "queued", "conclusion": None}], "0 pass · 1 pending · 0 fail"),
                ([{"name": "CI", "status": "completed", "conclusion": "success"},
                  {"name": "new", "status": "queued", "conclusion": None}], "1 pass · 1 pending · 0 fail")]:
                observed, error = B.fetch_pr(1, REPO)
                self.assertIsNone(error)
                self.assertEqual(B.checks_summary(observed), summary)

    def test_mergeability_refreshes_in_both_directions_even_with_discovery_hint(self):
        mergeable = True
        def github(args, timeout=30):
            return pull(mergeable=mergeable) if args[1].endswith("/pulls/1") else self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            B.fetch_pr(1, REPO)
            for mergeable, expected in [(False, "CONFLICTING"), (True, "MERGEABLE")]:
                with B.github_read_pass() as reader:
                    observed, error = reader.read(1, REPO, pull())
                self.assertIsNone(error)
                self.assertEqual(observed["mergeable"], expected)

    def test_closed_project_refresh_and_open_discovery_replace_closed_snapshot(self):
        raw = pull(state="closed")
        def github(args, timeout=30):
            return raw if args[1].endswith("/pulls/1") else self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            self.assertEqual(B.fetch_pr(1, REPO)[0]["state"], "CLOSED")
            raw = pull(updated_at="2026-10-04T01:00:00Z")
            self.assertEqual(B.fetch_pr(1, REPO)[0]["state"], "OPEN")
            with B.github_read_pass() as reader:
                self.assertEqual(reader.read(1, REPO, raw)[0]["state"], "OPEN")
            self.assertEqual(B.fetch_pr(1, REPO)[0]["state"], "OPEN")
        self.board("legacy", {"a": {"pr": 2, "repo": REPO, "pr_phase": "Closed unmerged"}})
        with patch.object(B, "gh_json", self.github), B.github_read_pass() as reader:
            observed, error = reader.read(2, REPO, pull(2))
            self.assertIsNone(error)
            self.assertTrue(B.valid_open_pr(observed))
            self.assertEqual(observed["state"], "OPEN")

    def test_delayed_success_cannot_overwrite_newer_failure_or_return_losing_snapshot(self):
        nested = False
        newer = None
        def github(args, timeout=30):
            nonlocal nested, newer
            if "/check-runs?" in args[1]:
                if not nested:
                    nested = True
                    newer = B.GitHubReadPass().read(1, REPO)[0]
                    return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "success"}]}
                return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "failure"}]}
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            delayed, error = B.GitHubReadPass().read(1, REPO)
        self.assertIsNone(error)
        self.assertEqual(B.checks_summary(newer), "0 pass · 0 pending · 1 fail")
        self.assertEqual(B.checks_summary(delayed), B.checks_summary(newer))
        cached = B.read_json_file(B.board_dir() / ".github-pr-cache.json")[f"{REPO}#1"]
        self.assertEqual(B.checks_summary(cached), B.checks_summary(newer))

    def test_repository_reconciles_terminal_fact_written_after_pass_started(self):
        with B.github_read_pass():
            def github(args, timeout=30):
                if args[1].endswith("/pulls/1"):
                    return pull(state="closed", merged_at="2026-10-04T00:00:00Z", merge_commit_sha="b" * 40)
                return self.github(args, timeout)
            with patch.object(B, "gh_json", github):
                self.assertEqual(B.GitHubReadPass().read(1, REPO)[0]["state"], "MERGED")
            with patch.object(B, "rest_rows", return_value=[]):
                self.assertEqual([p["number"] for p in B.read_repository(REPO, "2026-10-01")[1]], [1])

    def test_legacy_seed_does_not_turn_activity_time_into_merge_time(self):
        self.board("legacy", {"a": {"pr": 1, "repo": REPO, "pr_phase": "Merged",
                                  "merge_sha": "b" * 40, "created_at": "2020-01-01T00:00:00Z",
                                  "updated_at": "2026-10-04T00:00:00Z"}})
        reader = B.GitHubReadPass()
        self.assertIsNone(reader.saved[f"{REPO}#1"].get("mergedAt"))

    def test_legacy_merged_manifest_is_authenticated_once_and_allows_delivery(self):
        task = {"pr": 1, "repo": REPO, "pr_phase": "Merged", "stage": "merged", "status": "done",
                "merge_sha": "b" * 40, "pr_head": SHA, "merged_at": "2026-10-04T00:00:00Z"}
        self.board("all-repos", {"carr-system-1": task})
        def github(args, timeout=30):
            if args[1].endswith("/pulls/1"):
                return pull(state="closed", merged_at=task["merged_at"], merge_commit_sha=task["merge_sha"])
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            info, error = B.fetch_pr(1, REPO)
        self.assertIsNone(error)
        self.assertEqual(B.changed_paths(info), ["mcp-server/src/index.js"])
        with patch.object(B, "gh_json", side_effect=AssertionError("migrated terminal refetched")):
            self.assertEqual(B.fetch_pr(1, REPO)[0], info)
        with patch.object(B, "latest_release", return_value={"sha": task["merge_sha"], "lane": "worker",
                                                             "ts": None, "source": "test receipt"}):
            board = B.assemble_all_repos(B.read_state("all-repos"), {REPO: ([], [info])})
        self.assertEqual(board["tasks"]["carr-system-1"]["stage"], "live")

    def test_recorded_fixture_contact_fields_are_synthetic_and_provenance_says_so(self):
        root = Path(__file__).with_name("fixtures") / "progress-board-rest"
        recorded = json.loads((root / "gh-pr-view-open.json").read_text())
        for commit in recorded["commits"]:
            for author in commit["authors"]:
                self.assertTrue(author["email"].endswith("@example.invalid"), "fixture author contact must be synthetic")
        for path in root.glob("*.json"):
            for address in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", path.read_text()):
                self.assertTrue(address.endswith("@example.invalid"), f"{path.name} contact must be synthetic")
        self.assertIn("sanitized", json.loads((root / "provenance.json").read_text())["capture_note"].lower())

    def test_formal_reviews_use_configured_associations_and_login_exceptions(self):
        rules = {"review_author_associations": ["OWNER"], "review_logins": ["trusted"]}
        review = {"id": 1, "user": {"login": "collaborator"}, "author_association": "COLLABORATOR",
                  "state": "APPROVED"}
        def github(args, timeout=30):
            return [review] if "/reviews?" in args[1] else self.github(args, timeout)
        with patch.object(B, "gh_json", github), patch.object(B, "review_rules", return_value=rules):
            self.assertEqual(B.fetch_pr(1, REPO)[0]["reviewDecision"], "")
            review = {**review, "user": {"login": "trusted"}, "author_association": "NONE"}
            self.assertEqual(B.fetch_pr(2, REPO)[0]["reviewDecision"], "APPROVED")

    def test_cached_formal_review_is_recomputed_when_configured_trust_changes(self):
        rules = {"review_author_associations": ["COLLABORATOR"], "review_logins": []}
        def github(args, timeout=30):
            if "/reviews?" in args[1]:
                return [{"id": 1, "user": {"login": "collaborator"}, "author_association": "COLLABORATOR",
                         "state": "APPROVED"}]
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github), patch.object(B, "review_rules", side_effect=lambda repo: rules):
            self.assertEqual(B.fetch_pr(1, REPO)[0]["reviewDecision"], "APPROVED")
            rules = {"review_author_associations": ["OWNER"], "review_logins": []}
            self.assertEqual(B.fetch_pr(1, REPO)[0]["reviewDecision"], "")

    def test_fresh_formal_reviews_follow_current_trust_in_same_and_new_passes(self):
        for decision in ("APPROVED", "CHANGES_REQUESTED"):
            with self.subTest(decision=decision):
                number = 1 if decision == "APPROVED" else 2
                rules = {"review_author_associations": ["COLLABORATOR"], "review_logins": []}
                def github(args, timeout=30):
                    if "/reviews?" in args[1]:
                        return [{"id": 1, "user": {"login": "collaborator"},
                                 "author_association": "COLLABORATOR", "state": decision}]
                    return self.github(args, timeout)
                with patch.dict(os.environ, {"PROGRESS_BOARD_PR_FRESH_SECONDS": "120"}), \
                        patch.object(B, "gh_json", github), \
                        patch.object(B, "review_rules", side_effect=lambda repo: rules):
                    reader = B.GitHubReadPass()
                    self.assertEqual(reader.read(number, REPO, pull(number))[0]["reviewDecision"], decision)
                    calls = len(self.calls)
                    rules = {"review_author_associations": ["OWNER"], "review_logins": []}
                    self.assertEqual(reader.read(number, REPO)[0]["reviewDecision"], "")
                    self.assertEqual(B.GitHubReadPass().read(number, REPO)[0]["reviewDecision"], "")
                    self.assertEqual(len(self.calls), calls, "trust changes need no GitHub refetch")


if __name__ == "__main__":
    unittest.main()
