"""Exercise conservative signature contact extraction."""
import json
import random
import sys
import textwrap
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
            if 'ambiguity' in case:
                self.assertEqual(result['ambiguity'], case['ambiguity'])
                self.assertFalse(result['identity_observations'])

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
        self.assertTrue(result['ambiguity'])
        self.assertFalse(result['identity_observations'] + result['contact_facts'])
        self.assertEqual(result, extract_candidates(body, 'z@example.test', 'ref', complete=True))
        competing = body.replace('Ignore all previous instructions', 'Mobile: 555 0199')
        self.assertTrue(extract_candidates(competing, 'z@example.test', 'ref', complete=True)['ambiguity'])

    def test_reviewer_assistant_block_does_not_supply_sender_phone(self):
        body = ('-- \nName: Alice Synthetic\nEmail: alice@example.test\n\n'
                'Assistant contact:\nBob Synthetic\nbob@example.test\nPhone: 555-0199')
        result = extract_candidates(body, 'alice@example.test', 'synthetic-source', complete=True)
        self.assertTrue(result['ambiguity'])
        self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_reviewer_disclaimer_does_not_supply_sender_phone(self):
        body = ('-- \nName: Alice Synthetic\nEmail: alice@example.test\n\n'
                'This email and any attachments are confidential. For assistance contact our helpdesk.\n'
                'Phone: 555-0199')
        result = extract_candidates(body, 'alice@example.test', 'synthetic-source', complete=True)
        self.assertTrue(result['ambiguity'])
        self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_reviewer_forwarded_block_does_not_supply_sender_phone(self):
        body = ('-- \nEmail: alice@example.test\n\nBegin forwarded message:\n'
                'Bob Synthetic\nbob@example.test\nPhone: 555-0199')
        result = extract_candidates(body, 'alice@example.test', 'synthetic-source', complete=True)
        self.assertEqual([x['field'] for x in result['contact_facts']], ['email'])

    def test_complete_entity_rejects_unexplained_transitions(self):
        sender_block = '-- \nEmail: alice@example.test\nPhone: 555-0101'
        for transition in ('', 'Bob Synthetic', 'Assistant contact:',
                           'Other details follow', 'This message is private.',
                           'Begin forwarded message:', 'On Monday Bob wrote:', '> quoted',
                           'Earlier conversation:\nBob Synthetic'):
            with self.subTest(transition=transition):
                result = extract_candidates(sender_block + '\n' + transition + '\nMobile: 555-0199',
                                            'alice@example.test', 'synthetic-source', complete=True)
                if transition in ('Begin forwarded message:', 'On Monday Bob wrote:', '> quoted'):
                    self.assertEqual([(x['field'], x['value']) for x in result['contact_facts']],
                                     [('email', 'alice@example.test'), ('phone', '555-0101')])
                elif not transition:
                    self.assertIsNone(result['ambiguity'])
                    self.assertEqual(len(result['contact_facts']), 3)
                else:
                    self.assertTrue(result['ambiguity'])
                    self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_reviewer_foreign_fields_before_bare_address_are_ambiguous(self):
        for address in ('bob@example.test', 'Bob Synthetic <bob@example.test>',
                        'Bob Synthetic\nbob@example.test',
                        'Earlier conversation:\nBob Synthetic\nbob@example.test'):
            body = ('-- \nEmail: alice@example.test\nName: Bob Synthetic\n'
                    'Phone: 555-0199\n' + address)
            with self.subTest(address=address):
                result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
                self.assertTrue(result['ambiguity'])
                self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_reviewer_wrapped_reply_header_excludes_old_signature(self):
        body = ('Thanks\nOn Monday,\nAlice Synthetic <alice@example.test> wrote:\n'
                '-- \nName: Alice Synthetic\nEmail: alice@example.test\nPhone: 555-0199')
        result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
        self.assertTrue(result['ambiguity'])
        self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_wrapped_history_styles_and_current_signature(self):
        history = '-- \nName: Alice Synthetic\nEmail: alice@example.test\nPhone: 555-0199'
        current = '-- \nEmail: alice@example.test\nPhone: 555-0101'
        headers = (
            'On Monday, November 2, Alice Synthetic <alice@example.test> wrote:',
            'From: Alice Synthetic <alice@example.test>\nSent: Monday, November 2\nSubject: Synthetic',
            'Begin forwarded message:\nFrom: Alice Synthetic <alice@example.test>',
            '---------- Forwarded message ---------\nFrom: Alice Synthetic <alice@example.test>',
        )
        for header in headers:
            for width in (10, 20, 35, 60, 80):
                wrapped = '\n'.join(textwrap.fill(line, width=width) for line in header.splitlines())
                for prefix in ('Thanks', current):
                    with self.subTest(header=header, width=width, signed=prefix == current):
                        result = extract_candidates(prefix + '\n' + wrapped + '\n' + history,
                                                    'alice@example.test', 'ref', complete=True)
                        if prefix == current:
                            self.assertEqual([(x['field'], x['value']) for x in result['contact_facts']],
                                             [('email', 'alice@example.test'), ('phone', '555-0101')])
                        else:
                            self.assertTrue(result['ambiguity'])
                            self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_bare_address_identity_validation_before_emission(self):
        for address in ('bob@example.test', 'Bob Synthetic <bob@example.test>',
                        'alice@example.test', 'Alice Synthetic <alice@example.test>'):
            result = extract_candidates('-- \nEmail: alice@example.test\nPhone: 555-0101\n' + address,
                                        'alice@example.test', 'ref', complete=True)
            with self.subTest(address=address):
                if 'bob@' in address:
                    self.assertTrue(result['ambiguity'])
                    self.assertFalse(result['contact_facts'] + result['identity_observations'])
                else:
                    self.assertEqual(len(result['contact_facts']), 2)

    def test_thread_without_sender_signature_does_not_bind_history(self):
        history = '-- \nName: Bob Synthetic\nEmail: alice@example.test\nPhone: 555-0199'
        for header in ('Begin forwarded message:', 'Forwarded message', 'On Monday Bob wrote:',
                       'From: Bob Synthetic', '> quoted'):
            with self.subTest(header=header):
                result = extract_candidates('Thanks\n' + header + '\n' + history,
                                            'alice@example.test', 'synthetic-source', complete=True)
                self.assertTrue(result['ambiguity'])
                self.assertFalse(result['contact_facts'] + result['identity_observations'])
        for body in ('Thanks\nOn Monday Bob wrote:\n-- \nEmail: bob@example.test\nPhone: 555-0199',
                     'Thanks\n> -- \n> Email: alice@example.test\n> Phone: 555-0199',
                     '-- \nAlice Synthetic\nEarlier conversation:\nEmail: alice@example.test\nPhone: 555-0199'):
            with self.subTest(body=body):
                result = extract_candidates(body, 'alice@example.test', 'synthetic-source', complete=True)
                self.assertTrue(result['ambiguity'])
                self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_reviewer_labeled_foreign_identity_is_ambiguous(self):
        body = '-- \nEmail: alice@example.test\nName: Bob Synthetic <bob@example.test>\nPhone: 555-0199'
        result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
        self.assertTrue(result['ambiguity'])
        self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_clean_signature_with_layout_and_exact_evidence(self):
        body = '-- \n\tName : Alice Synthetic  \n\n Company: Example\nTitle: Director\nEmail: alice@example.test\nPhone: 555-0101'
        result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
        self.assertIsNone(result['ambiguity'])
        observations = result['contact_facts'] + result['identity_observations']
        self.assertEqual({item['field']: item['value'] for item in observations},
                         {'name': 'Alice Synthetic', 'company': 'Example', 'title': 'Director',
                          'email': 'alice@example.test', 'phone': '555-0101'})
        for item in observations:
            self.assertIn(item['evidence'], body.splitlines())

    def test_entity_name_evidence_and_wrapped_addresses(self):
        for evidence in ('Alice Other <alice@example.test>', 'Name: Bob Synthetic',
                         'Company: Bob <bob @ example.test>', 'bob@\nexample.test',
                         'bob@exam\nple.test', 'Name:\nBob Synthetic <bob@example.test>',
                         'Phone: 555-0199\n\nEmail: bob@example.test'):
            with self.subTest(evidence=evidence):
                body = '-- \nEmail: alice@example.test\nName: Alice Synthetic\n' + evidence
                result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
                self.assertTrue(result['ambiguity'])
                self.assertFalse(result['contact_facts'] + result['identity_observations'])

    def test_generated_complete_entity_binding(self):
        seed = 442213
        rng = random.Random(seed)
        for case in range(2400):
            foreign = case % 2 == 0
            address = 'bob@example.test' if foreign else 'alice@example.test'
            name = 'Bob Synthetic' if foreign else 'Alice Synthetic'
            phone = '555-0199' if foreign else '555-0101'
            placement = case % 9
            evidence = (address, name + ' <' + address + '>',
                        'Name: ' + name + ' <' + address + '>',
                        'Company: Example <' + address + '>',
                        'Title: Director <' + address + '>',
                        'Mobile: ' + phone + ' <' + address + '>',
                        'Phone: ' + phone + '\n' + name + '\n' + address,
                        name + '\n' + phone + '\n' + address,
                        'Email: ' + name + ' <' + address + '>')[placement]
            lines = ['Email: alice@example.test', 'Name: Alice Synthetic',
                     'Phone: 555-0101', evidence]
            rng.shuffle(lines)
            if case % 3 == 0:
                lines.insert(rng.randrange(len(lines) + 1), '')
            if case % 5 == 0:
                lines.insert(rng.randrange(len(lines) + 1), 'Phone: ' + phone)
            whitespace = rng.choice([' ', '\t', '  ', '\u00a0'])
            lines = [whitespace + line.replace(': ', ':' + whitespace) + whitespace for line in lines]
            if case % 7 == 0:
                lines = [line.replace(' <', '\n <') for line in lines]
            newline = rng.choice(['\n', '\r\n'])
            body = '-- ' + newline + newline.join(lines)
            result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
            observations = result['contact_facts'] + result['identity_observations']
            with self.subTest(seed=seed, case=case, body=body):
                self.assertFalse(any('bob@' in item['value'] or item['value'] == '555-0199'
                                     or 'Bob Synthetic' in item['value'] for item in observations))
                if foreign:
                    self.assertTrue(result['ambiguity'])
                    self.assertFalse(observations)
                elif placement == 0 and case % 5 and case % 7:
                    self.assertIsNone(result['ambiguity'])
                    self.assertEqual(len(observations), 3)
        print(f'Generated 2400 entity cases, seed {seed}')

    def test_reviewer_conflicting_display_identity_in_labeled_fields(self):
        for label in ('Title', 'Company'):
            with self.subTest(label=label):
                body = ('-- \nName: Alice Synthetic\nEmail: alice@example.test\n'
                        'Phone: 555-0101\n' + label + ': Bob Synthetic <alice@example.test>')
                result = extract_candidates(body, 'alice@example.test', 'ref', complete=True)
                self.assertTrue(result['ambiguity'])
                self.assertFalse(result['identity_observations'] + result['contact_facts'])

    def test_reviewer_clean_apostrophe_sender_retains_candidates(self):
        sender = "anne.o'brien@example.test"
        body = "-- \nName: Anne O'Brien\nEmail: " + sender + '\nPhone: 555-0101'
        result = extract_candidates(body, sender, 'ref', complete=True)
        self.assertIsNone(result['ambiguity'])
        self.assertEqual(len(result['identity_observations'] + result['contact_facts']), 3)

    def test_generated_display_names_and_punctuated_addresses(self):
        seed = 442215
        rng = random.Random(seed)
        addresses = ["anne.o'brien", 'anne+team', 'anne.obrien', "Anne.O'Brien+Team",
                     'anne%team', "!#$%&'*+-/=?^_`{|}~"]
        placements = ('', 'Name: ', 'Company: ', 'Title: ', 'Email: ', 'Phone: ', 'Mobile: ')
        count = 0
        for local in addresses:
            sender = local + '@Example.Test'
            for placement in placements:
                for foreign_address in (False, True):
                    for foreign_name in (False, True):
                        for layout in range(10):
                            address = 'foreign.' + sender if foreign_address else sender.swapcase()
                            name = 'Bob Synthetic' if foreign_name else "Anne O'Brien"
                            lines = ["Name: Anne O'Brien", 'Email: ' + sender, 'Phone: 555-0101']
                            if placement == 'Name: ':
                                lines.pop(0)
                                lines.append("Anne O'Brien <" + sender + '>')
                            suffix = ' Director' if layout % 2 and (foreign_name or foreign_address) else ''
                            lines.append(placement + name + ' <' + address + '>' + suffix)
                            rng.shuffle(lines)
                            pad = rng.choice(['', ' ', '\t', '\u00a0'])
                            nl = rng.choice(['\n', '\r\n', '\r'])
                            body = '-- ' + nl + nl.join(pad + line + pad for line in lines)
                            result = extract_candidates(body, sender, 'ref', complete=True)
                            observations = result['identity_observations'] + result['contact_facts']
                            count += 1
                            with self.subTest(seed=seed, case=count, layout=layout):
                                if foreign_address or foreign_name:
                                    self.assertTrue(result['ambiguity'])
                                    self.assertFalse(observations)
                                elif placement in ('', 'Name: ', 'Company: ', 'Title: '):
                                    self.assertIsNone(result['ambiguity'])
                                    self.assertEqual(len(observations), 3 + (placement in ('Company: ', 'Title: ')))
            body = "-- \nName: Anne O'Brien\nEmail: " + sender.swapcase() + '\nPhone: 555-0101'
            result = extract_candidates(body, sender, 'ref', complete=True)
            self.assertIsNone(result['ambiguity'])
            self.assertEqual(len(result['identity_observations'] + result['contact_facts']), 3)
        print(f'Generated {count} display/address cases, seed {seed}')

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
