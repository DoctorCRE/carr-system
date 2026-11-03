#!/usr/bin/env python3
"""Check incoming-email summaries and resumable backfill with fake models."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
import json
from datetime import datetime, timezone
from lib import email_summary


class SummaryTests(unittest.TestCase):
    def test_strip_signature_history_and_transport(self):
        body = 'Please review the terms.\n\nBest,\nSynthetic Sender\nConfidential notice\nOn Monday, Old wrote:\nOld request.'
        seen = []
        result = email_summary.generate({'body': body}, lambda text: seen.append(text) or {
            'text': 'The sender asks for a review of the terms.', 'evidence': 'Please review the terms.'})
        self.assertEqual(seen, ['Please review the terms.'])
        self.assertEqual(result['status'], 'available')

    def test_refusal_failure_incomplete_and_ungrounded_are_unavailable(self):
        for email, model in [
            ({'body': 'Please review.'}, lambda _: {}),
            ({'body': 'Please review.'}, lambda _: {'text': 'Invented.', 'evidence': 'Invented.'}),
            ({'body': 'Please review.', 'email_context': {'body_truncated': True}}, lambda _: self.fail('model called')),
            ({'body': ''}, lambda _: self.fail('model called')),
        ]:
            self.assertEqual(email_summary.generate(email, model)['status'], 'unavailable')

    def test_decoded_transport_evidence_keeps_literal_raw_provenance(self):
        body = 'Please=20review=20the=20terms=20&amp;=20confirm.'
        result = email_summary.generate({'body': body}, lambda text: {
            'text': 'The sender asks for a review and confirmation.',
            'evidence': 'Please review the terms & confirm.'})
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['provenance']['evidence'], body)

    def test_captured_subject_and_sender_reach_model_as_fenced_data(self):
        seen = []
        for subject in ['Re: Approve the purchase?', 'Re: Cancel the purchase?']:
            result = email_summary.generate({'body': 'Yes.', 'email_context': {
                'subject': subject, 'sender': {'name': 'Synthetic sender', 'email': 'sender@example.test'}}},
                lambda text: seen.append(text) or {'text': 'The sender agrees.', 'evidence': subject})
            self.assertEqual(result['status'], 'available')
            self.assertEqual(result['provenance']['evidence'], subject)
        self.assertNotEqual(seen[0], seen[1])
        self.assertIn('Synthetic sender', seen[0])
        self.assertIn('sender@example.test', seen[0])
        self.assertIn('<incoming_email>', seen[0])

    def test_sentence_limit_is_enforced(self):
        for text, status in [('Please review. Please review. Please review.', 'unavailable'),
                             ('Please review! Confirm? Wait.', 'unavailable'),
                             ('Please review. Please confirm.', 'available'),
                             ('Dr. Smith requests review of the $3.50 charge.', 'available'),
                             ('Please review', 'available')]:
            with self.subTest(text=text):
                result = email_summary.generate({'body': 'Please review.'}, lambda _: {
                    'text': text, 'evidence': 'Please review.'})
                self.assertEqual(result['status'], status)

    def test_exception_is_honest(self):
        def fails(_):
            raise RuntimeError('synthetic failure')
        result = email_summary.generate({'body': 'Please review.'}, fails)
        self.assertIsNone(result['text'])
        self.assertEqual(result['provenance']['reason'], 'generation_failed')

    def test_forward_is_preserved_as_data(self):
        seen = []
        email_summary.generate({'body': 'FYI\n--- Forwarded message ---\nFrom: Person\nPlease review.'},
            lambda text: seen.append(text) or {})
        self.assertIn('Please review.', seen[0])

    def test_signature_before_forward_does_not_discard_forwarded_message(self):
        body = 'FYI\nBest,\nSynthetic sender\n--- Forwarded message ---\nFrom: Synthetic landlord\nPlease send insurance evidence.'
        result = email_summary.generate({'body': body}, lambda text: {
            'text': 'The forwarded message asks for insurance evidence.',
            'evidence': 'Please send insurance evidence.'} if 'Please send insurance evidence.' in text else {})
        self.assertEqual(result['status'], 'available')

    def test_injected_clock_bounds_backfill_and_preserves_cursor(self):
        calls = []
        def carr(verb, args):
            calls.append(verb)
            return {'emails': [{'activity_id': 'source', 'source_hash': 'hash'}], 'more': False}
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = email_summary.backfill(carr, lambda _: self.fail('model called'), now=now,
            clock=lambda: datetime(2026, 1, 1, 1, tzinfo=timezone.utc), after='previous')
        self.assertEqual(result['next_cursor'], 'previous')
        self.assertTrue(result['more'])
        self.assertEqual(calls, ['email-router-queue'])

    def test_synthetic_summary_eval(self):
        cases = [json.loads(line) for line in (Path(__file__).resolve().parents[1] / 'evals/email-followup-summary.jsonl').read_text().splitlines()]
        old_overlong = new_overlong = grounded = clean = 0
        for case in cases:
            with self.subTest(case=case['id']):
                result = email_summary.generate(case['email'], lambda text: case['output'])
                self.assertEqual(result['status'], case['expected_status'])
                old_overlong += len(case['email']['body'][:2000]) > 300
                new_overlong += len(result['text'] or '') > 300
                if result['status'] == 'available':
                    self.assertIn(result['provenance']['evidence'], case['email']['body'])
                    grounded += 1
                    for boilerplate in case['boilerplate_absent']:
                        self.assertNotIn(boilerplate, result['text'])
                    clean += 1
        print(f'Synthetic fixed-output eval: cases={len(cases)}, raw_over_300={old_overlong}, summary_over_300={new_overlong}, literal_evidence={grounded}, boilerplate_clean={clean}. No model-quality inference.')

    def test_backfill_dry_run_and_claim_reuse(self):
        calls = []
        def carr(verb, args):
            calls.append((verb,args))
            if verb == 'email-router-queue':
                return {'emails': [{'activity_id': 'source', 'source_hash': 'hash', 'body': 'Please review.'}], 'next_cursor': 'next', 'more': True}
            return {'generate': False}
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dry = email_summary.backfill(carr, lambda _: self.fail('model called'), now=now, dry_run=True)
        self.assertEqual(dry['activity_ids'], ['source'])
        self.assertEqual(dry['candidates'], 1)
        self.assertEqual(len(calls), 1)
        done = email_summary.backfill(carr, lambda _: self.fail('model called'), now=now)
        self.assertEqual(done['reused'], 1)
        self.assertEqual(done['next_cursor'], 'next')


if __name__ == '__main__':
    unittest.main()
