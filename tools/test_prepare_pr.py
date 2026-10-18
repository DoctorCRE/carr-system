"""Preparation must push a descendant and preserve source conflicts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from git_env import fixture_env
from prepare_pr import prepare


class PrepareTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, fixture_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'test')
        (self.repo / 'source.txt').write_text('base\n')
        self.git('add', 'source.txt')
        self.git('commit', '-qm', 'base')
        self.git('branch', 'feature')
        (self.repo / 'new.txt').write_text('main\n')
        self.git('add', 'new.txt')
        self.git('commit', '-qm', 'advance')
        self.main = self.git('rev-parse', 'HEAD')
        self.head = self.git('rev-parse', 'feature')
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(self.repo), str(self.remote)], check=True)
        self.receipt = self.root / 'receipt.json'

    def git(self, *args, repo=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', *args], cwd=repo or self.repo, env=env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def test_normal_push_contains_both_main_and_generated_commit(self):
        def regen(repo):
            (repo / 'generated.txt').write_text('fresh\n')
        sha = prepare(str(self.remote), 'feature', self.head, self.main, self.receipt, regen)
        self.assertEqual(self.git('rev-parse', 'feature', repo=self.remote), sha)
        self.git('merge-base', '--is-ancestor', self.head, sha, repo=self.remote)
        self.git('merge-base', '--is-ancestor', self.main, sha, repo=self.remote)
        self.assertEqual(self.git('show', f'{sha}:generated.txt', repo=self.remote), 'fresh')
        self.assertEqual(json.loads(self.receipt.read_text())['prepared'], sha)

    def test_failed_generator_never_pushes(self):
        def fail(_):
            raise ValueError('seeded generator failure')
        with self.assertRaisesRegex(ValueError, 'seeded'):
            prepare(str(self.remote), 'feature', self.head, self.main, self.receipt, fail)
        self.assertEqual(self.git('rev-parse', 'feature', repo=self.remote), self.head)

    def test_main_race_before_push_refuses(self):
        def advance(_):
            self.git('update-ref', 'refs/heads/main', self.head, repo=self.remote)
        with self.assertRaisesRegex(ValueError, 'main moved'):
            prepare(str(self.remote), 'feature', self.head, self.main, self.receipt, advance)
        self.assertEqual(self.git('rev-parse', 'feature', repo=self.remote), self.head)

    def conflicting_branches(self, path):
        self.git('switch', '-q', 'feature')
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('PR bytes\n')
        self.git('add', '--', path)
        self.git('commit', '-qm', 'PR change')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('switch', '-q', 'main')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('main bytes\n')
        self.git('add', '--', path)
        self.git('commit', '-qm', 'main change')
        self.main = self.git('rev-parse', 'HEAD')
        self.git('push', str(self.remote), 'main', 'feature')

    def test_source_conflict_is_never_overwritten(self):
        self.conflicting_branches('source.txt')
        with self.assertRaisesRegex(ValueError, 'source conflict'):
            prepare(str(self.remote), 'feature', self.head, self.main, self.receipt, lambda _: None)
        self.assertEqual(self.git('rev-parse', 'feature', repo=self.remote), self.head)

    def test_derived_only_conflict_is_resolved_by_generator(self):
        self.conflicting_branches('ops/config/gate-baseline.json')
        def regen(repo):
            (repo / 'ops/config/gate-baseline.json').write_text('fresh\n')
        sha = prepare(str(self.remote), 'feature', self.head, self.main, self.receipt, regen)
        self.assertEqual(self.git('show', f'{sha}:ops/config/gate-baseline.json', repo=self.remote), 'fresh')

    def test_pr_race_before_push_refuses(self):
        def advance(repo):
            (repo / 'generated.txt').write_text('fresh\n')
            self.git('update-ref', 'refs/heads/feature', self.main, repo=self.remote)
        with self.assertRaisesRegex(ValueError, 'PR head moved'):
            prepare(str(self.remote), 'feature', self.head, self.main, self.receipt, advance)
        self.assertEqual(self.git('rev-parse', 'feature', repo=self.remote), self.main)


if __name__ == '__main__':
    unittest.main()
