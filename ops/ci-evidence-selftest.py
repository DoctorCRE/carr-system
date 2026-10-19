#!/usr/bin/env python3
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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ci-verdict-selftest-')
        self.addCleanup(self.tmp.cleanup)
        self.summary = pathlib.Path(self.tmp.name) / 'summary'
        self.env = patch.dict(os.environ, GITHUB_REPOSITORY='jbookout/carr-system',
                              GITHUB_RUN_ID='39284055792', GITHUB_RUN_ATTEMPT='1',
                              GITHUB_STEP_SUMMARY=str(self.summary))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.jobs = [
            {'id': 118164883351, 'run_attempt': 1, 'status': 'completed',
             'conclusion': 'success', 'name': 'ops/ci.sh --strict --only migration'},
            {'id': 118164883636, 'run_attempt': 1, 'status': 'completed',
             'conclusion': 'success', 'name': 'ops/ci.sh --strict --only gates'},
            {'id': 118164883742, 'run_attempt': 1, 'status': 'completed',
             'conclusion': 'success', 'name': 'ops/ci.sh --strict --only '
                 'pushfloor unit types contract secret dependency binding artifact freshness'},
            {'id': 118174315239, 'run_attempt': 1, 'status': 'in_progress',
             'conclusion': None, 'name': 'ops/ci.sh --strict'},
        ]

    def response(self, jobs):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps([
            {'total_count': len(jobs), 'jobs': jobs[:2]},
            {'total_count': len(jobs), 'jobs': jobs[2:]},
        ]))

    def test_waits_for_class_conclusions_after_needs_succeeded(self):
        stale = copy.deepcopy(self.jobs)
        for job in (stale[0], stale[2]):
            job.update(status='in_progress', conclusion=None)
        with patch('subprocess.run', side_effect=[self.response(stale),
                                                 self.response(self.jobs)]), \
                patch('time.sleep'):
            self.assertEqual(evidence.hosted_verdict(), 0)
        summary = self.summary.read_text()
        self.assertIn('migration: PASSED', summary)
        self.assertIn('unit: PASSED', summary)
        self.assertNotIn('INCOMPLETE', summary)


class Reuse(unittest.TestCase):
    def setUp(self):
        self.tree = 'a' * 40
        self.run = {'id': 42, 'run_attempt': 2, 'head_sha': 'b' * 40, 'status': 'completed',
                    'conclusion': 'success', 'event': 'pull_request', 'name': 'CI'}
        self.receipt = {'schema': 'carr-ci-evidence/v1', 'run_id': 42, 'run_attempt': 2,
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
    def test_every_evidence_binding_is_required(self):
        for key,value in [('tree_sha','e'*40),('contract','e'*64),('run_id',99),
                          ('run_attempt',1),('head_sha','e'*40),('groups',['gates']),('schema','unknown')]:
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
        gh.runs = [self.run, dict(self.run, id=43, conclusion='failure')]
        self.assertEqual(evidence.resolve(gh,'f'*40,self.tree,'d'*64,['gates','migration']), {'reused':False})

    def test_unavailable_github_evidence_runs_classes_without_disclosing_credentials(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="ci-evidence-selftest-"))
        try:
            gh = root / 'gh'
            gh.write_text('#!/bin/sh\necho "$GH_TOKEN" >&2\nexit 1\n')
            gh.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       GH_TOKEN='must-not-appear-in-evidence',
                       GITHUB_REPOSITORY='jbookout/carr-system',
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


if __name__=='__main__':
    unittest.main()
