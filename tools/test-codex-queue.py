"""Exercise the queue CLI with record-layer and executor process fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QueueRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'bin').mkdir()
        address = self.root / 'mcp-server/src/progress-address.v1.json'
        address.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'mcp-server/src/progress-address.v1.json', address)
        rules = self.root / 'tools/room-bridge/job-rules.md'
        rules.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'tools/room-bridge/job-rules.md', rules)
        self.log = self.root / 'calls.jsonl'
        self.job = dict(work_request='WR-000557', slice_ordinal=2, j_number=3,
                        card_id='WR557.S2.J3', executor='codex', model='fixture-model',
                        effort='high', card_title='Queue runner', description='Build the runner.',
                        runner_job_id=None, repository='DoctorCRE/carr-system')

    def run_queue(self, jobs, *, claim_ok=True, status=0, repository='DoctorCRE/carr-system'):
        shutil.copyfile(ROOT / 'bin/codex-queue', self.root / 'bin/codex-queue')
        adapter = f'''#!{sys.executable}
import json, pathlib, sys
with pathlib.Path({str(self.log)!r}).open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'call':
    if sys.argv[2] == 'work-queue':
        print(json.dumps({{'ok': True, 'jobs': {jobs!r}}}))
    else:
        args = json.loads(sys.argv[3])
        print(json.dumps({{'ok': {claim_ok!r}, 'card_id': 'WR557.S' + str(args['slice']) + '.J' + str(args['job']),
                          'runner_job_id': args['runner_job_id']}}))
else:
    sys.exit({status})
'''
        for path in (self.root / 'run.sh', self.root / 'bin/codex-job'):
            path.write_text(adapter)
            path.chmod(0o755)
        result = subprocess.run([sys.executable, str(self.root / 'bin/codex-queue'),
                                 repository], capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def test_next_job_keeps_card_identity_and_records_runner_before_launch(self):
        result, calls = self.run_queue([self.job])
        self.assertEqual(result.returncode, 0, result.stderr)
        claim = json.loads(calls[1][2])
        self.assertEqual(calls[1][:2], ['call', 'dispatch-job'])
        self.assertEqual((claim['work_request'], claim['slice'], claim['job']),
                         ('WR-000557', 2, 3))
        self.assertEqual(claim['repository'], 'DoctorCRE/carr-system')
        launched = calls[2]
        self.assertEqual(launched[0], 'DoctorCRE/carr-system')
        self.assertIn('WR557.S2.J3', launched[1])
        self.assertIn(claim['runner_job_id'], result.stdout)
        self.assertIn('Card: WR557.S2.J3', launched[2])
        self.assertIn('PR body', launched[2])
        self.assertIn('Build the runner.', launched[2])
        self.assertEqual(launched[3:], ['-c', 'model=fixture-model',
                                      '-c', 'model_reasoning_effort=high'])

    def test_every_queued_brief_contains_the_tracked_house_rules(self):
        for repository in ('DoctorCRE/carr-system', 'jbookout/doctorcre-app',
                           'jbookout/software-factory'):
            with self.subTest(repository=repository):
                self.log.unlink(missing_ok=True)
                result, calls = self.run_queue([{**self.job, 'repository': repository}],
                                               repository=repository)
                self.assertEqual(result.returncode, 0, result.stderr)
                brief = calls[2][2]
                self.assertIn('Never run env, printenv or ps eww or print environment variables.', brief)
                rules = (ROOT / 'tools/room-bridge/job-rules.md').read_text().strip()
                self.assertEqual(brief.count(rules), 1)
                self.assertIn('Card: WR557.S2.J3', brief.splitlines())

    def test_job_without_a_work_request_or_slice_never_starts(self):
        for changes in ({'work_request': None}, {'slice_ordinal': 0},
                        {'card_id': 'WR557.S9.J3'}, {'j_number': False}):
            with self.subTest(changes=changes):
                self.log.unlink(missing_ok=True)
                result, calls = self.run_queue([{**self.job, **changes}])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)

    def test_empty_queue_does_not_dispatch_or_launch(self):
        result, calls = self.run_queue([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [['call', 'work-queue', '{}']])

    def test_started_and_other_executor_jobs_are_skipped_in_queue_order(self):
        result, calls = self.run_queue([{**self.job, 'runner_job_id': 'already-started',
                                        'j_number': 1, 'card_id': 'WR557.S2.J1'},
                                       {**self.job, 'executor': 'another-executor'}, self.job,
                                       {**self.job, 'j_number': 4, 'card_id': 'WR557.S2.J4'}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(calls[1][2])['job'], 3)
        self.assertEqual(len(calls), 3)

    def test_failed_claim_never_launches(self):
        result, calls = self.run_queue([self.job], claim_ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)

    def test_executor_failure_reaches_the_caller(self):
        result, calls = self.run_queue([self.job], status=7)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(calls), 3)

    def test_other_repositories_and_unbound_jobs_are_not_started(self):
        result, calls = self.run_queue([{**self.job, 'repository': 'jbookout/doctorcre-app'},
                                       {**self.job, 'repository': None}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_unbound_jobs_are_reported_even_when_a_bound_job_launches(self):
        for tail in ([], [self.job]):
            with self.subTest(launch=bool(tail)):
                self.log.unlink(missing_ok=True)
                result, calls = self.run_queue([
                    {**self.job, 'repository': None, 'card_id': 'WR557.S1.J1'},
                    {**self.job, 'repository': None, 'card_id': 'WR557.S1.J2'},
                    *tail])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Skipped 2 unstarted Codex jobs without a repository', result.stderr)
                self.assertIn('WR557.S1.J1', result.stderr)
                self.assertIn('WR557.S1.J2', result.stderr)
                self.assertEqual(len(calls), 3 if tail else 1)

    def test_repository_choices_follow_the_address_contract(self):
        address_path = self.root / 'mcp-server/src/progress-address.v1.json'
        address = json.loads(address_path.read_text())
        address['repositories'] = {'fixture/contract-repo': 'fixture'}
        address_path.write_text(json.dumps(address))
        result, calls = self.run_queue([{**self.job, 'repository': 'fixture/contract-repo'}],
                                       repository='fixture/contract-repo')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[2][0], 'fixture/contract-repo')

    def test_terminating_the_queue_runner_stops_its_executor(self):
        self.run_queue([self.job])
        pidfile = self.root / 'executor.pid'
        (self.root / 'bin/codex-job').write_text(
            f'#!{sys.executable}\nimport os, pathlib, time\n'
            f'pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
            'time.sleep(30)\n')
        process = subprocess.Popen([sys.executable, str(self.root / 'bin/codex-queue'),
                                    'DoctorCRE/carr-system'], stdout=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(pidfile.exists(), 'executor must start before termination')
            process.terminate()
            process.wait(timeout=5)
            state = subprocess.run(['ps', '-p', pidfile.read_text(), '-o', 'stat='],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith('Z'), 'executor survived termination')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), 9)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main()
