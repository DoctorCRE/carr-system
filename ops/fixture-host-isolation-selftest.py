#!/usr/bin/env python3
"""Run installer proofs with hostile parent host state and deny native launchctl."""
import fcntl
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

    def test_orphan_reaper_proof_is_independent_of_host(self):
        self.run_suite('orphan-reaper-selftest.py')

    def test_watchdog_streaming_proof_ignores_parent_capacity(self):
        with tempfile.TemporaryDirectory(prefix='carr-parent-slots-') as temp:
            root = Path(temp)
            with (root / 'slot-0.lock').open('a') as occupied:
                fcntl.flock(occupied, fcntl.LOCK_EX)
                env = dict(fixture_env(), CARR_CODEX_SLOT_DIR=str(root),
                           CARR_CODEX_MAX_CONCURRENT='1')
                result = subprocess.run([sys.executable, str(ROOT / 'tools/test_job_watchdog.py'),
                                         'RunnerTests.test_model_room_streams_progress_before_completion'],
                                        env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
