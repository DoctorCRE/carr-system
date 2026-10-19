#!/usr/bin/env python3
"""Required package checks must execute tests through the local CI entrypoint."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('mcp-server', 'control-room', 'workspace', 'practice-plugin')


class UnitClassTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'ops/config').mkdir(parents=True)
        for name in ('ci.sh', 'ci-quarantine.py', 'git_env.py', 'ci-unit.py', 'node_test_summary.py'):
            source = ROOT / 'ops' / name
            if source.exists():
                shutil.copy2(source, self.root / 'ops' / name)
        (self.root / 'ops/config/ci-quarantine.json').write_text('{"version":1,"tests":[]}')
        (self.root / 'ops/config/ci-check-scope.json').write_text('{}')
        self.env = fixture_env()
        subprocess.run(['git', 'init', '-q', str(self.root)], env=self.env, check=True)

    def run_class(self):
        return subprocess.run(['bash', str(self.root / 'ops/ci.sh'), '--strict', '--only', 'unit'],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

    def package(self, name, body="test('executes', () => {});"):
        path = self.root / name
        (path / 'test').mkdir(parents=True)
        (path / 'package.json').write_text(json.dumps({'scripts': {'test': 'node --test test/*.test.mjs'}}))
        (path / 'test/suite.test.mjs').write_text("import test from 'node:test';\n" + body + '\n')

    def test_empty_class_is_red(self):
        result = self.run_class()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL', result.stdout)
        self.assertIn('mcp-server', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
