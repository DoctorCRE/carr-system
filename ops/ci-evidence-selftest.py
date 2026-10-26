#!/usr/bin/env python3
"""Tests CI evidence: hosted verdict, canary reuse and per-program records."""
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_evidence', ROOT / 'ops/ci-evidence.py')
assert spec is not None and spec.loader is not None
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class HostedVerdict(unittest.TestCase):
    def test_completed_needs_succeeds_without_contacting_github(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = pathlib.Path(directory) / 'summary'
            needs = {'scope': {'result': 'success', 'outputs': {'full': 'true'}},
                     'fallow': {'result': 'success'}, 'classes': {'result': 'success'},
                     'migration-fixtures': {'result': 'success'}}
            with patch.dict(os.environ, CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
                            GITHUB_REPOSITORY='DoctorCRE/carr-system',
                            GITHUB_RUN_ID='39800451182', GITHUB_RUN_ATTEMPT='1',
                            GITHUB_STEP_SUMMARY=str(summary)), \
                    patch('subprocess.run', side_effect=AssertionError('verdict must not query jobs API')):
                self.assertEqual(evidence.hosted_verdict(), 0)
            self.assertIn('classes: PASSED', summary.read_text())

    def test_fallow_failure_cannot_pass_the_required_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            for result in ('failure', 'cancelled', 'skipped', 'success'):
                with self.subTest(result=result):
                    needs = {'scope': {'result': 'success', 'outputs': {'full': 'false'}},
                             'classes': {'result': 'success'},
                             'migration-fixtures': {'result': 'skipped'},
                             'fallow': {'result': result}}
                    with patch.dict(os.environ, CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
                                    GITHUB_STEP_SUMMARY=str(pathlib.Path(directory) / 'summary')):
                        self.assertEqual(evidence.hosted_verdict(), 0 if result == 'success' else 1)

    def test_failed_scope_cannot_authorize_skipping_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            needs = {'scope': {'result': 'failure', 'outputs': {'full': 'false'}},
                     'fallow': {'result': 'success'}, 'classes': {'result': 'success'}, 'migration-fixtures': {'result': 'skipped'}}
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
                             'fallow': {'result': 'success'}, 'classes': {'result': classes}, 'migration-fixtures': {'result': fixtures}}
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
                    'conclusion': 'success', 'event': 'pull_request', 'name': 'CI'}
        self.receipt = {'event': 'pull_request', 'draft': False, 'schema': 'carr-ci-evidence/v1', 'run_id': 42, 'run_attempt': 2,
                        'head_sha': 'b' * 40, 'tested_sha': 'c' * 40,
                        'tree_sha': self.tree, 'contract': 'd' * 64,
                        'groups': ['gates', 'migration']}
        self.jobs = [{'name': 'ops/ci.sh --strict', 'conclusion': 'success'},
                     {'name': 'ops/ci.sh --strict --only gates', 'conclusion': 'success'},
                     {'name': 'ops/ci.sh --strict --only migration', 'conclusion': 'success'}]
    def eligible(self):
        return evidence.can_reuse(self.run, self.jobs, self.receipt,
                                  tree=self.tree, head_tree=self.tree,
                                  tested_tree=self.tree, contract='d' * 64,
                                  groups=['gates', 'migration'])
    def test_exact_tree_success_records_source_run(self):
        self.assertTrue(self.eligible())

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

    def test_ready_receipt_binds_event_head_tree_contract_and_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            event = pathlib.Path(directory) / 'event.json'
            event.write_text(json.dumps({'action': 'ready_for_review', 'pull_request': {
                'draft': False, 'head': {'sha': 'b' * 40}}}))
            output = pathlib.Path(directory) / 'receipt.json'
            subprocess.run([sys.executable, str(ROOT / 'ops/ci-evidence.py'),
                            'record', '--output', str(output)],
                env={**os.environ, 'GITHUB_EVENT_NAME': 'pull_request',
                     'GITHUB_EVENT_PATH': str(event), 'GITHUB_RUN_ID': '42',
                     'GITHUB_RUN_ATTEMPT': '2'}, capture_output=True, text=True, check=True, timeout=10)
            receipt = json.loads(output.read_text())
            self.assertEqual((receipt['event'], receipt['action'], receipt['draft']),
                             ('pull_request', 'ready_for_review', False))
            self.assertEqual((receipt['run_id'], receipt['run_attempt'], receipt['head_sha']),
                             (42, 2, 'b' * 40))
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
    def test_non_pr_incomplete_or_red_runs_refuse(self):
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

    def test_resolver_records_source_and_refuses_newer_red_run(self):
        test = self
        class GH:
            runs = [test.run]
            def api(self, path):
                if path.endswith('/pulls'):
                    return [{'merged_at':'2026-10-05', 'number': 11,
                             'base':{'ref':'main'},'head':{'sha':'b'*40}}]
                if '/runs?' in path:
                    return {'workflow_runs': self.runs}
                return {'jobs': test.jobs}
            def tree(self, sha): return test.tree
            def receipt(self, run): return test.receipt
        gh = GH()
        result = evidence.resolve(gh, 'f'*40, self.tree, 'd'*64, ['gates','migration'])
        self.assertEqual(result['source_run_id'], 42)
        for conclusion in ('failure', 'cancelled', 'skipped'):
            gh.runs = [self.run, dict(self.run, id=43, conclusion=conclusion)]
            self.assertEqual(evidence.resolve(gh,'f'*40,self.tree,'d'*64,['gates','migration']), {'reused':False})

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
            (root / 'timeout.py').write_text(
                "import sys\nsys.exit({'ops/a-selftest.py': 124, 'tools/test-c.sh': 3}.get(sys.argv[-1], 0))\n")
            source = (ROOT / 'ops/ci.sh').read_text()
            body = source[source.index('\nrecord_program() {'):source.index('\nLOGDIR=')]
            body += source[source.index('\ncheck_gates() {'):source.index('\ncheck_secret() {')]
            prefix = '\n'.join(('set -uo pipefail', f'PY={sys.executable}', f'LOGDIR={root / "logs"}',
                                'CI_SELFTEST_TIMEOUT_SECONDS=1', f'CI_TIMEOUT_HELPER={root / "timeout.py"}',
                                'GATE_SHARD=1', 'GATE_SHARDS=1', '_class_name=gates',
                                'tree_fingerprint() { echo static; }', 'ok() { :; }', 'bad() { :; }',
                                'fail_tail() { :; }'))
            result = subprocess.run(['bash', '-c', prefix + body + '\ncheck_gates'], cwd=root,
                                    capture_output=True, text=True, timeout=60,
                                    env={'PATH': os.environ['PATH']})
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
