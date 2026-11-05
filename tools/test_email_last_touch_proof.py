#!/usr/bin/env python3
"""Test bounded email readback with synthetic fixtures and read-only PostgreSQL."""
import importlib.util
import json
import os
import re
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('last_touch_proof', ROOT / 'tools/email-last-touch-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
START = '2026-11-01T00:00:00Z'
END = '2026-11-03T00:00:00Z'
ACCOUNT = 'fixture@example.test'
IDENTITY = dict(mailbox_owner='joe', mailbox_account=ACCOUNT, item_id='source')


def request(ref='C-1', **changes):
    payload = dict(ref=ref, kind='email_in', occurred_at='2026-11-01T01:00:00Z', email_identity=IDENTITY)
    payload.update(changes)
    return dict(verb='log-activity', acknowledged=True, disposition='acknowledged',
                activity_id='00000000-0000-0000-0000-000000000001',
                created_at=1793577600, payload=json.dumps(payload))


def row(binding='one', subject_type='client', **changes):
    result = dict(binding=binding, subject_type=subject_type, subject_id='target', excluded=False,
                  dangling=False, activity_id=request()['activity_id'], target_matches=True,
                  kind='email_in', occurred_at='2026-11-01T01:00:00Z', email_identity={**IDENTITY, 'subject': 'target'},
                  latest='2026-11-02', last_touch='2026-11-02', lifecycle=None)
    result.update(changes)
    return result


class ProofTests(unittest.TestCase):
    def run_proof(self, requests=None, rows=None):
        return proof.prove({'requests': requests or {'one': request()}},
                           lambda selected: rows if rows is not None else [row()],
                           ACCOUNT, START, END, lambda: NOW)

    def test_all_types_and_newer_contact_are_corroborated(self):
        requests = {k: request(ref) for k, ref in [('one', 'C-1'), ('two', 'L-1'), ('three', 'V-1')]}
        result = self.run_proof(requests, [row(), row('two', 'lead'), row('three', 'vendor')])
        self.assertTrue(result['ok'])
        self.assertEqual(result['corroborated'], dict(client=1, lead=1, vendor=1))
        self.assertNotIn('fixture@example.test', json.dumps(result))
        self.assertNotIn('source', json.dumps(result))

    def test_every_vendor_binding_is_available_for_private_readback(self):
        entries = {'one': request(), 'two': request('L-1'), 'three': request('V-1'), 'four': request('V-2')}
        result = self.run_proof(entries, [row(), row('two','lead'), row('three','vendor'),
                                         row('four','vendor',subject_id='second-vendor',
                                             email_identity={**IDENTITY,'subject':'second-vendor'})])
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['examples']['vendor']), 2)
        self.assertTrue(all(len(binding) == 64 for binding in result['examples']['vendor']))

    def test_mismatches_are_separate_from_unknown_identity(self):
        for change in [dict(target_matches=False), dict(kind='email_out'),
                       dict(occurred_at='2026-11-02T01:00:00Z'),
                       dict(email_identity={**IDENTITY, 'item_id': 'different'}),
                       dict(activity_id=None), dict(last_touch='2026-10-31')]:
            with self.subTest(change=change):
                self.assertEqual(self.run_proof(rows=[row(**change)])['mismatches'], 1)
        result = self.run_proof(rows=[row(email_identity=None)])
        self.assertEqual(result['unavailable_identities'], 1)
        self.assertEqual(result['mismatches'], 0)
        self.assertFalse(result['ok'])

    def test_exclusions_dangling_missing_and_legacy(self):
        for change, counter in [(dict(excluded=True), 'excluded'),
                                (dict(dangling=True), 'dangling_targets'),
                                (dict(subject_id=None), 'missing_targets')]:
            result = self.run_proof(rows=[row(**change)])
            self.assertEqual(result[counter], 1)
            self.assertEqual(sum(result['active_targets'].values()), 0)
        result = self.run_proof({'one': request(email_identity=None)})
        self.assertEqual(result['unavailable_identities'], 1)
        self.assertEqual(result['mismatches'], 0)

    def test_excluded_requests_do_not_fail_other_corroborated_examples(self):
        entries = {'one': request(), 'two': request('L-1'), 'three': request('V-1'), 'four': request('C-retired')}
        result = self.run_proof(entries, [row(), row('two','lead'), row('three','vendor'),row('four',excluded=True)])
        self.assertTrue(result['ok'])
        self.assertEqual(result['excluded'], 1)

    def test_terminal_lifecycle_does_not_invent_projection_removal(self):
        for state in ['deleted', 'recalled', 'superseded']:
            result = self.run_proof(rows=[row(lifecycle=state)])
            self.assertEqual(result['mismatches'], 0)
            self.assertEqual(result['lifecycle'][state], 1)

    def test_selection_is_bounded_and_account_fixed(self):
        captured = []
        entries = {'one': request(), 'old': {**request(), 'created_at': 1},
                   'refused': {**request(), 'disposition': 'refused'},
                   'unack': {**request(), 'acknowledged': False},
                   'other': request(email_identity={**IDENTITY, 'mailbox_account': 'other@example.test'})}
        proof.prove({'requests': entries}, lambda selected: captured.extend(selected) or [row()],
                    ACCOUNT, START, END, lambda: NOW)
        self.assertEqual([r['binding'] for r in captured], ['one'])
        with self.assertRaises(ValueError):
            proof.prove({'requests': entries}, lambda _: [], ACCOUNT, START,
                        '2027-01-01T00:00:00Z', lambda: NOW)

    def test_missing_or_duplicate_snapshot_rows_fail_closed(self):
        for rows in [[], [row(), row()]]:
            with self.assertRaises(ValueError):
                self.run_proof(rows=rows)

    def test_timezone_instants_and_opaque_output(self):
        result = self.run_proof(rows=[row(occurred_at='2026-10-31T20:00:00-05:00', last_touch='2026-11-02')])
        self.assertEqual(result['mismatches'], 0)
        bad = self.run_proof(rows=[row(target_matches=False)])
        self.assertEqual(len(bad['findings'][0]['binding']), 64)
        self.assertNotIn('C-1', json.dumps(bad))


class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, postgres_fixture_group
        cls.group = postgres_fixture_group()
        cls.group.__enter__()
        cls.addClassCleanup(cls.group.__exit__, None, None, None)
        binaries = postgres_bin_dir()
        cls.pg = DisposablePostgres('carr-last-touch-', binaries / 'pg_ctl',
                                    env={'PATH': os.defpath, 'LC_ALL': 'C'})
        cls.addClassCleanup(cls.pg.close)
        data = cls.pg.root / 'data'
        cls.pg.run([binaries / 'initdb', '-D', data, '-U', 'fixture', '-A', 'trust'],
                   check=True, capture_output=True, timeout=60)
        cls.pg.run([binaries / 'pg_ctl', '-D', data, '-l', cls.pg.root / 'postgres.log',
                    '-o', f"-k {cls.pg.root} -h ''", '-w', 'start'],
                   check=True, capture_output=True, timeout=60)
        cls.dsn = f'host={cls.pg.root} user=fixture dbname=postgres'
        cls.ids = [f'00000000-0000-0000-0000-{n:012d}' for n in range(1, 12)]
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute("""
                create table party(id uuid primary key, merged_into uuid, deleted_at timestamptz);
                create table client(id uuid primary key, party_id uuid, roster_ref text, merged_into uuid);
                create table lead(id uuid primary key, party_id uuid, registry_ref text);
                create table vendor(id uuid primary key, party_id uuid, vendor_ref text, merged_into uuid);
                create table deal(id uuid primary key,client_id uuid);
                create table activity_kind(slug text primary key,is_contact boolean);
                create table activity(id uuid primary key,kind text,summary text,occurred_at timestamptz,
                    client_id uuid,lead_id uuid,vendor_id uuid,deal_id uuid,email_identity jsonb,email_context jsonb);
                insert into activity_kind values ('email_in',true),('call',true),('note',false),('analysis',false);
            """)
            migration = (ROOT / 'migrations/0953_morning_brief_chicago_calendar.sql').read_text()
            conn.execute(re.search(r'create or replace function public.carr_business_date.*?\$\$;', migration, re.S)[0])
            conn.execute(re.search(r'create or replace view v_last_touch as.*?;', migration, re.S)[0])
            for number, (table, refcol, ref) in enumerate([
                    ('client', 'roster_ref', 'C-1'), ('lead', 'registry_ref', 'L-1'),
                    ('vendor', 'vendor_ref', 'V-1')]):
                ident = cls.ids[number]
                conn.execute('insert into party(id) values (%s)', (ident,))
                conn.execute(f'insert into {table}(id,party_id,{refcol}) values (%s,%s,%s)', (ident, ident, ref))
                identity = json.dumps({**IDENTITY, 'item_id': ref, 'subject': ident})
                conn.execute(f"""insert into activity(id,kind,occurred_at,{table}_id,email_identity)
                    values (%s,'email_in','2026-11-01T01:00:00Z',%s,%s::jsonb)""",
                             (cls.ids[number + 3], ident, identity))
            conn.execute("""insert into activity(id,kind,summary,occurred_at,client_id) values
                (%s,'call','unrelated newer contact','2026-11-02T01:00:00Z',%s),
                (%s,'note','last-touch stamp (imported)','2026-11-03T01:00:00Z',%s),
                (%s,'analysis','import does not imply contact','2026-11-04T01:00:00Z',%s)""",
                         (cls.ids[6],cls.ids[0],cls.ids[7],cls.ids[0],cls.ids[8],cls.ids[0]))

    def selected(self):
        return [dict(binding=ref, ref=ref, activity_id=self.ids[n+3])
                for n, ref in enumerate(['C-1','L-1','V-1'])]

    def test_real_sql_projection_and_read_only_guard(self):
        import psycopg
        with patch.dict(os.environ, {'DATABASE_URL': self.dsn, 'PGTZ': 'Asia/Tokyo'}, clear=True):
            rows = proof.database_read(self.selected())
            self.assertEqual({r['subject_type']: r['latest'] for r in rows},
                             dict(client='2026-11-02', lead='2026-10-31', vendor='2026-10-31'))
            self.assertTrue(all(r['latest'] == r['last_touch'] and r['target_matches'] for r in rows))
            with patch.object(proof, 'SQL', 'delete from public.activity where %s::jsonb is not null returning id'):
                with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                    proof.database_read([])
            state = {'requests': {r['ref']: {**request(r['ref'], email_identity={**IDENTITY, 'item_id': r['ref']}),
                      'activity_id': r['activity_id']} for r in self.selected()}}
            result = proof.prove(state, proof.database_read, ACCOUNT, START, END, lambda: NOW)
            self.assertTrue(result['ok'])

    def test_sql_reflects_corrected_target_and_terminal_email(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute("update activity set client_id=null,vendor_id=%s,email_context=%s::jsonb where id=%s",
                         (self.ids[2],json.dumps({'lifecycle': {'state': 'superseded'}}),self.ids[3]))
            rows = conn.execute(proof.SQL, (json.dumps(self.selected()),)).fetchall()
            client = next(r for r in rows if r['subject_type'] == 'client')
            self.assertFalse(client['target_matches'])
            self.assertEqual(client['lifecycle'], 'superseded')
            self.assertEqual(client['latest'], '2026-11-02')
            conn.rollback()

    def test_sql_handles_role_and_party_tombstones_dangling_and_missing(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute('update vendor set merged_into=id')
            conn.execute('update party set deleted_at=now() where id=%s', (self.ids[1],))
            conn.execute('delete from party where id=%s', (self.ids[0],))
            selected = self.selected() + [dict(binding='missing', ref='V-missing', activity_id=None)]
            rows = conn.execute(proof.SQL, (json.dumps(selected),)).fetchall()
            rows = {r['binding']: r for r in rows}
            self.assertTrue(rows['C-1']['dangling'])
            self.assertTrue(rows['L-1']['excluded'])
            self.assertTrue(rows['V-1']['excluded'])
            self.assertIsNone(rows['missing']['subject_id'])
            conn.rollback()

    def test_fixture_cli_needs_no_database(self):
        state = {'requests': {'one': request()}}
        state_path, fixture_path = self.pg.root / 'requests.json', self.pg.root / 'snapshot.json'
        state_path.write_text(json.dumps(state))
        fixture_path.write_text(json.dumps({'rows': [row()]}))
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(StringIO()) as output:
            rc = proof.main(['--requests',str(state_path),'--fixture',str(fixture_path),
                             '--account',ACCOUNT,'--start',START,'--end',END], clock=lambda: NOW)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(output.getvalue())['mismatches'], 0)


if __name__ == '__main__':
    unittest.main()
