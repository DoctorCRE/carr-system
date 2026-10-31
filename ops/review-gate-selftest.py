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
        self.approval = {'user': {'login': 'reviewer'}, 'state': 'APPROVED', 'commit_id': 'head', 'submitted_at': '2026-01-01T00:00:00Z'}

    def test_safe_and_threshold(self):
        self.assertTrue(gate.approved(self.pr, self.files, [], "reviewer"))
        self.assertFalse(gate.approved({**self.pr, 'additions': 151}, self.files, [], "reviewer"))
        self.assertTrue(gate.approved({**self.pr, 'additions': 151}, self.files, [self.approval], "reviewer"))
        self.assertFalse(gate.approved(self.pr, [{'filename': 'migrations/a'}], [], 'reviewer'))
        self.assertFalse(gate.approved(self.pr, [{'filename': 'safe', 'previous_filename': 'db/a'}], [], 'reviewer'))
        self.assertFalse(gate.approved(self.pr, [], [], "reviewer"))

    def test_docs_only_exemption_at_any_size(self):
        self.assertTrue(gate.approved({**self.pr, "additions": 900}, [{"filename": "docs/outlook.md"}], [], "reviewer"))

    def test_risky_paths_and_renames(self):
        paths = ['LaunchAgents/job', 'a/job.plist', 'ops/install-job.sh', 'outlook/a', 'hxstore/a', 'hxprobe/a', 'calendar.js', '.github/workflows/deploy.yml', 'migrations/a', 'db/a', 'wrangler.toml', 'tools/migrate.py']
        for path in paths:
            for key in ('filename', 'previous_filename'):
                with self.subTest(path=path, key=key):
                    self.assertFalse(gate.approved(self.pr, [{key: path}], [], 'reviewer'))
        self.assertFalse(gate.approved(self.pr, [{'filename': 'ops/new.py', 'status': 'added'}], [], 'reviewer'))

    def test_armed_safe_needs_no_review(self):
        armed = {**self.pr, 'auto_merge': {'enabled_by': {'login': 'author'}}}
        self.assertTrue(gate.approved(armed, self.files, [], "reviewer"))
        self.assertTrue(gate.approved(armed, self.files, [self.approval], "reviewer"))

    def test_review_states_and_identity(self):
        large = {**self.pr, 'additions': 151}
        for review in [{**self.approval, 'commit_id': 'old'}, {**self.approval, 'state': 'DISMISSED'}, {**self.approval, 'user': {'login': 'author'}}, {'body': 'VERDICT: APPROVE\nReviewed-SHA: head\nReviewer: x'}]:
            self.assertFalse(gate.approved(large, self.files, [review], "reviewer"))
        changes = {**self.approval, 'state': 'CHANGES_REQUESTED', 'submitted_at': '2026-01-02T00:00:00Z'}
        self.assertFalse(gate.approved(large, self.files, [self.approval, changes], "reviewer"))
        self.assertFalse(gate.approved(self.pr, self.files, [changes], "reviewer"))
        self.assertTrue(gate.approved(large, self.files, [changes, {**self.approval, 'submitted_at': '2026-01-03T00:00:00Z'}], 'reviewer'))

    def test_older_approval_requires_proven_unchanged_merges(self):
        pr = {**self.pr, "additions": 151}
        old = {**self.approval, "commit_id": "old"}
        self.assertTrue(gate.approved(pr, self.files, [old], "reviewer", lambda sha: sha == "old"))
        self.assertFalse(gate.approved(pr, self.files, [old], "reviewer", lambda sha: False))

    def test_unknown_reviewer_never_approves(self):
        large = {**self.pr, "additions": 151}
        self.assertFalse(gate.approved(large, self.files, [self.approval], ""))
        self.assertFalse(gate.approved(large, self.files, [self.approval], "other[bot]"))

    def test_card_and_skip_ci(self):
        self.assertFalse(gate.card_allowed({**self.pr, 'body': ''}, self.files))
        self.assertFalse(gate.approved({**self.pr, 'title': '[skip ci] fix'}, self.files, [self.approval], "reviewer"))

    def test_adapter_publishes_current_head_decision(self):
        with patch.object(gate, 'api', side_effect=[self.pr, {}, self.files, [], self.pr, {}]) as api:
            self.assertEqual(gate.run({'number': 4}, 'owner/repo'), 0)
            self.assertEqual(api.call_args.args[0], 'repos/owner/repo/statuses/head')
            self.assertEqual(api.call_args.args[1]['state'], 'success')

    def test_adapter_checks_head_and_metadata(self):
        with patch.object(gate, 'api', side_effect=[self.pr, {}, self.files, [], {**self.pr, 'title': 'changed'}]) as api:
            gate.run({'number': 4}, 'owner/repo')
            self.assertEqual(api.call_count, 5)

class MergeHistory(unittest.TestCase):
    def test_only_unchanged_main_merge_preserves_review(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            def git(*args):
                return subprocess.check_output(['git', '-C', raw, *args], text=True, stderr=subprocess.DEVNULL, env=fixture_env()).strip()
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
            base = git('rev-parse', 'HEAD')
            git('checkout', 'feature'); git('merge', '--no-edit', 'main')
            head = git('rev-parse', 'HEAD')
            self.assertTrue(gate.unchanged_merges(raw, base, old, head))
            (root / 'feature').write_text('unreviewed merge resolution')
            git('add', 'feature'); git('commit', '--amend', '--no-edit')
            self.assertFalse(gate.unchanged_merges(raw, base, old, git('rev-parse', 'HEAD')))
            git('reset', '--hard', head)
            (root / 'feature').write_text('changed after review')
            git('add', 'feature'); git('commit', '-m', 'author change')
            self.assertFalse(gate.unchanged_merges(raw, base, old, git('rev-parse', 'HEAD')))

if __name__ == '__main__': unittest.main()
