#!/usr/bin/env python3
"""Exercise the real installer and compare its decoder with the same local CI build.

Only synthetic homes and committed source copies enter this test. A different
host toolchain is valid when the source receipt and locked release tests pass.
"""
import hashlib
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
INSTALLER_FILES = ('ops/hxprobe-source-digest.py', 'bin/install-outlook-intake.sh', 'tools/outlook-intake-copy.c', 'lib/outlook_settings.py',
                   'ops/launchd/com.carr.outlook-intake.plist', 'ops/launchd/com.carr.outlook-refresh.plist')


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
        (self.repo / '.venv/bin').mkdir(parents=True)
        (self.repo / '.venv/bin/python').symlink_to(sys.executable)
        (self.repo / '.gitignore').write_text('.venv/\nout\ntarget/\n')
        self.git('init', '--quiet')
        self.commit('fixture')
        self.vendor = self.home / 'carr local/hxstore'
        self.helper = self.home / 'carr local/bin/outlook-intake-copy'
        write_settings(self.home, self.repo, vendor=str(self.vendor), copy_helper=str(self.helper),
                       profile=str(self.home / 'Profile'),
                       executable_path=f'{Path(shutil.which("cargo")).parent}:/usr/bin:/bin:/usr/sbin:/sbin')
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
        return subprocess.run(command, capture_output=True, text=True, check=check, env=env)

    def test_installed_decoder_equals_the_ci_build_and_rebuilds_only_on_source_change(self):
        self.install()
        self.assertEqual(sha256(self.decoder), self.ci_build)
        pin = (self.vendor / '.carr-pin').read_text()
        helper_before, decoder_before = self.helper.stat().st_mtime_ns, self.decoder.stat().st_mtime_ns

        self.install()
        self.assertEqual(self.decoder.stat().st_mtime_ns, decoder_before, 'unchanged source skips the build')

        with (self.repo / 'vendor/hxprobe/UPSTREAM.md').open('a') as notes:
            notes.write('\nFixture source change.\n')
        self.commit('source change')
        self.install()
        self.assertNotEqual((self.vendor / '.carr-pin').read_text(), pin, 'a source change rebuilds')
        self.assertEqual(sha256(self.decoder), self.ci_build)
        self.assertEqual(self.helper.stat().st_mtime_ns, helper_before, 'the Full Disk Access helper is not rebuilt')

    def test_a_failed_first_build_is_retried_once_the_receipt_is_fixed(self):
        receipt = self.repo / 'vendor/hxprobe/BUILD-SHA256'
        good = receipt.read_text()
        receipt.write_text('0' * 64 + '  target/release/hxprobe\n')
        self.commit('stale receipt')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('records source (none)', result.stderr)
        self.assertFalse(self.decoder.exists())
        self.assertFalse((self.vendor / '.carr-pin').exists())
        receipt.write_text(good)
        self.commit('fixed receipt')
        self.install()
        self.assertEqual(sha256(self.decoder), self.ci_build)
        self.assertTrue((self.vendor / '.carr-pin').read_text().startswith('source '))

    def test_a_stale_source_receipt_keeps_the_installed_decoder(self):
        self.install()
        pin = (self.vendor / '.carr-pin').read_text()
        (self.repo / 'vendor/hxprobe/BUILD-SHA256').write_text('0' * 64 + '  target/release/hxprobe\n')
        self.commit('stale receipt')
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('records source (none)', result.stderr)
        self.assertEqual(sha256(self.decoder), self.ci_build)
        self.assertEqual((self.vendor / '.carr-pin').read_text(), pin)


if __name__ == '__main__':
    unittest.main()
