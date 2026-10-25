"""Retired approval guards cannot veto forward control maintenance."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

import migrate

ROOT = Path(__file__).resolve().parents[1]
CONTROL = 'delegation_names_model_and_effort'
spec = importlib.util.spec_from_file_location('control_metadata_shadow', ROOT / 'ops/migration-shadow.py')
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)


class CurrentGrantCatalogTests(unittest.TestCase):
    def test_exact_new_relation_grant_is_accounted_for_against_the_sealed_baseline(self):
        sys.path.insert(0, str(ROOT / 'ops'))
        from scac_mutation_db_inventory import validate_registered_grants
        categories = ('secdef_execute', 'relation_dml', 'column_dml')
        baseline = {name: {'count': 0, 'digest':
            'sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'} for name in categories}
        grant = {'ingress_key': 'db-relation-acl:public.doctrine_embedding:carr_writer:insert',
                 'ingress_kind': 'db_relation_acl', 'relation': 'public.doctrine_embedding',
                 'relation_kind': 'r', 'grantee': 'carr_writer', 'privilege': 'insert', 'grantable': False}
        contract = {'secdef_execute': [], 'relation_dml': [grant]}
        projection = {name: [] for name in categories}
        projection['relation_dml'] = [grant]
        validate_registered_grants(projection, baseline, contract)
        for rows in ([], [{**grant, 'grantable': True}], [grant, {**grant, 'privilege': 'update'}]):
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                validate_registered_grants({**projection, 'relation_dml': rows}, baseline, contract)


class ApprovedControlMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bins = shadow.find_pg18(None)
        if cls.bins is None:
            raise unittest.SkipTest('disposable PostgreSQL 18 is unavailable')
        import psycopg
        cls.psycopg = psycopg
        cls.child = shadow.local_pg.scrub_cloud_environment(os.environ)
        cls.fixture = shadow.local_pg.DisposablePostgres('approved-control.', cls.bins / 'pg_ctl', cls.child)
        cls.addClassCleanup(cls.fixture.close)
        cls.port = shadow.free_port(55800)
        cls.dsn = f'postgres://carr_ci@127.0.0.1:{cls.port}/carr_ci'
        cls.fixture.run([cls.bins / 'initdb', '-D', cls.fixture.root / 'data', '-U', 'carr_ci',
                         '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, capture_output=True)
        cls.fixture.run([cls.bins / 'pg_ctl', '-D', cls.fixture.root / 'data', '-l', cls.fixture.root / 'postgres.log',
                         '-o', f'-h 127.0.0.1 -p {cls.port} -k {cls.fixture.root}', '-w', 'start'],
                        check=True, capture_output=True)
        cls.fixture.run([cls.bins / 'createdb', '-h', '127.0.0.1', '-p', str(cls.port), '-U', 'carr_ci', 'carr_ci'],
                        check=True, capture_output=True)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('create role neondb_owner')
        loaded = subprocess.run([str(cls.bins / 'psql'), cls.dsn, '-X', '-v', 'ON_ERROR_STOP=1', '-q',
                                 '-f', str(ROOT / 'db/schema.sql')], env=cls.child,
                                capture_output=True, text=True, timeout=120)
        if loaded.returncode:
            raise RuntimeError(loaded.stderr[-2000:])
        sys.path.insert(0, str(ROOT / 'ops'))
        from rule_projection_fixture import seed_reviewed_rule_projection
        with psycopg.connect(cls.dsn) as conn, conn.cursor() as cur:
            seed_reviewed_rule_projection(cur, slug='control-retirement-fixture',
                title='Control retirement regression', rule_label='Control retirement fixture',
                uuid_tail='000000009445')

    def test_approved_contracts_allow_forward_maintenance_and_preserve_evidence(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.transaction():
                conn.execute("insert into actor(slug,kind,display_name) values ('joe','human','Joe') on conflict(slug) do nothing")
                joe = conn.execute("select id from actor where slug='joe'").fetchone()[0]
                rule = conn.execute("""insert into rule(statement,human_quote,taught_by,status)
                    values ('Name the delegation model and effort.','Local regression approval.',%s,'proposed')
                    returning id""", (joe,)).fetchone()[0]
                conn.execute("""insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                    values (%s,left(%s::text,8),'control','{}','shared','Migration regression fixture',
                    'tools/test_migrate_control_metadata.py',repeat('0',64))""", (rule, rule))
                conn.execute("""insert into ops.rule_control_binding(rule_id,control_key,statement_hash,binding_contract)
                    select id,%s,encode(digest(statement,'sha256'),'hex'),'{"fixture":"approved control metadata"}'
                    from rule where id=%s""", (CONTROL, rule))
                conn.execute('create role carr_authority_joe login; grant carr_authority to carr_authority_joe')
                conn.execute("""select set_config('carr.acting_actor_slug','joe',true),
                    set_config('carr.sponsoring_human_slug','joe',true),
                    set_config('carr.verified_human_actor_slug','joe',true)""")
                conn.execute('set session authorization carr_authority_joe')
                approved = conn.execute("""select ops.approve_rule(%s,'machine_enforceable',array[%s],
                    'metadata-regression','Local migration regression')""", (rule, CONTROL)).fetchone()[0]
                conn.execute('reset session authorization')
                self.assertEqual(approved['policy_status'], 'active')
            receipt = conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar where rule_id=%s', (rule,)).fetchone()
            for statement, params in (
                ('update ops.enforcement_control_catalog set installed=false where control_key=%s', (CONTROL,)),
                ('update ops.rule_admission set admitted_at=clock_timestamp() where rule_id=%s', (rule,)),
                ('delete from ops.rule_control_binding where rule_id=%s', (rule,)),
                ('delete from ops.rule_enforcement_point where rule_id=%s', (rule,)),
            ):
                with self.subTest(statement=statement), conn.transaction(force_rollback=True):
                    conn.execute(statement, params)
            self.assertEqual(conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar where rule_id=%s', (rule,)).fetchone(), receipt)
            with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'append-only'):
                with conn.transaction():
                    conn.execute('delete from ops.rule_approval_receipt where rule_id=%s', (rule,))


if __name__ == '__main__':
    unittest.main()
