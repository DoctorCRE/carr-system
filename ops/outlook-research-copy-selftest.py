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

    def test_profile_permission_denial_names_helper_and_fix(self):
        wrapper = self.root / 'denied-profile.c'
        wrapper.write_text('''#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
static int denied_openat(int fd, const char *name, int flags, ...) {
    if (strcmp(name, "Main Identity") == 0) { errno = EPERM; return -1; }
    if (flags & O_CREAT) {
        va_list args; va_start(args, flags); int mode = va_arg(args, int); va_end(args);
        return openat(fd, name, flags, mode);
    }
    return openat(fd, name, flags);
}
#define openat denied_openat
''' + f'#include {json.dumps(str(ROOT / "tools/outlook-research-copy.c"))}\n')
        self.build(wrapper)
        result = self.copy()
        self.assertEqual(result.returncode, 77)
        self.assertIn(f'full_disk_access_missing: {self.helper}'.encode(), result.stderr)
        self.assertIn(b'Full Disk Access', result.stderr)

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

    def test_repeated_snapshots_preserve_all_completed_snapshots(self):
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
        self.assertEqual([p.name for p in kept], names)
        self.assertEqual((first / 'HxStore.hxd').read_bytes(), b'synthetic-store')
        self.assertEqual((foreign / 'keep.txt').read_bytes(), b'not-a-helper-snapshot')
        self.assertTrue(alias.is_symlink())
        self.assertEqual((outside / 'keep.txt').read_bytes(), b'outside')
        self.assertTrue((self.out / 'snap-not-a-timestamp').is_dir())

    def test_future_dated_snapshot_does_not_prevent_capture(self):
        self.assertEqual(self.copy().returncode, 0)
        first, = self.snapshots()
        future = self.out / 'snap-29990101T000000-000000000Z'
        first.rename(future)
        before = {str(p.relative_to(future)): p.read_bytes()
                  for p in future.rglob('*') if p.is_file()}
        self.assertEqual(self.copy().returncode, 0)
        self.assertEqual(len(self.snapshots()), 2)
        self.assertEqual(before, {str(p.relative_to(future)): p.read_bytes()
                                  for p in future.rglob('*') if p.is_file()})

    def test_failed_publication_preserves_every_existing_snapshot(self):
        self.assertEqual(self.copy().returncode, 0)
        first, = self.snapshots()
        for index in range(25):
            shutil.copytree(first, self.out / f'snap-20000101T000000-{index:09d}Z')
        names = {p.name for p in self.snapshots()}
        before = {str(p.relative_to(self.out)): p.read_bytes()
                  for snapshot in self.snapshots()
                  for p in snapshot.rglob('*') if p.is_file()}
        wrapper = self.root / 'failed-publish.c'
        wrapper.write_text('''#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE
#include <errno.h>
#include <stdio.h>
#include <string.h>
static int fail_publish(int fromfd, const char *from, int tofd, const char *to, unsigned int flags) {
    if (strstr(from, ".pending")) { errno = EIO; return -1; }
    return renameatx_np(fromfd, from, tofd, to, flags);
}
#define renameatx_np fail_publish
''' + f'#include {json.dumps(str(ROOT / "tools/outlook-research-copy.c"))}\n')
        self.build(wrapper)
        self.assertNotEqual(self.copy().returncode, 0)
        self.assertEqual({p.name for p in self.snapshots()}, names)
        self.assertEqual(before, {str(p.relative_to(self.out)): p.read_bytes()
                                  for snapshot in self.snapshots()
                                  for p in snapshot.rglob('*') if p.is_file()})
        self.assertEqual({p.name for p in self.out.iterdir()}, names | {'.lock'})

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


IDENTITY_SHA1 = '0123456789ABCDEF0123456789ABCDEF01234567'
REQUIREMENT = ('designated => identifier "com.carr.outlook-research-copy" and '
               f'certificate leaf = H"{IDENTITY_SHA1.lower()}"')
FALLBACK = ('CARR Local Helpers signing identity not found; keeping the pinned ad-hoc helper, '
            'so a rebuild will need Full Disk Access re-granted')
# Read-only keychain queries; IDENTITY_NAME lists one valid identity of that name.
SECURITY_STUB = f'''#!{sys.executable}
import json, os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write(json.dumps(['security', *sys.argv[1:]]) + '\\n')
name = os.environ.get('IDENTITY_NAME')
if sys.argv[1] == 'find-identity':
    if name:
        print('  1) {IDENTITY_SHA1} "' + name + '"')
    print('     ' + str(int(bool(name))) + ' valid identities found')
else:
    sys.exit(44)
'''
# Logs each call; -d prints the requirement the signing call passed with -r=.
CODESIGN_STUB = f'''#!{sys.executable}
import json, os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write(json.dumps(['codesign', *sys.argv[1:]]) + '\\n')
if '-d' in sys.argv:
    print('Executable=' + sys.argv[-1], file=sys.stderr)
    print({json.dumps(REQUIREMENT)})
'''


@unittest.skipUnless(sys.platform == 'darwin', 'the installer requires macOS')
class InstallerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo, self.home = self.root / 'repo', self.root / 'home & fixtures'
        for relative in ('bin', 'tools', 'spec/outlook', 'lib', '.venv/bin'):
            (self.repo / relative).mkdir(parents=True)
        (self.repo / '.venv/bin/python').symlink_to(sys.executable)
        for relative in ('bin/install-outlook-research-copy.sh',
                         'tools/outlook-research-copy.c',
                         'lib/helper_signing.py',
                         'spec/outlook/com.carr.outlook-research-copy.plist'):
            shutil.copyfile(ROOT / relative, self.repo / relative)
        self.stubs = self.root / 'stubs'
        self.stubs.mkdir()
        for name, text in (('security', SECURITY_STUB), ('codesign', CODESIGN_STUB)):
            (self.stubs / name).write_text(text)
            (self.stubs / name).chmod(0o700)
        self.log = self.root / 'stub-calls'
        self.helper = self.home / 'carr-local/bin/outlook-research-copy'
        self.pin = self.helper.with_suffix('.pin')

    def install(self, *args, identity=False, check=True):
        command = ['/bin/zsh', '-f', '-c',
                   'export HOME="$1"; exec /bin/zsh -f "$2" "${@:3}"',
                   'fixture-installer', str(self.home), str(self.repo / 'bin/install-outlook-research-copy.sh'), *args]
        env = {**os.environ, 'PATH': f"{self.stubs}:{os.environ['PATH']}", 'STUB_LOG': str(self.log)}
        env.pop('IDENTITY_NAME', None)
        if identity:
            env['IDENTITY_NAME'] = 'CARR Local Helpers'
        return subprocess.run(command, check=check, capture_output=True, text=True, env=env)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_fixture_install_preserves_identity_pins_binary_and_only_renders_job(self):
        home, repo, root, helper, pin = self.home, self.repo, self.root, self.helper, self.pin
        result = self.install()
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
        self.install()
        self.assertEqual(helper.stat().st_mtime_ns, before)
        helper.write_bytes(b'tampered')
        self.install()
        self.assertEqual(helper.read_bytes(), original)
        profile = home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity'
        profile.mkdir(parents=True)
        (profile / 'HxStore.hxd').write_bytes(b'fixture-installed-store')
        subprocess.run([str(helper)], cwd=root, check=True, capture_output=True)
        snapshots = list((home / 'carr-local/outlook-research').glob('snap-*'))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual((snapshots[0] / 'HxStore.hxd').read_bytes(), b'fixture-installed-store')

    def test_carr_local_helpers_identity_signs_with_an_explicit_certificate_requirement(self):
        result = self.install(identity=True)
        self.assertEqual(self.calls()[0], ['security', 'find-identity', '-p', 'codesigning', '-v'])
        signing = [call for call in self.calls() if call[0] == 'codesign']
        self.assertEqual([call[:-1] for call in signing], [
            ['codesign', '--force', '-s', IDENTITY_SHA1, '--identifier', 'com.carr.outlook-research-copy',
             f'-r={REQUIREMENT}'],
            ['codesign', '--verify', '--strict'],
            ['codesign', '-d', '-r-']])
        self.assertIn(REQUIREMENT, result.stdout.splitlines())
        self.assertIn('rebuilds keep the grant', result.stdout)
        self.assertNotIn(FALLBACK, result.stderr)
        pinned = json.loads(self.pin.read_text())
        self.assertEqual(pinned['binary_sha256'], hashlib.sha256(self.helper.read_bytes()).hexdigest())
        before = self.helper.stat().st_mtime_ns
        self.install(identity=True)
        self.assertEqual(self.helper.stat().st_mtime_ns, before, 'an unchanged signed helper is kept')
        self.pin.unlink()
        self.install(identity=True)
        self.assertEqual(sum(call[:2] == ['codesign', '--force'] for call in self.calls()), 2,
                         'a deleted pin rebuilds the signed helper')

    def test_missing_identity_prints_one_line_and_keeps_the_pinned_ad_hoc_helper(self):
        result = self.install()
        self.assertEqual(result.stderr.splitlines().count(FALLBACK), 1)
        self.assertFalse([call for call in self.calls() if call[0] == 'codesign'])
        flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                 f'-DOUTLOOK_PROFILE={json.dumps(str(self.home / "Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity"))}',
                 f'-DOUTLOOK_RESEARCH_ROOT={json.dumps(str(self.home / "carr-local/outlook-research"))}']
        source = (self.repo / 'tools/outlook-research-copy.c').read_bytes()
        self.assertEqual(json.loads(self.pin.read_text())['build_sha256'],
                         hashlib.sha256(source + json.dumps(flags).encode()).hexdigest(),
                         'the ad-hoc pin keeps its pre-identity format')
        self.assertIn('Rebuilding the binary requires re-granting access.', result.stdout)
        before = self.helper.stat().st_mtime_ns
        result = self.install()
        self.assertEqual(self.helper.stat().st_mtime_ns, before, 'an ad-hoc helper is never rebuilt needlessly')
        self.assertIn(FALLBACK, result.stderr)
        self.assertNotEqual(self.install('--rebuild-helper', check=False).returncode, 0, 'no arguments accepted')


if __name__ == '__main__':
    unittest.main()
