#!/usr/bin/env python3
"""Build and run the dedicated copier against synthetic files only."""
import hashlib
import json
import importlib.util
import os
import plistlib
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from git_env import fixture_env
from outlook_test_support import write_settings
from lib.outlook_settings import load_settings, SettingsError


# Rewrites every page of the file in order, forever: generation, page, then a
# filler that repeats the generation so a torn page is visible too.
WRITER = """
import os, struct, sys
fd = os.open(sys.argv[1], os.O_WRONLY)
pages = int(sys.argv[2])
generation = 0
while True:
    generation += 1
    page = struct.pack('<QQ', generation, 0) + struct.pack('<Q', generation) * 510
    for index in range(pages):
        os.pwrite(fd, page[:8] + struct.pack('<Q', index) + page[16:], index * 4096)
"""


def page_stamps(data, pages):
    stamps = []
    for index in range(pages):
        page = data[index * 4096:(index + 1) * 4096]
        generation, = struct.unpack_from('<Q', page)
        whole = page[16:] == struct.pack('<Q', generation) * 510
        stamps.append(generation if whole and struct.unpack_from('<Q', page, 8)[0] in (0, index) else -1)
    return stamps


def one_instant(stamps):
    """Pages read g..g then g-1..g-1 (one boundary), with no torn page."""
    if -1 in stamps:
        return False
    drops = [(a, b) for a, b in zip(stamps, stamps[1:]) if a != b]
    return len(drops) <= 1 and all(a == b + 1 for a, b in drops)


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
        self.build()

    def build(self, *sources):
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        f'-DOUTLOOK_PROFILE={json.dumps(str(self.profile))}',
                        f'-DOUTLOOK_WORK_ROOT={json.dumps(str(self.out))}',
                        f'-DOUTLOOK_REPO={json.dumps(str(self.root))}',
                        str(ROOT / 'tools/outlook-intake-copy.c'), *sources, '-o', str(self.helper)],
                       check=True, capture_output=True)

    def copy(self, *args, cwd=None, env=None, input=None):
        return subprocess.run([str(self.helper), *args], cwd=cwd or self.work,
                              env=env, input=input, capture_output=True)

    def sidecar_open_fault(self, action):
        race = self.root / 'remove-sidecar.c'
        race.write_text("""
#define _DARWIN_C_SOURCE
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
#include <unistd.h>
int openat(int fd, const char *name, int flags, ...) {
    int mode = 0;
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, int);
        va_end(args);
    }
    if (!(flags & O_CREAT) && strcmp(name, "7.dat") == 0) { SIDECAR_FAULT; }
    int (*real_openat)(int, const char *, int, ...) = dlsym(RTLD_NEXT, "openat");
    return real_openat(fd, name, flags, mode);
}
""".replace('SIDECAR_FAULT', action))
        self.build(str(race))

    def test_sidecar_disappearing_at_open_does_not_fail_snapshot(self):
        self.sidecar_open_fault('unlinkat(fd, name, 0)')
        result = self.copy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.sidecars / '7.dat').exists(), 'the race deleted the source after listing')
        self.assertFalse((self.work / 'Files/S0/845/EFMData/7.dat').exists())
        self.assertEqual((self.work / 'store.hxd').read_bytes(), b'synthetic-store')

    def test_sidecar_open_errors_other_than_enoent_still_fail(self):
        self.sidecar_open_fault('errno = EACCES; return -1')
        result = self.copy()
        self.assertEqual(result.returncode, 13)
        self.assertIn(b'errno 13', result.stderr)

    def test_missing_required_store_still_fails(self):
        (self.profile / 'HxStore.hxd').unlink()
        self.assertEqual(self.copy().returncode, 2)

    def test_selected_attachments_are_copied_privately_without_refreshing_snapshot(self):
        selected = self.profile / 'Files/S0/845/Attachments/9'
        selected.mkdir(parents=True)
        (selected / 'selected.pdf').write_bytes(b'synthetic-attachment')
        (selected / 'unmatched.pdf').write_bytes(b'must-not-copy')
        self.assertEqual(self.copy().returncode, 0)
        (self.profile / 'HxStore.hxd').write_bytes(b'changed-store')
        (self.sidecars / '7.dat').write_bytes(b'changed-body')
        request = b'~/Files/S0/845/Attachments/9/selected.pdf\n'
        for _ in range(2):
            self.assertEqual(self.copy('--attachments', input=request).returncode, 0)
        copied = self.work / 'Files/S0/845/Attachments/9/selected.pdf'
        self.assertEqual(copied.read_bytes(), b'synthetic-attachment')
        self.assertFalse(copied.with_name('unmatched.pdf').exists())
        self.assertEqual((self.work / 'store.hxd').read_bytes(), b'synthetic-store')
        self.assertEqual((self.work / 'Files/S0/845/EFMData/7.dat').read_bytes(), b'synthetic-body')
        for directory, _, files in os.walk(self.work):
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            for name in files:
                self.assertEqual(stat.S_IMODE((Path(directory) / name).stat().st_mode), 0o600)

    def test_attachment_requests_reject_malformed_paths(self):
        self.assertEqual(self.copy().returncode, 0)
        for request in (b'Files/S0/845/Attachments/9/a.pdf\n',
                        b'~/Files/S0/../Attachments/9/a.pdf\n',
                        b'~/Files/S0/845/EFMData/9/a.pdf\n',
                        b'~/Files/S0/845/Attachments/x/a.pdf\n',
                        b'~/Files/S0/845/Attachments/9/../a.pdf\n',
                        b'~/Files/S0/845/Attachments/9/..\n',
                        b'~/Files/S0/845/Attachments/9/a.pdf',
                        b'~/Files/S0/845/Attachments/9/a\tb.pdf\n',
                        b'~/Files/S0/845/Attachments/9/a\x00.pdf\n', b'\n'):
            with self.subTest(request=request):
                self.assertNotEqual(self.copy('--attachments', input=request).returncode, 0)
        self.assertFalse((self.work / 'Files/S0/845/Attachments').exists())
        self.assertNotEqual(self.copy('--attachments', input=b'', cwd=self.root).returncode, 0)

    def test_missing_and_oversize_attachments_are_not_copied(self):
        selected = self.profile / 'Files/S0/845/Attachments/9'
        selected.mkdir(parents=True)
        with (selected / 'large.pdf').open('wb') as stream:
            stream.truncate(25 * 1024 * 1024 + 1)
        self.assertEqual(self.copy().returncode, 0)
        request = b'~/Files/S0/845/Attachments/9/large.pdf\n~/Files/S0/845/Attachments/9/missing.pdf\n'
        self.assertEqual(self.copy('--attachments', input=request).returncode, 0)
        self.assertEqual(list((self.work / 'Files/S0/845/Attachments/9').iterdir()), [])

    def test_attachment_symlinks_are_refused_at_source_and_destination(self):
        selected = self.profile / 'Files/S0/845/Attachments/9'
        selected.mkdir(parents=True)
        outside = self.root / 'outside'
        outside.write_bytes(b'outside-scope')
        attachment = selected / 'a.pdf'
        attachment.symlink_to(outside)
        self.assertEqual(self.copy().returncode, 0)
        request = b'~/Files/S0/845/Attachments/9/a.pdf\n'
        self.assertNotEqual(self.copy('--attachments', input=request).returncode, 0)
        attachment.unlink()
        attachment.write_bytes(b'allowed')
        destination = self.work / 'Files/S0/845/Attachments/9/a.pdf'
        destination.symlink_to(outside)
        self.assertNotEqual(self.copy('--attachments', input=request).returncode, 0)
        self.assertEqual(outside.read_bytes(), b'outside-scope')
        destination.unlink()
        shutil.rmtree(selected)
        selected.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.copy('--attachments', input=request).returncode, 0)
        selected.unlink()
        selected.mkdir()
        (selected / 'a.pdf').write_bytes(b'allowed')
        destination.parent.rmdir()
        destination.parent.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.copy('--attachments', input=request).returncode, 0)
        self.assertFalse((self.root / 'a.pdf').exists())

    def test_attachment_copy_caps_batch_count_and_bytes(self):
        selected = self.profile / 'Files/S0/845/Attachments/9'
        selected.mkdir(parents=True)
        self.assertEqual(self.copy().returncode, 0)
        for name in range(51):
            (selected / str(name)).write_bytes(b'a')
        requests = ''.join(f'~/Files/S0/845/Attachments/9/{name}\n' for name in range(51)).encode()
        self.assertEqual(self.copy('--attachments', input=requests).returncode, 0)
        destination = self.work / 'Files/S0/845/Attachments/9'
        self.assertEqual(len(list(destination.iterdir())), 50)
        self.assertFalse((destination / '50').exists())
        shutil.rmtree(destination)
        for name in range(5):
            with (selected / str(name)).open('wb') as stream:
                stream.truncate(25 * 1024 * 1024)
        requests = ''.join(f'~/Files/S0/845/Attachments/9/{name}\n' for name in range(5)).encode()
        self.assertEqual(self.copy('--attachments', input=requests).returncode, 0)
        self.assertEqual(sum(path.stat().st_size for path in destination.iterdir()), 100 * 1024 * 1024)
        self.assertFalse((destination / '4').exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'APFS cloning is macOS only')
    def test_attachment_copy_falls_back_when_cloning_is_unsupported(self):
        self.build(str(self.unsupported_clone()))
        self.test_selected_attachments_are_copied_privately_without_refreshing_snapshot()

    @unittest.skipUnless(sys.platform == 'darwin', 'APFS cloning is macOS only')
    def test_store_and_sidecars_are_cloned_when_the_volume_supports_it(self):
        log = self.root / 'clones.log'
        recording = self.root / 'recording-clone.c'
        recording.write_text('\n'.join((
            '#include <dlfcn.h>', '#include <stdio.h>', '#include <sys/clonefile.h>',
            'int fclonefileat(int source, int target, const char *name, uint32_t flags) {',
            f'    FILE *log = fopen({json.dumps(str(log))}, "a");',
            '    if (log) { fprintf(log, "%s\\n", name); fclose(log); }',
            '    int (*real)(int, int, const char *, uint32_t) =',
            '        (int (*)(int, int, const char *, uint32_t))dlsym(RTLD_NEXT, "fclonefileat");',
            '    return real(source, target, name, flags);', '}', '')))
        self.build(str(recording))
        self.test_copies_only_the_fixed_store_and_body_sidecars_privately()
        self.assertEqual(log.read_text().splitlines(), ['store.hxd', '7.dat'])

    @unittest.skipUnless(sys.platform == 'darwin', 'APFS cloning is macOS only')
    def test_store_copy_falls_back_to_a_full_copy_when_cloning_is_unsupported(self):
        self.build(str(self.unsupported_clone()))
        self.test_copies_only_the_fixed_store_and_body_sidecars_privately()

    @unittest.skipUnless(sys.platform == 'darwin', 'APFS cloning is macOS only')
    def test_store_clone_is_one_instant_while_the_store_is_being_written(self):
        # A writer stamps every 4 KiB page with a generation number, sweeping
        # the file front to back again and again, one pwrite per page. A copy
        # taken at one instant shows at most one boundary, between generation
        # g and g - 1; a copy assembled over time can show more.
        pages = 4096
        store = self.profile / 'HxStore.hxd'
        store.write_bytes(bytes(4096 * pages))
        writer = subprocess.Popen([sys.executable, '-c', WRITER, str(store), str(pages)])
        self.addCleanup(writer.kill)
        for attempt in range(20):
            work = Path(tempfile.mkdtemp(prefix='work-', dir=self.out))
            self.assertEqual(self.copy(cwd=work).returncode, 0)
            stamps = page_stamps((work / 'store.hxd').read_bytes(), pages)
            self.assertTrue(one_instant(stamps), f'copy {attempt} mixes moments: {sorted(set(stamps))}')
            shutil.rmtree(work)
        self.assertIsNone(writer.poll(), 'the writer kept writing throughout')

    def unsupported_clone(self):
        unsupported = self.root / 'unsupported-clone.c'
        unsupported.write_text('\n'.join((
            '#include <errno.h>', '#include <sys/clonefile.h>',
            'int fclonefileat(int source, int target, const char *name, uint32_t flags) {',
            '    (void)source; (void)target; (void)name; (void)flags;',
            '    errno = ENOTSUP; return -1;', '}', '')))
        return unsupported

    def test_scope_readback_names_only_the_compiled_paths_without_copying(self):
        result = self.copy('--scope', cwd=self.root)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode(), f'{self.profile}\n{self.out}\n{self.root}\n')
        self.assertFalse((self.work / 'store.hxd').exists())

    def test_helper_launches_only_fixed_intake_and_outbox_commands(self):
        (self.root / 'bin').mkdir()
        (self.root / 'bin/run-scheduled.sh').write_text('printf "%s\\n" "$@"\nexit 23\n')
        for service, mode, tail in (
                ('intake', '--run-intake', ['-c', 'from lib.outlook_intake import main; raise SystemExit(main())']),
                ('outbox', '--run-outbox', ['-m', 'lib.outlook_outbox'])):
            with self.subTest(service=service):
                result = self.copy(mode, cwd=self.root)
                self.assertEqual(result.returncode, 23)
                self.assertEqual(result.stdout.decode().splitlines(), [f'outlook-{service}',
                    f'outlook.{service}', str(self.root / '.venv/bin/python'), *tail])
        self.assertNotEqual(self.copy('--run-outbox', '/tmp/command').returncode, 0)

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


def digest_gate():
    spec = importlib.util.spec_from_file_location('fixture_digest', ROOT / 'ops/hxprobe-source-digest.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


@unittest.skipUnless(sys.platform == 'darwin', 'the installer requires macOS zsh and plutil')
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.home = self.root / 'repo & quoted', self.root / 'home'
        for relative in ('bin', 'tools', 'ops/launchd', 'lib', '.venv/bin', 'vendor/hxprobe', 'spec/outlook'):
            (self.repo / relative).mkdir(parents=True)
        for relative in ('ops/hxprobe-source-digest.py', 'bin/install-outlook-intake.sh', 'bin/install-outlook-triage.sh',
                         'tools/outlook-intake-copy.c', 'lib/outlook_settings.py',
                         'ops/launchd/com.carr.outlook-intake.plist',
                         'ops/launchd/com.carr.outlook-refresh.plist',
                         'ops/launchd/com.carr.outlook-outbox.plist',
                         'ops/launchd/com.carr.outlook-triage.plist'):
            shutil.copyfile(ROOT / relative, self.repo / relative)
        for relative in ('vendor/hxprobe/Cargo.toml', 'spec/outlook/hxstore-atlas.json', 'ops/hxstore-atlas.py'):
            (self.repo / relative).write_text('synthetic decoder source\n')
        (self.repo / '.venv/bin/python').symlink_to(sys.executable)
        (self.repo / '.gitignore').write_text('.venv/\nout\n')
        self.git(self.repo, 'init', '--quiet')
        self.git(self.repo, 'add', '-A')
        self.git(self.repo, 'commit', '--quiet', '-m', 'fixture')
        self.profile = self.home / 'Custom Profile'
        self.helper = self.home / 'custom binaries/granted-outlook-copy'
        self.vendor = self.home / 'custom vendor/hxstore'
        self.config = write_settings(self.home, self.repo, profile=str(self.profile),
                                     vendor=str(self.vendor), copy_helper=str(self.helper))
        (self.vendor / 'hxprobe/target/release').mkdir(parents=True)
        decoder = self.vendor / 'hxprobe/target/release/hxprobe'
        decoder.write_text('#!/bin/sh\nexit 99\n')
        decoder.chmod(0o700)
        receipt = {'source_digest': digest_gate().digest(self.repo / 'vendor/hxprobe'),
                   'binary_sha256': hashlib.sha256(decoder.read_bytes()).hexdigest()}
        (self.vendor / '.carr-build.json').write_text(json.dumps(receipt))

    def install(self, name='intake', check=True):
        command = ['/bin/zsh', '-f', '-c', 'export HOME="$1"; exec /bin/zsh -f "$2"',
                   'installer-test', str(self.home), str(self.repo / f'bin/install-outlook-{name}.sh')]
        return subprocess.run(command, capture_output=True, text=True, check=check, env=fixture_env())

    def test_installer_uses_settings_without_a_machine_role_and_preserves_the_granted_binary(self):
        result = self.install()
        self.assertTrue(self.helper.is_file())
        self.assertEqual(stat.S_IMODE(self.helper.stat().st_mode), 0o700)
        self.assertIn(f'Full Disk Access must be re-granted for {self.helper}', result.stdout)
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

    def test_unchanged_install_keeps_binary_bytes_and_mtime_without_an_access_warning(self):
        self.install()
        before_bytes = self.helper.read_bytes()
        before_mtime = self.helper.stat().st_mtime_ns
        result = self.install()
        self.assertEqual(self.helper.read_bytes(), before_bytes)
        self.assertEqual(self.helper.stat().st_mtime_ns, before_mtime)
        self.assertNotIn('Full Disk Access', result.stdout)

    def test_source_change_rebuilds_once_and_names_the_binary_needing_access(self):
        self.install()
        before = self.helper.read_bytes()
        source = self.repo / 'tools/outlook-intake-copy.c'
        source.write_text(source.read_text().replace('snapshot failed', 'fixture snapshot failed'))
        result = self.install()
        self.assertNotEqual(self.helper.read_bytes(), before)
        warning = f'Full Disk Access must be re-granted for {self.helper}'
        self.assertEqual(sum(warning in line for line in result.stdout.splitlines()), 1)
        before_bytes = self.helper.read_bytes()
        before_mtime = self.helper.stat().st_mtime_ns
        result = self.install()
        self.assertEqual(self.helper.read_bytes(), before_bytes)
        self.assertEqual(self.helper.stat().st_mtime_ns, before_mtime)
        self.assertNotIn('Full Disk Access', result.stdout)

    def test_compiled_settings_change_rebuilds_and_warns_for_the_configured_binary(self):
        self.install()
        values = json.loads(self.config.read_text())
        values['profile'] = str(self.home / 'Changed Profile')
        self.config.write_text(json.dumps(values))
        result = self.install()
        scope = subprocess.run([str(self.helper), '--scope'], capture_output=True, text=True, check=True)
        self.assertEqual(scope.stdout.splitlines()[0], values['profile'])
        self.assertIn(f'Full Disk Access must be re-granted for {self.helper}', result.stdout)

    def test_outbox_is_rendered_only_when_enabled_and_never_loaded(self):
        self.install()
        rendered = self.repo / 'out/com.carr.outlook-outbox.plist'
        self.assertFalse(rendered.exists())
        before = self.helper.read_bytes()
        values = json.loads(self.config.read_text())
        values['services'].append('outbox')
        self.config.write_text(json.dumps(values))
        result = self.install()
        plist = plistlib.loads(rendered.read_bytes())
        self.assertEqual(plist['WorkingDirectory'], str(self.repo))
        self.assertEqual(plist['StartCalendarInterval'], [{'Minute': n} for n in range(60)])
        self.assertEqual(plist['ProgramArguments'], [str(self.helper), '--run-outbox'])
        self.assertEqual(self.helper.read_bytes(), before)
        self.assertEqual(stat.S_IMODE((self.repo / 'out/outlook-outbox-launchd.log').stat().st_mode), 0o600)
        self.assertIn('outbox worker rendered; it is NOT loaded', result.stdout)
        self.assertFalse((self.home / 'Library/LaunchAgents').exists())

    def git(self, checkout, *args):
        return subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.test',
                               *args], cwd=checkout, env=fixture_env(), check=True, capture_output=True, text=True).stdout.strip()

    def test_first_build_uses_configured_tools_for_another_owner(self):
        decoder = self.vendor / 'hxprobe/target/release/hxprobe'
        decoder.unlink()
        (self.vendor / '.carr-build.json').unlink()
        tools = self.home / 'configured tools'
        tools.mkdir(parents=True)
        cargo = tools / 'cargo'
        cargo.write_text(f'#!{sys.executable}\n' + '''
from pathlib import Path
import sys
args = sys.argv[1:]
if args == ['--version']:
    print('cargo synthetic')
elif args[0] == 'build':
    out = Path(args[args.index('--target-dir') + 1]) / 'release/hxprobe'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b'synthetic local decoder')
''')
        cargo.chmod(0o700)
        rustc = tools / 'rustc'
        rustc.write_text(f'#!{sys.executable}\nprint("rustc synthetic-other-host")\n')
        rustc.chmod(0o700)
        gate = digest_gate()
        write_settings(self.home, self.repo, owner='dell', account='partner@example.test',
                       profile=str(self.home / 'Main Profile'), vendor=str(self.vendor),
                       copy_helper=str(self.helper), executable_path=f'{tools}:/usr/bin:/bin:/usr/sbin:/sbin')
        self.install()
        self.assertEqual(decoder.read_bytes(), b'synthetic local decoder')
        receipt = json.loads((self.vendor / '.carr-build.json').read_text())
        self.assertEqual(receipt['rustc'], 'rustc synthetic-other-host')
        self.assertEqual(receipt['source_digest'], gate.digest(self.repo / 'vendor/hxprobe'))
        load_settings(self.home).verify_helper()
        before = self.helper.stat().st_mtime_ns
        self.install()
        self.assertEqual(self.helper.stat().st_mtime_ns, before)

    def test_an_upstream_decoder_checkout_is_refused_until_moved_aside(self):
        decoder = self.vendor / 'hxprobe/target/release/hxprobe'
        (self.vendor / '.carr-pin').write_text('191b54e42757687806202f8f696e9066f0514e11 a1ca5a3d\n')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('move it aside first', result.stderr)
        self.assertEqual(decoder.read_text(), '#!/bin/sh\nexit 99\n')
        self.assertFalse(self.helper.exists())
        (self.vendor / '.carr-pin').unlink()
        (self.vendor / 'hxprobe/Cargo.toml').write_text('[package]\n')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('move it aside first', result.stderr)

    def test_uncommitted_decoder_source_is_refused_before_any_build(self):
        (self.repo / 'spec/outlook/hxstore-atlas.json').write_text('edited\n')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('uncommitted decoder sources', result.stderr)
        self.assertFalse((self.vendor / 'build').exists())

    def test_jobs_run_from_a_checkout_only_the_installer_moves_without_rebuilding_the_helper(self):
        runtime = self.home / 'carr-local/outlook-repo'
        write_settings(self.home, runtime, profile=str(self.profile), vendor=str(self.vendor),
                       copy_helper=str(self.helper))
        result = self.install()
        first = self.git(self.repo, 'rev-parse', 'HEAD')
        self.assertIn(f'Outlook jobs run from {runtime} at {first}', result.stdout)
        self.assertEqual(self.git(runtime, 'rev-parse', 'HEAD'), first)
        self.assertEqual(self.git(runtime, 'status', '--porcelain', '--untracked-files=no'), '')
        self.assertEqual((runtime / 'out').resolve(), self.repo / 'out')
        self.assertEqual((runtime / '.venv').resolve(), self.repo / '.venv')
        intake = plistlib.loads((self.repo / 'out/com.carr.outlook-intake.plist').read_bytes())
        self.assertEqual(intake['WorkingDirectory'], str(runtime))
        scope = subprocess.run([str(self.helper), '--scope'], capture_output=True, text=True, check=True)
        self.assertEqual(scope.stdout.splitlines(), [str(self.profile), str(self.repo / 'out/outlook-intake'), str(runtime)])
        load_settings(self.home).verify_helper()
        self.profile.mkdir(parents=True)
        (self.profile / 'HxStore.hxd').write_bytes(b'runtime-fixture')
        out = runtime / 'out/outlook-intake'
        out.mkdir(mode=0o700)
        work = Path(tempfile.mkdtemp(prefix='work-', dir=out))
        subprocess.run([str(self.helper)], cwd=work, capture_output=True, check=True)
        self.assertEqual((work / 'store.hxd').read_bytes(), b'runtime-fixture')

        (self.repo / 'lib/later_change.py').write_text('LATER = True\n')
        self.git(self.repo, 'add', '-A')
        self.git(self.repo, 'commit', '--quiet', '-m', 'later')
        second = self.git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(self.git(runtime, 'rev-parse', 'HEAD'), first, 'moving the canonical checkout ships nothing')
        before = self.helper.stat().st_mtime_ns
        self.install()
        self.assertEqual(self.git(runtime, 'rev-parse', 'HEAD'), second)
        self.assertTrue((runtime / 'lib/later_change.py').is_file())
        self.assertEqual(self.helper.stat().st_mtime_ns, before, 'a re-run keeps the granted helper and its compiled paths')

        (runtime / 'lib/later_change.py').write_text('LATER = False\n')
        refused = self.install(check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('must be a clean checkout', refused.stderr)

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
