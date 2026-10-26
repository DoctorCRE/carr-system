#!/usr/bin/env python3
"""Exercise contract checks through the CI command in disposable Git trees."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class ContractClassTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='carr-contract-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'ops/config').mkdir(parents=True)
        for name in ('ci.sh', 'ci-contract.py', 'ci-secret-scan.py', 'pii_guard.py'):
            source = ROOT / 'ops' / name
            if source.is_file():
                shutil.copy2(source, self.root / 'ops' / name)
        shutil.copy2(ROOT / 'ops/config/public-source-identities.v1.json',
                     self.root / 'ops/config/public-source-identities.v1.json')
        self.env = fixture_env()
        subprocess.run(['git', 'init', '-q', str(self.root)], env=self.env, check=True)
        self.write('fixture.py', 'value = 1\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        subprocess.run(['git', 'add', name], cwd=self.root, env=self.env, check=True)

    def run_class(self):
        return subprocess.run(['bash', 'ops/ci.sh', '--strict', '--only', 'contract'],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15)

    def test_python_contract_command_passes_without_optional_contracts(self):
        result = subprocess.run([sys.executable, 'ops/ci-contract.py'], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_python_runner_fails_the_class(self):
        (self.root / 'ops/ci-contract.py').unlink()
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL', result.stdout)
        self.assertIn('contract checks failed', result.stdout)

    def test_absent_optional_contracts_pass(self):
        result = self.run_class()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('workspace + control-room contracts validate', result.stdout)

    def test_platform_path_policy_excludes_only_the_resolver(self):
        path = '/opt/homebrew/opt/' + 'postgresql@17/bin'
        self.write('lib/disposable_pg_fixture.py', repr(path))
        self.write('notes.txt', path)
        self.assertEqual(self.run_class().returncode, 0)
        self.write('other.py', repr(path))
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('PostgreSQL paths must use lib/disposable_pg_fixture.py (grep exit 0)', result.stderr)
        self.assertIn('other.py:1:', result.stderr)

    def test_git_read_failure_cannot_pass(self):
        shutil.rmtree(self.root / '.git')
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('PostgreSQL paths must use lib/disposable_pg_fixture.py (grep exit 128)', result.stderr)

    def test_all_existing_contracts_run_after_failures(self):
        self.write('workspace/contracts/validate.mjs', "throw Error('workspace failed');\n")
        for name in ('contracts', 'work-request-projection'):
            self.write(f'control-room/test/{name}.test.mjs',
                       f"import test from 'node:test'; test('{name}', () => {{ throw Error('{name} failed'); }});\n")
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for label in ('workspace/contracts', 'control-room/test/contracts.test.mjs',
                      'control-room/test/work-request-projection.test.mjs'):
            self.assertIn(label, result.stdout + result.stderr)
        for diagnostic in ('workspace failed', 'contracts failed', 'work-request-projection failed'):
            self.assertIn(diagnostic, result.stderr)

    def test_present_passing_contracts_pass(self):
        self.write('workspace/contracts/validate.mjs', "console.log('valid');\n")
        for name in ('contracts', 'work-request-projection'):
            self.write(f'control-room/test/{name}.test.mjs',
                       "import test from 'node:test'; test('contract', () => {});\n")
        result = self.run_class()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('workspace + control-room contracts validate', result.stdout)


if __name__ == '__main__':
    unittest.main()
