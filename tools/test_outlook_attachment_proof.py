#!/usr/bin/env python3
"""Synthetic checks of the real read-only first-send verifier."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.loadpy import load_module_from_path
from lib import outlook_send as send

proof = load_module_from_path('attachment_proof', str(Path(__file__).with_name('outlook-first-send-proof.py')))


class AttachmentProofTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(account='partner@example.test', partner_cc='copy@example.test')
        self.item = send.SentRow(self.settings.account, 'client@example.test;copy@example.test',
                                 'Synthetic subject', 1800000000, 'sent-1', send.body_fingerprint('Synthetic body'))
        self.snapshot = send.Snapshot((self.item,), ())
        self.row = dict(proposal_id='11111111-1111-4111-8111-111111111111', version=3,
                        status='sent', partner_submitted=True, account=self.settings.account,
                        partner_cc=self.settings.partner_cc, to=['client@example.test'], cc=[self.settings.partner_cc],
                        subject=self.item.subject, body='Synthetic body', attachments=[],
                        send_result=dict(status='sent', sender_account=self.settings.account,
                                         sent_item_id=self.item.item_id, sent_at=self.item.sent_at))
        self.row['correspondence'] = [dict(send_id=self.row['proposal_id'], sent_item_id=self.item.item_id,
            sent_at=self.item.sent_at, sender=dict(email=self.settings.account),
            recipients=[dict(email='client@example.test', kind='to'), dict(email=self.settings.partner_cc, kind='cc')],
            attachments=[])]
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"outlook-outbox|{self.row['proposal_id']}|2"))
        self.ledger = [dict(key=key, fingerprint='synthetic-fingerprint', state=state, at=self.item.sent_unix,
                            sent_item_id=self.item.item_id, sent_at=self.item.sent_at) for state in ('pressing', 'done')]

    def run_proof(self):
        return proof.prove(self.row, self.ledger, self.snapshot, self.settings)

    def attach(self):
        self.row['attachments'] = [dict(path='/synthetic/private.pdf', sha256=d * 64, size=i + 1)
                                   for i, d in enumerate(('a', 'b'))]
        self.row['correspondence'][0]['attachments'] = [dict(sha256=a['sha256'], size=a['size'])
                                                       for a in self.row['attachments']]
        for row in self.ledger:
            row['attachments'] = dict(count=2, sha256=['a' * 64, 'b' * 64])

    def test_zero_attachments_preserves_proof(self):
        result = self.run_proof()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['checks']['submission_manifest_verified'])
        self.assertEqual(result['schema'], 'outlook-first-send-proof.v2')

    def test_matching_manifest_cannot_prove_unobserved_sent_bytes(self):
        self.attach()
        result = self.run_proof()
        self.assertTrue(result['checks']['submission_manifest_verified'], result)
        self.assertIsNone(result['checks']['sent_attachment_content_verified'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'sent_attachment_content_unavailable')
        for private in ('private.pdf', 'a' * 64, 'client@example.test', 'Synthetic body', 'sent-1'):
            self.assertNotIn(private, json.dumps(result))

    def test_digest_multiplicity_order_and_corruption(self):
        self.attach()
        self.ledger[0]['attachments']['sha256'].reverse()
        self.assertTrue(self.run_proof()['checks']['submission_manifest_verified'])
        for index in (0, 1):
            for digests in (['a' * 64], ['a' * 64, 'c' * 64], ['a' * 64, 'b' * 64, 'c' * 64], ['a' * 64] * 2):
                with self.subTest(index=index, digests=digests):
                    ledger = copy.deepcopy(self.ledger)
                    self.ledger[index]['attachments'] = dict(count=len(digests), sha256=digests)
                    result = self.run_proof()
                    self.assertFalse(result['checks']['submission_manifest_verified'])
                    self.assertEqual(result['reason'], 'submission_manifest_mismatch')
                    self.ledger = ledger

    def test_missing_manifest_and_false_count(self):
        self.attach()
        for value in (None, {}, dict(count=1, sha256=['a' * 64, 'b' * 64])):
            self.ledger[0]['attachments'] = value
            self.assertFalse(self.run_proof()['checks']['submission_manifest_verified'])

    def test_actual_send_identity_and_correspondence(self):
        self.attach()
        original = copy.deepcopy(self.row)
        for change in (dict(send_id='another-proposal'), dict(sent_item_id='another-item'),
                       dict(attachments=[]), dict(attachments=[dict(sha256='c' * 64, size=1)])):
            self.row['correspondence'][0].update(change)
            self.assertFalse(self.run_proof()['checks']['submission_manifest_verified'])
            self.row = copy.deepcopy(original)
        self.row['correspondence'] = []
        self.assertFalse(self.run_proof()['ok'])
        self.assertFalse(self.run_proof()['checks']['submission_manifest_verified'])

    def test_duplicate_press_and_cross_key_press(self):
        for key in (self.ledger[0]['key'], 'another-key'):
            duplicate = dict(self.ledger[0], key=key)
            self.assertFalse(proof.prove(self.row, self.ledger + [duplicate], self.snapshot, self.settings)['ok'])

    def test_done_fingerprint_must_match_press(self):
        self.attach()
        self.ledger[1]['fingerprint'] = 'another-manifest'
        self.assertFalse(self.run_proof()['checks']['submission_manifest_verified'])

    def test_missing_selected_evidence_and_malformed_submission(self):
        self.attach()
        for ledger in ([], [self.ledger[0]], [self.ledger[1]],
                       [dict(r, key='another-key') for r in self.ledger]):
            result = proof.prove(self.row, ledger, self.snapshot, self.settings)
            self.assertFalse(result['ok'])
            self.assertFalse(result['checks']['submission_manifest_verified'])
        for manifest in (None, {}, [dict(sha256='invalid', size=1)],
                         [dict(sha256='a' * 64, size=True)]):
            self.row['attachments'] = manifest
            self.assertFalse(self.run_proof()['checks']['submission_manifest_verified'])

    def test_attachment_free_invariants_and_reconciliation(self):
        self.assertTrue(self.run_proof()['ok'])
        self.row['version'] = 4
        self.row['send_result']['reconciled_from_uncertainty'] = True
        self.assertTrue(self.run_proof()['ok'])
        original = copy.deepcopy(self.row)
        for change in (dict(partner_submitted=False), dict(status='approved_to_send'),
                       dict(account='other@example.test'), dict(cc=[]), dict(body='Changed body')):
            self.row.update(change)
            self.assertFalse(self.run_proof()['ok'])
            self.row = copy.deepcopy(original)
        self.snapshot = send.Snapshot((), ())
        self.assertFalse(self.run_proof()['ok'])

    def test_no_submission(self):
        result = proof.prove(None, [], send.Snapshot((), ()), self.settings)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'no_partner_submission')
        self.assertEqual(result['schema'], 'outlook-first-send-proof.v2')


if __name__ == '__main__':
    unittest.main()
