#!/usr/bin/env python3
import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops.git_env import fixture_env
import desks
import dispatch

REAL_RUN = subprocess.run


class CheckoutDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'canonical'
        self.source.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Checkout Author')
        self.git('config', 'user.email', '123+author@users.noreply.github.com')
        (self.source / 'AGENTS.md').write_text('fixture instructions\n')
        self.git('add', 'AGENTS.md')
        self.git('commit', '-m', 'fixture')
        self.git('branch', 'repair')
        self.origin = self.root / 'origin.git'
        REAL_RUN(['git', 'clone', '--bare', str(self.source), str(self.origin)],
                 check=True, capture_output=True, env=fixture_env())
        self.git('remote', 'add', 'origin', str(self.origin))
        (self.root / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'gpt-6.1-sol'}]}))
        self.env = fixture_env({**os.environ, 'CODEX_HOME': str(self.root),
                                'CARR_REPO_ROOT': str(self.source)})
        self.reg = desks.Registry(self.root / 'desks.json')
        self.reg.register('cx', 'codex-session', cwd=str(self.source), family='sol', effort='high')
        self.results = self.root / 'results.jsonl'
        self.codex_calls = []
        canonical = patch('codex_checkout.CANONICAL_REPO', self.source)
        canonical.start()
        self.addCleanup(canonical.stop)
        checkout_root = patch('codex_checkout.CHECKOUT_ROOT', self.root)
        checkout_root.start()
        self.addCleanup(checkout_root.stop)

    def git(self, *args, cwd=None):
        return REAL_RUN(['git', '-C', str(cwd or self.source), *args], check=True,
                        capture_output=True, text=True, env=fixture_env()).stdout.strip()

    def execute(self, argv, **kwargs):
        if argv[0] != 'codex':
            return REAL_RUN(argv, **kwargs)
        self.codex_calls.append((argv, kwargs))
        Path(argv[argv.index('-o') + 1]).write_text('done')
        return subprocess.CompletedProcess(argv, 0, '{"type":"turn.completed"}\n', '')

    def send(self, **kwargs):
        with patch.object(dispatch.subprocess, 'run', side_effect=self.execute):
            row = dispatch.dispatch('cx', 'repair it', registry=self.reg,
                                    results_path=self.results, env=self.env, **kwargs)
        workspace = Path(row['checkout_workspace'])
        self.addCleanup(lambda: shutil.rmtree(workspace, ignore_errors=True))
        return row

    def test_clone_cwd_and_noreply_author_before_fresh_turn(self):
        before = self.git('status', '--porcelain')
        row = self.send(checkout='repair', fresh=True)
        repo = Path(row['checkout_path'])
        workspace = Path(row['checkout_workspace'])
        self.assertTrue(repo.is_relative_to(self.root))
        self.assertEqual(repo.parent, workspace)
        self.assertTrue((repo / '.git').is_dir())
        self.assertFalse((workspace / '.git').exists())
        self.assertEqual(self.git('branch', '--show-current', cwd=repo), 'repair')
        self.assertEqual(self.git('config', '--local', 'user.name', cwd=repo), 'Checkout Author')
        self.assertEqual(self.git('config', '--local', 'user.email', cwd=repo),
                         '123+author@users.noreply.github.com')
        argv, options = self.codex_calls[0]
        self.assertEqual(argv[argv.index('-C') + 1], str(workspace))
        self.assertEqual(options['cwd'], str(workspace))
        self.assertIn('--skip-git-repo-check', argv)
        self.assertIn(str(repo), argv[-1])
        self.assertIn('AGENTS.md', argv[-1])
        self.assertNotIn('-s', argv)
        self.assertNotIn('--add-dir', argv)
        self.assertEqual(self.git('status', '--porcelain'), before)
        self.assertEqual(self.git('config', '--local', 'user.email'),
                         '123+author@users.noreply.github.com')

    def test_new_branch_uses_origin_default_head(self):
        row = self.send(checkout='new:job-repair')
        repo = Path(row['checkout_path'])
        self.assertEqual(self.git('branch', '--show-current', cwd=repo), 'job-repair')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=repo), self.git('rev-parse', 'main'))

    def test_resumed_thread_keeps_cwd_and_names_checkout(self):
        self.reg.remember_thread('cx', 'old-thread')
        row = self.send(checkout='repair')
        argv, options = self.codex_calls[0]
        self.assertIn('resume', argv)
        self.assertIn('old-thread', argv)
        self.assertNotIn('-C', argv)
        self.assertNotIn('--skip-git-repo-check', argv)
        self.assertIn(row['checkout_path'], argv[-1])
        self.assertNotEqual(options.get('cwd'), row['checkout_workspace'])
        self.assertEqual(self.reg.entries()['cx']['cwd'], str(self.source))

    def test_git_location_environment_cannot_redirect_checkout_or_executor(self):
        self.env.update({'GIT_DIR': str(self.source / '.git'), 'GIT_WORK_TREE': str(self.source),
                         'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'user.name',
                         'GIT_CONFIG_VALUE_0': 'wrong author'})
        row = self.send(checkout='repair')
        self.assertEqual(self.git('config', '--local', 'user.name', cwd=row['checkout_path']),
                         'Checkout Author')
        executor_env = self.codex_calls[0][1]['env']
        self.assertNotIn('GIT_DIR', executor_env)
        self.assertNotIn('GIT_WORK_TREE', executor_env)
        self.assertNotIn('GIT_CONFIG_COUNT', executor_env)

    def test_non_noreply_canonical_author_refuses(self):
        self.git('config', 'user.email', 'author@example.com')
        with patch.object(dispatch.subprocess, 'run', side_effect=self.execute):
            with self.assertRaisesRegex(desks.DeskError, 'noreply'):
                dispatch.dispatch('cx', 'repair', registry=self.reg, results_path=self.results,
                                  env=self.env, checkout='repair')
        self.assertEqual(self.codex_calls, [])

    def test_workspace_allocation_failure_refuses_without_codex(self):
        with patch.object(dispatch.subprocess, 'run', side_effect=self.execute), \
             patch.object(dispatch.codex_checkout.tempfile, 'mkdtemp', side_effect=PermissionError):
            with self.assertRaisesRegex(desks.DeskError, 'checkout.*workspace'):
                dispatch.dispatch('cx', 'repair', registry=self.reg, results_path=self.results,
                                  env=self.env, checkout='repair')
        self.assertEqual(self.codex_calls, [])

    def test_live_fresh_turn_receives_container_cwd(self):
        self.reg.register('live', 'codex-live', socket='/tmp/fixture.sock',
                          cwd=str(self.source), family='sol', effort='high')
        with patch.object(desks, 'is_live', return_value=True), \
             patch.object(dispatch.codex_wire, 'run_turn', return_value={'status': 'completed'}) as turn:
            row = dispatch.dispatch('live', 'repair', registry=self.reg, results_path=self.results,
                                    env=self.env, checkout='repair', fresh=True)
        self.addCleanup(lambda: shutil.rmtree(row['checkout_workspace'], ignore_errors=True))
        self.assertEqual(turn.call_args.kwargs['cwd'], row['checkout_workspace'])
        self.assertIn(row['checkout_path'], turn.call_args.args[1])

    def test_clone_failure_refuses_without_codex(self):
        for target in ('missing-branch', 'new:bad..name'):
            with self.subTest(target=target), \
                 patch.object(dispatch.subprocess, 'run', side_effect=self.execute):
                with self.assertRaisesRegex(desks.DeskError, 'checkout'):
                    dispatch.dispatch('cx', 'repair', registry=self.reg,
                                      results_path=self.results, env=self.env, checkout=target)
                self.assertEqual(self.codex_calls, [])

    def test_cli_documents_and_forwards_checkout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            dispatch.main(['send', '--help'])
        self.assertIn('--checkout', out.getvalue())
        self.assertIn('new:', out.getvalue())
        with patch.object(dispatch, 'dispatch', return_value={'status': 'completed'}) as send, \
             patch.object(dispatch.seat_gate, 'check', return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dispatch.main(['--registry', str(self.reg.path), 'send', 'cx',
                                           'repair', '--checkout', 'repair']), 0)
        self.assertEqual(send.call_args.kwargs['checkout'], 'repair')


if __name__ == '__main__':
    unittest.main(verbosity=2)
