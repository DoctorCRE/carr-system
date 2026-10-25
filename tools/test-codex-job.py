"""Run the job lifecycle against local Git and a fake Codex process tree."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import threading
import shutil
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env

ENV = fixture_env()


class CodexJobTests(unittest.TestCase):
    def setUp(self):
        picker = types.ModuleType('skill_picker')
        picker.prepare = lambda brief, _: (brief, [])
        for stub in (patch.dict(sys.modules, {'skill_picker': picker}), patch('os.execv')):
            stub.start()
            self.addCleanup(stub.stop)
        self.job = runpy.run_path(str(ROOT / 'bin/codex-job'))
        self.job['main'].__globals__.update(IDLE_SECONDS=2, POLL_SECONDS=.05)

    def launch(self, mode, cancel=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / 'origin'
            subprocess.run(['git', 'init', '-q', str(origin)], env=ENV, check=True)
            subprocess.run(['git', '-C', str(origin), '-c', 'user.name=Test',
                            '-c', 'user.email=test@example.invalid', 'commit', '-qm',
                            'fixture', '--allow-empty'], env=ENV, check=True)
            fake = root / 'codex'
            fake.write_text(f'#!{sys.executable}\n' + '''import os, pathlib, signal, subprocess, sys, time
clone = pathlib.Path(sys.argv[sys.argv.index('-C') + 1])
mode = sys.argv[-1].splitlines()[0]
(clone / 'brief').write_text(sys.argv[-1])
log = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/01/01/job.jsonl'
log.parent.mkdir(parents=True, exist_ok=True)
if mode == 'bad-json':
    log.write_text('[]')
if mode == 'failure':
    subprocess.run(['git', '-C', str(clone), '-c', 'user.name=Test', 'commit', '-qm', 'unpushed work', '--allow-empty'], check=True)
if mode in ('success', 'failure'):
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('final message\\n')
    sys.exit(7 if mode == 'failure' else 0)
if mode != 'leader-exits':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import pathlib,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path(sys.argv[1]).touch(); time.sleep(30)', str(clone / 'ready')])
while not (clone / 'ready').exists(): time.sleep(.01)
(pathlib.Path(os.environ['CODEX_HOME']).parent / 'pids').write_text(f'{os.getpid()} {child.pid}')
for i in range(80):
    if mode in ('bad-json',):
        print('activity', flush=True)
    if mode == 'unrelated':
        other = log.with_name('other.jsonl')
        with other.open('a') as f: f.write('unrelated activity\\n')
    time.sleep(.05)
child.kill()
child.wait()
''')
            fake.chmod(0o755)
            argv = ['codex-job', str(origin), 'test/job', mode]
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {**ENV, 'PATH': str(root) + os.pathsep + os.defpath,
                                            'CODEX_HOME': str(root / 'home')}, clear=True), \
                    patch.object(sys, 'argv', argv), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                stop = threading.Event()
                if cancel:
                    def interrupt():
                        for _ in range(500):
                            if stop.wait(.01):
                                return
                            if (root / 'pids').exists():
                                os.kill(os.getpid(), cancel)
                                return
                    worker = threading.Thread(target=interrupt)
                    worker.start()
                previous = {s: signal.signal(s, self.job['terminate']) for s in (signal.SIGTERM, signal.SIGINT)}
                try:
                    try:
                        code = self.job['main']()
                    except SystemExit as error:
                        code = error.code
                finally:
                    stop.set()
                    if cancel:
                        worker.join()
                    for s, handler in previous.items():
                        signal.signal(s, handler)
            clone = Path(errors.getvalue().split('Clone: ')[1].splitlines()[0])
            self.addCleanup(shutil.rmtree, clone.parent, True)
            if (root / 'pids').exists():
                pids = list(map(int, (root / 'pids').read_text().split()))
                for pid in pids:
                    self.addCleanup(self.kill, pid)
                for pid in pids:
                    for _ in range(100):
                        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
                        if not state or state.startswith('Z'): break
                        time.sleep(.01)
                    self.assertTrue(not state or state.startswith('Z'), f'process {pid} survived')
            return code, output.getvalue(), errors.getvalue(), clone

    @staticmethod
    def kill(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)

    def test_failure_retains_checkout_and_message(self):
        code, output, errors, clone = self.launch('failure')
        self.assertEqual((code, output), (7, 'final message\n'))
        self.assertTrue(clone.is_dir())
        commits = subprocess.check_output(['git', '-C', str(clone), 'rev-list', '--count', 'HEAD'], env=ENV, text=True)
        self.assertEqual(commits.strip(), '2')
        self.assertIn(f'Retained checkout: {clone}', errors)
        self.assertIn((ROOT / 'tools/room-bridge/brief-footer.md').read_text(), (clone / 'brief').read_text())

    def test_success_cleans_up(self):
        with patch('os.killpg') as killpg:
            code, output, _, clone = self.launch('success')
        killpg.assert_not_called()
        self.assertEqual((code, output), (0, 'final message\n'))
        self.assertFalse(clone.parent.exists())

    def test_idle_ignores_unrelated_sessions_and_kills_group(self):
        for mode in ('stalled', 'unrelated'):
            with self.subTest(mode=mode):
                code, _, errors, clone = self.launch(mode)
                self.assertEqual(code, 124)
                self.assertIn('stalled', errors)
                self.assertTrue(clone.exists())

    def test_non_object_session_does_not_abort_job(self):
        code, _, errors, _ = self.launch('bad-json')
        self.assertEqual(code, 0, errors)

    def test_exit_during_idle_observation_preserves_success(self):
        child = Mock(pid=12345, returncode=0)
        child.poll.side_effect = [None, 0, 0]
        with patch('subprocess.Popen', return_value=child), patch('os.killpg'):
            self.job['run_codex'].__globals__['IDLE_SECONDS'] = 0
            self.assertEqual(self.job['run_codex'](['fixture']), 0)

    def test_failed_clone_removes_empty_workspace(self):
        errors = io.StringIO()
        with patch.object(sys, 'argv', ['codex-job', '/nonexistent/fixture', 'test/job', 'brief']), \
                contextlib.redirect_stderr(errors):
            with self.assertRaises(subprocess.CalledProcessError):
                self.job['main']()
        clone = Path(errors.getvalue().split('Clone: ')[1].splitlines()[0])
        self.addCleanup(shutil.rmtree, clone.parent, True)
        self.assertFalse(clone.parent.exists())
        self.assertNotIn('Retained checkout:', errors.getvalue())

    def test_leader_exit_does_not_leave_grandchild(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                code, _, errors, clone = self.launch('leader-exits', cancel=signum)
                self.assertEqual(code, 128 + signum, errors)
                self.assertTrue(clone.exists())


if __name__ == '__main__':
    unittest.main()
