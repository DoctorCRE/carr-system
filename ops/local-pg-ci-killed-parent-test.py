#!/usr/bin/env python3
"""A killed local CI process must not leave its PostgreSQL server running."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import postgres_bin_dir


class KilledParent(unittest.TestCase):
    def test_local_ci_server_stops_after_sigkill(self):
        bins = postgres_bin_dir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        child = subprocess.Popen([sys.executable, '-u', str(ROOT / 'ops/local-pg-ci.py'),
                                  '--port', str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        root = None
        pid = None
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and child.poll() is None:
                for candidate in Path('/tmp').glob('carr-local-pg-ci.*'):
                    try:
                        fields = (candidate / 'data/postmaster.pid').read_text().splitlines()
                        if int(fields[3]) == port:
                            root, pid = candidate, int(fields[0])
                            break
                    except (OSError, ValueError, IndexError):
                        continue
                if pid:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(pid, 'local CI did not start its server')
            child.kill()
            child.wait(timeout=5)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail('PostgreSQL survived its killed local CI parent')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if root and (root / 'data/postmaster.pid').exists():
                subprocess.run([bins / 'pg_ctl', '-D', root / 'data', '-m', 'immediate', '-w', 'stop'], capture_output=True)
            if root and root.exists():
                shutil.rmtree(root)


if __name__ == '__main__':
    unittest.main()
