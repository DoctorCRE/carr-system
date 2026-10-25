"""S2 removes lease-bound cache machinery without changing retained cache behavior."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/0966_retire_cache_observations.sql'
FUNCTIONS = (
    'ops.get_cognition_cache_for_job(uuid,uuid,text)',
    'ops.put_cognition_cache_for_job(uuid,uuid,text,text,integer,integer,jsonb,text[],integer)',
    'ops.invalidate_cognition_cache_for_job(uuid,uuid,text)',
)
KEYS = (
    'db-function-acl:ops.get_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text):carr_jobs:execute',
    'db-function-acl:ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer):carr_jobs:execute',
    'db-function-acl:ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text):carr_jobs:execute',
)
spec = importlib.util.spec_from_file_location('cache_retirement_shadow', ROOT / 'ops/migration-shadow.py')
assert spec is not None and spec.loader is not None
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)


class CacheObservationRetirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bins = shadow.find_pg18(None)
        if bins is None:
            raise unittest.SkipTest('disposable PostgreSQL 18 is unavailable')
        import psycopg
        cls.psycopg = psycopg
        child = shadow.local_pg.scrub_cloud_environment(os.environ)
        cls.fixture = shadow.local_pg.DisposablePostgres('cache-retirement.', bins / 'pg_ctl', child)
        cls.addClassCleanup(cls.fixture.close)
        port = shadow.free_port(55900)
        cls.dsn = f'postgres://carr_ci@127.0.0.1:{port}/carr_ci'
        cls.fixture.run([bins / 'initdb', '-D', cls.fixture.root / 'data', '-U', 'carr_ci',
                         '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, capture_output=True)
        cls.fixture.run([bins / 'pg_ctl', '-D', cls.fixture.root / 'data', '-l', cls.fixture.root / 'postgres.log',
                         '-o', f'-h 127.0.0.1 -p {port} -k {cls.fixture.root}', '-w', 'start'],
                        check=True, capture_output=True)
        cls.fixture.run([bins / 'createdb', '-h', '127.0.0.1', '-p', str(port), '-U', 'carr_ci', 'carr_ci'],
                        check=True, capture_output=True)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('create role carr_reader; create role carr_writer; create role carr_jobs')
            conn.execute('''
                create schema ops;
                create table ops.job(id uuid primary key);
                create table ops.job_definition(key text,version integer,primary key(key,version));
                create table ops.cognition_job(key text,version integer);
                create table ops.cognition_result_cache(
                  cache_key text primary key,cognition_key text,cognition_version integer,
                  output_schema_version integer,proposal jsonb,dependency_refs text[],
                  evidence_refs text[] default '{}',validated_at timestamptz,
                  expires_at timestamptz,invalidated_at timestamptz);
                create table ops.job_receipt(id integer primary key);
                create function ops.refuse_job_evidence_rewrite() returns trigger
                language plpgsql as $$begin raise exception 'append-only'; end$$;
                create trigger job_receipt_append_only before update or delete on ops.job_receipt
                for each row execute function ops.refuse_job_evidence_rewrite();
                insert into ops.job values ('00000000-0000-0000-0000-000000000001');
                insert into ops.job_definition values ('synthetic',1);
                insert into ops.cognition_job values ('synthetic',1);
                insert into ops.job_receipt values (1);
            ''')
            conn.execute((ROOT / 'migrations/0195_control_plane_cache_observations.sql').read_text())
            cache = (ROOT / 'migrations/0153_control_plane_resilience.sql').read_text()
            conn.execute(cache[cache.index('create or replace function ops.get_cognition_cache('):
                               cache.index('create or replace function ops.reserve_job_cost(')])
            conn.execute('''
                insert into ops.cognition_cache_observation(job_id,attempt,workflow_key,workflow_version,
                  mode,cache_key,observation_kind)
                values ('00000000-0000-0000-0000-000000000001',1,'synthetic',1,'live','synthetic','miss');
                create table ops.scac_mutation_registry_entry(
                    registry_version text, ingress_key text, contract jsonb,
                    primary key(registry_version,ingress_key));
                create table ops.fixture_seals(version text primary key, valid boolean);
                insert into ops.fixture_seals select 'scac-mutation-registry.v'||n,true from generate_series(1,118) n;
                create function ops.scac_mutation_registry_seal_valid(version text) returns boolean
                language sql stable as $$select valid from ops.fixture_seals where version=$1$$;
            ''')
            for key in KEYS:
                row = {'ingress_key': key, 'grantee': 'carr_jobs', 'privilege': 'execute',
                       'effect_class': 'mutation', 'owner_package': 'fixture',
                       'implementation_state': 'implemented', 'classification_authorizing': False,
                       'source_locator': 'synthetic'}
                conn.execute('insert into ops.scac_mutation_registry_entry values (%s,%s,%s::jsonb)',
                             ('scac-mutation-registry.v118', key, json.dumps(row)))
            cls.set_contract(conn, {'registry_version': 'scac-mutation-registry.v118',
                                   'retired_secdef_execute': [{'ingress_key': 'previous-retirement'}],
                                   'runtime_dml_grants': {'fixture': 'preserve'}})
            conn.execute('''
                create function ops.scac_mutation_catalog_v118_current() returns boolean
                language sql stable as $$
                  select not exists (
                    select 1 from ops.scac_mutation_registry_entry e
                    where (to_regprocedure(regexp_replace(split_part(split_part(
                           e.ingress_key,'db-function-acl:',2), ':carr_jobs:',1),
                           'p_[a-z_]+ ', '', 'g')) is not null) = exists (
                      select 1 from jsonb_array_elements(
                        ops.scac_current_grant_contract()->'retired_secdef_execute') item
                      where item = e.contract - 'effect_class' - 'owner_package' - 'implementation_state'
                        - 'classification_authorizing' - 'source_locator'))
                $$;
            ''')

    @staticmethod
    def set_contract(conn, contract):
        from psycopg import sql
        body = sql.SQL('select {}::jsonb').format(sql.Literal(json.dumps(contract)))
        conn.execute(sql.SQL('create or replace function ops.scac_current_grant_contract() '
                            'returns jsonb language sql immutable set search_path=pg_catalog as {}')
                     .format(sql.Literal(body.as_string(conn))))

    def test_retirement_preserves_history_and_unbound_cache_behavior(self):
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            history = conn.execute('select * from ops.scac_mutation_registry_entry order by ingress_key').fetchall()
            conn.execute(MIGRATION.read_text())
            self.assertIsNone(conn.execute("select to_regclass('ops.cognition_cache_observation')").fetchone()[0])
            for signature in FUNCTIONS:
                self.assertIsNone(conn.execute('select to_regprocedure(%s)', (signature,)).fetchone()[0])
            retired = [{'ingress_key': key, 'grantee': 'carr_jobs', 'privilege': 'execute'} for key in sorted(KEYS)]
            self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0],
                             {**before, 'retired_secdef_execute': [*before['retired_secdef_execute'], *retired]})
            self.assertEqual(conn.execute('select * from ops.scac_mutation_registry_entry order by ingress_key').fetchall(), history)
            self.assertTrue(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])
            self.assertEqual(conn.execute('select count(*) from ops.fixture_seals where valid').fetchone()[0], 118)
            for table in ('job', 'job_definition', 'cognition_job', 'job_receipt'):
                self.assertEqual(conn.execute('select count(*) from ops.' + table).fetchone()[0], 1)
            self.assertTrue(conn.execute("select ops.put_cognition_cache('retained','synthetic',1,1,'{\"ok\":true}',array['dependency'],60)").fetchone()[0])
            self.assertEqual(conn.execute("select ops.get_cognition_cache('retained')").fetchone()[0], {'ok': True})
            self.assertEqual(conn.execute("select evidence_refs from ops.cognition_result_cache where cache_key='retained'").fetchone()[0], [])
            self.assertEqual(conn.execute("select ops.invalidate_cognition_cache('dependency')").fetchone()[0], 1)
            self.assertIsNone(conn.execute("select ops.get_cognition_cache('retained')").fetchone()[0])
            with self.assertRaises(self.psycopg.errors.RaiseException):
                with conn.transaction():
                    conn.execute('delete from ops.job_receipt')

    def test_dependency_refuses_and_rolls_back_grant_changes(self):
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            conn.execute('create view ops.synthetic_consumer as select * from ops.cognition_cache_observation')
            before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            with self.assertRaises(self.psycopg.errors.DependentObjectsStillExist):
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], before)
            self.assertEqual(conn.execute('select count(*) from ops.synthetic_consumer').fetchone()[0], 1)
            for signature in FUNCTIONS:
                self.assertIsNotNone(conn.execute('select to_regprocedure(%s)', (signature,)).fetchone()[0])

    def test_invalid_or_missing_historical_seal_refuses(self):
        for version, valid in ((1, False), (57, None), (118, False)):
            with self.subTest(version=version, valid=valid), self.psycopg.connect(self.dsn) as conn:
                with conn.transaction(force_rollback=True):
                    conn.execute('update ops.fixture_seals set valid=%s where version=%s',
                                 (valid, f'scac-mutation-registry.v{version}'))
                    with self.assertRaises(self.psycopg.errors.RaiseException):
                        with conn.transaction():
                            conn.execute(MIGRATION.read_text())
                    self.assertEqual(conn.execute('select count(*) from ops.cognition_cache_observation').fetchone()[0], 1)

    def test_catalog_drift_before_or_after_drops_rolls_back(self):
        for expression in ('false', 'null::boolean',
                           "to_regclass('ops.cognition_cache_observation') is not null"):
            with self.subTest(expression=expression), self.psycopg.connect(self.dsn) as conn:
                with conn.transaction(force_rollback=True):
                    conn.execute('create or replace function ops.scac_mutation_catalog_v118_current() '
                                 'returns boolean language sql stable as $$select ' + expression + '$$')
                    before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
                    with self.assertRaises(self.psycopg.errors.RaiseException):
                        with conn.transaction():
                            conn.execute(MIGRATION.read_text())
                    self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], before)
                    self.assertEqual(conn.execute('select count(*) from ops.cognition_cache_observation').fetchone()[0], 1)

    def test_missing_capability_refuses_without_drops(self):
        for key in KEYS:
            with self.subTest(key=key), self.psycopg.connect(self.dsn) as conn:
                with conn.transaction(force_rollback=True):
                    conn.execute('delete from ops.scac_mutation_registry_entry where ingress_key=%s', (key,))
                    with self.assertRaises(self.psycopg.errors.RaiseException):
                        with conn.transaction():
                            conn.execute(MIGRATION.read_text())
                    self.assertEqual(conn.execute('select count(*) from ops.cognition_cache_observation').fetchone()[0], 1)

    def test_duplicate_retirement_refuses_without_changes(self):
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            contract = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            contract['retired_secdef_execute'].append({'ingress_key': KEYS[0]})
            self.set_contract(conn, contract)
            conn.execute('create or replace function ops.scac_mutation_catalog_v118_current() '
                         'returns boolean language sql stable as $$select true$$')
            with self.assertRaises(self.psycopg.errors.RaiseException):
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], contract)
            self.assertEqual(conn.execute('select count(*) from ops.cognition_cache_observation').fetchone()[0], 1)

    def test_busy_ledger_times_out_and_rolls_back(self):
        with self.psycopg.connect(self.dsn) as blocker, self.psycopg.connect(self.dsn) as conn:
            blocker.execute('lock table ops.cognition_cache_observation in access share mode')
            before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            conn.execute("set local statement_timeout='8s'")
            with self.assertRaises(self.psycopg.errors.LockNotAvailable):
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], before)
            self.assertEqual(conn.execute('select count(*) from ops.cognition_cache_observation').fetchone()[0], 1)
            for signature in FUNCTIONS:
                self.assertIsNotNone(conn.execute('select to_regprocedure(%s)', (signature,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
