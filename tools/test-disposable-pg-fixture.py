#!/usr/bin/env python3
"""A nested PostgreSQL proof owns one host-wide fixture budget until teardown."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import shutil
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, fixture_root_from_line

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


class KilledParent(unittest.TestCase):
    def test_server_stops_when_python_parent_is_killed(self):
        for during_start in (False, True):
            with self.subTest(during_start=during_start):
                self.kill_parent(during_start)

    def kill_parent(self, during_start):
        bins = postgres_bin_dir()
        script = """
import sys
from pathlib import Path
from lib.disposable_pg_fixture import DisposablePostgres
bins, during_start = Path(sys.argv[1]), sys.argv[2] == 'True'
f = DisposablePostgres('killed-parent-', bins / 'pg_ctl')
data = f.root / 'data'
f.run([bins / 'initdb', '-D', data, '-A', 'trust'], check=True, capture_output=True)
if during_start:
    print(f.root, flush=True)
f.run([bins / 'pg_ctl', '-D', data, '-l', f.root / 'log', '-o', f"-k {f.root} -h ''", '-w', 'start'], check=True, capture_output=True)
print(f.root, flush=True)
sys.stdin.read()
"""
        child = subprocess.Popen([sys.executable, '-u', '-c', script, str(bins), str(during_start)], cwd=ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        root = None
        try:
            root = fixture_root_from_line(child.stdout.readline(), 'killed-parent-')
            if not during_start:
                self.assertTrue((root / 'data' / 'postmaster.pid').exists())
            child.kill()  # mid-start, the supervisor finishes the launch, then stops it
            child.wait(timeout=5)
            deadline = time.monotonic() + 15
            while root.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(root.exists(), 'server or root survived killed parent')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if root and (root / 'data' / 'postmaster.pid').exists():
                subprocess.run([bins / 'pg_ctl', '-D', root / 'data', '-m', 'immediate', '-w', 'stop'], capture_output=True)
            if root and root.exists():
                shutil.rmtree(root)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()


class SharedMemoryPolicy(unittest.TestCase):
    def test_fixture_overrides_sysv_and_cleans_up_on_body_failure(self):
        bins = postgres_bin_dir()
        root = None
        with self.assertRaisesRegex(ValueError, 'body failed'):
            with DisposablePostgres('memory-policy-', bins / 'pg_ctl') as fixture:
                root = fixture.root
                data = root / 'data'
                fixture.run([bins / 'initdb', '-D', data, '-A', 'trust',
                             '-c', 'shared_memory_type=sysv',
                             '-c', 'dynamic_shared_memory_type=sysv'],
                            check=True, capture_output=True)
                fixture.run([bins / 'pg_ctl', '-D', data, '-l', root / 'postgres.log',
                             '-o', f"-k {root} -h ''", '--options=-c shared_memory_type=sysv',
                             '-o', '-c dynamic_shared_memory_type=sysv',
                             '-w', 'start'], check=True, capture_output=True)
                settings = subprocess.check_output([
                    bins / 'psql', '-h', root, '-d', 'postgres', '-Atc',
                    'SHOW shared_memory_type; SHOW dynamic_shared_memory_type'], text=True)
                self.assertEqual(settings.splitlines(), ['mmap', 'posix'])
                raise ValueError('body failed')
        self.assertFalse(root.exists())

    def test_start_failure_stops_registered_cluster_without_global_ipc_calls(self):
        calls = []
        def failed(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, int('start' in args), '', 'startup failed')
        with patch('lib.disposable_pg_fixture.subprocess.run',
                   side_effect=AssertionError('must not inspect or remove host IPC')):
            with DisposablePostgres('memory-policy-', '/fake/pg_ctl', runner=failed) as fixture:
                result = fixture.run(['/fake/pg_ctl', '-D', fixture.root / 'data', 'start'])
                self.assertEqual(result.returncode, 1)
        self.assertEqual(calls[-1][-1], 'stop')
        count = len(calls)
        fixture.close()
        self.assertEqual(len(calls), count)
        self.assertTrue(any(command[-1] == 'stop' for command in calls))

    def test_start_failure_reads_owned_log_and_stops_server(self):
        calls = []
        def failed(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, int('start' in args), '', 'examine log')
        with DisposablePostgres('memory-policy-', '/fake/pg_ctl', runner=failed) as fixture:
            log = fixture.root / 'postgres.log'
            log.write_text('FATAL: could not create shared memory segment: No space left on device')
            with self.assertRaisesRegex(RuntimeError, 'System V shared-memory exhaustion'):
                fixture.run(['/fake/pg_ctl', '-D', fixture.root / 'data', '-l', log, 'start'])
        self.assertEqual(calls[-1][-1], 'stop')

    def test_unreadable_log_preserves_original_startup_error(self):
        failure = subprocess.CalledProcessError(1, ['pg_ctl'], stderr='original failure')
        def failed(args, **kwargs):
            if 'start' in args:
                raise failure
            return subprocess.CompletedProcess(args, 0, '', '')
        with DisposablePostgres('memory-policy-', '/fake/pg_ctl', runner=failed) as fixture:
            log = fixture.root / 'postgres.log'
            log.touch()
            with patch.object(Path, 'open', side_effect=PermissionError('unreadable')):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    fixture.run(['/fake/pg_ctl', '-D', fixture.root / 'data', '-l', log, 'start'])
            self.assertIs(caught.exception, failure)

    def test_enospc_is_reported_without_retry_or_host_cleanup(self):
        calls = []
        def failed(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, '',
                'FATAL: could not create shared memory segment: No space left on device')
        with DisposablePostgres('memory-policy-', '/fake/pg_ctl', runner=failed) as fixture:
            with self.assertRaisesRegex(RuntimeError, 'System V shared-memory exhaustion'):
                fixture.run(['/fake/initdb', '-D', fixture.root / 'data'])
        self.assertEqual(sum(Path(command[0]).name == 'initdb' for command in calls), 1)


if __name__ == '__main__':
    unittest.main()
