#!/usr/bin/env python3
"""Offline contracts for the local production ledger proof."""
import importlib.util
from contextlib import redirect_stdout
import io
import hashlib
import shutil
import tempfile
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('production_proof', ROOT / 'ops/migration-production-proof.py')
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proof
spec.loader.exec_module(proof)

from migration_number_contract import FROZEN_COLLISIONS
POSTGRES = "--postgres" in sys.argv
if POSTGRES:
    sys.argv.remove("--postgres")

def dump(body, owner='neondb_owner'):
    return f'''-- PostgreSQL database dump
\\restrict random_nonce
-- Dumped from database version 18.1
-- Dumped by pg_dump version 18.1
--
-- Name: example; Type: TABLE; Schema: public; Owner: {owner}
--
{body}
\\unrestrict random_nonce
-- PostgreSQL database dump complete
'''



def repository_ledger():
    return proof.repository_ledger(ROOT, proof.MigrationBaseline.load(ROOT))


class ProofTests(unittest.TestCase):
    def test_baseline_requires_every_original_row_before_pending_suffix(self):
        baseline = proof.MigrationBaseline.load(ROOT)
        covered = dict(baseline.migrations)
        repository = dict(covered, **{'9999_future.sql': 'f' * 64})
        before = dict(covered)
        result = proof.ledger_proof(repository, covered, baseline)
        self.assertTrue(result['ok'])
        self.assertEqual(result['pending'], ['9999_future.sql'])
        self.assertEqual(covered, before)
        for missing in (baseline.migrations[0][0], baseline.cutoff):
            partial = {name: sha for name, sha in covered.items() if name != missing}
            result = proof.ledger_proof(repository, partial, baseline)
            self.assertFalse(result['ok'])
            self.assertIn('missing applied coverage', result['error'])

    def test_pending_suffix_passes(self):
        result = proof.ledger_proof({'0001_a.sql': 'a', '0003_c.sql': 'c'}, {'0001_a.sql': 'a'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['pending'], ['0003_c.sql'])

    def test_ledger_rejects_missing_changed_and_backfilled_files(self):
        for repository, applied in [
            ({'0001_a.sql': 'a'}, {'0001_a.sql': 'a', '0004_unknown.sql': 'd'}),
            ({'0001_a.sql': 'wrong'}, {'0001_a.sql': 'a'}),
            ({'0001_a.sql': 'a', '0002_backfill.sql': 'b', '0003_c.sql': 'c'},
             {'0001_a.sql': 'a', '0003_c.sql': 'c'}),
        ]:
            with self.subTest(applied=applied):
                self.assertFalse(proof.ledger_proof(repository, applied)['ok'])

    def test_same_number_earlier_hole_refuses(self):
        self.assertFalse(proof.ledger_proof(
            {'0001_a.sql': 'a', '0001_b.sql': 'b'}, {'0001_b.sql': 'b'})['ok'])

    def test_partial_strict_atomic_group_refuses(self):
        repository = repository_ledger()
        applied = {name: digest for name, digest in repository.items()
                   if name <= '0872_self_closing_records.sql'}
        result = proof.ledger_proof(repository, applied)
        self.assertFalse(result['ok'])
        self.assertIn('partial strict atomic migration group', result['error'])

    def test_empty_production_fails_closed(self):
        self.assertFalse(proof.ledger_proof({'0001_a.sql': 'a'}, {})['ok'])

    def test_snapshot_drift_refuses(self):
        read_text = Path.read_text
        applied = repository_ledger()
        snapshot = (ROOT / 'db/schema.sql').read_text()
        filename = next(iter(applied))
        row = next(line for line in snapshot.splitlines() if line.startswith(filename + '\t'))
        for kind, drifted in [
            ('edited', snapshot.replace(applied[filename], '0' * 64, 1)),
            ('missing', snapshot.replace(row + '\n', '', 1)),
            ('extra', snapshot.replace(row, row + '\n9999_unknown.sql\t' + '0' * 64 + '\tfixture', 1)),
        ]:
            with self.subTest(kind=kind), \
                 patch.object(proof, 'production_dsn') as credentials, \
                 patch.object(Path, 'read_text', autospec=True,
                              side_effect=lambda path, *args, **kwargs: drifted
                              if path == ROOT / 'db/schema.sql' else read_text(path, *args, **kwargs)):
                with self.assertRaisesRegex(RuntimeError, 'ledger'):
                    proof.live_proof()
                credentials.assert_not_called()


    def test_reconstruction_failure_reports_post_pending_grants_not_run(self):
        repository = repository_ledger()
        for failure in ([RuntimeError('reference failed')],
                        [dict(grants={}, ledger={}, dump=''), RuntimeError('replay failed')]):
            with self.subTest(failure=failure), \
                 patch.object(proof, 'production_dsn', return_value='fixture'), \
                 patch.object(proof, 'read_source', return_value=(repository, '', {})), \
                 patch.object(proof.comparison, 'find_pg18', return_value=Path('/bin')), \
                 patch.object(proof, 'build_schema', side_effect=failure):
                result = proof.live_proof(ledger_only=False)
            self.assertIsNone(result['grants']['ok'])
            self.assertEqual(result['grants']['status'], 'not_run')
            self.assertEqual(result['grants']['stage'], 'production plus pending')
            self.assertFalse(result['equivalence']['ok'])
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(proof, 'live_proof', return_value=result), \
             patch.object(sys, 'argv', ['proof', '--schema', '--report', str(Path(directory) / 'report.json')]), \
             patch.dict(os.environ, GITHUB_ACTIONS='false', CARR_CI_PORTABLE_ONLY='0'), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(proof.main(), 1)
        self.assertIn('grants: NOT RUN stage=production plus pending', output.getvalue())
        self.assertNotIn('grants: FAIL', output.getvalue())

    def test_hosted_refuses_before_credential_resolution(self):
        for flags in ([], ['--schema']):
            result = subprocess.run([sys.executable, str(ROOT / 'ops/migration-production-proof.py'), *flags],
                                    env=dict(os.environ, GITHUB_ACTIONS='true'), capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('LOCAL-ONLY', result.stdout)


    def test_ci_reports_live_proofs_not_run_on_hosted_runners(self):
        source = (ROOT / 'ops/ci.sh').read_text()
        start = source.index('  excluded_reason()')
        end = source.index('  local eligible=', start)
        script = 'scope="$1"; PY="$2"; CARR_CI_PORTABLE_ONLY=0\n' + source[start:end] + '\nexcluded_reason migration-production-proof.py\n'
        result = subprocess.run(['bash', '-c', script, 'scope-fixture',
                                 str(ROOT / 'ops/config/ci-check-scope.json'), sys.executable],
                                env=dict(os.environ, GITHUB_ACTIONS='true'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LOCAL-ONLY:', result.stdout)

    def test_failed_subprocess_diagnostics_redact_credentials(self):
        response = subprocess.CompletedProcess([], 1, b'', b'password=placeholder postgres://reader:placeholder@localhost/example')  # ci-secret-scan: allow synthetic placeholder
        with patch.object(proof.subprocess, 'run', return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                proof.checked(['pg_dump'], {'PGPASSWORD': 'placeholder'}, 'fixture dump')
        self.assertNotIn('placeholder', str(caught.exception))


    def test_dump_envelope_is_normalized_but_structure_is_preserved(self):
        a = dump('CREATE TABLE public.example (id integer);')
        b = a.replace('random_nonce', 'another_nonce').replace('18.1', '18.2')
        self.assertEqual(proof.schema_difference(a, b), [])
        for change in [
            a.replace('integer', 'bigint'),
            a.replace('CREATE TABLE', 'GRANT SELECT ON TABLE'),
        ]:
            self.assertTrue(proof.schema_difference(a, change))


    def test_function_bodies_and_constraint_whitespace_are_preserved(self):
        self.assertTrue(proof.schema_difference(dump("SELECT 'a  b';"), dump("SELECT 'a b';")))
        function = "CREATE FUNCTION x() RETURNS text LANGUAGE sql AS $$\n-- Dumped by pg_dump version literal\nSELECT 'yes';\n$$;"
        self.assertTrue(proof.schema_difference(dump(function), dump(function.replace('literal', 'changed'))))


    def test_privileges_compare_only_roles_present_in_both_environments(self):
        left = dict(roles=['PUBLIC', 'reader', 'source_owner'], privileges=[
            ['TABLE', 'public.example', '', 'reader', 'SELECT', False],
            ['TABLE', 'public.example', '', 'source_owner', 'SELECT', True]])
        right = dict(roles=['PUBLIC', 'reader', 'snapshot_owner'], privileges=[
            ['TABLE', 'public.example', '', 'reader', 'SELECT', False],
            ['TABLE', 'public.example', '', 'snapshot_owner', 'SELECT', True]])
        result = proof.privilege_difference(left, right)
        self.assertTrue(result['ok'])
        self.assertEqual(result['production_only_roles'], ['source_owner'])
        self.assertEqual(result['snapshot_only_roles'], ['snapshot_owner'])
        right['privileges'][0][4] = 'UPDATE'
        result = proof.privilege_difference(left, right)
        self.assertFalse(result['ok'])
        self.assertEqual(result['production_only'], [left['privileges'][0]])
        self.assertEqual(result['snapshot_only'], [right['privileges'][0]])


    def test_provisioning_external_roles_does_not_expand_the_original_acl_scope(self):
        grant = ['table', 'public.example', '', 'external_backup', 'select', False]
        runtime = ['table', 'public.example', '', 'reader', 'select', False]
        left = dict(roles=['reader', 'external_backup'], privileges=[grant, runtime])
        right = dict(roles=['reader', 'external_backup'], privileges=[runtime])
        result = proof.privilege_difference(left, right, ['reader'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['informational_external_acl_counts'], dict(production=1, snapshot=0))
        right['privileges'] = []
        result = proof.privilege_difference(left, right, ['reader'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['production_only'], [runtime])

    def test_common_role_attributes_and_membership_options_must_match(self):
        role = ('reader', False, True, False, False, False, False, -1)
        left = dict(roles=['reader'], role_attributes=[role], privileges=[])
        right = dict(roles=['reader'], role_attributes=[(*role[:2], False, *role[3:])], privileges=[])
        self.assertFalse(proof.privilege_difference(left, right)['ok'])

    def test_object_order_does_not_matter(self):
        a = dump('CREATE TABLE public.example (id integer);')
        b = a.replace('Name: example', 'Name: second').replace('public.example', 'public.second')
        self.assertEqual(proof.schema_difference(a + b, b + a), [])


    def test_dump_credentials_stay_in_environment_with_read_only_enforced(self):
        with patch.object(proof, 'checked', return_value='dump') as run:
            proof.schema_dump('postgres://reader:placeholder@localhost:5433/example?options=-c%20default_transaction_read_only%3Doff',  # ci-secret-scan: allow synthetic placeholder
                              Path('/bin'), {}, 'snapshot-id')
        command, env, label = run.call_args.args
        self.assertNotIn('placeholder', str(command))
        self.assertEqual(env['PGHOST'], 'localhost')
        self.assertEqual(env['PGPORT'], '5433')
        self.assertEqual(env['PGDATABASE'], 'example')
        self.assertEqual(env['PGPASSWORD'], 'placeholder')
        self.assertIn('default_transaction_read_only=on', env['PGOPTIONS'])
        self.assertIn('--schema-only', command)
        self.assertIn('--no-owner', command)
        self.assertIn('--no-acl', command)
        self.assertIn('snapshot-id', command)


    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_committed_snapshot_schema_only_round_trip_with_zero_pending(self):
        shadow = proof.load('round_trip_shadow', ROOT / 'ops/migration-shadow.py')
        bins = shadow.find_pg18(None)
        read_source = proof.read_source
        sources = []
        def capture_source(dsn, bins):
            sources.append(read_source(dsn, bins, include_acl=True))
            return read_source(dsn, bins)
        with patch.object(proof, 'read_source', side_effect=capture_source):
            reference = proof.build_schema(ROOT / 'db/schema.sql', bins)
        self.assertEqual(len(reference['ledger']), len(repository_ledger()))
        self.assertEqual(proof.ledger_proof(
            repository_ledger(),
            reference['ledger'])['pending'], [])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'schema.sql'
            source.write_text(sources[0][1], newline='')
            rebuilt = proof.build_schema(source, bins,
                production_ledger=reference['ledger'], grants=reference['grants'])
        self.assertEqual(rebuilt['ledger'], reference['ledger'])
        self.assertEqual(proof.schema_difference(rebuilt['dump'], reference['dump']), [])
        self.assertTrue(proof.privilege_difference(rebuilt['grants'], reference['grants'])['ok'])

    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_compacted_runner_is_read_only_and_preserves_original_ledger_timestamps(self):
        import psycopg
        bins = proof.comparison.find_pg18(None)
        read_source = proof.read_source
        observed = []

        def check_no_op(dsn, bins):
            def ledger():
                with psycopg.connect(dsn) as conn:
                    return conn.execute('select filename, sha256, applied_at from schema_migrations order by filename').fetchall()
            before = ledger()
            with tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                (repo / 'db').mkdir()
                (repo / 'db/migration-baseline.json').write_bytes((ROOT / 'db/migration-baseline.json').read_bytes())
                (repo / 'migrations').mkdir()
                baseline = dict(proof.MigrationBaseline.load(ROOT).migrations)
                for migration in (ROOT / 'migrations').glob('*.sql'):
                    if migration.name not in baseline:
                        shutil.copyfile(migration, repo / 'migrations' / migration.name)
                program = ("import sys; from pathlib import Path; "
                           "sys.path.insert(0, sys.argv[1]); import migrate; "
                           "migrate.MIGRATIONS_DIR=Path(sys.argv[2])/'migrations'; "
                           "sys.argv=['migrate.py','--apply','--yes']; migrate.main()")
                output = proof.checked([sys.executable, '-c', program, ROOT / 'tools', repo],
                    dict(PATH=os.environ.get('PATH', ''), DATABASE_URL=dsn,
                         PGOPTIONS='-c default_transaction_read_only=on'), 'read-only compacted runner fixture')
            self.assertIn('pending: 0', output)
            self.assertEqual(ledger(), before)
            observed.append(len(before))
            return read_source(dsn, bins)

        with patch.object(proof, 'read_source', side_effect=check_no_op):
            proof.build_schema(ROOT / 'db/schema.sql', bins)
        self.assertEqual(observed, [len(repository_ledger())])

    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_live_proof_accepts_production_owner_with_zero_pending(self):
        bins = proof.comparison.find_pg18(None)
        read_source = proof.read_source
        captured = []

        def capture_production(dsn, bins):
            import psycopg
            with psycopg.connect(dsn) as conn:
                conn.execute('ALTER ROLE neondb_owner LOGIN')
                conn.execute('ALTER TABLE public.deal OWNER TO neondb_owner')
                conn.execute('CREATE ROLE carr_backup')
                conn.execute('CREATE POLICY carr_backup_full_read ON ops.work_request FOR SELECT TO carr_backup USING(true)')
                conn.execute('CREATE POLICY carr_backup_full_read_memory_item ON public.memory_item FOR SELECT TO carr_backup USING(true)')
                conn.execute('GRANT carr_reader TO neondb_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE')
            captured.append(read_source(dsn, bins, include_acl=True))
            return read_source(dsn, bins)

        with patch.object(proof, 'read_source', side_effect=capture_production):
            proof.build_schema(ROOT / 'db/schema.sql', bins)

        def production_source(dsn, bins, include_acl=False):
            return captured[0] if dsn == 'captured-production' else read_source(dsn, bins, include_acl)

        with patch.object(proof, 'production_dsn', return_value='captured-production'), \
             patch.object(proof, 'read_source', side_effect=production_source):
            result = proof.live_proof(ledger_only=False)
        self.assertTrue(result['ledger']['ok'], result['ledger'])
        self.assertEqual(result['ledger']['applied'], len(repository_ledger()))
        self.assertEqual(result['ledger']['pending'], [])
        self.assertTrue(result['equivalence']['ok'], result['equivalence'])
        self.assertTrue(result['grants']['ok'], result['grants'])
        self.assertEqual(result['grants']['informational_owner_attributes']['production_only'],
                         [])

    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_live_proof_rejects_explicit_select_drift(self):
        bins = proof.comparison.find_pg18(None)
        read_source = proof.read_source
        captured = []

        def capture_production(dsn, bins):
            import psycopg
            with psycopg.connect(dsn) as conn:
                conn.execute('ALTER ROLE neondb_owner LOGIN')
                conn.execute('ALTER TABLE public.deal OWNER TO neondb_owner')
                conn.execute('GRANT SELECT ON public.deal TO carr_exporter')
            captured.append(read_source(dsn, bins, include_acl=True))
            return read_source(dsn, bins)

        with patch.object(proof, 'read_source', side_effect=capture_production):
            proof.build_schema(ROOT / 'db/schema.sql', bins)

        def production_source(dsn, bins, include_acl=False):
            return captured[0] if dsn == 'captured-production' else read_source(dsn, bins, include_acl)

        with patch.object(proof, 'production_dsn', return_value='captured-production'), \
             patch.object(proof, 'read_source', side_effect=production_source):
            result = proof.live_proof(ledger_only=False)
        self.assertTrue(result['ledger']['ok'], result['ledger'])
        self.assertEqual(result['ledger']['pending'], [])
        self.assertTrue(result['equivalence']['ok'], result['equivalence'])
        self.assertFalse(result['grants']['ok'], result['grants'])
        self.assertEqual(result['grants']['production_only'],
                         [['table', 'public.deal', '', 'carr_exporter', 'select', False]])
        self.assertEqual(result['grants']['snapshot_only'], [])

    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_owner_identity_is_normalized_without_owner_privileges(self):
        shadow = proof.load('owner_shadow', ROOT / 'ops/migration-shadow.py')
        bins = shadow.find_pg18(None)
        read_source = proof.read_source
        sources = []
        def capture_source(dsn, bins):
            result = read_source(dsn, bins, include_acl=True)
            sources.append(result)
            return read_source(dsn, bins)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'schema.sql'
            source.write_text("""create table public.example (id integer);
alter table public.example owner to neondb_owner;
create table public.schema_migrations (filename text primary key, sha256 text);
""")
            with patch.object(proof, 'read_source', side_effect=capture_source):
                reference = proof.build_schema(source, bins)
                source.write_text(sources[0][1], newline='')
                rebuilt = proof.build_schema(source, bins, grants=sources[0][2])
                source.write_text(sources[0][1] + '\nalter table public.example owner to carr_ci;\n', newline='')
                changed = proof.build_schema(source, bins, grants=sources[0][2])
        self.assertEqual(proof.schema_difference(reference['dump'], rebuilt['dump']), [])
        self.assertEqual(proof.schema_difference(reference['dump'], changed['dump']), [])
        self.assertTrue(proof.privilege_difference(reference['grants'], changed['grants'])['ok'])
        difference = proof.privilege_difference(reference['grants'], rebuilt['grants'])
        self.assertTrue(difference['ok'], difference)
        owner_facts = [row for row in rebuilt['grants']['privileges']
                       if row[3] == 'neondb_owner' and row[1] == 'public.example']
        self.assertEqual(owner_facts, [])
        self.assertIn(('owner', 'table:public.example', '', '<owner>', 'owner', False),
                      rebuilt['grants']['privileges'])

    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_public_execute_revoke_detects_drift_with_identical_schema(self):
        bins = proof.comparison.find_pg18(None)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'schema.sql'
            base = """create function public.example_read() returns int language sql as 'select 1';
create table public.schema_migrations (filename text primary key, sha256 text);
"""
            source.write_text(base)
            reference = proof.build_schema(source, bins)
            source.write_text(base + 'revoke execute on function public.example_read() from public;\n')
            revoked = proof.build_schema(source, bins)
        self.assertEqual(proof.schema_difference(reference['dump'], revoked['dump']), [])
        difference = proof.privilege_difference(reference['grants'], revoked['grants'])
        self.assertFalse(difference['ok'], difference)
        public_execute = ('function', 'public.example_read()', '', 'PUBLIC', 'execute', False)
        self.assertIn(public_execute, reference['grants']['privileges'])
        self.assertNotIn(public_execute, revoked['grants']['privileges'])
        self.assertEqual(difference['production_only'], [list(public_execute)])
        self.assertEqual(difference['snapshot_only'], [])

    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_production_plus_pending_equals_snapshot_and_detects_drift(self):
        shadow = proof.load('fixture_shadow', ROOT / 'ops/migration-shadow.py')
        bins = shadow.find_pg18(None)
        self.assertIsNotNone(bins, 'fixture requires PostgreSQL 18')
        with tempfile.TemporaryDirectory(prefix='baseline-fixture-') as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            (root / 'migrations').mkdir()
            for name in ['migrate.py', 'migration_baseline.py', 'migration_number_contract.py']:
                shutil.copyfile(ROOT / 'tools' / name, root / 'tools' / name)
            scripts = {name: 'select 1;' for names in FROZEN_COLLISIONS.values() for name in names}
            scripts.update({'0900_base.sql': 'create table public.example (id integer);',
                            '0901_pending.sql': "do $$ begin if current_user <> 'fixture_owner' then raise exception 'wrong migration principal'; end if; end $$; alter table public.example add column label text;"})
            hashes = {}
            for name, sql in scripts.items():
                (root / 'migrations' / name).write_text(sql)
                hashes[name] = hashlib.sha256(sql.encode()).hexdigest()
            base = """create table public.example (id integer);
grant select on public.example to reader;
create function public.example_read() returns int language sql as 'select 1';
create table public.schema_migrations (filename text primary key, sha256 text not null,
applied_at timestamptz not null default now());
create schema ops;
create table ops.rule_delivery_policy(singleton boolean primary key, mode text, changed_by text, reason text);
create function public.empty_policy_guard() returns trigger language plpgsql as $$
begin
  if not exists(select 1 from ops.rule_delivery_policy where mode='shadow') then
    raise exception 'empty policy has no bootstrap configuration';
  end if;
  return null;
end $$;
create constraint trigger empty_policy_guard after insert on public.schema_migrations
 deferrable initially deferred for each row execute function public.empty_policy_guard();
grant usage on schema public to fixture_owner;
grant usage on schema ops to fixture_owner;
grant select on ops.rule_delivery_policy to fixture_owner;
alter table public.example owner to fixture_owner;
alter table public.schema_migrations owner to fixture_owner;
"""
            production = root / 'production.sql'
            production.write_text(base)
            applied = {name: hashes[name] for name in scripts if name < '0901'}
            grants = dict(roles=['reader', 'reader_group', 'source_only', 'fixture_owner'],
                          memberships=[('reader_group', 'reader', False, True, False)],
                          migration_role='fixture_owner', database_owner='fixture_owner',
                          role_attributes=[('fixture_owner', False, True, False, False, True, False, -1, False)])
            built = proof.build_schema(production, bins, root, production_ledger=applied, grants=grants)
            self.assertEqual(built['ledger'], hashes)
            production.write_text(base + "insert into ops.rule_delivery_policy values(true,'full','fixture','existing policy');")
            with self.assertRaisesRegex(RuntimeError, 'empty policy has no bootstrap configuration'):
                proof.build_schema(production, bins, root, production_ledger=applied, grants=grants)
            production.write_text(base)
            expected = root / 'expected.sql'
            ledger = ''.join(f"insert into schema_migrations(filename,sha256) values ('{name}','{digest}');\n"
                             for name, digest in hashes.items())
            roles = 'create role reader; create role reader_group; create role fixture_owner login; grant reader_group to reader with admin false, inherit true, set false;'
            expected.write_text(roles + base + "set role fixture_owner;" + scripts['0901_pending.sql'] + \
                "reset role; insert into ops.rule_delivery_policy values(true,'shadow','fixture','fresh snapshot');\n" + ledger)
            reference = proof.build_schema(expected, bins, root)
            self.assertEqual(proof.schema_difference(built['dump'], reference['dump']), [])
            self.assertTrue(proof.privilege_difference(built['grants'], reference['grants'])['ok'])
            expected.write_text(expected.read_text() + 'alter table public.example alter column id type bigint;')
            drift = proof.build_schema(expected, bins, root)
            self.assertTrue(proof.schema_difference(built['dump'], drift['dump']))
            expected.write_text(expected.read_text() + 'revoke execute on function public.example_read() from public;')
            revoked = proof.build_schema(expected, bins, root)
            self.assertFalse(proof.privilege_difference(built['grants'], revoked['grants'])['ok'])
            (root / 'migrations' / '0902_data_guard.sql').write_text(
                "do $$ begin if not exists (select 1 from public.example) then raise exception 'missing data preimage'; end if; end $$;")
            with self.assertRaisesRegex(RuntimeError, 'missing data preimage'):
                proof.build_schema(production, bins, root, production_ledger=applied, grants=grants)
            (root / 'migrations' / '0902_data_guard.sql').unlink()
            production.write_text(base + """
create function public.ledger_guard() returns trigger language plpgsql as $$
begin raise exception 'pending ledger trigger active'; end $$;
create constraint trigger ledger_guard after insert on public.schema_migrations
 deferrable initially deferred for each row execute function public.ledger_guard();
""")
            with self.assertRaisesRegex(RuntimeError, 'pending ledger trigger active'):
                proof.build_schema(production, bins, root, production_ledger=applied, grants=grants)


if __name__ == '__main__':
    unittest.main()
