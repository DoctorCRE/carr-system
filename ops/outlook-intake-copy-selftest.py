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

    def sidecar_open_fault(self, action, name='7.dat'):
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
    if (!(flags & O_CREAT) && strcmp(name, "FAULT_NAME") == 0) { SIDECAR_FAULT; }
    int (*real_openat)(int, const char *, int, ...) = dlsym(RTLD_NEXT, "openat");
    return real_openat(fd, name, flags, mode);
}
""".replace('SIDECAR_FAULT', action).replace('FAULT_NAME', name))
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

    def test_profile_permission_denial_names_helper_and_fix(self):
        self.sidecar_open_fault('errno = EPERM; return -1', name='Main Identity')
        result = self.copy()
        self.assertEqual(result.returncode, 77)
        self.assertIn(f'full_disk_access_missing: {self.helper}'.encode(), result.stderr)
        self.assertIn(b'Full Disk Access', result.stderr)
        self.assertFalse((self.work / 'store.hxd').exists())

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


IDENTITY_SHA1 = '0123456789ABCDEF0123456789ABCDEF01234567'
FALLBACK = ('CARR Local Helpers signing identity not found; keeping the pinned ad-hoc helper, '
            'so a rebuild will need Full Disk Access re-granted')

# Logs each call; -d prints the requirement passed with -r= at signing, unless REQUIREMENT overrides it.
SIGN_STUB = '''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys
with open(os.environ['SIGN_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
signed = Path(os.environ['SIGN_LOG'] + '.requirements')
known = json.loads(signed.read_text()) if signed.exists() else {}
if '-s' in sys.argv:
    if '--force' not in sys.argv:
        sys.exit('already signed; replacement requires --force')
    if os.environ.get('SIGN_FAIL'):
        sys.stderr.write('specified item could not be found in the keychain')
        sys.exit(1)
    given = [arg[3:] for arg in sys.argv if arg.startswith('-r=')]
    known[sys.argv[-1]] = given[0] if given else 'designated => cdhash H"0123"'
    signed.write_text(json.dumps(known))
elif '--verify' in sys.argv:
    pass
elif 'REQUIREMENT' in os.environ:
    print(os.environ['REQUIREMENT'], file=sys.stderr)
else:
    print('Executable=' + sys.argv[-1], file=sys.stderr)
    print(known.get(sys.argv[-1], 'designated => cdhash H"0123"'))
'''

# Read-only keychain queries; IDENTITY_NAME lists one valid identity of that name.
SECURITY_STUB = '''#!/usr/bin/python3
import json
import os
import sys
with open(os.environ['SIGN_LOG'] + '.security', 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
name = os.environ.get('IDENTITY_NAME')
if sys.argv[1] == 'find-identity':
    if name:
        print(f'  1) {os.environ["IDENTITY_SHA1"]} "{name}"')
    print(f'     {1 if name else 0} valid identities found')
elif sys.argv[1] == 'find-certificate' and name and sys.argv[sys.argv.index('-c') + 1] == name:
    print('SHA-256 hash: ' + 'AB' * 32)
    print('SHA-1 hash: ' + os.environ['IDENTITY_SHA1'])
else:
    sys.exit('security: The specified item could not be found in the keychain.')
'''


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
                         'tools/outlook-intake-copy.c', 'lib/outlook_settings.py', 'lib/outlook_helper.py',
                         'ops/launchd/com.carr.outlook-intake.plist',
                         'ops/launchd/com.carr.outlook-outbox.plist',
                         'ops/launchd/com.carr.outlook-triage.plist'):
            shutil.copyfile(ROOT / relative, self.repo / relative)
        shutil.copytree(ROOT / 'lib', self.repo / 'lib', dirs_exist_ok=True)
        shutil.copyfile(ROOT / 'ops/config-as-code.py', self.repo / 'ops/config-as-code.py')
        shutil.copyfile(ROOT / 'ops/git_env.py', self.repo / 'ops/git_env.py')
        shutil.copytree(ROOT / 'ops/config', self.repo / 'ops/config')
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

        self.sign_tools = self.root / 'signing tools'
        self.sign_tools.mkdir()
        launchctl = self.sign_tools / 'launchctl'
        launchctl.write_text('#!/bin/sh\necho \"Could not find service \\\"com.carr.outlook-refresh\\\"\" >&2\nexit 113\n')
        launchctl.chmod(0o700)
        self.sign_log = self.root / 'signing-calls'
        codesign = self.sign_tools / 'codesign'
        codesign.write_text(SIGN_STUB)
        codesign.chmod(0o700)
        security = self.sign_tools / 'security'
        security.write_text(SECURITY_STUB)
        security.chmod(0o700)

    def install(self, name='intake', check=True, signing_env=None, args=()):
        if name == 'intake' and self.config.exists():
            values = json.loads(self.config.read_text())
            if str(self.sign_tools) not in values['executable_path'].split(':'):
                values['executable_path'] = f"{self.sign_tools}:{values['executable_path']}"
                self.config.write_text(json.dumps(values))
        command = ['/bin/zsh', '-f', '-c', 'export HOME="$1"; exec /bin/zsh -f "$2" "${@:3}"',
                   'installer-test', str(self.home), str(self.repo / f'bin/install-outlook-{name}.sh'), *args]
        return subprocess.run(command, capture_output=True, text=True, check=check,
                              env={**fixture_env(), 'SIGN_LOG': str(self.sign_log), **(signing_env or {})})

    def test_installer_retires_refresh_and_is_repeatable(self):
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        live = agents / 'com.carr.outlook-refresh.plist'
        live.write_bytes(plistlib.dumps({'Label': 'com.carr.outlook-refresh'}))
        marker = self.root / 'loaded'
        marker.write_text(str(live))
        launchctl = self.sign_tools / 'launchctl'
        launchctl.write_text("""#!/usr/bin/python3
import os, pathlib, sys
marker = pathlib.Path(os.environ['LAUNCH_MARKER'])
if sys.argv[1] == 'bootout':
    marker.unlink()
elif marker.exists():
    print('path = ' + marker.read_text())
else:
    print('Could not find service "com.carr.outlook-refresh"', file=sys.stderr)
    sys.exit(113)
""")
        launchctl.chmod(0o700)
        result = self.install(check=False, signing_env={'LAUNCH_MARKER': str(marker)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(live.exists())
        copies = list((self.home / 'Library/LaunchAgents-quarantine/carr-retired').glob('*/com.carr.outlook-refresh.plist'))
        self.assertEqual(len(copies), 1)
        self.install(signing_env={'LAUNCH_MARKER': str(marker)})
        self.assertEqual(len(list(copies[0].parent.parent.glob('*/com.carr.outlook-refresh.plist'))), 1)
        marker.write_text(str(live))
        self.install(signing_env={'LAUNCH_MARKER': str(marker)})
        self.assertFalse(marker.exists())

    def test_installer_uses_settings_without_a_machine_role_and_preserves_the_granted_binary(self):
        result = self.install()
        self.assertTrue(self.helper.is_file())
        self.assertEqual(stat.S_IMODE(self.helper.stat().st_mode), 0o700)
        self.assertIn(f'ad-hoc helper rebuilds need a Full Disk Access re-grant for {self.helper}', result.stdout)
        self.assertFalse((self.home / 'Library/LaunchAgents').exists())
        for service in ('intake',):
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
        before_pin = self.helper.with_suffix('.pin').read_bytes()
        result = self.install()
        self.assertEqual(self.helper.read_bytes(), before_bytes)
        self.assertEqual(self.helper.stat().st_mtime_ns, before_mtime)
        self.assertEqual(self.helper.with_suffix('.pin').read_bytes(), before_pin)
        self.assertNotIn('Full Disk Access', result.stdout)

    def test_changed_digest_refuses_before_touching_helper_or_pin(self):
        self.install()
        before = (self.helper.read_bytes(), self.helper.stat().st_mtime_ns,
                  self.helper.with_suffix('.pin').read_bytes(), self.sign_log.read_bytes())
        source = self.repo / 'tools/outlook-intake-copy.c'
        source.write_text(source.read_text().replace('snapshot failed', 'fixture snapshot failed'))
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('--rebuild-helper', result.stderr)
        self.assertIn('Full Disk Access', result.stderr)
        self.assertEqual((self.helper.read_bytes(), self.helper.stat().st_mtime_ns,
                          self.helper.with_suffix('.pin').read_bytes(), self.sign_log.read_bytes()), before)

    def test_changed_settings_or_missing_pin_require_explicit_rebuild(self):
        self.install()
        before = self.helper.read_bytes(), self.helper.stat().st_mtime_ns, self.sign_log.read_bytes()
        values = json.loads(self.config.read_text())
        values['profile'] = str(self.home / 'Changed Profile')
        self.config.write_text(json.dumps(values))
        for missing_pin in (False, True):
            with self.subTest(missing_pin=missing_pin):
                if missing_pin:
                    values['profile'] = str(self.profile)
                    self.config.write_text(json.dumps(values))
                    self.helper.with_suffix('.pin').unlink()
                result = self.install(check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('--rebuild-helper', result.stderr)
                self.assertEqual((self.helper.read_bytes(), self.helper.stat().st_mtime_ns,
                                  self.sign_log.read_bytes()), before)
                if missing_pin:
                    self.assertFalse(self.helper.with_suffix('.pin').exists())

    def test_source_change_rebuilds_once_and_names_the_binary_needing_access(self):
        self.install()
        before = self.helper.read_bytes()
        source = self.repo / 'tools/outlook-intake-copy.c'
        source.write_text(source.read_text().replace('snapshot failed', 'fixture snapshot failed'))
        result = self.install(args=('--rebuild-helper',))
        self.assertNotEqual(self.helper.read_bytes(), before)
        warning = f'ad-hoc helper rebuilds need a Full Disk Access re-grant for {self.helper}'
        self.assertEqual(sum(warning in line for line in result.stdout.splitlines()), 1)
        self.assertIn('System Settings > Privacy & Security > Full Disk Access', result.stdout)
        self.assertIn(f'Remove the old entry and add {self.helper} again', result.stdout)
        self.assertIn('next scheduled intake run succeeds', result.stdout)
        before_bytes = self.helper.read_bytes()
        before_mtime = self.helper.stat().st_mtime_ns
        result = self.install(args=('--rebuild-helper',))
        self.assertEqual(self.helper.read_bytes(), before_bytes)
        self.assertEqual(self.helper.stat().st_mtime_ns, before_mtime)
        self.assertNotIn('Full Disk Access', result.stdout)

    def test_compiled_settings_change_rebuilds_and_warns_for_the_configured_binary(self):
        self.install()
        values = json.loads(self.config.read_text())
        values['profile'] = str(self.home / 'Changed Profile')
        self.config.write_text(json.dumps(values))
        result = self.install(args=('--rebuild-helper',))
        scope = subprocess.run([str(self.helper), '--scope'], capture_output=True, text=True, check=True)
        self.assertEqual(scope.stdout.splitlines()[0], values['profile'])
        self.assertIn(f'ad-hoc helper rebuilds need a Full Disk Access re-grant for {self.helper}', result.stdout)

    def identity(self, name='CARR Local Helpers', **extra):
        return {'IDENTITY_NAME': name, 'IDENTITY_SHA1': IDENTITY_SHA1, **extra}

    def calls(self, log=''):
        return [json.loads(line) for line in Path(f'{self.sign_log}{log}').read_text().splitlines()]

    def test_carr_local_helpers_identity_signs_with_an_explicit_certificate_requirement(self):
        requirement = ('designated => identifier "com.carr.outlook-intake-copy" and '
                       f'certificate leaf = H"{IDENTITY_SHA1.lower()}"')
        result = self.install(signing_env=self.identity())
        self.assertEqual(self.calls('.security'), [['find-identity', '-p', 'codesigning', '-v'],
                                                   ['find-certificate', '-c', 'CARR Local Helpers', '-Z']])
        signing = self.calls()
        self.assertEqual(signing[0][:-1], ['--force', '-s', IDENTITY_SHA1, '--identifier',
                                           'com.carr.outlook-intake-copy', f'-r={requirement}'])
        self.assertEqual(signing[1][:-1], ['--verify', '--strict'])
        self.assertEqual(signing[2][:-1], ['-d', '-r-'])
        self.assertIn(requirement, result.stdout.splitlines())
        self.assertNotIn(FALLBACK, result.stderr)
        self.assertNotIn('re-grant', result.stdout)
        load_settings(self.home).verify_helper()
        before = self.helper.stat().st_mtime_ns, len(signing)
        self.install(signing_env=self.identity())
        self.assertEqual((self.helper.stat().st_mtime_ns, len(self.calls())), before,
                         'an unchanged signed helper is kept')
        result = self.install(signing_env=self.identity(), args=('--rebuild-helper',))
        self.assertEqual(len([c for c in self.calls() if c[0] == '--force']), 2, 'a signed helper rebuilds on request')
        self.assertNotIn('re-grant', result.stdout)
        load_settings(self.home).verify_helper()

    def test_switching_an_ad_hoc_helper_to_the_identity_needs_the_rebuild_flag(self):
        self.install()
        before = self.helper.stat().st_mtime_ns, self.helper.with_suffix('.pin').read_text()
        result = self.install(check=False, signing_env=self.identity())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--rebuild-helper', result.stderr)
        self.assertEqual((self.helper.stat().st_mtime_ns, self.helper.with_suffix('.pin').read_text()), before)
        self.install(signing_env=self.identity(), args=('--rebuild-helper',))
        forced = [call for call in self.calls() if call[0] == '--force']
        self.assertEqual(forced[-1][1:5], ['-s', IDENTITY_SHA1, '--identifier', 'com.carr.outlook-intake-copy'])
        self.assertNotEqual(self.helper.with_suffix('.pin').read_text(), before[1])

    def test_missing_identity_prints_one_line_and_keeps_the_pinned_ad_hoc_helper(self):
        result = self.install()
        self.assertEqual(result.stderr.splitlines().count(FALLBACK), 1)
        signing = self.calls()
        self.assertEqual([call[:-1] for call in signing], [['--force', '-s', '-', '--identifier', 'outlook-intake-copy']])
        flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                 f'-DOUTLOOK_PROFILE={json.dumps(str(self.profile))}',
                 f'-DOUTLOOK_WORK_ROOT={json.dumps(str((self.repo / "out/outlook-intake").resolve()))}',
                 f'-DOUTLOOK_REPO={json.dumps(str(self.repo))}']
        source = (self.repo / 'tools/outlook-intake-copy.c').read_bytes()
        self.assertEqual(self.helper.with_suffix('.pin').read_text(),
                         hashlib.sha256(source + json.dumps([flags, '']).encode()).hexdigest(),
                         'the ad-hoc pin keeps its pre-identity format')
        before = self.helper.stat().st_mtime_ns
        result = self.install(args=('--rebuild-helper',))
        self.assertEqual(self.helper.stat().st_mtime_ns, before, 'an ad-hoc helper is never rebuilt needlessly')
        self.assertIn(FALLBACK, result.stderr)

    def test_configured_identity_overrides_the_default_name(self):
        self.install()
        values = json.loads(self.config.read_text())
        values['signing_identity'] = 'Fixture Signing Identity'
        self.config.write_text(json.dumps(values))
        result = self.install(signing_env=self.identity('Fixture Signing Identity'), args=('--rebuild-helper',))
        self.assertNotIn('re-grant', result.stdout)
        self.assertIn(['find-certificate', '-c', 'Fixture Signing Identity', '-Z'], self.calls('.security'))
        load_settings(self.home).verify_helper()
        result = self.install(check=False, signing_env=self.identity(), args=('--rebuild-helper',))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('keychain', result.stderr)

    def test_signing_failure_preserves_existing_helper_and_pin(self):
        self.install()
        before = self.helper.read_bytes(), self.helper.with_suffix('.pin').read_text()
        for env, message in (({'SIGN_FAIL': '1'}, 'keychain'),
                             ({'REQUIREMENT': 'designated => cdhash H"123"'}, 'designated requirement'),
                             ({'REQUIREMENT': ''}, 'designated requirement'),
                             ({'REQUIREMENT': f'designated => identifier "com.carr.outlook-intake-copy" and '
                                              f'certificate leaf = H"{IDENTITY_SHA1.lower()}" or cdhash H"123"'},
                              'designated requirement')):
            with self.subTest(env=env):
                result = self.install(check=False, signing_env=self.identity(**env), args=('--rebuild-helper',))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn('re-grant', result.stdout)
                self.assertEqual((self.helper.read_bytes(), self.helper.with_suffix('.pin').read_text()), before)

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

        source = self.repo / 'tools/outlook-intake-copy.c'
        source.write_text(source.read_text().replace('snapshot failed', 'fixture snapshot failed'))
        self.git(self.repo, 'add', 'tools/outlook-intake-copy.c')
        self.git(self.repo, 'commit', '--quiet', '-m', 'helper change')
        before = self.helper.read_bytes()
        refused = self.install(check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('--rebuild-helper', refused.stderr)
        self.assertEqual(self.helper.read_bytes(), before)
        self.install(args=('--rebuild-helper',))
        self.assertNotEqual(self.helper.read_bytes(), before)

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
