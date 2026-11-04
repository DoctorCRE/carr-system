#!/usr/bin/env python3
"""Canonical acquisition and eligibility without credentials or a database."""
import contextlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.studio_proof import (RunBatch, Requirement, Source, Installation, Evidence,
                              observe, read_runs)

spec = importlib.util.spec_from_file_location('ops_record_read_test', ROOT / 'tools/ops-record.py')
assert spec is not None and spec.loader is not None
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
SHA = 'a' * 40

def row(**changes):
    value = dict(run_id='00000000-0000-4000-8000-000000000001',
                 correlation_id='00000000-0000-4000-8000-000000000002',
                 service='outlook-intake', run_key='outlook.intake', environment='production',
                 source_kind='wrapper', source_ref='bin/run-scheduled.sh', state='succeeded',
                 exit_code=0, started_at=NOW-timedelta(minutes=2), ended_at=NOW-timedelta(minutes=1),
                 observed_at=NOW, expires_at=NOW+timedelta(hours=1),
                 detail=json.dumps(dict(state='succeeded', reason='processed', runtime_sha=SHA,
                                        counts={'run': {'mail_ids': 2}, 'backlog': {}})))
    value.update(changes)
    return value

class RunReadTest(unittest.TestCase):
    def setUp(self):
        self.req = Requirement('identity', 'outlook-intake', 'outlook.intake', 'production',
                               'studio', 'run.mail_ids', 1, 3600)
        self.source = Source('DoctorCRE/carr-system', SHA, SHA, 'studio')
        self.install = Installation('studio', SHA, NOW-timedelta(hours=1),
                                    NOW-timedelta(minutes=10), True, True, True, True)

    def acquire(self, rows):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [tuple(r[k] for k in recorder.RUN_READ_COLUMNS) for r in rows]
        with patch.object(recorder, 'connect', return_value=conn) as connect:
            result = recorder.read_run_batch(self.req.service, self.req.run_key, self.req.environment,
                                            NOW-timedelta(hours=1), NOW, 10)
        connect.assert_called_once_with('read', timeout=10)
        return result, cur

    def evidence(self, rows=None, **changes):
        batch, _ = self.acquire([row()] if rows is None else rows)
        return Evidence(batch, self.install, self.source.release_sha,
                        {r.run_id: 'studio' for r in batch.runs}, source_ancestry_verified=True, **changes)

    def test_read_parser_and_query_are_bounded_and_preserve_all_states(self):
        batch, cur = self.acquire([row(state='failed', exit_code=1)])
        self.assertEqual(batch.status, 'ok')
        self.assertEqual(batch.runs[0].state, 'failed')
        query, params = cur.execute.call_args_list[-1].args
        self.assertIn('join ops.service', query)
        self.assertNotIn("state = 'succeeded'", query)
        self.assertEqual(params[-1], 11)
        self.assertIn('transaction read only', cur.execute.call_args_list[0].args[0])
        with patch.object(recorder, 'read_run_batch', return_value=batch) as read, \
             patch.object(sys, 'argv', ['ops-record.py', 'read-runs', '--service', 'outlook-intake',
                 '--key', 'outlook.intake', '--environment', 'production', '--since',
                 '2026-01-01T23:00:00Z', '--until', '2026-01-02T00:00:00Z']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(recorder.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['runs'][0]['run_id'], row()['run_id'])
        read.assert_called_once()

    def test_statechart_and_reused_run(self):
        good = self.evidence()
        first = observe(self.req, self.source, good, NOW)
        self.assertEqual(first.state, 'live-proven')
        self.assertEqual(observe(self.req, self.source, good, NOW), first)
        later = NOW+timedelta(hours=2)
        reread = replace(good, batch=replace(good.batch, until=later))
        self.assertEqual(observe(self.req, self.source, reread, later).state, 'held')
        failure = row(run_id='00000000-0000-4000-8000-000000000003', state='failed', exit_code=1,
                      ended_at=NOW, detail='failed before receipt')
        failed = self.evidence([row(observed_at=NOW+timedelta(hours=1)), failure])
        self.assertEqual(observe(self.req, self.source, failed, NOW).state, 'failed')
        for state in ('timed_out', 'cancelled'):
            newest = {**failure, 'state': state}
            self.assertEqual(observe(self.req, self.source, self.evidence([row(), newest]), NOW).state, 'failed')
        self.assertEqual(observe(self.req, self.source, good, NOW).state, 'live-proven')
        self.assertEqual(observe(self.req, self.source, self.evidence([row(state='skipped')]), NOW).state, 'held')

    def test_counterexamples(self):
        for changes in ({'started_at': None}, {'ended_at': None}, {'run_id': '42'}, {'correlation_id': None},
                        {'detail': '{}'}, {'detail': 'bad'}, {'source_ref': 'caller'},
                        {'service': 'other'}, {'environment': 'local'}, {'run_key': 'other'},
                        {'state': 'unknown'}, {'state': 'running'},
                        {'ended_at': NOW-timedelta(hours=2), 'started_at': NOW-timedelta(hours=3)}):
            with self.subTest(changes=changes):
                self.assertEqual(observe(self.req, self.source, self.evidence([row(**changes)]), NOW).state, 'held')
        detail = json.loads(row()['detail'])
        for value in (True, -1, None):
            changed = {**detail, 'counts': {'run': {'mail_ids': value}}}
            self.assertEqual(observe(self.req, self.source,
                self.evidence([row(detail=json.dumps(changed))]), NOW).state, 'held')
        for sha in ('b'*40, 'a'*7, None):
            self.assertEqual(observe(self.req, self.source,
                self.evidence([row(detail=json.dumps({**detail, 'runtime_sha': sha}))]), NOW).state, 'held')
        good = self.evidence()
        for evidence in (replace(good, target_bindings={}),
                         replace(good, target_bindings={row()['run_id']: 'other'}),
                         replace(good, installation=replace(self.install, ended_at=None)),
                         replace(good, installation=replace(self.install, clean_executables=False)),
                         replace(good, source_ancestry_verified=False),
                         replace(good, installation=replace(self.install, helper_verified=False)),
                         replace(good, installation=replace(self.install, plists_verified=False)),
                         replace(good, installation=replace(self.install, registered_command_verified=False)),
                         replace(good, release_sha='b'*40),
                         replace(good, batch=replace(good.batch, truncated=True)),
                         replace(good, batch=replace(good.batch, status='unavailable'))):
            self.assertEqual(observe(self.req, self.source, evidence, NOW).state, 'held')
        self.assertEqual(observe(self.req, replace(self.source, repository='other'), good, NOW).state, 'held')
        tied = self.evidence([row(), row(run_id='00000000-0000-4000-8000-000000000003')])
        self.assertEqual(observe(self.req, self.source, tied, NOW).state, 'held')

    def test_transport_failure_and_truncation(self):
        with patch.object(recorder, 'connect', side_effect=RuntimeError('private dsn')):
            batch = recorder.read_run_batch('outlook-intake', 'outlook.intake', 'production',
                                           NOW-timedelta(hours=1), NOW, 1)
        self.assertEqual(batch.status, 'unavailable')
        self.assertNotIn('private', str(batch))
        batch, _ = self.acquire([row()] * 11)
        self.assertTrue(batch.truncated)
        self.assertFalse(batch.complete)
        with patch.object(recorder, 'connect', side_effect=AssertionError('must validate first')):
            self.assertEqual(recorder.read_run_batch('x', 'x', 'production', NOW, NOW, 1).status, 'invalid-query')

class PostgresReadTest(unittest.TestCase):
    @unittest.skipUnless('--postgres' in sys.argv, 'opt-in disposable PostgreSQL transport check')
    def test_actual_sql_readonly_and_latest_failure(self):
        import psycopg
        initdb = shutil.which('initdb')
        self.assertIsNotNone(initdb, 'PostgreSQL binaries required for --postgres')
        binary = Path(initdb).parent
        with tempfile.TemporaryDirectory(prefix='carr-proof-pg-') as directory:
            root = Path(directory)
            data, socket = root / 'data', root / 'socket'
            socket.mkdir()
            subprocess.run([initdb, '-D', str(data), '-A', 'trust', '--no-locale', '-E', 'UTF8'],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'log'),
                            '-o', f"-F -k {socket} -h ''", '-w', 'start'],
                           check=True, capture_output=True, timeout=30)
            try:
                def connect(_, **options):
                    return psycopg.connect(host=str(socket), dbname='postgres', autocommit=True)
                with connect(None) as conn:
                    conn.execute('create schema ops')
                    conn.execute('create table ops.service(id uuid primary key, key text)')
                    conn.execute("""create table ops.run(id uuid, correlation_id uuid, service_id uuid,
                        run_key text, environment text, source_kind text, source_ref text, state text,
                        exit_code int, started_at timestamptz, ended_at timestamptz, observed_at timestamptz,
                        expires_at timestamptz, detail text)""")
                    sid = '00000000-0000-4000-8000-000000000004'
                    conn.execute('insert into ops.service values (%s,%s)', (sid, 'outlook-intake'))
                    for r in (row(), row(run_id='00000000-0000-4000-8000-000000000005',
                                          state='failed', exit_code=1, ended_at=NOW)):
                        values = [r[k] for k in recorder.RUN_READ_COLUMNS]
                        values[2] = sid
                        conn.execute('insert into ops.run values (' + ','.join(['%s']*14) + ')', values)
                with patch.object(recorder, 'connect', side_effect=connect):
                    batch = recorder.read_run_batch('outlook-intake', 'outlook.intake', 'production',
                                                   NOW-timedelta(hours=1), NOW, 10)
                self.assertEqual(batch.status, 'ok')
                self.assertEqual([r.state for r in batch.runs], ['failed', 'succeeded'])
                self.assertEqual(batch.runs[1].counts['run']['mail_ids'], 2)
                self.assertEqual(batch.runs[0].ended_at, NOW)
                self.assertEqual(len(batch.runs[0].run_id), 36)
            finally:
                subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                               check=True, capture_output=True, timeout=30)


if __name__ == '__main__':
    unittest.main(argv=[arg for arg in sys.argv if arg != '--postgres'])
