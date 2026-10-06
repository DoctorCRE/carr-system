#!/usr/bin/env python3
"""The generated fixture owns current-version discovery and runtime rendering."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class GeneratedFrontier(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix='scac-frontier-')) / 'repo'
        subprocess.run(['git', 'clone', '-q', '--shared', str(ROOT), str(self.repo)],
                       env=fixture_env(), check=True, capture_output=True)
        shutil.copyfile(ROOT / 'ops/scac-mutation-inventory.mjs', self.repo / 'ops/scac-mutation-inventory.mjs')
        shutil.copyfile(ROOT / 'ops/config/scac-registry-chain.json', self.repo / 'ops/config/scac-registry-chain.json')
        self.path = self.repo / 'ops/config/scac-registry-source-inventory-fixtures.v1.json'
        shutil.copyfile(ROOT / 'ops/config/scac-registry-source-inventory-fixtures.v1.json', self.path)
        self.fixture = json.loads(self.path.read_text())
        previous = self.fixture['patches'][-1]
        self.number = int(previous['version'][1:]) + 1
        self.fixture['patches'].append({
            'version': f'v{self.number}', 'remove': [], 'upsert': [],
            'expected_count': previous['expected_count'],
            'expected_sha256': previous['expected_sha256'],
        })

    def run_node(self, code):
        self.path.write_text(json.dumps(self.fixture))
        return subprocess.run(['node', '--input-type=module', '-e', code],
                              cwd=self.repo, env=fixture_env(), text=True, capture_output=True)

    def test_repository_frontier_includes_current_generated_history(self):
        current = json.loads((ROOT / 'ops/config/scac-registry-source-inventory-fixtures.v1.json').read_text())['patches'][-1]
        result = self.run_node(f"""
import assert from 'node:assert/strict';
import {{CURRENT_REGISTRY_VERSION,frozenInventory}} from './ops/scac-mutation-inventory.mjs';
const current='scac-mutation-registry.{current['version']}';
assert.equal(CURRENT_REGISTRY_VERSION,'scac-mutation-registry.v{int(current['version'][1:])+1}');
assert.equal(frozenInventory(current).length,{current['expected_count']});
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_successor_uses_fixture_frontier_and_preserves_historical_rows(self):
        result = self.run_node(f"""
import assert from 'node:assert/strict';
import {{CURRENT_REGISTRY_VERSION,frozenInventory,renderRuntimeProjection}} from './ops/scac-mutation-inventory.mjs';
const version='scac-mutation-registry.v{self.number}';
assert.equal(CURRENT_REGISTRY_VERSION,version);
const rows=frozenInventory(version);
assert.deepEqual(rows,frozenInventory('scac-mutation-registry.v{self.number-1}'));
assert.match(renderRuntimeProjection(rows,{{version}}),new RegExp('SCAC_MUTATION_REGISTRY_VERSION = "'+version+'"'));
assert.throws(()=>renderRuntimeProjection(rows,{{version:'scac-mutation-registry.v{self.number+1}'}}),/unsupported/);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_frontier_gap_is_refused(self):
        self.fixture['patches'][-1]['version'] = f'v{self.number+1}'
        result = self.run_node("await import('./ops/scac-mutation-inventory.mjs');")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source-inventory frontier', result.stderr)

    def test_frontier_duplicate_is_refused(self):
        self.fixture['patches'][-1]['version'] = f'v{self.number-1}'
        result = self.run_node("await import('./ops/scac-mutation-inventory.mjs');")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source-inventory frontier', result.stderr)


if __name__ == '__main__':
    unittest.main()
