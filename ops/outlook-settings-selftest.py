#!/usr/bin/env python3
"""Exercise settings through their public loader and validation command."""
import json
import importlib.util
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch, MagicMock
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from outlook_test_support import write_settings
from lib.outlook_settings import load_settings, SettingsError


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.repo = self.home / 'repo'
        self.path = write_settings(self.home, self.repo)

    def test_reads_account_profile_owner_and_credential_pointers_without_opening_credentials(self):
        settings = load_settings(self.home)
        self.assertEqual(settings.owner, 'fixture-owner')
        self.assertEqual(settings.account, 'owner@example.test')
        self.assertEqual(settings.profile / 'HxStore.hxd', self.home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity/HxStore.hxd')
        self.assertEqual(settings.transport_environment()['CARR_MCP_ENV'], str(self.home / '.config/carr/mcp-tokens.env'))
        self.assertFalse(settings.mcp_credentials.exists())
        self.assertFalse(settings.database_credentials.exists())

    def test_second_person_changes_the_returned_identity_and_profile(self):
        write_settings(self.home, self.repo, owner='other-owner', account='other@other.test',
                       internal_domain='other.test', profile=str(self.home / 'Other Profile'), timezone='Europe/London')
        settings = load_settings(self.home)
        self.assertEqual((settings.owner, settings.account, settings.internal_domain, str(settings.timezone)),
                         ('other-owner', 'other@other.test', 'other.test', 'Europe/London'))
        self.assertEqual(settings.profile, self.home / 'Other Profile')

    def test_missing_and_invalid_fields_name_file_and_field(self):
        values = json.loads(self.path.read_text())
        for field, value in [('owner', ''), ('account', 'bad'), ('timezone', 'No/Such_Zone'),
                             ('profile', 'relative'), ('repo', 'relative'), ('services', ['wrong']),
                             ('mcp_credentials', 123), ('internal_domain', '@example.test'), ('full_screen_height', 0), ('executable_path', '/usr/bin::/bin')]:
            for missing in (True, False):
                with self.subTest(field=field, missing=missing):
                    bad = dict(values)
                    if missing:
                        bad.pop(field)
                    else:
                        bad[field] = value
                    self.path.write_text(json.dumps(bad))
                    with self.assertRaises(SettingsError) as error:
                        load_settings(self.home)
                    self.assertIn(str(self.path), str(error.exception))
                    self.assertIn(field, str(error.exception))

    def test_missing_malformed_wide_mode_and_symlink_refuse(self):
        self.path.unlink()
        with self.assertRaisesRegex(SettingsError, 'outlook.json.*file'):
            load_settings(self.home)
        self.path.write_text('{')
        self.path.chmod(0o600)
        with self.assertRaisesRegex(SettingsError, 'outlook.json.*json'):
            load_settings(self.home)
        write_settings(self.home, self.repo)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(SettingsError, 'outlook.json.*mode'):
            load_settings(self.home)
        self.path.unlink()
        target = self.home / 'target'
        target.write_text('{}')
        target.chmod(0o600)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(SettingsError, 'outlook.json.*file'):
            load_settings(self.home)

    def test_write_command_creates_private_settings_and_never_overwrites(self):
        contents = self.path.read_text()
        self.path.unlink()
        command = [sys.executable, '-m', 'lib.outlook_settings', 'write']
        env = {**os.environ, 'HOME': str(self.home)}
        done = subprocess.run(command, input=contents, cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_settings(self.home).account, 'owner@example.test')
        refused = subprocess.run(command, input='{}', cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.path.read_text(), contents)
        self.assertIn(str(self.path), refused.stderr)

    def test_invalid_write_removes_new_file(self):
        self.path.unlink()
        done = subprocess.run([sys.executable, '-m', 'lib.outlook_settings', 'write'],
                              input='{}', cwd=ROOT, env={**os.environ, 'HOME': str(self.home)},
                              capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('account', done.stderr)
        self.assertFalse(self.path.exists())

    def test_mcp_file_pointer_cannot_be_overridden_by_ambient_credentials(self):
        with patch.dict(os.environ, {'CARR_MCP_LOCAL_TOKEN': 'synthetic-wrong-token',
                                     'CARR_MCP_CLIENT_PROFILE': 'hermes-projector',
                                     'CARR_BREAK_GLASS': '1', 'CARR_BREAK_GLASS_REASON': 'synthetic'}, clear=True):
            env = load_settings(self.home).transport_environment()
        self.assertEqual(env['CARR_MCP_ENV'], str(self.home / '.config/carr/mcp-tokens.env'))
        self.assertEqual(env['CARR_MCP_CLIENT_PROFILE'], 'local')
        self.assertNotIn('CARR_MCP_LOCAL_TOKEN', env)
        self.assertNotIn('CARR_BREAK_GLASS', env)

    def test_repository_alias_is_canonicalized_for_the_fixed_copy_scope(self):
        real = self.home / 'real-repo'
        real.mkdir()
        self.repo.symlink_to(real, target_is_directory=True)
        settings = load_settings(self.home)
        self.assertEqual(settings.repo, real)
        settings.require_repo(self.repo)

    def test_catalog_sync_resolves_the_settings_owner_before_the_database_boundary(self):
        spec = importlib.util.spec_from_file_location('outlook_catalog_test', ROOT / 'tools/ops-record.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry = self.home / 'registry.json'
        registry.write_text(json.dumps({'services': [{'key': 'outlook-intake', 'name': 'Fixture intake',
                                                     'owner_actor': 'outlook_settings.owner'}]}))
        connection = MagicMock()
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (False,)
        cursor.fetchall.return_value = []
        with patch.object(module, 'REGISTRY', registry), patch.object(module, 'connect', connection), \
                patch('pathlib.Path.home', return_value=self.home), redirect_stdout(StringIO()):
            self.assertEqual(module.cmd_sync_registry(None), 0)
        statement, values = cursor.execute.call_args_list[0].args
        self.assertIn('insert into ops.service', statement)
        self.assertEqual(values[5], 'fixture-owner')

    def test_configured_exporter_file_wins_over_an_unrelated_shell_export(self):
        from exporters import common
        pointer = self.home / 'exporter-fixture.txt'
        pointer.write_text("CARR_DB_EXPORTER_URL='postgresql://synthetic.invalid/fixture'\n")
        with patch.dict(os.environ, {'CARR_DB_EXPORTER_URL': 'postgresql://wrong.invalid/other'}), \
                patch.object(common.psycopg, 'connect') as connect:
            common.connect(credential_file=pointer)
        connect.assert_called_once_with('postgresql://synthetic.invalid/fixture')

    def test_documented_studio_command_writes_and_validates_in_a_synthetic_home(self):
        repo = self.home / 'carr-system'
        repo.mkdir()
        (repo / '.venv/bin').mkdir(parents=True)
        (repo / '.venv/bin/python').symlink_to(sys.executable)
        (repo / 'lib').symlink_to(ROOT / 'lib', target_is_directory=True)
        self.path.unlink()
        document = (ROOT / 'spec/outlook/studio-settings-migration.md').read_text()
        command = document.split('```sh\n', 1)[1].split('```', 1)[0]
        done = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=ROOT,
                              env={**os.environ, 'HOME': str(self.home)}, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        settings = load_settings(self.home)
        self.assertEqual((settings.owner, settings.account, settings.internal_domain),
                         ('joe', 'joe.bookout@carr.us', 'carr.us'))
        self.assertEqual(settings.repo, repo)
        self.assertEqual(settings.services, ('intake', 'refresh', 'triage'))
        self.assertIn('outlook settings valid:', done.stdout)
        self.assertFalse(settings.mcp_credentials.exists())
        self.assertFalse(settings.database_credentials.exists())
        self.assertFalse((repo / 'out').exists())

    def test_window_guard_uses_the_configured_display_height(self):
        from lib import outlook_ui as ui
        window = {'title': 'Inbox • owner@example.test', 'on_current_space': True,
                  'bounds': {'height': 949}}
        self.assertFalse(ui.is_ordinary_main_window(window, 949))
        self.assertTrue(ui.is_ordinary_main_window(window, 1200))

    def test_validation_command_refuses_before_effects(self):
        self.path.unlink()
        done = subprocess.run([sys.executable, '-m', 'lib.outlook_settings', 'validate'], cwd=ROOT,
                              env={**os.environ, 'HOME': str(self.home)}, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(str(self.path), done.stderr)
        self.assertIn('file', done.stderr)
        self.assertFalse(self.repo.exists())


if __name__ == '__main__':
    unittest.main()
