#!/usr/bin/env python3
"""Prove independent verb and suite additions merge and rebuild together."""
from pathlib import Path
import shutil
import json
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'


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
        for path in ('lib/deal_schedule.schema.json', 'mcp-server/package.json',
                     'mcp-server/run-tests.mjs', 'ops/scac-mutation-inventory.mjs',
                     'ops/git_env.py', 'tools/regen.py', '.gitignore'):
            shutil.copy(ROOT / path, self.repo / path)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'jbookout')
        self.git('config', 'user.email', 'jbookout@users.noreply.github.com')
        self.git('add', 'mcp-server/src', 'mcp-server/package.json', 'mcp-server/run-tests.mjs',
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
        target.write_text(f'''export default {{
  source: "{path}",
  createTools: () => ({{"wr622-{name}": {{
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
            self.git('add', path)
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
        self.assertEqual(self.git('ls-files', REGISTRY).stdout, '', 'generated output must not be tracked')
        self.regen()
        self.command('node', 'ops/scac-mutation-inventory.mjs')
        result = self.command('node', '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import {TOOLS} from './mcp-server/src/tools.js';
import {assertRegisteredOperation} from './mcp-server/src/mutation-registry.js';
import {SCAC_MUTATION_OPERATIONS as operations} from './mcp-server/src/scac-mutation-registry.current.generated.js';
for (const name of ['wr622-a', 'wr622-b']) {
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

    def test_failed_build_preserves_registry(self):
        self.regen()
        original = (self.repo / REGISTRY).read_bytes()
        path = self.add_verb('broken')
        (self.repo / path).write_text('invalid javascript !\n')
        self.assertNotEqual(self.regen(check=False).returncode, 0)
        self.assertEqual((self.repo / REGISTRY).read_bytes(), original)

    def test_rehearsal_contract_refresh_is_explicit_and_failure_preserves_inputs(self):
        self.regen()
        path = self.repo / 'rehearsal.sql'
        result = self.command('node', '--input-type=module', '-e',
            "import {SCAC_MUTATION_CURRENT_DB_GRANTS as grants} from './" + REGISTRY +
            "'; console.log(JSON.stringify(grants));")
        contract = json.loads(result.stdout)
        contract['review_note'] = "synthetic O'Brien"
        literal = json.dumps(contract).replace("'", "''")
        path.write_text("CREATE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb "
                        "LANGUAGE sql IMMUTABLE AS $$SELECT '" + literal + "'::jsonb$$;\n")
        self.command('python3', 'tools/regen.py', 'registry', '--grant-contract', str(path))
        inputs = self.repo / 'mcp-server/src/scac-mutation-registry.inputs.js'
        self.assertIn("synthetic O'Brien", inputs.read_text())
        before = inputs.read_bytes()
        path.write_text('SELECT arbitrary_database_state();')
        result = self.command('python3', 'tools/regen.py', 'registry', '--grant-contract', str(path), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(inputs.read_bytes(), before)

    def test_duplicate_registration_refuses_build(self):
        self.regen()
        path = self.add_verb('duplicate')
        target = self.repo / path
        target.write_text(target.read_text().replace('wr622-duplicate', 'today-triage'))
        result = self.regen(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate tool registration', result.stderr)


if __name__ == '__main__':
    unittest.main()
