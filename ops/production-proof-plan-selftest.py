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
                      ['ops/example-selftest.py'], ['ops/app-proof-observe.py'], ['.github/workflows/ci.yml']]:
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


class AppObserverTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('app_observer', ROOT / 'ops/app-proof-observe.py')
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = fixture_env()
        self.env.update(GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                        GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
        self.git('init', '-q')
        self.git('-c', 'core.hooksPath=/dev/null', 'commit', '--allow-empty', '-qm', 'first')
        self.merge = self.git('rev-parse', 'HEAD')
        self.git('-c', 'core.hooksPath=/dev/null', 'commit', '--allow-empty', '-qm', 'served')
        self.served = self.git('rev-parse', 'HEAD')
        self.identity = json.dumps({'service': 'doctorcre-app', 'environment': 'production',
                                    'source_commit': self.served}).encode()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], env=self.env,
                                       text=True, stderr=subprocess.PIPE, timeout=10).strip()

    def observe(self, assertions=(), human_steps=(), fetch=None, merge=None):
        return self.observer.observe(self.repo, merge or self.merge, assertions, human_steps,
                                     fetch=fetch or (lambda path, timeout, limit: self.identity))

    def test_release_ancestry_and_public_marker(self):
        def fetch(path, timeout, limit):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 15)
            return self.identity if path == '/app-release' else b'export const Documents = true;'
        receipt = self.observe([('/js/documents.js', 'Documents')], ['Open the signed-in Documents tab'], fetch)
        self.assertEqual(receipt['source_commit'], self.served)
        self.assertEqual(receipt['merge_commit'], self.merge)
        self.assertEqual(receipt['status'], 'human_pending')
        self.assertEqual(receipt['human_steps'], ['Open the signed-in Documents tab'])
        self.assertEqual(receipt['assertions'][0]['path'], '/js/documents.js')
        self.assertEqual(self.observe()['status'], 'passed')

    def test_unknown_or_unreleased_commit_refuses(self):
        with self.assertRaisesRegex(ValueError, 'unknown|unavailable'):
            self.observe(merge='f' * 40)
        self.identity = json.dumps({'service': 'doctorcre-app', 'environment': 'production',
                                   'source_commit': self.merge}).encode()
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            self.observe(merge=self.served)

    def ancestry_cli(self, source, settings=None):
        curl = self.repo / 'curl'
        identity = json.dumps({'service': 'doctorcre-app', 'environment': 'production',
                               'source_commit': source})
        curl.write_text('#!' + sys.executable + '\nprint(' + repr(identity) + ')\n')
        curl.chmod(0o700)
        env = dict(self.env, PATH=str(self.repo) + os.pathsep + self.env.get('PATH', os.defpath))
        env.update(settings or {})
        return subprocess.run([sys.executable, str(ROOT / 'ops/app-proof-observe.py'),
            '--app-repo', str(self.repo), '--merge-sha', self.merge], env=env,
            capture_output=True, text=True, timeout=10)

    def unrelated_commit(self):
        self.git('checkout', '--orphan', 'unrelated')
        self.git('-c', 'core.hooksPath=/dev/null', 'commit', '--allow-empty', '-qm', 'unrelated')
        return self.git('rev-parse', 'HEAD')

    def assert_ancestry_refused(self, source, settings=None):
        result = self.ancestry_cli(source, settings)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('not a proven ancestor', result.stderr)

    def test_replacement_cannot_forge_cli_ancestry(self):
        unrelated = self.unrelated_commit()
        self.assert_ancestry_refused(unrelated)
        for namespace, settings in [('refs/replace/', {}), ('refs/custom/', {
                'GIT_REPLACE_REF_BASE': 'refs/custom/',
                'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.useReplaceRefs',
                'GIT_CONFIG_VALUE_0': 'true'})]:
            with self.subTest(namespace=namespace):
                ref = namespace + unrelated
                self.git('update-ref', ref, self.served)
                if not settings:
                    self.assertEqual(self.git('merge-base', '--is-ancestor', self.merge, unrelated), '')
                before = self.git('show-ref')
                self.assert_ancestry_refused(unrelated, settings)
                result = self.ancestry_cli(self.served, settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'passed')
                self.assertEqual(self.git('show-ref'), before)
                self.git('update-ref', '-d', ref)
        self.assert_ancestry_refused(unrelated)

    def test_grafts_cannot_forge_cli_ancestry(self):
        unrelated = self.unrelated_commit()
        for graft in [self.repo / '.git/info/grafts', self.repo / 'custom-grafts']:
            graft.parent.mkdir(exist_ok=True)
            data = unrelated + ' ' + self.merge + '\n'
            graft.write_text(data)
            settings = {} if graft.name == 'grafts' else {'GIT_GRAFT_FILE': str(graft)}
            with self.subTest(graft=graft.name):
                self.assert_ancestry_refused(unrelated, settings)
                result = self.ancestry_cli(self.served, settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(graft.read_text(), data)
            graft.unlink()

    def test_object_type_replacements_and_inherited_state(self):
        blob = subprocess.check_output(['git', '-C', str(self.repo), 'hash-object', '-w', '--stdin'],
            input=b'not a commit', env=self.env, timeout=10).decode().strip()
        self.git('replace', '-f', blob, self.served)
        self.identity = json.dumps({'service': 'doctorcre-app', 'environment': 'production',
                                    'source_commit': blob}).encode()
        with patch.dict(os.environ, self.env, clear=True):
            before = dict(os.environ)
            config = (self.repo / '.git/config').read_bytes()
            refs = self.git('show-ref')
            with self.assertRaisesRegex(ValueError, 'unknown|unavailable'):
                self.observe()
            self.assertEqual(dict(os.environ), before)
            self.assertEqual((self.repo / '.git/config').read_bytes(), config)
            self.assertEqual(self.git('show-ref'), refs)
        self.git('replace', '-d', blob)

    def test_real_history_ignores_overlays_and_caller_routing(self):
        unrelated = self.unrelated_commit()
        self.git('replace', self.served, unrelated)
        self.git('config', 'core.useReplaceRefs', 'true')
        with patch.dict(os.environ, {**self.env,
                'GIT_DIR': str(self.repo / 'nonexistent.git'),
                'GIT_OBJECT_DIRECTORY': str(self.repo / 'nonexistent-objects'),
                'GIT_NO_REPLACE_OBJECTS': '0',
                'GIT_CONFIG_PARAMETERS': "'core.useReplaceRefs=true'"}, clear=True):
            before = dict(os.environ)
            config = (self.repo / '.git/config').read_bytes()
            refs = self.git('show-ref')
            self.assertEqual(self.observe()['status'], 'passed')
            self.assertEqual(dict(os.environ), before)
            self.assertEqual((self.repo / '.git/config').read_bytes(), config)
            self.assertEqual(self.git('show-ref'), refs)

    def test_network_identity_and_marker_fail_closed(self):
        def offline(*args):
            raise OSError('network unavailable')
        with self.assertRaises(OSError):
            self.observe(fetch=offline)
        for identity in [b'{}', b'not JSON', json.dumps({'service': 'wrong',
                'environment': 'production', 'source_commit': self.served}).encode()]:
            self.identity = identity
            with self.assertRaises(ValueError):
                self.observe()
        self.identity = json.dumps({'service': 'doctorcre-app', 'environment': 'production',
                                    'source_commit': self.served}).encode()
        with self.assertRaisesRegex(ValueError, 'marker'):
            self.observe([('/js/example.js', 'absent')])

    def test_input_and_execution_budgets(self):
        for path in ['https://other.test/', '//other.test', '/../secret', '/%2e%2e/a', '/a?token=x']:
            with self.assertRaisesRegex(ValueError, 'path'):
                self.observe([(path, 'marker')])
        with self.assertRaisesRegex(ValueError, 'requests'):
            self.observe([('/a', 'x')] * 20)
        with self.assertRaisesRegex(ValueError, 'SHA'):
            self.observe(merge='HEAD')
        with self.assertRaisesRegex(ValueError, 'bytes'):
            self.observe(fetch=lambda *args: b'x' * 1048577)
        with patch.object(self.observer.time, 'monotonic', side_effect=[0, 301]):
            with self.assertRaisesRegex(ValueError, 'deadline'):
                self.observe()

    def test_curl_is_bounded_and_has_no_redirect_or_retry(self):
        with patch.object(self.observer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{}', b'')) as run:
            self.observer.public_get('/app-release', 4, 1234)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['curl', '-q'])
        self.assertIn('--max-time', command)
        self.assertIn('--max-filesize', command)
        self.assertNotIn('--location', command)
        self.assertNotIn('--retry', command)
        self.assertLessEqual(run.call_args.kwargs['timeout'], 5)
        with patch.object(self.observer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7, b'', b'offline')):
            with self.assertRaisesRegex(ValueError, 'GET failed'):
                self.observer.public_get('/app-release', 4, 1234)

    def test_cli_network_failure_and_timeout_are_nonzero(self):
        for failure in [ValueError('public GET failed'), subprocess.TimeoutExpired('curl', 4)]:
            with patch.object(self.observer, 'observe', side_effect=failure), \
                    patch.object(sys, 'argv', ['observer', '--app-repo', str(self.repo), '--merge-sha', self.merge]):
                self.assertEqual(self.observer.main(), 1)

        curl = self.repo / 'curl'
        curl.write_text('#!' + sys.executable + '\nimport sys\nsys.exit(7)\n')
        curl.chmod(0o700)
        settings = dict(self.env, PATH=str(self.repo) + os.pathsep + self.env.get('PATH', os.defpath))
        result = subprocess.run([sys.executable, str(ROOT / 'ops/app-proof-observe.py'),
            '--app-repo', str(self.repo), '--merge-sha', self.merge], env=settings,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('public GET failed for /app-release (curl 7)', result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
