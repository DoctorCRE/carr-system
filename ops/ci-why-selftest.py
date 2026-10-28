#!/usr/bin/env python3
"""Exercise bin/ci-why against synthetic recorded gh output, with no network."""
# Real failure it prevents (2026-10-28 retro): a ci-why that dumps whole logs or cries wolf on the review gate.
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ci_why', str(ROOT / 'bin' / 'ci-why'))
spec = importlib.util.spec_from_loader('ci_why', loader)
ci_why = importlib.util.module_from_spec(spec)
loader.exec_module(ci_why)

T = '2026-01-01T00:00:00.0000000Z '
SETUP = [T + '##[group]Run actions/setup-node@v4', T + 'Error: optional cache miss (harmless)', T + '##[endgroup]']
PYTEST_LOG = '\n'.join(SETUP + [
    T + '##[group]Run python3 -m pytest',
    T + '##[endgroup]',
    *[T + f'collected item {i}' for i in range(30)],
    T + '\x1b[31mFAILED\x1b[0m tests/test_widget.py::test_sum - AssertionError: 2 != 3',
    T + 'Traceback (most recent call last):',
    T + '  File "widget.py", line 3, in add',
    T + '##[error]Process completed with exit code 1.',
    T + 'Post job cleanup.',
    T + 'cleanup noise that must not print',
])
LONG_LOG = '\n'.join([T + '##[group]Run make test', T + 'Error: first problem']
                     + [T + f'detail {i}' for i in range(200)]
                     + [T + '##[error]Process completed with exit code 2.'])
GATE_LOG = '\n'.join([T + '##[group]Run python3 ops/review-gate.py', T + 'approval required',
                      T + '##[error]Process completed with exit code 1.'])


def job(job_id, name, step, conclusion='failure'):
    return {'databaseId': job_id, 'name': name, 'conclusion': conclusion,
            'steps': [{'name': 'Set up job', 'conclusion': 'success'}, {'name': step, 'conclusion': conclusion}]}


def check(name, bucket, run_id=None, job_id=None, link=None):
    link = link or f'https://github.com/o/r/actions/runs/{run_id}/job/{job_id}'
    return {'name': name, 'bucket': bucket, 'link': link, 'workflow': 'CI'}


class Fake:
    def __init__(self, checks, runs, logs):
        self.checks, self.runs, self.logs, self.calls = checks, runs, logs, []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ('pr', 'view'):
            return json.dumps({'headRefOid': 'abc123def4567890'})
        if args[:2] == ('pr', 'checks'):
            return json.dumps(self.checks)
        if args[:2] == ('run', 'view'):
            return json.dumps({'jobs': self.runs[args[2]]})
        if args[0] == 'api':
            assert '--allow-escape-sequences' in args
            return self.logs[args[-1].split('/')[-2]]
        raise AssertionError(args)


def run(fake, *argv):
    ci_why.gh = fake
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ci_why.main([str(a) for a in argv])
    return out.getvalue()


class CiWhyTests(unittest.TestCase):
    def test_review_gate_only_is_called_out_without_fetching_logs(self):
        fake = Fake([check('evaluate', 'fail', 7, 70), check('review-gate', 'fail', 8, 80), check('unit', 'pass', 9, 90)],
                    {}, {'70': GATE_LOG})
        text = run(fake, 12)
        self.assertIn('2 failing, 0 pending, 3 checks', text)
        self.assertIn('that is the only failure, nothing to fix in code', text)
        self.assertFalse([c for c in fake.calls if c[0] in ('api', 'run')])

    def test_failed_step_window_skips_setup_noise_and_cleanup(self):
        fake = Fake([check('unit', 'fail', 5, 51), check('evaluate', 'fail', 6, 60), check('lint', 'pending', 5, 52)],
                    {'5': [job(51, 'unit', 'Run tests'), job(53, 'other', 'x')]}, {'51': PYTEST_LOG})
        text = run(fake, 12)
        self.assertIn('review gate (evaluate) is waiting for an approval on this head; not a code failure.', text)
        self.assertIn('== unit (job 51)\nfailed step: Run tests', text)
        self.assertIn('FAILED tests/test_widget.py::test_sum - AssertionError', text)
        self.assertIn('##[error]Process completed with exit code 1.', text)
        for absent in ('\x1b', '2026-01-01T', 'harmless', 'cleanup noise', 'collected item 19', '== other'):
            self.assertNotIn(absent, text)
        self.assertIn('collected item 20', text)

    def test_window_is_bounded_and_keeps_the_error_line(self):
        fake = Fake([check('build', 'fail', 1, 11)], {'1': [job(11, 'build', 'Run make test')]}, {'11': LONG_LOG})
        lines = run(fake, 3).splitlines()
        body = lines[lines.index('failed step: Run make test') + 1:]
        self.assertEqual(body[:2], ['##[group]Run make test', 'Error: first problem'])
        self.assertEqual(body[-2:], ['...', '##[error]Process completed with exit code 2.'])
        self.assertEqual(len(body), ci_why.WINDOW)

    def test_total_output_is_capped(self):
        checks = [check(f'job{i}', 'fail', 1, i) for i in range(10)]
        fake = Fake(checks, {'1': [job(i, f'job{i}', 'Run make test') for i in range(10)]},
                    {str(i): LONG_LOG for i in range(10)})
        lines = run(fake, 3).splitlines()
        self.assertEqual(len(lines), ci_why.CAP)
        self.assertEqual(lines[-1], f'... truncated at {ci_why.CAP} lines')

    def test_repo_flag_reaches_every_call_and_external_checks_show_their_link(self):
        fake = Fake([check('build', 'fail', 4, 41), check('Vercel', 'fail', link='https://vercel.example/x')],
                    {'4': [job(41, 'build', 'Run make test')]}, {'41': LONG_LOG})
        text = run(fake, 9, '--repo', 'jbookout/software-factory')
        self.assertIn('Vercel: not a GitHub Actions job, see https://vercel.example/x', text)
        for call in fake.calls:
            self.assertTrue('jbookout/software-factory' in call or 'repos/jbookout/software-factory/actions/jobs/41/logs' in call, call)

    def test_run_mode_explains_every_failed_job(self):
        fake = Fake([], {'77': [job(1, 'unit', 'Run tests'), job(2, 'ok', 'Run', 'success')]}, {'1': PYTEST_LOG})
        text = run(fake, '--run', 77)
        self.assertIn('== unit (job 1)', text)
        self.assertNotIn('== ok', text)


if __name__ == '__main__':
    unittest.main()
