#!/usr/bin/env python3
"""Prove admission outcomes through real subprocess entry barriers."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import disposable_pg_fixture as pg

SCRIPT = '''
import sys, json
from pathlib import Path
from lib import disposable_pg_fixture as pg
pg.LOCK_PATH = Path(sys.argv[1])
try:
    with pg.postgres_fixture_group(demand=int(sys.argv[2]), timeout=.3) as lease:
        with pg.postgres_fixture_group(demand=1):
            pass
        print(json.dumps(lease.receipt), flush=True)
        sys.stdin.read()
except pg.CapacityUnavailable as exc:
    print(str(exc), flush=True)
    sys.exit(75)
'''


class Admission(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.lock = Path(self.directory.name) / 'budget'
        self.children = []

    def tearDown(self):
        for child in self.children:
            if not child.stdin.closed:
                child.stdin.close()
        for child in self.children:
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()
        self.directory.cleanup()

    def claimant(self, demand=2):
        child = subprocess.Popen([sys.executable, '-u', '-c', SCRIPT, str(self.lock), str(demand)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def receipt(self, child):
        self.assertTrue(select.select([child.stdout], [], [], 3)[0], 'admission has no finite outcome')
        line = child.stdout.readline()
        self.assertTrue(line, 'no admission receipt')
        return json.loads(line)

    def test_two_claimants_enter_before_either_releases(self):
        a, b = self.claimant(), self.claimant()
        first, second = self.receipt(a), self.receipt(b)
        self.assertEqual(first['state'], 'admitted')
        self.assertEqual(second['state'], 'admitted')
        self.assertTrue(set(first['slots']).isdisjoint(second['slots']))

    def test_capacity_deadline_nested_hold_and_next_admission(self):
        holders = [self.claimant() for _ in range(2)]
        for child in holders:
            self.assertEqual(self.receipt(child)['demand'], 2)
        extra = self.claimant()
        receipt = self.receipt(extra)
        self.assertEqual(extra.wait(timeout=2), 75)
        self.assertEqual(receipt['state'], 'capacity_unavailable')
        self.assertGreaterEqual(receipt['wait_seconds'], .25)
        self.assertLess(receipt['wait_seconds'], 1)
        self.assertTrue(all(c.poll() is None for c in holders))
        holders[0].stdin.close()
        self.assertEqual(holders[0].wait(timeout=3), 0)
        self.assertEqual(self.receipt(self.claimant())['state'], 'admitted')

    def test_over_capacity_demand_refuses(self):
        child = self.claimant(5)
        self.assertEqual(self.receipt(child)['state'], 'capacity_unavailable')
        self.assertEqual(child.wait(timeout=2), 75)

    def test_legacy_holder_prevents_independent_budgets(self):
        import fcntl
        with self.lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            child = self.claimant()
            self.assertEqual(self.receipt(child)['state'], 'capacity_unavailable')
            self.assertEqual(child.wait(timeout=2), 75)

    def test_policy_mismatch_refuses(self):
        with pg.postgres_fixture_group(demand=1, lock_path=self.lock):
            policy = self.lock.with_suffix('.admission-v2') / 'policy.json'
            policy.write_text('{}')
            child = self.claimant()
            self.assertEqual(self.receipt(child)['state'], 'capacity_unavailable')
            self.assertEqual(child.wait(timeout=2), 75)


if __name__ == '__main__':
    unittest.main()
