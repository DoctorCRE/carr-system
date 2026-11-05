#!/usr/bin/env python3
"""Run admission against executable and contradictory synthetic receipts."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plan', ROOT / 'ops/production-proof-plan.py')
assert spec is not None and spec.loader is not None
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
fixture_spec = importlib.util.spec_from_file_location('admission_test', ROOT / 'ops/production-proof-plan-selftest.py')
assert fixture_spec is not None and fixture_spec.loader is not None
admission_test = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(admission_test)


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'proofs/requirements').mkdir(parents=True)
        self.contract = json.loads((ROOT / plan.CONTRACT_PATH).read_text())
        self.contract['x-runners']['receipt-predicate.v1'] = {
            'path': 'ops/production-proof-plan.py', 'targets': ['worker', 'studio', 'app'],
            'fixture_interface': 'receipt-predicate.v1', 'sources': {
            'ops/production-proof-plan.py': hashlib.sha256((ROOT / 'ops/production-proof-plan.py').read_bytes()).hexdigest()}}
        self.value = json.loads((ROOT / 'proofs/requirements/wr634-triage-runner.json').read_text())
        self.assertion = self.value['assertions'][0]
        self.assertion.pop('deferred', None)
        self.assertion['runner'] = 'receipt-predicate.v1'
        self.assertion['execution'] = {
            'command': ['python3', 'ops/production-proof-plan.py', '--observe-receipt'],
            'receipt_source': 'natural outlook-triage service receipt',
            'fixture_pass': 'proofs/fixtures/pass.json', 'fixture_fail': 'proofs/fixtures/fail.json',
            'checks': [{'path': 'exit_code', 'operator': 'equals', 'value': 78}]}
        (self.root / 'proofs/fixtures').mkdir()
        self.good = self.root / 'proofs/fixtures/pass.json'
        self.bad = self.root / 'proofs/fixtures/fail.json'
        self.good.write_text('{"exit_code":78}')
        self.bad.write_text('{"exit_code":0}')

    def admit(self):
        path = self.root / 'proofs/requirements/test.json'
        path.write_text(json.dumps(self.value))
        admission_test.admission_repository(self.root)
        return plan.admission('## Production proof\nproofs/requirements/test.json',
                              ['lib/outlook_intake.py', 'proofs/requirements/test.json'], self.root, self.contract)

    def test_good_and_bad_fixture_execute(self):
        self.assertIn('fixture executed', self.admit())
        self.bad.write_text(self.good.read_text())
        with self.assertRaisesRegex(ValueError, 'failing fixture.*passed'):
            self.admit()

    def test_six_unrunnable_shapes(self):
        original = copy.deepcopy(self.value)
        mutations = [
            ('command', lambda a: a['execution'].pop('command')),
            ('passing fixture', lambda a: a['execution']['checks'][0].update(value=0)),
            ('fixture interface', lambda a: a.update(runner='studio-run-receipt.v1')),
            ('fixture', lambda a: a['execution'].update(fixture_pass='proofs/fixtures/waiting-job.json')),
            ('receipt_source', lambda a: a['execution'].pop('receipt_source')),
            ('ancestry', lambda a: a['execution']['checks'][0].update(path='source_sha', value='a'*40)),
        ]
        for message, mutate in mutations:
            with self.subTest(message=message):
                self.value = copy.deepcopy(original)
                mutate(self.value['assertions'][0])
                with self.assertRaisesRegex(ValueError, message):
                    self.admit()

    def test_deferred_is_explicit_and_never_executed(self):
        self.assertion.pop('execution')
        self.assertion['deferred'] = {'blocking_unit': 'WR823.S4 unified delivery graph',
                                       'reason': 'The waiting Studio job is not built yet'}
        result = self.admit()
        self.assertIn('DEFERRED', result)
        self.assertNotIn('fixture executed', result)
        self.assertion['deferred']['blocking_unit'] = ''
        with self.assertRaises(ValueError):
            self.admit()

    def test_unmarked_prose_refused(self):
        self.assertion.pop('execution')
        with self.assertRaisesRegex(ValueError, 'execution or deferred'):
            self.admit()

    def test_no_shell_and_no_escaping_fixture(self):
        for field, value in [('command', ['sh', '-c', 'true']),
                             ('fixture_pass', '../outside.json'),
                             ('checks', [])]:
            original = copy.deepcopy(self.assertion['execution'])
            self.assertion['execution'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.admit()
            self.assertion['execution'] = original
        self.good.unlink()
        self.good.symlink_to(self.bad)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.admit()

    def test_live_cli_uses_same_predicate(self):
        self.admit()
        command = [sys.executable, str(ROOT / 'ops/production-proof-plan.py'),
            '--observe-receipt', '--root', str(self.root),
            '--requirement', str(self.root / 'proofs/requirements/test.json'),
            '--assertion', self.assertion['id'], '--receipt-file']
        for receipt, code in [(self.good, 0), (self.bad, 1)]:
            result = subprocess.run([*command, str(receipt)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def test_budgets_and_source_drift_refuse(self):
        self.value['safety']['max_artifact_bytes'] = 1
        with self.assertRaisesRegex(ValueError, 'artifact bytes'):
            self.admit()
        self.value['safety']['max_artifact_bytes'] = 65536
        runner = self.contract['x-runners']['receipt-predicate.v1']
        runner['sources']['ops/production-proof-plan.py'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'source (binding|digest)'):
            self.admit()

    def test_every_checked_in_requirement_has_a_runnable_or_named_deferred_plan(self):
        for path in sorted((ROOT / 'proofs/requirements').glob('*.json')):
            with self.subTest(path=path.name):
                value = json.loads(path.read_text())
                plan.validate(value, self.contract)
                for assertion in value['assertions']:
                    result = plan.assertion_execution(assertion, ROOT, self.contract, plan.execution_budget(value))
                    self.assertTrue('DEFERRED' in result or 'fixture executed' in result)


    def test_descendant_release_uses_real_ancestry(self):
        from git_env import fixture_env
        env = fixture_env()
        env.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.com',
                   GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.com')
        def git(*args):
            return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                cwd=self.root, env=env, text=True, stderr=subprocess.PIPE).strip()
        admission_test.admission_repository(self.root)
        git('commit', '--allow-empty', '-qm', 'merge')
        merge = git('rev-parse', 'HEAD')
        git('commit', '--allow-empty', '-qm', 'descendant release')
        served = git('rev-parse', 'HEAD')
        self.assertion['execution']['checks'] = [
            {'path': 'source_sha', 'operator': 'contains_commit', 'value': merge}]
        self.good.write_text(json.dumps({'source_sha': served}))
        self.bad.write_text(json.dumps({'source_sha': 'f'*40}))
        self.assertIn('fixture executed', self.admit())
        git('replace', '-f', merge, served)
        self.assertIn('fixture executed', self.admit())


if __name__ == '__main__':
    unittest.main()
