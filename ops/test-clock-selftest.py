#!/usr/bin/env python3
"""Prove the MCP test clock gate rejects ambient clocks and accepts fixed fixtures."""
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import sys
from git_env import scrubbed_env
import unittest

spec = importlib.util.spec_from_file_location('clock_check', Path(__file__).with_name('test-clock-check.py'))
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


class ClockGate(unittest.TestCase):
    def test_clock_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / 'sample.test.mjs'
            for source in ['Date.now()', 'new Date()', 'new Date ( /* clock */ )',
                           'Date /* clock */ . now ()', '''`https://example.test/${Date.now()}`''', '''`It's ${Date.now()} o'clock`''',
                           r'const s = /https?:\/\//; Date.now();',
                           '`nested ${`inner ${new Date()}`} tail`',
                           r"const ok = true && /https?:\/\//.test('x'); Date.now();",
                           r"if (true) /https?:\/\//.test('x'); Date.now();"]:
                fixture.write_text(source)
                self.assertTrue(clock.violations(root), source)
            for source in ['new Date("2026-10-27T15:00:00Z")', 'new Date(NOW)',
                           '// Date.now()\n/* new Date() */', '"new Date()"', 'performance.now()', '/new Date()/']:
                fixture.write_text(source)
                self.assertEqual(clock.violations(root), [], source)
            fixture.rename(root / 'shared-oauth-worker.test.mjs')
            (root / 'shared-oauth-worker.test.mjs').write_text('Date.now()')
            self.assertEqual(clock.violations(root), [])

    def test_main_and_changed_files(self):
        checker = Path(__file__).with_name('test-clock-check.py').resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(['git', *args], cwd=root, env=scrubbed_env(), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            git('init', '-q')
            git('config', 'user.name', 'Synthetic')
            git('config', 'user.email', 'synthetic@example.test')
            fixture = root / 'mcp-server/test/sample.test.mjs'
            fixture.parent.mkdir(parents=True)
            fixture.write_text('new Date("2026-10-27T15:00:00Z")')
            git('add', str(fixture.relative_to(root)))
            git('commit', '-qm', 'Synthetic main')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            def check():
                return subprocess.run([sys.executable, str(checker)], cwd=root,
                                      env=scrubbed_env(), capture_output=True, text=True)
            self.assertEqual(check().returncode, 0)
            fixture.write_text('Date.now()')
            result = check()
            self.assertEqual(result.returncode, 1)
            self.assertIn('sample.test.mjs:1:', result.stdout)
            fixture.write_text('new Date(0)')
            added = fixture.with_name('added.test.mjs')
            added.write_text('new Date()')
            self.assertEqual(check().returncode, 1)
            added.unlink()
            self.assertEqual(check().returncode, 0)


if __name__ == '__main__':
    unittest.main()
