"""Exercise the job CLI with real Git and local process fixtures."""
import os
import json
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
NOW = 2000000000
WRAPPER = f"""
import os, runpy, sys, tempfile, time
from pathlib import Path
from unittest.mock import Mock
sys.argv.pop(0)
sys.prefix = sys.argv.pop(1)
tempfile.tempdir = os.environ['TMPDIR']
time.time = Mock(return_value={NOW})
import subprocess
real_run = subprocess.run
def run(argv, **kwargs):
    if 'dispatch-job' in argv:
        argv = [sys.executable, os.environ['FAKE_VERB_DOOR'], argv[-1]]
    elif argv[-3:] == ['remote', 'get-url', 'origin']:
        return subprocess.CompletedProcess(argv, 0, stdout='https://github.com/DoctorCRE/carr-system.git')
    return real_run(argv, **kwargs)
subprocess.run = run
sys.path.insert(0, str(Path(sys.argv[0]).parent))
import skill_picker
real_prepare = skill_picker.prepare
skill_picker.prepare = lambda b, c: real_prepare(b, c, home=Path(os.environ['TMPDIR']) / 'home')
runpy.run_path(sys.argv[0], run_name='__main__')
"""
# Subtests rerun on one fixture remote, so each fresh job replaces the branch.
PUSH = """import subprocess as _git, sys as _sys
_clone = _sys.argv[_sys.argv.index('-C') + 1]
_git.run(['git', '-C', _clone, '-c', 'user.name=Fixture', 'commit', '-q', '--allow-empty', '-m', 'job'], check=True)
_git.run(['git', '-C', _clone, 'push', '-qf', 'origin', 'HEAD'], check=True)
"""
CARD = 'Type: feature-backend\nCard: WR557.S2.J2\nTitle: Require readable titles\nDescription: Require readable titles for job cards.\n'
PR_BRIEF = CARD + 'Fix it and deliver through a ready pull request.'


class CodexJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git_env = fixture_env()
        self.door = self.root / 'verb-door.py'
        self.refusal = self.root / 'refusal'
        self.dispatches = self.root / 'dispatches.json'
        self.door.write_text(f"""import json, pathlib, sys
args = json.loads(sys.argv[1])
if pathlib.Path({str(self.refusal)!r}).exists():
    print(json.dumps({{'ok': False, 'error': 'not_registered'}}))
    sys.exit(1)
path = pathlib.Path({str(self.dispatches)!r})
rows = json.loads(path.read_text()) if path.exists() else {{}}
rows[args['idempotency_key']] = args
path.write_text(json.dumps(rows))
print(json.dumps({{'ok': True, 'card_id': 'WR557.S2.J2', 'runner_job_id': args['runner_job_id']}}))
""")
        for name in ('pstack:poteto-mode', 'pstack:principle-prove-it-works',
                     'mattpocock-skills:grill-with-docs', 'mattpocock-skills:tdd',
                     'mattpocock-skills:pr', 'mattpocock-skills:code-review',
                     'file-pr', 'babysit-pr', 'modernize'):
            path = self.root / 'home/.agents/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text(f'---\nname: {name}\ndescription: Fixture skill\n---\nInstructions\n')
        self.processes = self.root / 'processes'
        self.processes.write_text('')
        ps = self.root / 'ps'
        ps.write_text(f'#!{sys.executable}\nimport pathlib\n'
                      f'print(pathlib.Path({str(self.processes)!r}).read_text())\n')
        ps.chmod(0o755)
        seed = self.root / 'seed'
        subprocess.run(['git', 'init', '-q', str(seed)], env=self.git_env, check=True)
        subprocess.run(['git', '-C', str(seed), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture',
                        '--allow-empty'], env=self.git_env, check=True)
        self.origin = self.root / 'origin.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(self.origin)],
                       env=self.git_env, check=True)
        self.start = self.git('rev-parse', 'HEAD')
        self.codex = self.root / 'codex'
        self.codex.write_text(f'#!{sys.executable}\n')
        self.codex.chmod(0o755)
        self.pr_url = self.root / 'pr-url'
        self.gh_args = self.root / 'gh-args'
        gh = self.root / 'gh'
        gh.write_text(f'#!{sys.executable}\nimport pathlib, sys\n'
                      f'pathlib.Path({str(self.gh_args)!r}).write_text(repr(sys.argv[1:]))\n'
                      f'url = pathlib.Path({str(self.pr_url)!r})\n'
                      'print(url.read_text() if url.exists() else "")\n')
        gh.chmod(0o755)
        self.pr_url.write_text('https://github.com/o/r/pull/9')  # a delivering job opened its PR

    def git(self, *args):
        return subprocess.run(['git', '--git-dir', str(self.origin), *args], env=self.git_env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def launch(self, body, repo=None, flags=(), brief=CARD + 'brief', push=True):
        if push:  # a well-behaved job commits and pushes its branch before it exits
            body = PUSH + body
        self.codex.write_text(f'#!{sys.executable}\n' + body)
        job_env = dict(self.git_env, PATH=str(self.codex.parent) + os.pathsep + os.defpath,
                       TMPDIR=str(self.root), FAKE_VERB_DOOR=str(self.door), CODEX_HOME=str(self.root))
        return subprocess.Popen([sys.executable, '-c', WRAPPER, str(ROOT / 'bin/codex-job'),
                                 str(ROOT / '.venv'), str(repo or self.origin), 'test/job', brief, *flags],
                                env=job_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def finish(self, job):
        output, errors = job.communicate(timeout=15)
        retained = list(self.root.glob('codex-job-*'))
        for workspace in retained:
            self.addCleanup(shutil.rmtree, workspace, True)
        return output.decode().splitlines()[-1], errors.decode(), bool(retained)

    def test_missing_free_text_and_unregistered_cards_launch_nothing(self):
        marker = self.root / 'launched'
        for brief, registered, card in (('brief', True, '<missing>'),
                                        ('Card: V2 cleanup', True, 'V2 cleanup'),
                                        ('Card: WR999999.S2.J2\nTitle: Build cards\nDescription: Build cards.', False, 'WR999999.S2.J2'),
                                        ('Card: WR557.S99.J2\nTitle: Build cards\nDescription: Build cards.', False, 'WR557.S99.J2')):
            with self.subTest(card=card):
                if not registered:
                    self.refusal.touch()
                job = self.launch(f'import pathlib; pathlib.Path({str(marker)!r}).touch()',
                                  brief='Type: feature-backend\n' + brief, push=False)
                output, errors = job.communicate(timeout=10)
                self.assertEqual(job.returncode, 1)
                self.assertEqual(output, b'')
                self.assertEqual(errors.decode(), f'codex-job: {card}: job registration refused\n')
                self.assertFalse(marker.exists())
                self.assertFalse(self.dispatches.exists())

    def test_missing_unknown_type_and_missing_skill_launch_nothing(self):
        marker = self.root / 'launched'
        for brief, expected in (('Card: WR557.S2.J2', 'Type:'),
                                ('Card: WR557.S2.J2\nType: unknown', 'Type:'),
                                (CARD, 'modernize')):
            with self.subTest(brief=brief):
                if brief == CARD:
                    (self.root / 'home/.agents/skills/modernize/SKILL.md').unlink()
                job = self.launch(f'import pathlib; pathlib.Path({str(marker)!r}).touch()',
                                  brief=brief, push=False)
                output, errors = job.communicate(timeout=10)
                self.assertEqual(job.returncode, 1)
                self.assertEqual(output, b'')
                self.assertIn(expected, errors.decode())
                if 'Type:' == expected:
                    for name in ('feature-backend', 'feature-ui', 'bug-fix', 'removal-refactor',
                                 'data-migration', 'review', 'research-audit'):
                        self.assertIn(name, errors.decode())
                self.assertFalse(marker.exists())
                self.assertFalse(self.dispatches.exists())
                self.assertEqual(list(self.root.glob('codex-job-*')), [])

    def test_child_receives_table_stages_and_required_skill_bodies(self):
        receipt = self.root / 'brief.txt'
        job = self.launch(f'import pathlib, sys\npathlib.Path({str(receipt)!r}).write_text(sys.argv[-1])')
        _, errors, _ = self.finish(job)
        self.assertEqual(job.returncode, 0, errors)
        brief = receipt.read_text()
        self.assertIn('### Workflow stages', brief)
        self.assertIn('pin_head: true', brief)
        self.assertIn('### modernize', brief)
        self.assertIn('Instructions', brief)
        self.assertNotIn('Skills:', brief)

    def test_relaunch_records_once_at_the_verb_door(self):
        for _ in range(2):
            job = self.launch('')
            _, errors, _ = self.finish(job)
            self.assertEqual(job.returncode, 0, errors)
        rows = list(json.loads(self.dispatches.read_text()).values())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['work_request'], rows[0]['slice'], rows[0]['job']),
                         ('WR-000557', 2, 2))

    def test_job_that_never_pushed_reports_failure_and_keeps_its_work(self):
        job = self.launch('', brief=PR_BRIEF, push=False)
        line, errors, retained = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertEqual(line, f'Delivery: failed missing=push,pr branch=none start={self.start} '
                               f'head={self.start} pr=unchecked')
        self.assertFalse(self.gh_args.exists(), 'no branch, so nothing to look up')
        self.assertIn('Retained checkout:', errors)
        self.assertTrue(retained, 'unpushed work must not be deleted')

    def test_pushing_the_unchanged_start_point_is_not_a_new_commit(self):
        body = "import subprocess, sys\nsubprocess.run(['git', '-C', sys.argv[sys.argv.index('-C') + 1], 'push', '-q', 'origin', 'HEAD'], check=True)\n"
        job = self.launch(body, push=False)
        line, _, _ = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertEqual(line, f'Delivery: failed missing=push,pr branch=none start={self.start} '
                               f'head={self.start} pr=unchecked')

    def test_job_without_a_pr_reports_failure(self):
        self.pr_url.unlink()
        job = self.launch('', brief=PR_BRIEF)
        line, _, retained = self.finish(job)
        remote = self.git('rev-parse', 'refs/heads/test/job')
        self.assertNotEqual(remote, self.start)
        self.assertEqual(job.returncode, 1)
        self.assertEqual(line, f'Delivery: failed missing=pr branch=test/job start={self.start} '
                               f'head={remote} pr=none')
        self.assertTrue(retained)

    def test_job_with_neither_names_both(self):
        job = self.launch('', brief=PR_BRIEF, push=False)
        line, _, _ = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertTrue(line.startswith('Delivery: failed missing=push,pr '), line)

    def test_unanswerable_pr_lookup_counts_as_missing(self):
        (self.root / 'gh').write_text(f'#!{sys.executable}\nimport sys; sys.exit(4)\n')
        job = self.launch('', brief=PR_BRIEF)
        line, _, _ = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertTrue(line.startswith('Delivery: failed missing=pr ') and line.endswith(' pr=unchecked'), line)

    def test_pushed_job_with_its_pr_succeeds(self):
        self.pr_url.write_text('https://github.com/o/r/pull/2\n')
        job = self.launch('', brief=PR_BRIEF)
        line, errors, retained = self.finish(job)
        self.assertEqual(job.returncode, 0, errors)
        self.assertEqual(line, f'Delivery: ok missing=none branch=test/job start={self.start} '
                               f"head={self.git('rev-parse', 'refs/heads/test/job')} "
                               'pr=https://github.com/o/r/pull/2')
        self.assertIn("'--head', 'test/job'", self.gh_args.read_text())
        self.assertIn("'--state', 'all'", self.gh_args.read_text())
        self.assertFalse(retained)

    def test_push_to_an_existing_prs_branch_is_delivery(self):
        self.pr_url.write_text('https://github.com/o/r/pull/3\n')
        body = PUSH.replace("'HEAD']", "'HEAD:job/existing']")
        job = self.launch(body, brief=PR_BRIEF, push=False)
        line, errors, retained = self.finish(job)
        self.assertEqual(job.returncode, 0, errors)
        self.assertEqual(line, f'Delivery: ok missing=none branch=job/existing start={self.start} '
                               f"head={self.git('rev-parse', 'refs/heads/job/existing')} "
                               'pr=https://github.com/o/r/pull/3')
        self.assertIn("'--head', 'job/existing'", self.gh_args.read_text())
        self.assertFalse(retained)

    def test_branch_already_on_the_remote_is_not_a_push(self):
        seed = self.root / 'seed'
        subprocess.run(['git', '-C', str(seed), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'earlier run',
                        '--allow-empty'], env=self.git_env, check=True)
        subprocess.run(['git', '-C', str(seed), 'push', '-q', str(self.origin), 'HEAD:refs/heads/test/job'],
                       env=self.git_env, check=True)
        job = self.launch('', brief=PR_BRIEF, push=False)
        line, _, retained = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertTrue(line.startswith('Delivery: failed missing=push,pr branch=none '), line)
        self.assertTrue(retained)

    def test_commit_made_after_the_push_is_not_delivered(self):
        job = self.launch(PUSH.split('\n_git.run', 1)[0] + "\n_git.run(['git', '-C', _clone, '-c', "
                          "'user.name=Fixture', 'commit', '-q', '--allow-empty', '-m', 'late'], check=True)\n",
                          brief=PR_BRIEF)
        line, _, retained = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertTrue(line.startswith('Delivery: failed missing=push,pr branch=none '), line)
        self.assertTrue(retained, 'the unpushed commit must be kept')

    def test_pr_is_required_even_when_the_brief_never_names_one(self):
        self.pr_url.unlink()
        job = self.launch('')
        line, _, _ = self.finish(job)
        self.assertEqual(job.returncode, 1)
        self.assertTrue(line.startswith('Delivery: failed missing=pr '), line)

    def test_child_failure_keeps_its_own_exit_and_reports_delivery(self):
        job = self.launch('import sys; sys.exit(7)', push=False)
        line, _, _ = self.finish(job)
        self.assertEqual(job.returncode, 7)
        self.assertTrue(line.startswith('Delivery: failed missing=push,pr '), line)

    def test_hung_job_exits_at_configured_deadline(self):
        job = self.launch('import time; time.sleep(60)', flags=('--timeout-seconds', '1'))
        _, errors = job.communicate(timeout=15)
        self.assertEqual(job.returncode, 124, errors.decode())
        self.assertIn(b'Retained checkout:', errors)

    def test_concurrent_jobs_get_separate_checkouts(self):
        git = self.root / 'git'
        git.write_text(f'#!{sys.executable}\nimport pathlib, sys\n'
                       "if sys.argv[1] == 'clone': pathlib.Path(sys.argv[-1]).mkdir()\n")
        git.chmod(0o755)
        jobs = [self.launch('import time; time.sleep(1); raise SystemExit(7)', push=False)
                for _ in range(2)]
        checkouts = {Path(job.communicate(timeout=15)[1].decode().split('Retained checkout: ')[1].strip())
                     for job in jobs}
        self.assertEqual([job.returncode for job in jobs], [7, 7])
        self.assertEqual(len(checkouts), 2)
        self.assertTrue(all(checkout.is_dir() for checkout in checkouts))

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
        old = NOW - 3 * 24 * 60 * 60
        for workspace in (stale, active, sibling, unrelated, target):
            for path in (workspace, workspace / 'checkout'):
                if path.exists():
                    os.utime(path, (old, old))
        os.utime(recent, (NOW, NOW))
        (self.root / 'checkout').mkdir()
        alias = self.root / 'checkout -alias'
        alias.symlink_to(active / 'checkout', target_is_directory=True)
        self.processes.write_text(f'{self.root / "bin space/codex"} exec -C {alias} --model fixture\n')
        job = self.launch('import sys; sys.exit(7)')
        _, errors = job.communicate(timeout=10)
        self.assertEqual(job.returncode, 7, errors.decode())
        self.assertFalse(stale.exists(), 'old idle retained checkout must be pruned')
        self.assertFalse(sibling.exists(), 'a shared path prefix does not mean in use')
        for workspace in (recent, active, unrelated, target, link):
            self.assertTrue(workspace.exists(), f'{workspace.name} must be preserved')

    def test_startup_preserves_retained_workspace_with_recent_activity(self):
        for recent in ('', 'checkout', 'checkout/.git/index',
                       'checkout/.git/logs/HEAD', 'result.md'):
            with self.subTest(recent=recent):
                workspace = self.root / 'codex-job-edited'
                checkout = workspace / 'checkout'
                index = checkout / '.git' / 'index'
                head = checkout / '.git' / 'logs' / 'HEAD'
                head.parent.mkdir(parents=True)
                for path in (index, head, workspace / 'result.md'):
                    path.touch()
                old = NOW - 3 * 24 * 60 * 60
                for path in workspace.rglob('*'):
                    os.utime(path, (old, old))
                os.utime(workspace, (old, old))
                os.utime(workspace / recent, (NOW, NOW))
                job = self.launch('')
                _, errors = job.communicate(timeout=10)
                self.assertEqual(job.returncode, 0, errors.decode())
                self.assertTrue(index.exists(), 'recent activity must preserve an old workspace')
                shutil.rmtree(workspace)

    def test_relative_live_checkout_prevents_unsafe_pruning(self):
        active = self.root / 'codex-job-active'
        (active / 'checkout' / '.git').mkdir(parents=True)
        old = NOW - 3 * 24 * 60 * 60
        for path in (active, active / 'checkout'):
            os.utime(path, (old, old))
        self.processes.write_text('codex exec -C checkout\n')
        job = self.launch('')
        _, errors = job.communicate(timeout=10)
        self.assertEqual(job.returncode, 0, errors.decode())
        self.assertTrue(active.exists(), 'relative live checkout must be preserved')

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

    def test_job_log_is_private_and_ignored(self):
        log = self.root / 'job.log'
        log.write_text('')
        log.chmod(0o644)
        self.codex.write_text(f'#!{sys.executable}\n' + PUSH)
        with log.open('ab') as sink:
            job = subprocess.Popen([sys.executable, '-c', WRAPPER, str(ROOT / 'bin/codex-job'),
                                    str(ROOT / '.venv'), str(self.origin), 'test/job', CARD + 'brief'],
                                   env=dict(self.git_env, PATH=str(self.codex.parent) + os.pathsep + os.defpath,
                                            TMPDIR=str(self.root), FAKE_VERB_DOOR=str(self.door), CODEX_HOME=str(self.root)), stdout=subprocess.PIPE, stderr=sink)
            job.communicate(timeout=10)
        self.assertEqual(job.returncode, 0)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        for path in ('scratchpad/jobs/logs/x.log', 'out/calendar-archive/x.ics'):
            ignored = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', path])
            self.assertEqual(ignored.returncode, 0, path)


if __name__ == '__main__':
    unittest.main()
