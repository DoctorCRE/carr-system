#!/usr/bin/env python3
"""Offline contracts for the local production ledger proof."""
import importlib.util
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



class ProofTests(unittest.TestCase):
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
        repository = {name: digest for name, sql, digest in proof.migrate.load_migrations()}
        applied = {name: digest for name, digest in repository.items()
                   if name <= '0872_self_closing_records.sql'}
        result = proof.ledger_proof(repository, applied)
        self.assertFalse(result['ok'])
        self.assertIn('partial strict atomic migration group', result['error'])

    def test_empty_production_fails_closed(self):
        self.assertFalse(proof.ledger_proof({'0001_a.sql': 'a'}, {})['ok'])

    def test_snapshot_drift_refuses(self):
        migrations = proof.migrate.load_migrations()
        applied = {name: digest for name, sql, digest in migrations}
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
                 patch.object(proof.migrate, 'load_migrations', return_value=migrations), \
                 patch.object(Path, 'read_text', return_value=drifted):
                with self.assertRaisesRegex(RuntimeError, 'ledger'):
                    proof.live_proof()
                credentials.assert_not_called()


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
    def test_production_plus_pending_equals_snapshot_and_detects_drift(self):
        shadow = proof.load('fixture_shadow', ROOT / 'ops/migration-shadow.py')
        bins = shadow.find_pg18(None)
        self.assertIsNotNone(bins, 'fixture requires PostgreSQL 18')
        with tempfile.TemporaryDirectory(prefix='baseline-fixture-') as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            (root / 'migrations').mkdir()
            for name in ['migrate.py', 'migration_number_contract.py']:
                shutil.copyfile(ROOT / 'tools' / name, root / 'tools' / name)
            scripts = {name: 'select 1;' for names in FROZEN_COLLISIONS.values() for name in names}
            scripts.update({'0900_base.sql': 'create table public.example (id integer);',
                            '0901_pending.sql': 'alter table public.example add column label text;'})
            hashes = {}
            for name, sql in scripts.items():
                (root / 'migrations' / name).write_text(sql)
                hashes[name] = hashlib.sha256(sql.encode()).hexdigest()
            base = """create table public.example (id integer);
grant select on public.example to reader;
create function public.example_read() returns int language sql as 'select 1';
create table public.schema_migrations (filename text primary key, sha256 text not null,
applied_at timestamptz not null default now());
"""
            production = root / 'production.sql'
            production.write_text(base)
            applied = {name: hashes[name] for name in scripts if name < '0901'}
            grants = dict(roles=['reader', 'reader_group', 'source_only'], memberships=[('reader_group', 'reader', False, True, False)])
            built = proof.build_schema(production, bins, root, production_ledger=applied, grants=grants)
            self.assertEqual(built['ledger'], hashes)
            expected = root / 'expected.sql'
            ledger = ''.join(f"insert into schema_migrations(filename,sha256) values ('{name}','{digest}');\n"
                             for name, digest in hashes.items())
            roles = 'create role reader; create role reader_group; grant reader_group to reader with admin false, inherit true, set false;'
            expected.write_text(roles + base + scripts['0901_pending.sql'] + '\n' + ledger)
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


if __name__ == '__main__':
    unittest.main()
