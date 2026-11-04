#!/usr/bin/env python3
"""Exercise admission through the same CLI used by CI."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'ops/production-proof-plan.py'


def module():
    spec = importlib.util.spec_from_file_location('production_proof_plan', PROGRAM)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'proofs/requirements').mkdir(parents=True)
        self.body = self.root / 'body.md'
        self.files = self.root / 'files.json'
        self.files.write_text(json.dumps(['mcp-server/src/example.js']))
        self.requirement = {
            'schema': 'carr-production-proof-plan.v1', 'requirement_id': 'synthetic',
            'targets': ['worker'], 'assertions': [{
                'id': 'release', 'predicate': 'Released worker returns the expected response shape',
                'runner': 'worker-release-smoke.v1',
                'expected_result': 'all required shape assertions pass',
                'input_class': 'read-only production response', 'deadline_seconds': 300}],
            'safety': {'read_only': True, 'no_sends': True, 'max_requests': 20,
                       'max_records': 100, 'max_runtime_seconds': 300, 'max_artifact_bytes': 65536}}
        self.manifest = self.root / 'proofs/requirements/synthetic.json'
        self.manifest.write_text(json.dumps(self.requirement))

    def run_plan(self, body, files=None):
        self.body.write_text(body)
        if files is not None:
            self.files.write_text(json.dumps(files))
        return subprocess.run([sys.executable, str(PROGRAM), '--root', str(self.root),
                               '--body-file', str(self.body), '--files-file', str(self.files)],
                              capture_output=True, text=True, timeout=20,
                              env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})

    def test_hosted_invalid_then_corrected_declaration(self):
        for body in ['## Production proof\n', '## Production proof\n<!-- add proof -->\n']:
            result = self.run_plan(body)
            self.assertNotEqual(result.returncode, 0, result.stdout)
        print('Synthetic invalid declaration REFUSED:', result.stdout.strip())
        result = self.run_plan('## Production proof\nproofs/requirements/synthetic.json\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Synthetic corrected declaration ACCEPTED:', result.stdout.strip())

    def test_unknown_runner(self):
        self.requirement['assertions'][0]['runner'] = 'arbitrary-shell'
        self.manifest.write_text(json.dumps(self.requirement))
        result = self.run_plan('## Production proof\nproofs/requirements/synthetic.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('runner', result.stdout)

    def test_escapes_and_symlinks(self):
        for path in ['../outside.json', '/tmp/outside.json', 'proofs/requirements/../../outside.json']:
            result = self.run_plan('## Production proof\n' + path)
            self.assertNotEqual(result.returncode, 0, path)
        self.manifest.unlink()
        self.manifest.symlink_to(self.body)
        self.assertNotEqual(self.run_plan('## Production proof\nproofs/requirements/synthetic.json').returncode, 0)

    def test_non_product_allowlist_and_mixed_diff(self):
        for paths in [['docs/guide.md'], ['mcp-server/test/example.test.mjs'],
                      ['ops/example-selftest.py'], ['.github/workflows/ci.yml']]:
            result = self.run_plan('## Production proof\nn/a: source-only verification', paths)
            self.assertEqual(result.returncode, 0, result.stdout)
        for path in ['mcp-server/src/prompt.md', 'tools/agent.md', 'ops/runtime.py',
                     'migrations/0001.sql', 'docs/../mcp-server/src/a.js']:
            result = self.run_plan('## Production proof\nn/a: documentation', ['docs/a.md', path])
            self.assertNotEqual(result.returncode, 0, path)

    def test_placeholder_and_duplicate_sections(self):
        for body in ['## Production proof\nn/a:', '## Production proof\nn/a: TODO',
                     '## Production proof\n<!-- n/a: docs -->',
                     '## Production proof\nn/a: docs\n## Production proof\nn/a: docs']:
            self.assertNotEqual(self.run_plan(body, ['docs/a.md']).returncode, 0, body)

    def test_missing_required_fields_and_bounds(self):
        del self.requirement['safety']
        self.manifest.write_text(json.dumps(self.requirement))
        self.assertNotEqual(self.run_plan('## Production proof\nproofs/requirements/synthetic.json').returncode, 0)
        self.assertNotEqual(self.run_plan('## Production proof\nn/a: docs', ['docs/a.md'] * 10001).returncode, 0)

    def test_rollout_old_pr_push_and_body_edit(self):
        plan = module()
        boundary = '2026-11-04T03:00:00Z'
        before = '2026-11-04T02:00:00Z'
        after = '2026-11-04T04:00:00Z'
        self.assertFalse(plan.activated(before, None, boundary))
        self.assertFalse(plan.activated(before, before, boundary))
        self.assertTrue(plan.activated(after, None, boundary))
        self.assertTrue(plan.activated(before, after, boundary))

    def test_ci_event_body_and_metadata_failure(self):
        plan = module()
        event = {'pull_request': {'number': 1, 'base': {'sha': 'a' * 40},
                                 'head': {'sha': 'b' * 40}, 'body': '## Production proof\n'},
                 'repository': {'full_name': 'DoctorCRE/carr-system'}}
        event_file = self.root / 'event.json'
        event_file.write_text(json.dumps(event))
        settings = {'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event_file)}
        metadata = {'createdAt': '2026-11-04T04:00:00Z', 'lastEditedAt': None}
        with patch.dict(os.environ, settings), patch.object(sys, 'argv', [str(PROGRAM)]), \
                patch.object(plan, 'command', return_value='2026-11-04T03:00:00Z'), \
                patch.object(plan, 'github_metadata', return_value=metadata):
            self.assertEqual(plan.main(), 1)
        with patch.dict(os.environ, settings), patch.object(sys, 'argv', [str(PROGRAM)]), \
                patch.object(plan, 'command', return_value='2026-11-04T03:00:00Z'), \
                patch.object(plan, 'github_metadata', side_effect=OSError('unavailable')):
            self.assertEqual(plan.main(), 1)
        with patch.dict(os.environ, settings), patch.object(sys, 'argv', [str(PROGRAM)]), \
                patch.object(plan, 'command', return_value=''), \
                patch.object(plan, 'github_metadata') as fetch:
            self.assertEqual(plan.main(), 0)
            fetch.assert_not_called()

    def test_stale_body_or_head_refuses(self):
        plan = module()
        event = {'repository': {'full_name': 'DoctorCRE/carr-system'},
                 'pull_request': {'number': 1, 'body': 'current', 'head': {'sha': 'b' * 40}}}
        for body, head in [('old', 'b' * 40), ('current', 'c' * 40)]:
            response = {'data': {'repository': {'pullRequest': {'body': body, 'headRefOid': head}}}}
            with patch.object(plan, 'command', return_value=json.dumps(response)):
                with self.assertRaisesRegex(ValueError, 'stale'):
                    plan.github_metadata(event)

    def test_budget_shape_and_target_refusals(self):
        plan = module()
        contract = json.loads((ROOT / plan.CONTRACT_PATH).read_text())
        for value in [False, 0, 301]:
            self.requirement['assertions'][0]['deadline_seconds'] = value
            with self.assertRaises(ValueError):
                plan.validate(self.requirement, contract)
        self.requirement['assertions'][0]['deadline_seconds'] = 300
        self.requirement['targets'] = ['app']
        self.manifest.write_text(json.dumps(self.requirement))
        self.assertNotEqual(self.run_plan('## Production proof\nproofs/requirements/synthetic.json').returncode, 0)

    def test_large_input_and_nested_requirement_symlink(self):
        self.body.write_text('x' * 1048577)
        with self.assertRaises(ValueError):
            module().read(self.body)
        real = self.root / 'elsewhere'
        (self.root / 'proofs/requirements').rename(real)
        (self.root / 'proofs/requirements').symlink_to(real, target_is_directory=True)
        self.assertNotEqual(self.run_plan('## Production proof\nproofs/requirements/synthetic.json').returncode, 0)

    def test_first_parent_rollout_and_rename_sources_with_real_git(self):
        plan = module()
        from datetime import datetime
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.com',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.com')
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.root, env=env, text=True).strip()
        git('init', '-q')
        (self.root / 'runtime.py').write_text('pass\n')
        git('add', 'runtime.py')
        git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial')
        (self.root / 'contracts').mkdir()
        (self.root / plan.CONTRACT_PATH).write_text('{}')
        git('add', plan.CONTRACT_PATH)
        git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'rollout')
        boundary = git('log', '--first-parent', '--diff-filter=A', '--format=%cI', 'HEAD', '--', plan.CONTRACT_PATH)
        self.assertIsNotNone(datetime.fromisoformat(boundary))
        base = git('rev-parse', 'HEAD')
        (self.root / 'docs').mkdir()
        git('mv', 'runtime.py', 'docs/runtime.md')
        git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'rename')
        paths = git('diff', '--name-only', '--no-renames', base, 'HEAD').splitlines()
        self.assertEqual(plan.product_paths(paths, json.loads((ROOT / plan.CONTRACT_PATH).read_text())), ['runtime.py'])


if __name__ == '__main__':
    unittest.main()
