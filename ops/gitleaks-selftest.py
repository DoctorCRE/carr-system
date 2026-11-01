#!/usr/bin/env python3
"""Exercise real Gitleaks with runtime-only synthetic credentials in isolated Git repos."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from git_env import fixture_env
import gitleaks

ROOT = pathlib.Path(__file__).resolve().parents[1]


class DetectionTest(unittest.TestCase):
    def test_missing_or_tampered_binary_fails_closed(self):
        verified = gitleaks.binary()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(gitleaks.Path, 'home', return_value=pathlib.Path(directory)):
                with self.assertRaisesRegex(ValueError, 'Install: python3 ops/gitleaks.py --install'):
                    gitleaks.binary()
            with patch.object(gitleaks.Path, 'read_bytes', autospec=True) as read:
                read.return_value = b'tampered'
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    gitleaks.binary()

    def test_staged_range_and_deleted_history_are_refused_without_values(self):
        values = ['AKIA' + 'Z' * 16, 'AIza' + 'z' * 35, 'GOCSPX-' + 'z' * 28,
                  'ghp_' + 'z' * 36, 'xoxb-' + 'z' * 20, 'sk-ant-' + 'z' * 24,
                  'sk-proj-' + 'z' * 40, 'postgres://fixture:' + 'z' * 10 + '@invalid/db',
                  '-----' + 'BEGIN PGP PRIVATE KEY BLOCK-----']
        for value in values:
            with self.subTest(shape=value[:8]):
                self.check_shape(value)

    def check_shape(self, value):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'ops/config').mkdir(parents=True)
            for name in ('ci-secret-scan.py', 'pii_guard.py', 'gitleaks.py'):
                shutil.copy(ROOT / 'ops' / name, root / 'ops' / name)
            shutil.copy(ROOT / '.gitleaks.toml', root)
            shutil.copy(ROOT / 'ops/config/public-source-identities.v1.json', root / 'ops/config')
            env = fixture_env()
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, env=env, check=True, capture_output=True)
            def scan(*args):
                return subprocess.run([sys.executable, 'ops/ci-secret-scan.py', *args],
                                      cwd=root, env=env, capture_output=True, text=True)
            git('init', '-b', 'fixture')
            git('config', 'user.name', 'Synthetic Tester')
            git('config', 'user.email', 'test@example.invalid')
            (root / 'fixture.txt').write_text('clean\n')
            git('add', 'fixture.txt')
            git('commit', '--no-verify', '-m', 'baseline')
            base = git('rev-parse', 'HEAD').stdout.decode().strip()
            self.assertEqual(scan('--staged').returncode, 0)
            (root / 'fixture.txt').write_text(value + '\n')
            git('add', 'fixture.txt')
            (root / 'fixture.txt').write_text('clean working copy\n')
            result = scan('--staged')
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn(value, result.stdout + result.stderr)
            git('commit', '--no-verify', '-m', 'synthetic staged value')
            (root / 'fixture.txt').write_text('clean\n')
            git('add', 'fixture.txt')
            git('commit', '--no-verify', '-m', 'remove synthetic value')
            self.assertEqual(scan('--staged').returncode, 0)
            for args in (('--range', base + '..HEAD'), ('--history',)):
                result = scan(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn(value, result.stdout + result.stderr)



if __name__ == '__main__':
    unittest.main()
