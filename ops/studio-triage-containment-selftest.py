#!/usr/bin/env python3
"""Containment decision chart exercised through the actual observer CLI."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ContainmentTest(unittest.TestCase):
    def run_receipt(self, receipt):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt'
            path.write_text('outlook-triage-receipt ' + json.dumps(receipt))
            return subprocess.run([sys.executable, str(ROOT / 'ops/studio-triage-containment-observe.py'),
                                   '--receipt-file', str(path), '--runtime-sha', 'a' * 40],
                                  capture_output=True, text=True, timeout=10)

    def receipt(self):
        return dict(state='skipped', reason='capability_not_verified', runtime_sha='a' * 40,
                    counts={'run': dict(snapshot_validated=True, observed=1120,
                                        moved=0, held=True, exit_code=78)})

    def assert_refused(self, receipt):
        result = self.run_receipt(receipt)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('BLOCKED', result.stdout)

    def test_held_validated_nonempty_zero_moves_passes(self):
        result = self.run_receipt(self.receipt())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('OBSERVED', result.stdout)

    def test_bounded_reader_refuses_missing_malformed_or_multiple_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt'
            command = [sys.executable, str(ROOT / 'ops/studio-triage-containment-observe.py'),
                       '--receipt-file', str(path), '--runtime-sha', 'a' * 40]
            for raw in [None, b'x' * 1048577, b'outlook-triage-receipt {', b'\xff',
                        b'outlook-outbox-receipt {}',
                        b'outlook-triage-receipt {}\noutlook-triage-receipt {}']:
                if raw is not None:
                    path.write_bytes(raw)
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('BLOCKED', result.stdout)

    def test_registered_source_binding_and_declaration(self):
        import hashlib
        contract = json.loads((ROOT / 'contracts/production-proof-plan.v1.json').read_text())
        runner = contract['x-runners']['studio-triage-containment.v1']
        self.assertEqual(runner['path'], 'ops/studio-triage-containment-observe.py')
        self.assertEqual(runner['targets'], ['studio'])
        self.assertEqual(runner['sources'], {runner['path']:
                         hashlib.sha256((ROOT / runner['path']).read_bytes()).hexdigest()})
        self.assertEqual(contract['x-runners']['studio-run-receipt.v1']['revision'],
                         'f699e1859dc87e4122501bbaba35a312607452a7')
        requirement = json.loads((ROOT / 'proofs/requirements/wr823-s2-producer-containment.json').read_text())
        self.assertEqual(requirement['assertions'][0]['runner'], 'studio-triage-containment.v1')

    def test_each_false_predicate_refuses(self):
        for field, values in dict(snapshot_validated=[False, 1, 'true', None],
                                  observed=[0, -1, True, '1120', None],
                                  moved=[1, -1, False, '0', None],
                                  held=[False, 1, 'true', None],
                                  exit_code=[0, 1, 77, 79, True, '78', None]).items():
            for value in values:
                with self.subTest(field=field, value=value):
                    receipt = self.receipt()
                    receipt['counts']['run'][field] = value
                    self.assert_refused(receipt)

    def test_every_missing_field_refuses(self):
        for field in self.receipt()['counts']['run']:
            with self.subTest(field=field):
                receipt = self.receipt()
                del receipt['counts']['run'][field]
                self.assert_refused(receipt)

    def test_successful_moves_while_held_refuses(self):
        receipt = self.receipt()
        receipt['state'] = 'succeeded'
        receipt['counts']['run'].update(exit_code=0, moved=1)
        self.assert_refused(receipt)

    def test_wrong_identity_state_or_reason_refuses(self):
        for field, value in [('runtime_sha', 'b' * 40), ('runtime_sha', 'a' * 12),
                             ('state', 'succeeded'), ('state', 'timed_out'),
                             ('reason', 'window_closed')]:
            with self.subTest(field=field, value=value):
                receipt = self.receipt()
                receipt[field] = value
                self.assert_refused(receipt)
        for receipt in [None, [], {}, {'counts': []}]:
            self.assert_refused(receipt)


if __name__ == '__main__':
    unittest.main()
