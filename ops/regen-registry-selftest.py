#!/usr/bin/env python3
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'


class RegistryMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='regen-registry-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.environment = fixture_env()
        (self.repo / 'ops').mkdir()
        (self.repo / 'tools').mkdir()
        shutil.copytree(ROOT / 'mcp-server/src', self.repo / 'mcp-server/src')
        shutil.copy(ROOT / 'mcp-server/package.json', self.repo / 'mcp-server/package.json')
        (self.repo / 'mcp-server/node_modules').symlink_to(ROOT / 'mcp-server/node_modules')
        shutil.copy(ROOT / 'ops/scac-mutation-inventory.mjs', self.repo / 'ops')
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

    def command(self, *args, check=True):
        result = subprocess.run(args, cwd=self.repo, env=self.environment, capture_output=True, text=True)
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
        self.assertEqual((self.repo / REGISTRY).read_bytes(), self.conflicted)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout.strip(), REGISTRY)


if __name__ == '__main__':
    unittest.main()
