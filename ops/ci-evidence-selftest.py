#!/usr/bin/env python3
# Tests CI evidence validation and required-check aggregation.
import copy
import json
import sys
import importlib.util
import pathlib
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from git_env import fixture_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_evidence', ROOT / 'ops/ci-evidence.py')
assert spec is not None and spec.loader is not None
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class ControllerContractTest(unittest.TestCase):
    def test_controller_source_changes_the_evidence_contract(self):
        read_bytes = pathlib.Path.read_bytes
        before = evidence.contract_digest()
        def changed(path):
            source = read_bytes(path)
            return source + b"\n" if path == ROOT / 'ops/ci.py' else source
        with patch.object(pathlib.Path, 'read_bytes', changed):
            self.assertNotEqual(before, evidence.contract_digest())


class HostedVerdict(unittest.TestCase):
    def test_completed_needs_succeeds_without_contacting_github(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = pathlib.Path(directory) / 'summary'
            needs = {'scope': {'result': 'success', 'outputs': {'full': 'true'}},
                     'unit-coverage': {'result': 'success'}, 'classes': {'result': 'success'},
                     'migration-fixtures': {'result': 'success'}}
            with patch.dict(os.environ, CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
                            GITHUB_REPOSITORY='DoctorCRE/carr-system',
                            GITHUB_RUN_ID='39800451182', GITHUB_RUN_ATTEMPT='1',
                            GITHUB_STEP_SUMMARY=str(summary)), \
                    patch('subprocess.run', side_effect=AssertionError('verdict must not query jobs API')):
                self.assertEqual(evidence.hosted_verdict(), 0)
            self.assertIn('classes: PASSED', summary.read_text())

    def test_pr_fast_lane_must_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            for state, expected in [('success', 0), ('failure', 1), ('cancelled', 1), ('skipped', 1)]:
                with self.subTest(state=state), patch.dict(os.environ,
                    CI_EVENT='pull_request', CI_NEEDS=json.dumps({'fast': {'result': state}, 'gates': {'result': 'success'}, 'secret': {'result': 'success'}, 'unit-coverage': {'result': 'success'}}),
                    CI_DRAFT='false', GITHUB_STEP_SUMMARY=str(pathlib.Path(directory) / 'summary')):
                    self.assertEqual(evidence.hosted_verdict(), expected)

    def test_shard_coverage_must_succeed_on_pr_and_merge_group(self):
        with tempfile.TemporaryDirectory() as directory:
            for event in ('pull_request', 'merge_group'):
                for state in ('failure', 'cancelled', 'skipped'):
                    needs = {'fast': {'result': 'success'}, 'gates': {'result': 'success'}, 'secret': {'result': 'success'},
                             'scope': {'result': 'success', 'outputs': {'full': 'true'}},
                             'classes': {'result': 'success'},
                             'migration-fixtures': {'result': 'success'},
                             'unit-coverage': {'result': state}}
                    with self.subTest(event=event, state=state), patch.dict(os.environ,
                        CI_EVENT=event, CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
                        GITHUB_STEP_SUMMARY=str(pathlib.Path(directory) / 'summary')):
                        self.assertEqual(evidence.hosted_verdict(), 1)

    def test_full_scope_rejects_skipped_fixtures_regardless_of_legacy_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for fixtures, expected in (('false', 1), ('true', 1), ('', 1)):
                needs = {'scope': {'result': 'success', 'outputs': {'full': 'true', 'fixtures': fixtures}},
                         'unit-coverage': {'result': 'success'}, 'classes': {'result': 'success'}, 'migration-fixtures': {'result': 'skipped'}}
                with patch.dict(os.environ, CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
                                GITHUB_STEP_SUMMARY=str(pathlib.Path(directory) / 'summary')):
                    self.assertEqual(evidence.hosted_verdict(), expected)

    def test_failed_scope_cannot_authorize_skipping_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            needs = {'scope': {'result': 'failure', 'outputs': {'full': 'false'}},
                     'unit-coverage': {'result': 'success'}, 'classes': {'result': 'success'}, 'migration-fixtures': {'result': 'skipped'}}
            with patch.dict(os.environ, CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
                            GITHUB_STEP_SUMMARY=str(pathlib.Path(directory) / 'summary')):
                self.assertEqual(evidence.hosted_verdict(), 1)

    def test_cli_fails_closed_for_results_and_allows_only_documented_skips(self):
        cases = [
            ('true', 'success', 'success', False, 0),
            ('false', 'success', 'skipped', False, 0),
            ('true', 'success', 'skipped', True, 1),
            ('', 'success', 'skipped', False, 1),
            ('true', 'success', 'skipped', False, 1),
        ]
        for result in ('failure', 'cancelled', 'skipped', 'neutral', '', None):
            cases.append(('true', result, 'success', False, 1))
            if result != 'skipped':
                cases.append(('false', 'success', result, True, 1))
        with tempfile.TemporaryDirectory() as directory:
            for full, classes, fixtures, draft, expected in cases:
                with self.subTest(full=full, classes=classes, fixtures=fixtures, draft=draft):
                    needs = {'scope': {'result': 'success', 'outputs': {'full': full}},
                             'unit-coverage': {'result': 'success'}, 'classes': {'result': classes}, 'migration-fixtures': {'result': fixtures}}
                    result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'), 'verdict'],
                        env={**os.environ, 'CI_NEEDS': json.dumps(needs), 'CI_DRAFT': str(draft).lower(),
                             'GITHUB_STEP_SUMMARY': str(pathlib.Path(directory) / 'summary')},
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            for malformed in ('{}', 'not-json', 'null', '{"scope": null}'):
                result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'), 'verdict'],
                    env={**os.environ, 'CI_NEEDS': malformed}, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)


class Reuse(unittest.TestCase):
    def setUp(self):
        self.tree = 'a' * 40
        self.run = {'id': 42, 'run_attempt': 2, 'head_sha': 'b' * 40, 'status': 'completed',
                    'conclusion': 'success', 'event': 'merge_group', 'name': 'CI'}
        self.receipt = {'event': 'merge_group', 'draft': False, 'schema': 'carr-ci-evidence/v1', 'run_id': 42, 'run_attempt': 2,
                        'head_sha': 'b' * 40, 'tested_sha': 'c' * 40,
                        'tree_sha': self.tree, 'contract': 'd' * 64,
                        'groups': ['gates', 'migration']}
        self.jobs = [{'name': 'ops/ci.sh --strict', 'conclusion': 'success'},
                     {'name': 'ops/ci.sh --strict --only gates', 'conclusion': 'success'},
                     {'name': 'ops/ci.sh --strict --only migration', 'conclusion': 'success'},
                     {'name': 'unit-coverage', 'conclusion': 'success'},
                     *[{'name': f'unit / MCP unit shard {n} of 4', 'conclusion': 'success'}
                       for n in range(1, 5)]]
    def eligible(self):
        return evidence.can_reuse(self.run, self.jobs, self.receipt,
                                  tree=self.tree, head_tree=self.tree,
                                  tested_tree=self.tree, contract='d' * 64,
                                  groups=['gates', 'migration'])
    def test_exact_tree_success_records_source_run(self):
        self.assertTrue(self.eligible())

    def test_reuse_requires_every_mcp_shard_and_coverage(self):
        proof_jobs = ['unit-coverage', *[f'unit / MCP unit shard {n} of 4' for n in range(1, 5)]]
        self.assertTrue(self.eligible())
        for name in proof_jobs:
            for state in ('failure', 'cancelled', 'skipped', None):
                jobs = copy.deepcopy(self.jobs)
                next(job for job in jobs if job['name'] == name)['conclusion'] = state
                self.assertFalse(evidence.can_reuse(self.run, jobs, self.receipt,
                    tree=self.tree, head_tree=self.tree, tested_tree=self.tree,
                    contract='d'*64, groups=['gates', 'migration']))
            jobs = [job for job in self.jobs if job['name'] != name]
            self.assertFalse(evidence.can_reuse(self.run, jobs, self.receipt,
                tree=self.tree, head_tree=self.tree, tested_tree=self.tree,
                contract='d'*64, groups=['gates', 'migration']))

    def test_fast_pr_receipt_is_never_reusable(self):
        self.run['event'] = self.receipt['event'] = 'pull_request'
        self.assertFalse(self.eligible())

    def test_fast_pr_cannot_create_full_suite_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            event = pathlib.Path(directory) / 'event.json'
            event.write_text(json.dumps({'pull_request': {'draft': False}}))
            output = pathlib.Path(directory) / 'receipt.json'
            result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'),
                                     'record', '--output', str(output)],
                env={**os.environ, 'GITHUB_EVENT_NAME': 'pull_request',
                     'GITHUB_EVENT_PATH': str(event), 'GITHUB_RUN_ID': '42',
                     'GITHUB_RUN_ATTEMPT': '1'}, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_draft_record_refuses_even_when_called_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            event = pathlib.Path(directory) / 'event.json'
            event.write_text(json.dumps({'pull_request': {'draft': True}}))
            output = pathlib.Path(directory) / 'receipt.json'
            result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'),
                                     'record', '--output', str(output)],
                env={**os.environ, 'GITHUB_EVENT_NAME': 'pull_request',
                     'GITHUB_EVENT_PATH': str(event), 'GITHUB_RUN_ID': '42',
                     'GITHUB_RUN_ATTEMPT': '1'}, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_merge_group_receipt_binds_tree_contract_and_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            event = pathlib.Path(directory) / 'event.json'
            event.write_text(json.dumps({'action': 'checks_requested'}))
            output = pathlib.Path(directory) / 'receipt.json'
            subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'),
                            'record', '--output', str(output)],
                env={**os.environ, 'GITHUB_EVENT_NAME': 'merge_group',
                     'GITHUB_EVENT_PATH': str(event), 'GITHUB_RUN_ID': '42',
                     'GITHUB_RUN_ATTEMPT': '2'}, capture_output=True, text=True, check=True, timeout=10)
            receipt = json.loads(output.read_text())
            self.assertEqual((receipt['event'], receipt['run_id'], receipt['run_attempt']),
                             ('merge_group', 42, 2))
            self.assertEqual(receipt['tested_sha'], evidence.git('HEAD'))
            self.assertEqual(receipt['tree_sha'], evidence.git('HEAD^{tree}'))
            self.assertEqual(receipt['contract'], evidence.contract_digest())
            self.assertEqual(receipt['groups'], evidence.class_groups())

    def test_every_evidence_binding_is_required(self):
        for key,value in [('tree_sha','e'*40),('contract','e'*64),('run_id',99),
                          ('run_attempt',1),('head_sha','e'*40),('groups',['gates']),('schema','unknown'),('draft',True),('event','workflow_dispatch')]:
            with self.subTest(key=key):
                previous=copy.deepcopy(self.receipt)
                self.receipt[key]=value
                self.assertFalse(self.eligible())
                self.receipt=previous
    def test_missing_failed_skipped_and_neutral_class_refuse(self):
        for result in ['failure', 'cancelled', 'skipped', 'neutral', None]:
            self.jobs[-1]['conclusion']=result
            self.assertFalse(self.eligible())
        self.jobs.pop()
        self.assertFalse(self.eligible())
    def test_untrusted_incomplete_or_red_runs_refuse(self):
        for key,value in [('event','workflow_dispatch'),('status','in_progress'),
                          ('conclusion','failure'),('name','untrusted')]:
            old=self.run[key];self.run[key]=value
            self.assertFalse(self.eligible());self.run[key]=old
    def test_pr_head_and_tested_tree_must_both_equal_main(self):
        for key in ['head_tree','tested_tree']:
            args=dict(tree=self.tree,head_tree=self.tree,tested_tree=self.tree,
                      contract='d'*64,groups=['gates','migration'])
            args[key]='e'*40
            self.assertFalse(evidence.can_reuse(self.run,self.jobs,self.receipt,**args))

    def test_merge_group_resolver_reuses_only_successful_identical_tree(self):
        self.run['event'] = self.receipt['event'] = 'merge_group'
        self.receipt['tested_sha'] = self.run['head_sha']
        test = self
        class GH:
            head_tree = test.tree
            runs = [test.run]
            def api(self, path):
                if path.endswith('/pulls'):
                    return []
                if path == 'actions/workflows/ci.yml/runs?event=merge_group&per_page=100':
                    return {'workflow_runs': self.runs}
                if path == 'actions/runs/42/jobs?per_page=100':
                    return {'jobs': test.jobs}
                raise AssertionError(path)
            def tree(self, sha):
                test.assertEqual(sha, test.run['head_sha'])
                return self.head_tree
            def receipt(self, run): return test.receipt
        gh = GH()
        def resolve():
            return evidence.resolve(gh, 'f'*40, self.tree, 'd'*64, ['gates', 'migration'])
        self.assertEqual(resolve(), {'reused': True, 'source_run_id': 42,
            'source_run_attempt': 2, 'source_pr': None,
            'source_head_sha': 'b'*40, 'source_tested_sha': 'b'*40})
        gh.head_tree = 'e'*40
        self.assertEqual(resolve(), {'reused': False})
        gh.head_tree = self.tree
        self.receipt['event'] = 'pull_request'
        self.assertEqual(resolve(), {'reused': False})
        self.receipt['event'] = 'merge_group'
        for conclusion in ('failure', 'cancelled', 'skipped'):
            with self.subTest(conclusion=conclusion):
                self.run['conclusion'] = conclusion
                self.assertEqual(resolve(), {'reused': False})
        self.run['conclusion'] = 'success'
        gh.runs = [dict(self.run, id=43, conclusion='failure'), self.run]
        self.assertEqual(resolve(), {'reused': False})

    def test_unavailable_github_evidence_runs_classes_without_disclosing_credentials(self):
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())) / "ci-evidence-selftest"
        root.mkdir()
        try:
            gh = root / 'gh'
            gh.write_text('#!/bin/sh\necho "$GH_TOKEN" >&2\nexit 1\n')
            gh.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       GH_TOKEN='must-not-appear-in-evidence',
                       GITHUB_REPOSITORY='DoctorCRE/carr-system',
                       GITHUB_OUTPUT=str(root / 'output'),
                       GITHUB_STEP_SUMMARY=str(root / 'summary'))
            result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'),
                                     'resolve', '--output', str(root / 'receipt.json')],
                                    env=env, capture_output=True, text=True, check=True)
            self.assertFalse(json.loads((root / 'receipt.json').read_text())['reused'])
            self.assertIn('reused=false', (root / 'output').read_text())
            self.assertNotIn(env['GH_TOKEN'], result.stdout + result.stderr
                             + (root / 'summary').read_text())
        finally:
            target = root.parent / '_to_delete'
            target.mkdir(exist_ok=True)
            root.rename(target / root.name)


class ProgramRecords(unittest.TestCase):
    """WR-000542: per-program exit and elapsed records bound to the tested source."""
    ENV = {'GITHUB_RUN_ID': '41', 'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_JOB': 'classes'}

    def write(self, directory, rows, output, environ=None):
        path = pathlib.Path(directory) / 'rows.tsv'
        path.write_text(''.join('\t'.join(map(str, row)) + '\n' for row in rows))
        with patch.object(evidence, 'git', side_effect=lambda value, root=None: 'tree' if '^' in value else 'sha'), \
                patch.object(evidence, 'contract_digest', return_value='contract'):
            return evidence.program_records(path, output, environ=environ or self.ENV)

    def test_rows_bind_source_attempt_and_keep_first_failure_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / 'programs.json'
            self.write(directory, [('gates', 'gate-a-selftest.py', 1, '10.5', '12.0'),
                                   ('gates', 'gate-a-selftest.py', 0, '12.0', '12.25'),
                                   ('gates', 'gate-b-selftest.py', 124, '10', '130'),
                                   ('gates', 'gate-c-selftest.py', 78, '1,0', '1,5')], output)
            record = json.loads(output.read_text())
            self.assertEqual({k: record[k] for k in ('schema', 'run_id', 'run_attempt', 'job',
                                                     'tested_sha', 'tree_sha', 'contract')},
                             {'schema': 'carr-ci-programs/v1', 'run_id': 41, 'run_attempt': 2,
                              'job': 'classes', 'tested_sha': 'sha', 'tree_sha': 'tree',
                              'contract': 'contract'})
            self.assertEqual([(p['program'], p['exit'], p['elapsed_s']) for p in record['programs']],
                             [('gate-a-selftest.py', 1, 1.5), ('gate-a-selftest.py', 0, 0.25),
                              ('gate-b-selftest.py', 124, 120.0), ('gate-c-selftest.py', 78, 0.5)])

    def test_same_run_extends_and_another_attempt_replaces(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / 'programs.json'
            self.write(directory, [('migration', 'db-gate-a.py', 0, 0, 3)], output)
            self.write(directory, [('migration', 'acceptance-leads', 1, 0, 4)], output)
            self.assertEqual([p['program'] for p in json.loads(output.read_text())['programs']],
                             ['db-gate-a.py', 'acceptance-leads'])
            self.write(directory, [], output, {**self.ENV, 'GITHUB_RUN_ATTEMPT': '3'})
            self.assertEqual(json.loads(output.read_text())['programs'], [])

    def test_ci_sh_records_every_gate_program_with_its_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for rel in ('ops/a-selftest.py', 'ops/b-selftest.py', 'tools/test-c.sh'):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text('')
            (root / 'hooks').mkdir()
            (root / 'hooks/gate-integrity.py').write_text('')
            (root / 'logs').mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True,
                           env=fixture_env())
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'],
                           cwd=root, check=True, env=fixture_env())
            subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'],
                           cwd=root, check=True, env=fixture_env())
            (root / 'timeout.py').write_text(
                "import sys\nsys.exit({'ops/a-selftest.py': 124, 'tools/test-c.sh': 3}.get(sys.argv[-1], 0))\n")
            result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-gates.py'),
                '--logdir', str(root / 'logs'), '--result', str(root / 'result'),
                '--python', sys.executable, '--timeout-helper', str(root / 'timeout.py')], cwd=root,
                capture_output=True, text=True, timeout=60,
                env=fixture_env())
            records = root / 'logs/programs.tsv'
            self.assertTrue(records.is_file(), result.stderr)
            rows = {line.split('\t')[1]: line.split('\t')[2] for line in records.read_text().splitlines()}
            self.assertEqual(rows.get('gate-a-selftest.py'), '124', rows)
            self.assertEqual(rows.get('gate-b-selftest.py'), '0', rows)
            self.assertEqual(rows.get('safety-hooks'), '0', 'run_quiet programs are recorded too')
            # The timeout stops the shell suites, so test-c.sh has no row: a
            # program that did not run is a missing record, never a pass.
            self.assertNotIn('gate-test-c.sh', rows)


if __name__=='__main__':
    unittest.main()
