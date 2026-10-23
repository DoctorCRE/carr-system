"""Exercise the launcher with a real Git origin and a stand-in Codex CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env


class CodexJobTests(unittest.TestCase):
    def setUp(self):
        self.env = fixture_env()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'jbookout@users.noreply.github.com')
        self.git('config', 'user.name', 'Test')
        (self.origin / 'hello.txt').write_text('hello\n')
        self.git('add', 'hello.txt')
        message = self.root / 'commit.txt'
        message.write_text('fixture\n')
        self.git('-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-F', str(message))
        cli = self.root / 'codex'
        cli.write_text('''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
clone = pathlib.Path(args[args.index('-C') + 1])
assert (clone / 'hello.txt').read_text() == 'hello\\n'
branch = subprocess.check_output(['git', '-C', str(clone), 'branch', '--show-current'], text=True).strip()
pathlib.Path(os.environ['OBSERVED']).write_text(json.dumps({'args': args, 'clone': str(clone), 'branch': branch, 'stdin': sys.stdin.read()}))
while os.environ.get('CODEX_WAIT'):
    time.sleep(.05)
pathlib.Path(args[args.index('-o') + 1]).write_text('top-level files: 1\\n')
print('Codex progress')
sys.exit(int(os.environ.get('CODEX_EXIT', '0')))
''')
        cli.chmod(0o755)
        self.env = {**self.env, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
                    'OBSERVED': str(self.root / 'observed.json'), 'TYPESAFE_API_KEY': ''}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.origin), *args], check=True,
                              capture_output=True, text=True, env=self.env)

    def run_job(self, *extra):
        return subprocess.run([str(ROOT / 'bin/codex-job'), str(self.origin), 'test/count',
                               'Count the top-level files and stop.', *extra],
                              env=self.env, input='do not append this to the brief',
                              capture_output=True, text=True)

    def test_fresh_branch_final_message_and_cleanup(self):
        result = self.run_job('-c', 'model_reasoning_effort=low')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'top-level files: 1\n')
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertEqual(observed['branch'], 'test/count')
        self.assertEqual(observed['stdin'], '')
        self.assertIn('--dangerously-bypass-hook-trust', observed['args'])
        self.assertIn('model_reasoning_effort=low', observed['args'])
        self.assertTrue(observed['args'][-1].startswith('Skills:'))
        self.assertIn('Count the top-level files and stop.', observed['args'][-1])
        self.assertNotIn('-m', observed['args'])
        self.assertFalse(Path(observed['clone']).parent.exists())

    def test_failed_codex_preserves_its_exit_code_and_final_message_then_cleans_up(self):
        self.env['CODEX_EXIT'] = '7'
        result = self.run_job()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, 'top-level files: 1\n')
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertFalse(Path(observed['clone']).parent.exists())

    def test_termination_cleans_up_the_clone(self):
        self.env['CODEX_WAIT'] = '1'
        with subprocess.Popen([str(ROOT / 'bin/codex-job'), str(self.origin), 'test/count',
                               'Count the files and stop.'], env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as job:
            try:
                deadline = time.monotonic() + 10
                while not (self.root / 'observed.json').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((self.root / 'observed.json').exists())
                job.terminate()
                _, error = job.communicate(timeout=10)
                self.assertEqual(job.returncode, 143, error)
            finally:
                if job.poll() is None:
                    job.kill()
                    job.communicate(timeout=10)
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertFalse(Path(observed['clone']).parent.exists())


if __name__ == '__main__':
    unittest.main()
