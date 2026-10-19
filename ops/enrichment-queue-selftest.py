#!/usr/bin/env python3
"""Synthetic queue policy tests. No production reads or credentials."""
from __future__ import annotations

import importlib.util
import unittest
import json
import os
import subprocess
import sys

import psycopg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('enrichment_queue', ROOT / 'tools/enrichment-queue.py')
assert spec and spec.loader
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


def snapshot(**changes):
    result = dict(parties=[], vendors=[], leads=[], clients=[], deals=[], participants=[],
                expired=[], verified=[], weekly_subject_count=0)
    result.update(changes)
    return result


class QueueTests(unittest.TestCase):
    def test_empty_contact_queue(self):
        self.assertEqual(queue.plan('contact', snapshot()),
                         dict(selected=[], remaining=0, cap=40, trimmed=False, non_contact_expired=[]))

    def test_contact_bands_exclusions_and_subject_deduplication(self):
        data = snapshot(
            parties=[dict(id=str(i), name=str(i), contact_state='active') for i in range(8)],
            vendors=[dict(id='type', party_id='1', disposition='active'),
                     dict(id='location', party_id='2', disposition='active', category_slug='gc'),
                     dict(id='vertical', party_id='3', disposition='active', category_slug='gc', verticals=[])],
            leads=[dict(id='gap', party_id='4', created_at='2026-01-01')],
            expired=[dict(subject_type='vendor', subject_id='type', observed_at='2026-01-01'),
                     dict(subject_type='vendor', subject_id='type', observed_at='2026-01-02'),
                     dict(subject_type='commit', subject_id='commit', observed_at='2026-01-01'),
                     dict(subject_type='party', subject_id='5', observed_at='2026-01-01')])
        data['parties'][3].update(city='City', county='County')
        data['parties'][5]['contact_state'] = 'do_not_contact'
        result = queue.plan('contact', data)
        self.assertEqual([(x['subject_type'], x['subject_id']) for x in result['selected']],
                         [('vendor', 'type'), ('vendor', 'location'), ('vendor', 'vertical'), ('lead', 'gap')])
        self.assertEqual(result['non_contact_expired'], [dict(subject_type='commit', subject_id='commit')])

    def test_deal_history_bands_and_budget_boundary(self):
        data = snapshot(
            parties=[dict(id=str(i), name=str(i), contact_state='active') for i in range(60)],
            clients=[dict(id=str(i), party_id=str(i), created_at='2026-01-01',
                          status='active_deal', roster_ref='ref') for i in range(50)],
            deals=[dict(id='old', client_id='0', created_at='2026-01-01', salesforce_id='sf-old'),
                   dict(id='new', client_id='1', created_at='2026-02-01', salesforce_id='sf-new')],
            participants=[dict(deal_id='old', party_id='55')],
            weekly_subject_count=29)
        data['clients'].append(dict(id='anomaly', party_id='56', created_at='2026-01-01',
                                    status='closed', roster_ref=None))
        data['clients'][2]['status'] = 'closed'
        data['verified'] = [dict(subject_type='client', subject_id='3')]
        data['parties'][4]['contact_state'] = 'do_not_contact'
        result = queue.plan('deal-history', data)
        self.assertEqual(result['cap'], 25)
        self.assertFalse(result['trimmed'])
        self.assertEqual([x['subject_id'] for x in result['selected'][:2]], ['1', '0'])
        self.assertEqual(result['remaining'], 24)
        data['weekly_subject_count'] = 30
        trimmed = queue.plan('deal-history', data)
        self.assertEqual((trimmed['cap'], len(trimmed['selected']), trimmed['remaining']), (15, 15, 34))
        self.assertTrue(trimmed['trimmed'])
        data['clients'] = data['clients'][:5] + data['clients'][-1:]
        tail = queue.plan('deal-history', data)
        self.assertEqual([x['reason'] for x in tail['selected']],
                         ['salesforce_client', 'salesforce_client', 'counterparty', 'identity_anomaly'])

    def test_stable_ordering_cap_and_noncontact_only_queue(self):
        parties = [dict(id=str(i), name='Same', contact_state='active', city='City', county='County')
                   for i in range(45)]
        vendors = [dict(id=f'{i:02}', party_id=str(i), disposition='active', is_target=False,
                        relationship_level=1, category_slug=None) for i in range(45)]
        vendors[-1]['is_target'] = True
        vendors[-2]['relationship_level'] = 5
        data = snapshot(parties=parties, vendors=vendors)
        first = queue.plan('contact', data)
        self.assertEqual((len(first['selected']), first['remaining']), (40, 5))
        self.assertEqual([x['subject_id'] for x in first['selected'][:3]], ['44', '43', '00'])
        data['vendors'].reverse()
        data['parties'].reverse()
        self.assertEqual(queue.plan('contact', data), first)
        noncontact = queue.plan('contact', snapshot(expired=[dict(subject_type='repo', subject_id='r')]))
        self.assertEqual(noncontact['selected'], [])
        self.assertEqual(noncontact['non_contact_expired'], [dict(subject_type='repo', subject_id='r')])

    def test_newest_import_uses_instants_and_contact_only_expiry(self):
        data = snapshot(parties=[dict(id='p', contact_state='active'), dict(id='q', contact_state='active')],
                        clients=[dict(id='c', party_id='p', created_at='2026-01-01T00:00:00Z',
                                      status='closed', roster_ref='ref'),
                                 dict(id='other', party_id='q', created_at='2026-01-01T00:00:00Z',
                                      status='closed', roster_ref='ref')],
                        deals=[dict(id='a', client_id='c', salesforce_id='sf',
                                    created_at='2026-01-01T12:00:00+00:00'),
                               dict(id='b', client_id='c', salesforce_id='sf2',
                                    created_at='2026-01-01T10:00:00-06:00'),
                               dict(id='c', client_id='other', salesforce_id='sf3',
                                    created_at='2026-01-01T14:00:00Z')],
                        expired=[dict(subject_type='party', subject_id='p', observed_at='2025-01-01')])
        result = queue.plan('deal-history', data)
        self.assertEqual([(x['subject_type'], x['subject_id']) for x in result['selected']],
                         [('client', 'c'), ('client', 'other')])

    def test_prompt_invokes_planner_and_deletes_selection_prose(self):
        for filename, lane in [('contact-enrichment-weekly', 'contact'),
                               ('deal-history-research-weekly', 'deal-history')]:
            text = (ROOT / 'ops/scheduled-tasks' / (filename + '.SKILL.md')).read_text()
            self.assertIn('tools/db-tap.py run tools/enrichment-queue.py ' + lane, text)
            self.assertNotIn('PICK THE ', text)
            self.assertNotIn('TOKEN-BUDGET INTERACTION', text)
            self.assertNotIn('QUEUE SOURCE ADDITION', text)


    def test_reverification_order_uses_age_floor_touches_then_oldest(self):
        data = snapshot(parties=[dict(id=i, contact_state='active') for i in ['a','b','c','d']],
                        expired=[dict(subject_type='party', subject_id='a', past_age_floor=False,
                                      subject_touches=100, observed_at='2025-01-01T00:00:00Z'),
                                 dict(subject_type='party', subject_id='b', past_age_floor=True,
                                      subject_touches=1, observed_at='2026-01-01T00:00:00Z'),
                                 dict(subject_type='party', subject_id='c', past_age_floor=True,
                                      subject_touches=2, observed_at='2026-01-01T10:00:00-06:00'),
                                 dict(subject_type='party', subject_id='d', past_age_floor=True,
                                      subject_touches=2, observed_at='2026-01-01T12:00:00Z')])
        self.assertEqual([x['subject_id'] for x in queue.plan('contact', data)['selected']],
                         ['d', 'c', 'b', 'a'])

    def test_invalid_budget_and_missing_snapshot_fail_instead_of_empty(self):
        for count in [None, -1, True, '30']:
            with self.subTest(count=count), self.assertRaises(ValueError):
                queue.plan('deal-history', snapshot(weekly_subject_count=count))
        with self.assertRaises(KeyError):
            queue.plan('contact', {})


class PostgresCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
        pg_spec = importlib.util.spec_from_file_location('local_pg', ROOT / 'ops/local-pg-ci.py')
        local_pg = importlib.util.module_from_spec(pg_spec)
        sys.modules[pg_spec.name] = local_pg
        pg_spec.loader.exec_module(local_pg)
        binaries = local_pg.find_postgres_binaries()
        budget = postgres_fixture_group()
        budget.__enter__()
        cls.addClassCleanup(budget.__exit__, None, None, None)
        env = local_pg.scrub_cloud_environment(os.environ)
        pg = DisposablePostgres('enrichment-test-', binaries.pg_ctl, env=env)
        cls.addClassCleanup(pg.close)
        data = pg.root / 'data'
        pg.run([binaries.initdb, '-D', data, '-U', 'fixture', '-A', 'trust', '--no-locale'],
               check=True, capture_output=True, timeout=60)
        pg.run([binaries.pg_ctl, '-D', data, '-l', pg.root / 'pg.log', '-o',
                f"-k {pg.root} -h ''", '-w', 'start'], check=True, capture_output=True, timeout=60)
        cls.dsn = f'host={pg.root} dbname=postgres user=fixture'
        cls.env = dict(env, DATABASE_URL=cls.dsn)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute("""
              create table party(id text, name text, contact_state text default 'active',
                city text, county text, title text, org_id text, email text, phone text, cell text,
                merged_into text, deleted_at timestamptz);
              create table vendor(id text, party_id text, disposition text default 'active',
                category_slug text, is_target boolean default false, relationship_level int,
                verticals text[], merged_into text);
              create table lead(id text, party_id text, created_at timestamptz default now());
              create table client(id text, party_id text, created_at timestamptz default now(),
                status text default 'engaged', roster_ref text, merged_into text);
              create table deal(id text, client_id text, salesforce_id text, created_at timestamptz);
              create table deal_participant(deal_id text, party_id text);
              create table v_expired_verification(subject_type text, subject_id text,
                observed_at timestamptz, past_age_floor boolean, subject_touches int);
              create table record_flag(subject_type text, subject_id text, kind text,
                expires_on date, observed_at timestamptz);
              create view v_record_flag_subject as select * from record_flag
                where current_setting('transaction_read_only') = 'on';
              insert into party(id,name) select i::text, 'Synthetic' from generate_series(1,45) i;
              insert into client(id,party_id,roster_ref) select id,id,id from party;
              insert into party(id,name,merged_into) values ('merged-party','Synthetic','1');
              insert into party(id,name,deleted_at) values ('deleted-party','Synthetic',now());
              insert into client(id,party_id,roster_ref) values
                ('merged-party','merged-party','ref'), ('deleted-party','deleted-party','ref');
              update party set contact_state='do_not_contact' where id='45';
              update client set merged_into='1' where id='44';
              insert into vendor(id,party_id,merged_into) values ('merged-vendor','1','2');
              insert into record_flag values ('party','1','verified',current_date-1,now());
              insert into record_flag values ('party','1','email',null,now());
              insert into record_flag values ('client','43','verified',current_date+1,now());
              insert into record_flag values ('party','old','email',null,now()-interval '8 days');
              insert into record_flag values ('party','future','email',null,now()+interval '8 days');
            """)

    def run_cli(self, lane):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/enrichment-queue.py'), lane],
                                env=self.env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_null_status_identity_anomaly_and_tombstone_exclusions(self):
        parties = [
            ('null-status-party', 'active', None, None),
            ('null-status-dnc-party', 'do_not_contact', None, None),
            ('null-status-merged-party', 'active', '1', None),
            ('null-status-deleted-party', 'active', None, '2026-01-01T00:00:00Z'),
            ('closed-roster-party', 'active', None, None),
        ]
        clients = [
            ('null-status-client', 'null-status-party', None, 'C-SYN', None),
            ('null-status-dnc-client', 'null-status-dnc-party', None, 'C-DNC', None),
            ('null-status-merged-client', '1', None, 'C-MERGED', '1'),
            ('null-status-merged-party-client', 'null-status-merged-party', None, 'C-MERGED-PARTY', None),
            ('null-status-deleted-party-client', 'null-status-deleted-party', None, 'C-DELETED-PARTY', None),
            ('closed-roster-client', 'closed-roster-party', 'closed', 'C-CLOSED', None),
        ]
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("update client set status='closed' where id in (select i::text from generate_series(1,45) i)")
            for party_id, state, merged_into, deleted_at in parties:
                conn.execute(
                    "insert into party(id, name, contact_state, merged_into, deleted_at) "
                    "values (%s, 'Synthetic', %s, %s, %s)",
                    (party_id, state, merged_into, deleted_at))
            for client in clients:
                conn.execute(
                    "insert into client(id, party_id, status, roster_ref, merged_into) "
                    "values (%s, %s, %s, %s, %s)", client)
        try:
            selected = self.run_cli('deal-history')['selected']
            by_id = {row['subject_id']: row for row in selected}
            self.assertEqual(by_id.get('null-status-client'), {
                'subject_type': 'client', 'subject_id': 'null-status-client',
                'reason': 'identity_anomaly'})
            for excluded_id in ('null-status-dnc-client', 'null-status-merged-client',
                                'null-status-merged-party-client', 'null-status-deleted-party-client',
                                'closed-roster-client'):
                self.assertNotIn(excluded_id, by_id)
        finally:
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute("delete from client where id like 'null-status-%' or id='closed-roster-client'")
                conn.execute("delete from party where id like 'null-status-%' or id='closed-roster-party'")
                conn.execute("update client set status='engaged' where id in (select i::text from generate_series(1,45) i)")

    def test_reader_cli_budget_window_tombstones_and_failure(self):
        contact = self.run_cli('contact')
        self.assertEqual(contact['selected'][0]['subject_type'], 'client')
        self.assertEqual(contact['remaining'], 3)
        normal = self.run_cli('deal-history')
        self.assertEqual((normal['cap'], normal['remaining']), (25, 17))
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("""insert into record_flag
              select 'party', 'budget-'||i, 'email', null, now() from generate_series(1,28) i""")
        trimmed = self.run_cli('deal-history')
        self.assertEqual((trimmed['cap'], trimmed['remaining']), (15, 27))
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute('alter view v_record_flag_subject rename to unavailable_fixture')
        result = subprocess.run([sys.executable, str(ROOT / 'tools/enrichment-queue.py'), 'contact'],
                                env=self.env, text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
