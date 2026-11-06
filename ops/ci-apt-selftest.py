#!/usr/bin/env python3
"""Exercise CI package setup against runner capabilities and apt failures."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class SystemDependencies(unittest.TestCase):
    def invoke(self, directory, installed, run):
        with patch.dict(os.environ, CARR_CI_POSTGRES_IMAGE='', CARR_CI_APT_ARCHIVES=str(directory / 'archives'),
                        GITHUB_PATH=str(directory / 'path')), \
             patch('sys.argv', ['ci-apt.py', '--postgres', '--documents']), \
             patch('pathlib.Path.is_file', return_value=installed), \
             patch('shutil.which', return_value='/usr/bin/tool' if installed else None), \
             patch('subprocess.run', run):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(ROOT / 'ops/ci-apt.py'), run_name='__main__')
            self.assertEqual(result.exception.code, 0)

    def test_existing_runner_packages_skip_apt_and_publish_postgres_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = Mock()
            self.invoke(directory, True, run)
            run.assert_not_called()
            self.assertEqual((directory / 'path').read_text(), '/usr/lib/postgresql/17/bin\n')

    def test_cached_packages_install_without_refresh_or_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = Mock(return_value=SimpleNamespace(returncode=0))
            self.invoke(directory, False, run)
            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertIn('--no-download', command)
            self.assertIn('postgresql-17-pgvector', command)
            self.assertIn('libreoffice-calc', command)
            self.assertNotIn('update', command)
            self.assertEqual((directory / 'path').read_text(), '/usr/lib/postgresql/17/bin\n')

    def test_missing_packages_use_archives_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = Mock(return_value=SimpleNamespace(returncode=1))
            self.invoke(directory, False, run)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn('--no-download', commands[0])
            self.assertEqual(commands[1], ['bash', 'ops/ci-postgres.sh'])
            self.assertEqual(len(commands), 4)
            self.assertIn(f'Dir::Cache::archives={directory / "archives"}', commands[3])
            self.assertIn('APT::Keep-Downloaded-Packages=true', commands[3])
            self.assertIn('libreoffice-calc', commands[3])
            self.assertFalse(run.call_args_list[0].kwargs['check'])
            for call in run.call_args_list[1:]:
                self.assertTrue(call.kwargs['check'])
                self.assertLessEqual(call.kwargs['timeout'], 240)
            run.side_effect = subprocess.CalledProcessError(1, 'apt-get')
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke(directory, False, run)

    def test_missing_or_old_rust_refuses_image_before_path_publication(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('image_apt', ROOT / 'ops/ci-apt.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for fault in ('missing-rustc', 'missing-cargo', 'old-rustc', 'old-cargo', 'timeout'):
            def run(command, **kwargs):
                self.assertEqual(kwargs['timeout'], 5)
                tool = command[0]
                if fault == 'timeout':
                    raise subprocess.TimeoutExpired(command, 5)
                if fault == 'missing-' + tool:
                    raise FileNotFoundError(tool)
                return subprocess.CompletedProcess(command, 0, f'{tool} 1.{84 if fault == "old-" + tool else 90}.0 (hash date)')
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'path'
                with patch.dict(os.environ, CARR_CI_POSTGRES_IMAGE='both', GITHUB_PATH=str(output)), \
                     patch.object(module.os, 'geteuid', return_value=1001), \
                     patch.object(module.subprocess, 'run', side_effect=run):
                    with self.assertRaises((RuntimeError, OSError, subprocess.TimeoutExpired)):
                        module.qualify_image(postgres=False)
                    self.assertFalse(output.exists())

    def test_image_document_and_postgres_checks_never_invoke_apt(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('image_apt', ROOT / 'ops/ci-apt.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for documents_present in (True, False):
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / 'path'
                def run(command, **kwargs):
                    self.assertEqual(command[-1], '--version')
                    if command[0] in ('rustc', 'cargo'):
                        return subprocess.CompletedProcess(command, 0, f'{command[0]} 1.90.0 (hash date)')
                    major = 17 if '/17/' in command[0] else 18
                    return subprocess.CompletedProcess(command, 0, f'tool (PostgreSQL) {major}.1')
                with patch.dict(os.environ, CARR_CI_POSTGRES_IMAGE='both', GITHUB_PATH=str(output)), \
                     patch.object(module.os, 'geteuid', return_value=1001), \
                     patch.object(module.Path, 'is_file', return_value=True), \
                     patch.object(module.shutil, 'which', return_value='/usr/bin/tool' if documents_present else None), \
                     patch.object(module.subprocess, 'run', side_effect=run):
                    if documents_present:
                        self.assertEqual(module.main(['--postgres', '--documents']), 0)
                        self.assertTrue(output.exists())
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'document tools'):
                            module.main(['--postgres', '--documents'])
                        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
