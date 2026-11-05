#!/usr/bin/env python3
"""Exercise held release through its public reconciler and real temporary Git repositories."""
import importlib.util
from pathlib import Path
import tempfile
import os
from git_env import fixture_env
import subprocess
import sys
from unittest.mock import patch
import unittest

spec = importlib.util.spec_from_file_location('held_release', Path(__file__).with_name('held-branch-release.py'))
assert spec is not None and spec.loader is not None
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class Admission(unittest.TestCase):
    def test_unmerged_event_ignored(self):
        self.assertEqual(release.reconcile({'action': 'closed', 'pull_request': {'merged': False}}, [], None), [])

    def test_manifest_rejects_unsafe_and_duplicate_branches(self):
        good = {'branch': 'topic', 'card': 'WR823.S15.J6', 'holders': [1, 2], 'title': 'A held change', 'body': 'Card: WR823.S15.J6'}
        self.assertEqual(release.manifest({'schema': 'held-branches.v1', 'branches': [good]}), [good])
        for field, value in [('branch', '--main'), ('branch', 'main'), ('holders', []), ('holders', [True]), ('card', 'bad')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                release.manifest({'schema': 'held-branches.v1', 'branches': [{**good, field: value}]})
        with self.assertRaises(ValueError):
            release.manifest({'schema': 'held-branches.v1', 'branches': [good, good]})


EVENT = {'action': 'closed', 'pull_request': {'merged': True, 'base': {'ref': 'main'}},
         'repository': {'full_name': release.REPOSITORY}}
ROW = {'branch': 'topic', 'card': 'WR823.S15.J6', 'holders': [1, 2],
       'title': 'A held change', 'body': 'Card: WR823.S15.J6'}


class LocalGithub:
    def __init__(self, root):
        self.transport = release.Github(root)
        self.merged = {1, 2}
        self.prs = []
        self.overlaps = []
        self.created = 0

    def git(self, *args, **kwargs):
        return self.transport.git(*args, **kwargs)

    def holder_merged(self, number):
        return number in self.merged

    def pull_requests(self, branch=None):
        return self.prs

    def overlap(self, paths, branch):
        return self.overlaps

    def create_pr(self, row):
        self.created += 1
        self.prs = [{'number': 42, 'state': 'open', 'base': {'ref': 'main'}}]
        return 42


class Reconciliation(unittest.TestCase):
    def setUp(self):
        isolated = patch.dict(os.environ, fixture_env(), clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / 'remote.git'
        self.tree = root / 'source'
        release.command('git', 'init', '--bare', str(self.remote))
        release.command('git', 'clone', str(self.remote), str(self.tree))
        self.g = LocalGithub(self.tree)
        self.g.git('config', 'user.email', 'test@example.invalid')
        self.g.git('config', 'user.name', 'Synthetic builder')
        self.g.git('switch', '-c', 'main')
        self.commit('shared.txt', 'base', 'base')
        self.g.git('push', 'origin', 'main')
        self.g.git('switch', '-c', 'topic')
        self.commit('topic.txt', 'held work', 'held')
        self.g.git('push', 'origin', 'topic')
        self.head = self.g.git('rev-parse', 'HEAD')
        self.g.git('switch', 'main')
        self.commit('main.txt', 'new main', 'main advances')
        self.g.git('push', 'origin', 'main')
        self.main = self.g.git('rev-parse', 'HEAD')

    def commit(self, path, text, message):
        (self.tree / path).write_text(text)
        self.g.git('add', path)
        self.g.git('commit', '-m', message)

    def run_release(self):
        return release.reconcile(EVENT, [ROW], self.g, execute=True)[0]

    def test_all_holders_then_publish_and_duplicate_converges(self):
        self.g.merged = {1}
        self.assertEqual(self.run_release()['reason'], 'holders_unmerged')
        self.assertEqual(self.g.created, 0)
        self.g.merged.add(2)
        first = self.run_release()
        self.assertEqual((first['state'], first['pr']), ('published', 42))
        self.g.git('merge-base', '--is-ancestor', self.main, first['head'])
        self.g.git('merge-base', '--is-ancestor', self.head, first['head'])
        self.assertEqual(self.run_release()['head'], first['head'])
        self.assertEqual(self.g.created, 1)

    def test_hook_git_location_cannot_escape_worktree(self):
        with patch.dict(os.environ, {'GIT_DIR': '/nonexistent-hook-git-dir'}):
            self.assertEqual(self.run_release()['state'], 'published')

    def test_default_mode_collects_without_remote_writes(self):
        result = release.reconcile(EVENT, [ROW], self.g)[0]
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(self.g.created, 0)
        self.assertEqual(self.g.git('rev-parse', 'origin/topic'), self.head)

    def test_overlap_holds_before_any_push(self):
        self.g.overlaps = [3]
        self.assertEqual(self.run_release()['reason'], 'overlap')
        self.assertEqual(self.g.git('rev-parse', 'origin/topic'), self.head)
        self.assertEqual(self.g.created, 0)

    def test_overlap_preserves_whitespace_and_unicode_paths(self):
        self.g.git('switch', 'topic')
        self.commit(' spaced café.txt', 'held', 'unusual path')
        self.g.git('push', 'origin', 'topic')
        self.g.git('switch', 'main')
        with patch.object(self.g, 'overlap', return_value=[3]) as overlap:
            self.assertEqual(self.run_release()['reason'], 'overlap')
            self.assertIn(' spaced café.txt', overlap.call_args.args[0])

    def test_new_overlap_after_merge_holds(self):
        with patch.object(self.g, 'overlap', side_effect=[[], [3]]):
            self.assertEqual(self.run_release()['reason'], 'overlap_after_merge')
        self.assertEqual(self.g.git('rev-parse', 'origin/topic'), self.head)

    def test_conflict_routes_deterministic_fix_and_cleans_worktree(self):
        self.g.git('switch', 'topic')
        self.commit('shared.txt', 'held edit', 'topic edits')
        self.g.git('push', 'origin', 'topic')
        held = self.g.git('rev-parse', 'HEAD')
        self.g.git('switch', 'main')
        self.commit('shared.txt', 'main edit', 'main edits')
        self.g.git('push', 'origin', 'main')
        result = self.run_release()
        self.assertEqual(result['state'], 'fix_needed')
        self.assertEqual(result['fix_job']['paths'], ['shared.txt'])
        self.assertEqual(self.run_release()['fix_job'], result['fix_job'])
        self.assertEqual(self.g.git('rev-parse', 'origin/topic'), held)
        self.assertEqual(self.g.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_release_serialization_rechecks_overlap_between_branches(self):
        self.g.git('branch', 'second', self.head)
        self.g.git('push', 'origin', 'second')
        def prs(branch=None):
            return self.g.prs if branch == 'topic' else []
        with patch.object(self.g, 'pull_requests', side_effect=prs), patch.object(self.g, 'overlap', side_effect=[[], [], [42]]):
            results = release.reconcile(EVENT, [ROW, {**ROW, 'branch': 'second'}], self.g, execute=True)
        self.assertEqual([r['state'] for r in results], ['published', 'held'])
        self.assertEqual(results[1]['holders'], [42])
        self.assertEqual(self.g.created, 1)
        self.assertEqual(self.g.git('rev-parse', 'origin/second'), self.head)

    def test_closed_pr_never_reopens(self):
        self.g.prs = [{'state': 'closed'}]
        self.assertEqual(self.run_release()['state'], 'retired')
        self.assertEqual(self.g.created, 0)

    def test_empty_branch_retires(self):
        self.g.git('merge', '--no-edit', 'topic')
        self.g.git('push', 'origin', 'main')
        self.assertEqual(self.run_release()['reason'], 'empty_diff')

    def test_push_failure_stops_and_cleans_worktree(self):
        original = self.g.git
        def git(*args, **kwargs):
            if args[0] == 'push':
                raise subprocess.CalledProcessError(1, 'git push')
            return original(*args, **kwargs)
        with patch.object(self.g, 'git', side_effect=git), self.assertRaises(subprocess.CalledProcessError):
            self.run_release()
        self.assertEqual(self.g.created, 0)
        self.assertEqual(self.g.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_lost_push_response_converges(self):
        original = self.g.git
        lost = False
        def git(*args, **kwargs):
            nonlocal lost
            answer = original(*args, **kwargs)
            if args[0] == 'push' and not lost:
                lost = True
                raise subprocess.TimeoutExpired('git push', 45)
            return answer
        with patch.object(self.g, 'git', side_effect=git), self.assertRaises(subprocess.TimeoutExpired):
            self.run_release()
        self.assertEqual(self.run_release()['state'], 'published')
        self.assertEqual(self.g.created, 1)

    def test_lost_create_response_discovers_existing_pr(self):
        original = self.g.create_pr
        def create(row):
            original(row)
            raise subprocess.TimeoutExpired('gh', 45)
        with patch.object(self.g, 'create_pr', side_effect=create), self.assertRaises(subprocess.TimeoutExpired):
            self.run_release()
        self.assertEqual(self.run_release()['pr'], 42)
        self.assertEqual(self.g.created, 1)

    def test_production_proof_cli_pass_and_failure_use_actual_receipt_writer(self):
        result = self.run_release()
        receipt = Path(self.temp.name) / 'held-release.json'
        runtime = self.g.git('rev-parse', 'HEAD')
        release.write_receipt(receipt, [result], runtime)
        observer = Path(__file__).with_name('studio-proof-observe.py')
        argv = [sys.executable, str(observer), '--receipt-file', str(receipt.with_suffix('.txt')),
                '--service', 'held-branch-release', '--runtime-sha', runtime, '--count', 'published']
        self.assertEqual(subprocess.run([*argv, '--minimum', '1'], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([*argv, '--minimum', '2'], capture_output=True).returncode, 1)

    def test_event_scope_rejects_unmerged_foreign_and_other_base(self):
        for event in ({**EVENT, 'action': 'opened'},
                      {**EVENT, 'repository': {'full_name': 'other/repo'}},
                      {**EVENT, 'pull_request': {'merged': True, 'base': {'ref': 'dev'}}}):
            self.assertEqual(release.reconcile(event, [ROW], None), [])


class GithubContracts(unittest.TestCase):
    def test_rest_holder_shape_requires_merged_main(self):
        g = release.Github(Path('.'))
        with patch.object(g, 'api', return_value={'merged': False, 'base': {'ref': 'main'}}):
            self.assertFalse(g.holder_merged(1))
        with patch.object(g, 'api', return_value={'merged': True, 'base': {'ref': 'main'}}) as api:
            self.assertTrue(g.holder_merged(2))
            self.assertTrue(g.holder_merged(2))
            api.assert_called_once()
        with patch.object(g, 'api', return_value=[{}] * 100), self.assertRaises(ValueError):
            g.pull_requests()

    def test_same_branch_name_in_fork_still_blocks(self):
        g = release.Github(Path('.'))
        prs = [{'number': 8, 'head': {'ref': 'topic', 'repo': {'full_name': 'other/carr-system'}}}]
        with patch.object(g, 'pull_requests', return_value=prs), patch.object(g, 'files', return_value={'a.txt'}):
            self.assertEqual(g.overlap({'a.txt'}, 'topic'), [8])

    def test_renamed_files_overlap_both_paths(self):
        g = release.Github(Path('.'))
        with patch.object(release, 'command', return_value='[[{"filename":"new.txt","previous_filename":"old.txt"}]]'):
            self.assertEqual(g.files(1), {'new.txt', 'old.txt'})


if __name__ == '__main__':
    unittest.main()
