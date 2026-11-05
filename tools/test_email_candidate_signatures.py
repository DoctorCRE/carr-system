"""Candidate evidence source identity and raw revision checks."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_candidate_signatures import observations
from lib.email_contact_review import digest


def mail(ident, keys=None, **extra):
    return {'ident': ident, 'local_keys': keys or [], 'sender': 'sender@synthetic.test',
            'subject': 'Synthetic', 'sent_unix': 1, 'occurred_at': '2026-01-01T00:00:00Z',
            'message_id': 'synthetic-message', 'full': True, 'signature_html': None,
            'body': '--\nName: Synthetic Sender\nEmail: sender@synthetic.test\nPhone: 555-010-1234',
            'text': '', **extra}


class CandidateSources(unittest.TestCase):
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

    def test_html_revision_or_lost_completeness_is_not_an_old_signature(self):
        state = {}
        first = observations([mail('first')], state, digest, 300)['sender@synthetic.test'][0]
        changed = observations([mail('first', signature_html='<blockquote>Changed</blockquote>')], state, digest, 300)['sender@synthetic.test'][0]
        self.assertEqual(first['source_id'], changed['source_id'])
        self.assertNotEqual(first['raw_digest'], changed['raw_digest'])
        self.assertTrue(changed['extraction']['ambiguity'])
        partial = observations([mail('first', full=False)], state, digest, 300)['sender@synthetic.test'][0]
        self.assertNotEqual(first['raw_digest'], partial['raw_digest'])


if __name__ == '__main__':
    unittest.main()
