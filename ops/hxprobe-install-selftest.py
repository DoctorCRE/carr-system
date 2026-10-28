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
INSTALLER_FILES = ('ops/hxprobe-source-digest.py', 'bin/install-outlook-intake.sh', 'tools/outlook-intake-copy.c', 'lib/outlook_settings.py', 'lib/outlook_helper.py',
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
        codesign = self.repo / '.venv/bin/codesign'
        codesign.write_text(f'#!{sys.executable}\n')
        codesign.chmod(0o700)
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
        return subprocess.run(command, capture_output=True, text=True, check=check, env=env)

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


if __name__ == '__main__':
    unittest.main()
