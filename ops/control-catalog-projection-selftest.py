#!/usr/bin/env python3
"""Regression checks for suite renames and revision-bound catalog installation."""
import importlib.util
import os
import sys
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from git_env import fixture_env

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('catalog', ROOT / 'ops/sync_control_catalog.py')
assert spec is not None and spec.loader is not None
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class SuiteClassificationTests(unittest.TestCase):
    def test_rename_fixture_remains_runnable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            suite = root / 'proof.test.mjs'
            suite.write_text("import { test } from 'node:test'; test('proof', () => {});\n")
            for extension in ('mjs', 'ts'):
                target = root / f'proof.test.{extension}'
                suite.rename(target)
                suite = target
                name = f'mcp-server/test/{target.name}'
                rows = catalog.compile_rows({'outbound_format': {
                    'implementation': ['ops/check.py'], 'test': [name],
                    'enforcement_class': 'deny_gate'}}, {'ops/check.py', name})
                self.assertTrue(rows[0]['installed'], rows)
                result = subprocess.run(['node', '--input-type=module', '-e',
                    "import { discoverSuites } from './mcp-server/suite-discovery.ts'; "
                    "console.log(JSON.stringify(await discoverSuites(process.argv[1], {expected:1})));",
                    str(root)], cwd=ROOT, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), [target.name])
                subprocess.run(['node', '--test', str(target)], check=True, capture_output=True)

    def test_checkout_moving_during_compilation_refuses_before_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            env = fixture_env()
            def git(*args):
                return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                               cwd=root, env=env, text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('commit', '--allow-empty', '-qm', 'initial')
            revision = git('rev-parse', 'HEAD')
            def move_checkout():
                git('commit', '--allow-empty', '-qm', 'candidate changed')
                return []
            with patch.dict(os.environ, env, clear=True), patch.object(catalog, 'REPO', root), \
                    patch.object(catalog, 'compile_catalog', side_effect=move_checkout):
                with self.assertRaisesRegex(catalog.CatalogError, 'revision differs'):
                    catalog.install_projection(None, revision)

    def test_nested_or_missing_suite_cannot_verify(self):
        self.assertFalse(catalog._runs_in_ci('path', 'mcp-server/test/nested/proof.test.ts'))
        self.assertFalse(catalog._runs_in_ci('path', 'mcp-server/test/proof.spec.ts'))


@unittest.skipUnless(os.environ.get('CARR_LOCAL_PG_DSN'), 'requires disposable PostgreSQL fixture')
class ProjectionDatabaseTests(unittest.TestCase):
    def test_full_apply_retires_legacy_controls_before_projection(self):
        import psycopg
        dsn = os.environ['CARR_LOCAL_PG_DSN']
        legacy = ('completion_evidence', 'conduct_stop', 'delegation_tripwire', 'executor_tier')
        with psycopg.connect(dsn) as conn:
            for key in legacy:
                conn.execute("insert into ops.enforcement_control_catalog "
                             "(control_key, implementation_ref, test_ref, enforcement_class, installed) "
                             "values (%s, 'deleted-hook.py', 'deleted-test.py', 'surfacing', false)", (key,))
            conn.commit()
            try:
                result = subprocess.run([sys.executable, 'tools/migrate.py', '--apply', '--yes'],
                    cwd=ROOT, env={**os.environ, 'DATABASE_URL': dsn},
                    capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('control-catalog-projection:', result.stdout)
                self.assertEqual(conn.execute("select control_key from ops.enforcement_control_catalog "
                    "where control_key = any(%s)", (list(legacy),)).fetchall(), [])
                conn.commit()
                conn.execute("insert into ops.enforcement_control_catalog "
                             "(control_key, implementation_ref, test_ref, enforcement_class, installed) "
                             "values ('unknown-probe', 'unknown.py', 'unknown-test.py', 'surfacing', false)")
                conn.commit()
                with patch.object(catalog, 'source_revision', return_value='fixture'):
                    with self.assertRaisesRegex(catalog.CatalogError, 'unknown-probe'):
                        catalog.install_projection(conn, 'fixture')
                conn.rollback()
                self.assertIsNotNone(conn.execute("select 1 from ops.enforcement_control_catalog "
                                                 "where control_key='unknown-probe'").fetchone())
            finally:
                conn.rollback()
                conn.execute("delete from ops.enforcement_control_catalog where control_key = any(%s)",
                             (list(legacy) + ['unknown-probe'],))
                conn.commit()

    def test_atomic_install_idempotence_and_readonly_refusals(self):
        import psycopg
        dsn = os.environ['CARR_LOCAL_PG_DSN']
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        env = {**os.environ, 'DATABASE_URL': dsn}
        with psycopg.connect(dsn) as conn:
            with self.assertRaises(catalog.CatalogError):
                catalog.install_projection(conn, '0' * 40)
            catalog.install_projection(conn, revision)
            conn.commit()
            def rows():
                return conn.execute('select * from ops.enforcement_control_catalog order by control_key').fetchall()
            original = rows()
            catalog.install_projection(conn, revision)
            self.assertEqual(original, rows(), 'no-op installation must preserve verification timestamps')
            conn.commit()
            conn.execute("update ops.enforcement_control_catalog set test_ref = 'unreviewed.test.ts' "
                         "where control_key = 'outbound_format'")
            conn.commit()
            try:
                for command, error in (
                    ([sys.executable, 'ops/control-catalog-parity-gate.py'], 'test reference drifted'),
                    (['bin/schema-snapshot.sh', '--from-disposable-local', dsn, '--verify-only'],
                     'exact reviewed control catalog is missing or drifted'),
                ):
                    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(error, result.stdout + result.stderr)
                self.assertEqual(conn.execute("select test_ref from ops.enforcement_control_catalog "
                                             "where control_key='outbound_format'").fetchone()[0],
                                 'unreviewed.test.ts', 'verification must not repair drift')
            finally:
                catalog.install_projection(conn, revision)
                conn.commit()


if __name__ == '__main__':
    unittest.main()
