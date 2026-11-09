#!/usr/bin/env python3
"""Exercise the shard receipt command with omitted and empty execution."""
import json
import os
import textwrap
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import ci_unit_shards as planner

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_COUNT = 7


class ShardCoverage(unittest.TestCase):
    def test_current_inventory_fits_watchdog_with_cold_setup_and_headroom(self):
        records, setup = planner.durations()
        self.assertGreaterEqual(setup, 167478, 'retain observed hosted cold setup floor')
        files = planner.suite_files(ROOT)
        shards = planner.plan()
        self.assertEqual(sorted(file for shard in shards for file in shard), files)
        self.assertLessEqual(max(planner.estimate(shard, records, setup) for shard in shards), planner.BUDGET_MS)
        for count in range(1, len(shards)):
            self.assertGreater(max(planner.estimate(shard, records, setup) for shard in planner.plan(count=count)),
                               planner.BUDGET_MS, f'{count} shards would fit with headroom')

    def test_extension_rename_preserves_measured_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            (tests / 'converted.test.ts').touch()
            self.inventory(root, 1)
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            timing.write_text(json.dumps({'suites': {'converted.test.mjs': {
                'milliseconds': 310000, 'serial': True}}}))
            records, _ = planner.durations(root)
            self.assertEqual(records['converted.test.ts'], {'milliseconds': 310000, 'serial': True})
            self.assertEqual(planner.plan(root), [['converted.test.ts']])
            (tests / 'converted.test.mjs').touch()
            self.inventory(root, 2)
            records, _ = planner.durations(root)
            self.assertTrue(records['converted.test.ts']['unmeasured'])
            self.assertEqual(records['converted.test.ts']['milliseconds'], planner.DEFAULT_SUITE_MS)

    def test_budget_admission_refuses_injected_overweight_shard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            records = {}
            for index in range(FIXTURE_COUNT):
                name = f'{index}.test.mjs'
                (tests / name).touch()
                records[name] = {'milliseconds': 1, 'serial': True}
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            self.inventory(root, FIXTURE_COUNT)
            command = [sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root), 'budget']
            timing.write_text(json.dumps({'suites': records}))
            green = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(green.returncode, 0, green.stderr)
            records['0.test.mjs']['milliseconds'] = planner.BUDGET_MS + 1
            timing.write_text(json.dumps({'suites': records}))
            red = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(red.returncode, 0)
            self.assertIn('exceeds', red.stderr)
            self.assertIn(str(planner.BUDGET_MS), red.stderr)
            workflow = (ROOT / '.github/workflows/unit-shards.yml').read_text()
            script = textwrap.dedent(workflow.split('      - id: assign\n        run: |\n', 1)[1].split('\n  unit:', 1)[0])
            shutil.copy2(ROOT / 'ops/ci_unit_shards.py', root / 'ops/ci_unit_shards.py')
            output = root / 'github-output'
            launch = subprocess.run(['bash', '-e', '-c', script], cwd=root,
                env={**os.environ, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
            self.assertNotEqual(launch.returncode, 0, launch.stdout + launch.stderr)
            self.assertFalse(output.exists(), 'failed planning must not publish an empty matrix')
            records.pop('0.test.mjs')
            timing.write_text(json.dumps({'suites': records}))
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 0, missing.stderr)

    def test_combined_additions_expand_capacity_without_trimming_estimates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            records = {}
            for index in range(2 * FIXTURE_COUNT - 1):
                name = f'base-{index}.test.mjs'
                (tests / name).touch()
                records[name] = {'milliseconds': planner.BUDGET_MS // 2, 'serial': True}
            self.inventory(root, len(records))
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            command = [sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root), 'budget']
            for addition in ['branch-a.test.mjs', 'branch-b.test.mjs']:
                (tests / addition).touch()
                timing.write_text(json.dumps({'suites': {**records, addition: {
                    'milliseconds': planner.BUDGET_MS // 2, 'serial': True}}}))
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                (tests / addition).unlink()
            for addition in ['branch-a.test.mjs', 'branch-b.test.mjs']:
                (tests / addition).touch()
                records[addition] = {'milliseconds': planner.BUDGET_MS // 2, 'serial': True}
            timing.write_text(json.dumps({'suites': records}))
            combined = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(combined.returncode, 0, combined.stderr)
            self.assertEqual(len(json.loads(combined.stdout)), FIXTURE_COUNT + 1)
            admitted = planner.plan(root)
            receipt_paths = []
            for index, suites in enumerate(admitted, 1):
                path = root / f'receipt-{index}.json'
                path.write_text(json.dumps({'shard': index, 'suites': suites}))
                receipt_paths.append(str(path))
            checked = subprocess.run([*command[:-1], 'verify', *receipt_paths], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            incomplete = subprocess.run([*command[:-1], 'verify', *receipt_paths[:-1]], capture_output=True, text=True)
            self.assertNotEqual(incomplete.returncode, 0)
            self.assertIn(f'each of {len(admitted)} shards', incomplete.stderr)
            for index in range(2 * planner.MAX_COUNT + 1):
                name = f'cap-{index}.test.mjs'
                (tests / name).touch()
                records[name] = {'milliseconds': planner.BUDGET_MS // 2, 'serial': True}
            timing.write_text(json.dumps({'suites': records}))
            capped = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(capped.returncode, 0)
            self.assertIn(f'{planner.MAX_COUNT}-shard cap', capped.stderr)

    def test_workflow_and_consumers_share_shard_cardinality(self):
        workflow = (ROOT / '.github/workflows/unit-shards.yml').read_text()
        matrix = json.loads(subprocess.check_output(
            [sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), 'matrix'], text=True))['include']
        count = len(planner.plan())
        self.assertEqual(matrix, [{'shard': index, 'count': count} for index in range(1, count + 1)])
        self.assertIn('matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}', workflow)
        self.assertIn('python3 ops/ci_unit_shards.py matrix', workflow)
        self.assertIn('of ${{ matrix.count }}', workflow)
        self.assertIn("needs.plan.result == 'success'", workflow)
        self.assertLessEqual(count, planner.MAX_COUNT)

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
            self.assertEqual(records['new.test.mjs']['milliseconds'], planner.DEFAULT_SUITE_MS)
            self.assertEqual(planner.plan(root), [['new.test.mjs']])
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            timing.write_text(json.dumps({'suites': {'retired.test.mjs': {'milliseconds': planner.WATCHDOG_MS * 100, 'serial': False}}}))
            records, _ = planner.durations(root)
            self.assertEqual(records['new.test.mjs'], {'milliseconds': planner.DEFAULT_SUITE_MS, 'serial': True, 'unmeasured': True})
            self.assertEqual(planner.plan(root), [['new.test.mjs']])

    def test_unknown_work_cannot_consume_measured_headroom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            measured = 'known-browser.test.mjs'
            (tests / measured).touch()
            timing.write_text(json.dumps({'snapshot_template_ms': 167478, 'suites': {
                measured: {'milliseconds': 310000, 'serial': True}}}))
            self.inventory(root, 1)
            for count in (1, 10, 20):
                unknown = [f'new-{index}-browser.test.mjs' for index in range(count)]
                for file in unknown:
                    (tests / file).touch()
                shards = planner.plan(root)
                self.assertIn([measured], shards)
                self.assertEqual(sorted(file for shard in shards for file in shard),
                                 sorted([measured, *unknown]))
                self.assertLessEqual(len(shards), planner.MAX_COUNT)
                records, setup = planner.durations(root)
                records[unknown[0]]['milliseconds'] = 300000
                assigned = next(shard for shard in shards if unknown[0] in shard)
                self.assertLessEqual(planner.estimate(assigned, records, setup), planner.WATCHDOG_MS)
            for index in range(200):
                (tests / f'overflow-{index}.test.mjs').touch()
            with self.assertRaisesRegex(ValueError, '16-shard cap'):
                planner.plan(root)

    def inventory(self, root, expected):
        mcp = root / 'mcp-server'
        shutil.copy2(ROOT / 'mcp-server/suite-discovery.ts', mcp / 'suite-discovery.ts')
        (mcp / 'suite-inventory.json').write_text(json.dumps({'expected': expected}))

    def test_measured_durations_balance_and_discovery_keeps_new_suites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            records = {}
            for index, milliseconds in enumerate([90] * FIXTURE_COUNT + [10] * FIXTURE_COUNT):
                name = f'{index}.test.mjs'
                (tests / name).touch()
                records[name] = {'milliseconds': milliseconds, 'serial': True}
            timing_file = root / 'ops/config/ci-unit-durations.json'
            timing_file.parent.mkdir(parents=True)
            timing_file.write_text(json.dumps({'suites': records}))
            self.inventory(root, len(list(tests.glob("*.test.mjs"))))
            command = [sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root), 'plan', '--count', str(FIXTURE_COUNT)]
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            shards = json.loads(result.stdout)
            self.assertEqual(sorted(sum(records[file]['milliseconds'] for file in shard) for shard in shards), [100] * FIXTURE_COUNT)
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
            shutil.copy2(ROOT / 'mcp-server/run-tests.ts', root / 'mcp-server/run-tests.ts')
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
            timing = root / 'ops/config/ci-unit-durations.json'
            timing.parent.mkdir(parents=True)
            timing.write_text(json.dumps({'suites': {file.name: {'milliseconds': 1, 'serial': True}
                for file in tests.glob('*.test.mjs')}}))
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
