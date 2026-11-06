#!/usr/bin/env python3
"""System setup uses signed apt and verifies actual document executables."""
import contextlib
import io
import os
import sys
import tempfile
import time
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('apt_setup', Path(__file__).with_name('ci-apt.py'))
assert SPEC is not None and SPEC.loader is not None
apt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apt)


class SystemDependencies(unittest.TestCase):
    def test_documents_acquired_then_executed(self):
        with patch.object(apt.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(apt.main(['--documents']), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn('update', commands[0])
        self.assertIn('libreoffice-calc', commands[1])
        self.assertEqual(commands[2:], [['pdftotext', '-v'], ['libreoffice', '--version']])
        for call in run.call_args_list:
            self.assertTrue(call.kwargs['check'])
            self.assertLessEqual(call.kwargs['timeout'], 3610)
        self.assertNotIn('--no-download', str(commands))
        self.assertNotIn('Dir::Cache', str(commands))

    def test_slow_first_fetch_then_native_retry_succeeds(self):
        attempts = []
        def run(command, **kwargs):
            if 'install' in command:
                # Model apt's first 15s stall followed by the observed slow payload.
                attempts.append('slow first fetch')
                if 'Acquire::Retries=2' not in command:
                    raise subprocess.CalledProcessError(100, command)
                if kwargs['timeout'] < 3300:
                    raise subprocess.TimeoutExpired(command, kwargs['timeout'])
                attempts.append('retry succeeds')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(apt.subprocess, 'run', side_effect=run):
            self.assertEqual(apt.main(['--documents']), 0)
        self.assertEqual(attempts, ['slow first fetch', 'retry succeeds'])

    def test_permanent_mirror_failure_is_annotated_and_bounded(self):
        for error in (subprocess.CalledProcessError(100, ['apt-get']),
                      subprocess.TimeoutExpired(['apt-get'], 3600)):
            output = io.StringIO()
            with patch.object(apt.subprocess, 'run', side_effect=error) as run, contextlib.redirect_stderr(output):
                self.assertNotEqual(apt.main(['--documents']), 0)
            self.assertIn('::error::', output.getvalue())
            self.assertEqual(run.call_count, 1)
            self.assertLessEqual(run.call_args.kwargs['timeout'], 3610)

    def test_permanent_mirror_cli_exits_promptly(self):
        with tempfile.TemporaryDirectory() as directory:
            sudo = Path(directory) / 'sudo'
            sudo.write_text('#!' + sys.executable + "\nimport sys\nsys.exit(100)\n")
            sudo.chmod(0o755)
            timeout = Path(directory) / 'timeout'
            timeout.write_text('#!' + sys.executable + "\nimport os,sys\nos.execvp(sys.argv[3], sys.argv[3:])\n")
            timeout.chmod(0o755)
            start = time.monotonic()
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('ci-apt.py')), '--documents'],
                                    env={**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH']},
                                    capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('::error::', result.stderr)
            self.assertLess(time.monotonic() - start, 5)

    def test_documents_follow_current_shard_assignments(self):
        from ci_unit_shards import plan
        original = subprocess.run
        def run_command(command, **kwargs):
            if command[0] == 'node':
                return original(command, **kwargs)
            return subprocess.CompletedProcess(command, 0)
        for shard, suites in enumerate(plan(), 1):
            with patch.object(apt.subprocess, 'run', side_effect=run_command) as run:
                self.assertEqual(apt.main(['--unit-shard', str(shard)]), 0)
            self.assertEqual(any('libreoffice-calc' in c.args[0] for c in run.call_args_list),
                             'fact-store-postgres.test.mjs' in suites)

    def test_source_and_missing_tool_fail_before_tests(self):
        for failure_at in (0, 1, 2, 3):
            def run(command, **kwargs):
                if len(commands) == failure_at:
                    raise subprocess.CalledProcessError(100, command)
                commands.append(command)
                return subprocess.CompletedProcess(command, 0)
            commands = []
            with patch.object(apt.subprocess, 'run', side_effect=run):
                self.assertNotEqual(apt.main(['--documents']), 0)
            self.assertEqual(len(commands), failure_at)


if __name__ == '__main__':
    unittest.main()
