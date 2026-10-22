#!/usr/bin/env python3
"""Exercise checkout retirement against disposable Git repositories."""
import os
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops.git_env import fixture_env
from ops import job_cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='carr-cleaner-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = fixture_env()
        self.remote = self.root / 'origin.git'
        self.git(self.root, 'init', '--bare', str(self.remote))
        self.seed = self.root / 'seed'
        self.git(self.root, 'clone', str(self.remote), str(self.seed))
        self.git(self.seed, 'config', 'user.email', 'jbookout@users.noreply.github.com')
        self.git(self.seed, 'config', 'user.name', 'Fixture')
        self.git(self.seed, 'switch', '-c', 'main')
        (self.seed / 'file').write_text('base')
        self.git(self.seed, 'add', 'file')
        self.git(self.seed, 'commit', '-m', 'base')
        self.git(self.seed, 'push', '-u', 'origin', 'main')
        self.workspace = self.root / 'room-codex-fixture'
        self.workspace.mkdir()
        self.repo = self.workspace / 'checkout'
        self.git(self.root, 'clone', '-b', 'main', str(self.remote), str(self.repo))
        self.git(self.repo, 'switch', '-c', 'job')
        for key, value in [('user.email', 'jbookout@users.noreply.github.com'), ('user.name', 'Fixture')]:
            self.git(self.repo, 'config', key, value)
        self.staging = self.root / '_to_delete'
        self.now = time.time() + 3 * 86400

    def git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], env=self.env,
                              check=True, text=True, capture_output=True).stdout.strip()

    def commit(self):
        (self.repo / 'file').write_text('job change')
        self.git(self.repo, 'add', 'file')
        self.git(self.repo, 'commit', '-m', 'job')

    def clean(self, **kwargs):
        return job_cleanup.clean(self.repo, staging=self.staging, now=self.now, **kwargs)

    def test_unpushed_commit_kept(self):
        self.commit()
        result = self.clean()
        self.assertEqual(result['action'], 'kept')
        self.assertTrue(self.repo.exists())

    def test_dirty_tree_kept(self):
        (self.repo / 'untracked').write_text('work')
        result = self.clean()
        self.assertEqual(result['reason'], 'dirty tree')
        self.assertTrue(self.repo.exists())

    def test_live_lock_kept(self):
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        lock = self.repo / '.git/room-writer.lock'
        lock.write_text(json.dumps({'kind': 'process_group', 'pid': os.getpid(), 'pgid': os.getpgrp()}))
        result = self.clean(immediate=True)
        self.assertEqual(result['reason'], 'live or uncertain writer')
        self.assertTrue(self.repo.exists())

    def test_merged_pushed_checkout_staged(self):
        self.commit()
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        with patch.object(job_cleanup, 'pull_requests', return_value=[{'state': 'MERGED', 'headRefOid': head}]):
            result = self.clean()
        self.assertEqual(result['action'], 'staged')
        self.assertFalse(self.repo.exists())
        self.assertEqual(self.git(Path(result['destination']) / 'checkout', 'rev-parse', 'HEAD'), head)

    def test_other_unpushed_branch_kept(self):
        self.git(self.repo, 'switch', '-c', 'private-work')
        self.commit()
        self.git(self.repo, 'switch', 'job')
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        result = self.clean(immediate=True)
        self.assertEqual(result['action'], 'kept')
        self.assertTrue(self.repo.exists())

    def test_pushed_open_pr_kept_by_daily_sweep(self):
        self.commit()
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        with patch.object(job_cleanup, 'pull_requests', return_value=[{'state': 'OPEN', 'headRefOid': self.git(self.repo, 'rev-parse', 'HEAD')}]):
            result = self.clean()
        self.assertEqual(result['action'], 'kept')
        self.assertTrue(self.repo.exists())

    def test_young_checkout_kept(self):
        self.now = time.time()
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        result = self.clean()
        self.assertEqual(result['reason'], 'younger than two days')
        self.assertTrue(self.repo.exists())

    def test_pushed_checkout_removed_after_first_week(self):
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        self.staging.mkdir()
        (self.staging / '.cleaner-started').write_text(str(self.now - 8 * 86400))
        (self.workspace / 'evidence').write_text('retained')
        result = self.clean(immediate=True)
        self.assertEqual(result['action'], 'removed')
        self.assertFalse(self.repo.exists())
        self.assertEqual((self.workspace / 'evidence').read_text(), 'retained')

    def test_held_flock_kept_without_lock_payload(self):
        with (self.repo / '.git/room-writer.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.clean(immediate=True)
        self.assertEqual(result['reason'], 'live or uncertain writer')
        self.assertTrue(self.repo.exists())

    def test_merged_deleted_remote_branch_staged(self):
        self.commit()
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        self.git(self.repo, 'push', 'origin', '--delete', 'job')
        with patch.object(job_cleanup, 'pull_requests', return_value=[{'state': 'MERGED', 'headRefOid': head}]):
            result = self.clean()
        self.assertEqual(result['action'], 'staged')
        self.assertFalse(self.repo.exists())

    def test_worktree_staged_with_registration_preserved(self):
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        tree = self.root / 'worktree'
        self.git(self.seed, 'fetch', 'origin', 'job:job')
        self.git(self.seed, 'worktree', 'add', str(tree), 'job')
        result = job_cleanup.clean(tree, staging=self.staging, now=self.now, immediate=True)
        self.assertEqual(result['action'], 'staged')
        self.assertFalse(tree.exists())
        self.assertIn(result['destination'], self.git(self.seed, 'worktree', 'list', '--porcelain'))

    def test_stash_kept(self):
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        (self.repo / 'file').write_text('stashed work')
        self.git(self.repo, 'stash', 'push')
        result = self.clean(immediate=True)
        self.assertEqual(result['action'], 'kept')
        self.assertTrue(self.repo.exists())

    def test_remote_branch_ahead_still_preserves_local_tip(self):
        self.git(self.repo, 'push', '-u', 'origin', 'job')
        self.git(self.seed, 'fetch', 'origin', 'job:job')
        self.git(self.seed, 'switch', 'job')
        (self.seed / 'next').write_text('remote change')
        self.git(self.seed, 'add', 'next')
        self.git(self.seed, 'commit', '-m', 'remote change')
        self.git(self.seed, 'push', 'origin', 'job')
        result = self.clean(immediate=True)
        self.assertEqual(result['action'], 'staged')
        self.assertFalse(self.repo.exists())

    def test_daily_empty_job_without_remote_branch_staged(self):
        with patch.object(job_cleanup, 'pull_requests', return_value=[]):
            result = self.clean()
        self.assertEqual(result['action'], 'staged')
        self.assertFalse(self.repo.exists())


if __name__ == '__main__':
    unittest.main()
