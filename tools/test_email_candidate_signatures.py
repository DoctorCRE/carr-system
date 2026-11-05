"""Candidate evidence source identity and raw revision checks."""
import sys
import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_candidate_signatures import observations
from lib.email_contact_review import digest
from lib.outlook_intake import Run, CarrError, _hash


def mail(ident, keys=None, **extra):
    return {'ident': ident, 'local_keys': keys or [], 'sender': 'sender@synthetic.test',
            'subject': 'Synthetic', 'sent_unix': 1, 'occurred_at': '2026-01-01T00:00:00Z',
            'message_id': 'synthetic-message', 'full': True, 'signature_html': None,
            'body': '--\nName: Synthetic Sender\nEmail: sender@synthetic.test\nPhone: 555-010-1234',
            'text': '', **extra}


class CandidateSources(unittest.TestCase):
    def test_terminal_source_refusal_is_acknowledged_once(self):
        calls = []
        def refuse(verb, args):
            calls.append(verb)
            raise CarrError(code='email_not_live')
        with tempfile.TemporaryDirectory() as directory:
            run = Run(SimpleNamespace(carr=refuse), {}, datetime.now(timezone.utc), Path(directory),
                      SimpleNamespace(timezone=timezone.utc))
            args = {'idempotency_key': 'synthetic-request'}
            self.assertTrue(run.write('observe-email-candidate-signatures', args, 'synthetic'))
            self.assertTrue(run.write('observe-email-candidate-signatures', args, 'synthetic'))
            self.assertEqual(len(calls), 1)
            self.assertEqual(run.state['requests']['synthetic-request']['disposition'], 'source_invalid')

    def test_candidate_stage_caps_requests_and_stops_after_two_failures(self):
        for fail, expected in [(False, 20), (True, 2)]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                calls, mails, queued = [], [], {}
                def append(verb, args):
                    calls.append(verb)
                    if fail:
                        raise CarrError(code='timeout')
                    return {'ok': True, 'inserted': 1}
                for index in range(25):
                    address = f'sender{index}@synthetic.test'
                    source = mail(str(index), message_id=str(index), metadata_incomplete=False,
                                  sender=address, body=mail('')['body'].replace('sender@synthetic.test', address))
                    mails.append(source)
                    queued[str(index)] = {'sender': _hash(address)}
                settings = SimpleNamespace(timezone=timezone.utc, owner='joe', account='owner@internal.test',
                                           folder_account_id=7, internal_domain='internal.test')
                run = Run(SimpleNamespace(carr=append), {'mail_unresolved': queued}, datetime.now(timezone.utc), Path(directory), settings)
                run.candidate_signatures(mails, SimpleNamespace(signature_contact=lambda sender: None))
                self.assertEqual(len(calls), expected)
                self.assertTrue(run.state['reads_due'])

    def test_keyless_and_keyed_representations_share_one_observation(self):
        state = {}
        first = observations([mail('keyless')], state, digest, 300)['sender@synthetic.test'][0]
        second = observations([mail('keyed', ['7'])], state, digest, 300)['sender@synthetic.test'][0]
        self.assertEqual(first['source_id'], second['source_id'])
        self.assertEqual(first['raw_digest'], second['raw_digest'])
        self.assertEqual(second['item_ids'], ['keyed', 'keyless'])

    def test_conflicting_store_keys_are_distinct_sources(self):
        result = observations([mail('first', ['7']), mail('second', ['8'])], {}, digest, 300)
        self.assertEqual(len(result['sender@synthetic.test']), 2)

    def test_first_ambiguous_signature_is_retained_as_unverified_evidence(self):
        source = mail('ambiguous', body=mail('')['body'].replace('sender@synthetic.test', 'other@synthetic.test'))
        result = observations([source], {}, digest, 300)['sender@synthetic.test']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['extraction']['ambiguity'], 'competing_identity')

    def test_changed_keyless_representation_holds_retained_candidate(self):
        state = {}
        first = observations([mail('keyless')], state, digest, 300)['sender@synthetic.test'][0]
        revised = mail('new-keyless', body=mail('keyless')['body'].replace('1234', '5678'))
        changed = observations([revised], state, digest, 300)['sender@synthetic.test'][0]
        self.assertEqual(first['source_id'], changed['source_id'])
        self.assertEqual(changed['extraction']['ambiguity'], 'competing_source_representation')

    def test_html_revision_or_lost_completeness_is_not_an_old_signature(self):
        state = {}
        first = observations([mail('first')], state, digest, 300)['sender@synthetic.test'][0]
        changed = observations([mail('first', signature_html='<blockquote>Changed</blockquote>')], state, digest, 300)['sender@synthetic.test'][0]
        self.assertEqual(first['source_id'], changed['source_id'])
        self.assertNotEqual(first['raw_digest'], changed['raw_digest'])
        self.assertTrue(changed['extraction']['ambiguity'])
        partial = observations([mail('first', full=False)], state, digest, 300)['sender@synthetic.test'][0]
        self.assertNotEqual(first['raw_digest'], partial['raw_digest'])
        absent = observations([mail('first', body=None, full=False)], state, digest, 300)['sender@synthetic.test'][0]
        self.assertEqual(absent['extraction']['ambiguity'], 'incomplete_or_oversized')


if __name__ == '__main__':
    unittest.main()
