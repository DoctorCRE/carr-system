#!/usr/bin/env python3
"""Exercise the research copier using synthetic profiles, never the live store."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'the helper uses macOS CommonCrypto')
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
        self.out = self.root / 'research'
        self.helper = self.root / 'copier'
        self.build(ROOT / 'tools/outlook-research-copy.c')

    def build(self, source):
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        f'-DOUTLOOK_PROFILE={json.dumps(str(self.profile))}',
                        f'-DOUTLOOK_RESEARCH_ROOT={json.dumps(str(self.out))}',
                        str(source), '-o', str(self.helper)],
                       check=True)

    def copy(self, *args):
        return subprocess.run([str(self.helper), *args], cwd=self.root, capture_output=True)

    def snapshots(self):
        return sorted(self.out.glob('snap-*'))

    def test_snapshot_copies_fixed_scope_and_records_independently_verified_metadata(self):
        before = {str(p.relative_to(self.profile)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.profile.rglob('*') if p.is_file()}
        self.assertEqual(self.copy().returncode, 0)
        snapshot, = self.snapshots()
        self.assertRegex(snapshot.name, r'^snap-\d{8}T\d{6}-\d{9}Z$')
        self.assertEqual((snapshot / 'HxStore.hxd').read_bytes(), b'synthetic-store')
        self.assertEqual((snapshot / 'Files/S0/845/EFMData/7.dat').read_bytes(), b'synthetic-body')
        manifest = json.loads((snapshot / 'manifest.json').read_text())
        self.assertEqual(manifest['schema'], 'outlook-research-snapshot/v1')
        expected = {'HxStore.hxd', 'Files/S0/845/EFMData/7.dat'}
        self.assertEqual({item['path'] for item in manifest['files']}, expected)
        for item in manifest['files']:
            data, mtime = before[item['path']]
            self.assertEqual(item['size'], len(data))
            self.assertEqual(item['mtime_ns'], mtime)
            self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(before, {str(p.relative_to(self.profile)): (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in self.profile.rglob('*') if p.is_file()})
        self.assertFalse((snapshot / 'Files/S0/845/EFMData/ignore.txt').exists())
        for directory, _, files in os.walk(self.out):
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            self.assertEqual(Path(directory).stat().st_uid, os.getuid())
            for name in files:
                self.assertEqual(stat.S_IMODE((Path(directory) / name).stat().st_mode), 0o600)

    def test_repeated_snapshots_prune_only_oldest_owned_completed_snapshots(self):
        self.assertEqual(self.copy().returncode, 0)
        first, = self.snapshots()
        self.out.joinpath('snap-not-a-timestamp').mkdir()
        foreign = self.out / 'snap-20000101T000000-000000000Z'
        foreign.mkdir(mode=0o700)
        (foreign / 'keep.txt').write_bytes(b'not-a-helper-snapshot')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_bytes(b'outside')
        alias = self.out / 'snap-20000102T000000-000000000Z'
        alias.symlink_to(outside, target_is_directory=True)
        names = [first.name]
        for _ in range(21):
            self.assertEqual(self.copy().returncode, 0)
            names.append(max(p.name for p in self.snapshots()
                             if (p / 'manifest.json').is_file() and not p.is_symlink()))
        kept = [p for p in self.snapshots() if p.name in names]
        self.assertEqual([p.name for p in kept], names[-20:])
        self.assertFalse(first.exists())
        self.assertEqual((foreign / 'keep.txt').read_bytes(), b'not-a-helper-snapshot')
        self.assertTrue(alias.is_symlink())
        self.assertEqual((outside / 'keep.txt').read_bytes(), b'outside')
        self.assertTrue((self.out / 'snap-not-a-timestamp').is_dir())

    def test_clock_rollback_refuses_capture_without_removing_study_inputs(self):
        self.assertEqual(self.copy().returncode, 0)
        first, = self.snapshots()
        for index in range(20):
            shutil.copytree(first, self.out / f'snap-29990101T000000-{index:09d}Z')
        shutil.rmtree(first)
        before = {p.name: (p / 'HxStore.hxd').read_bytes() for p in self.snapshots()}
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(before, {p.name: (p / 'HxStore.hxd').read_bytes()
                                  for p in self.snapshots()})
        self.assertEqual(len(list(self.out.iterdir())), 21)

    def test_busy_prune_never_leaves_a_gutted_published_snapshot(self):
        self.assertEqual(self.copy().returncode, 0)
        first, = self.snapshots()
        for index in range(20):
            shutil.copytree(first, self.out / f'snap-20000101T000000-{index:09d}Z')
        shutil.rmtree(first)
        before = {p.name for p in self.snapshots()}
        wrapper = self.root / 'busy-rmdir.c'
        wrapper.write_text('''#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>
static int busy_unlinkat(int fd, const char *name, int flags) {
    if (flags == AT_REMOVEDIR && strstr(name, "snap-20000101T000000-000000000Z")) {
        errno = EBUSY;
        return -1;
    }
    return unlinkat(fd, name, flags);
}
#define unlinkat busy_unlinkat
''' + f'#include {json.dumps(str(ROOT / "tools/outlook-research-copy.c"))}\n')
        self.build(wrapper)
        self.assertNotEqual(self.copy().returncode, 0)
        for snapshot in self.snapshots():
            self.assertIn(snapshot.name, before)
            self.assertEqual((snapshot / 'HxStore.hxd').read_bytes(), b'synthetic-store')
            self.assertTrue((snapshot / 'manifest.json').is_file())
        self.assertFalse(list(self.out.glob('.*.pending')))
        self.build(ROOT / 'tools/outlook-research-copy.c')
        self.assertEqual(self.copy().returncode, 0)
        self.assertEqual(len(self.snapshots()), 20)

    def test_one_run_restores_limit_when_many_old_snapshots_exist(self):
        self.assertEqual(self.copy().returncode, 0)
        first, = self.snapshots()
        for index in range(25):
            shutil.copytree(first, self.out / f'snap-20000101T000000-{index:09d}Z')
        self.assertEqual(self.copy().returncode, 0)
        self.assertEqual(len(self.snapshots()), 20)

    def test_arguments_and_wrong_destinations_are_refused(self):
        self.assertNotEqual(self.copy(str(self.root / 'elsewhere')).returncode, 0)
        self.assertFalse(self.out.exists())
        self.out.mkdir()
        self.out.chmod(0o755)
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(list(self.out.iterdir()), [])
        self.out.rmdir()
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        self.out.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_sidecars_still_produces_a_store_snapshot(self):
        shutil.rmtree(self.profile / 'Files')
        self.assertEqual(self.copy().returncode, 0)
        snapshot, = self.snapshots()
        self.assertTrue((snapshot / 'Files').is_dir())
        self.assertEqual(len(json.loads((snapshot / 'manifest.json').read_text())['files']), 1)

    def test_profile_and_store_links_cannot_redirect_the_compiled_source(self):
        original = self.root / 'original-profile'
        self.profile.rename(original)
        self.profile.symlink_to(original, target_is_directory=True)
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(self.snapshots(), [])
        self.profile.unlink()
        original.rename(self.profile)
        store = self.profile / 'HxStore.hxd'
        store.unlink()
        store.symlink_to(self.sidecars / '7.dat')
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(self.snapshots(), [])

    def test_source_symlink_is_refused_without_publishing_or_leaving_copied_data(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'outside-scope')
        body = self.sidecars / '7.dat'
        body.unlink()
        body.symlink_to(outside)
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual(self.snapshots(), [])
        self.assertEqual([p.name for p in self.out.iterdir()], ['.lock'])
        self.assertEqual(outside.read_bytes(), b'outside-scope')


@unittest.skipUnless(sys.platform == 'darwin', 'the installer requires macOS')
class InstallerTest(unittest.TestCase):
    def test_fixture_install_preserves_identity_pins_binary_and_only_renders_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo, home = root / 'repo', root / 'home & fixtures'
            for relative in ('bin', 'tools', 'spec/outlook', '.venv/bin'):
                (repo / relative).mkdir(parents=True)
            (repo / '.venv/bin/python').symlink_to(sys.executable)
            for relative in ('bin/install-outlook-research-copy.sh',
                             'tools/outlook-research-copy.c',
                             'spec/outlook/com.carr.outlook-research-copy.plist'):
                shutil.copyfile(ROOT / relative, repo / relative)
            command = ['/bin/zsh', '-f', '-c',
                       'export HOME="$1"; exec /bin/zsh -f "$2"',
                       'fixture-installer', str(home), str(repo / 'bin/install-outlook-research-copy.sh')]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            helper = home / 'carr-local/bin/outlook-research-copy'
            pin = helper.with_suffix('.pin')
            self.assertTrue(helper.is_file())
            self.assertTrue(pin.is_file())
            self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
            self.assertIn('Full Disk Access', result.stdout)
            self.assertIn('launchctl bootstrap', result.stdout)
            self.assertFalse((home / 'Library/LaunchAgents').exists())
            self.assertFalse((home / 'carr-local/outlook-research').exists())
            rendered = repo / 'out/com.carr.outlook-research-copy.plist'
            job = plistlib.loads(rendered.read_bytes())
            self.assertEqual(job['ProgramArguments'], [str(helper)])
            for key in ('RunAtLoad', 'KeepAlive', 'StartInterval', 'StartCalendarInterval',
                        'WatchPaths', 'QueueDirectories'):
                self.assertNotIn(key, job)
            load = next(line.strip() for line in result.stdout.splitlines()
                        if line.startswith('  mkdir -p '))
            subprocess.run(['/bin/zsh', '-f', '-c', load], check=True, capture_output=True)
            self.assertEqual(plistlib.loads((home / 'Library/LaunchAgents/com.carr.outlook-research-copy.plist').read_bytes()), job)
            before = helper.stat().st_mtime_ns
            original = helper.read_bytes()
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(helper.stat().st_mtime_ns, before)
            helper.write_bytes(b'tampered')
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(helper.read_bytes(), original)
            profile = home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity'
            profile.mkdir(parents=True)
            (profile / 'HxStore.hxd').write_bytes(b'fixture-installed-store')
            subprocess.run([str(helper)], cwd=root, check=True, capture_output=True)
            snapshots = list((home / 'carr-local/outlook-research').glob('snap-*'))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual((snapshots[0] / 'HxStore.hxd').read_bytes(), b'fixture-installed-store')


if __name__ == '__main__':
    unittest.main()
