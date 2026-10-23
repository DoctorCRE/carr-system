#!/usr/bin/env python3
"""Build and run the dedicated copier against synthetic files only."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


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
    def test_installer_builds_the_helper_and_prints_the_one_time_privacy_step(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo = root / 'repo'
            home = root / 'home'
            for relative in ('bin', 'tools', 'ops/launchd', 'lib', '.venv/bin', 'vendor-patches'):
                (repo / relative).mkdir(parents=True)
            for relative in ('bin/install-outlook-intake.sh', 'tools/outlook-intake-copy.c',
                             'lib/outlook_intake.py',
                             'ops/launchd/com.carr.outlook-intake.plist',
                             'vendor-patches/hxprobe-calendar-and-bodies.patch'):
                shutil.copyfile(ROOT / relative, repo / relative)
            (repo / 'lib/machine_role.py').write_text('def is_primary(repo): return True\n')
            (repo / 'lib/launchd_scope.py').write_text('def allowed_on_machine(name, primary): return primary\n')
            (repo / '.venv/bin/python').symlink_to(sys.executable)
            vendor = home / 'carr-local/hxstore-reverse-engineering'
            (vendor / 'hxprobe/target/release').mkdir(parents=True)
            decoder = vendor / 'hxprobe/target/release/hxprobe'
            decoder.write_text('#!/bin/sh\nexit 99\n')
            decoder.chmod(0o700)
            digest = hashlib.sha256((repo / 'vendor-patches/hxprobe-calendar-and-bodies.patch').read_bytes()).hexdigest()
            (vendor / '.carr-pin').write_text(f'191b54e42757687806202f8f696e9066f0514e11 {digest}\n')
            command = ['/bin/zsh', '-f', '-c', 'export HOME="$1"; exec /bin/zsh -f "$2"',
                       'installer-test', str(home), str(repo / 'bin/install-outlook-intake.sh')]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            helper = home / 'carr-local/bin/outlook-intake-copy'
            self.assertTrue(helper.is_file())
            self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
            self.assertIn(f'grant Full Disk Access to {helper}', result.stdout)
            self.assertFalse((home / 'Library/LaunchAgents').exists())
            profile = home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity'
            profile.mkdir(parents=True)
            (profile / 'HxStore.hxd').write_bytes(b'installer-fixture')
            out = repo / 'out/outlook-intake'
            out.mkdir(mode=0o700)
            work = Path(tempfile.mkdtemp(prefix='work-', dir=out))
            subprocess.run([str(helper)], cwd=work, capture_output=True, check=True)
            self.assertEqual((work / 'store.hxd').read_bytes(), b'installer-fixture')
            before = helper.stat().st_mtime_ns
            subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(helper.stat().st_mtime_ns, before, 'an unchanged helper retains its privacy identity')


if __name__ == '__main__':
    unittest.main()
