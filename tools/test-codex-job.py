"""Exercise the job CLI with real Git and local process fixtures."""
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env
WRAPPER = """
import runpy, sys, types
from unittest.mock import Mock
sys.argv.pop(0)
sys.prefix = sys.argv.pop(1)
sys.modules['skill_picker'] = types.SimpleNamespace(prepare=Mock(side_effect=lambda b, c: (b, [])))
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class CodexJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git_env = fixture_env()
        self.origin = self.root / 'origin'
        subprocess.run(['git', 'init', '-q', str(self.origin)], env=self.git_env, check=True)
        subprocess.run(['git', '-C', str(self.origin), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture',
                        '--allow-empty'], env=self.git_env, check=True)
        self.codex = self.root / 'codex'
        self.codex.write_text(f'#!{sys.executable}\n')
        self.codex.chmod(0o755)

    def launch(self, body, repo=None):
        self.codex.write_text(f'#!{sys.executable}\n' + body)
        job_env = dict(self.git_env, PATH=str(self.codex.parent) + os.pathsep + os.defpath,
                       TMPDIR=str(self.root))
        return subprocess.Popen([sys.executable, '-c', WRAPPER, str(ROOT / 'bin/codex-job'),
                                 str(ROOT / '.venv'), str(repo or self.origin), 'test/job', 'brief'],
                                env=job_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_no_descendant_survives_any_job_exit(self):
        for outcome in (0, 7, signal.SIGTERM, signal.SIGINT):
            with self.subTest(outcome=outcome):
                pidfile = self.root / 'descendant'
                pidfile.unlink(missing_ok=True)
                job = self.launch(f"""
import os, signal, sys, time
leader = os.getpid()
pid = os.fork()
if pid == 0:
    for fd in (1, 2): os.dup2(os.open(os.devnull, os.O_WRONLY), fd)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open({str(pidfile) + '.tmp'!r}, 'w') as ready:
        ready.write(f'{{os.getpid()}} {{leader}}')
    os.rename({str(pidfile) + '.tmp'!r}, {str(pidfile)!r})
    while True: time.sleep(1)
while not os.path.exists({str(pidfile)!r}): time.sleep(.01)
if {int(outcome)} in (0, 7): sys.exit({int(outcome)})
while True: time.sleep(1)
""")
                try:
                    deadline = time.monotonic() + 10
                    while not pidfile.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(pidfile.exists(), 'fixture must start its descendant')
                    pid = int(pidfile.read_text().split()[0])
                    if outcome in (signal.SIGTERM, signal.SIGINT):
                        job.send_signal(outcome)
                    _, errors = job.communicate(timeout=10)
                    self.assertEqual(job.returncode, int(outcome) if outcome in (0, 7)
                                     else 128 + outcome, errors.decode())
                    deadline = time.monotonic() + 2
                    while True:
                        state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                               capture_output=True, text=True).stdout.strip()
                        if not state or state.startswith('Z') or time.monotonic() >= deadline:
                            break
                        time.sleep(.01)
                    self.assertTrue(not state or state.startswith('Z'), 'descendant is still running')
                finally:
                    if job.poll() is None:
                        job.kill()
                    job.communicate(timeout=5)
                    if pidfile.exists():
                        for leaked in pidfile.read_text().split():
                            try:
                                os.kill(int(leaked), signal.SIGKILL)
                            except ProcessLookupError:
                                pass

    def test_startup_prunes_only_old_idle_retained_workspaces(self):
        stale, recent, active, sibling = [self.root / f'codex-job-{name}'
                                        for name in ('stale', 'recent', 'active space', 'active space-extra')]
        for workspace in (stale, recent, active, sibling):
            (workspace / 'checkout' / '.git').mkdir(parents=True)
        unrelated = self.root / 'other-workspace'
        unrelated.mkdir()
        target = self.root / 'outside'
        (target / 'checkout' / '.git').mkdir(parents=True)
        link = self.root / 'codex-job-link'
        link.symlink_to(target, target_is_directory=True)
        old = time.time() - 3 * 24 * 60 * 60
        for workspace in (stale, active, sibling, unrelated, target):
            os.utime(workspace, (old, old))
        ready = self.root / 'ready'
        self.codex = self.root / 'bin space' / 'codex'
        self.codex.parent.mkdir()
        self.codex.write_text(f'#!{sys.executable}\n' +
                              f'import pathlib, time\npathlib.Path({str(ready)!r}).touch()\ntime.sleep(30)\n')
        self.codex.chmod(0o755)
        (self.root / 'checkout').mkdir()
        alias = self.root / 'checkout -alias'
        alias.symlink_to(active / 'checkout', target_is_directory=True)
        live = subprocess.Popen([str(self.codex), 'exec', '-C', str(alias)])
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'live Codex fixture must be running')
            job = self.launch('import sys; sys.exit(7)')
            _, errors = job.communicate(timeout=10)
            self.assertEqual(job.returncode, 7, errors.decode())
            self.assertFalse(stale.exists(), 'old idle retained checkout must be pruned')
            self.assertFalse(sibling.exists(), 'a shared path prefix does not mean in use')
            for workspace in (recent, active, unrelated, target, link):
                self.assertTrue(workspace.exists(), f'{workspace.name} must be preserved')
            self.assertIsNone(live.poll(), 'active Codex process must survive cleanup')
        finally:
            live.terminate()
            live.wait(timeout=5)

    def test_relative_live_checkout_prevents_unsafe_pruning(self):
        active = self.root / 'codex-job-active'
        (active / 'checkout' / '.git').mkdir(parents=True)
        old = time.time() - 3 * 24 * 60 * 60
        os.utime(active, (old, old))
        ready = self.root / 'ready'
        self.codex.write_text(f'#!{sys.executable}\n' +
                              f'import pathlib, time\npathlib.Path({str(ready)!r}).touch()\ntime.sleep(30)\n')
        live = subprocess.Popen([str(self.codex), 'exec', '-C', 'checkout'], cwd=active)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'relative live Codex fixture must be running')
            job = self.launch('')
            _, errors = job.communicate(timeout=10)
            self.assertEqual(job.returncode, 0, errors.decode())
            self.assertTrue(active.exists(), 'relative live checkout must be preserved')
            self.assertIsNone(live.poll())
        finally:
            if live.poll() is None:
                live.terminate()
            live.wait(timeout=5)

    def test_closed_stdout_preserves_success_and_cleanup(self):
        job = self.launch("import pathlib, sys\npathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('x' * 1000000)\n")
        job.stdout.close()
        job.stdout = None
        _, errors = job.communicate(timeout=10)
        self.assertEqual(job.returncode, 0, errors.decode())
        self.assertEqual(list(self.root.glob('codex-job-*')), [])

    def test_only_failed_jobs_keep_a_checkout(self):
        for status, valid_repo in ((0, True), (7, True), (7, False)):
            with self.subTest(status=status, valid_repo=valid_repo):
                if not valid_repo:
                    git = self.root / 'git'
                    git.write_text(f'#!{sys.executable}\nimport pathlib, sys\npathlib.Path(sys.argv[-1]).mkdir()\nsys.exit(7)\n')
                    git.chmod(0o755)
                job = self.launch(f'import sys; sys.exit({status})',
                                  repo=self.origin if valid_repo else self.root / 'absent')
                _, errors = job.communicate(timeout=10)
                self.assertEqual(job.returncode, status, errors.decode())
                parents = list(self.root.glob('codex-job-*'))
                try:
                    self.assertEqual(len(parents), int(status != 0 and valid_repo))
                    if parents:
                        self.assertTrue((parents[0] / 'checkout' / '.git').is_dir())
                        self.assertIn(str(parents[0] / 'checkout').encode(), errors)
                finally:
                    for parent in parents:
                        shutil.rmtree(parent)


if __name__ == '__main__':
    unittest.main()
