#!/usr/bin/env python3
"""Exercise doctrine fingerprint capacity on isolated synthetic PostgreSQL tables."""
from pathlib import Path
import re
import socket
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir
import psycopg

TABLES = ('doctrine_document', 'doctrine_slug_alias', 'doctrine_section',
          'doctrine_revision', 'doctrine_edge_type', 'doctrine_edge', 'doctrine_link',
          'doctrine_review_policy', 'doctrine_snapshot', 'doctrine_gate_check',
          'doctrine_concept_mapping')
MIGRATION = ROOT / 'migrations/1157_doctrine_projection_bounded_digest.sql'
LEGACY = '--legacy' in sys.argv
if LEGACY:
    sys.argv.remove('--legacy')


def definition(name):
    source = (ROOT / 'db/schema.sql').read_text()
    match = re.search(r'CREATE FUNCTION ops\.' + name + r'\(.*?\$\$;', source, re.S)
    if not match:
        raise ValueError(name)
    return match[0]


class DoctrineDigestTest(unittest.TestCase):
    def test_fingerprint_covers_history_without_aggregate_body_limit(self):
        bins = postgres_bin_dir()
        with DisposablePostgres('carr-doctrine-digest-', bins / 'pg_ctl') as fixture:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            data = fixture.root / 'data'
            for command in (
                [bins / 'initdb', '-D', data, '-U', 'tester', '-A', 'trust', '--no-locale'],
                [bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'postgres.log',
                 '-o', f'-h 127.0.0.1 -p {port} -k {fixture.root}', '-w', 'start'],
            ):
                result = fixture.run(command, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
            with psycopg.connect(f'postgresql://tester@127.0.0.1:{port}/postgres', autocommit=True) as conn:
                conn.execute('create extension pgcrypto; create schema ops')
                for table in TABLES:
                    conn.execute(f'create table {table}(id integer, body text)')
                conn.execute(definition('scac_canonical_json'))
                conn.execute(definition('scac_doctrine_projection_digest') if LEGACY else MIGRATION.read_text())

                def digest():
                    return conn.execute('select ops.scac_doctrine_projection_digest()').fetchone()[0]

                empty = digest()
                self.assertRegex(empty, r'^sha256:[a-f0-9]{64}$')
                self.assertEqual(empty, digest())
                table_digests = set()
                for table in TABLES:
                    conn.execute(f"insert into {table} values (1,'synthetic')")
                    self.assertNotEqual(empty, digest(), table)
                    table_digests.add(digest())
                    conn.execute(f'delete from {table}')
                    self.assertEqual(empty, digest())
                self.assertEqual(len(table_digests), len(TABLES), 'table identity must remain visible')
                conn.execute("insert into doctrine_revision values (1,'a'),(2,'b')")
                before = digest()
                conn.execute("delete from doctrine_revision; insert into doctrine_revision values (2,'b'),(1,'a')")
                self.assertEqual(before, digest(), 'insertion order must not affect the fingerprint')
                conn.execute("update doctrine_revision set body='c' where id=1")
                self.assertNotEqual(before, digest(), 'historical row edits must remain visible')
                once = digest()
                conn.execute("insert into doctrine_revision values (1,'c')")
                self.assertNotEqual(once, digest(), 'duplicate multiplicity must remain visible')
                conn.execute('delete from doctrine_revision')
                conn.execute("insert into doctrine_revision select i,repeat('x',1048576) from generate_series(1,257) i")
                large = digest()
                self.assertRegex(large, r'^sha256:[a-f0-9]{64}$')
                conn.execute("update doctrine_revision set body=body||'changed' where id=257")
                self.assertNotEqual(large, digest())


if __name__ == '__main__':
    unittest.main()
