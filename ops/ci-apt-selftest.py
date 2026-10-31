#!/usr/bin/env python3
"""Exercise CI package setup against runner capabilities and apt failures."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SystemDependencies(unittest.TestCase):
    def invoke(self, directory, installed, run):
        with patch.dict(os.environ, CARR_CI_APT_ARCHIVES=str(directory / 'archives'),
                        GITHUB_PATH=str(directory / 'path')), \
             patch('sys.argv', ['ci-apt.py', '--postgres', '--documents']), \
             patch('pathlib.Path.is_file', return_value=installed), \
             patch('shutil.which', return_value='/usr/bin/tool' if installed else None), \
             patch('subprocess.run', run):
            runpy.run_path(str(ROOT / 'ops/ci-apt.py'), run_name='__main__')

    def test_existing_runner_packages_skip_apt_and_publish_postgres_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            from unittest.mock import Mock
            run = Mock()
            self.invoke(directory, True, run)
            run.assert_not_called()
            self.assertEqual((directory / 'path').read_text(), '/usr/lib/postgresql/17/bin\n')

    def test_missing_packages_use_archives_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            from unittest.mock import Mock
            run = Mock()
            self.invoke(directory, False, run)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], ['bash', 'ops/ci-postgres.sh'])
            self.assertEqual(len(commands), 3)
            self.assertIn(f'Dir::Cache::archives={directory / "archives"}', commands[2])
            self.assertIn('APT::Keep-Downloaded-Packages=true', commands[2])
            self.assertIn('libreoffice-calc', commands[2])
            for call in run.call_args_list:
                self.assertTrue(call.kwargs['check'])
                self.assertLessEqual(call.kwargs['timeout'], 240)
            run.side_effect = subprocess.CalledProcessError(1, 'apt-get')
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke(directory, False, run)


if __name__ == '__main__':
    unittest.main()
