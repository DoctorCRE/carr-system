#!/usr/bin/env python3
"""Required PostgreSQL capabilities fail setup before PATH publication."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('postgres', Path(__file__).with_name('ci-apt.py'))
assert SPEC is not None and SPEC.loader is not None
pg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pg)


class Provisioning(unittest.TestCase):
    def test_official_setup_and_fail_closed_verification(self):
        for fault in ('none', 'extension', 'version', 'binary', 'source'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'path'
                commands = []
                def run(command, **kwargs):
                    commands.append(command)
                    if fault == 'source' or (fault == 'binary' and command[-1] == '--version'):
                        raise subprocess.CalledProcessError(100, command)
                    version = '17' if '/17/' in command[0] else '18'
                    return subprocess.CompletedProcess(command, 0, f'postgres (PostgreSQL) {16 if fault == "version" else version}.6')
                with patch.dict(os.environ, GITHUB_PATH=str(output)), patch.object(pg.subprocess, 'run', side_effect=run), patch.object(pg.Path, 'is_file', return_value=fault != 'extension'):
                    if fault == 'none':
                        self.assertEqual(pg.main(['--postgres']), 0)
                        self.assertEqual(output.read_text(), '/usr/lib/postgresql/17/bin\n')
                        self.assertEqual(commands[0], ['sudo', str(pg.SETUP), '-y'])
                        self.assertTrue(set(pg.PACKAGES).issubset(commands[1]))
                        binaries = [c[0] for c in commands if c[-1] == '--version']
                        for version in (17, 18):
                            for binary in ('initdb', 'pg_ctl', 'postgres', 'psql', 'pg_dump', 'pg_restore'):
                                self.assertIn(f'/usr/lib/postgresql/{version}/bin/{binary}', binaries)
                    else:
                        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                            pg.main(['--postgres'])
                        self.assertFalse(output.exists())
                        if fault == 'source':
                            self.assertEqual(len(commands), 1)


if __name__ == '__main__':
    unittest.main()
