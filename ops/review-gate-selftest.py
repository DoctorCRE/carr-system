"""Behavior tests for the conditional review gate."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import fixture_env

spec = importlib.util.spec_from_file_location('gate', Path(__file__).with_name('review-gate.py'))
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

class ReviewGate(unittest.TestCase):
    def setUp(self):
        self.pr = {'title': 'Fix', 'body': 'Card: WR622.S1.J5', 'user': {'login': 'author', 'type': 'User'}, 'changed_files': 1, 'additions': 150, 'deletions': 0, 'head': {'sha': 'head'}, 'base': {'ref': 'main'}, 'auto_merge': None}
        self.files = [{'filename': 'lib/safe.py', 'status': 'modified'}]
        self.approval = {'user': {'login': 'reviewer[bot]', 'type': 'Bot'}, 'state': 'APPROVED', 'commit_id': 'head', 'submitted_at': '2026-01-01T00:00:00Z'}

    def test_safe_and_threshold(self):
        self.assertTrue(gate.approved(self.pr, self.files, [], "reviewer[bot]"))
        self.assertFalse(gate.approved({**self.pr, 'additions': 151}, self.files, [], "reviewer[bot]"))
        self.assertTrue(gate.approved({**self.pr, 'additions': 151}, self.files, [self.approval], "reviewer[bot]"))
        self.assertFalse(gate.approved(self.pr, [{'filename': 'migrations/a'}], [], 'reviewer[bot]'))
        self.assertFalse(gate.approved(self.pr, [{'filename': 'safe', 'previous_filename': 'db/a'}], [], 'reviewer[bot]'))
        self.assertFalse(gate.approved(self.pr, [], [], "reviewer[bot]"))

    def test_markdown_exempt_at_any_size_with_app_veto(self):
        self.assertTrue(gate.approved(self.pr, [{"filename": "docs/safe.md"}], [], "reviewer[bot]"))
        self.assertTrue(gate.approved({**self.pr, "additions": 900}, [{"filename": "docs/safe.md"}], [], "reviewer[bot]"))
        self.assertTrue(gate.approved(self.pr, [{"filename": "migrations/notes.md"}], [], "reviewer[bot]"))
        self.assertFalse(gate.approved(self.pr, [{"filename": "docs/safe.md"}], [{**self.approval, "state": "CHANGES_REQUESTED"}], "reviewer[bot]"))

    def test_risky_paths_and_renames(self):
        paths = ['LaunchAgents/job', 'a/job.plist', 'ops/install-job.sh', 'outlook/a', 'hxstore/a', 'hxprobe/a', 'calendar.js', '.github/workflows/deploy.yml', 'migrations/a', 'db/a', 'wrangler.toml', 'tools/migrate.py', 'CLAUDE.md', 'AGENTS.md', '.claude/settings.json', '.agents/skills/a.md', 'docs/agents/guide.md']
        for path in [p for p in paths if not p.lower().endswith('.md')]:
            for key in ('filename', 'previous_filename'):
                with self.subTest(path=path, key=key):
                    self.assertFalse(gate.approved(self.pr, [{'filename': 'lib/code.py', key: path}], [], 'reviewer[bot]'))
        self.assertFalse(gate.approved(self.pr, [{'filename': 'ops/new.py', 'status': 'added'}], [], 'reviewer[bot]'))

    def test_armed_safe_needs_no_review(self):
        armed = {**self.pr, 'auto_merge': {'enabled_by': {'login': 'author'}}}
        self.assertTrue(gate.approved(armed, self.files, [], "reviewer[bot]"))
        self.assertTrue(gate.approved(armed, self.files, [self.approval], "reviewer[bot]"))

    def test_added_machinery(self):
        pr = {"changed_files": 1, "additions": 1, "deletions": 0, "head": {"sha": "abc"}}
        for path, status, expected in [("bin/foo", "added", False), ("bin/existing", "modified", True), ("docs/x.md", "added", True)]:
            with self.subTest(path=path, status=status):
                self.assertEqual(gate.approved(pr, [{"filename": path, "status": status}], [], "reviewer[bot]"), expected)

    def test_review_states_and_identity(self):
        large = {**self.pr, 'additions': 151}
        for review in [{**self.approval, 'commit_id': 'old'}, {**self.approval, 'state': 'DISMISSED'}, {**self.approval, 'user': {'login': 'author'}}, {'body': 'VERDICT: APPROVE\nReviewed-SHA: head\nReviewer: x'}]:
            self.assertFalse(gate.approved(large, self.files, [review], "reviewer[bot]"))
        changes = {**self.approval, 'state': 'CHANGES_REQUESTED', 'submitted_at': '2026-01-02T00:00:00Z'}
        self.assertFalse(gate.approved(large, self.files, [self.approval, changes], "reviewer[bot]"))
        self.assertFalse(gate.approved(self.pr, self.files, [changes], "reviewer[bot]"))
        self.assertTrue(gate.approved(large, self.files, [changes, {**self.approval, 'submitted_at': '2026-01-03T00:00:00Z'}], 'reviewer[bot]'))

    def test_missing_app_setting_depends_only_on_diff(self):
        cases = [(self.pr, self.files, 0),
                 ({**self.pr, 'additions': 151}, self.files, 1),
                 (self.pr, [{'filename': 'migrations/a'}], 1)]
        for pr, files, expected in cases:
            with self.subTest(pr=pr, files=files), patch.dict(gate.os.environ, {'CARR_REVIEW_APP_LOGIN': ''}):
                with patch.object(gate, 'api', side_effect=[pr, {}, files, [self.approval], pr, {}]) as api:
                    self.assertEqual(gate.run({'number': 4}, 'owner/repo'), expected)
                    status = api.call_args.args[1]
                    self.assertEqual(status['state'], 'failure' if expected else 'success')
                    if expected:
                        self.assertIn('CARR_REVIEW_APP_LOGIN', status['description'])

    def test_self_and_non_app_approvals_fail(self):
        pr = {**self.pr, 'additions': 151}
        for login, configured in [('author', 'author'), ('someone', 'reviewer[bot]')]:
            with self.subTest(login=login):
                review = {**self.approval, 'user': {'login': login}}
                self.assertFalse(gate.approved(pr, self.files, [review], configured))

    def test_human_app_setting_fails_closed(self):
        pr = {**self.pr, 'additions': 151}
        human = {**self.approval, 'user': {'login': 'human', 'type': 'User'}}
        self.assertFalse(gate.approved(pr, self.files, [human], 'human'))
        for candidate in (pr, self.pr):
            with self.subTest(pr=candidate), patch.dict(gate.os.environ, {'CARR_REVIEW_APP_LOGIN': 'human'}):
                with patch.object(gate, 'api', side_effect=[candidate, {}, self.files, [human], candidate, {}]) as api:
                    self.assertEqual(gate.run({'number': 4}, 'owner/repo'), 1)
                    self.assertEqual(api.call_args.args[1]['state'], 'failure')
                    self.assertIn('CARR_REVIEW_APP_LOGIN must name an App bot', api.call_args.args[1]['description'])

    def test_app_approval_requires_bot_type(self):
        pr = {**self.pr, 'additions': 151}
        for kind in ('User', None):
            review = {**self.approval, 'user': {'login': 'reviewer[bot]', 'type': kind}}
            self.assertFalse(gate.approved(pr, self.files, [review], 'reviewer[bot]'))
        self.assertTrue(gate.approved(pr, self.files, [self.approval], 'reviewer[bot]'))
        self.assertFalse(gate.approved({**pr, 'user': self.approval['user']}, self.files,
                                       [self.approval], 'reviewer[bot]'))

    def test_unknown_reviewer_never_approves(self):
        large = {**self.pr, "additions": 151}
        self.assertFalse(gate.approved(large, self.files, [self.approval], ""))
        self.assertFalse(gate.approved(large, self.files, [self.approval], "other[bot]"))

    def test_card_and_skip_ci(self):
        self.assertFalse(gate.card_allowed({**self.pr, 'body': ''}, self.files))
        self.assertFalse(gate.approved({**self.pr, 'title': '[skip ci] fix'}, self.files, [self.approval], "reviewer[bot]"))

    def test_adapter_publishes_current_head_decision(self):
        with patch.object(gate, 'api', side_effect=[self.pr, {}, self.files, [], self.pr, {}]) as api:
            self.assertEqual(gate.run({'number': 4}, 'owner/repo'), 0)
            self.assertEqual(api.call_args.args[0], 'repos/owner/repo/statuses/head')
            self.assertEqual(api.call_args.args[1]['state'], 'success')

    def test_arming_during_run_still_publishes_status(self):
        latest = {**self.pr, "auto_merge": {"enabled_by": {"login": "author"}}}
        with patch.object(gate, "api", side_effect=[self.pr, {}, self.files, [], latest, {}]) as api:
            self.assertEqual(gate.run({"number": 4}, "owner/repo"), 0)
            self.assertEqual(api.call_args.args[1]["state"], "success")

    def test_adapter_checks_head_and_metadata(self):
        with patch.object(gate, 'api', side_effect=[self.pr, {}, self.files, [], {**self.pr, 'title': 'changed'}]) as api:
            gate.run({'number': 4}, 'owner/repo')
            self.assertEqual(api.call_count, 5)

class QueueAndCutover(unittest.TestCase):
    setUp = ReviewGate.setUp
    def test_queue_checks_all_current_constituents(self):
        event = {'merge_group': {'head_ref': 'refs/heads/gh-readonly-queue/main/pr-4-head', 'head_sha': 'queue', 'base_sha': 'base'}}
        large = {**self.pr, 'number': 4, 'state': 'open', 'additions': 900}
        other = {**large, 'number': 5, 'head': {'sha': 'other'}}
        def exercise(approved_other, login='reviewer[bot]', broken=False, moved=False):
            calls = []
            def api(path, payload=None):
                calls.append((path, payload))
                if payload: return {}
                if broken: raise ValueError('lookup failed')
                if '/compare/' in path: return {'commits': [{'sha': 'head'}, {'sha': 'other'}, {'sha': 'queue'}], 'total_commits': 3}
                if '/commits/head/pulls' in path: return [large]
                if '/commits/other/pulls' in path: return [{**other, 'head': {'sha': 'moved'}}] if moved else [other]
                if '/commits/queue/pulls' in path: return []
                if '/files?' in path: return self.files
                if '/reviews?' in path: return [self.approval] if '/4/' in path else ([{**self.approval, 'commit_id': 'other'}] if approved_other else [])
                if path.endswith('/4'): return large
                if path.endswith('/5'): return {**other, 'head': {'sha': 'moved'}} if moved else other
                raise AssertionError(path)
            with patch.dict(gate.os.environ, {'CARR_REVIEW_APP_LOGIN': login}), patch.object(gate, 'api', side_effect=api):
                result = gate.run(event, 'owner/repo')
            self.assertEqual(calls[-1][1]['state'], 'success' if approved_other and login and not broken and not moved else 'failure')
            return result
        self.assertEqual(exercise(True), 0)
        self.assertEqual(exercise(False), 1)
        self.assertEqual(exercise(True, login=''), 1)
        self.assertEqual(exercise(True, broken=True), 1)
        self.assertEqual(exercise(True, moved=True), 1)

    def test_dispatch_evaluates_all_open_main_heads(self):
        prs = [{'number': 4}, {'number': 5}]
        with patch.object(gate, 'api', return_value=prs) as api, patch.object(gate, 'evaluate_pr', return_value=0) as evaluate:
            self.assertEqual(gate.run({}, 'owner/repo'), 0)
            self.assertIn('state=open&base=main', api.call_args.args[0])
            self.assertEqual([c.args[1] for c in evaluate.call_args_list], [4, 5])
        workflow = Path(__file__).resolve().parents[1] / '.github/workflows/review-gate.yml'
        self.assertIn('workflow_dispatch:', workflow.read_text())

class MergeHistory(unittest.TestCase):
    def test_main_merge_requires_new_head_approval(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            def git(*args):
                return subprocess.check_output(['git', '-C', raw, *args], text=True,
                    stderr=subprocess.DEVNULL, env=fixture_env()).strip()
            git('init', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.com')
            (root / 'base').write_text('base')
            git('add', 'base'); git('commit', '-m', 'base')
            git('checkout', '-b', 'feature')
            (root / 'feature').write_text('approved')
            git('add', 'feature'); git('commit', '-m', 'feature')
            old = git('rev-parse', 'HEAD')
            git('checkout', 'main')
            (root / 'base').write_text('main advanced')
            git('add', 'base'); git('commit', '-m', 'main moved')
            git('checkout', 'feature'); git('merge', '--no-edit', 'main')
            head = git('rev-parse', 'HEAD')
            self.assertNotEqual(old, head)
            pr = {'head': {'sha': head}, 'additions': 151, 'deletions': 0, 'changed_files': 1}
            review = {'user': {'login': 'reviewer[bot]', 'type': 'Bot'}, 'state': 'APPROVED', 'commit_id': old}
            self.assertFalse(gate.approved(pr, [{'filename': 'feature'}], [review], 'reviewer[bot]'))
            self.assertTrue(gate.approved(pr, [{'filename': 'feature'}],
                [{**review, 'commit_id': head}], 'reviewer[bot]'))

if __name__ == '__main__': unittest.main()
