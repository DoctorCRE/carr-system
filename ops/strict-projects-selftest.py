#!/usr/bin/env python3
"""Check that independent admissions compose without shared list edits."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('strict_projects', Path(__file__).with_name('strict-projects.py'))
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class StrictProjects(unittest.TestCase):
    def test_import_real_git_head_preserves_only_new_admissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = root / 'mcp-server'
            admissions = pkg / 'typescript/projects'
            admissions.mkdir(parents=True)
            (pkg / 'old.ts').touch()
            (pkg / 'new.test.ts').touch()
            (admissions / 'old.json').write_text(json.dumps({'node': ['old.ts'], 'entry': ['old.ts']}))
            for group, files in [('node', ['old.ts', 'new.test.ts']), ('worker', [])]:
                (pkg / f'tsconfig.{group}.json').write_text(json.dumps({'files': files}))
            (pkg / 'knip.json').write_text(json.dumps({'entry': ['old.ts', '*.test.ts']}))
            env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
            def git(*args):
                subprocess.run(['git', *args], cwd=root, env=env, check=True, capture_output=True)
            git('init', '-q')
            git('add', 'mcp-server/tsconfig.node.json', 'mcp-server/tsconfig.worker.json', 'mcp-server/knip.json')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'conversion')
            module.admit(pkg, 'HEAD', 'conversion')
            destination = admissions / 'conversion.json'
            first = destination.read_bytes()
            self.assertEqual(json.loads(first), {'node': ['new.test.ts'], 'worker': [], 'entry': ['new.test.ts']})
            module.admit(pkg, 'HEAD', 'conversion')
            self.assertEqual(destination.read_bytes(), first)

    def test_independent_fragments_cover_compiler_and_knip(self):
        with tempfile.TemporaryDirectory() as directory:
            pkg = Path(directory)
            admissions = pkg / 'typescript/projects'
            admissions.mkdir(parents=True)
            for name in ('a', 'b'):
                (pkg / f'{name}.ts').touch()
                (admissions / f'{name}.json').write_text(json.dumps({
                    'node': [f'{name}.ts'], 'worker': [], 'entry': [f'{name}.ts']}))
            module.generate(pkg)
            node = json.loads((pkg / 'tsconfig.node.json').read_text())
            knip = json.loads((pkg / 'knip.json').read_text())
            self.assertEqual(node['files'], ['a.ts', 'b.ts'])
            self.assertEqual(knip['project'], node['files'])
            self.assertEqual(knip['entry'], node['files'])
            before = (pkg / 'knip.json').read_bytes()
            module.generate(pkg)
            self.assertEqual((pkg / 'knip.json').read_bytes(), before)
            (pkg / 'a.ts').unlink()
            module.generate(pkg)
            self.assertEqual(json.loads((pkg / 'knip.json').read_text())['project'], ['b.ts'])

    def test_fallow_renames_only_existing_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            pkg = Path(directory)
            (pkg / 'typescript/projects').mkdir(parents=True)
            baseline = pkg / 'fallow-baselines'
            baseline.mkdir()
            source = {'unused_exports': ['unused-export:converted.js:existing', 'unused-export:remaining.js:existing']}
            (baseline / 'dead-code.legacy.json').write_text(json.dumps(source))
            (pkg / 'converted.ts').touch()
            module.generate(pkg)
            actual = json.loads((baseline / 'dead-code.json').read_text())
            self.assertEqual(actual['unused_exports'],
                             ['unused-export:converted.ts:existing', 'unused-export:remaining.js:existing'])
            self.assertEqual(json.loads((baseline / 'dead-code.legacy.json').read_text()), source)
            self.assertNotIn('unused-export:converted.ts:new', actual['unused_exports'])

    def test_unknown_fields_and_uncovered_entries_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            pkg = Path(directory)
            admissions = pkg / 'typescript/projects'
            admissions.mkdir(parents=True)
            (pkg / 'a.ts').touch()
            file = admissions / 'a.json'
            for record in ({'node': 'a.ts'}, {'ignore': ['a.ts']}, {'entry': ['a.ts']}):
                file.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    module.generate(pkg)


if __name__ == '__main__':
    unittest.main()
