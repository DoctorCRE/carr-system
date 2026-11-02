#!/usr/bin/env python3
"""Exercise the brief's SQL calendar using only disposable, synthetic PostgreSQL.

Substitute SQL clock expressions to test historical instants without changing
the host clock. All view predicates and timestamp conversions remain intact.
"""
import importlib.util
import os
import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
from migration_source import read_migration

spec = importlib.util.spec_from_file_location('local_pg', ROOT / 'ops/local-pg-ci.py')
assert spec is not None and spec.loader is not None
local_pg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = local_pg
spec.loader.exec_module(local_pg)

SOURCES = {
    'v_last_touch': '0033_deal_inherits_client_touch.sql',
    'v_today_triage': '0094_call_mode_post_call.sql',
    'v_claim_card': '0086_candidate_claim_card.sql',
    'v_renewal_decision_queue': '0279_carr_lease_renewal_provider.sql',
    'v_renewal_decision_queue_status': '0279_carr_lease_renewal_provider.sql',
}
MIGRATION = ROOT / 'migrations/0953_morning_brief_chicago_calendar.sql'

SCHEMA = """
create role carr_reader;
create role carr_writer;
create role carr_jobs;
create role carr_exporter;
create role carr_authority;
create role dot_reader noinherit;
create role carr_backup noinherit;
create table actor(id int, slug text);
create table next_action(id int,subject_type text,subject_id int,owner_id int,
  description text,due_on date,hold_until date,status text);
create table capture_post_call_action(id int,deal_id int,owner_id int,description text,due_on date,status text);
create table critical_date(id int,deal_id int,kind text,note text,due_on date,status text);
create table ingest_inbox(id int,payload jsonb,source text,received_at timestamptz,status text);
create table v_ref_index(subject_type text,subject_id int,display_name text,org_name text,ref text);
create table candidate_pool(id int,version int,source text,name text,org_name text,vertical text,city text,
 county text,state text,segment text,segment_play text,score int,score_basis text,est_lease_event date,
 est_basis text,dup_tier text,dup_ref text,dup_basis text,email text,phone text,created_at timestamptz,
 status text,dup_do_not_contact boolean);
create table activity(kind text,summary text,occurred_at timestamptz,deal_id int,client_id int,lead_id int,vendor_id int);
create table activity_kind(slug text,is_contact boolean);
create table deal(id int,client_id int);
create table client(id int,party_id int,vertical text,created_by int);
create table party(id int,name text,city text,state text,org_id int,email text,phone text,merged_into int,deleted_at timestamptz);
create table deal_participant(deal_id int,actor_id int,role text,to_at timestamptz);
create table lease(deal_id int,client_id int,expiration_on date,created_at timestamptz,updated_at timestamptz,
 status text,evidence_kind text,evidence_ref text,source text);
insert into actor values(1,'joe'),(2,'dell');
insert into activity_kind values('call',true),('analysis',false),('note',false);
insert into party(id,name,email) values(1,'Synthetic practice','fixture@example.test');
insert into client values(1,1,'medical',1);
insert into deal values(1,1);
"""


def definition(name, source):
    text = read_migration(ROOT, source)
    statement = re.search(r'create (?:or replace )?view ' + name + r'\s+as\b.*?;', text, re.I | re.S)[0]
    return re.sub(r'^create view', 'create or replace view', statement, flags=re.I)


class ChicagoCalendar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.group = postgres_fixture_group()
        cls.group.__enter__()
        cls.addClassCleanup(cls.group.__exit__, None, None, None)
        b = local_pg.find_postgres_binaries()
        cls.pg = DisposablePostgres('carr-brief-calendar-', b.pg_ctl,
                                    env=local_pg.scrub_cloud_environment(os.environ))
        cls.addClassCleanup(cls.pg.close)
        data = cls.pg.root / 'data'
        cls.pg.run([b.initdb,'-D',data,'-U','fixture','-A','trust'], check=True,capture_output=True,timeout=60)
        cls.pg.run([b.pg_ctl,'-D',data,'-l',cls.pg.root/'pg.log','-o',f"-k {cls.pg.root} -h ''",'-w','start'],
                   check=True,capture_output=True,timeout=60)
        cls.command = [b.psql,'-XAt','-h',cls.pg.root,'-U','fixture','-d','postgres','-v','ON_ERROR_STOP=1']
        cls.sql(SCHEMA)
        days = read_migration(ROOT, '0088_triage_names_and_age.sql')
        cls.sql(re.search(r'create or replace function carr_business_days\b.*?\$\$;',days,re.S)[0])

    @classmethod
    def sql(cls, sql):
        sql = f"set timezone='{getattr(cls, 'zone', 'UTC')}';\n" + sql
        result = cls.pg.run(cls.command, input=sql, text=True,capture_output=True,timeout=30)
        if result.returncode:
            raise AssertionError(result.stderr)
        return [line for line in result.stdout.splitlines() if line and not line.startswith(
            ('CREATE','INSERT','SET','DELETE','GRANT','REVOKE','COMMENT','BEGIN','COMMIT','RESET'))]

    def install(self, instant, zone):
        type(self).zone = zone
        self.sql(f"set timezone='{zone}'; create or replace function fixture_now() returns timestamptz language sql immutable as $$select '{instant}'::timestamptz$$;")
        text = '\n'.join(definition(name, source) for name, source in SOURCES.items())
        text += '\ngrant select on ' + ','.join(SOURCES) + ' to carr_reader,dot_reader,carr_backup;'
        if MIGRATION.exists():
            text += '\n' + MIGRATION.read_text()
        text = re.sub(r'\bcurrent_date\b', '(fixture_now()::date)', text, flags=re.I)
        text = re.sub(r'\bcurrent_timestamp\b', 'fixture_now()', text, flags=re.I)
        self.sql(text)
        for table, column in [('next_action','id'),('capture_post_call_action','id'),('critical_date','id'),
                              ('ingest_inbox','id'),('candidate_pool','id'),('lease','deal_id'),('activity','kind')]:
            self.sql(f'delete from {table} where {column} is not null')

    def test_calendar_predicates_and_timestamp_days_ignore_session_timezone(self):
        cases = [
            ('2026-10-24T01:00:00Z','2026-10-23'),
            ('2026-01-24T01:00:00Z','2026-01-23'),
            ('2026-10-24T04:59:59Z','2026-10-23'),
            ('2026-10-24T05:00:00Z','2026-10-24'),
            ('2026-01-24T05:59:59Z','2026-01-23'),
            ('2026-01-24T06:00:00Z','2026-01-24'),
            ('2026-03-08T07:59:59Z','2026-03-08'),
            ('2026-03-08T08:00:00Z','2026-03-08'),
            ('2026-11-01T06:59:59Z','2026-11-01'),
            ('2026-11-01T07:00:00Z','2026-11-01'),
        ]
        for instant, day in cases:
            for zone in ['UTC','America/Chicago']:
                with self.subTest(instant=instant,zone=zone):
                    self.install(instant,zone)
                    self.sql(f"""
                    insert into next_action values
                      (1,'deal',1,1,'due',date '{day}',null,'open'),
                      (2,'deal',1,1,'tomorrow',date '{day}'+1,null,'open'),
                      (3,'deal',1,1,'held',date '{day}'-1,date '{day}'+1,'open'),
                      (4,'deal',1,1,'hold equality',date '{day}',date '{day}','open'),
                      (5,'deal',1,1,'closed',date '{day}',null,'done');
                    insert into capture_post_call_action values(6,1,2,'shared due',date '{day}','open'),(7,1,2,'future',date '{day}'+1,'open');
                    insert into critical_date values(8,1,'notice',null,date '{day}'+14,'open'),(9,1,'notice',null,date '{day}'+15,'open');
                    insert into ingest_inbox values(10,'{{"summary":"Synthetic intake"}}','fixture','{instant}','new');
                    insert into candidate_pool(id,status,dup_do_not_contact,est_lease_event) values(1,'pool',false,date '{day}'+5),(2,'pool',true,date '{day}'),(3,'declined',false,date '{day}');
                    insert into lease(deal_id,client_id,expiration_on,status,evidence_kind,evidence_ref,source) values
                      (1,1,date '{day}'-90,'current','executed_lease','fixture','fixture'),
                      (1,1,date '{day}'+548,'current','lease_abstract','fixture','fixture'),
                      (1,1,date '{day}'-91,'current','executed_lease','fixture','fixture'),
                      (1,1,date '{day}'+549,'current','executed_lease','fixture','fixture'),
                      (1,1,date '{day}','current','web_research','fixture','fixture');
                    insert into activity values('call','Synthetic contact','{instant}',null,1,1,1),('analysis','Not contact','{instant}'::timestamptz+interval '1 day',1,null,null,null);
                    """)
                    self.assertEqual(self.sql('select id from v_today_triage order by id'),['1','4','6','8','10'])
                    self.assertEqual(self.sql("select due_on,business_days_overdue from v_today_triage where id=10"),[day+'|0'])
                    self.assertEqual(self.sql('select days_to_window from v_claim_card'),['5'])
                    expected_renewals = [str(date.fromisoformat(day) + timedelta(days=offset)) for offset in [-90,548]]
                    self.assertEqual(self.sql('select est_lease_event from v_renewal_decision_queue order by est_lease_event'), expected_renewals)
                    self.assertEqual(self.sql('select distinct last_touch from v_last_touch'),[day])
                    self.assertEqual(self.sql('select count(*) from v_last_touch'),['4'])

    def test_reader_preserves_safe_renewal_columns_and_date_function_access(self):
        self.install('2026-10-24T01:00:00Z','UTC')
        columns = self.sql("select column_name from information_schema.columns where table_name='v_renewal_decision_queue'")
        self.assertFalse(set(columns) & {'id','lease_id','deal_id','client_id','party_id','evidence_ref','source','email','phone','address'})
        if MIGRATION.exists():
            self.assertEqual(self.sql("set role carr_reader;select carr_business_date('2026-10-24T01:00:00Z');reset role;"),['2026-10-23'])
            for name in SOURCES:
                self.assertEqual(self.sql(f'set role carr_reader;select count(*) from {name};reset role;'), ['0' if name != 'v_renewal_decision_queue_status' else '2'])
            self.assertEqual(self.sql("select count(*) from pg_proc p, lateral aclexplode(p.proacl) a where p.oid='public.carr_business_date(timestamptz)'::regprocedure and a.grantee=0 and a.privilege_type='EXECUTE'"), ['0'])

    def test_all_view_readers_can_read_populated_calendar_views(self):
        self.install('2026-10-24T01:00:00Z','UTC')
        self.sql("""
          insert into next_action values(1,'deal',1,1,'Synthetic action','2026-10-23',null,'open');
          insert into candidate_pool(id,status,dup_do_not_contact,est_lease_event)
            values(1,'pool',false,'2026-10-24');
          insert into lease(deal_id,client_id,expiration_on,status,evidence_kind,evidence_ref,source)
            values(1,1,'2026-10-23','current','executed_lease','fixture','fixture');
          insert into activity values('call','Synthetic contact','2026-10-24T01:00:00Z',null,1,1,1);
        """)
        checks = {
            'select due_on from v_today_triage': ['2026-10-23'],
            'select days_to_window from v_claim_card': ['1'],
            'select est_lease_event from v_renewal_decision_queue': ['2026-10-23'],
            'select distinct last_touch from v_last_touch': ['2026-10-23'],
        }
        for role in ['dot_reader','carr_backup']:
            for query, expected in checks.items():
                with self.subTest(role=role,query=query):
                    self.assertEqual(self.sql(f'set role {role};{query};reset role;'), expected)
        self.assertEqual(self.sql("select count(*) from pg_proc p, lateral aclexplode(p.proacl) a where p.oid='public.carr_business_date(timestamptz)'::regprocedure and a.grantee=0 and a.privilege_type='EXECUTE'"), ['0'])


if __name__ == '__main__':
    unittest.main()
