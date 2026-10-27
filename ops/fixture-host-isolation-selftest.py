#!/usr/bin/env python3
"""Sole gate owner of two installer proofs: runs each under hostile parent host
state with native launchctl denied (ops/config/ci-check-scope.json run_by)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class HostIsolation(unittest.TestCase):
    def run_suite(self, name):
        with tempfile.TemporaryDirectory(prefix='carr-host-isolation-') as temp:
            root = Path(temp)
            holds = root / '.config/carr/launchd-hold'
            holds.parent.mkdir(parents=True)
            holds.write_text('local.fixture-parent-held synthetic parent hold\n')
            native_calls = root / 'native-launchctl.log'
            launchctl = root / 'launchctl'
            launchctl.write_text(f'#!/bin/sh\necho "$*" >> "{native_calls}"\nexit 99\n')
            launchctl.chmod(0o700)
            env = dict(fixture_env(), HOME=str(root), PATH=str(root) + os.pathsep + os.environ['PATH'])
            result = subprocess.run([sys.executable, str(ROOT / 'ops' / name)],
                                    env=env, capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
            self.assertFalse(native_calls.exists(), native_calls.read_text() if native_calls.exists() else '')
            self.assertNotIn('local.fixture-parent-held', output)
            self.assertEqual(result.returncode, 0, output)
            self.assertEqual(holds.read_text(), 'local.fixture-parent-held synthetic parent hold\n')

    def test_calendar_installer_proof_is_independent_of_host(self):
        self.run_suite('reinstall-launchd-calendar-selftest.py')

    def test_config_reconciliation_proof_is_independent_of_host(self):
        self.run_suite('config-as-code-selftest.py')

    def test_gate_collector_leaves_these_suites_to_this_owner(self):
        scope = json.loads((ROOT / 'ops/config/ci-check-scope.json').read_text())
        owned = {e['check'] for e in scope.get('run_by', []) if e['owner'] == Path(__file__).name}
        self.assertEqual(owned, {'reinstall-launchd-calendar-selftest.py', 'config-as-code-selftest.py'})


if __name__ == '__main__':
    unittest.main()
