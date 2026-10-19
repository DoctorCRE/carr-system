#!/usr/bin/env python3
"""Required package checks must execute tests through the local CI entrypoint."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('mcp-server', 'control-room', 'workspace', 'practice-plugin')


class UnitClassTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'ops/config').mkdir(parents=True)
        (self.root / 'bin').mkdir()
        shutil.copy2(ROOT / 'bin/with-timeout.py', self.root / 'bin/with-timeout.py')
        for name in ('ci.sh', 'ci-quarantine.py', 'git_env.py', 'ci-unit.py', 'node_test_summary.py',
                     'ci-secret-scan.py', 'pii_guard.py'):
            source = ROOT / 'ops' / name
            if source.exists():
                shutil.copy2(source, self.root / 'ops' / name)
        (self.root / 'ops/config/ci-quarantine.json').write_text('{"version":1,"tests":[]}')
        (self.root / 'ops/config/ci-check-scope.json').write_text('{}')
        shutil.copy2(ROOT / 'ops/config/public-source-identities.v1.json',
                     self.root / 'ops/config/public-source-identities.v1.json')
        self.env = fixture_env()
        subprocess.run(['git', 'init', '-q', str(self.root)], env=self.env, check=True)
        subprocess.run(['git', 'add', 'ops/ci.sh'], cwd=self.root, env=self.env, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'fixture'], cwd=self.root, env=self.env, check=True)

    def run_class(self):
        return subprocess.run(['bash', str(self.root / 'ops/ci.sh'), '--strict', '--only', 'unit'],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

    def package(self, name, body="test('executes', () => {});"):
        path = self.root / name
        (path / 'test').mkdir(parents=True)
        (path / 'package.json').write_text(json.dumps({'scripts': {'test': 'node --test test/*.test.mjs'}}))
        (path / 'test/suite.test.mjs').write_text("import test from 'node:test';\n" + body + '\n')

    def test_empty_class_is_red(self):
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL', result.stdout)
        self.assertIn('mcp-server', result.stdout + result.stderr)

    def all_packages(self):
        for name in PACKAGES:
            self.package(name)

    def test_all_required_packages_execute_tests(self):
        self.all_packages()
        result = self.run_class()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('4 tests, 4 passed', result.stdout)
        for name in PACKAGES:
            self.assertIn(f'OK unit {name}: 1 tests, 1 passed', result.stdout)

    def test_one_missing_package_fails(self):
        for name in PACKAGES[:-1]:
            self.package(name)
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('practice-plugin: required package.json is missing', result.stderr)

    def test_zero_tests_fails_even_when_node_exits_zero(self):
        self.all_packages()
        (self.root / 'workspace/test/suite.test.mjs').write_text('')
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('workspace: zero passing tests', result.stderr)

    def test_skipped_only_package_fails_even_when_node_exits_zero(self):
        self.all_packages()
        (self.root / 'workspace/test/suite.test.mjs').write_text(
            "import test from 'node:test'; test.skip('not executed', () => {});")
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('workspace: zero passing tests', result.stderr)

    def test_failed_test_fails_the_class(self):
        self.all_packages()
        (self.root / 'workspace/test/suite.test.mjs').write_text(
            "import test from 'node:test'; test('broken', () => { throw Error('seeded defect'); });")
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('workspace: command exited 1', result.stderr)

    def test_exit_zero_without_test_output_fails(self):
        self.all_packages()
        (self.root / 'workspace/package.json').write_text(
            json.dumps({'scripts': {'test': 'node -e "console.log(123)"'}}))
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('workspace: no complete test counts', result.stderr)

    def test_missing_runner_fails(self):
        self.all_packages()
        (self.root / 'ops/ci-unit.py').unlink()
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_mcp_quarantine_receipts_report_executed_tests(self):
        self.all_packages()
        mcp = self.root / 'mcp-server'
        shutil.copy2(ROOT / 'mcp-server/run-tests.mjs', mcp / 'run-tests.mjs')
        (mcp / 'test/suite.test.mjs').rename(mcp / 'test/workspace-command-center-browser.test.mjs')
        (mcp / 'package.json').write_text(json.dumps({'scripts': {'test': 'node run-tests.mjs'}}))
        self.env['CARR_CI_PYTHON'] = shutil.which('python3')
        result = self.run_class()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('mcp-server: 1 tests, 1 passed', result.stdout)

    def test_package_skip_exit_is_red(self):
        self.all_packages()
        (self.root / 'workspace/package.json').write_text(
            json.dumps({'scripts': {'test': 'node -e "process.exit(78)"'}}))
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('workspace: command exited 78', result.stderr)

    def test_partial_individual_skips_are_reported(self):
        self.all_packages()
        (self.root / 'workspace/test/suite.test.mjs').write_text(
            "import test from 'node:test'; test('executes', () => {}); test.skip('migration case', () => {});")
        result = self.run_class()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('workspace: 2 tests, 1 passed, 1 skipped', result.stdout)

    def test_tap_reporter_counts_executed_tests(self):
        self.all_packages()
        (self.root / 'workspace/package.json').write_text(
            json.dumps({'scripts': {'test': 'node --test --test-reporter=tap test/*.test.mjs'}}))
        result = self.run_class()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('workspace: 1 tests, 1 passed', result.stdout)

    def test_tap_empty_file_is_not_a_test(self):
        self.all_packages()
        (self.root / 'workspace/package.json').write_text(
            json.dumps({'scripts': {'test': 'node --test --test-reporter=tap test/*.test.mjs'}}))
        (self.root / 'workspace/test/suite.test.mjs').write_text('')
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('workspace: zero passing tests', result.stderr)

    def test_mcp_skipped_only_receipts_fail(self):
        self.all_packages()
        mcp = self.root / 'mcp-server'
        shutil.copy2(ROOT / 'mcp-server/run-tests.mjs', mcp / 'run-tests.mjs')
        (mcp / 'test/suite.test.mjs').rename(mcp / 'test/workspace-command-center-browser.test.mjs')
        (mcp / 'test/workspace-command-center-browser.test.mjs').write_text(
            "import test from 'node:test'; test.skip('missing dependency', () => {});")
        (mcp / 'package.json').write_text(json.dumps({'scripts': {'test': 'node run-tests.mjs'}}))
        self.env['CARR_CI_PYTHON'] = shutil.which('python3')
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('mcp-server: zero passing tests', result.stderr)


if __name__ == '__main__':
    unittest.main()
