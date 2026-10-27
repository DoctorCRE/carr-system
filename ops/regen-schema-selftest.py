#!/usr/bin/env python3
# Test schema regeneration during merges.
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'db/schema.sql'


class SchemaMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='regen-schema-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.environment = fixture_env()
        (self.repo / 'db').mkdir()
        (self.repo / 'tools').mkdir()
        (self.repo / 'ops').mkdir()
        (self.repo / 'migrations').mkdir()
        (self.repo / SCHEMA).write_text('-- base schema\n')
        (self.repo / 'migrations/0001_base.sql').write_text('select 1;\n')
        shutil.copy(ROOT / 'run.sh', self.repo)
        shutil.copy(ROOT / 'tools/regen.py', self.repo / 'tools')
        shutil.copy(ROOT / 'ops/git_env.py', self.repo / 'ops')
        (self.repo / 'ops/migration-shadow.py').write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "schema = Path('db/schema.sql')\n"
            "schema.write_text(Path('ops/candidate.sql').read_text())\n"
            "sys.exit(1 if Path('ops/fail').exists() else 0)\n")
        (self.repo / 'ops/candidate.sql').write_text('-- generated schema\n')
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', 'db', 'migrations', 'ops', 'tools', 'run.sh')
        self.commit('base')
        for branch, migration, schema_line in (
                ('a', '0002_a.sql', '-- branch a\n'),
                ('b', '0003_b.sql', '-- branch b\n')):
            self.git('checkout', '-b', branch, 'main')
            (self.repo / f'migrations/{migration}').write_text('select 2;\n')
            (self.repo / SCHEMA).write_text('-- base schema\n' + schema_line)
            self.git('add', SCHEMA, f'migrations/{migration}')
            self.commit(branch)
        self.git('checkout', 'a')
        result = self.git('merge', '--no-edit', 'b', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout.strip(), SCHEMA)
        self.conflicted = (self.repo / SCHEMA).read_bytes()

    def command(self, *args, check=True, input=None):
        result = subprocess.run(args, cwd=self.repo, env=self.environment, capture_output=True,
                                text=True, input=input)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *args, check=True, input=None):
        return self.command('git', '-c', 'core.hooksPath=/dev/null', *args, check=check, input=input)

    def commit(self, message):
        message_file = self.repo / 'message.txt'
        message_file.write_text(message)
        self.git('commit', '-F', str(message_file))

    def regen(self):
        return self.command('zsh', 'run.sh', 'regen', 'schema', check=False)

    def test_schema_only_conflict_is_regenerated_and_staged(self):
        result = self.regen()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.repo / SCHEMA).read_text(), '-- generated schema\n')
        self.assertIn(SCHEMA, self.git('diff', '--cached', '--name-only').stdout.splitlines())
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout, '')
        self.assertTrue((self.repo / 'migrations/0002_a.sql').exists())
        self.assertTrue((self.repo / 'migrations/0003_b.sql').exists())

    def test_failed_regeneration_restores_conflict_and_index(self):
        (self.repo / 'ops/fail').touch()
        before = self.git('ls-files', '--stage').stdout
        result = self.regen()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.repo / SCHEMA).read_bytes(), self.conflicted)
        self.assertEqual(self.git('ls-files', '--stage').stdout, before)
        self.assertEqual(self.git('diff', '--name-only', '--diff-filter=U').stdout.strip(), SCHEMA)

    def test_unrelated_source_conflict_is_refused_without_touching_schema(self):
        source = 'migrations/0001_base.sql'
        ours = self.git('hash-object', '-w', '--stdin', input='select 2;\n').stdout.strip()
        theirs = self.git('hash-object', '-w', '--stdin', input='select 3;\n').stdout.strip()
        base = self.git('rev-parse', f'main:{source}').stdout.strip()
        entries = f'0 {"0" * 40}\t{source}\n'
        entries += f'100644 {base} 1\t{source}\n100644 {ours} 2\t{source}\n100644 {theirs} 3\t{source}\n'
        self.git('update-index', '--index-info', input=entries)
        before = self.git('ls-files', '--stage').stdout
        result = self.regen()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('resolve source conflicts before regenerating the schema', result.stderr)
        self.assertEqual((self.repo / SCHEMA).read_bytes(), self.conflicted)
        self.assertEqual(self.git('ls-files', '--stage').stdout, before)


if __name__ == '__main__':
    unittest.main()
