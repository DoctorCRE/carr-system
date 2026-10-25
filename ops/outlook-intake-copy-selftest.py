#!/usr/bin/env python3
"""Build and run the dedicated copier against synthetic files only."""
import json
import hashlib
import os
import plistlib
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from outlook_test_support import write_settings
from lib.outlook_settings import load_settings, SettingsError


class CopyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.profile = self.root / 'Main Identity'
        self.profile.mkdir()
        (self.profile / 'HxStore.hxd').write_bytes(b'synthetic-store')
        self.sidecars = self.profile / 'Files/S0/845/EFMData'
        self.sidecars.mkdir(parents=True)
        (self.sidecars / '7.dat').write_bytes(b'synthetic-body')
        (self.sidecars / 'ignore.txt').write_bytes(b'not-a-body')
        self.out = self.root / 'out/outlook-intake'
        self.out.mkdir(parents=True, mode=0o700)
        self.work = Path(tempfile.mkdtemp(prefix='work-', dir=self.out))
        self.helper = self.root / 'outlook-intake-copy'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        f'-DOUTLOOK_PROFILE={json.dumps(str(self.profile))}',
                        f'-DOUTLOOK_WORK_ROOT={json.dumps(str(self.out))}',
                        f'-DOUTLOOK_REPO={json.dumps(str(self.root))}',
                        str(ROOT / 'tools/outlook-intake-copy.c'), '-o', str(self.helper)],
                       check=True, capture_output=True)

    def copy(self, *args, cwd=None, env=None):
        return subprocess.run([str(self.helper), *args], cwd=cwd or self.work,
                              env=env, capture_output=True)

    def test_scope_readback_names_only_the_compiled_paths_without_copying(self):
        result = self.copy('--scope', cwd=self.root)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode(), f'{self.profile}\n{self.out}\n{self.root}\n')
        self.assertFalse((self.work / 'store.hxd').exists())

    def test_runtime_refuses_a_settings_profile_that_differs_from_the_granted_binary(self):
        settings_home = tempfile.TemporaryDirectory()
        self.addCleanup(settings_home.cleanup)
        home = Path(settings_home.name).resolve()
        write_settings(home, self.root, profile=str(self.profile), copy_helper=str(self.helper))
        load_settings(home).verify_helper()
        write_settings(home, self.root, profile=str(self.root / 'Other Profile'), copy_helper=str(self.helper))
        with self.assertRaisesRegex(SettingsError, 'outlook.json.*copy_helper.*scope differs'):
            load_settings(home).verify_helper()

    def test_copies_only_the_fixed_store_and_body_sidecars_privately(self):
        self.assertEqual(self.copy().returncode, 0)
        self.assertEqual((self.work / 'store.hxd').read_bytes(), b'synthetic-store')
        self.assertEqual((self.work / 'Files/S0/845/EFMData/7.dat').read_bytes(), b'synthetic-body')
        self.assertFalse((self.work / 'Files/S0/845/EFMData/ignore.txt').exists())
        for directory, _, files in os.walk(self.work):
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            for name in files:
                self.assertEqual(stat.S_IMODE((Path(directory) / name).stat().st_mode), 0o600)

    def test_arguments_and_other_working_directories_are_refused(self):
        self.assertNotEqual(self.copy('/tmp/other-store').returncode, 0)
        self.assertNotEqual(self.copy(cwd=self.root).returncode, 0)
        self.assertFalse((self.root / 'store.hxd').exists())
        os.chmod(self.work, 0o755)
        self.assertNotEqual(self.copy().returncode, 0)

    def test_environment_overrides_do_not_change_source_or_destination(self):
        env = {**os.environ, 'HOME': str(self.root / 'other-home'),
               'CARR_OUTLOOK_STORE': str(self.root / 'other-store')}
        self.assertEqual(self.copy(env=env).returncode, 0)
        self.assertEqual((self.work / 'store.hxd').read_bytes(), b'synthetic-store')

    def test_source_and_sidecar_symlinks_are_refused(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'outside-scope')
        for path in (self.profile / 'HxStore.hxd', self.sidecars / '7.dat'):
            original = path.read_bytes()
            path.unlink()
            path.symlink_to(outside)
            self.assertNotEqual(self.copy().returncode, 0)
            self.assertFalse(any(p.read_bytes() == b'outside-scope'
                                 for p in self.work.rglob('*') if p.is_file()))
            path.unlink()
            path.write_bytes(original)
            shutil.rmtree(self.work)
            self.work.mkdir(mode=0o700)

    def test_existing_destination_files_are_never_followed_or_overwritten(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'keep')
        (self.work / 'store.hxd').symlink_to(outside)
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_a_store_without_sidecars_still_gets_an_empty_private_files_directory(self):
        shutil.rmtree(self.profile / 'Files')
        self.assertEqual(self.copy().returncode, 0)
        self.assertTrue((self.work / 'Files').is_dir())


@unittest.skipUnless(sys.platform == 'darwin', 'the installer requires macOS zsh and plutil')
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.home = self.root / 'repo & quoted', self.root / 'home'
        for relative in ('bin', 'tools', 'ops/launchd', 'lib', '.venv/bin', 'vendor-patches'):
            (self.repo / relative).mkdir(parents=True)
        for relative in ('bin/install-outlook-intake.sh', 'bin/install-outlook-triage.sh',
                         'tools/outlook-intake-copy.c', 'lib/outlook_settings.py',
                         'ops/launchd/com.carr.outlook-intake.plist',
                         'ops/launchd/com.carr.outlook-refresh.plist',
                         'ops/launchd/com.carr.outlook-triage.plist',
                         'vendor-patches/hxprobe-calendar-and-bodies.patch'):
            shutil.copyfile(ROOT / relative, self.repo / relative)
        (self.repo / '.venv/bin/python').symlink_to(sys.executable)
        self.profile = self.home / 'Custom Profile'
        self.helper = self.home / 'custom binaries/granted-outlook-copy'
        self.vendor = self.home / 'custom vendor/hxstore'
        self.config = write_settings(self.home, self.repo, profile=str(self.profile),
                                     vendor=str(self.vendor), copy_helper=str(self.helper))
        (self.vendor / 'hxprobe/target/release').mkdir(parents=True)
        decoder = self.vendor / 'hxprobe/target/release/hxprobe'
        decoder.write_text('#!/bin/sh\nexit 99\n')
        decoder.chmod(0o700)
        digest = hashlib.sha256((self.repo / 'vendor-patches/hxprobe-calendar-and-bodies.patch').read_bytes()).hexdigest()
        (self.vendor / '.carr-pin').write_text(f'191b54e42757687806202f8f696e9066f0514e11 {digest}\n')

    def install(self, name='intake', check=True):
        command = ['/bin/zsh', '-f', '-c', 'export HOME="$1"; exec /bin/zsh -f "$2"',
                   'installer-test', str(self.home), str(self.repo / f'bin/install-outlook-{name}.sh')]
        return subprocess.run(command, capture_output=True, text=True, check=check)

    def test_installer_uses_settings_without_a_machine_role_and_preserves_the_granted_binary(self):
        result = self.install()
        self.assertTrue(self.helper.is_file())
        self.assertEqual(stat.S_IMODE(self.helper.stat().st_mode), 0o700)
        self.assertIn(f'grant Full Disk Access to {self.helper}', result.stdout)
        self.assertFalse((self.home / 'Library/LaunchAgents').exists())
        for service in ('intake', 'refresh'):
            rendered = plistlib.loads((self.repo / f'out/com.carr.outlook-{service}.plist').read_bytes())
            self.assertEqual(rendered['WorkingDirectory'], str(self.repo))
            self.assertEqual(rendered['EnvironmentVariables']['PATH'], load_settings(self.home).executable_path)
            self.assertEqual(stat.S_IMODE((self.repo / f'out/outlook-{service}-launchd.log').stat().st_mode), 0o600)
        intake = plistlib.loads((self.repo / 'out/com.carr.outlook-intake.plist').read_bytes())
        self.assertEqual(intake['ProgramArguments'][0], str(self.helper))
        self.profile.mkdir(parents=True)
        (self.profile / 'HxStore.hxd').write_bytes(b'installer-fixture')
        out = self.repo / 'out/outlook-intake'
        out.mkdir(mode=0o700)
        work = Path(tempfile.mkdtemp(prefix='work-', dir=out))
        subprocess.run([str(self.helper)], cwd=work, capture_output=True, check=True)
        self.assertEqual((work / 'store.hxd').read_bytes(), b'installer-fixture')
        load_settings(self.home).verify_helper()
        before = self.helper.stat().st_mtime_ns
        self.install()
        self.assertEqual(self.helper.stat().st_mtime_ns, before, 'an unchanged helper retains its privacy identity')

    def test_invalid_settings_refuse_both_installers_before_build_or_launchagent_writes(self):
        self.config.unlink()
        for service in ('intake', 'triage'):
            result = self.install(service, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(self.config), result.stderr)
            self.assertIn('file', result.stderr)
        self.assertFalse(self.helper.exists())
        self.assertFalse((self.repo / 'out').exists())
        self.assertFalse((self.home / 'Library/LaunchAgents').exists())

    def test_disabled_service_refuses_before_build(self):
        write_settings(self.home, self.repo, services=['triage'])
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outlook.json', result.stderr)
        self.assertIn('services', result.stderr)
        self.assertFalse(self.helper.exists())
        self.assertFalse((self.repo / 'out').exists())

    def test_triage_installer_uses_configured_guide_and_search_path_without_loading(self):
        guide = self.home / 'custom folders.md'
        guide.write_text('Synthetic folder guide')
        guide.chmod(0o600)
        write_settings(self.home, self.repo, folder_guide=str(guide), executable_path='/usr/bin:/bin')
        result = self.install('triage')
        dest = self.home / 'Library/LaunchAgents/com.carr.outlook-triage.plist'
        self.assertTrue(dest.is_file())
        self.assertIn('NOT loaded', result.stdout)
        rendered = plistlib.loads(dest.read_bytes())
        self.assertEqual(rendered['WorkingDirectory'], str(self.repo))
        self.assertEqual(rendered['EnvironmentVariables']['PATH'], '/usr/bin:/bin')
        self.assertEqual(stat.S_IMODE((self.repo / 'out/outlook-triage-launchd.log').stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
