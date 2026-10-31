#!/usr/bin/env python3
"""Exercise the shard receipt command with omitted and empty execution."""
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShardCoverage(unittest.TestCase):
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
            for index, milliseconds in enumerate((90, 80, 70, 60, 40, 30, 20, 10)):
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
            self.assertEqual(sorted(sum(records[file]['milliseconds'] for file in shard) for shard in shards), [100] * 4)
            (tests / 'new.test.mjs').touch()
            self.inventory(root, 9)
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            self.assertEqual(sorted(file for shard in json.loads(result.stdout) for file in shard), sorted([*records, 'new.test.mjs']))
            log = root / 'incomplete.log'
            log.write_text('suite-timing: 0.test.mjs 90ms\n')
            result = subprocess.run([sys.executable, str(ROOT / 'ops/ci_unit_shards.py'), '--root', str(root),
                                     'timings', str(log), '--source-run', '1', '--output', str(timing_file)],
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing=', result.stderr)

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
