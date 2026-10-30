"""Retired approval guards cannot veto forward control maintenance."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTROL = 'delegation_names_model_and_effort'
spec = importlib.util.spec_from_file_location('control_metadata_shadow', ROOT / 'ops/migration-shadow.py')
assert spec is not None and spec.loader is not None
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)


class CurrentGrantCatalogTests(unittest.TestCase):
    def test_retired_relation_grants_preserve_the_seal_and_refuse_reinstalled_grants(self):
        sys.path.insert(0, str(ROOT / 'ops'))
        from scac_mutation_db_inventory import digest, validate_registered_grants
        grant = {'ingress_key': 'db-relation-acl:ops.provider_route:carr_writer:insert',
                 'ingress_kind': 'db_relation_acl', 'relation': 'ops.provider_route',
                 'relation_kind': 'r', 'grantee': 'carr_writer', 'privilege': 'insert', 'grantable': False}
        projection = {name: [] for name in ('secdef_execute', 'relation_dml', 'column_dml')}
        baseline = {name: {'count': len(rows), 'digest': digest(rows)}
                    for name, rows in {**projection, 'relation_dml': [grant]}.items()}
        contract = {'retired_relation_dml': [grant]}
        validate_registered_grants(projection, baseline, contract)
        for rows in ([grant], [{**grant, 'grantable': True}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, 'retired grant is still installed'):
                validate_registered_grants({**projection, 'relation_dml': rows}, baseline, contract)

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
        cls.dsn = f'postgres://carr_ci@{quote(str(cls.fixture.root), safe="")}/carr_ci'
        cls.fixture.run([cls.bins / 'initdb', '-D', cls.fixture.root / 'data', '-U', 'carr_ci',
                         '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, capture_output=True)
        cls.fixture.run([cls.bins / 'pg_ctl', '-D', cls.fixture.root / 'data', '-l', cls.fixture.root / 'postgres.log',
                         '-o', f'-c listen_addresses= -k {cls.fixture.root}', '-w', 'start'],
                        check=True, capture_output=True)
        cls.fixture.run([cls.bins / 'createdb', '-h', str(cls.fixture.root), '-U', 'carr_ci', 'carr_ci'],
                        check=True, capture_output=True)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('create role neondb_owner')
        source = (ROOT / 'db/schema.sql').read_text()
        loaded = subprocess.run([str(cls.bins / 'psql'), cls.dsn, '-X', '-v', 'ON_ERROR_STOP=1', '-q'],
                                input=source, env=cls.child, capture_output=True, text=True, timeout=120)
        if loaded.returncode:
            raise RuntimeError(loaded.stderr[-2000:])
        sys.path.insert(0, str(ROOT / 'ops'))
        from rule_projection_fixture import seed_reviewed_rule_projection
        with psycopg.connect(cls.dsn) as conn, conn.cursor() as cur:
            seed_reviewed_rule_projection(cur, slug='control-retirement-fixture',
                title='Control retirement regression', rule_label='Control retirement fixture',
                uuid_tail='000000009445')
        result = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'), '--apply', '--yes'],
            cwd=ROOT, env={**cls.child, 'DATABASE_URL': cls.dsn}, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def test_orphan_retirement_preserves_scac_with_present_or_absent_target(self):
        retirement = next((ROOT / 'migrations').glob('*_drop_orphan_continuity_and_unreferenced_tables.sql'))
        predecessor = sorted(path.name for path in (ROOT / 'migrations').glob('*.sql')
                             if path.name < retirement.name)[-1]
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute('create database orphan_retirement_before template template0')
        before_dsn = self.dsn.rsplit('/', 1)[0] + '/orphan_retirement_before'
        source = subprocess.check_output(['git', 'show',
            '7ec2c96068a8d8c2be2a54cc7f9bdd634a83da67:db/schema.sql'],
            cwd=ROOT, env=self.child, text=True)
        loaded = subprocess.run([str(self.bins / 'psql'), before_dsn, '-X', '-v', 'ON_ERROR_STOP=1', '-q'],
            input=source, env=self.child, capture_output=True, text=True, timeout=120)
        self.assertEqual(loaded.returncode, 0, loaded.stderr[-2000:])
        from rule_projection_fixture import seed_reviewed_rule_projection
        with self.psycopg.connect(before_dsn) as conn, conn.cursor() as cur:
            seed_reviewed_rule_projection(cur, slug='orphan-retirement-fixture',
                title='Orphan retirement regression', rule_label='Orphan retirement fixture',
                uuid_tail='000000009446')
        result = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'), '--apply', '--yes',
                                 '--through', predecessor],
            cwd=ROOT, env={**self.child, 'DATABASE_URL': before_dsn}, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with self.psycopg.connect(before_dsn, autocommit=True) as conn:
            for missing in (False, True):
                with self.subTest(workflow_receipt_absent=missing), conn.transaction(force_rollback=True):
                    if missing:
                        conn.execute('drop table if exists ops.workflow_cutover_retire_receipt_use restrict')
                    self.assertIs(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0], True)
                    conn.execute(retirement.read_text())
                    self.assertIs(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0], True)

    def test_source_integrity_and_grants_survive_retirement(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute('update doctrine_meta set generation=generation+1 where id=1')
            for statement, message in (
                ("update schema_migrations set sha256=repeat('f',64) where filename=(select min(filename) from schema_migrations)", 'applied schema migration rows are immutable'),
                ("delete from schema_migrations where filename=(select max(filename) from schema_migrations)", 'applied schema migration rows are immutable'),
                ('update doctrine_meta set generation=generation-1 where id=1', 'doctrine generation rollback refused'),
                ('delete from ops.rule_load_layer where rule_id=(select rule_id from ops.rule_load_layer limit 1)', 'rule delivery projection is not activation-safe'),
            ):
                with self.subTest(integrity=message), self.assertRaisesRegex(self.psycopg.errors.RaiseException, message):
                    with conn.transaction():
                        conn.execute(statement)
            for role in ('carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'):
                self.assertFalse(conn.execute("select has_function_privilege(%s,'ops.policy_integrity_snapshot()','execute')", (role,)).fetchone()[0])
            self.assertTrue(conn.execute("select has_table_privilege('carr_writer','public.deal_vendor_assignment','insert')").fetchone()[0])
            self.assertEqual(conn.execute("""select count(*) from pg_proc p,
                lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                where p.oid='ops.policy_integrity_snapshot()'::regprocedure
                and a.grantee=0 and a.privilege_type='EXECUTE'""").fetchone()[0], 0)
            self.assertTrue(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])
            for statement in (
                'grant update on public.doctrine_embedding to carr_writer',
                'grant execute on function public.search_doctrine_semantic(text,uuid,text[],integer,text,vector[]) to public',
            ):
                with conn.transaction(force_rollback=True):
                    conn.execute(statement)
                    self.assertFalse(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])
            self.assertTrue(conn.execute('select ops.scac_mutation_catalog_v118_current()').fetchone()[0])

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
                with self.subTest(statement=statement), conn.transaction():
                    self.assertEqual(conn.execute(statement, params).rowcount, 1)
            self.assertEqual(conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar where rule_id=%s', (rule,)).fetchone(), receipt)
            with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'append-only'):
                with conn.transaction():
                    conn.execute('delete from ops.rule_approval_receipt where rule_id=%s', (rule,))


class SnapshotRevisionTests(unittest.TestCase):
    def test_fixture_uses_checkout_when_remote_main_has_a_newer_migration(self):
        if shadow.find_pg18(None) is None:
            self.skipTest('disposable PostgreSQL 18 is unavailable')
        with tempfile.TemporaryDirectory(prefix='control-snapshot-revision.') as directory:
            clone = Path(directory) / 'checkout'
            child = shadow.local_pg.scrub_cloud_environment(os.environ)
            subprocess.run(['git', 'clone', '--shared', '--quiet', str(ROOT), str(clone)],
                           check=True, capture_output=True, env=child)
            schema = clone / 'db/schema.sql'
            header = 'COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n'
            schema.write_text(schema.read_text().replace(header, header +
                '9999_future_fixture.sql\t' + '0' * 64 + '\t1970-01-01 00:00:00+00\n', 1))
            subprocess.run(['git', 'add', 'db/schema.sql'], cwd=clone, env=child, check=True)
            tree = subprocess.check_output(['git', 'write-tree'], cwd=clone, env=child, text=True).strip()
            ahead = subprocess.check_output(['git', '-c', 'user.name=jbookout', '-c',
                'user.email=jbookout@users.noreply.github.com', 'commit-tree', tree, '-p', 'HEAD'],
                input='Advance the remote schema fixture\n', cwd=clone, env=child, text=True).strip()
            subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', ahead],
                           cwd=clone, env=child, check=True)
            subprocess.run(['git', 'restore', '--source=HEAD', '--staged', '--worktree', 'db/schema.sql'],
                           cwd=clone, env=child, check=True)
            script = clone / 'tools/test_migrate_control_metadata.py'
            script.write_bytes(Path(__file__).read_bytes())
            result = subprocess.run([sys.executable, str(script), 'ApprovedControlMigrationTests'],
                cwd=clone, env=child, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
