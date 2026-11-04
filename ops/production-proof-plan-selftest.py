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
from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'ops/production-proof-plan.py'


def module():
    spec = importlib.util.spec_from_file_location('production_proof_plan', PROGRAM)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def runner_source(runner, root=ROOT):
    revision, path = runner['revision'], runner['path']
    ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', revision, 'HEAD'],
                              cwd=root, env=fixture_env(), capture_output=True)
    if ancestry.returncode:
        reason = 'not an ancestor of tested HEAD' if ancestry.returncode == 1 else 'unresolvable revision or tested HEAD'
        raise ValueError(f"runner pin {revision}: {reason}")
    source = subprocess.run(['git', 'show', revision + ':' + path],
                            cwd=root, env=fixture_env(), capture_output=True)
    if source.returncode:
        raise ValueError(f"runner pin {revision}: {path}: unresolvable path")
    return source.stdout


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
                              env=fixture_env())

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

    def test_ci_implementation_declarations(self):
        for path in ['ops/ci-evidence.py', 'ops/ci-unit.py', 'ops/ci-secret.py',
                     'ops/ci.py', 'ops/ci-pushfloor.py', 'ops/ci-dep-check.py',
                     'ops/ci-migration.py', 'ops/ci-migration-gates.py', 'ops/ci-node-file.mjs',
                     'ops/ci-apt.py', 'ops/ci-postgres.sh', 'ops/ci-zsh.sh']:
            with self.subTest(path=path):
                result = self.run_plan('## Production proof\nn/a: CI checks only; no deployed runtime changes', [path])
                self.assertEqual(result.returncode, 0, result.stdout)
        for path in ['ops/ci-dispatch.py', 'ops/ci-secret-scan.py', 'lib/outlook_runtime.py', 'ops/runtime.py']:
            self.assertNotEqual(self.run_plan('## Production proof\nn/a: CI checks only', [path]).returncode, 0)

    def test_ci_only_helpers_and_mixed_runtime(self):
        body = '## Production proof\nn/a: CI checks only; no deployed runtime changes'
        for path in ['ops/ci_unit_shards.py', 'ops/node_test_summary.py']:
            with self.subTest(path=path):
                result = self.run_plan(body, [path])
                self.assertEqual(result.returncode, 0, result.stdout)
                result = self.run_plan(body, [path, 'mcp-server/src/work-queue.js'])
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_worker_and_mixed_changes_require_worker_coverage(self):
        body = '## Production proof\nproofs/requirements/synthetic.json'
        worker_assertion = dict(self.requirement['assertions'][0])
        studio_assertion = {**worker_assertion, 'id': 'studio-receipt',
                            'runner': 'studio-run-receipt.v1',
                            'predicate': 'Installed Studio receipt matches expected runtime and counts'}
        for paths in [['mcp-server/src/work-queue.js'],
                      ['mcp-server/src/work-queue.js', 'lib/outlook_runtime.py'],
                      ['mcp-server/wrangler.toml'], ['mcp-server/package-lock.json'],
                      ['migrations/0001.sql']]:
            with self.subTest(paths=paths):
                self.requirement['targets'] = ['studio']
                self.requirement['assertions'] = [studio_assertion]
                self.manifest.write_text(json.dumps(self.requirement))
                result = self.run_plan(body, paths)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('changed runtime target missing', result.stdout)
                mixed = 'lib/outlook_runtime.py' in paths
                self.requirement['targets'] = ['worker', 'studio'] if mixed else ['worker']
                self.manifest.write_text(json.dumps(self.requirement))
                result = self.run_plan(body, paths)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('target has no registered runner', result.stdout)
                self.requirement['assertions'] = [worker_assertion, studio_assertion] if mixed else [worker_assertion]
                self.manifest.write_text(json.dumps(self.requirement))
                result = self.run_plan(body, paths)
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_studio_declaration_and_worker_mismatch(self):
        body = '## Production proof\nproofs/requirements/synthetic.json'
        paths = ['lib/outlook_runtime.py']
        self.requirement['targets'] = ['studio']
        self.requirement['assertions'][0]['runner'] = 'studio-run-receipt.v1'
        self.requirement['assertions'][0]['predicate'] = 'After the installed Studio run, read the receipt and check processed counts'
        self.manifest.write_text(json.dumps(self.requirement))
        result = self.run_plan(body, paths)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.requirement['assertions'][0]['runner'] = 'worker-release-smoke.v1'
        self.manifest.write_text(json.dumps(self.requirement))
        self.assertNotEqual(self.run_plan(body, paths).returncode, 0)
        self.requirement['targets'] = ['worker']
        self.manifest.write_text(json.dumps(self.requirement))
        self.assertNotEqual(self.run_plan(body, paths).returncode, 0)

    def test_runner_revision_ancestry(self):
        path = 'ops/studio-proof-observe.py'
        with self.assertRaisesRegex(ValueError, 'e105804540f17afba50dd45f78b84b50698a3ffc.*(not an ancestor|unresolvable revision)'):
            runner_source({'revision': 'e105804540f17afba50dd45f78b84b50698a3ffc', 'path': path})
        self.assertEqual(
            runner_source({'revision': 'f699e1859dc87e4122501bbaba35a312607452a7', 'path': path}),
            (ROOT / path).read_bytes())

    def test_runner_rejects_resolvable_nonancestor_and_missing_path(self):
        env = fixture_env()
        env.update(GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.com',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.com')
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.root, env=env, text=True).strip()
        git('init', '-q')
        (self.root / 'runner.py').write_text('pass\n')
        git('add', 'runner.py')
        git('-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial')
        ancestor = git('rev-parse', 'HEAD')
        git('-c', 'core.hooksPath=/dev/null', 'commit', '--allow-empty', '-qm', 'unmerged')
        unmerged = git('rev-parse', 'HEAD')
        git('checkout', '--detach', ancestor)
        with self.assertRaisesRegex(ValueError, unmerged + '.*not an ancestor'):
            runner_source({'revision': unmerged, 'path': 'runner.py'}, self.root)
        with self.assertRaisesRegex(ValueError, ancestor + '.*missing.py.*unresolvable path'):
            runner_source({'revision': ancestor, 'path': 'missing.py'}, self.root)
        self.assertEqual(runner_source({'revision': ancestor, 'path': 'runner.py'}, self.root), b'pass\n')

    def test_runner_registry_pins_and_studio_observer(self):
        contract = json.loads((ROOT / module().CONTRACT_PATH).read_text())
        sources = {name: runner_source(runner) for name, runner in contract['x-runners'].items()}
        runner = contract['x-runners']['studio-run-receipt.v1']
        source = sources['studio-run-receipt.v1']
        self.assertEqual(source, (ROOT / runner['path']).read_bytes())
        observer = self.root / 'observer.py'
        observer.write_bytes(source)
        receipt = self.root / 'receipt.txt'
        receipt.write_text('outlook-intake-receipt ' + json.dumps({
            'state': 'succeeded', 'runtime_sha': 'a' * 40, 'counts': {'run': {'processed': 1}}}))
        result = subprocess.run([sys.executable, str(observer), '--receipt-file', str(receipt),
            '--runtime-sha', 'a' * 40, '--service', 'outlook-intake',
            '--count', 'run.processed', '--minimum', '1'],
            capture_output=True, text=True, timeout=10, env=fixture_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
        env = fixture_env()
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


class EventScopeTest(unittest.TestCase):
    """Real Git and CLI; only the remote metadata response is synthetic."""

    def setUp(self):
        AdmissionTest.setUp(self)
        self.env = fixture_env()
        self.env.update(GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.com',
                        GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.com')
        self.git('init', '-q')
        (self.root / 'contracts').mkdir()
        (self.root / module().CONTRACT_PATH).write_text('{}')
        self.commit('rollout', [module().CONTRACT_PATH])
        self.base = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-qb', 'studio')
        self.paths = [
            'docs/models/email-signature-layout/README.md',
            'docs/models/email-signature-layout/chart.mmd',
            'docs/models/email-signature-layout/design.md',
            'lib/calendar_agenda.py', 'lib/email_contact_candidates.py',
            'lib/email_contact_review.py', 'lib/outlook_intake.py',
            'ops/calendar-agenda-selftest.py', 'ops/email-contact-candidates-selftest.py',
            'ops/email-contact-review-selftest.py', 'ops/outlook-intake-selftest.py',
            'proofs/requirements/wr442-s2-signature-layout.json']
        for name in self.paths:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic\n')
        self.requirement['targets'] = ['studio']
        self.requirement['assertions'][0]['runner'] = 'studio-run-receipt.v1'
        self.manifest = self.root / self.paths[-1]
        self.manifest.write_text(json.dumps(self.requirement))
        self.commit('Studio source', self.paths)
        self.head = self.git('rev-parse', 'HEAD')
        self.event = {'repository': {'full_name': 'Synthetic/fixture'}, 'pull_request': {
            'number': 1, 'base': {'sha': self.base}, 'head': {'sha': self.head},
            'body': '## Production proof\n' + self.paths[-1]}}
        self.metadata = {'createdAt': '2099-01-01T00:00:00Z', 'lastEditedAt': None,
                         'body': self.event['pull_request']['body'], 'headRefOid': self.head}
        binary = self.root / 'bin'
        binary.mkdir()
        gh = binary / 'gh'
        gh.write_text('#!' + sys.executable + '\nimport os\nfrom pathlib import Path\n'
                      'print(Path(os.environ["FIXTURE_METADATA"]).read_text())\n')
        gh.chmod(0o755)
        self.env.update(PATH=str(binary) + os.pathsep + self.env.get('PATH', os.defpath),
                        FIXTURE_METADATA=str(self.root / 'metadata.json'))

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
            cwd=self.root, env=self.env, text=True, stderr=subprocess.PIPE, timeout=15).strip()

    def commit(self, message, paths):
        self.git('add', '--', *paths)
        self.git('commit', '-qm', message)

    def advance_and_merge(self):
        self.git('checkout', '-qb', 'advanced', self.base)
        names = [f'mcp-server/src/unrelated-{i}.js' for i in range(25)]
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic\n')
        self.commit('unrelated Worker changes', names)
        self.git('merge', '--no-ff', '-m', 'candidate merge', self.head)
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)
        self.assertEqual(len(self.git('diff', '--name-only', self.base + '...HEAD').splitlines()),
                         25 + len(self.git('diff', '--name-only', self.base + '...' + self.head).splitlines()))

    def cli(self, event=True, *args):
        event_path = self.root / 'event.json'
        event_path.write_text(json.dumps(self.event))
        (self.root / 'metadata.json').write_text(json.dumps({
            'data': {'repository': {'pullRequest': self.metadata}}}))
        env = dict(self.env, GITHUB_EVENT_NAME='pull_request' if event else 'push',
                   GITHUB_EVENT_PATH=str(event_path))
        return subprocess.run([sys.executable, str(PROGRAM), '--root', str(self.root), *args],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=30)

    def test_divergent_merge_uses_twelve_pr_paths(self):
        self.advance_and_merge()
        self.assertEqual(self.git('diff', '--name-only', self.base + '...' + self.head).splitlines(), self.paths)
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('proof declaration accepted', result.stdout)

    def test_actual_worker_change_still_refuses(self):
        self.git('checkout', '--detach', self.head)
        name = 'mcp-server/src/actual-worker.js'
        path = self.root / name
        path.parent.mkdir(parents=True)
        path.write_text('synthetic\n')
        self.commit('actual Worker edit', [name])
        self.head = self.git('rev-parse', 'HEAD')
        self.event['pull_request']['head']['sha'] = self.head
        self.metadata['headRefOid'] = self.head
        self.advance_and_merge()
        result = self.cli()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('changed runtime target missing', result.stdout)

    def test_source_and_no_main_advance_merge(self):
        for merge in [False, True]:
            with self.subTest(merge=merge):
                if merge:
                    self.git('checkout', '-qb', 'candidate', self.base)
                    self.git('merge', '--no-ff', '-m', 'candidate', self.head)
                result = self.cli()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_event_objects_refuse(self):
        for field in ['base', 'head']:
            with self.subTest(field=field):
                original = self.event['pull_request'][field]['sha']
                self.event['pull_request'][field]['sha'] = 'f' * 40
                if field == 'head':
                    self.metadata['headRefOid'] = 'f' * 40
                result = self.cli()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('REFUSED', result.stdout)
                self.event['pull_request'][field]['sha'] = original

    def test_unrelated_event_topology_refuses(self):
        self.git('checkout', '--orphan', 'disconnected')
        self.git('rm', '-rf', '--cached', '.')
        name = 'orphan.txt'
        (self.root / name).write_text('synthetic\n')
        self.commit('orphan', [name])
        head = self.git('rev-parse', 'HEAD')
        self.event['pull_request']['head']['sha'] = head
        self.metadata['headRefOid'] = head
        self.assertEqual(self.cli().returncode, 1)

    def test_local_explicit_body_and_non_pr(self):
        self.advance_and_merge()
        self.body.write_text(self.event['pull_request']['body'])
        result = self.cli(True, '--body-file', str(self.body), '--base', self.base)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('changed runtime target missing', result.stdout)
        self.git('checkout', '--detach', self.head)
        result = self.cli(True, '--body-file', str(self.body), '--base', self.base)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('not evaluated', self.cli(False).stdout)

    def test_real_cli_stale_metadata_refuses(self):
        for field, stale in [('body', 'old body'), ('headRefOid', 'f' * 40)]:
            original = self.metadata[field]
            self.metadata[field] = stale
            result = self.cli()
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('stale PR event', result.stdout)
            self.metadata[field] = original


if __name__ == '__main__':
    unittest.main()
