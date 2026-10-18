#!/usr/bin/env python3
"""Exercise the leader migration and guard against an owned disposable PostgreSQL server."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.studio_failover import Leader


class PostgresLeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try: import psycopg
        except ImportError: raise unittest.SkipTest('psycopg not installed')
        cls.psycopg = psycopg
        pg_ctl = next((p for p in ('/opt/homebrew/opt/postgresql@17/bin/pg_ctl',
                                  '/usr/lib/postgresql/17/bin/pg_ctl') if Path(p).is_file()), shutil.which('pg_ctl'))
        if not pg_ctl: raise unittest.SkipTest('disposable PostgreSQL binaries unavailable')
        if os.getuid() == 0: raise unittest.SkipTest('initdb requires non-root fixture user')
        cls.temp = tempfile.TemporaryDirectory(prefix='carr-failover-pg-', dir='/tmp')
        cls.base = Path(cls.temp.name)
        cls.data, cls.socket = cls.base / 'data', cls.base / 'socket'
        cls.socket.mkdir(mode=0o700)
        cls.pg_ctl = pg_ctl
        cls.addClassCleanup(cls.temp.cleanup)
        cls.addClassCleanup(lambda: subprocess.run(
            [cls.pg_ctl, '-D', str(cls.data), '-m', 'immediate', '-w', 'stop'],
            check=True, capture_output=True, timeout=15) if (cls.data / 'postmaster.pid').exists() else None)
        pg_bin = Path(pg_ctl).parent
        subprocess.run([str(pg_bin / 'initdb'), '-D', str(cls.data), '-A', 'trust', '-U', 'fixture'],
                       check=True, capture_output=True, timeout=30)
        # Unix-only, private owned socket directory, no inherited DSN or production host.
        subprocess.run([pg_ctl, '-D', str(cls.data), '-l', str(cls.base / 'postgres.log'),
                        '-o', "-h '' -k " + str(cls.socket), '-w', 'start'],
                       check=True, capture_output=True, timeout=30)
        cls.dsn = dict(host=str(cls.socket), user='fixture', dbname='postgres', autocommit=True)
        with psycopg.connect(**cls.dsn) as c:
            c.execute('create schema ops; create role carr_jobs; create role carr_authority')
            authority = (ROOT / 'migrations/0161_control_plane_authority_boundary.sql').read_text()
            c.execute(authority[authority.index('create or replace function ops.authority_actor_slug()'):
                                authority.index('create or replace function ops.record_workflow_acceptance(')])
            c.execute((ROOT / 'migrations/0900_studio_failover_leader.sql').read_text())
            c.execute('create role carr_authority_joe login; grant carr_authority to carr_authority_joe')

    def connect(self): return self.psycopg.connect(**self.dsn)

    def authority(self):
        return self.psycopg.connect(**{**self.dsn, 'user': 'carr_authority_joe'})

    def evidence(self, source='studio', target='macbook'):
        from datetime import datetime, timezone
        return {'kind': 'powered-off', 'source': source, 'target': target,
                'target_sha': 'a' * 40, 'verified_at': datetime.now(timezone.utc).isoformat(),
                'keep_off_until_failback': True}

    def setUp(self):
        with self.connect() as c: c.execute("update ops.studio_leader set host='studio',epoch=1")

    def test_running_job_blocks_transfer_then_old_host_cannot_restart(self):
        a, b = [Leader(self.connect()) for _ in range(2)]
        admin = Leader(self.authority())
        try:
            self.assertTrue(a.acquire('studio', 'nightly'))
            self.assertFalse(b.acquire('studio', 'nightly'))
            b.close()
            with self.assertRaisesRegex(Exception, 'running_jobs'):
                admin.claim('studio', 'macbook', self.evidence())
            a.close()
            self.assertEqual(admin.claim('studio', 'macbook', self.evidence()), 2)
            old, new = Leader(self.connect()), Leader(self.connect())
            try:
                self.assertFalse(old.acquire('studio', 'nightly'))
                self.assertTrue(new.acquire('macbook', 'nightly'))
            finally: old.close(); new.close()
        finally: a.close(); b.close(); admin.close()

    def test_owner_persists_after_connections_die_and_failback_is_symmetric(self):
        a = Leader(self.authority())
        self.assertEqual(a.claim('studio', 'macbook', self.evidence()), 2)
        a.close()
        b = Leader(self.authority())
        try:
            self.assertEqual(b.read(), ('macbook', 2))
            import json
            with self.assertRaisesRegex(Exception, 'leader_epoch_conflict'):
                b.conn.execute('select ops.transfer_studio_leader(%s,%s,%s,%s::jsonb)',
                               ('macbook', 'studio', 1, json.dumps(self.evidence('macbook', 'studio'))))
            self.assertEqual(b.claim('studio', 'macbook', self.evidence()), 2)
            self.assertEqual(b.claim('macbook', 'studio', self.evidence('macbook', 'studio')), 3)
        finally: b.close()

    def test_jobs_can_read_owner_but_only_authority_can_transfer(self):
        with self.connect() as conn:
            conn.execute('set role carr_jobs')
            jobs = Leader(conn)
            self.assertEqual(jobs.read(), ('studio', 1))
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                jobs.claim('studio', 'macbook', self.evidence())
        with self.authority() as conn:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                conn.execute("update ops.studio_leader set host='macbook'")
            self.assertEqual(Leader(conn).claim('studio', 'macbook', self.evidence()), 2)

    def test_transfer_rejects_unbound_evidence_and_nonhuman_bundle_session(self):
        with self.authority() as conn:
            with self.assertRaisesRegex(Exception, 'invalid_fence_evidence'):
                Leader(conn).claim('studio', 'macbook', {'kind': 'powered-off'})
        with self.connect() as conn:
            conn.execute('set role carr_authority')
            with self.assertRaisesRegex(Exception, 'authority session user'):
                Leader(conn).claim('studio', 'macbook', self.evidence())

    def test_schema_rebuild_without_runtime_owner_refuses_jobs_and_transfer(self):
        with self.connect() as conn:
            try:
                conn.execute('delete from ops.studio_leader')
                with self.assertRaisesRegex(RuntimeError, 'leader row missing'):
                    Leader(conn).acquire('studio', 'nightly')
                with self.authority() as authority:
                    with self.assertRaisesRegex(RuntimeError, 'leader row missing'):
                        Leader(authority).claim('studio', 'macbook', self.evidence())
            finally:
                conn.execute("insert into ops.studio_leader(singleton,host) values(true,'studio')")
            self.assertEqual(Leader(conn).read(), ('studio', 1))


if __name__ == '__main__': unittest.main()
