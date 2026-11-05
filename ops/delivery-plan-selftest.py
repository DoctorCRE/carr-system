#!/usr/bin/env python3
"""Exercise commit-bound delivery resolution and shared admission classification."""
import importlib.util
import hashlib
import os
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

from git_env import fixture_env
import delivery_plan as delivery

ROOT = Path(__file__).resolve().parents[1]


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = fixture_env()
        self.env.update(GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                        GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
        self.git('init', '-qb', 'main')
        self.write('ops/observer.py', 'synthetic observer\n')
        revision = self.commit()
        policy = json.loads((ROOT / delivery.POLICY).read_text())
        policy['x-runners'] = {'fixture.v1': {'revision': revision, 'path': 'ops/observer.py',
                                            'targets': ['worker', 'studio']}}
        self.write(delivery.POLICY, json.dumps(policy))
        self.write(delivery.INVENTORY, (ROOT / delivery.INVENTORY).read_text())
        self.write(delivery.SCHEMA, (ROOT / delivery.SCHEMA).read_text())
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
            cwd=self.root, env=self.env, text=True, stderr=subprocess.PIPE).strip()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        paths = self.git('ls-files', '--modified', '--others', '--exclude-standard').splitlines()
        if paths:
            self.git('add', '--', *paths)
        self.git('commit', '-qm', 'synthetic fixture')
        return self.git('rev-parse', 'HEAD')

    def requirement(self, path='proofs/requirements/change.json', targets=None):
        req = {'schema': 'carr-production-proof-plan.v1', 'requirement_id': 'synthetic',
               'targets': targets or ['worker', 'studio'], 'assertions': [{
                   'id': 'example', 'predicate': 'Observe the exact bound release', 'runner': 'fixture.v1',
                   'expected_result': 'matching release identity', 'input_class': 'synthetic only',
                   'deadline_seconds': 30}],
               'safety': {'read_only': True, 'no_sends': True, 'max_requests': 1,
                          'max_records': 1, 'max_runtime_seconds': 30, 'max_artifact_bytes': 1024}}
        self.write(path, json.dumps(req))
        return req

    def resolve(self, sha=None):
        return delivery.resolve(self.root, sha or self.git('rev-parse', 'HEAD'), main_ref='main')

    def test_requested_to_bound_to_resolved_and_repeat(self):
        self.write('docs/example.md', 'guide')
        sha = self.commit()
        plan = self.resolve(sha)
        self.assertEqual(plan['lanes'], ['docs'])
        self.assertEqual(plan['source']['commit'], sha)
        self.assertEqual(plan['source']['first_parent'], self.base)
        self.assertEqual(plan['source']['tree'], self.git('rev-parse', sha + '^{tree}'))
        self.assertEqual(plan, self.resolve(sha))
        self.write('docs/example.md', 'dirty')
        self.write(delivery.INVENTORY, '{}')
        self.assertEqual(plan, self.resolve(sha))
        self.assertFalse(plan['execution_attested'])
        code = "const fs=require('fs');const {Validator}=require('@cfworker/json-schema');" + \
            "const x=JSON.parse(fs.readFileSync(0,'utf8'));" + \
            "const r=new Validator(x.schema).validate(x.plan);" + \
            "if(!r.valid){console.error(JSON.stringify(r.errors));process.exit(1)}"
        result = subprocess.run(['node', '-e', code], cwd=ROOT / 'mcp-server',
            input=json.dumps({'schema': json.loads((ROOT / delivery.SCHEMA).read_text()), 'plan': plan}),
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_requested_to_refused(self):
        for sha in ['HEAD', 'f' * 40, self.git('rev-parse', 'HEAD^{tree}')]:
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                self.resolve(sha)
        self.git('checkout', '-qb', 'side')
        self.write('docs/side.md', 'side')
        sha = self.commit()
        self.git('checkout', 'main')
        with self.assertRaisesRegex(ValueError, 'main'):
            self.resolve(sha)
        self.git('merge', '--no-ff', '-m', 'synthetic merge', 'side')
        with self.assertRaisesRegex(ValueError, 'main'):
            self.resolve(sha)

    def test_bound_to_refused_unknown_and_missing_proof(self):
        self.write('unclassified/a', 'unknown')
        sha = self.commit()
        with self.assertRaisesRegex(ValueError, 'unclassified'):
            self.resolve(sha)

    def test_missing_runtime_proof_refuses(self):
        self.write('mcp-server/src/a.md', 'installed markdown')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'requirement|target'):
            self.resolve()

    def test_union_and_immutable_requirements_and_observers(self):
        self.requirement()
        for path in ['mcp-server/src/prompt.md', 'lib/outlook_runtime.py', 'migrations/100.sql']:
            self.write(path, 'synthetic')
        sha = self.commit()
        plan = self.resolve(sha)
        self.assertEqual(plan['lanes'], ['control', 'database', 'studio', 'worker'])
        self.assertEqual(plan['targets'], ['studio', 'worker'])
        ref = plan['requirements'][0]
        self.assertEqual(ref['commit'], sha)
        self.assertRegex(ref['sha256'], '^[0-9a-f]{64}$')
        self.assertEqual(ref['observers'][0]['runner'], 'fixture.v1')
        self.write('proofs/requirements/change.json', '{}')
        self.assertEqual(plan, self.resolve(sha))
        self.assertNotIn('predicate', json.dumps(plan))

    def test_rename_classifies_both_sides_and_deleted_requirement_refuses(self):
        self.requirement()
        self.write('lib/prompt.md', 'installed')
        self.commit()
        (self.root / 'docs').mkdir()
        self.git('mv', 'lib/prompt.md', 'docs/prompt.md')
        sha = self.commit()
        # This commit must carry its own landed requirement, not inherit a PR body.
        with self.assertRaisesRegex(ValueError, 'requirement|target'):
            self.resolve(sha)
        req = self.requirement()
        req['requirement_id'] = 'renamed'
        self.write('proofs/requirements/change.json', json.dumps(req))
        self.commit()
        self.git('mv', 'docs/prompt.md', 'lib/prompt.md')
        req['requirement_id'] = 'renamed-back'
        self.write('proofs/requirements/change.json', json.dumps(req))
        plan = self.resolve(self.commit())
        self.assertIn('studio', plan['lanes'])
        self.assertIn('docs', plan['lanes'])
        self.assertIn('docs/prompt.md', plan['changed_paths'])
        self.git('rm', 'proofs/requirements/change.json')
        with self.assertRaisesRegex(ValueError, 'deleted requirement'):
            self.resolve(self.commit())

    def test_invalid_requirement_and_observer_fail_closed(self):
        req = self.requirement()
        req['assertions'][0]['runner'] = 'arbitrary'
        self.write('proofs/requirements/change.json', json.dumps(req))
        with self.assertRaisesRegex(ValueError, 'runner'):
            self.resolve(self.commit())

    def test_admission_and_resolver_share_classifier(self):
        spec = importlib.util.spec_from_file_location('admission', ROOT / 'ops/production-proof-plan.py')
        admission = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(admission)
        self.assertIs(admission.classify_paths, delivery.classify_paths)
        policy = json.loads((ROOT / delivery.POLICY).read_text())
        paths = ['docs/guide.md', 'lib/outlook_runtime.py', 'mcp-server/src/prompt.md']
        classified = delivery.classify_paths(paths, policy)
        self.assertEqual(admission.product_paths(paths, policy), classified['product_paths'])
        self.assertEqual(classified['required_targets'], ['studio', 'worker'])

    def test_path_and_source_budgets(self):
        policy = json.loads((ROOT / delivery.POLICY).read_text())
        for paths in [[], ['docs/a.md'] * 10001, ['/tmp/a'], ['docs/../a'], ['a\\b'], ['docs/a\n']]:
            with self.subTest(paths=paths[:1]), self.assertRaises(ValueError):
                delivery.classify_paths(paths, policy)
        self.write('docs/large.md', 'x' * 1048577)
        sha = self.commit()
        source = delivery.Source(self.root)
        with self.assertRaisesRegex(ValueError, 'oversized'):
            source.blob(sha, 'docs/large.md')
        with patch.object(delivery.time, 'monotonic', return_value=source.deadline + 1):
            with self.assertRaisesRegex(ValueError, 'deadline'):
                source.git('rev-parse', 'HEAD')

    def test_source_digest_binding_and_sanitized_projection(self):
        self.requirement()
        policy = json.loads((self.root / delivery.POLICY).read_text())
        policy['x-runners']['fixture.v1'] = {
            'path': 'ops/observer.py', 'targets': ['worker', 'studio'],
            'sources': {'ops/observer.py': hashlib.sha256(b'synthetic observer\n').hexdigest()}}
        self.write(delivery.POLICY, json.dumps(policy))
        sha = self.commit()
        plan = self.resolve(sha)
        self.assertEqual(plan['requirements'][0]['observers'][0]['sources'][0]['commit'], sha)
        self.assertEqual(plan['plan_digest'], delivery.digest(delivery.canonical({
            k: v for k, v in plan.items() if k != 'plan_digest'})))
        serialized = json.dumps(plan)
        for field in ['predicate', 'expected_result', 'input_class']:
            self.assertNotIn(field, serialized)
        self.write('ops/observer.py', 'wrong source')
        req = self.requirement()
        req['requirement_id'] = 'changed-observer'
        self.write('proofs/requirements/change.json', json.dumps(req))
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.resolve(self.commit())

    def test_source_identity_ignores_git_environment_and_replacements(self):
        self.write('docs/source.md', 'source')
        sha = self.commit()
        expected = self.resolve(sha)
        self.git('replace', sha, self.base)
        with patch.dict(os.environ, {**self.env, 'GIT_DIR': '/nonexistent',
                'GIT_CONFIG_PARAMETERS': "'core.useReplaceRefs=true'"}, clear=True):
            self.assertEqual(self.resolve(sha), expected)

    def test_undeclared_target_and_duplicate_assertions_refuse(self):
        req = self.requirement(targets=['worker'])
        self.write('lib/runtime.md', 'installed')
        with self.assertRaisesRegex(ValueError, 'target'):
            self.resolve(self.commit())
        req['targets'] = ['worker', 'studio']
        req['assertions'] *= 2
        self.write('proofs/requirements/change.json', json.dumps(req))
        with self.assertRaisesRegex(ValueError, 'duplicate assertion'):
            self.resolve(self.commit())

    def test_admission_to_resolution_requires_changed_bound_requirement(self):
        spec = importlib.util.spec_from_file_location('admission', ROOT / 'ops/production-proof-plan.py')
        admission = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(admission)
        name = 'proofs/requirements/change.json'
        req = self.requirement(targets=['worker'])
        self.commit()
        self.write('mcp-server/src/runtime.py', 'synthetic runtime')
        sha = self.commit()
        policy = json.loads((self.root / delivery.POLICY).read_text())
        body = '## Production proof\n' + name
        with self.assertRaisesRegex(ValueError, 'changed requirement'):
            admission.admission(body, ['mcp-server/src/runtime.py'], self.root, policy)
        with self.assertRaisesRegex(ValueError, 'landed requirements'):
            self.resolve(sha)
        req['requirement_id'] = 'new-change'
        self.write(name, json.dumps(req))
        sha = self.commit()
        paths = ['mcp-server/src/runtime.py', name]
        self.assertIn('accepted', admission.admission(body, paths, self.root, policy))
        self.assertEqual(self.resolve(sha)['requirements'][0]['commit'], sha)
        policy['x-runners']['fixture.v1']['revision'] = 'f' * 40
        self.write(delivery.POLICY, json.dumps(policy))
        req['requirement_id'] = 'invalid-observer'
        self.write(name, json.dumps(req))
        self.commit()
        with self.assertRaises(ValueError):
            admission.admission(body, paths, self.root, policy)
        with self.assertRaises(ValueError):
            self.resolve()

    def test_cli_stdout_respects_response_budget(self):
        for i in range(1000):
            self.write(f'docs/file-{i:04d}.md', 'synthetic')
        name = 'proofs/requirements/change.json'
        req = self.requirement(name)
        req['requirement_id'] = 'a' * 900000
        self.write(name, json.dumps(req))
        self.commit()
        size = len(delivery.canonical(self.resolve()))
        req['requirement_id'] += 'a' * (1048401 - size)
        self.write(name, json.dumps(req))
        self.git('add', '--', name)
        self.git('commit', '--amend', '--no-edit')
        sha = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', sha)
        result = subprocess.run([sys.executable, str(ROOT / 'ops/delivery-plan.py'),
            '--root', str(self.root), '--main-sha', sha],
            env=self.env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 1048576)
        self.assertEqual(result.stdout, delivery.canonical(self.resolve(sha)) + b'\n')

    def test_cli_refusal_has_no_partial_plan(self):
        result = subprocess.run([sys.executable, str(ROOT / 'ops/delivery-plan.py'),
            '--root', str(self.root), '--main-sha', 'f' * 40],
            env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('refused', result.stderr)

    def test_inventory_identity_and_job_kinds_fail_closed(self):
        inventory = json.loads((ROOT / delivery.INVENTORY).read_text())
        for key, value in [('repository', 'Other/repo'), ('environment', 'staging'),
                           ('lanes', {**inventory['lanes'], 'arbitrary-job': {}})]:
            changed = {**inventory, key: value}
            self.write(delivery.INVENTORY, json.dumps(changed))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'contract'):
                self.resolve(self.commit())

    def test_response_budget_refuses_large_requirement_identifiers(self):
        for suffix in ['one', 'two']:
            path = 'proofs/requirements/' + suffix + '.json'
            req = self.requirement(path)
            req['requirement_id'] = 'a' * 600000 + suffix
            self.write(path, json.dumps(req))
        with self.assertRaisesRegex(ValueError, 'projection'):
            self.resolve(self.commit())

    def test_observer_source_count_is_bounded(self):
        self.requirement()
        policy = json.loads((self.root / delivery.POLICY).read_text())
        policy['x-runners']['fixture.v1'] = {
            'path': 'ops/observer.py', 'targets': ['worker', 'studio'],
            'sources': {'ops/observer.py': 'a' * 64, **{
                'ops/source-' + str(i) + '.py': 'a' * 64 for i in range(16)}}}
        self.write(delivery.POLICY, json.dumps(policy))
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.resolve(self.commit())

    def test_cli_reads_canonical_main_plan_and_empty_commit(self):
        self.write('docs/cli.md', 'committed')
        sha = self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', sha)
        result = subprocess.run([sys.executable, str(ROOT / 'ops/delivery-plan.py'),
            '--root', str(self.root), '--main-sha', sha],
            env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.resolve(sha))
        self.git('commit', '--allow-empty', '-qm', 'empty source change')
        empty = self.resolve()
        self.assertEqual(empty['changed_paths'], [])
        self.assertEqual(empty['lanes'], ['docs'])


if __name__ == '__main__':
    unittest.main()
