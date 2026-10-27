"""Retire scheduler SQL on the supported synthetic PostgreSQL rebuild."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/1001_retire_scheduler_contracts.sql'
spec = importlib.util.spec_from_file_location('scheduler_shadow', ROOT / 'ops/migration-shadow.py')
assert spec is not None and spec.loader is not None
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)

RETIRED_TABLES = (
    'cost_reservation', 'provider_observation', 'cognition_result_cache',
    'provider_route', 'cognition_job', 'legacy_schedule_provider_contract',
    'legacy_schedule_launchd_contract', 'legacy_schedule_surface_registry',
)
EVIDENCE_TABLES = (
    'legacy_schedule_disable_receipt', 'legacy_schedule_observation_receipt',
    'staging_release_approval_receipt', 'job_definition', 'job', 'job_attempt', 'job_receipt',
    'scac_mutation_registry_version', 'scac_mutation_registry_entry',
)


class SchedulerRetirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bins = shadow.find_pg18(None)
        if cls.bins is None:
            raise unittest.SkipTest('requires disposable PostgreSQL 18')
        import psycopg
        cls.psycopg = psycopg
        cls.child = shadow.local_pg.scrub_cloud_environment(os.environ)
        cls.fixture = shadow.local_pg.DisposablePostgres('scheduler-retirement.', cls.bins / 'pg_ctl', cls.child)
        cls.addClassCleanup(cls.fixture.close)
        port = shadow.free_port(55900)
        cls.dsn = f'postgresql://carr_ci@127.0.0.1:{port}/carr_ci'
        for command in (
            [cls.bins / 'initdb', '-D', cls.fixture.root / 'data', '-U', 'carr_ci',
             '--auth=trust', '--encoding=UTF8', '--no-locale'],
            [cls.bins / 'pg_ctl', '-D', cls.fixture.root / 'data', '-l', cls.fixture.root / 'postgres.log',
             '-o', f'-h 127.0.0.1 -p {port} -k {cls.fixture.root}', '-w', 'start'],
            [cls.bins / 'createdb', '-h', '127.0.0.1', '-p', str(port), '-U', 'carr_ci', 'carr_ci'],
        ):
            cls.fixture.run(command, check=True, capture_output=True, timeout=120)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('create role neondb_owner')
        loaded = cls.fixture.run([cls.bins / 'psql', cls.dsn, '-X', '-v', 'ON_ERROR_STOP=1',
                                 '-q', '-f', ROOT / 'db/schema.sql'], capture_output=True, text=True, timeout=180)
        if loaded.returncode:
            raise RuntimeError(loaded.stderr[-2000:])
        migrated = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'), '--apply', '--yes',
], cwd=ROOT,
                                  env={**cls.child, 'DATABASE_URL': cls.dsn}, capture_output=True,
                                  text=True, timeout=180)
        if migrated.returncode:
            raise RuntimeError(migrated.stdout + migrated.stderr)

    @staticmethod
    def evidence(conn):
        return {name: conn.execute(f'select md5(coalesce(string_agg(md5(to_jsonb(t)::text),\'\' order by md5(to_jsonb(t)::text)),\'\')) '
                                  f'from ops.{name} t').fetchone()[0] for name in EVIDENCE_TABLES}

    def test_retired_schema_preserves_history_and_shared_product_contracts(self):
        sys.path.insert(0, str(ROOT / 'ops'))
        from scac_mutation_db_inventory import validate_current_catalog
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            self.seed_evidence(conn)
            before = self.evidence(conn)
            for name in RETIRED_TABLES:
                self.assertIsNone(conn.execute('select to_regclass(%s)', ('ops.' + name,)).fetchone()[0], name)
            self.assertEqual(self.evidence(conn), before)
            for name in EVIDENCE_TABLES[:3]:
                with self.subTest(evidence=name), self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'append-only'):
                    with conn.transaction():
                        conn.execute(f'delete from ops.{name}')
            self.assertTrue(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])
            self.assertEqual(conn.execute("select count(*) from generate_series(1,118) n where "
                "ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v'||n) is true").fetchone()[0], 118)
            validate_current_catalog(conn.cursor())
            self.assert_enqueue(conn)
            self.assert_staging(conn)
            conn.execute('set constraints all immediate')

    def test_reinstalled_retired_relation_grant_fails_closed(self):
        sys.path.insert(0, str(ROOT / 'ops'))
        from scac_mutation_db_inventory import validate_current_catalog
        with self.psycopg.connect(self.dsn) as conn, conn.transaction(force_rollback=True):
            conn.execute('create table ops.provider_route(route_key text)')
            conn.execute('grant insert on ops.provider_route to carr_writer')
            self.assertFalse(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])
            with self.assertRaisesRegex(RuntimeError, 'retired grant is still installed'):
                validate_current_catalog(conn.cursor())

    @staticmethod
    def seed_evidence(conn):
        conn.execute('set local session_replication_role=replica')
        conn.execute("""insert into ops.service(key,name,family,criticality,owner_actor)
            values('synthetic-retirement','Synthetic retirement','internal','low','system')""")
        conn.execute("""insert into ops.job_definition(key,version,risk,execution_kind,execution_contract,
            recurrence,retry_policy,deduplication,completion_contract)
            values('synthetic-retirement',1,'green','deterministic','{"entrypoint":"synthetic"}',
                   '{}','{"max_attempts":2,"timeout_seconds":90}','{}','{}')""")
        conn.execute("""insert into ops.legacy_schedule_disable_receipt
            (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
            select 'synthetic-disable','synthetic-disable',key,version,'synthetic','synthetic','synthetic','joe'
            from ops.job_definition limit 1""")
        conn.execute("""insert into ops.legacy_schedule_observation_receipt
            (receipt_ref,idempotency_key,surface_id,workflow_key,workflow_version,locator,scheduler_state,
             cron_expression,timezone,definition_sha256,provider_revision,source_fingerprint,observed_at,device_id)
            values('synthetic-observation','synthetic-observation','synthetic','synthetic',1,'synthetic',
                   'disabled','0 0 * * *','America/Chicago',repeat('a',64),'synthetic',repeat('b',64),now(),'synthetic')""")
        release = conn.execute("""insert into ops.release
            (release_key,service_id,environment,git_sha,maker_actor,source_kind,source_ref,plan_hash,
             artifact_digest,dependency_lock_digest,test_evidence_ref,security_evidence_ref,maker_verification_ref,
             verifier_actor,verifier_evidence_ref,rollback_ready,rollback_plan_ref)
            select 'synthetic-retirement',id,'staging',repeat('a',40),'synthetic','operator','synthetic',
                   'sha256:'||repeat('a',64),'synthetic','synthetic','synthetic','synthetic','synthetic',
                   'synthetic-verifier','synthetic-verification',true,'synthetic-rollback'
            from ops.service limit 1 returning id""").fetchone()[0]
        conn.execute("""insert into ops.staging_release_approval_receipt
            (idempotency_key,release_id,plan_hash,approved_by_actor,approved_at,approval_expires_at,
             verifier_actor,verifier_evidence_ref,approval_sha256,evidence_ref)
            values(gen_random_uuid(),%s,'sha256:'||repeat('a',64),'joe',now(),now()+interval '1 hour',
                   'synthetic','synthetic','sha256:'||repeat('c',64),
                   'ops.staging-release-approval:sha256:'||repeat('c',64))""", (release,))
        conn.execute('set local session_replication_role=origin')

    def assert_staging(self, conn):
        conn.execute('reset role')
        conn.execute("update ops.release set state='approved' where release_key='synthetic-retirement'")
        self.assertEqual(conn.execute("select state,approved_by_actor,staging_approval_receipt_id from ops.release "
                                      "where release_key='synthetic-retirement'").fetchone(), ('approved', None, None))
        with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'environment is immutable'):
            with conn.transaction():
                conn.execute("update ops.release set state='candidate',environment='production' "
                             "where release_key='synthetic-retirement'")
        conn.execute("update ops.release set plan_hash='sha256:'||repeat('d',64) "
                     "where release_key='synthetic-retirement'")
        self.assertEqual(conn.execute("select state from ops.release where release_key='synthetic-retirement'")
                         .fetchone()[0], 'candidate')
        conn.execute("update ops.release set environment='production' where release_key='synthetic-retirement'")
        with self.assertRaises(self.psycopg.Error):
            with conn.transaction():
                conn.execute("update ops.release set state='ready' where release_key='synthetic-retirement'")

    def assert_enqueue(self, conn):
        conn.execute('set local role carr_jobs')
        query = "select (ops.enqueue_job('synthetic-retirement',1,now(),'{}',%s,%s)).id"
        first = conn.execute(query, ('synthetic-replay', 'replay')).fetchone()[0]
        self.assertEqual(conn.execute(query, ('synthetic-replay', 'replay')).fetchone()[0], first)
        self.assertIsNotNone(conn.execute(query, ('synthetic-shadow', 'shadow')).fetchone()[0])
        for mode, message in (('invalid', 'invalid job mode'), ('live', 'no accepted canary'),
                              ('canary', 'no accepted shadow')):
            with self.subTest(mode=mode), self.assertRaisesRegex(self.psycopg.errors.RaiseException, message):
                with conn.transaction():
                    conn.execute(query, ('synthetic-' + mode, mode))
        self.assertEqual(conn.execute('select max_attempts,timeout_seconds from ops.job where id=%s',
                                      (first,)).fetchone(), (2, 90))

if __name__ == '__main__':
    unittest.main()
