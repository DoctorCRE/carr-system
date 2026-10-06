#!/usr/bin/env python3
"""Exercise successor recovery and approval carry-forward through their CLIs."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class SuccessorCommands(unittest.TestCase):
    def test_generated_successor_rehome_uses_all_current_main_predecessor_inputs(self):
        from unittest.mock import patch
        subprocess.run(['git', 'clone', '--quiet', '--shared', str(ROOT), str(self.repo / 'source')],
                       env=self.env, check=True, capture_output=True)
        self.repo = self.repo / 'source'
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'set-url', 'origin', str(self.repo))
        self.git('branch', 'main', 'HEAD')
        self.git('switch', '-qc', 'feature')
        self.base = self.head()
        old = json.loads((self.repo / 'ops/config/scac-registry-chain.json').read_text())
        current = old['versions'][-1]
        tail = max(int(p.name[:4]) for p in (self.repo / 'migrations').glob('*.sql'))
        domain = f'migrations/{tail+1:04d}_rehome_fixture.sql'
        seal = f'migrations/{tail+2:04d}_rehome_fixture_scac_successor.sql'
        self.write(domain, 'select 1;\n')
        compiler = """import fs from 'node:fs';
import {appendSuccessor,registryChain} from './ops/registry-chain.mjs';
import {historicalRows} from './ops/registry-history.mjs';
const row=registryChain.versions.at(-1);
const result=appendSuccessor({rows:historicalRows(row.number),catalog:row.catalog,
entrySetDigest:row.entry_set_digest,domainMigration:{filename:process.argv[1],
sql:fs.readFileSync(process.argv[1],'utf8'),successor_filename:process.argv[2].split('/').at(-1)}});
fs.writeFileSync(process.argv[2],result.sql);
fs.writeFileSync('mcp-server/src/scac-mutation-registry.current.generated.js',result.runtime);
for(const [path,value] of [['scac-registry-chain.json',result.chain],
['scac-registry-source-inventory-fixtures.v1.json',result.fixture],
['scac-registry-full-entry-set-seals.json',result.seals]])
fs.writeFileSync('ops/config/'+path,JSON.stringify(value,null,2)+'\\n');
"""
        def compile_at(repo, domain_path, seal_path):
            subprocess.run(['node', '--input-type=module', '-e', compiler, str(domain_path), str(seal_path)],
                           cwd=repo, env=self.env, check=True, capture_output=True)
        compile_at(self.repo, domain, seal)
        inputs = ['ops/config/scac-registry-chain.json',
                  'ops/config/scac-registry-source-inventory-fixtures.v1.json',
                  'ops/config/scac-registry-full-entry-set-seals.json',
                  'mcp-server/src/scac-mutation-registry.current.generated.js']
        self.commit(domain, seal, *inputs)
        approved = self.head()
        self.advance_main()
        self.git('fetch', '-q', 'origin', 'main')
        module = self.module()
        def regenerate(repo, plan, domains, successor, predecessor):
            self.assertEqual(plan['registry_predecessor'], current['number'])
            for path in inputs:
                self.assertEqual((repo / path).read_bytes(), module.file_at(self.repo, self.main, path), path)
            compile_at(repo, domains[0].relative_to(repo), successor.relative_to(repo))
        with patch.object(module, 'regenerate', regenerate):
            staging, rewritten = module.prepare(self.repo, self.base, approved, self.main, [])
        chain = json.loads((staging / inputs[0]).read_text())
        self.assertEqual(chain['versions'][:-1], old['versions'])
        self.assertEqual(chain['versions'][-1]['number'], current['number'] + 1)
        self.assertEqual(module.file_at(staging, 'HEAD', domain), module.file_at(self.repo, approved, domain))
        self.assertTrue(set(inputs).issubset(rewritten))

    def test_chain_ownership_preserves_policy_and_only_adds_successor_groups(self):
        from registry_chain import registry_chain
        from successor_ownership import is_owned_file
        import copy
        old = registry_chain()
        new = copy.deepcopy(old)
        current = dict(new['versions'][-1], number=len(old['versions'])+1,
                       predecessor=old['versions'][-1]['version'],
                       version=f"scac-mutation-registry.v{len(old['versions'])+1}",
                       atomic_pair=['0900_fixture.sql', '0901_fixture_scac_successor.sql'], strict_atomic=True)
        new['versions'].append(current)
        new['atomic_groups'].append(current['atomic_pair'])
        new['strict_atomic_groups'].append(current['atomic_pair'])
        path = 'ops/config/scac-registry-chain.json'
        owned = lambda value: is_owned_file(path, json.dumps(old).encode(), json.dumps(value).encode())
        self.assertTrue(owned(new))
        for mutate in [
            lambda value: value['inactive_atomic_groups'].append({'group': old['atomic_groups'][-1], 'reason': 'unreviewed'}),
            lambda value: value.update(unreviewed_policy=True),
            lambda value: value['atomic_groups'].append(['9999_unrelated.sql']),
            lambda value: value['strict_atomic_groups'].pop(),
        ]:
            changed = copy.deepcopy(new)
            mutate(changed)
            self.assertFalse(owned(changed))

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="successor-rehome-test-"))
        self.env = fixture_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("domain.txt", "before\n")
        self.commit("domain.txt")
        self.base = self.head()
        self.git("remote", "add", "origin", str(self.repo))
        self.git("fetch", "-q", "origin")
        self.git("switch", "-qc", "feature")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo,
                                       env=self.env, text=True, stderr=subprocess.DEVNULL).strip()

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, *paths):
        self.git("add", "--", *paths)
        message = self.repo / ".git" / "fixture-message"
        message.write_text("Fixture change\n")
        self.git("commit", "-q", "-F", str(message))

    def head(self):
        return self.git("rev-parse", "HEAD")

    def command(self, name, *args):
        return subprocess.run([sys.executable, str(ROOT / "ops" / name), *args],
                              cwd=self.repo, env=self.env, capture_output=True, text=True)

    def advance_main(self, path="main.txt", content="main advanced\n"):
        self.git("switch", "-q", "main")
        self.write(path, content)
        self.commit(path)
        self.main = self.head()
        self.git("switch", "-q", "feature")

    def test_clean_rehome_preserves_domain_and_merge_parents(self):
        self.write("domain.txt", "feature\n")
        self.commit("domain.txt")
        approved = self.head()
        self.advance_main()
        result = self.command("rehome-successor.py", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("show", "HEAD:domain.txt"), "feature")
        self.assertEqual(self.git("rev-parse", "HEAD^1"), approved)
        self.assertEqual(self.git("rev-parse", "HEAD^2"), self.main)
        manifest = json.loads((self.repo / ".git" / "successor-rehome.json").read_text())
        self.assertEqual(manifest["rewritten_paths"], [])
        self.assertEqual(manifest["approved_sha"], approved)
        self.assertEqual(manifest["main_sha"], self.main)
        self.assertEqual(manifest["new_sha"], self.head())
        checked = self.command("successor-only-diff.py", approved, self.head())
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_domain_conflict_refuses_with_filename_and_preserves_head(self):
        self.write("domain.txt", "feature\n")
        self.commit("domain.txt")
        approved = self.head()
        self.advance_main("domain.txt", "main\n")
        result = self.command("rehome-successor.py", str(self.repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("domain.txt", result.stderr)
        self.assertEqual(self.head(), approved)
        self.assertEqual((self.repo / "domain.txt").read_text(), "feature\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.repo / ".git" / "successor-rehome.json").exists())

    def test_owned_json_conflict_preserves_main_history(self):
        path = "ops/config/scac-registry-full-entry-set-seals.json"
        self.git("switch", "-q", "main")
        self.write(path, json.dumps({'scac-mutation-registry.v1': 'sha256:'+'a'*64}))
        self.commit(path)
        self.git("fetch", "-q", "origin")
        self.git("switch", "-q", "feature")
        self.git("merge", "-q", "main")
        self.write(path, json.dumps({'scac-mutation-registry.v1': 'sha256:'+'a'*64, 'scac-mutation-registry.v2': 'sha256:'+'b'*64}))
        self.commit(path)
        approved = self.head()
        self.advance_main(path, json.dumps({'scac-mutation-registry.v1': 'sha256:'+'a'*64, 'scac-mutation-registry.v2': 'sha256:'+'c'*64}))
        result = self.command("rehome-successor.py", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.repo / path).read_text()), {'scac-mutation-registry.v1': 'sha256:'+'a'*64, 'scac-mutation-registry.v2': 'sha256:'+'c'*64})
        receipt = json.loads((self.repo / ".git/successor-rehome.json").read_text())
        self.assertEqual(receipt["rewritten_paths"], [path])
        self.assertEqual(self.command("successor-only-diff.py", approved, self.head()).returncode, 0)

    def test_checker_accepts_generated_changes_and_domain_migration_rename(self):
        self.write("migrations/0749_feature.sql", "select 'domain';\n")
        self.write("mcp-server/src/scac-mutation-registry.v98.generated.js", "old generated\n")
        self.commit("migrations/0749_feature.sql", "mcp-server/src/scac-mutation-registry.v98.generated.js")
        approved = self.head()
        self.git("mv", "migrations/0749_feature.sql", "migrations/0750_feature.sql")
        self.write("mcp-server/src/scac-mutation-registry.v98.generated.js", "new generated\n")
        self.commit("migrations/0750_feature.sql",
                    "mcp-server/src/scac-mutation-registry.v98.generated.js")
        result = self.command("successor-only-diff.py", approved, self.head())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_checker_rejects_domain_migration_content_change(self):
        self.write("migrations/0749_feature.sql", "select 'domain';\n")
        self.commit("migrations/0749_feature.sql")
        approved = self.head()
        self.git("mv", "migrations/0749_feature.sql", "migrations/0750_feature.sql")
        self.write("migrations/0750_feature.sql", "select 'changed behavior';\n")
        self.commit("migrations/0750_feature.sql")
        result = self.command("successor-only-diff.py", approved, self.head())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("feature.sql", result.stdout + result.stderr)

    def test_checker_rejects_domain_edit_in_mixed_bookkeeping_file(self):
        self.write("bin/schema-snapshot.sh", "echo domain-before\n")
        self.commit("bin/schema-snapshot.sh")
        approved = self.head()
        self.write("bin/schema-snapshot.sh", "echo domain-after\n")
        self.commit("bin/schema-snapshot.sh")
        result = self.command("successor-only-diff.py", approved, self.head())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bin/schema-snapshot.sh", result.stdout + result.stderr)

    @staticmethod
    def module():
        import importlib.util
        spec = importlib.util.spec_from_file_location('rehome_review', ROOT / 'ops/rehome-successor.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def snapshot_fixture(self):
        source = ('BASE_REGISTRY_APPLIED="$("$PSQL" -Atqc \\\n'
                  '  "select exists (select 1 from schema_migrations where filename=\'0001_base.sql\')" \\\n'
                  '  2>/dev/null)"\ncase "$BASE_REGISTRY_APPLIED" in\n'
                  '  t|f) ;;\n  *) echo "schema-snapshot: ledger unreadable" >&2; exit 1 ;;\nesac\n'
                  'SCAC_CURRENT_NUMBER=109\nSCAC_VERSION_ARRAY="\'scac-mutation-registry.v109\'"\n'
                  'SCAC_CURRENT_CATALOG_FUNCTION="ops.scac_mutation_catalog_v109_current()"\n')
        self.write('bin/schema-snapshot.sh', source)
        return source

    def test_snapshot_symlink_and_parent_escape_refused(self):
        module = self.module()
        source = self.snapshot_fixture()
        external = Path(tempfile.mkdtemp()) / 'external'
        external.write_text(source)
        target = self.repo / 'bin/schema-snapshot.sh'
        target.unlink()
        target.symlink_to(external)
        with self.assertRaises(ValueError):
            module.validate_outputs(self.repo, ['bin/schema-snapshot.sh'])
        self.assertEqual(external.read_text(), source)

    def test_test_sink_symlink_refused_before_snapshot_write(self):
        module = self.module()
        source = self.snapshot_fixture()
        external = Path(tempfile.mkdtemp()) / 'external'
        external.write_text('assert "SCAC_CURRENT_NUMBER=109" in GENERATOR\n')
        (self.repo / 'ops').mkdir()
        (self.repo / 'ops/schema-snapshot-registry-seed-selftest.py').symlink_to(external)
        with self.assertRaises(ValueError):
            module.validate_outputs(self.repo, ['bin/schema-snapshot.sh', 'ops/schema-snapshot-registry-seed-selftest.py'])
        self.assertEqual((self.repo / 'bin/schema-snapshot.sh').read_text(), source)

    def test_same_sha_branch_switch_refuses_promotion(self):
        from unittest.mock import patch
        for branch in ('other', 'main'):
            with self.subTest(branch=branch):
                self.git('switch', '-q', 'feature')
                approved = self.head()
                if branch == 'other':
                    self.git('branch', 'other', approved)
                self.advance_main(content='main ' + branch + '\n')
                module = self.module()
                real_prepare = module.prepare
                def prepare(*args):
                    result = real_prepare(*args)
                    if branch == 'main':
                        self.git('update-ref', 'refs/heads/main', approved)
                    self.git('switch', '-q', branch)
                    return result
                with patch.object(module, 'prepare', prepare):
                    with self.assertRaises(module.RehomeError):
                        module.rehome(self.repo)
                self.assertEqual(self.head(), approved)
                self.assertEqual(self.git('rev-parse', 'feature'), approved)

    def test_commit_has_no_unused_amend_mode(self):
        import inspect
        self.assertEqual(list(inspect.signature(self.module().commit).parameters), ['repo', 'message'])


    def test_preparation_refuses_every_symlink_sink(self):
        module = self.module()
        for path in ('bin/schema-snapshot.sh', 'ops/schema-snapshot-registry-seed-selftest.py',
                     'ops/config/scac-registry-full-entry-set-seals.json',
                     'ops/config/scac-registry-source-inventory-fixtures.v1.json',
                     'mcp-server/src/mutation-registry.js',
                     'mcp-server/src/scac-mutation-registry.v111.generated.js',
                     'migrations/0002_fixture_scac_successor.sql'):
            with self.subTest(path=path):
                self.setUp()
                external = Path(tempfile.mkdtemp()) / 'external'
                external.write_text('external unchanged\n')
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(external)
                self.commit(path)
                self.advance_main()
                with self.assertRaises(ValueError):
                    module.prepare(self.repo, self.base, self.head(), self.main, [])
                self.assertEqual(external.read_text(), 'external unchanged\n')

    def test_snapshot_symlink_ancestor_refused(self):
        module = self.module()
        external = Path(tempfile.mkdtemp())
        external.joinpath('schema-snapshot.sh').write_text('unchanged\n')
        (self.repo / 'bin').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            module.validate_outputs(self.repo, ['bin/schema-snapshot.sh'])
        self.assertEqual(external.joinpath('schema-snapshot.sh').read_text(), 'unchanged\n')
