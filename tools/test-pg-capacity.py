#!/usr/bin/env python3
"""Capacity decisions use recorded Darwin tables and injected read failures."""
import json
import os
import runpy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import pg_capacity as capacity

# Studio readback, Nov 11, 2026. Only two rows retained to bound the fixture.
LIMITS = """kern.sysv.shmmni: 32
kern.sysv.shmall: 1024
kern.sysv.shmmax: 4194304
kern.sysv.shmseg: 8
hw.pagesize: 16384
"""
IPC = """IPC status from <running system> as of Wed Nov 11 01:48:48 CST 2026
T     ID     KEY        MODE       OWNER    GROUP  CREATOR   CGROUP NATTCH  SEGSZ  CPID  LPID   ATIME    DTIME    CTIME
Shared Memory:
m 1060700161 0x93a17cce --rw------- booko staff booko staff 0 3833856 74119 74119 21:01:21 21:01:21 21:01:21
m 1113653250 0x95388683 --rw------- booko staff booko staff 2 56 28958 28958 1:48:48 1:48:48 1:48:48
"""

class Capacity(unittest.TestCase):
    def snapshot(self, limits=LIMITS, ipc=IPC):
        return capacity.parse_darwin(limits, ipc)

    def test_insufficient_reports_limits_and_separate_occupancy(self):
        result = capacity.evaluate(self.snapshot())
        self.assertEqual(result['outcome'], 'insufficient')
        self.assertEqual(result['observed']['used_pages'], 940)
        self.assertEqual(result['observed']['page_bytes'], 4096)
        self.assertEqual(result['observed']['hardware_page_bytes'], 16384)
        self.assertEqual(result['required']['shmmni'], 256)
        self.assertEqual(result['required']['shmall'], 65536)

    def test_sufficient_continues_with_declared_eight_job_envelope(self):
        snapshot = self.snapshot(LIMITS.replace(': 32', ': 256').replace(': 1024', ': 65536'))
        result = capacity.evaluate(snapshot)
        self.assertEqual(result['outcome'], 'sufficient')
        self.assertEqual(result['demand']['jobs'], 8)
        self.assertEqual(result['demand']['clusters_per_job'], 9)

    def test_occupancy_can_refuse_provisioned_host(self):
        snapshot = self.snapshot(LIMITS.replace(': 32', ': 256').replace(': 1024', ': 65536'),
                                 IPC.replace('3833856', str(65536 * 4096)))
        self.assertEqual(capacity.evaluate(snapshot)['outcome'], 'insufficient')

    def test_unreadable_fails_once_without_retry(self):
        for error in (OSError('denied'), subprocess.TimeoutExpired('sysctl', 5), ValueError('bad table')):
            with self.subTest(error=error), patch.object(capacity, 'read_capacity', side_effect=error) as read:
                result = capacity.preflight()
                self.assertEqual(result['outcome'], 'unreadable')
                self.assertIn('error', result)
                self.assertEqual(read.call_count, 1)

    def test_malformed_or_incomplete_tables_refuse(self):
        for limits, ipc in ((LIMITS.replace('kern.sysv.shmall: 1024', ''), IPC),
                            (LIMITS, IPC + 'm broken\n'), (LIMITS, 'permission denied'),
                            (LIMITS.replace(': 32', ': -1'), IPC)):
            with self.subTest(limits=limits, ipc=ipc), self.assertRaises(ValueError):
                self.snapshot(limits, ipc)

    def test_empty_table_is_readable(self):
        self.assertEqual(len(self.snapshot(ipc=IPC.split('m 1060')[0]).segments), 0)

    def test_read_uses_bounded_readonly_commands(self):
        def run(command, **kwargs):
            self.assertEqual(kwargs['timeout'], 5)
            return subprocess.CompletedProcess(command, 0, LIMITS if command[0] == 'sysctl' else IPC, '')
        with patch.object(capacity.sys, 'platform', 'darwin'), patch.object(capacity.subprocess, 'run', side_effect=run) as calls:
            self.assertEqual(capacity.read_capacity(), self.snapshot())
            self.assertEqual(calls.call_count, 2)

    def test_cli_outcomes_and_exit_codes(self):
        cases = ((self.snapshot(), 1, 'insufficient'),
                 (self.snapshot(LIMITS.replace(': 32', ': 256').replace(': 1024', ': 65536')),
                  0, 'sufficient'))
        for snapshot, code, outcome in cases:
            with patch.object(capacity, 'read_capacity', return_value=snapshot), patch('builtins.print') as output:
                self.assertEqual(capacity.main(), code)
                self.assertEqual(json.loads(output.call_args.args[0])['outcome'], outcome)
        with patch.object(capacity, 'read_capacity', side_effect=OSError('denied')), patch('builtins.print'):
            self.assertEqual(capacity.main(), 78)

    def test_linux_proc_readback(self):
        tables = {'shmmni': '4096', 'shmall': '1048576', 'shmmax': '16777216',
                  'shm': 'key shmid perms size cpid lpid nattch uid gid cuid cgid atime dtime ctime rss swap\n'
                         '42 12 600 56 28958 28958 1 501 20 501 20 1 0 1 4096 0\n'}
        with patch.object(capacity.sys, 'platform', 'linux'), patch.object(capacity.os, 'sysconf', return_value=4096), \
                patch.object(Path, 'read_text', lambda path: tables[path.name]):
            result = capacity.read_capacity()
        self.assertEqual(result.segments, (capacity.Segment(12, 56, 28958),))
        self.assertEqual(capacity.evaluate(result)['outcome'], 'sufficient')

    def test_boot_profile_matches_required_floors(self):
        profile = Path(__file__).resolve().parents[1] / 'ops/config/studio-sysv.conf'
        values = dict(line.split('=') for line in profile.read_text().splitlines())
        self.assertEqual(int(values['kern.sysv.shmmni']), capacity.PROFILE_SHMMNI)
        self.assertEqual(int(values['kern.sysv.shmall']), capacity.PROFILE_SHMALL)
        self.assertEqual(values['kern.sysv.shmmax'], '4194304')
        self.assertEqual(values['kern.sysv.shmmin'], '1')
        self.assertEqual(values['kern.sysv.shmseg'], '8')

class Wiring(unittest.TestCase):
    def test_ci_continues_only_after_sufficient_capacity(self):
        root = Path(__file__).resolve().parents[1]
        module = runpy.run_path(str(root / 'ops/ci.py'))
        controller = unittest.mock.Mock()
        controller.run.return_value = 0
        with patch.object(capacity, 'main', return_value=0) as read, \
                patch.dict(module['main'].__globals__, Controller=unittest.mock.Mock(return_value=controller)):
            self.assertEqual(module['main'](['--strict', '--only', 'gates']), 0)
            read.assert_called_once_with()
            controller.run.assert_called_once_with()


    def test_ci_refuses_before_controller_for_every_expensive_class(self):
        root = Path(__file__).resolve().parents[1]
        module = runpy.run_path(str(root / 'ops/ci.py'))
        for name in ('unit', 'gates', 'migration'):
            with self.subTest(name=name), patch.object(capacity, 'main', return_value=78) as read, patch.dict(module['main'].__globals__, Controller=unittest.mock.Mock(side_effect=AssertionError('started'))):
                self.assertEqual(module['main'](['--strict', '--only', name]), 78)
                self.assertEqual(read.call_count, 1)

class Telemetry(unittest.TestCase):
    def test_local_ci_preserves_only_named_telemetry_settings(self):
        root = Path(__file__).resolve().parents[1]
        module = runpy.run_path(str(root / 'ops/local-pg-ci.py'))
        result = module['scrub_cloud_environment']({
            'CARR_PG_TELEMETRY': '/tmp/owned.jsonl', 'CARR_PG_JOB_ID': 'test-job',
            'DATABASE_URL': 'forbidden', 'UNRELATED': 'private'})
        self.assertEqual(result, {'CARR_PG_TELEMETRY': '/tmp/owned.jsonl', 'CARR_PG_JOB_ID': 'test-job'})


    def test_events_attribute_only_owned_pid_and_bound_diagnostics(self):
        from lib.disposable_pg_fixture import DisposablePostgres
        with tempfile.TemporaryDirectory() as directory:
            sink = Path(directory) / 'events.jsonl'
            with patch.dict(os.environ, {'CARR_PG_TELEMETRY': str(sink), 'CARR_PG_JOB_ID': 'WR622.S1.J982'}):
                def runner(command, **kwargs):
                    data = Path(command[command.index('-D') + 1])
                    if 'start' in command:
                        data.mkdir(exist_ok=True)
                        (data / 'postmaster.pid').write_text('28958\n')
                    return subprocess.CompletedProcess(command, 0, '', '')
                with patch.object(capacity, 'read_capacity', return_value=capacity.parse_darwin(LIMITS, IPC)):
                    with DisposablePostgres('telemetry-', '/fake/pg_ctl', runner=runner) as pg:
                        data = pg.root / 'data'
                        pg.run(['/fake/initdb', '-D', data])
                        pg.run(['/fake/pg_ctl', '-D', data, 'start'])
            events = [json.loads(line) for line in sink.read_text().splitlines()]
            self.assertEqual([e['event'] for e in events], ['initdb_begin', 'initdb_end', 'start_begin', 'start_end', 'stop_begin', 'stop_end'])
            start = events[3]
            self.assertEqual(start['owned_segments'], [1113653250])
            self.assertEqual(start['job'], 'WR622.S1.J982')
            self.assertEqual(start['backends'], ['mmap', 'posix'])
            self.assertIn('timestamp', start)
            self.assertEqual(events[-1]['postmasters'], start['postmasters'])

    def test_enomem_and_enospc_classified_without_retry(self):
        from lib.disposable_pg_fixture import DisposablePostgres
        for message in ('Cannot allocate memory', 'No space left on device'):
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                raise subprocess.CalledProcessError(1, command, stderr='shmget: ' + message)
            pg = DisposablePostgres('capacity-error-', '/fake/pg_ctl', runner=runner)
            try:
                with self.assertRaisesRegex(RuntimeError, 'shared-memory exhaustion'):
                    pg.run(['/fake/initdb', '-D', pg.root / 'data'])
                self.assertEqual(len(calls), 1)
            finally:
                pg.runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
                pg.close()

if __name__ == '__main__':
    unittest.main()
