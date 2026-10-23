"""Two source branches merge through regen without hand-editing projections."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import regen
from git_env import clone_index_fixture, fixture_env


REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'
POSTGRES = '--postgres' in sys.argv
if POSTGRES:
    sys.argv.remove('--postgres')


class MergeFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = fixture_env()
        for directory in ['tools', 'ops', 'mcp-server/src', 'verbs', 'migrations', 'db']:
            (self.root / directory).mkdir(parents=True)
        for name in ['ops/scac-mutation-inventory.mjs', 'tools/generated-merge-driver.py', '.gitattributes']:
            if (regen.ROOT / name).exists():
                shutil.copyfile(regen.ROOT / name, self.root / name)
        self.write('mcp-server/package.json', '{"type":"module"}')
        self.write(REGISTRY, 'export const SCAC_MUTATION_REGISTRY_VERSION = "fixture";\n'
                   'export const SCAC_MUTATION_OPERATIONS = Object.freeze({\n});\n'
                   'export const GRANTS = "preserved";\n')
        self.write('mcp-server/src/tools.js', '''import { readdirSync } from 'node:fs';
export const TOOLS = Object.fromEntries(readdirSync('verbs').sort().map(name =>
  [name, {registrySource: `verbs/${name}`, inputSchema: {type: 'object'}}]));
''')
        self.write('ops/migration-shadow.py', '''from pathlib import Path
Path('db/schema.sql').write_text(''.join(p.read_text() for p in sorted(Path('migrations').glob('*.sql'))))
''')
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'selftest@example.invalid')
        self.git('config', 'user.name', 'Selftest')
        self.source('base')
        self.commit('base')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def write(self, name, content):
        (self.root / name).write_text(content)

    def command(self, *args):
        result = subprocess.run(args, cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def git(self, *args):
        return self.command('git', *args)

    def commit(self, message):
        paths = self.git('ls-files', '--modified', '--others', '--exclude-standard').splitlines()
        self.git('add', '--', *paths)
        self.git('commit', '-m', message)

    def source(self, name):
        self.write(f'verbs/{name}', f'{name}\n')
        self.write(f'migrations/{name}.sql', f'create table {name}(id integer);\n')
        self.command('node', 'ops/scac-mutation-inventory.mjs', '--write-current')
        self.command(sys.executable, 'ops/migration-shadow.py', '--write')

    def merge(self):
        with patch.object(regen, 'ROOT', self.root), patch.object(sys, 'argv', ['regen', '--merge']), \
                patch.dict('os.environ', self.env, clear=True):
            return regen.main()

    def branches(self):
        self.git('checkout', '-b', 'peer')
        self.source('peer')
        self.commit('peer verb and migration')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('checkout', '-b', 'feature', 'main')
        self.source('feature')
        self.commit('feature verb and migration')


class GeneratedMergeTests(MergeFixture, unittest.TestCase):
    def test_two_branches_keep_both_verbs_and_migrations_and_stage_fresh_outputs(self):
        self.branches()
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.git('ls-files', '-u'), '')
        self.command('node', 'ops/scac-mutation-inventory.mjs')
        self.assertEqual((self.root / 'db/schema.sql').read_text(),
                         'create table base(id integer);\ncreate table feature(id integer);\n'
                         'create table peer(id integer);\n')
        operations = self.command('node', '--input-type=module', '-e',
            f'import {{SCAC_MUTATION_OPERATIONS as operations, GRANTS}} from "./{REGISTRY}";'
            'console.log(JSON.stringify([Object.keys(operations), GRANTS]));')
        self.assertEqual(json.loads(operations), [['base', 'feature', 'peer'], 'preserved'])
        self.assertEqual(self.git('diff', '--name-only'), '')
        self.assertNotEqual(self.git('rev-parse', 'MERGE_HEAD').strip(), '')
        config = subprocess.run(['git', 'config', '--local', '--get-regexp', '^merge\\.'],
                                cwd=self.root, env=self.env, capture_output=True)
        self.assertEqual(config.returncode, 1)

    def test_plain_git_still_reports_conflicts_without_the_regeneration_command(self):
        self.branches()
        result = subprocess.run(['git', 'merge', '--no-commit', 'origin/main'],
                                cwd=self.root, env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(set(self.git('diff', '--name-only', '--diff-filter=U').splitlines()),
                         {REGISTRY, 'db/schema.sql'})

    def test_dirty_tree_is_refused_before_merge(self):
        self.branches()
        self.write('verbs/uncommitted', 'local work\n')
        self.assertEqual(self.merge(), 1)
        self.assertEqual(self.git('ls-files', '-u'), '')
        self.assertEqual((self.root / 'verbs/uncommitted').read_text(), 'local work\n')

    def test_genuine_registry_metadata_conflict_is_not_discarded(self):
        self.git('checkout', '-b', 'peer')
        self.write(REGISTRY, (self.root / REGISTRY).read_text().replace('preserved', 'peer'))
        self.commit('peer grant change')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('checkout', '-b', 'feature', 'main')
        self.write(REGISTRY, (self.root / REGISTRY).read_text().replace('preserved', 'feature'))
        self.commit('feature grant change')
        self.assertEqual(self.merge(), 1)
        self.assertIn(REGISTRY, self.git('diff', '--name-only', '--diff-filter=U'))

    def test_metadata_updated_by_main_is_preserved_with_both_source_changes(self):
        self.git('checkout', '-b', 'peer')
        self.source('peer')
        self.write(REGISTRY, (self.root / REGISTRY).read_text().replace('preserved', 'peer grants'))
        self.commit('peer metadata and source')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('checkout', '-b', 'feature', 'main')
        self.source('feature')
        self.commit('feature source')
        self.assertEqual(self.merge(), 0)
        self.assertIn('peer grants', (self.root / REGISTRY).read_text())
        self.command('node', 'ops/scac-mutation-inventory.mjs')

    def test_handwritten_source_conflicts_remain_visible(self):
        self.write('source.py', 'value = "base"\n')
        self.commit('shared source')
        self.git('checkout', '-b', 'peer')
        self.source('peer')
        self.write('source.py', 'value = "peer"\n')
        self.commit('peer source')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('checkout', '-b', 'feature', 'main')
        self.source('feature')
        self.write('source.py', 'value = "feature"\n')
        self.commit('feature source')
        self.assertEqual(self.merge(), 1)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').strip(), 'source.py')

    def test_failed_generator_does_not_commit_or_claim_success(self):
        self.branches()
        self.write('ops/migration-shadow.py', 'raise SystemExit("fixture generation failed")\n')
        self.commit('failing generator')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.merge(), 1)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertNotEqual(self.git('rev-parse', 'MERGE_HEAD').strip(), '')

    def test_registry_staleness_check_still_fails_after_a_source_change(self):
        self.write('verbs/stale', 'stale\n')
        result = subprocess.run(['node', 'ops/scac-mutation-inventory.mjs'],
                                cwd=self.root, env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'operation set drifted', result.stderr)


@unittest.skipUnless(POSTGRES, 'run with --postgres for the full disposable schema proof')
class PostgresMergeTests(MergeFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='carr-generated-proof-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout'
        self.env = fixture_env()
        clone_index_fixture(regen.ROOT, self.root)
        self.git('config', 'user.email', 'selftest@example.invalid')
        self.git('config', 'user.name', 'Selftest')
        for directory in ['.venv', 'mcp-server/node_modules']:
            source = regen.ROOT / directory
            if source.exists():
                (self.root / directory).symlink_to(source, target_is_directory=True)
        self.git('checkout', '-B', 'main')
        for side in ['left', 'right']:
            self.write(f'mcp-server/src/generated-merge-{side}.js', 'export default {};\n')
        tools = self.root / 'mcp-server/src/tools.js'
        tools.write_text("import left from './generated-merge-left.js';\n"
                         "import right from './generated-merge-right.js';\n" + tools.read_text().replace(
                             'Object.freeze(TOOLS);', 'Object.assign(TOOLS, left, right);\nObject.freeze(TOOLS);'))
        self.command('node', 'ops/scac-mutation-inventory.mjs', '--write-current')
        self.commit('fixture source seams')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

    def source(self, name):
        side, slot = ('left', '9902') if name == 'feature' else ('right', '9901')
        path = f'mcp-server/src/generated-merge-{side}.js'
        self.write(path, 'export default ' + json.dumps({f'fixture-{side}': {
            'registrySource': path, 'inputSchema': {'type': 'object'}}}) + ';\n')
        self.write(f'migrations/{slot}_generated_merge_{side}.sql',
                   f'create table public.generated_merge_{side}(id integer);\n')
        self.command('node', 'ops/scac-mutation-inventory.mjs', '--write-current')
        self.command(sys.executable, 'ops/migration-shadow.py', '--base', self.base, '--write')

    def test_real_schema_and_registry_merge_and_staleness(self):
        self.branches()
        self.assertEqual(self.merge(), 0)
        self.assertEqual(self.git('ls-files', '-u'), '')
        self.command('node', 'ops/scac-mutation-inventory.mjs')
        self.command(sys.executable, 'ops/migration-shadow.py', '--base', 'origin/main')
        schema = self.root / 'db/schema.sql'
        for side in ['left', 'right']:
            self.assertIn(f'CREATE TABLE public.generated_merge_{side}', schema.read_text())
        schema.write_bytes(schema.read_bytes() + b'\nCREATE TABLE public.fixture_stale(id integer);\n')
        stale = subprocess.run([sys.executable, 'ops/migration-shadow.py', '--base', 'origin/main'],
                               cwd=self.root, env=self.env, capture_output=True)
        self.assertEqual(stale.returncode, 1, stale.stdout.decode() + stale.stderr.decode())
        self.assertIn(b'is STALE', stale.stderr)

if __name__ == '__main__':
    unittest.main()
