#!/usr/bin/env python3
"""Check that independent admissions compose without shared list edits."""
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from git_env import fixture_env

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
            env = fixture_env()
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

    def test_node_compat_emits_declarations_for_worker_without_node_globals(self):
        with tempfile.TemporaryDirectory() as directory:
            pkg = Path(directory)
            admissions = pkg / 'typescript/projects'
            admissions.mkdir(parents=True)
            for name in ('hash.ts', 'consumer.ts'):
                (pkg / name).touch()
            (admissions / 'compat.json').write_text(json.dumps({
                'node_compat': ['hash.ts'], 'worker': ['consumer.ts'], 'entry': ['consumer.ts']}))
            module.generate(pkg)
            compat = json.loads((pkg / 'tsconfig.node-compat.json').read_text())
            worker = json.loads((pkg / 'tsconfig.worker.json').read_text())
            self.assertEqual(compat['files'], ['hash.ts'])
            self.assertTrue(compat['compilerOptions']['emitDeclarationOnly'])
            self.assertFalse(compat['compilerOptions']['noEmit'])
            self.assertEqual(compat['compilerOptions']['lib'], ['esnext'])
            self.assertEqual(worker['compilerOptions']['types'], ['./.generated/worker-runtime'])
            self.assertEqual(worker['compilerOptions']['lib'], ['esnext'])
            self.assertEqual(worker['references'], [{'path': './tsconfig.node-compat.json'}])
            self.assertEqual(json.loads((pkg / 'knip.json').read_text())['project'], ['consumer.ts', 'hash.ts'])

    def test_one_runtime_owner_per_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            pkg = Path(directory)
            admissions = pkg / 'typescript/projects'
            admissions.mkdir(parents=True)
            (pkg / 'hash.ts').touch()
            (admissions / 'bad.json').write_text(json.dumps({
                'node_compat': ['hash.ts'], 'worker': ['hash.ts']}))
            with self.assertRaisesRegex(ValueError, 'multiple runtime'):
                module.generate(pkg)

    def test_official_declarations_compile_and_refuse_cross_runtime_globals(self):
        pkg = Path(__file__).resolve().parents[1] / 'mcp-server'
        compiler = pkg / 'node_modules/.bin/tsc'
        runtime = pkg / '.generated/worker-runtime.d.ts'
        if not compiler.exists() or not runtime.exists():
            self.skipTest('npm ci and npm run generate:types are required for compiler probes')
        with tempfile.TemporaryDirectory(dir=pkg / '.generated') as directory:
            root = Path(directory)
            (root / 'hash.ts').write_text(
                'import {createHash} from "node:crypto";\n'
                'export function digest(value: string): string {\n'
                'return createHash("sha256").update(value).digest("hex"); }\n')
            node = {'extends': str(pkg / 'tsconfig.base.json'), 'compilerOptions': {
                'lib': ['esnext'], 'noEmit': False, 'declaration': True,
                'emitDeclarationOnly': True, 'composite': True, 'outDir': './declarations'},
                'files': ['hash.ts']}
            worker = {'extends': str(pkg / 'tsconfig.base.json'), 'compilerOptions': {
                'module': 'ESNext', 'moduleResolution': 'Bundler', 'lib': ['esnext'],
                'types': [str(runtime)], 'disableSourceOfProjectReferenceRedirect': True},
                'files': ['consumer.ts'], 'references': [{'path': './node.json'}]}
            for name, config in [('node', node), ('worker', worker)]:
                (root / f'{name}.json').write_text(json.dumps(config))
            consumer = root / 'consumer.ts'
            valid = ('import {digest} from "./hash.js";\n'
                     'export const response = new Response(digest("synthetic"));\n'
                     'export function storage(state: DurableObjectState) { return state.storage; }\n')
            consumer.write_text(valid)
            def check(name):
                return subprocess.run([str(compiler), '-p', str(root / f'{name}.json')],
                                      capture_output=True, text=True, timeout=60)
            for name in ('node', 'worker'):
                result = check(name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            consumer.write_text(valid + 'import {createHash} from "node:crypto";\n'
                                'process.cwd(); Buffer.from("wrong"); digest(7); createHash("sha256");\n')
            result = check('worker')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("'process'", result.stdout)
            self.assertIn("'Buffer'", result.stdout)
            self.assertIn("'node:crypto'", result.stdout)
            self.assertIn('TS2345', result.stdout)
            with (root / 'hash.ts').open('a') as stream:
                stream.write('export function leak(state: DurableObjectState) { return state; }\n')
            result = check('node')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("'DurableObjectState'", result.stdout)
    def test_sibling_workspace_keeps_consumers_as_entries_and_libraries_as_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = root / 'mcp-server'
            admissions = pkg / 'typescript/projects'
            admissions.mkdir(parents=True)
            plugin = root / 'practice-plugin'
            (plugin / 'test').mkdir(parents=True)
            (plugin / 'src').mkdir()
            (plugin / 'package.json').write_text('{"name":"practice-plugin"}')
            (pkg / 'local.ts').touch()
            for file in ('test/calculators.test.mjs', 'test/fixtures.ts', 'src/runway.ts'):
                (plugin / file).touch()
            (admissions / 'plugin.json').write_text(json.dumps({
                'node': ['local.ts', '../practice-plugin/test/calculators.test.mjs',
                         '../practice-plugin/test/fixtures.ts'],
                'worker': ['../practice-plugin/src/runway.ts'],
                'entry': ['local.ts', '../practice-plugin/test/calculators.test.mjs']}))
            module.generate(pkg)
            graph = json.loads((pkg / 'knip.json').read_text())
            self.assertEqual(graph['workspaces'], {
                '.': {'entry': ['local.ts'], 'project': ['local.ts']},
                '../practice-plugin': {
                    'node': False,
                    'entry': ['test/calculators.test.mjs'],
                    'project': ['src/runway.ts', 'test/calculators.test.mjs', 'test/fixtures.ts']}})
            self.assertEqual(graph['project'], sorted([
                'local.ts', '../practice-plugin/src/runway.ts',
                '../practice-plugin/test/calculators.test.mjs', '../practice-plugin/test/fixtures.ts']))
            before = (pkg / 'knip.json').read_bytes()
            module.generate(pkg)
            self.assertEqual((pkg / 'knip.json').read_bytes(), before)

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


class M043Proof(unittest.TestCase):
    def test_release_containment_exit_codes(self):
        spec = importlib.util.spec_from_file_location('m043_proof', Path(__file__).with_name('prove-m043.py'))
        proof = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proof)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = fixture_env()
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, env=env, text=True).strip()
            git('init', '-q')
            def commit(message):
                git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--allow-empty', '-qm', message)
                return git('rev-parse', 'HEAD')
            older = commit('older')
            merge = commit('merge')
            descendant = commit('descendant')
            git('checkout', '--orphan', 'unrelated')
            unrelated = commit('unrelated')
            real_run = subprocess.run
            for name, revision, expected, assertion_status in (
                    ('descendant', descendant, 0, 0), ('equal', merge, 0, 0),
                    ('older', older, 78, 0), ('unrelated', unrelated, 78, 0),
                    ('unknown', 'f' * 40, 78, 0), ('unavailable', None, 78, 0),
                    ('assertion failure', merge, 1, 1), ('deadline', merge, 78, 124),
                    ('missing executable', merge, 78, 127)):
                with self.subTest(release=name):
                    from lib.release_reader import Reply
                    reply = Reply(200, {}, json.dumps({'ok': True, 'env': {'value': 'production'},
                                                       'git_sha': {'value': revision}}).encode())
                    checks = []
                    def run(command, **kwargs):
                        if command[0] == 'git':
                            kwargs.update(cwd=root, env=env)
                            return real_run(command, **kwargs)
                        checks.append(command)
                        return subprocess.CompletedProcess(command, assertion_status)
                    with patch.object(proof, 'read', return_value=reply), \
                            patch.object(proof.subprocess, 'run', side_effect=run), \
                            patch.object(proof.TIMEOUT, 'main', side_effect=lambda argv: run(argv[2:]).returncode), \
                            patch('sys.argv', ['prove-m043.py', '--expected-merge', merge]), \
                            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        try:
                            status = proof.main()
                        except subprocess.CalledProcessError:
                            status = 1
                    self.assertEqual(status, expected)
                    self.assertEqual(len(checks), 3 if expected == 0 else 1 if revision == merge else 0)


if __name__ == '__main__':
    unittest.main()
