#!/usr/bin/env python3
"""Exercise the shard receipt command with omitted and empty execution."""
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ci_unit_shards as planner

ROOT = Path(__file__).resolve().parents[1]


class ShardCoverage(unittest.TestCase):
    def test_current_inventory_fits_watchdog_with_cold_setup_and_headroom(self):
        records, setup = planner.durations()
        self.assertGreaterEqual(setup, 167478, 'retain observed hosted cold setup floor')
        files = planner.suite_files(ROOT)
        shards = planner.plan()
        self.assertEqual(sorted(file for shard in shards for file in shard), files)
        self.assertLessEqual(max(planner.estimate(shard, records, setup) for shard in shards), planner.BUDGET_MS)
        for count in range(1, planner.COUNT):
            self.assertGreater(max(planner.estimate(shard, records, setup) for shard in planner.plan(count=count)),
                               planner.BUDGET_MS, f'{count} shards would fit with headroom')

    def test_workflow_and_consumers_share_shard_cardinality(self):
        workflow = (ROOT / '.github/workflows/unit-shards.yml').read_text()
        matrix = json.loads(re.search(r'shard: (\[[^\n]+\])', workflow)[1])
        self.assertEqual(matrix, list(range(1, planner.COUNT + 1)))
        self.assertEqual(int(re.search(r'name: MCP unit shard .* of (\d+)', workflow)[1]), planner.COUNT)

    def test_setup_is_counted_once_and_unknown_suites_are_conservative(self):
        records = {'a': {'milliseconds': 20, 'serial': True},
                   'b': {'milliseconds': 10, 'serial': False}}
        self.assertEqual(planner.estimate(['a', 'b'], records, 50), 80)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            (tests / 'new.test.mjs').touch()
            self.inventory(root, 1)
            records, _ = planner.durations(root)
            self.assertTrue(records['new.test.mjs']['serial'])
            self.assertGreaterEqual(records['new.test.mjs']['milliseconds'], planner.WATCHDOG_MS)
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            timing.write_text(json.dumps({'suites': {'known': {'milliseconds': 251409, 'serial': False}}}))
            records, _ = planner.durations(root)
            self.assertEqual(records['new.test.mjs'], {'milliseconds': 251409, 'serial': True})

    def inventory(self, root, expected):
        mcp = root / 'mcp-server'
        shutil.copy2(ROOT / 'mcp-server/suite-discovery.mjs', mcp / 'suite-discovery.mjs')
        (mcp / 'suite-inventory.json').write_text(json.dumps({'expected': expected}))

    def test_measured_durations_balance_and_discovery_keeps_new_suites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            records = {}
            for index, milliseconds in enumerate([90] * planner.COUNT + [10] * planner.COUNT):
                name = f'{index}.test.mjs'
                (tests / name).touch()
                records[name] = {'milliseconds': milliseconds, 'serial': True}
            timing_file = root / 'ops/config/ci-unit-durations.json'
            timing_file.parent.mkdir(parents=True)
            timing_file.write_text(json.dumps({'suites': records}))
            self.inventory(root, len(list(tests.glob("*.test.mjs"))))
            command = [sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root), 'plan']
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            shards = json.loads(result.stdout)
            self.assertEqual(sorted(sum(records[file]['milliseconds'] for file in shard) for shard in shards), [100] * planner.COUNT)
            original_floor = (root / 'mcp-server/suite-inventory.json').read_bytes()
            (tests / 'new.test.mjs').write_text("import test from 'node:test'; test('extra suite', () => {});")
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            self.assertEqual(sorted(file for shard in json.loads(result.stdout) for file in shard), sorted([*records, 'new.test.mjs']))
            self.assertEqual((root / 'mcp-server/suite-inventory.json').read_bytes(), original_floor)
            log = root / 'incomplete.log'
            log.write_text('suite-timing: 0.test.mjs 90ms\n')
            result = subprocess.run([sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root),
                                     'timings', str(log), '--source-run', '1', '--output', str(timing_file)],
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing=', result.stderr)
            shutil.copy2(ROOT / 'mcp-server/run-tests.mjs', root / 'mcp-server/run-tests.mjs')
            log.write_text('suite-timing: snapshot-template 99ms\nsuite-timing: snapshot-template 101ms\n' +
                           ''.join(f'suite-timing: {file} 10ms\n' for file in [*records, 'new.test.mjs']))
            planner.regenerate([log], 1, timing_file, root)
            self.assertEqual(json.loads(timing_file.read_text())['snapshot_template_ms'], 101)

    def test_receipts_require_every_suite_and_nonempty_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            for n in range(32):
                (tests / f'suite{n}.test.mjs').write_text('')
            self.inventory(root, len(list(tests.glob("*.test.mjs"))))
            command = [sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root)]
            plan = subprocess.run([*command, 'plan'], text=True, capture_output=True)
            self.assertEqual(plan.returncode, 0, plan.stderr)
            shards = json.loads(plan.stdout)
            for index, suites in enumerate(shards, 1):
                (root / f'{index}.json').write_text(json.dumps({'shard': index, 'suites': suites}))
            verify = [*command, 'verify', *map(str, sorted(root.glob('*.json'))) ]
            result = subprocess.run(verify, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            missing = subprocess.run(verify[:-1], text=True, capture_output=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('exactly one receipt', missing.stderr)
            duplicate = subprocess.run([*verify, str(root / '1.json')], text=True, capture_output=True)
            self.assertNotEqual(duplicate.returncode, 0)
            hidden = shards[0].pop()
            (root / '1.json').write_text(json.dumps({'shard': 1, 'suites': shards[0]}))
            result = subprocess.run(verify, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(hidden, result.stderr)
            (root / '1.json').write_text(json.dumps({'shard': 1, 'suites': []}))
            result = subprocess.run(verify, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('zero suites', result.stderr)


if __name__ == '__main__':
    unittest.main()
