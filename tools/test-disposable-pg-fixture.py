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
import ctypes
import pwd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.disposable_pg_fixture import DisposablePostgres

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


class SharedMemoryPreflight(unittest.TestCase):
    def test_start_reclaims_only_owned_unattached_segments_with_dead_creators(self):
        owner = pwd.getpwuid(os.getuid()).pw_name
        table = f"""T ID KEY MODE OWNER GROUP CREATOR CGROUP NATTCH SEGSZ CPID LPID
m 11 0 --rw {owner} staff {owner} staff 0 56 900001 0
m 12 0 --rw {owner} staff {owner} staff 1 56 900002 0
m 13 0 --rw {owner} staff {owner} staff 0 56 {os.getpid()} 0
m 14 0 --rw another staff another staff 0 56 900004 0
"""
        removed = []
        def system(args, **kwargs):
            if args[0] == 'ipcs':
                return subprocess.CompletedProcess(args, 0, table, '')
            if args[0] == 'ipcrm':
                removed.append(args[-1])
                return subprocess.CompletedProcess(args, 0, '', '')
            self.assertEqual(removed, ['11'], 'reclaim must precede initdb')
            return subprocess.CompletedProcess(args, 0, '', '')
        def alive(pid, sig):
            if pid != os.getpid():
                raise ProcessLookupError()
        with patch('lib.disposable_pg_fixture.subprocess.run', side_effect=system), \
             patch('lib.disposable_pg_fixture.os.kill', side_effect=alive), \
             patch('lib.disposable_pg_fixture.sys.platform', 'darwin'):
            with DisposablePostgres('shm-test-', '/fake/pg_ctl') as fixture:
                fixture.run(['/fake/initdb', '-D', fixture.root / 'data'])

    def test_shared_memory_exhaustion_names_environment_fault_and_orphan_count(self):
        def failed(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '',
                'FATAL: could not create shared memory segment: No space left on device')
        with patch('lib.disposable_pg_fixture.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, 'ID OWNER NATTCH CPID\n', '')):
            with DisposablePostgres('shm-test-', '/fake/pg_ctl', runner=failed) as fixture:
                with self.assertRaisesRegex(RuntimeError, 'environment fault.*0 orphan'):
                    fixture.run(['/fake/initdb', '-D', fixture.root / 'data'])

    def test_linux_preflight_preserves_segment_that_gains_an_attachment(self):
        inspections = 0
        removed = []
        def system(args, **kwargs):
            nonlocal inspections
            if args == ['ipcs', '-m']:
                inspections += 1
                table = f"key shmid owner perms bytes nattch status\n0 21 {os.getuid()} 600 56 {int(inspections > 1)}\n"
            elif args == ['ipcs', '-m', '-p']:
                table = f"shmid owner cpid lpid\n21 {os.getuid()} 900001 0\n"
            else:
                if args[0] == 'ipcrm':
                    removed.append(args[-1])
                table = ''
            return subprocess.CompletedProcess(args, 0, table, '')
        with patch('lib.disposable_pg_fixture.subprocess.run', side_effect=system), \
             patch('lib.disposable_pg_fixture.os.kill', side_effect=ProcessLookupError()), \
             patch('lib.disposable_pg_fixture.sys.platform', 'linux'):
            with DisposablePostgres('shm-test-', '/fake/pg_ctl') as fixture:
                fixture.run(['/fake/pg_ctl', '-D', fixture.root / 'data', 'start'])
        self.assertEqual(removed, [])
        self.assertGreaterEqual(inspections, 2)

    def test_start_failure_reads_owned_postgres_log(self):
        def failed(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'pg_ctl: examine the log output')
        with patch('lib.disposable_pg_fixture.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, 'ID OWNER NATTCH CPID\n', '')):
            with DisposablePostgres('shm-test-', '/fake/pg_ctl', runner=failed) as fixture:
                log = fixture.root / 'postgres.log'
                log.write_text('FATAL: could not create shared memory segment: No space left on device')
                with self.assertRaisesRegex(RuntimeError, 'environment fault.*0 orphan'):
                    fixture.run(['/fake/pg_ctl', '-D', fixture.root / 'data', '-l', log, 'start'])

    def test_unreadable_log_preserves_original_startup_error(self):
        failure = subprocess.CalledProcessError(1, ['pg_ctl'], stderr='original failure')
        def failed(args, **kwargs):
            if 'start' in args:
                raise failure
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch('lib.disposable_pg_fixture.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, 'ID OWNER NATTCH CPID\n', '')):
            with DisposablePostgres('shm-test-', '/fake/pg_ctl', runner=failed) as fixture:
                log = fixture.root / 'postgres.log'
                log.touch()
                with patch.object(Path, 'open', side_effect=PermissionError('unreadable')):
                    with self.assertRaises(subprocess.CalledProcessError) as caught:
                        fixture.run(['/fake/pg_ctl', '-D', fixture.root / 'data', '-l', log, 'start'])
                self.assertIs(caught.exception, failure)

    def test_unrecognized_table_reports_unavailable_count(self):
        def failed(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'shmget: No space left on device')
        with patch('lib.disposable_pg_fixture.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, 'unknown table', '')):
            with DisposablePostgres('shm-test-', '/fake/pg_ctl', runner=failed) as fixture:
                with self.assertRaisesRegex(RuntimeError, 'orphan segment count unavailable'):
                    fixture.run(['/fake/initdb', '-D', fixture.root / 'data'])

    def test_real_preflight_reclaims_synthetic_orphan_and_keeps_attached_segment(self):
        kernel = ctypes.CDLL(None, use_errno=True)
        kernel.shmat.restype = ctypes.c_void_p
        kernel.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        kernel.shmdt.argtypes = [ctypes.c_void_p]
        script = """
import ctypes
kernel = ctypes.CDLL(None, use_errno=True)
ident = kernel.shmget(0, 56, 0o1600)
if ident == -1:
    raise OSError(ctypes.get_errno(), 'synthetic shmget failed')
print(ident)
"""
        ids = []
        attached = None
        try:
            for _ in range(2):
                child = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                       text=True, check=True)
                ids.append(int(child.stdout))
            attached = kernel.shmat(ids[1], None, 0)
            self.assertNotEqual(attached, ctypes.c_void_p(-1).value)
            def successful(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, '', '')
            # Restrict inspection to our synthetic IDs. Never reclaim other host segments.
            real_run = subprocess.run
            def inspect(args, **kwargs):
                result = real_run(args, **kwargs)
                if args[0] == 'ipcs':
                    lines = []
                    for line in result.stdout.splitlines():
                        fields = line.split()
                        if not fields or not (fields[0].isdigit() or fields[0] == 'm' or fields[0].startswith('0x')):
                            lines.append(line)
                        elif any(str(ident) == field for ident in ids for field in fields[:2]):
                            lines.append(line)
                    result.stdout = '\n'.join(lines)
                return result
            with patch('lib.disposable_pg_fixture.subprocess.run', side_effect=inspect):
                with DisposablePostgres('shm-test-', '/fake/pg_ctl', runner=successful) as fixture:
                    fixture.run(['/fake/initdb', '-D', fixture.root / 'data'])
            self.assertEqual(kernel.shmctl(ids[0], 0, None), -1, 'orphan must already be removed')
            table = real_run(['ipcs', '-m'], capture_output=True, text=True, check=True).stdout
            attached_rows = [line.split() for line in table.splitlines()
                             if len(line.split()) > 1 and line.split()[1] == str(ids[1])]
            self.assertEqual(len(attached_rows), 1, 'attached segment must remain visible')
            self.assertNotIn('dest', attached_rows[0], 'attached segment must not be marked for deletion')
        finally:
            if attached is not None and attached != ctypes.c_void_p(-1).value:
                kernel.shmdt(attached)
            for ident in ids:
                kernel.shmctl(ident, 0, None)



if __name__ == '__main__':
    unittest.main()
