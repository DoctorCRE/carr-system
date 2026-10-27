#!/usr/bin/env python3
# Characterize gate orchestration through the canonical CI command.
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location('ci_fixture', Path(__file__).with_name('ci-selftest.py'))
assert spec is not None and spec.loader is not None
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class GatesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = ci.ci_fixture()
        self.root = self.fixture.__enter__()
        (self.root / 'logs').mkdir()
        (self.root / 'hooks/gate-integrity.py').write_text('print("safety installed")\n')
        (self.root / 'tools/test_migration_fixture.py').write_text('# Run a synthetic tool.\nprint("tool passed")\n')

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def suite(self, name, source):
        path = self.root / name
        path.write_text(source + '\n')
        path.chmod(0o755)

    def run_ci(self, *args, settings=None):
        result = subprocess.run(['bash', 'ops/ci.sh', '--strict', '--only', 'gates', *args],
                                cwd=self.root, env={**ci.fixture_env(), 'RUNNER_TEMP': str(self.root / 'logs'), **(settings or {})},
                                capture_output=True, text=True, errors='replace', timeout=30)
        output = ci.ANSI.sub('', result.stdout + result.stderr)
        return result.returncode, output

    def test_pass_and_temporary_fixture_isolation(self):
        self.suite('ops/a-selftest.py',
                   'import os,tempfile\nfrom pathlib import Path\n'
                   'assert os.environ["CARR_HOOK_FIXTURE"] == "1"\n'
                   'assert os.environ["CI_SELFTEST_TIMEOUT_SECONDS"] == "120"\n'
                   'assert Path(os.environ["LOGDIR"]).is_dir()\n'
                   'assert Path(os.environ["CI_TIMEOUT_HELPER"]).is_file()\n'
                   'assert os.environ["pooled_timeout"] == "480"\n'
                   'assert os.environ["PY"]\n'
                   'with tempfile.TemporaryDirectory() as d: assert "tmp-a-selftest.py" in d\n'
                   'print("suite passed")')
        rc, output = self.run_ci()
        self.assertEqual(rc, 0, output)
        self.assertIn('OK    gates       2 selftest suites + safety hook installation', output)
        self.assertIn('suite passed', output)

    def test_suite_can_remove_its_empty_temporary_directory(self):
        self.suite('ops/remove-selftest.py',
                   'import tempfile\nfrom pathlib import Path\nPath(tempfile.gettempdir()).rmdir()')
        rc, output = self.run_ci()
        self.assertEqual(rc, 0, output)
        self.assertIn('2 selftest suites + safety hook installation', output)

    def test_leaked_fixture_fails_and_keeps_log(self):
        self.suite('ops/leak-selftest.py',
                   'import tempfile\nfrom pathlib import Path\nPath(tempfile.gettempdir(), "leftover").touch()')
        rc, output = self.run_ci()
        self.assertEqual(rc, 1, output)
        self.assertIn('leaked temporary fixtures: leftover', output)
        self.assertIn('failed: leak-selftest.py', output)
        directory = Path(re.search(r'ci-retained-logs: (.+)', output)[1])
        self.assertEqual((directory / 'gate-leak-selftest.py.rc').read_text().strip(), '75')
        self.assertFalse((directory / 'tmp-leak-selftest.py').exists())

    def test_owned_and_local_scopes_preserve_summary(self):
        self.suite('ops/owner.py', '')
        self.suite('ops/owned-selftest.py', 'raise AssertionError("must not run")')
        self.suite('ops/local-selftest.py', 'raise AssertionError("must not run")')
        (self.root / 'ops/config/ci-check-scope.json').write_text(json.dumps({
            'run_by': [{'check': 'owned-selftest.py', 'owner': 'owner.py', 'reason': 'nested proof'}],
            'local_only': [{'check': 'local-selftest.py', 'reason': 'machine proof'}]}))
        rc, output = self.run_ci(settings={'CARR_CI_PORTABLE_ONLY': '1'})
        self.assertEqual(rc, 0, output)
        self.assertIn('owned    owned-selftest.py', output)
        self.assertIn('NOT RUN: local-selftest.py', output)
        self.assertNotIn('NOT RUN: owned', output)

    def test_unconfigured_python_is_reported_without_class_skip(self):
        self.suite('ops/skip-selftest.py', 'print("fixture unavailable"); raise SystemExit(78)')
        rc, output = self.run_ci()
        self.assertEqual(rc, 0, output)
        self.assertIn('NOT CONFIGURED (exit 78): fixture unavailable', output)
        self.assertIn('NOT RUN: skip-selftest.py', output)

    def test_unconfigured_suite_output_can_contain_arbitrary_bytes(self):
        self.suite('ops/bytes-selftest.py',
                   'import sys\nsys.stdout.buffer.write(bytes([255, 10])); raise SystemExit(78)')
        rc, output = self.run_ci()
        self.assertEqual(rc, 0, output)
        self.assertIn('NOT CONFIGURED (exit 78):', output)
        self.assertIn('NOT RUN: bytes-selftest.py', output)

    def test_tree_mutation_is_failure(self):
        self.suite('ops/mutate-selftest.py',
                   'from pathlib import Path\nPath("requirements.txt").write_text("changed\\n")')
        rc, output = self.run_ci()
        self.assertEqual(rc, 1, output)
        self.assertIn('tree-mutated-by-selftests', output)
        self.assertIn('CHANGED THE WORKING TREE', output)

    def test_shell_failure_and_timeout_stop_order(self):
        self.suite('tools/test-a.sh', '#!/bin/sh\necho shell-failure\nexit 7')
        self.suite('tools/test-b.sh', '#!/bin/sh\necho shell-timeout\nexit 124')
        self.suite('tools/test-c.sh', '#!/bin/sh\ntouch shell-ran')
        rc, output = self.run_ci()
        self.assertEqual(rc, 1, output)
        self.assertIn('failed: test-a.sh TIMEOUT:test-b.sh', output)
        self.assertIn('aborting remaining gate selftests', output)
        self.assertFalse((self.root / 'shell-ran').exists())

    def test_signaled_invariant_keeps_shell_exit_status(self):
        self.suite('ops/stale-count-check.py',
                   'import os,signal\nos.kill(os.getpid(), signal.SIGTERM)')
        rc, output = self.run_ci()
        self.assertEqual(rc, 1, output)
        directory = Path(re.search(r'ci-retained-logs: (.+)', output)[1])
        self.assertEqual((directory / 'gate-stale-count-check.log.rc').read_text(), '143\n')

    def test_clock_scan_checks_changed_tests(self):
        self.suite('mcp-server/test-clock.mjs', 'Date.now()')
        path = self.root / 'mcp-server/test'
        path.mkdir()
        (path / 'clock.test.mjs').write_text('new Date()\n')
        rc, output = self.run_ci()
        self.assertEqual(rc, 1, output)
        self.assertIn('failed: test-clocks', output)


if __name__ == '__main__':
    unittest.main()
