#!/usr/bin/env python3
"""Exercise the real installer and compare its decoder with the same local CI build.

Only synthetic homes and committed source copies enter this test. A different
host toolchain is valid when the locked release tests pass.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from git_env import fixture_env
from outlook_test_support import write_settings

CRATE = ROOT / 'vendor/hxprobe'
SOURCES = ('vendor/hxprobe', 'spec/outlook/hxstore-atlas.json', 'ops/hxstore-atlas.py')
INSTALLER_FILES = ('ops/hxprobe-source-digest.py', 'ops/config-as-code.py', 'ops/git_env.py', 'ops/config/services.json', 'bin/install-outlook-intake.sh', 'bin/run-scheduled.sh', 'tools/outlook-intake-copy.c',
                   'ops/launchd/com.carr.outlook-intake.plist', 'tools/credential_env.py')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('cargo'), 'needs macOS and Cargo')
class InstalledDecoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--release', '--locked', '--quiet'], cwd=CRATE, check=True)
        cls.ci_build = sha256(CRATE / 'target/release/hxprobe')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.repo, self.home = root / 'runtime checkout', root / 'home'
        for relative in INSTALLER_FILES + SOURCES[1:]:
            (self.repo / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, self.repo / relative)
        shutil.copytree(CRATE, self.repo / SOURCES[0], ignore=shutil.ignore_patterns('target'))
        shutil.copytree(ROOT / 'lib', self.repo / 'lib', ignore=shutil.ignore_patterns('__pycache__'))
        (self.repo / '.venv/bin').mkdir(parents=True)
        (self.repo / '.venv/bin/python').symlink_to(sys.executable)
        codesign = self.repo / '.venv/bin/codesign'
        codesign.write_text(f'#!{sys.executable}\n')
        codesign.chmod(0o700)
        security = self.repo / '.venv/bin/security'
        security.write_text(f'#!{sys.executable}\nprint("     0 valid identities found")\n')
        security.chmod(0o700)
        launchctl = self.repo / '.venv/bin/launchctl'
        launchctl.write_text(f'#!{sys.executable}\nimport sys\n'
                             'if len(sys.argv) != 3 or sys.argv[1] != "print" or not sys.argv[2].endswith("/com.carr.outlook-refresh"):\n'
                             '    sys.exit(1)\n'
                             'sys.stderr.write(\'Could not find service "com.carr.outlook-refresh"\\n\')\n'
                             'sys.exit(113)\n')
        launchctl.chmod(0o700)
        (self.repo / '.gitignore').write_text('.venv/\nout\ntarget/\n')
        self.git('init', '--quiet')
        self.commit('fixture')
        self.vendor = self.home / 'carr local/hxstore'
        self.helper = self.home / 'carr local/bin/outlook-intake-copy'
        write_settings(self.home, self.repo, vendor=str(self.vendor), copy_helper=str(self.helper),
                       profile=str(self.home / 'Profile'),
                       executable_path=f'{self.repo}/.venv/bin:{Path(shutil.which("cargo")).parent}:/usr/bin:/bin:/usr/sbin:/sbin')
        self.decoder = self.vendor / 'hxprobe/target/release/hxprobe'

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=fixture_env(), check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git('add', '-A')
        self.git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.test', 'commit', '--quiet', '-m', message)

    def install(self, check=True):
        env = fixture_env()
        env['CARGO_HOME'] = os.environ.get('CARGO_HOME', str(Path.home() / '.cargo'))
        env['RUSTUP_HOME'] = os.environ.get('RUSTUP_HOME', str(Path.home() / '.rustup'))
        command = ['/bin/zsh', '-f', '-c', 'export HOME="$1"; exec /bin/zsh -f "$2"', 'installer-test',
                   str(self.home), str(self.repo / 'bin/install-outlook-intake.sh')]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if check and result.returncode:
            self.fail(f'installer failed ({result.returncode}):\n{result.stderr}\n{result.stdout}')
        return result

    def receipt(self):
        return json.loads((self.vendor / '.carr-build.json').read_text())

    def test_installed_decoder_equals_the_ci_build_and_rebuilds_only_on_source_change(self):
        (self.vendor).mkdir(parents=True)
        (self.vendor / '.carr-pin').write_text('source retired-installer-pin\n')
        self.install()
        self.assertEqual(sha256(self.decoder), self.ci_build)
        self.assertFalse((self.vendor / '.carr-pin').exists(), 'the retired pin is removed')
        receipt = self.receipt()
        helper_before, decoder_before = self.helper.stat().st_mtime_ns, self.decoder.stat().st_mtime_ns

        self.install()
        self.assertEqual(self.decoder.stat().st_mtime_ns, decoder_before, 'unchanged source skips the build')

        with (self.repo / 'vendor/hxprobe/tests/parser_regressions.rs').open('a') as tests:
            tests.write('\n// Fixture source change.\n')
        self.commit('source change')
        self.install()
        self.assertNotEqual(self.receipt()['source_digest'], receipt['source_digest'], 'a source change rebuilds')
        self.assertEqual(sha256(self.decoder), self.ci_build)
        self.assertEqual(self.helper.stat().st_mtime_ns, helper_before, 'the Full Disk Access helper is not rebuilt')

    def test_an_altered_decoder_is_replaced_by_the_ci_build(self):
        self.install()
        self.decoder.write_bytes(b'altered decoder')
        result = self.install()
        self.assertIn('does not match .carr-build.json', result.stderr)
        self.assertEqual(sha256(self.decoder), self.ci_build)
        self.assertEqual(self.receipt()['binary_sha256'], self.ci_build)


    def test_saved_intake_state_reconciles_through_launchd_before_decoder_replacement(self):
        self.install()
        with (self.repo / 'vendor/hxprobe/tests/parser_regressions.rs').open('a') as tests:
            tests.write('\n// Fixture source change.\n')
        self.commit('source change')
        state = self.repo / 'out/outlook-intake/state.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({'mail_unresolved': {}}))
        built = self.receipt()
        launchctl = self.repo / '.venv/bin/launchctl'
        launchctl.write_text(f'''#!{sys.executable}
import sys
from pathlib import Path
repo = Path({str(self.repo)!r})
if sys.argv[1:] == ['kickstart', 'gui/{os.getuid()}/com.carr.outlook-intake']:
    (repo / 'out/kickstarted').touch()
    sys.exit(0 if (repo / 'out/loaded').exists() else 113)
if sys.argv[1] == 'print' and sys.argv[2].endswith('/com.carr.outlook-refresh'):
    sys.stderr.write('Could not find service "com.carr.outlook-refresh"\\n')
    sys.exit(113)
sys.exit(1)
''')
        sleep = self.repo / '.venv/bin/sleep'
        sleep.write_text(f'''#!{sys.executable}
import json
import sys
from pathlib import Path
repo = Path({str(self.repo)!r})
assert sys.argv[1:] == ['2']
path = repo / 'out/waits'
waits = int(path.read_text()) + 1 if path.exists() else 1
path.write_text(str(waits))
if waits == 2 and (repo / 'out/complete').exists():
    state = repo / 'out/outlook-intake/state.json'
    state.write_text(json.dumps({{'mail_unresolved': {{}}, 'mail_ids': {{}}}}))
''')
        sleep.chmod(0o700)
        self.helper.write_text(f'#!{sys.executable}\nimport sys\n'
                               f'if sys.argv[1:] == ["--scope"]:\n'
                               f'    print({str(self.home / "Profile")!r})\n'
                               f'    print({str(self.repo / "out/outlook-intake")!r})\n'
                               f'    print({str(self.repo)!r})\n'
                               'else:\n    sys.exit(23)\n')
        self.helper.chmod(0o700)
        (self.repo / 'lib/outlook_intake.py').write_text('raise SystemExit(24)\n')
        helper_before = self.helper.read_bytes()

        missing = self.install(check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('job is not loaded', missing.stderr)
        self.assertIn('out/run-scheduled.log', missing.stderr)
        self.assertFalse((self.repo / 'out/waits').exists())
        self.assertEqual(self.receipt(), built)

        (self.repo / 'out/loaded').touch()
        failed = self.install(check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('out/run-scheduled.log', failed.stderr)
        self.assertIn('Rerun the installer', failed.stderr)
        self.assertEqual((self.repo / 'out/waits').read_text(), '60')
        self.assertEqual(self.receipt(), built)
        self.assertNotIn('mail_ids', json.loads(state.read_text()))
        self.assertEqual(self.helper.read_bytes(), helper_before)

        (self.repo / 'out/waits').unlink()
        (self.repo / 'out/complete').touch()
        self.install()
        self.assertTrue((self.repo / 'out/kickstarted').exists())
        self.assertEqual((self.repo / 'out/waits').read_text(), '2')
        self.assertIn('mail_ids', json.loads(state.read_text()))
        self.assertNotEqual(self.receipt()['source_digest'], built['source_digest'])
        (self.repo / 'out/kickstarted').unlink()
        self.install()
        self.assertFalse((self.repo / 'out/kickstarted').exists(), 'reconciled state skips launchd')


if __name__ == '__main__':
    unittest.main()
