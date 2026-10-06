#!/usr/bin/env python3
"""Prove same-tree measurement and one fix loop per suite."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
COLLECTOR = REPO / 'ops/ci-flakes.py'


class FlakeTests(unittest.TestCase):
    def test_measure_requires_later_pass_of_same_test_tree_and_workflow(self):
        observations = [
            {'test': 'ops/a-selftest.py', 'tree': 'aaa', 'workflow': 'CI', 'result': 'fail', 'at': 1, 'url': 'failure'},
            {'test': 'ops/a-selftest.py', 'tree': 'bbb', 'workflow': 'CI', 'result': 'pass', 'at': 2, 'url': 'wrong tree'},
            {'test': 'ops/a-selftest.py', 'tree': 'aaa', 'workflow': 'other', 'result': 'pass', 'at': 3, 'url': 'wrong workflow'},
            {'test': 'ops/b-selftest.py', 'tree': 'aaa', 'workflow': 'CI', 'result': 'pass', 'at': 4, 'url': 'wrong test'},
            {'test': 'ops/a-selftest.py', 'tree': 'aaa', 'workflow': 'CI', 'result': 'pass', 'at': 5, 'url': 'pass'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'observations.json'
            fixture.write_text(json.dumps(observations))
            result = subprocess.run([sys.executable, str(COLLECTOR), 'analyze', str(fixture)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['test'], 'ops/a-selftest.py')
            self.assertEqual(rows[0]['pass_url'], 'pass')

    def test_proposals_reuse_one_fix_loop_across_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / 'gh'
            store = root / 'issues.json'
            store.write_text('[]')
            gh.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\np=Path(os.environ['ISSUE_STORE']); rows=json.loads(p.read_text()); a=sys.argv[1:]\nif a[0]=='api' and '-X' not in a: print(json.dumps(rows))\nelif a[0]=='api' and a[a.index('-X')+1]=='POST':\n payload=json.loads(sys.stdin.read()); rows.append(dict(payload,number=len(rows)+1,html_url='https://github.com/jbookout/carr-system/issues/'+str(len(rows)+1),state='open')); p.write_text(json.dumps(rows)); print(json.dumps(rows[-1]))\nelse: raise SystemExit('unexpected gh arguments')\n")
            gh.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'ISSUE_STORE': str(store)}
            proposals = root / 'proposals.json'
            for tree in ('aaa', 'bbb'):
                proposals.write_text(json.dumps([{'repo': 'jbookout/carr-system', 'test': 'ops/a-selftest.py',
                    'tree': tree, 'workflow': 'CI', 'fail_url': 'https://github.com/jbookout/carr-system/actions/runs/1',
                    'pass_url': 'https://github.com/jbookout/carr-system/actions/runs/2'}]))
                result = subprocess.run([sys.executable, str(COLLECTOR), 'propose', '--repo', 'jbookout/carr-system',
                    '--input', str(proposals)], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(store.read_text())), 1)
            self.assertIn('quarantine', json.loads(store.read_text())[0]['body'])

    def test_job_logs_bind_checkout_and_only_successful_class_passes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_flakes', COLLECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a' * 40
        checkout = f'2026-10-05T00:00:00Z [command]/usr/bin/git log -1 --format=%H\n2026-10-05T00:00:00Z {sha}\n'
        run = {'id': 1, 'name': 'CI', 'head_sha': 'b' * 40, 'created_at': '2026-10-05T00:00:00Z'}
        rows = module.observations_from_logs('jbookout/carr-system', run, 1,
            {'gates.txt': checkout + 'FAIL gates selftest suites failed: a-selftest.py\n'}, ['ops/a-selftest.py'])
        self.assertEqual(rows[0]['source_sha'], sha)
        self.assertEqual(rows[0]['result'], 'fail')
        self.assertEqual(module.observations_from_logs('jbookout/carr-system', run, 2,
            {'gates.txt': checkout + 'FAIL gates dependency missing\n'}, ['ops/a-selftest.py']), [])
        passed = module.observations_from_logs('jbookout/carr-system', run, 2,
            {'gates.txt': checkout + 'OK gates 500 selftest suites + baseline integrity\n'}, ['ops/a-selftest.py'])
        self.assertEqual(passed[0]['result'], 'pass')

    def test_candidate_receipts_are_bound_and_duplicates_collapse(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_flakes', COLLECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a' * 40
        checkout = f'2026-10-05T00:00:00Z git log -1 --format=%H\n2026-10-05T00:00:00Z {sha}\n'
        receipt = {'version': 1, 'test': 'ops/a-selftest.py', 'candidate': True, 'sha': sha,
            'tree': 'b' * 40, 'first_exit': 1, 'rerun_exit': 0,
            'fingerprint': module.hashlib.sha256(b'').hexdigest()}
        event = {'id': 1, 'name': 'CI', 'head_repository': {'full_name': 'jbookout/carr-system'}}
        line = '2026-10-05T00:00:00Z CARR_FLAKE_RESULT ' + json.dumps(receipt) + '\n'
        rows = module.candidate_rows('jbookout/carr-system', event, 1, {'job.txt': checkout + line + line})
        self.assertEqual(len(rows), 1)
        receipt['sha'] = 'c' * 40
        wrong = 'CARR_FLAKE_RESULT ' + json.dumps(receipt)
        self.assertEqual(module.candidate_rows('jbookout/carr-system', event, 1, {'job.txt': checkout + wrong}), [])
        event['head_repository']['full_name'] = 'someone/fork'
        self.assertEqual(module.candidate_rows('jbookout/carr-system', event, 1, {'job.txt': checkout + line}), [])

    def test_mixed_node_outcomes_belong_only_to_the_named_suite(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_flakes_mixed', COLLECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a' * 40
        text = f'git log -1 --format=%H\n{sha}\n2026-10-05T00:00:01Z ok 1 a.test.mjs\n2026-10-05T00:00:02Z not ok 2 b.test.mjs\n'
        tests = ['mcp-server/test/a.test.mjs', 'mcp-server/test/b.test.mjs']
        run = {'id': 1, 'name': 'CI', 'created_at': '2026-10-05T00:00:00Z'}
        rows = module.observations_from_logs('jbookout/carr-system', run, 1, {'job.txt': text}, tests)
        self.assertEqual({r['test']: r['result'] for r in rows}, dict(zip(tests, ['pass', 'fail'])))
        self.assertEqual(module.failed_tests({'job.txt': text}, tests), [tests[1]])
        later = module.observations_from_logs('jbookout/carr-system', run, 2,
            {'job.txt': f'git log -1 --format=%H\n{sha}\nOK unit all passed\n'}, tests)
        self.assertEqual([r['test'] for r in module.analyze(rows + later)], [tests[1]])


if __name__ == '__main__':
    unittest.main()
