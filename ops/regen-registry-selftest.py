#!/usr/bin/env python3
"""Prove independent verb and suite additions merge and rebuild together."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'
CONTRACTS = 'mcp-server/contracts/operations'


class RegistryMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='registry-merge-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'seed'
        self.repo.mkdir()
        self.environment = fixture_env()
        for directory in ('ops', 'tools', 'lib', 'mcp-server/test'):
            (self.repo / directory).mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / 'mcp-server/src', self.repo / 'mcp-server/src')
        shutil.copytree(ROOT / 'mcp-server/contracts', self.repo / 'mcp-server/contracts')
        for path in ('lib/deal_schedule.schema.json', 'mcp-server/package.json', 'mcp-server/wrangler.toml',
                     'mcp-server/run-tests.mjs', 'mcp-server/suite-discovery.mjs',
                     'ops/scac-mutation-inventory.mjs',
                     'ops/git_env.py', 'tools/regen.py', 'tools/assemble_registry.py', '.gitignore'):
            shutil.copy(ROOT / path, self.repo / path)
        (self.repo / 'mcp-server/suite-inventory.json').write_text('{"expected": 1}\n')
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'jbookout')
        self.git('config', 'user.email', 'jbookout@users.noreply.github.com')
        self.git('add', 'mcp-server/src', 'mcp-server/contracts', 'mcp-server/package.json', 'mcp-server/wrangler.toml', 'mcp-server/run-tests.mjs',
                 'mcp-server/suite-discovery.mjs', 'mcp-server/suite-inventory.json',
                 'ops', 'tools', 'lib/deal_schedule.schema.json', '.gitignore')
        self.commit('main plus WR622 implementation')
        clone = Path(self.temp.name) / 'clone'
        self.git('clone', '--no-hardlinks', str(self.repo), str(clone))
        self.repo = clone
        self.git('config', 'user.name', 'jbookout')
        self.git('config', 'user.email', 'jbookout@users.noreply.github.com')
        (self.repo / 'mcp-server/node_modules').symlink_to(ROOT / 'mcp-server/node_modules')

    def command(self, *args, check=True):
        result = subprocess.run(args, cwd=self.repo, env=self.environment, capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *args, check=True):
        return self.command('git', '-c', 'core.hooksPath=/dev/null', *args, check=check)

    def commit(self, message):
        path = self.repo / 'message.txt'
        path.write_text(message)
        self.git('commit', '-F', str(path))

    def regen(self, check=True):
        return self.command('python3', 'tools/regen.py', 'registry', check=check)

    def add_verb(self, name):
        path = f'mcp-server/src/tool-families/fixture-{name}.js'
        target = self.repo / path
        target.parent.mkdir(exist_ok=True)
        operation = {'a': 'aaa-wr622-a', 'b': 'zzz-wr622-b'}.get(name, f'wr622-{name}')
        target.write_text(f'''export default {{
  source: "{path}",
  createTools: () => ({{"{operation}": {{
    inputSchema: {{type: "object", properties: {{}}}}, write: false,
    handler: async () => ({{ok: true}}),
  }}}}),
}};\n''')
        return path

    def test_independent_verbs_and_suites_merge_without_conflict(self):
        for name in ('a', 'b'):
            self.git('checkout', '-b', f'verb-{name}', 'main')
            path = self.add_verb(name)
            self.regen()
            self.git('add', path, CONTRACTS)
            self.commit(f'add verb {name}')
        for name in ('a', 'b'):
            self.git('checkout', '-b', f'suite-{name}', 'main')
            path = f'mcp-server/test/fixture-{name}.test.mjs'
            (self.repo / 'mcp-server/test').mkdir(exist_ok=True)
            (self.repo / path).write_text(f'import test from "node:test";\ntest("suite {name}", () => {{}});\n')
            self.git('add', path)
            self.commit(f'add suite {name}')
        self.git('checkout', 'main')
        for branch in ('verb-a', 'verb-b', 'suite-a', 'suite-b'):
            result = self.git('merge', '--no-ff', '--no-edit', branch)
            print(f'$ git merge --no-ff --no-edit {branch}\n{result.stdout.strip()}', flush=True)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout, '')
        self.assertEqual(self.git('ls-files', REGISTRY).stdout.strip(), REGISTRY)
        self.regen()
        self.command('node', 'ops/scac-mutation-inventory.mjs')
        result = self.command('node', '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import {TOOLS} from './mcp-server/src/tools.js';
import {assertRegisteredOperation} from './mcp-server/src/mutation-registry.js';
import {SCAC_MUTATION_OPERATIONS as operations} from './mcp-server/src/scac-mutation-registry.current.generated.js';
for (const name of ['aaa-wr622-a', 'zzz-wr622-b']) {
  assert.ok(TOOLS[name]); assert.ok(operations[name]);
  await assertRegisteredOperation(name, TOOLS[name], {});
}
console.log('both verbs assembled and present in the sole generated registry');
''')
        print(result.stdout.strip(), flush=True)
        result = self.command('node', 'mcp-server/run-tests.mjs')
        self.assertIn('suite a', result.stdout)
        self.assertIn('suite b', result.stdout)
        print('both newly discovered suites executed; git status clean = ' +
              str(not self.git('status', '--porcelain', '--untracked-files=no').stdout), flush=True)

    def test_fresh_clone_imports_without_generating_anything(self):
        result = self.command('node', '-e', 'import("./mcp-server/src/tools.js")', check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git('ls-files', REGISTRY).stdout.strip(), REGISTRY)
        self.assertFalse((self.repo / 'mcp-server/src/tool-families.generated.js').exists())

    def test_failed_build_preserves_registry(self):
        self.regen()
        original = (self.repo / REGISTRY).read_bytes()
        path = self.add_verb('broken')
        (self.repo / path).write_text('invalid javascript !\n')
        self.assertNotEqual(self.regen(check=False).returncode, 0)
        self.assertEqual((self.repo / REGISTRY).read_bytes(), original)

    def test_ci_check_is_read_only_and_refuses_stale_registry(self):
        target = self.repo / CONTRACTS / 'today-triage.json'
        target.write_text(target.read_text().replace('mcp-tool:today-triage', 'mcp-tool:stale-fixture'))
        before = target.read_bytes()
        result = self.command('node', 'ops/scac-mutation-inventory.mjs', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), before)

    def test_duplicate_registration_refuses_build(self):
        self.regen()
        path = self.add_verb('duplicate')
        target = self.repo / path
        target.write_text(target.read_text().replace('wr622-duplicate', 'today-triage'))
        result = self.regen(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate tool registration', result.stderr)




    def test_assembly_is_atomic_and_refuses_stale_inputs_and_alias_drift(self):
        self.command('python3', 'tools/assemble_registry.py')
        pointer = self.repo / 'mcp-server/.generated/current'
        original = pointer.resolve()
        row = self.repo / CONTRACTS / 'today-triage.json'
        before = row.read_bytes()
        row.write_text(row.read_text().replace('"write": false', '"write": true'))
        self.assertNotEqual(self.command('python3', 'tools/assemble_registry.py', check=False).returncode, 0)
        self.assertEqual(pointer.resolve(), original)
        row.write_bytes(before)
        config = self.repo / 'mcp-server/wrangler.toml'
        config.write_text(config.read_text().replace('"#verb-families"', '"#broken-alias"'))
        self.assertNotEqual(self.command('python3', 'tools/assemble_registry.py', check=False).returncode, 0)
        self.assertEqual(pointer.resolve(), original)

    def test_missing_extra_and_malformed_rows_refuse_read_only_check(self):
        row = self.repo / CONTRACTS / 'today-triage.json'
        before = row.read_bytes()
        row.unlink()
        self.assertNotEqual(self.command('node', 'ops/scac-mutation-inventory.mjs', check=False).returncode, 0)
        self.assertFalse(row.exists())
        row.write_bytes(before)
        extra = self.repo / CONTRACTS / 'extra.json'
        extra.write_text(before.decode().replace('mcp-tool:today-triage', 'mcp-tool:extra'))
        self.assertNotEqual(self.command('node', 'ops/scac-mutation-inventory.mjs', check=False).returncode, 0)
        extra.unlink()
        row.write_text('{}')
        self.assertNotEqual(self.command('node', 'ops/scac-mutation-inventory.mjs', check=False).returncode, 0)
        self.assertEqual(row.read_text(), '{}')

    def test_seal_drift_is_not_regenerated(self):
        target = self.repo / REGISTRY
        target.write_text(target.read_text().replace('SCAC_MUTATION_DB_METADATA_AUTHORITY = true', 'SCAC_MUTATION_DB_METADATA_AUTHORITY = false'))
        before = target.read_bytes()
        self.assertNotEqual(self.regen(check=False).returncode, 0)
        self.assertEqual(target.read_bytes(), before)

if __name__ == '__main__':
    unittest.main()
