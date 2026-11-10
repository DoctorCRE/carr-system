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
sys.path.insert(0, str(ROOT))
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

    def pr_needs(self, classes):
        from ci_scope import required_jobs
        jobs = ('lint', 'fast', 'gates', 'secret', 'unit', 'unit-coverage',
                'launchd-python39', 'scope', 'classes', 'migration-fixtures')
        required = required_jobs(classes)
        return {'select': {'result': 'success', 'outputs': {'classes': json.dumps(classes)}},
                **{job: {'result': 'success' if job in required else 'skipped'} for job in jobs}}

    def test_pr_selection_requires_every_selected_job(self):
        from ci import CLASS_DESCRIPTIONS
        from ci_scope import PRODUCT_CLASSES, required_jobs
        with tempfile.TemporaryDirectory() as directory:
            for classes in (['lint'], sorted(PRODUCT_CLASSES), sorted(CLASS_DESCRIPTIONS)):
                needs = self.pr_needs(classes)
                for job in [None, *required_jobs(classes)]:
                    for state in (['success'] if job is None else ['failure', 'cancelled', 'skipped']):
                        changed = copy.deepcopy(needs)
                        if job:
                            changed[job]['result'] = state
                        with self.subTest(classes=classes, job=job, state=state), patch.dict(os.environ,
                            CI_EVENT='pull_request', CI_NEEDS=json.dumps(changed), CI_DRAFT='false',
                            GITHUB_STEP_SUMMARY=str(pathlib.Path(directory) / 'summary')):
                            self.assertEqual(evidence.hosted_verdict(), 0 if job is None else 1)

    def test_pr_selector_failure_and_unexpected_skip_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            for bad in ('failed-selector', 'unknown-class', 'missing-lint', 'unexpected-job'):
                needs = self.pr_needs(['lint'])
                if bad == 'failed-selector':
                    needs['select']['result'] = 'failure'
                elif bad == 'unexpected-job':
                    needs['gates']['result'] = 'success'
                else:
                    needs['select']['outputs']['classes'] = json.dumps(['unknown'] if bad == 'unknown-class' else ['unit'])
                with self.subTest(bad=bad), patch.dict(os.environ, CI_EVENT='pull_request',
                    CI_NEEDS=json.dumps(needs), CI_DRAFT='false',
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
        self.shard_count = 8
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
                     {'name': 'unit / plan', 'conclusion': 'success'},
                     {'name': 'unit-coverage', 'conclusion': 'success'},
                     *[{'name': f'unit / MCP unit shard {n} of {self.shard_count}', 'conclusion': 'success'}
                       for n in range(1, self.shard_count + 1)]]
    def eligible(self):
        return evidence.can_reuse(self.run, self.jobs, self.receipt,
                                  tree=self.tree, head_tree=self.tree,
                                  tested_tree=self.tree, contract='d' * 64,
                                  groups=['gates', 'migration'], shard_count=self.shard_count)
    def test_exact_tree_success_records_source_run(self):
        self.assertTrue(self.eligible())

    def test_reuse_requires_every_mcp_shard_and_coverage(self):
        proof_jobs = ['unit / plan', 'unit-coverage', *[f'unit / MCP unit shard {n} of {self.shard_count}' for n in range(1, self.shard_count + 1)]]
        self.assertTrue(self.eligible())
        for name in proof_jobs:
            for state in ('failure', 'cancelled', 'skipped', None):
                jobs = copy.deepcopy(self.jobs)
                next(job for job in jobs if job['name'] == name)['conclusion'] = state
                self.assertFalse(evidence.can_reuse(self.run, jobs, self.receipt,
                    tree=self.tree, head_tree=self.tree, tested_tree=self.tree,
                    contract='d'*64, groups=['gates', 'migration'], shard_count=self.shard_count))
            jobs = [job for job in self.jobs if job['name'] != name]
            self.assertFalse(evidence.can_reuse(self.run, jobs, self.receipt,
                tree=self.tree, head_tree=self.tree, tested_tree=self.tree,
                contract='d'*64, groups=['gates', 'migration'], shard_count=self.shard_count))

    def test_reuse_refuses_empty_or_over_cap_shard_counts(self):
        for count in (0, 17):
            jobs = [job for job in self.jobs if not job['name'].startswith('unit / MCP unit shard')]
            jobs += [{'name': f'unit / MCP unit shard {index} of {count}', 'conclusion': 'success'}
                     for index in range(1, count + 1)]
            self.assertFalse(evidence.can_reuse(self.run, jobs, self.receipt,
                tree=self.tree, head_tree=self.tree, tested_tree=self.tree,
                contract='d'*64, groups=['gates', 'migration'], shard_count=count))

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
                      contract='d'*64,groups=['gates','migration'], shard_count=self.shard_count)
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
            return evidence.resolve(gh, 'f'*40, self.tree, 'd'*64, ['gates', 'migration'], self.shard_count)
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


class ConversionProof(unittest.TestCase):
    fixtures = ROOT / 'ops/fixtures/conversion-proof'

    def setUp(self):
        self.snapshot = json.loads((self.fixtures / 'github.json').read_text())
        self.release = json.loads((self.fixtures / 'release.json').read_text())
        self.health = json.loads((self.fixtures / 'health.json').read_text())
        self.commit = self.snapshot['data']['repository']['pullRequest']['mergeCommit']
        self.merge = self.commit['oid']
        self.workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.commands = []

    def run_proof(self, ancestry=0, timeout=2, cli=False):
        from lib.release_reader import Reply
        def command(argv, **kwargs):
            self.commands.append(argv)
            self.assertGreater(kwargs['timeout'], 0)
            if argv[0] == 'gh':
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.snapshot), '')
            if 'merge-base' in argv:
                return subprocess.CompletedProcess(argv, ancestry, '', '')
            if '--is-shallow-repository' in argv:
                return subprocess.CompletedProcess(argv, 0, 'false', '')
            if 'show' in argv:
                return subprocess.CompletedProcess(argv, 0, self.workflow, '')
            return subprocess.CompletedProcess(argv, 0, self.merge, '')
        def http(url, **kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            payload = self.release if url.endswith('/release') else self.health
            return Reply(200, {}, json.dumps(payload).encode())
        with patch.object(evidence.subprocess, 'run', side_effect=command), \
                patch('lib.release_reader.read', side_effect=http):
            if cli:
                with patch.object(sys, 'argv', ['ci-evidence.py', 'prove-conversion',
                                                '--pr', '3114', '--timeout', str(timeout)]):
                    return evidence.main()
            return evidence.prove_conversion(pr=3114, timeout=timeout)

    def test_recorded_equal_and_descendant_release(self):
        self.assertEqual(self.run_proof(), 0)
        self.release['git_sha']['value'] = 'a' * 40
        self.assertEqual(self.run_proof(), 0)
        self.assertEqual(len([x for x in self.commands if x[0] == 'gh']), 2)

    def test_older_unrelated_unknown_releases_are_insufficient(self):
        for result in (1, 128):
            with self.subTest(result=result):
                self.assertEqual(self.run_proof(ancestry=result), 78)
        for value in (None, {}, 'unknown', 'abc', {'value': 'a' * 40}):
            with self.subTest(value=value):
                self.release['git_sha']['value'] = value
                self.assertEqual(self.run_proof(), 78)

    def test_red_ci_and_health_only_fail_containing_release(self):
        suites = self.commit['checkSuites']['nodes']
        suite = next(s for s in suites if s['workflowRun'])
        strict = next(j for j in suite['checkRuns']['nodes'] if j['name'] == 'ops/ci.sh --strict')
        strict['conclusion'] = 'FAILURE'
        suite['conclusion'] = 'FAILURE'
        self.assertEqual(self.run_proof(), 1)
        self.assertEqual(self.run_proof(ancestry=1), 78)
        strict['conclusion'] = suite['conclusion'] = 'SUCCESS'
        self.health['ok'] = False
        self.assertEqual(self.run_proof(), 1)

    def test_missing_pending_paginated_and_wrong_event_evidence_refuses(self):
        original = copy.deepcopy(self.snapshot)
        for mode in ('missing', 'pending', 'pagination', 'event', 'shard', 'graphql'):
            self.snapshot = copy.deepcopy(original)
            self.commit = self.snapshot['data']['repository']['pullRequest']['mergeCommit']
            suite = next(s for s in self.commit['checkSuites']['nodes'] if s['workflowRun'])
            if mode == 'missing':
                suite['checkRuns']['nodes'] = []
            elif mode == 'pending':
                suite['status'] = 'IN_PROGRESS'
            elif mode == 'pagination':
                suite['checkRuns']['pageInfo']['hasNextPage'] = True
            elif mode == 'event':
                suite['workflowRun']['event'] = 'pull_request'
            elif mode == 'shard':
                suite['checkRuns']['nodes'] = [j for j in suite['checkRuns']['nodes']
                    if j['name'] != 'unit / MCP unit shard 8 of 8']
            else:
                self.snapshot['errors'] = [{'message': 'unavailable'}]
            with self.subTest(mode=mode):
                self.assertEqual(self.run_proof(), 78)

    def test_merge_commit_input_uses_one_snapshot(self):
        self.snapshot['data']['repository']['object'] = self.commit
        result = subprocess.CompletedProcess([], 0, json.dumps(self.snapshot), '')
        with patch.object(evidence.subprocess, 'run', return_value=result) as command, \
                patch('lib.release_reader.read', side_effect=OSError('offline')):
            self.assertEqual(evidence.prove_conversion(merge_sha=self.merge), 78)
        self.assertEqual(command.call_count, 1)
        self.assertIn('object(expression:', command.call_args.args[0][-1])

    def test_real_git_equal_descendant_older_unrelated_and_unknown(self):
        from lib.release_reader import Reply
        run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            env = fixture_env()
            def git(*args):
                result = run(['git', *args], cwd=root, env=env, capture_output=True, text=True, check=True)
                return result.stdout.strip()
            git('init', '-q')
            git('config', 'user.email', 'proof@example.test')
            git('config', 'user.name', 'Proof fixture')
            (root / '.github/workflows').mkdir(parents=True)
            (root / '.github/workflows/ci.yml').write_text(self.workflow)
            git('add', '.github/workflows/ci.yml')
            git('commit', '-qm', 'older')
            older = git('rev-parse', 'HEAD')
            git('commit', '--allow-empty', '-qm', 'merge')
            merge = git('rev-parse', 'HEAD')
            git('commit', '--allow-empty', '-qm', 'descendant')
            descendant = git('rev-parse', 'HEAD')
            git('checkout', '--orphan', 'unrelated')
            git('commit', '-qm', 'unrelated')
            unrelated = git('rev-parse', 'HEAD')
            self.commit['oid'] = merge
            self.commit['tree']['oid'] = git('rev-parse', merge + '^{tree}')
            def command(argv, **kwargs):
                if argv[0] == 'gh':
                    return subprocess.CompletedProcess(argv, 0, json.dumps(self.snapshot), '')
                if argv[:2] == ['git', 'fetch']:
                    raise subprocess.CalledProcessError(128, argv)
                return run(argv, **kwargs)
            def http(url, **kwargs):
                return Reply(200, {}, json.dumps(self.release if url.endswith('/release') else self.health).encode())
            for observed, expected in ((merge, 0), (descendant, 0), (older, 78),
                                       (unrelated, 78), ('f' * 40, 78)):
                self.release['git_sha']['value'] = observed
                with self.subTest(observed=observed), \
                        patch.object(evidence.subprocess, 'run', side_effect=command), \
                        patch('lib.release_reader.read', side_effect=http):
                    self.assertEqual(evidence.prove_conversion(pr=3114, root=root), expected)

    def test_cli_deadline_lifecycle(self):
        child_source = r"""
import json
import runpy
import signal
import sys
from unittest.mock import patch
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent))
suite = runpy.run_path(sys.argv[1])
case = suite['ConversionProof']()
case.setUp()
phase, outcome = sys.argv[2:]
if outcome == 'assertion':
    case.health['ok'] = False
elif outcome == 'unavailable':
    case.release['git_sha']['value'] = 'unknown'
previous = signal.getsignal(signal.SIGALRM)
real_timer = signal.setitimer
real_signal = signal.signal
injected = False
def expire():
    global injected
    injected = True
    signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
def timer(which, seconds, *args):
    result = real_timer(which, seconds, *args)
    if not injected and ((phase == 'setup' and seconds > 0)
                         or (phase == 'teardown' and seconds == 0)):
        expire()
    return result
def install(which, handler):
    result = real_signal(which, handler)
    if phase == 'install' and not injected:
        expire()
    return result
try:
    with patch.object(signal, 'setitimer', timer), patch.object(signal, 'signal', install):
        if phase == 'observation':
            from lib import release_reader
            def reply(*args, **kwargs):
                expire()
            with patch.object(release_reader, 'Reply', reply):
                code = case.run_proof(cli=True)
        else:
            code = case.run_proof(cli=True)
    raise SystemExit(code)
finally:
    print(json.dumps({'handler_restored': signal.getsignal(signal.SIGALRM) == previous,
                      'timer_disarmed': signal.getitimer(signal.ITIMER_REAL)[0] == 0}), file=sys.stderr)
    real_timer(signal.ITIMER_REAL, 0)
    real_signal(signal.SIGALRM, previous)
"""
        for phase in ('none', 'install', 'setup', 'observation', 'teardown'):
            for outcome, normal in (('pass', 0), ('assertion', 1), ('unavailable', 78)):
                with self.subTest(phase=phase, outcome=outcome):
                    child = subprocess.run([sys.executable, '-c', child_source, str(pathlib.Path(__file__).resolve()),
                                            phase, outcome], capture_output=True, text=True, timeout=10)
                    expected = normal if phase == 'none' else 78
                    self.assertEqual(child.returncode, expected, child.stdout + child.stderr)
                    self.assertEqual(len(child.stdout.splitlines()), 1, child.stdout)
                    receipt = json.loads(child.stdout)
                    if expected == 78:
                        self.assertEqual(receipt['classification'], 'insufficient_evidence')
                    self.assertEqual(json.loads(child.stderr),
                                     {'handler_restored': True, 'timer_disarmed': True})

    def test_one_wall_clock_deadline_interrupts_stalled_transport(self):
        import time
        with patch.object(evidence.subprocess, 'run', side_effect=lambda *a, **k: time.sleep(2)):
            start = time.monotonic()
            self.assertEqual(evidence.prove_conversion(pr=3114, timeout=0.05), 78)
            self.assertLess(time.monotonic() - start, 1)

    def test_deadline_and_unreadable_evidence_refuse(self):
        self.assertEqual(self.run_proof(timeout=0), 78)
        with patch.object(evidence.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gh', 1)):
            self.assertEqual(evidence.prove_conversion(pr=3114), 78)


if __name__=='__main__':
    unittest.main()
