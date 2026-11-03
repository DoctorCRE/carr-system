#!/usr/bin/env python3
"""Synthetic delivery-status evidence tests."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.email_delivery_failure import parse_report


class DeliveryFailureTest(unittest.TestCase):
    def test_explicit_evidence_fixture_matrix(self):
        cases = json.loads((ROOT / 'ops/fixtures/email-delivery-failures.v1.json').read_text())['cases']
        for case in cases:
            with self.subTest(case=case['name']):
                result = parse_report(case['body'], full=True)
                self.assertEqual(result is not None, case['accepted'])
                if result:
                    self.assertEqual(result.original_id, 'original@example.com')
                    self.assertEqual(result.status, '5.7.520')

    def test_incomplete_and_ambiguous_reply_identity(self):
        body = json.loads((ROOT / 'ops/fixtures/email-delivery-failures.v1.json').read_text())['cases'][0]['body']
        self.assertIsNone(parse_report(body, full=False))
        without_id = body.replace('Original-Message-ID: <original@example.com>\n', '')
        self.assertEqual(parse_report(without_id, full=True, in_reply_to='<parent@example.com>').original_id,
                         'parent@example.com')
        self.assertIsNone(parse_report(without_id, full=True, in_reply_to='<one@example.com> <two@example.com>'))
        self.assertIsNone(parse_report(body, full=True, in_reply_to='<other@example.com>'))
        self.assertIsNone(parse_report(body * 1000, full=True))
        self.assertIsNone(parse_report(body.replace('550 5.7.520', '250 2.0.0'), full=True))


if __name__ == '__main__':
    unittest.main()
