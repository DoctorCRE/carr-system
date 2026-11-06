#!/usr/bin/env python3
"""CI provisioning must recover once and verify real required artifacts."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('postgres', Path(__file__).with_name('ci-apt.py'))
assert SPEC is not None and SPEC.loader is not None
pg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pg)


class Provisioning(unittest.TestCase):
    def test_required_packages_and_official_bootstrap(self):
        self.assertEqual(pg.PACKAGES, ('postgresql-17', 'postgresql-18', 'postgresql-client-18',
                                       'postgresql-17-pgvector', 'postgresql-18-pgvector'))
        def run(command, **kwargs):
            version = '17' if '/17/' in command[0] else '18'
            return subprocess.CompletedProcess(command, 0, f'postgres (PostgreSQL) {version}.6')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, CARR_CI_POSTGRES_IMAGE='', GITHUB_PATH=str(Path(directory) / 'path')), patch.object(pg.Path, 'is_file', side_effect=[False, True, True, True, True]), patch.object(pg.subprocess, 'run', side_effect=run) as runner:
            pg.install()
        self.assertEqual(runner.call_args_list[0].args[0], ['sudo', 'apt-get', 'update', '-qq'])
        self.assertEqual(runner.call_args_list[1].args[0], ['sudo', 'apt-get', 'install', '-y', '-qq', 'postgresql-common', 'ca-certificates'])
        self.assertEqual(runner.call_args_list[2].args[0], ['sudo', str(pg.SETUP), '-y'])

    def test_image_capabilities_never_acquire_packages(self):
        for fault in ('none', 'extension', 'version', 'binary', 'timeout', 'root'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'path'
                def run(command, **kwargs):
                    self.assertEqual(command[-1], '--version')
                    self.assertEqual(kwargs['timeout'], 5)
                    if fault == 'binary':
                        raise FileNotFoundError(command[0])
                    if fault == 'timeout':
                        raise subprocess.TimeoutExpired(command, 5)
                    version = 17 if '/17/' in command[0] else 18
                    return subprocess.CompletedProcess(command, 0,
                        f'tool (PostgreSQL) {16 if fault == "version" else version}.6')
                with patch.dict(os.environ, CARR_CI_POSTGRES_IMAGE='both', GITHUB_PATH=str(output)), \
                     patch.object(pg.os, 'geteuid', return_value=0 if fault == 'root' else 1001), \
                     patch.object(pg.Path, 'is_file', return_value=fault != 'extension'), \
                     patch.object(pg.subprocess, 'run', side_effect=run) as runner:
                    if fault == 'none':
                        self.assertEqual(pg.main(['--provision-postgres']), 0)
                        self.assertEqual(output.read_text(), '/usr/lib/postgresql/17/bin\n')
                        self.assertEqual(runner.call_count, 14)
                        self.assertEqual({Path(call.args[0][0]).name for call in runner.call_args_list},
                            {'initdb', 'pg_ctl', 'postgres', 'psql', 'createdb', 'pg_dump', 'pg_restore'})
                    else:
                        with self.assertRaises((RuntimeError, FileNotFoundError, subprocess.TimeoutExpired)):
                            pg.main(['--provision-postgres'])
                        self.assertFalse(output.exists())

    def test_official_setup_and_fail_closed_verification(self):
        for fault in ('none', 'extension', 'version', 'binary', 'install'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'path'
                commands = []
                def run(command, **kwargs):
                    commands.append(command)
                    if fault == 'install' or (fault == 'binary' and command[-1] == '--version'):
                        raise subprocess.CalledProcessError(1, command)
                    version = '17' if '/17/' in command[0] else '18'
                    return subprocess.CompletedProcess(command, 0, f'postgres (PostgreSQL) {16 if fault == "version" else version}.6')
                with patch.dict(os.environ, CARR_CI_POSTGRES_IMAGE='', GITHUB_PATH=str(output)), patch.object(pg.subprocess, 'run', side_effect=run), patch.object(pg.Path, 'is_file', return_value=fault != 'extension'):
                    if fault == 'none':
                        pg.install()
                        self.assertEqual(output.read_text(), '/usr/lib/postgresql/17/bin\n')
                        self.assertEqual(commands[0], ['sudo', str(pg.SETUP), '-y'])
                        self.assertTrue(set(pg.PACKAGES).issubset(commands[1]))
                        self.assertEqual(len([c for c in commands if c[-1] == '--version']), 6)
                    else:
                        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                            pg.install()
                        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
