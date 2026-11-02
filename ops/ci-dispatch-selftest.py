#!/usr/bin/env python3
"""Exercise workflow recovery through the CLI with synthetic GitHub replies.

ci: selftest
"""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
URL = 'https://github.com/DoctorCRE/carr-system/actions/runs/123'


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.log = self.home / 'gh.jsonl'
        gh = self.home / 'gh'
        gh.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(Path.home() / 'gh.jsonl', 'a') as f:
    f.write(json.dumps(args) + '\\n')
if '--method' in args:
    if os.environ.get('FAIL_DISPATCH'):
        sys.exit(1)
    if 'DISPATCH_RESPONSE' in os.environ:
        print(os.environ['DISPATCH_RESPONSE'])
        sys.exit(0)
    print(json.dumps({'workflow_run_id':123, 'html_url':''' + repr(URL) + '''}))
elif '/commits/main' in args[1]:
    print(json.dumps({'sha': 'a'*40}))
elif 'status=success' not in args[1]:
    print(json.dumps({'workflow_runs': [{'head_sha':'a'*40, 'conclusion':None}] if os.environ.get('ALREADY_RUNNING') else []}))
else:
    print(json.dumps({'workflow_runs': [] if os.environ.get('NO_GREEN') else [{
        'head_sha': 'a'*40, 'conclusion':'success', 'event':'merge_group',
        'head_repository':{'full_name':'DoctorCRE/carr-system'},
        'html_url':'https://github.com/DoctorCRE/carr-system/actions/runs/100'}]}))
''')
        gh.chmod(0o755)
        self.environment = {**os.environ, 'HOME': str(self.home),
                            'PATH': str(self.home) + os.pathsep + os.environ['PATH']}

    def cli(self, *args):
        return subprocess.run(['./run.sh', 'ci', 'dispatch', *args], cwd=ROOT,
                              env=self.environment, text=True, capture_output=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_dispatch_records_spend_and_refuses_exhausted_candidate(self):
        first = self.cli('main-canary.yml')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(URL, first.stdout)
        ledger = list(self.home.rglob('ci-dispatch.jsonl'))
        self.assertEqual(len(ledger), 1)
        rows = [json.loads(line) for line in ledger[0].read_text().splitlines()]
        self.assertEqual(rows[0]['status'], 'reserved')
        self.assertEqual(rows[-1]['status'], 'dispatched')
        self.assertEqual(rows[-1]['admission']['gate'], 'github-actions-remote-ci')
        self.assertEqual(rows[-1]['candidate_sha'], SHA)
        second = self.cli('main-canary.yml')
        self.assertEqual(second.returncode, 77)
        self.assertIn('budget exhausted', second.stderr)
        self.assertEqual(sum('--method' in call for call in self.calls()), 1)

    def test_dry_run_does_not_dispatch_or_consume_budget(self):
        for _ in range(2):
            result = self.cli('main-canary.yml', '--ref', 'main', '--dry-run')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'dry_run')
        self.assertFalse(any('--method' in call for call in self.calls()))
        self.assertEqual(self.cli('main-canary.yml').returncode, 0)

    def test_invalid_workflow_and_ref_never_reach_github(self):
        for args in [('ci.yml',), ('deploy.yml', '--ref', 'feature'),
                     ('../deploy.yml',), ('main-canary.yml', '--ref', 'refs/heads/main')]:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 77)
                self.assertIn('refused', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_successful_evidence_refuses_before_spend(self):
        self.environment['NO_GREEN'] = '1'
        result = self.cli('deploy.yml')
        self.assertEqual(result.returncode, 77)
        self.assertIn('must have succeeded', result.stderr)
        self.assertFalse(any('--method' in call for call in self.calls()))

    def test_existing_pipeline_refuses_duplicate_spend(self):
        self.environment['ALREADY_RUNNING'] = '1'
        result = self.cli('main-canary.yml')
        self.assertEqual(result.returncode, 77)
        self.assertIn('already running or successful', result.stderr)
        self.assertFalse(any('--method' in call for call in self.calls()))

    def test_uncertain_dispatch_retains_reservation(self):
        self.environment['FAIL_DISPATCH'] = '1'
        result = self.cli('main-canary.yml')
        self.assertEqual(result.returncode, 77)
        self.assertIn('dispatch outcome unknown', result.stderr)
        self.assertIn(SHA, result.stderr)
        self.assertNotIn('refused', result.stderr)
        self.environment.pop('FAIL_DISPATCH')
        result = self.cli('main-canary.yml')
        self.assertEqual(result.returncode, 77)
        self.assertIn('budget exhausted', result.stderr)
        self.assertEqual(sum('--method' in call for call in self.calls()), 1)

    def test_unexpected_dispatch_response_records_unknown_outcome(self):
        for response in ('[]', '{"html_url": null}', 'null', '{"html_url": 123}', '{}', ''):
            with self.subTest(response=response):
                ledger = self.home / '.local/state/carr/ci-dispatch.jsonl'
                ledger.unlink(missing_ok=True)
                self.environment['DISPATCH_RESPONSE'] = response
                result = self.cli('main-canary.yml')
                self.assertEqual(result.returncode, 77, result.stderr)
                self.assertIn('dispatch outcome unknown', result.stderr)
                self.assertNotIn('refused', result.stderr)
                rows = [json.loads(line) for line in ledger.read_text().splitlines()]
                self.assertEqual([row['status'] for row in rows], ['reserved', 'dispatch_unconfirmed'])

    def test_parallel_callers_share_one_reservation(self):
        argv = ['./run.sh', 'ci', 'dispatch', 'main-canary.yml']
        processes = [subprocess.Popen(argv, cwd=ROOT, env=self.environment,
                                      text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(2)]
        for process in processes:
            process.communicate(timeout=15)
        self.assertEqual(sorted(p.returncode for p in processes), [0, 77])
        self.assertEqual(sum('--method' in call for call in self.calls()), 1)

    def test_meter_pause_refuses_without_dispatch(self):
        spec = importlib.util.spec_from_file_location('ci_dispatch', ROOT / 'ops/ci-dispatch.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        policy = json.loads((ROOT / 'ops/config/platform-metering.v1.json').read_text())
        policy['temporary_controls']['github_actions_pause']['repository_actions_enabled'] = False
        def api(path):
            if path.endswith('/commits/main'):
                return {'sha': SHA}
            return {'workflow_runs': [{'head_sha': SHA, 'conclusion': 'success',
                    'event': 'merge_group', 'head_repository': {'full_name': 'DoctorCRE/carr-system'},
                    'html_url': URL}]}
        with self.assertRaisesRegex(module.MeteringRefusal, 'intentionally disabled'):
            module.dispatch('main-canary.yml', 'main', False, ledger=self.home / 'ledger',
                            policy=policy, api=api)
        self.assertFalse((self.home / 'ledger').exists())

    def test_guard_allows_wrapper_and_blocks_raw_dispatch_and_watch(self):
        for command, allowed in [('./run.sh ci dispatch main-canary.yml --ref main', True),
                                 ('./run.sh ci dispatch deploy.yml --dry-run', True),
                                 ('gh workflow run main-canary.yml --ref main', False),
                                 ('gh api repos/o/r/actions/workflows/deploy.yml/dispatches -X POST -f ref=main', False),
                                 ('gh api --method POST repos/o/r/actions/workflows/deploy.yml/dispatches -f ref=main', False),
                                 ('gh api -X POST /repos/o/r/dispatches -f event_type=x', False),
                                 ('gh api repos/o/r/dispatches -F event_type=x', False),
                                 ('gh api repos/o/r/dispatches --input /tmp/payload.json', False),
                                 ('gh api repos/o/r/dispatches --method GET', True),
                                 ('gh api repos/o/r/actions/runs', True),
                                 ('gh run watch 123', False)]:
            payload = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': str(ROOT)}
            result = subprocess.run(['python3', str(ROOT / 'hooks/guard-unattended.py')],
                                    input=json.dumps(payload), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if allowed else 2, result.stderr)



if __name__ == '__main__':
    unittest.main()
