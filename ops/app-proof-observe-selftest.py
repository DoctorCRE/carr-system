#!/usr/bin/env python3
"""Exercise the retained post-deploy app observer and its registered source pin."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / 'contracts/production-proof-plan.v1.json'


def runner_source(runner, root=ROOT):
    if 'sources' in runner:
        sources, path = runner['sources'], runner.get('path')
        if ('revision' in runner or not isinstance(sources, dict) or not 1 <= len(sources) <= 16
                or not isinstance(path, str) or path not in sources):
            raise ValueError('runner requires exactly one complete source binding')
        entry = None
        for name, digest in sources.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', name)
                    or any(p in {'', '.', '..'} for p in name.split('/'))
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise ValueError('runner source path or SHA256 invalid')
            metadata = subprocess.run(['git', 'ls-tree', '-l', 'HEAD', '--', name],
                                      cwd=root, env=fixture_env(), capture_output=True, timeout=15)
            fields = metadata.stdout.split()
            if (metadata.returncode or len(fields) != 5 or fields[0] not in {b'100644', b'100755'}
                    or fields[1] != b'blob' or not fields[3].isdigit()):
                raise ValueError(f'runner source {name}: missing or not a regular file')
            if int(fields[3]) > 1048576:
                raise ValueError(f'runner source {name}: exceeds 1 MiB')
            source = subprocess.run(['git', 'show', 'HEAD:' + name], cwd=root,
                                    env=fixture_env(), capture_output=True, timeout=15)
            if source.returncode or hashlib.sha256(source.stdout).hexdigest() != digest:
                raise ValueError(f'runner source digest mismatch: {name}')
            if name == path:
                entry = source.stdout
        return entry
    revision, path = runner['revision'], runner['path']
    ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', revision, 'HEAD'],
                              cwd=root, env=fixture_env(), capture_output=True, timeout=15)
    if ancestry.returncode:
        reason = 'not an ancestor of tested HEAD' if ancestry.returncode == 1 else 'unresolvable revision or tested HEAD'
        raise ValueError(f"runner pin {revision}: {reason}")
    source = subprocess.run(['git', 'show', revision + ':' + path],
                            cwd=root, env=fixture_env(), capture_output=True, timeout=15)
    if source.returncode:
        raise ValueError(f"runner pin {revision}: {path}: unresolvable path")
    return source.stdout


class RunnerRegistryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_runner_registry_pins_and_studio_observer(self):
        contract = json.loads(CONTRACT_PATH.read_text())
        sources = {name: runner_source(runner) for name, runner in contract['x-runners'].items()}
        source = sources['studio-run-receipt.v1']
        self.assertEqual(hashlib.sha256(source).hexdigest(), '8bfffa61e72f0cf7c551d10f17e4cc6c158e625efa616c84db56808e5e844068')
        canonical = contract['x-runners']['studio-canonical-run.v1']
        self.assertEqual(set(canonical['sources']), {
            'ops/studio-proof-observe.py', 'lib/studio_proof.py',
            'tools/ops-record.py', 'lib/credential_file.py'})
        self.assertEqual(sources['studio-canonical-run.v1'], (ROOT / canonical['path']).read_bytes())
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

    def test_wr823_canonical_proof_rejects_empty_mail_ids(self):
        requirement = json.loads((ROOT / 'proofs/requirements/wr823-s2-canonical-intake-run.json').read_text())
        assertion = next(a for a in requirement['assertions'] if a['id'] == 'next-installed-intake-canonical-run')
        match = re.search(r'run\.mail_ids minimum (\d+)', assertion['predicate'])
        self.assertIsNotNone(match)
        minimum = match.group(1)
        contract = json.loads(CONTRACT_PATH.read_text())
        observer = self.root / 'observer.py'
        observer.write_bytes(runner_source(contract['x-runners']['studio-run-receipt.v1']))
        receipt = self.root / 'receipt.txt'
        receipt.write_text('outlook-intake-receipt ' + json.dumps({
            'state': 'succeeded', 'runtime_sha': 'a' * 40, 'counts': {'run': {'mail_ids': 0}}}))
        result = subprocess.run([sys.executable, str(observer), '--receipt-file', str(receipt),
            '--runtime-sha', 'a' * 40, '--service', 'outlook-intake',
            '--count', 'run.mail_ids', '--minimum', minimum],
            capture_output=True, text=True, timeout=10, env=fixture_env())
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('receipt count does not meet declared minimum', result.stdout)


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

    def test_registered_app_runner_preserves_human_pending(self):
        contract = json.loads(CONTRACT_PATH.read_text())
        source = runner_source(contract['x-runners']['app-release-live-walk.v1'])
        path = self.repo / 'pinned-observer.py'
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location('pinned_app_observer', path)
        observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observer)
        receipt = observer.observe(self.repo, self.merge,
            human_steps=['Joe performs the signed-in Home bulk-settle and undo walk'],
            fetch=lambda path, timeout, limit: self.identity)
        self.assertEqual(receipt['status'], 'human_pending')
        self.assertEqual(receipt['assertions'], [])
        self.assertEqual(receipt['human_steps'], ['Joe performs the signed-in Home bulk-settle and undo walk'])

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


if __name__ == "__main__":
    unittest.main()
