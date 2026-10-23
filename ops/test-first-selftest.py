#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class Footprint(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-first-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = fixture_env()
        self.git('init', '-q')
        self.write('lib/value.py', 'VALUE = 1\n')
        self.base = self.commit('lib/value.py')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args],
                                       env=self.env, text=True).strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, *names):
        self.git('add', '--', *names)
        self.git('-c', 'user.name=CI fixture', '-c', 'user.email=jbookout@users.noreply.github.com',
                 'commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def run_check(self, head):
        summary = self.repo / 'summary.txt'
        result = subprocess.run([sys.executable, str(ROOT / 'ops/ci-test-first.py'),
                                 '--base', self.base, '--head', head], cwd=self.repo,
                                env={**self.env, 'GITHUB_STEP_SUMMARY': str(summary)},
                                capture_output=True, text=True, timeout=30)
        return result, summary.read_text() if summary.exists() else ''

    def test_tests_that_pass_on_base_are_refused(self):
        self.write('lib/value.py', 'VALUE = 2\n')
        self.write('ops/probe-selftest.py', 'assert 1 + 1 == 2\n')
        result, summary = self.run_check(self.commit('lib/value.py', 'ops/probe-selftest.py'))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("these tests don't test the change", result.stdout)
        self.assertIn('base: PASS', summary)
        self.assertIn('head: PASS', summary)

    def test_head_tests_fail_on_base_and_pass_on_the_new_implementation(self):
        self.write('lib/value.py', 'VALUE = 2\n')
        self.write('ops/probe-selftest.py',
                   "from pathlib import Path\nassert (Path('lib/value.py').read_text()) == 'VALUE = 2\\n'\n")
        head = self.commit('lib/value.py', 'ops/probe-selftest.py')
        result, summary = self.run_check(head)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('base: FAIL', summary)
        self.assertIn('head: PASS', summary)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_head_failure_cannot_be_accepted_as_test_first(self):
        self.write('lib/value.py', 'VALUE = 2\n')
        self.write('ops/probe-selftest.py', 'assert False, "still broken"\n')
        result, summary = self.run_check(self.commit('lib/value.py', 'ops/probe-selftest.py'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('base: FAIL', summary)
        self.assertIn('head: FAIL', summary)

    def test_docs_config_pure_deletion_and_no_tests_are_not_applicable(self):
        for changed in ('docs/change.md', 'ops/config/change.json', 'lib/value.py', 'lib/new.py'):
            with self.subTest(changed=changed):
                self.git('checkout', '--detach', '-q', self.base)
                self.write('ops/probe-selftest.py', 'assert False\n')
                if changed == 'lib/value.py':
                    (self.repo / changed).unlink()
                else:
                    self.write(changed, '{}\n')
                names = [changed] if changed == 'lib/new.py' else [changed, 'ops/probe-selftest.py']
                result, summary = self.run_check(self.commit(*names))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('not applicable', summary)

    def test_modified_tests_overlay_without_copying_the_head_implementation(self):
        self.write('tools/test_value.py',
                   "from pathlib import Path\nassert Path('lib/value.py').read_text() == 'VALUE = 1\\n'\n")
        self.base = self.commit('tools/test_value.py')
        self.write('lib/value.py', 'VALUE = 2\n')
        self.write('tools/test_value.py',
                   "from pathlib import Path\nassert Path('lib/value.py').read_text() == 'VALUE = 2\\n'\n")
        result, summary = self.run_check(self.commit('tools/test_value.py', 'lib/value.py'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('base: FAIL', summary)
        self.assertIn('head: PASS', summary)

    def test_node_and_shell_tests_use_the_same_base_head_comparison(self):
        self.write('lib/value.py', 'VALUE = 2\n')
        self.write('mcp-server/test/value.test.mjs',
                   "import {readFileSync} from 'node:fs';\nimport assert from 'node:assert/strict';\n"
                   "assert.equal(readFileSync('lib/value.py','utf8'),'VALUE = 2\\n');\n")
        self.write('tools/test_value.sh', "test \"$(cat lib/value.py)\" = 'VALUE = 2'\n")
        result, summary = self.run_check(self.commit('mcp-server/test/value.test.mjs',
                                                    'tools/test_value.sh', 'lib/value.py'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('2 changed test files', summary)

    def test_pytest_module_is_collected_instead_of_exiting_without_tests(self):
        self.write('lib/value.py', 'VALUE = 2\n')
        self.write('tools/test_value.py',
                   "import pytest\nfrom pathlib import Path\n"
                   "def test_new_value():\n    assert Path('lib/value.py').read_text() == 'VALUE = 2\\n'\n")
        result, summary = self.run_check(self.commit('tools/test_value.py', 'lib/value.py'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('base: FAIL', summary)
        self.assertIn('head: PASS', summary)

    def test_required_verdict_fails_for_every_non_success_footprint_result(self):
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        aggregate = workflow['jobs']['checks']
        self.assertIn('test-first', aggregate['needs'])
        gate = next(step for step in aggregate['steps'] if step.get('name') == 'Require test-first footprint')
        for result in ('success', 'failure', 'skipped', 'cancelled', ''):
            status = subprocess.run(['bash', '-e', '-c', gate['run']],
                                    env={**self.env, 'TEST_FIRST_RESULT': result}).returncode
            self.assertEqual(status == 0, result == 'success')

    def test_extensionless_executable_is_a_source_change(self):
        self.write('bin/probe', '#!/bin/sh\necho old\n')
        (self.repo / 'bin/probe').chmod(0o755)
        self.base = self.commit('bin/probe')
        self.write('bin/probe', '#!/bin/sh\necho new\n')
        self.write('ops/probe-selftest.py',
                   "from pathlib import Path\nassert 'new' in Path('bin/probe').read_text()\n")
        result, summary = self.run_check(self.commit('bin/probe', 'ops/probe-selftest.py'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('base: FAIL', summary)
        self.assertIn('head: PASS', summary)


if __name__ == '__main__':
    unittest.main()
