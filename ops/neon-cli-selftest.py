#!/usr/bin/env python3
"""Neon authentication regressions using a local provider executable only."""
import importlib.util
import os
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class NeonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.marker = self.home / 'calls'
        self.browser = self.home / 'browser'
        self.fake = self.home / 'neonctl'
        self.fake.write_text('#!' + sys.executable + '''
import os
from pathlib import Path
Path(os.environ['CALLS']).open('a').write('call\\n')
if not os.environ.get('NEON_API_KEY', '').strip():
    Path(os.environ['BROWSER']).touch()
    raise SystemExit(1)
print('fixture-dsn')
''')
        self.fake.chmod(0o755)
        self.env = dict(HOME=str(self.home), PATH=str(self.home) + os.pathsep + os.defpath,
                        CALLS=str(self.marker), BROWSER=str(self.browser))
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.tap = load('test_neon_db_tap', 'tools/db-tap.py')
        self.tap.NEONCTL = str(self.fake)

    def test_missing_key_never_starts_provider(self):
        for invoke in (lambda: self.tap.dsn(),
                       lambda: self.tap._project_id_by_name('fixture', dict(os.environ)),
                       lambda: self.tap._staging_runtime_target(dict(os.environ))):
            with self.subTest(invoke=invoke):
                with self.assertRaises(SystemExit) as error:
                    invoke()
                self.assertIn('~/.config/carr/db.env', str(error.exception))
                self.assertFalse(self.marker.exists())
                self.assertFalse(self.browser.exists())

    def test_present_key_preserves_output_and_cache(self):
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        self.assertEqual(self.tap.dsn(), 'fixture-dsn')
        self.assertEqual(self.tap.dsn(), 'fixture-dsn')
        self.assertEqual(self.marker.read_text().splitlines(), ['call'])
        self.assertFalse(self.browser.exists())

    def test_stored_key_is_used(self):
        config = self.home / '.config/carr'
        config.mkdir(parents=True)
        (config / 'db.env').write_text('NEON_API_KEY=synthetic-stored-key\n')
        self.assertEqual(self.tap.dsn(), 'fixture-dsn')
        self.assertFalse(self.browser.exists())

    def test_command_launcher_refuses_without_key(self):
        for command in ('connection-string', 'projects', 'branches', 'api'):
            result = subprocess.run([sys.executable, str(ROOT / 'tools/neon-cli.py'), command],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('~/.config/carr/db.env', result.stderr)
        self.assertFalse(self.marker.exists())

    def test_command_launcher_passes_key_without_argv(self):
        from lib.neon_cli import run
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        result = run(['connection-string', 'fixture'], executable=str(self.fake))
        self.assertEqual((result.returncode, result.stdout), (0, 'fixture-dsn\n'))
        self.assertFalse(self.browser.exists())

    def test_auth_commands_are_refused_even_with_key(self):
        from lib.neon_cli import run
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        for command in ('auth', 'login', 'init'):
            with self.assertRaises(SystemExit):
                run([command], executable=str(self.fake))
        self.assertFalse(self.marker.exists())

    def test_production_proof_and_migration_helper_share_guard(self):
        from lib import neon_cli
        proof = load('test_neon_production_proof', 'ops/migration-production-proof.py')
        support = load('test_neon_migration_support', 'tools/migrate-prod-support.py')
        from contextlib import redirect_stderr, redirect_stdout
        import io
        args = types.SimpleNamespace(project_id='fixture-project')
        with patch.object(neon_cli, 'ROOT', self.home):
            with self.assertRaises(SystemExit) as error:
                proof.production_dsn()
            self.assertIn('~/.config/carr/db.env', str(error.exception))
            with redirect_stderr(io.StringIO()) as error:
                self.assertEqual(support.cmd_neon_dsn(args), 1)
            self.assertIn('~/.config/carr/db.env', error.getvalue())
            self.assertFalse(self.marker.exists())
            os.environ['NEON_API_KEY'] = 'synthetic-key'
            self.assertEqual(proof.production_dsn(), 'fixture-dsn')
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(support.cmd_neon_dsn(args), 0)
            self.assertEqual(output.getvalue(), 'fixture-dsn\n')

    def test_health_resolves_once_and_reads_with_server_read_only(self):
        health = load('test_health_store', 'tools/health_store.py')
        health._tap = self.tap
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        from unittest.mock import MagicMock
        connection = MagicMock()
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [(1, None, 'fixture')]
        cursor.nextset.return_value = False
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connection)}):
            for _ in range(6):
                result = health.query('select 1')
                self.assertEqual((result.returncode, result.stdout), (0, '1||fixture'))
        self.assertEqual(self.marker.read_text().splitlines(), ['call'])
        self.assertIn('default_transaction_read_only=on', connection.call_args.kwargs['options'])

    def test_health_caches_failed_resolution(self):
        health = load('test_health_store_failure', 'tools/health_store.py')
        health._tap = self.tap
        for _ in range(6):
            result = health.query('select 1')
            self.assertEqual(result.returncode, 1)
            self.assertIn('~/.config/carr/db.env', result.stderr)
        self.assertFalse(self.marker.exists())

    def test_health_provider_failure_is_not_retried(self):
        health = load('test_health_store_provider_failure', 'tools/health_store.py')
        health._tap = self.tap
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        self.fake.write_text('#!' + sys.executable + '''
import os
from pathlib import Path
Path(os.environ['CALLS']).open('a').write('call\\n')
raise SystemExit(7)
''')
        for _ in range(6):
            self.assertEqual(health.query('select 1').returncode, 1)
        self.assertEqual(self.marker.read_text().splitlines(), ['call'])

    def test_health_entry_points_use_shared_reader(self):
        source = (ROOT / 'tools/health-check.py').read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                    self.assertNotIn('tools/db-tap.py', ast.get_source_segment(source, node))

    def test_blank_key_never_launches(self):
        os.environ['NEON_API_KEY'] = '   '
        with self.assertRaises(SystemExit):
            self.tap.dsn()
        self.assertFalse(self.marker.exists())

    def test_missing_home_cannot_read_machine_credentials(self):
        from lib.neon_cli import api_key
        self.assertEqual(api_key({}), '')

    def test_real_health_snapshot_shares_resolution(self):
        health = load('test_health_snapshot_store', 'tools/health_store.py')
        health._tap = self.tap
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        from unittest.mock import MagicMock, Mock
        connection = MagicMock()
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.nextset.return_value = False
        tree = ast.parse((ROOT / 'tools/health-check.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == '_canonical_snapshot')
        namespace = dict(CANONICAL_FIXTURE=None, CANONICAL_SECTION='all',
                         REPO_ROOT=str(ROOT), os=os, sys=sys, json=__import__('json'),
                         RULE_ENFORCED_STATES=('hard_enforced', 'authority_enforced'),
                         RULE_UNENFORCEABLE_CLASS='judgment_advisory',
                         _store_query=health.query,
                         subprocess=types.SimpleNamespace(run=Mock(return_value=
                             subprocess.CompletedProcess([], 0, '{}', ''))))
        exec(compile(ast.Module(body=[function], type_ignores=[]),
                     'health-check.py', 'exec'), namespace)
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connection)}):
            namespace['_canonical_snapshot']()
        self.assertEqual(self.marker.read_text().splitlines(), ['call'])
        self.assertEqual(connection.call_count, 3)


if __name__ == '__main__':
    unittest.main()
