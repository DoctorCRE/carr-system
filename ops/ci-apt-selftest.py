#!/usr/bin/env python3
"""System setup uses signed apt and verifies actual document executables."""
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('apt_setup', Path(__file__).with_name('ci-apt.py'))
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
            self.assertLessEqual(call.kwargs['timeout'], 120)
        self.assertNotIn('--no-download', str(commands))
        self.assertNotIn('Dir::Cache', str(commands))

    def test_source_and_missing_tool_fail_before_tests(self):
        for failure_at in (0, 2, 3):
            def run(command, **kwargs):
                if len(commands) == failure_at:
                    raise subprocess.CalledProcessError(100, command)
                commands.append(command)
                return subprocess.CompletedProcess(command, 0)
            commands = []
            with patch.object(apt.subprocess, 'run', side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    apt.main(['--documents'])
            self.assertEqual(len(commands), failure_at)


if __name__ == '__main__':
    unittest.main()
