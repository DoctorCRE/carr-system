#!/usr/bin/env python3
"""Exercise the quarantine runner through its command-line seam."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / 'ops' / 'ci-quarantine.py'


class QuarantineTests(unittest.TestCase):
    def test_quarantined_failure_still_executes_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'executed'
            manifest = root / 'quarantine.json'
            manifest.write_text(json.dumps({'version': 1, 'tests': [{
                'test': 'ops/example-selftest.py', 'loop': 'https://github.com/jbookout/carr-system/issues/123',
                'owner': 'jbookout', 'expires': '2099-01-01', 'reason': 'Same-tree failure followed by pass.'}]}))
            result = subprocess.run([sys.executable, str(RUNNER), 'run', '--test', 'ops/example-selftest.py',
                '--manifest', str(manifest), '--log', str(root / 'test.log'), '--', sys.executable, '-c',
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran'); raise SystemExit(1)"],
                cwd=REPO, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(marker.read_text(), 'ran')
            receipt = json.loads((root / 'test.log.result.json').read_text())
            self.assertEqual(receipt['status'], 'quarantined-failure')
            self.assertEqual(receipt['first_exit'], 1)
            self.assertIn('QUARANTINED', result.stdout)

    def invoke(self, root, command, entries=(), name='ops/example-selftest.py'):
        manifest = root / 'quarantine.json'
        manifest.write_text(json.dumps({'version': 1, 'tests': list(entries)}))
        return subprocess.run([sys.executable, str(RUNNER), 'run', '--test', name,
            '--manifest', str(manifest), '--log', str(root / 'test.log'), '--',
            sys.executable, '-c', command], cwd=REPO, capture_output=True, text=True, timeout=15)

    def test_expired_and_incomplete_quarantines_fail_even_if_test_passes(self):
        for entry in (
            {'test': 'ops/example-selftest.py', 'loop': 'https://github.com/jbookout/carr-system/issues/123',
             'owner': 'jbookout', 'expires': '2000-01-01', 'reason': 'Fixture'},
            {'test': 'ops/example-selftest.py', 'expires': '2099-01-01'},
        ):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as directory:
                result = self.invoke(Path(directory), 'pass', [entry])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('quarantine invalid', result.stderr)

    def test_same_tree_rerun_proposes_without_suppressing_first_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'first'
            result = self.invoke(root,
                f"from pathlib import Path; p=Path({str(marker)!r}); seen=p.exists(); p.write_text('ran'); raise SystemExit(0 if seen else 1)")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            receipt = json.loads((root / 'test.log.result.json').read_text())
            self.assertEqual(receipt['status'], 'flake-candidate')
            self.assertEqual(receipt['rerun_exit'], 0)
            self.assertTrue(receipt['tree'])
            self.assertTrue(receipt['fingerprint'])
            self.assertEqual(json.loads((root / 'quarantine.json').read_text())['tests'], [])

    def test_persistent_failure_and_skip_do_not_propose(self):
        for code in (1, 78, 124):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result = self.invoke(root, f'raise SystemExit({code})')
                self.assertEqual(result.returncode, code)
                receipt = json.loads((root / 'test.log.result.json').read_text())
                self.assertNotEqual(receipt['status'], 'flake-candidate')

    def test_source_changes_between_attempts_cannot_be_a_flake(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / 'repo'
            fixture.mkdir()
            (fixture / 'code.py').write_text('original')
            from git_env import fixture_env
            env = fixture_env()
            subprocess.run(['git', 'init', str(fixture)], env=env, capture_output=True, check=True)
            subprocess.run(['git', '-C', str(fixture), 'add', 'code.py'], env=env, check=True)
            subprocess.run(['git', '-C', str(fixture), '-c', 'user.name=Fixture', '-c',
                'user.email=fixture@example.invalid', 'commit', '-m', 'seed'], env=env, capture_output=True, check=True)
            manifest = root / 'manifest.json'
            manifest.write_text('{"version":1,"tests":[]}')
            result = subprocess.run([sys.executable, str(RUNNER), 'run', '--test', 'code.py',
                '--manifest', str(manifest), '--repo', str(fixture), '--log', str(root / 'test.log'), '--',
                sys.executable, '-c', "from pathlib import Path; p=Path('code.py'); seen=p.read_text()=='changed'; p.write_text('changed'); raise SystemExit(0 if seen else 1)"],
                cwd=fixture, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            receipt = json.loads((root / 'test.log.result.json').read_text())
            self.assertNotEqual(receipt['status'], 'flake-candidate')

    def test_quarantine_cannot_hide_timeouts_or_missing_configuration(self):
        for code in (78, 124):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                entry = {'test': 'ops/example-selftest.py', 'loop': 'https://github.com/jbookout/carr-system/issues/123',
                    'owner': 'qa-engineer', 'expires': '2099-01-01', 'reason': 'Fixture'}
                result = self.invoke(root, f'raise SystemExit({code})', [entry])
                self.assertEqual(result.returncode, code)
                self.assertNotIn('QUARANTINED', result.stdout)


if __name__ == '__main__':
    unittest.main()
