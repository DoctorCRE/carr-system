#!/usr/bin/env python3
"""Exercise the shard receipt command with omitted and empty execution."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShardCoverage(unittest.TestCase):
    def test_receipts_require_every_suite_and_nonempty_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'mcp-server/test'
            tests.mkdir(parents=True)
            for n in range(32):
                (tests / f'suite{n}.test.mjs').write_text('')
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
