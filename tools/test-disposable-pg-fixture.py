#!/usr/bin/env python3
"""A nested PostgreSQL proof owns one host-wide fixture budget until teardown."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DisposablePostgresBudget(unittest.TestCase):
    def test_nested_proof_blocks_other_process_until_outer_teardown(self):
        script = """
import sys
from pathlib import Path
from lib import disposable_pg_fixture as fixture
fixture.LOCK_PATH = Path(sys.argv[1])
print('ready', flush=True)
with fixture.postgres_fixture_group():
    with fixture.postgres_fixture_group():
        print('entered', flush=True)
    print('nested-exited', flush=True)
    sys.stdin.readline()
"""
        with tempfile.TemporaryDirectory(prefix='postgres-budget-test-') as directory:
            processes = []
            try:
                for _ in range(2):
                    process = subprocess.Popen([sys.executable, '-u', '-c', script,
                        str(Path(directory) / 'budget.lock')], cwd=ROOT,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, env={**os.environ, 'PYTHONPATH': str(ROOT)})
                    processes.append(process)
                    self.assertEqual(process.stdout.readline().strip(), 'ready')
                    if len(processes) == 1:
                        self.assertEqual(process.stdout.readline().strip(), 'entered')
                        self.assertEqual(process.stdout.readline().strip(), 'nested-exited')
                first, second = processes
                self.assertEqual(select.select([second.stdout], [], [], 0.2)[0], [],
                                 'nested exit must not release the outer cluster budget')
                first.stdin.write('stop\n'); first.stdin.flush()
                self.assertEqual(first.wait(timeout=5), 0, first.stderr.read())
                self.assertEqual(second.stdout.readline().strip(), 'entered')
                self.assertEqual(second.stdout.readline().strip(), 'nested-exited')
                second.stdin.write('stop\n'); second.stdin.flush()
                self.assertEqual(second.wait(timeout=5), 0, second.stderr.read())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill(); process.wait()
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()


if __name__ == '__main__':
    unittest.main()
