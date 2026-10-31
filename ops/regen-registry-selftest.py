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
        source = self.repo / 'mcp-server/src/tools.js'
        text = source.read_text()
        for name in ('a', 'b'):
            path = f'mcp-server/src/tool-families/fixture-{name}.js'
            (self.repo / path).write_text(f'export default {{source: "{path}", createTools: () => ({{}})}};\n')
            text = f'import fixture_{name} from "./tool-families/fixture-{name}.js";\n' + text
        text = text.replace('const toolFamilies = [', 'const toolFamilies = [fixture_a, fixture_b, ')
        source.write_text(text)
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
        if name not in ('a', 'b'):
            source = self.repo / 'mcp-server/src/tools.js'
            text = f'import fixture_{name} from "./tool-families/fixture-{name}.js";\n' + source.read_text()
            source.write_text(text.replace('const toolFamilies = [', f'const toolFamilies = [fixture_{name}, '))
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
            self.git('add', path, REGISTRY)
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
        inputs = self.repo / REGISTRY
        self.assertIn("synthetic O'Brien", inputs.read_text())
        before = inputs.read_bytes()
        path.write_text('SELECT arbitrary_database_state();')
        result = self.command('python3', 'tools/regen.py', 'registry', '--grant-contract', str(path), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(inputs.read_bytes(), before)

    def test_ci_check_is_read_only_and_refuses_stale_registry(self):
        target = self.repo / REGISTRY
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




class RegistryConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='regen-registry-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.environment = fixture_env()
        (self.repo / 'ops').mkdir()
        (self.repo / 'tools').mkdir()
        (self.repo / 'lib').mkdir()
        shutil.copy(ROOT / 'lib/deal_schedule.schema.json', self.repo / 'lib')
        shutil.copytree(ROOT / 'mcp-server/src', self.repo / 'mcp-server/src')
        shutil.copy(ROOT / 'mcp-server/package.json', self.repo / 'mcp-server/package.json')
        (self.repo / 'mcp-server/node_modules').symlink_to(ROOT / 'mcp-server/node_modules')
        shutil.copy(ROOT / 'ops/scac-mutation-inventory.mjs', self.repo / 'ops')
        shutil.copy(ROOT / 'ops/git_env.py', self.repo / 'ops')
        shutil.copy(ROOT / 'run.sh', self.repo)
        if (ROOT / 'tools/regen.py').exists():
            shutil.copy(ROOT / 'tools/regen.py', self.repo / 'tools')
        for name in ('a', 'b'):
            (self.repo / f'mcp-server/src/fixture-{name}.js').write_text('export default {};\n')
        source = self.repo / 'mcp-server/src/tools.js'
        source.write_text(source.read_text().replace('Object.freeze(TOOLS);',
            'Object.assign(TOOLS, (await import("./fixture-a.js")).default, '
            '(await import("./fixture-b.js")).default);\nObject.freeze(TOOLS);'))
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', 'mcp-server/src', 'mcp-server/package.json', 'ops', 'tools', 'run.sh')
        self.commit('base')
        for name in ('a', 'b'):
            self.git('checkout', '-b', name, 'main')
            (self.repo / f'mcp-server/src/fixture-{name}.js').write_text(
                f'export default {{"wr491-{name}": {{registrySource: "mcp-server/src/fixture-{name}.js", '
                'inputSchema: {type: "object"}, write: false}};\n')
            self.command('node', 'ops/scac-mutation-inventory.mjs', '--write-current')
            self.git('add', f'mcp-server/src/fixture-{name}.js', REGISTRY)
            self.commit(name)
        self.git('checkout', 'a')
        result = self.git('merge', '--no-edit', 'b', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(REGISTRY, self.git('diff', '--name-only', '--diff-filter=U').stdout)
        self.conflicted = (self.repo / REGISTRY).read_bytes()

    def command(self, *args, check=True, input=None):
        result = subprocess.run(args, cwd=self.repo, env=self.environment, capture_output=True, text=True, input=input)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *args, check=True):
        return self.command('git', '-c', 'core.hooksPath=/dev/null', *args, check=check)

    def commit(self, message):
        message_file = self.repo / 'message.txt'
        message_file.write_text(message)
        self.git('commit', '-F', str(message_file))

    def regen(self):
        return self.command('zsh', 'run.sh', 'regen', 'registry', check=False)

    def test_merge_regenerates_both_verbs_and_preserves_sealed_metadata(self):
        ours = self.git('show', f':2:{REGISTRY}').stdout
        result = self.regen()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = (self.repo / REGISTRY).read_text()
        self.assertIn('"wr491-a"', output)
        self.assertIn('"wr491-b"', output)
        start = 'export const SCAC_MUTATION_OPERATIONS'
        end = 'export const SCAC_MUTATION_CURRENT_DB_GRANTS'
        self.assertEqual(output.split(start)[0], ours.split(start)[0])
        self.assertEqual(output.split(end)[1], ours.split(end)[1])
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout, '')
        self.command('node', 'ops/scac-mutation-inventory.mjs')
        (self.repo / 'mcp-server/src/fixture-b.js').write_text('export default {};\n')
        self.assertNotEqual(self.command('node', 'ops/scac-mutation-inventory.mjs', check=False).returncode, 0)

    def test_failed_generator_restores_conflict_and_index(self):
        (self.repo / 'mcp-server/src/fixture-b.js').write_text('invalid javascript !\n')
        result = self.regen()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SyntaxError', result.stderr)
        self.assertEqual((self.repo / REGISTRY).read_bytes(), self.conflicted)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout.strip(), REGISTRY)

    def test_sealed_metadata_conflict_is_refused_unchanged(self):
        theirs = self.git('show', f':3:{REGISTRY}').stdout
        changed = theirs.replace('SCAC_MUTATION_DB_METADATA_AUTHORITY = true',
                                 'SCAC_MUTATION_DB_METADATA_AUTHORITY = false')
        blob = self.command('git', 'hash-object', '-w', '--stdin', input=changed).stdout.strip()
        self.command('git', 'update-index', '--index-info', input=f'100644 {blob} 3\t{REGISTRY}\n')
        before = self.git('ls-files', '--stage').stdout
        result = self.regen()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('sealed metadata or grants', result.stderr)
        self.assertEqual((self.repo / REGISTRY).read_bytes(), self.conflicted)
        self.assertEqual(self.git('ls-files', '--stage').stdout, before)

    def test_source_conflict_is_refused_unchanged(self):
        source = 'mcp-server/src/fixture-b.js'
        blob = self.git('rev-parse', f'HEAD:{source}').stdout.strip()
        entries = f'0 {"0" * 40}\t{source}\n'
        entries += ''.join(f'100644 {blob} {stage}\t{source}\n' for stage in (1, 2, 3))
        self.command('git', 'update-index', '--index-info', input=entries)
        before = self.git('ls-files', '--stage').stdout
        result = self.regen()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('resolve source conflicts', result.stderr)
        self.assertEqual((self.repo / REGISTRY).read_bytes(), self.conflicted)
        self.assertEqual(self.git('ls-files', '--stage').stdout, before)


    def test_manual_metadata_edit_is_preserved_on_refusal(self):
        edited = self.conflicted.replace(b'SCAC_MUTATION_DB_METADATA_AUTHORITY = true',
                                         b'SCAC_MUTATION_DB_METADATA_AUTHORITY = false')
        (self.repo / REGISTRY).write_bytes(edited)
        before = self.git('ls-files', '--stage').stdout
        result = self.regen()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('edits outside operations', result.stderr)
        self.assertEqual((self.repo / REGISTRY).read_bytes(), edited)
        self.assertEqual(self.git('ls-files', '--stage').stdout, before)

    def test_inherited_index_cannot_redirect_regeneration(self):
        other_index = self.repo / 'other-index'
        shutil.copy(self.repo / '.git/index', other_index)
        before = other_index.read_bytes()
        self.environment['GIT_INDEX_FILE'] = str(other_index)
        result = self.regen()
        del self.environment['GIT_INDEX_FILE']
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(other_index.read_bytes(), before)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout, '')

if __name__ == '__main__':
    unittest.main()
