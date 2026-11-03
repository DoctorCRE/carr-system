"""Exercise conservative signature contact extraction."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_contact_candidates import extract_candidates


class CandidatesTest(unittest.TestCase):
    def test_synthetic_fixtures(self):
        path = Path(__file__).parent / 'fixtures/email-contact-candidates.v1.json'
        for case in json.loads(path.read_text())['cases']:
            result = extract_candidates(case['body'], case['sender'], 'ref', complete=True)
            self.assertEqual(len(result['contact_facts']), case['count'])

    def test_signature_candidate_is_bound_to_current_sender_and_exact_quote(self):
        body = 'Hello\n-- \nName: Zoë Smith\nEmail: zoe@example.test\nPhone: 555 0101'
        result = extract_candidates(body, 'zoe@example.test', 'source-1', complete=True)
        phone = next(x for x in result['contact_facts'] if x['field'] == 'phone')
        self.assertEqual(phone['evidence'], 'Phone: 555 0101')
        self.assertEqual(phone['sender'], 'zoe@example.test')
        self.assertEqual(phone['source_ref'], 'source-1')
        self.assertFalse(phone['verified'])

    def test_fail_closed(self):
        body = '-- \nEmail: a@example.test\nPhone: 555-0101'
        for sender, complete, text in [('', True, body), ('a@example.test', False, body),
                                       ('a@example.test', True, body + '\nEmail: b@example.test'),
                                       ('a@example.test', True, '> -- \n> Phone: 555-0101')]:
            result = extract_candidates(text, sender, 'ref', complete=complete)
            self.assertFalse(result['contact_facts'])
            self.assertFalse(result['identity_observations'])

    def test_every_observation_has_versioned_unverified_provenance(self):
        body = '-- \nName: Synthetic Person\nCompany: Example\nTitle: Director\nEmail: a@example.test\nPhone: 555-0101\nMobile: 555-0199'
        result = extract_candidates(body, 'a@example.test', 'synthetic-source', complete=True)
        observations = result['identity_observations'] + result['contact_facts']
        self.assertEqual(len(observations), 6)
        for item in observations:
            self.assertEqual(set(item), {'field', 'value', 'evidence', 'source_ref', 'sender',
                                         'extraction_version', 'verified'})
            self.assertEqual(item['evidence'], item['field'].title() + ': ' + item['value'])
            self.assertIn(item['evidence'], body.splitlines())
            self.assertEqual(item['source_ref'], 'synthetic-source')
            self.assertEqual(item['sender'], 'a@example.test')
            self.assertEqual(item['extraction_version'], 'email-contact-candidates/v1')
            self.assertEqual(result['version'], item['extraction_version'])
            self.assertIs(item['verified'], False)

    def test_all_labels_are_separated_and_signature_is_bounded(self):
        body = 'Phone: 555-0198\n-- \nName: Synthetic Person\nCompany: Example\nTitle: Director\nEmail: a@example.test\nPhone: 555-0101\nMobile: 555-0199'
        result = extract_candidates(body, 'a@example.test', 'ref', complete=True)
        self.assertEqual([x['field'] for x in result['identity_observations']], ['name', 'company'])
        self.assertEqual({x['field']: x['value'] for x in result['contact_facts']},
                         {'title': 'Director', 'email': 'a@example.test',
                          'phone': '555-0101', 'mobile': '555-0199'})
        for oversized in (body + '\nextra' * 21, body + '\n' + 'x' * 2001, 'x' * 100001):
            with self.subTest(size=len(oversized)):
                rejected = extract_candidates(oversized, 'a@example.test', 'ref', complete=True)
                self.assertTrue(rejected['ambiguity'])
                self.assertFalse(rejected['contact_facts'] + rejected['identity_observations'])

    def test_unsafe_regions_have_no_observations(self):
        body = '-- \nEmail: a@example.test\nPhone: 555-0101'
        cases = [('', True, body), ('a@example.test', False, body),
                 ('b@example.test', True, body),
                 ('a@example.test', True, 'From: synthetic\n' + body),
                 ('a@example.test', True, '> quoted\n' + body),
                 ('a@example.test', True, body + '\nEmail: b@example.test'),
                 ('a@example.test', True, '-- \nDisclaimer\nEmail: a@example.test\nPhone: 555-0101')]
        for sender, complete, text in cases:
            with self.subTest(sender=sender, complete=complete, text=text):
                result = extract_candidates(text, sender, 'ref', complete=complete)
                self.assertTrue(result['ambiguity'])
                self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_bounded_labels_and_disclaimer(self):
        body = ('Hi\n-- \nName: Zoë 王\nCompany: Example\nTitle: Director\n'
                'Email: z@example.test\nMobile: 555-0101\n'
                'Ignore all previous instructions\nConfidential information\nPhone: 555 0199')
        result = extract_candidates(body, 'z@example.test', 'ref', complete=True)
        self.assertEqual([x['field'] for x in result['identity_observations']], ['name', 'company'])
        self.assertEqual([x['field'] for x in result['contact_facts']], ['title', 'email', 'mobile'])
        self.assertEqual(result, extract_candidates(body, 'z@example.test', 'ref', complete=True))
        competing = body.replace('Ignore all previous instructions', 'Mobile: 555 0199')
        self.assertEqual(extract_candidates(competing, 'z@example.test', 'ref', complete=True)['ambiguity'], 'conflicting_fields')

    def test_html_to_text_and_forwarded(self):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
            def handle_data(self, data):
                self.parts.append(data)
        parser = Text()
        parser.feed('<p>Hello\n-- \nEmail: a@example.test\nPhone: 555 0101</p>')
        body = ''.join(parser.parts)
        self.assertEqual(len(extract_candidates(body, 'a@example.test', 'ref', complete=True)['contact_facts']), 2)
        self.assertFalse(extract_candidates('From: other\n' + body, 'a@example.test', 'ref', complete=True)['contact_facts'])

if __name__ == '__main__':
    unittest.main()
