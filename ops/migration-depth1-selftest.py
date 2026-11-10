#!/usr/bin/env python3
"""Exercise checkout closure and refusal of a reintroduced historical read."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from git_env import fixture_env

spec = importlib.util.spec_from_file_location('depth1', Path(__file__).with_name('migration-depth1.py'))
depth1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(depth1)


class DepthOneTests(unittest.TestCase):
    def test_history_read_fails_and_checkout_local_inputs_pass(self):
        env = fixture_env()
        env['GIT_NO_LAZY_FETCH'] = '1'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            source.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(source), *args], env=env, text=True).strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            (source / 'db').mkdir()
            (source / 'db/old.sql').write_text('select 1;')
            git('add', 'db/old.sql')
            git('commit', '-qm', 'old input')
            pin = git('rev-parse', 'HEAD')
            (source / 'db/migration-baseline.json').write_text(json.dumps({'source_revision': pin}))
            for suite in depth1.SUITES:
                target = source / suite
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("import test from 'node:test';\n" +
                                  "test('input closure', () => {});\n")
            git('add', 'db/migration-baseline.json', 'mcp-server/test')
            git('commit', '-qm', 'checkout-local fixtures')
            clone = Path(temp) / 'clone'
            subprocess.run(['git', 'clone', '-q', '--depth', '1', source.as_uri(), str(clone)],
                           env=env, check=True)
            with contextlib.redirect_stdout(io.StringIO()):
                receipt = depth1.run_acceptance(clone, env, reconstruction=False)
                self.assertEqual(receipt['counts']['pass'], 8)
                for mutation in ('history', 'skip'):
                    target = clone / depth1.SUITES[0]
                    target.write_text("import test from 'node:test';\n" +
                        ("import {execFileSync} from 'node:child_process';\n" +
                         "test('history read', () => execFileSync('git', ['show', '" + pin + ":db/old.sql']));\n"
                         if mutation == 'history' else "test.skip('silent skip', () => {});\n"))
                    with self.assertRaisesRegex(ValueError, 'failed or skipped'):
                        depth1.run_acceptance(clone, env, reconstruction=False)
            with self.assertRaisesRegex(ValueError, 'shallow'):
                depth1.assert_checkout(source, env)
            (clone / '.git/objects/info/alternates').write_text(str(source / '.git/objects'))
            with self.assertRaisesRegex(ValueError, 'alternates'):
                depth1.assert_checkout(clone, env)


if __name__ == '__main__':
    unittest.main()
