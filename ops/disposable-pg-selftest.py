#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import disposable_pg_fixture as fixture


class Lifecycle(unittest.TestCase):
    def test_sigterm_stops_postmaster_and_removes_owned_directory(self):
        with fixture.postgres_fixture_group():
            child = subprocess.Popen([sys.executable, '-u', '-c', textwrap.dedent('''
    import importlib.util, pathlib, sys, time
    sys.path.insert(0, sys.argv[1])
    from lib.disposable_pg_fixture import DisposablePostgres
    spec = importlib.util.spec_from_file_location('local_pg', pathlib.Path(sys.argv[1]) / 'ops/local-pg-ci.py')
    m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
    b = m.find_postgres_binaries()
    with DisposablePostgres('carr-local-pg-ci.signal-', b.pg_ctl, env=m.scrub_cloud_environment(__import__('os').environ)) as pg:
        data = pg.root / 'data'
        pg.run([b.initdb, '-D', data, '-U', 'fixture', '--auth=trust', '--no-locale'], check=True, capture_output=True, timeout=60)
        pg.run([b.pg_ctl, '-D', data, '-l', pg.root/'pg.log', '-o', f"-k {pg.root} -h ''", '-w', 'start'], check=True, capture_output=True, timeout=60)
        print(str(pg.root), flush=True)
        while True:
            time.sleep(0.05)
    '''), str(ROOT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([child.stdout], [], [], 90)[0], 'cluster startup timed out')
                line = child.stdout.readline().strip()
                if not line:
                    self.fail(child.communicate(timeout=20)[1])
                root = Path(line)
                pid = int((root / 'data/postmaster.pid').read_text().splitlines()[0])
                child.send_signal(signal.SIGTERM)
                try:
                    _, stderr = child.communicate(timeout=90)
                except subprocess.TimeoutExpired:
                    log = root / 'pg.log'
                    self.fail(f'teardown timed out; postgres log:\n{log.read_text() if log.exists() else "missing"}')
                self.assertEqual(child.returncode, 128 + signal.SIGTERM, stderr)
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
                self.assertFalse(root.exists())
            finally:
                if child.poll() is None:
                    child.send_signal(signal.SIGTERM)
                    child.communicate(timeout=90)


if __name__ == '__main__':
    unittest.main()
