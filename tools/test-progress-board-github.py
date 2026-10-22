#!/usr/bin/env python3
"""The board's GitHub reads: one GraphQL query per repository per refresh."""
import importlib.util
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("board", Path(__file__).with_name("progress_board.py"))
assert SPEC is not None and SPEC.loader is not None
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
REPO, APP = "jbookout/carr-system", "jbookout/doctorcre-app"
SHA = "a" * 40
RECENT = (B.now_utc() - B.timedelta(days=3)).isoformat(timespec="seconds")
EARLIER = (B.now_utc() - B.timedelta(days=4)).isoformat(timespec="seconds")
CI = {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "SUCCESS"}


def node(repo, number, **extra):
    return {"number": number, "title": f"PR {number}", "body": "Fix it.",
            "url": f"https://github.com/{repo}/pull/{number}", "createdAt": "2026-10-01T00:00:00Z",
            "updatedAt": "2026-10-04T00:00:00Z", "state": "OPEN", "isDraft": False, "headRefOid": SHA,
            "headRefName": "fix", "author": {"login": "builder"}, "mergedAt": None, "closedAt": None,
            "mergeCommit": None, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
            "labels": {"nodes": []}, "milestone": None,
            "statusCheckRollup": {"contexts": {"nodes": [CI]}},
            "comments": {"nodes": []}, "reviews": {"nodes": []}, **extra}


def merged(repo, number, merged_at="2026-10-20T00:00:00Z"):
    return node(repo, number, state="MERGED", mergedAt=merged_at, closedAt=merged_at, updatedAt=merged_at,
                mergeCommit={"oid": f"{number:040x}"}, changedFiles=1,
                files={"nodes": [{"path": "mcp-server/src/index.js"}]})


class FakeGitHub:
    """GitHub's GraphQL API over per-repository open and closed PR lists."""

    def __init__(self):
        self.open = {repo: [] for repo in B.CORE_REPOS}
        self.closed = {repo: [] for repo in B.CORE_REPOS}
        self.calls = []
        self.page = 100

    def project(self, row, query):
        result = copy.deepcopy(row)
        if 'fragment closed on PullRequest { ...pr ' in query and row['state'] != 'OPEN':
            for key in ('statusCheckRollup', 'comments', 'reviews'):
                result.pop(key, None)
        for kind in ('labels', 'comments', 'reviews', 'files', 'contexts'):
            parent = result.get('statusCheckRollup') if kind == 'contexts' else result
            if not parent or kind not in parent:
                continue
            match = re.search(r'\b' + kind + r'\((first|last): (\d+)(?:, after: "([^"]+)")?\)', query)
            if not match:
                continue
            rows = parent[kind]['nodes']
            size = int(match[2])
            start = int(match[3] or 0) if match[1] == 'first' else max(0, len(rows) - size)
            parent[kind] = {'nodes': rows[start:start + size], 'pageInfo': {
                'hasNextPage': start + size < len(rows), 'endCursor': str(start + size)}}
        return result

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        assert args[:2] == ["api", "graphql"], args
        fields = dict(arg.split("=", 1) for arg in args[2:] if "=" in arg)
        repo = f"{fields['owner']}/{fields['name']}"
        aliases = re.findall(r"pr(\d+): pullRequest", fields["query"])
        if aliases:
            every = {row["number"]: row for row in self.open[repo] + self.closed[repo]}
            return {"data": {"repository": {f"pr{n}": self.project(every[int(n)], fields['query'])
                                             if int(n) in every else None for n in aliases}}}
        found = {}
        closed_page = int(re.search(r"closed: pullRequests\([^)]*first: (\d+)", fields["query"])[1])
        for kind, rows, size in (("open", self.open[repo], self.page),
                                 ("closed", sorted(self.closed[repo], key=lambda r: r["updatedAt"], reverse=True),
                                  closed_page)):
            if fields[f"with{kind.title()}"] != "true":
                continue
            start = int(fields.get(f"{kind}After") or 0)
            found[kind] = {"nodes": [self.project(row, fields['query']) for row in rows[start:start + size]],
                           "pageInfo": {"hasNextPage": start + size < len(rows), "endCursor": str(start + size)}}
        return {"data": {"repository": found}}


class GitHubReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": self.tmp.name, "PROGRESS_BOARD_SKIP_PROBE": "1"})
        self.env.start()
        os.environ.pop("PROGRESS_BOARD_SKIP_GH", None)
        self.github = FakeGitHub()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def refresh(self, project="demo"):
        """One scheduled refresh: the project board and the all-repos board."""
        self.github.calls.clear()
        with patch.object(B, "gh_json", self.github), patch.object(B, "gh_binary", return_value="gh"), \
                B.github_read_pass():
            B.render(project, discover=True)
            B.build_all_repos()
        return len(self.github.calls)

    def card(self, number, repo=REPO):
        return B.read_state("demo")["tasks"][f"c{number}"]

    def board(self, *keys):
        B.write_json({"project": "demo", "tasks": {
            f"c{number}": {"pr": number, "repo": repo, "status": "running"} for repo, number in keys}})

    def test_a_refresh_reads_github_once_per_repository_whatever_the_open_pr_count(self):
        self.github.open[REPO] = [node(REPO, n) for n in range(1, 31)]
        self.github.open[APP] = [node(APP, n) for n in range(101, 109)]
        self.board(*[(REPO, n) for n in range(1, 11)], (APP, 101), (APP, 102))
        self.assertEqual(self.refresh(), len(B.CORE_REPOS))
        self.assertEqual(sum(row["open"] for row in B.read_state(B.ALL_REPOS_BOARD)["repos"]), 38)
        self.assertEqual(self.card(1)["pr_checks"], "1 pass · 0 pending · 0 fail")
        self.assertEqual(self.refresh(), len(B.CORE_REPOS), "every refresh costs the same")

    def test_more_than_a_page_of_open_prs_is_read_in_full(self):
        self.github.page = 2
        self.github.open[REPO] = [node(REPO, n) for n in range(1, 6)]
        self.board((REPO, 5))
        self.assertEqual(self.refresh(), len(B.CORE_REPOS) + 2)
        self.assertEqual(self.card(5)["pr_phase"], "Awaiting review")

    def test_a_card_for_an_unlisted_pr_is_read_once_in_one_batch_per_repository(self):
        start = B.now_utc()
        def at(hours):
            return patch.object(B, "now_utc", return_value=start + B.timedelta(hours=hours))
        B.write_json({"project": "x", "tasks": {}})
        with at(0):
            self.refresh("x")
        between = (start + B.timedelta(minutes=30)).isoformat(timespec="seconds")
        newer = [merged(REPO, n, between) for n in range(10, 50)]
        self.github.closed[REPO] = newer + [merged(REPO, n, RECENT) for n in range(50, 100)] + \
            [merged(REPO, n, EARLIER) for n in (7, 8, 9)]
        self.board(*[(REPO, n) for n in (7, 8, 9)])
        with at(1):
            self.assertEqual(self.refresh(), len(B.CORE_REPOS) + 2 + 1,
                             "closed pages back to the previous refresh, then one batch for the old merges")
        self.assertEqual(self.card(8)["pr_phase"], "Merged")
        self.assertEqual(self.card(8)["merge_sha"], f"{8:040x}")
        with at(2):
            self.assertEqual(self.refresh(), len(B.CORE_REPOS), "merged facts are kept")

    def test_merged_facts_never_change_and_a_reopened_pr_is_open_again(self):
        self.github.closed[REPO] = [merged(REPO, 1), node(REPO, 2, state="CLOSED", closedAt="2026-10-20T00:00:00Z",
                                                          updatedAt="2026-10-20T00:00:00Z", changedFiles=0,
                                                          files={"nodes": []})]
        self.board((REPO, 1), (REPO, 2))
        self.refresh()
        self.assertEqual(self.card(2)["pr_phase"], "Closed unmerged")
        self.github.closed[REPO] = [{**merged(REPO, 1), "mergeCommit": {"oid": "b" * 40}}]
        self.github.open[REPO] = [node(REPO, 2)]
        self.refresh()
        self.assertEqual(self.card(1)["merge_sha"], f"{1:040x}")
        self.assertEqual(self.card(2)["pr_phase"], "Awaiting review")

    def test_legacy_seeded_merge_is_replaced_by_an_observed_merge(self):
        self.board((REPO, 2))
        seeded = {'number': 2, 'state': 'MERGED', 'isDraft': False, 'headRefOid': SHA,
                  'author': {'login': 'builder'}, 'statusCheckRollup': [], 'comments': [],
                  'mergeCommit': {'oid': 'b' * 40}, '_legacy_terminal': True}
        (B.board_dir() / '.github-pr-cache.json').write_text(json.dumps({f'{REPO}#2': seeded}))
        self.github.closed[REPO] = [merged(REPO, 2)]
        self.refresh()
        self.assertEqual(self.card(2)['merge_sha'], f'{2:040x}')

    def test_failing_and_pending_checks_drive_the_card(self):
        status = {"__typename": "StatusContext", "context": "deploy", "state": "PENDING"}
        failing = {**CI, "name": "lint", "conclusion": "FAILURE"}
        self.github.open[REPO] = [
            node(REPO, 1, statusCheckRollup={"contexts": {"nodes": [CI, status]}}),
            node(REPO, 2, statusCheckRollup={"contexts": {"nodes": [CI, failing]}}),
            node(REPO, 3, mergeable="CONFLICTING", mergeStateStatus="DIRTY"),
            node(REPO, 4, statusCheckRollup=None)]
        self.board(*[(REPO, n) for n in range(1, 5)])
        self.refresh()
        self.assertEqual((self.card(1)["pr_phase"], self.card(1)["pr_checks"]), ("CI", "1 pass · 1 pending · 0 fail"))
        self.assertEqual(self.card(2)["blocked_reason"], "Failing checks: lint")
        self.assertEqual(self.card(3)["pr_phase"], "Merge conflict")
        self.assertEqual(self.card(4)["pr_checks"], "no checks")

    def test_a_failing_check_after_the_first_hundred_still_blocks_the_card(self):
        checks = [CI] * 100 + [{**CI, 'name': 'last check', 'conclusion': 'FAILURE'}]
        self.github.open[REPO] = [node(REPO, 1, statusCheckRollup={'contexts': {'nodes': checks}})]
        self.board((REPO, 1))
        self.refresh()
        self.assertEqual(self.card(1)['pr_phase'], 'Checks failing')
        self.assertEqual(self.card(1)['pr_checks'], '100 pass · 0 pending · 1 fail')

    def test_merging_a_pr_keeps_its_check_result(self):
        self.github.open[REPO] = [node(REPO, 1)]
        self.board((REPO, 1))
        self.refresh()
        self.github.open[REPO] = []
        self.github.closed[REPO] = [merged(REPO, 1)]
        self.refresh()
        self.assertEqual(self.card(1)['pr_checks'], '1 pass · 0 pending · 0 fail')

    def test_long_review_histories_and_file_lists_are_complete(self):
        comment = {'author': {'login': 'jbookout'}, 'authorAssociation': 'OWNER',
                   'body': 'Still working', 'createdAt': '2026-10-04T00:00:00Z'}
        review = {'databaseId': 1, 'state': 'CHANGES_REQUESTED', 'author': {'login': 'jbookout'},
                  'authorAssociation': 'OWNER', 'submittedAt': '2026-10-04T00:00:00Z'}
        self.github.open[REPO] = [node(REPO, 1,
            comments={'nodes': [{**comment, 'body': 'BLOCK'}] + [comment] * 100},
            reviews={'nodes': [review] + [{**review, 'databaseId': i + 2, 'state': 'COMMENTED'} for i in range(100)]})]
        paths = [{'path': f'mcp-server/src/file{i}.js'} for i in range(101)]
        self.github.closed[REPO] = [{**merged(REPO, 2), 'files': {'nodes': paths}, 'changedFiles': 101}]
        with patch.object(B, 'gh_json', self.github), B.github_read_pass():
            facts = B.fetch_prs([(REPO, 1), (REPO, 2)])
        info, error = facts[REPO, 1]
        self.assertIsNone(error)
        self.assertEqual(B.review_verdict(info, REPO), 'BLOCK')
        self.assertEqual(info['reviewDecision'], 'CHANGES_REQUESTED')
        info, error = facts[REPO, 2]
        self.assertIsNone(error)
        self.assertEqual(len(B.changed_paths(info) or []), 101)

    def test_formal_reviews_count_only_from_trusted_reviewers(self):
        rules = {"review_author_associations": ["OWNER"], "review_logins": ["trusted"]}
        def review(user, association, state):
            return {"reviews": {"nodes": [{"databaseId": 1, "state": state, "submittedAt": "2026-10-04T00:00:00Z",
                                           "author": {"login": user}, "authorAssociation": association}]}}
        self.github.open[REPO] = [node(REPO, 1, **review("outsider", "NONE", "APPROVED")),
                                  node(REPO, 2, **review("outsider", "NONE", "CHANGES_REQUESTED")),
                                  node(REPO, 3, **review("trusted", "NONE", "APPROVED")),
                                  node(REPO, 4, **review("owner", "OWNER", "CHANGES_REQUESTED"))]
        self.board(*[(REPO, n) for n in range(1, 5)])
        with patch.object(B, "review_rules", return_value=rules):
            self.refresh()
        self.assertEqual([self.card(n)["pr_phase"] for n in range(1, 5)],
                         ["Awaiting review", "Awaiting review", "Approved", "Changes requested"])

    def test_a_trusted_verdict_comment_on_the_head_makes_a_pr_ready(self):
        verdict = {"author": {"login": "jbookout"}, "authorAssociation": "OWNER",
                   "body": f"APPROVE\nReviewed-SHA: {SHA}\n", "createdAt": "2026-10-05T00:00:00Z"}
        self.github.open[REPO] = [node(REPO, 1, comments={"nodes": [verdict]}),
                                  node(REPO, 2, comments={"nodes": [{**verdict, "authorAssociation": "NONE"}]}),
                                  node(REPO, 3, comments={"nodes": [verdict]}, headRefOid="c" * 40)]
        self.board((REPO, 1), (REPO, 2), (REPO, 3))
        self.refresh()
        self.assertEqual([self.card(n)["review_verdict"] for n in (1, 2, 3)],
                         ["APPROVE", "Not recorded", "Not recorded"])
        self.assertEqual(self.card(1)["pr_phase"], "Ready to merge")

    def test_read_failure_keeps_every_card_and_marks_the_boards_stale(self):
        self.github.open[REPO] = [node(REPO, 1)]
        self.board((REPO, 1))
        self.refresh()
        cards, portfolio = B.read_state("demo")["tasks"], B.read_state(B.ALL_REPOS_BOARD)["tasks"]
        with patch.object(B, "gh_json", side_effect=RuntimeError("GraphQL unavailable")), B.github_read_pass():
            B.render("demo")
            state = B.build_all_repos()
        self.assertEqual(B.read_state("demo")["tasks"], cards)
        self.assertIn("GraphQL unavailable", B.read_state("demo")["github_sync"]["failed"][0]["error"])
        self.assertEqual(state["tasks"], portfolio)
        self.assertEqual(len(state["github_sync"]["failed"]), len(B.CORE_REPOS))

    def test_v1_discovery_shares_the_refresh_read(self):
        self.github.open[REPO] = [node(REPO, 1, title="W1: first slice")]
        self.github.closed[APP] = [{**merged(APP, 5), "labels": {"nodes": [{"name": "v1"}]}}]
        B.write_json({"project": B.LAUNCHD_BOARD, "tasks": {}})
        self.assertEqual(self.refresh(B.LAUNCHD_BOARD), len(B.CORE_REPOS))
        tasks = B.read_state(B.LAUNCHD_BOARD)["tasks"]
        self.assertEqual(tasks["pr-1"]["slice"], "W1")
        self.assertEqual(tasks["app-pr-5"]["pr_phase"], "Merged")

    def test_closed_backfill_stops_at_fourteen_days_and_resumes_from_disk(self):
        start = B.now_utc()
        recent = (start - B.timedelta(days=3)).isoformat(timespec='seconds')
        old = (start - B.timedelta(days=20)).isoformat(timespec='seconds')
        self.github.closed[REPO] = [merged(REPO, n, recent) for n in range(1, 43)] + \
            [merged(REPO, n, old) for n in range(43, 100)]
        B.write_json({'project': 'demo', 'tasks': {}})
        self.assertEqual(self.refresh(), len(B.CORE_REPOS) + 2)
        portfolio = B.read_state(B.ALL_REPOS_BOARD)
        self.assertEqual(next(r for r in portfolio['repos'] if r['repo'] == REPO)['merged'], 42)
        cursor = B.read_json_file(B.board_dir() / '.github-discovery.json')[REPO]
        self.assertGreaterEqual(cursor, (start - B.timedelta(seconds=2)).isoformat(timespec='seconds'))
        later = start + B.timedelta(minutes=5)
        self.github.closed[REPO].insert(0, merged(REPO, 200, later.isoformat(timespec='seconds')))
        with patch.object(B, 'now_utc', return_value=later):
            self.assertEqual(self.refresh(), len(B.CORE_REPOS))
        self.assertIn('carr-system-200', B.read_state(B.ALL_REPOS_BOARD)['tasks'])


class Publication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_server_linkage_on_read_is_not_a_failed_publication(self):
        """read-progress-board adds a derived `linkage` to every task it returns."""
        B.write_json({"project": "demo", "tasks": {"a": {"title": "A", "status": "running"}}})
        stored, writes = {}, []

        def verb(name, args):
            if name == "publish-board-snapshot":
                writes.append(args)
                stored.update(version=len(writes), snapshot_json=args["snapshot"])
                return {"ok": True}
            if name == "read-progress-board":
                if not stored:
                    return {"ok": True, "snapshot": None, "questions": []}
                snapshot = {**stored["snapshot_json"], "tasks": {
                    key: {**task, "linkage": {"references": []}} for key, task in stored["snapshot_json"]["tasks"].items()}}
                return {"ok": True, "snapshot": {**stored, "snapshot_json": snapshot}, "questions": []}
            raise AssertionError(name)

        with patch.object(B, "call_verb", side_effect=verb):
            self.assertEqual(B.publish_board("demo", costs={})["snapshot_version"], 1)
            self.assertEqual(B.publish_board("demo", costs={})["snapshot_version"], 1, "an unchanged board is not republished")
        self.assertEqual(len(writes), 1)

    def test_server_linkage_does_not_hide_a_changed_task_status(self):
        B.write_json({'project': 'demo', 'tasks': {'a': {'title': 'A', 'status': 'running'}}})
        stored = {}
        def verb(name, args):
            if name == 'publish-board-snapshot':
                stored.update(version=1, snapshot_json=copy.deepcopy(args['snapshot']))
                stored['snapshot_json']['tasks']['a'].update(status='failed', linkage={'references': []})
            return {'ok': True, 'snapshot': stored or None, 'questions': []}
        with patch.object(B, 'call_verb', side_effect=verb):
            with self.assertRaisesRegex(RuntimeError, 'did not read back'):
                B.publish_board('demo', costs={})


class ReleaseAncestry(unittest.TestCase):
    def test_local_git_distinguishes_ancestor_descendant_and_missing_objects(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.run(['git', '-C', root, *args], check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            git('-c', 'user.name=Synthetic', '-c', 'user.email=jbookout@users.noreply.github.com',
                'commit', '--allow-empty', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            git('-c', 'user.name=Synthetic', '-c', 'user.email=jbookout@users.noreply.github.com',
                'commit', '--allow-empty', '-qm', 'release')
            release = git('rev-parse', 'HEAD')
            with patch.object(B, 'RELEASE_TARGETS', {REPO: (Path(root), 'https://example.invalid/release')}):
                self.assertTrue(B.release_contains(REPO, release, base))
                self.assertFalse(B.release_contains(REPO, base, release))
                self.assertFalse(B.release_contains(REPO, release, 'f' * 40))


if __name__ == "__main__":
    unittest.main()
