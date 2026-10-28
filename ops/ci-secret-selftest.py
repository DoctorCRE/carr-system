#!/usr/bin/env python3
"""Pin secret-class routing, diagnostics and execution evidence with synthetic children."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('ci_fixture', Path(__file__).with_name('ci-selftest.py'))
assert spec is not None and spec.loader is not None
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class SecretTest(unittest.TestCase):
    def setUp(self):
        self.fixture = ci.ci_fixture()
        self.root = self.fixture.__enter__()
        (self.root / 'security').mkdir()
        for name in ('security/test_a.py', 'security/test_b.py',
                     'security/leak_guard.py', 'ops/ci-secret-scan.py'):
            (self.root / name).write_text(
                'import json,os,sys\nfrom pathlib import Path\n'
                'name=Path(__file__).name\n'
                'with open("trace", "a") as f: f.write(json.dumps([name,sys.argv[1:]])+"\\n")\n'
                'print(name+" diagnostic")\n'
                'raise SystemExit(7 if os.environ["FAIL_CHILD"] == name else 0)\n')

        scanner = self.root / 'ops/ci-secret-scan.py'
        scanner.write_text('import sys,runpy\n'
            'if "--redact" in sys.argv:\n'
            f'    print(runpy.run_path({str(ci.REPO / "ops/ci-secret-scan.py")!r})["redact"](sys.stdin.read()), end="")\n'
            '    raise SystemExit(0)\n' + scanner.read_text())

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def run_ci(self, settings=None):
        (self.root / 'trace').unlink(missing_ok=True)
        env = ci.fixture_env()
        env.update(GITHUB_EVENT_NAME='', CARR_CI_RANGE='', CARR_PUSH_RECEIVER='',
                   CARR_CI_PROGRAM_RECORDS='', CARR_SHADOW_ARTIFACT_DIR='', FAIL_CHILD='',
                   RUNNER_TEMP=str(self.root))
        env.update(settings or {})
        result = subprocess.run(['bash', 'ops/ci.sh', '--strict', '--only', 'secret'],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=20)
        output = ci.ANSI.sub('', result.stdout + result.stderr)
        trace = self.root / 'trace'
        return result.returncode, output, [json.loads(s) for s in trace.read_text().splitlines()] if trace.exists() else []

    def test_scope_precedence_and_receiver(self):
        for settings, leak, scan, words in (
            ({}, ['--staged'], [], 'tracked files'),
            ({'GITHUB_EVENT_NAME': 'pull_request'}, ['--ci'], [], 'tracked files'),
            ({'CARR_CI_RANGE': 'base..head'}, ['--range', 'base..head'],
             ['--range', 'base..head'], 'blobs pushed in base..head'),
            ({'GITHUB_EVENT_NAME': 'pull_request', 'CARR_CI_RANGE': 'base..head',
              'CARR_PUSH_RECEIVER': 'synthetic remote'}, ['--ci', '--remote', 'synthetic remote'],
             ['--range', 'base..head'], 'blobs pushed in base..head'),
        ):
            with self.subTest(settings=settings):
                rc, output, trace = self.run_ci(settings)
                self.assertEqual(rc, 0, output)
                self.assertEqual(trace, [['test_a.py', []], ['test_b.py', []],
                                         ['leak_guard.py', leak], ['ci-secret-scan.py', scan]])
                self.assertIn('no shaped credential in ' + words, output)
                self.assertNotIn('diagnostic', output)

    def test_failures_stop_later_checks_and_retain_only_failed_logs(self):
        for child, count, log, message in (
            ('test_a.py', 1, 'leak-guard-tests', 'leak guard qualification failed'),
            ('test_b.py', 2, 'leak-guard-tests', 'leak guard qualification failed'),
            ('leak_guard.py', 3, 'leak-guard', 'secret or client data in introduced Git blobs'),
            ('ci-secret-scan.py', 4, 'secret', 'credential-shaped content found in tracked files'),
        ):
            with self.subTest(child=child):
                rc, output, trace = self.run_ci({'FAIL_CHILD': child})
                self.assertEqual(rc, 1, output)
                self.assertEqual(len(trace), count)
                self.assertIn(message, output)
                self.assertNotIn(child + ' diagnostic', output)
                directory = Path(re.search(r'ci-retained-logs: (.+)', output)[1])
                self.assertEqual((directory / (log + '.log.rc')).read_text(), '7\n')
                self.assertEqual(sorted(p.name for p in directory.glob('*.log')), [log + '.log'])
                records = [s.split('\t')[:3] for s in (directory / 'programs.tsv').read_text().splitlines()]
                self.assertEqual(records[-1], ['secret', log, '7'])
                self.assertEqual(len(records), count)

    def test_failure_diagnostics_omit_values_and_stop_at_twenty_lines(self):
        secret = 'AKIA' + 'X' * 16
        (self.root / 'security/leak_guard.py').write_text(
            f"for i in range(25): print(f'fixtures/{secret}.py:{{i+1}} shaped-rule matched-value-never-print')\n"
            "raise SystemExit(1)\n")
        rc, output, _ = self.run_ci()
        self.assertEqual(rc, 1, output)
        self.assertNotIn(secret, output)
        self.assertIn('.py:1 shaped-rule', output)
        self.assertIn('.py:20 shaped-rule', output)
        self.assertNotIn('.py:21', output)
        self.assertNotIn('matched-value-never-print', output)

    def test_runner_crash_fails_closed(self):
        (self.root / 'ops/ci-secret.py').unlink()
        rc, output, trace = self.run_ci()
        self.assertEqual(rc, 1, output)
        self.assertIn('secret runner exited 2 without a verdict', output)
        self.assertEqual(trace, [])


if __name__ == '__main__':
    unittest.main()
