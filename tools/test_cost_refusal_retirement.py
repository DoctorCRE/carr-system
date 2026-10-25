"""S1 retires cost admission atomically without erasing sealed capability history."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/0963_retire_cost_refusal.sql'
KEY = ('db-function-acl:ops.admit_job_cost(p_job_id uuid, p_lease_token uuid, '
       'p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute')
spec = importlib.util.spec_from_file_location('cost_refusal_shadow', ROOT / 'ops/migration-shadow.py')
assert spec is not None and spec.loader is not None
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)


class CostRefusalRetirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bins = shadow.find_pg18(None)
        if bins is None:
            raise unittest.SkipTest('disposable PostgreSQL 18 is unavailable')
        import psycopg
        cls.psycopg = psycopg
        child = shadow.local_pg.scrub_cloud_environment(os.environ)
        cls.fixture = shadow.local_pg.DisposablePostgres('cost-refusal.', bins / 'pg_ctl', child)
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
                create table ops.provider_route(route_key text primary key);
                create table ops.cost_reservation(id uuid primary key);
                create table ops.job_receipt(id integer primary key);
                create function ops.refuse_job_evidence_rewrite() returns trigger
                language plpgsql as $$begin raise exception 'append-only'; end$$;
                create trigger job_receipt_append_only before update or delete on ops.job_receipt
                for each row execute function ops.refuse_job_evidence_rewrite();
                insert into ops.job values ('00000000-0000-0000-0000-000000000001');
                insert into ops.provider_route values ('synthetic');
                insert into ops.cost_reservation values ('00000000-0000-0000-0000-000000000002');
                insert into ops.job_receipt values (1);
            ''')
            conn.execute((ROOT / 'migrations/0165_control_plane_cost_refusals.sql').read_text())
            conn.execute('''
                insert into ops.cost_refusal(job_id,attempt,route_key,estimated_cost_usd,
                    monthly_budget_usd,spent_usd,reserved_usd,reason)
                values ('00000000-0000-0000-0000-000000000001',1,'synthetic',2,1,0,0,'monthly_budget_exceeded');
                create table ops.scac_mutation_registry_entry(
                    registry_version text, ingress_key text, contract jsonb,
                    primary key(registry_version,ingress_key));
                create table ops.fixture_seals(version text primary key, valid boolean);
                insert into ops.fixture_seals select 'scac-mutation-registry.v'||n,true from generate_series(1,118) n;
                create function ops.scac_mutation_registry_seal_valid(version text) returns boolean
                language sql stable as $$select valid from ops.fixture_seals where version=$1$$;
            ''')
            row = {'ingress_key': KEY, 'grantee': 'carr_jobs', 'privilege': 'execute',
                   'effect_class': 'mutation', 'owner_package': 'fixture',
                   'implementation_state': 'implemented', 'classification_authorizing': False,
                   'source_locator': 'synthetic'}
            conn.execute('insert into ops.scac_mutation_registry_entry values (%s,%s,%s::jsonb)',
                         ('scac-mutation-registry.v118', KEY, json.dumps(row)))
            contract = {'registry_version': 'scac-mutation-registry.v118',
                        'retired_secdef_execute': [{'ingress_key': 'previous-retirement'}],
                        'runtime_dml_grants': {'fixture': 'preserve'}}
            cls.set_contract(conn, contract)
            conn.execute("""
                create function ops.scac_mutation_catalog_v118_current() returns boolean
                language sql stable as $$
                  select case when to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)') is not null
                    then not exists (select 1 from jsonb_array_elements(
                      ops.scac_current_grant_contract()->'retired_secdef_execute') item
                      where item->>'ingress_key' like '%ops.admit_job_cost(%')
                    else exists (select 1 from ops.scac_mutation_registry_entry e,
                      jsonb_array_elements(ops.scac_current_grant_contract()->'retired_secdef_execute') item
                      where item = e.contract - 'effect_class' - 'owner_package' - 'implementation_state'
                        - 'classification_authorizing' - 'source_locator') end
                $$;
            """)

    @staticmethod
    def set_contract(conn, contract):
        from psycopg import sql
        body = sql.SQL('select {}::jsonb').format(sql.Literal(json.dumps(contract)))
        conn.execute(sql.SQL('create or replace function ops.scac_current_grant_contract() '
                            'returns jsonb language sql immutable set search_path=pg_catalog as {}')
                     .format(sql.Literal(body.as_string(conn))))

    def test_retirement_removes_only_the_orphan_and_preserves_history(self):
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            history = conn.execute('select * from ops.scac_mutation_registry_entry').fetchall()
            before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            conn.execute(MIGRATION.read_text())
            for query in ("to_regclass('ops.cost_refusal')", "to_regclass('ops.v_cost_refusal_metric')",
                          "to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)')"):
                self.assertIsNone(conn.execute('select ' + query).fetchone()[0])
            after = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            self.assertEqual(after, {**before, 'retired_secdef_execute': [
                *before['retired_secdef_execute'],
                {'ingress_key': KEY, 'grantee': 'carr_jobs', 'privilege': 'execute'}]})
            self.assertEqual(conn.execute('select * from ops.scac_mutation_registry_entry').fetchall(), history)
            self.assertTrue(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])
            self.assertEqual(conn.execute('select count(*) from ops.fixture_seals where valid').fetchone()[0], 118)
            for table in ('job', 'provider_route', 'cost_reservation', 'job_receipt'):
                self.assertEqual(conn.execute('select count(*) from ops.' + table).fetchone()[0], 1)
            with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'append-only'):
                with conn.transaction():
                    conn.execute('delete from ops.job_receipt')

    def test_unexpected_dependency_rolls_back_the_capability_retirement(self):
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            conn.execute('create view ops.synthetic_consumer as select * from ops.cost_refusal')
            before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            with self.assertRaises(self.psycopg.errors.DependentObjectsStillExist):
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], before)
            self.assertEqual(conn.execute('select count(*) from ops.synthetic_consumer').fetchone()[0], 1)
            self.assertIsNotNone(conn.execute("select to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)')").fetchone()[0])

    def test_invalid_or_missing_historical_seal_refuses_before_drops(self):
        for version, valid in ((1, False), (57, None), (118, False)):
            with self.subTest(version=version, valid=valid), self.psycopg.connect(self.dsn) as conn:
                with conn.transaction(force_rollback=True):
                    conn.execute('update ops.fixture_seals set valid=%s where version=%s',
                                 (valid, f'scac-mutation-registry.v{version}'))
                    with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'seal is not valid'):
                        with conn.transaction():
                            conn.execute(MIGRATION.read_text())
                    self.assertEqual(conn.execute('select count(*) from ops.cost_refusal').fetchone()[0], 1)

    def test_catalog_drift_before_or_after_drops_rolls_back(self):
        for expression, message in (("false", 'seal is not valid'),
                                    ("null::boolean", 'seal is not valid'),
                                    ("to_regclass('ops.cost_refusal') is not null", 'unaccounted SCAC capability')):
            with self.subTest(expression=expression), self.psycopg.connect(self.dsn) as conn:
                with conn.transaction(force_rollback=True):
                    conn.execute('create or replace function ops.scac_mutation_catalog_v118_current() '
                                 'returns boolean language sql stable as $$select ' + expression + '$$')
                    before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
                    with self.assertRaisesRegex(self.psycopg.errors.RaiseException, message):
                        with conn.transaction():
                            conn.execute(MIGRATION.read_text())
                    self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], before)
                    self.assertEqual(conn.execute('select count(*) from ops.cost_refusal').fetchone()[0], 1)

    def test_busy_ledger_times_out_and_rolls_back_grant_changes(self):
        with self.psycopg.connect(self.dsn) as blocker, self.psycopg.connect(self.dsn) as conn:
            blocker.execute('lock table ops.cost_refusal in access share mode')
            before = conn.execute('select ops.scac_current_grant_contract()').fetchone()[0]
            conn.execute("set local statement_timeout='8s'")
            with self.assertRaises(self.psycopg.errors.LockNotAvailable):
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            self.assertEqual(conn.execute('select ops.scac_current_grant_contract()').fetchone()[0], before)
            self.assertIsNotNone(conn.execute("select to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)')").fetchone()[0])
            self.assertEqual(conn.execute('select count(*) from ops.cost_refusal').fetchone()[0], 1)

    def test_absent_sealed_capability_refuses_without_drops(self):
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            conn.execute('delete from ops.scac_mutation_registry_entry')
            with self.assertRaises(self.psycopg.errors.NoDataFound):
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            self.assertEqual(conn.execute('select count(*) from ops.cost_refusal').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
