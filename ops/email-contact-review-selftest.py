"""Synthetic signature review transitions through the intake's real journal."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import outlook_intake as intake
from lib.outlook_settings import load_settings
sys.path.insert(0, str(Path(__file__).parent))
from outlook_test_support import write_settings


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        write_settings(self.home, Path(__file__).resolve().parents[1], internal_domain='internal.test')
        self.settings = load_settings(self.home)
        self.now = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)
        self.state = {}
        self.calls = []
        self.receipts = {}
        self.error = None
        self.snapshot = {'party_id': 101, 'party_version': 7,
                         'values': {'phone': '555-0100', 'email': 'pat@example.test',
                                    'name': 'Pat Example', 'company': 'Old Org', 'title': None, 'cell': None},
                         'provenance': {'source': 'party', 'party_id': 101, 'party_version': 7,
                                        'company': {'source': 'party.name', 'party_id': 201, 'party_version': 19}},
                         'unread_fields': []}
        self.message = {'ident': 'source:1', 'sender': 'pat@example.test', 'metadata_incomplete': False,
                        'sent_unix': int(self.now.timestamp()), 'full': True,
                        'text': '-- \nEmail: pat@example.test\nPhone: 555-0199',
                        'local_keys': ['item-1'], 'message_id': '<synthetic@example.test>'}

    def carr(self, verb, args):
        self.calls.append((verb, copy.deepcopy(args)))
        self.assertEqual(verb, 'record-finding')
        self.assertEqual(str(uuid.UUID(args['idempotency_key'])), args['idempotency_key'])
        if self.error == 'refuse':
            return {'ok': False, 'error': 'forbidden'}
        if self.error == 'transient':
            raise intake.CarrError(code='unavailable')
        self.receipts.setdefault(args['idempotency_key'], copy.deepcopy(args))
        if self.error == 'lost':
            raise intake.CarrError(code='unavailable')
        return {'ok': True}

    def run_review(self, snapshot=True, contacts=None):
        if contacts is None:
            raw = {'address': 'pat@example.test', 'ref': 'C-901', 'name': 'Pat Example', 'exact': True}
            if snapshot:
                raw['contact_snapshot'] = copy.deepcopy(self.snapshot)
            contacts = intake._contacts(lambda *_: {'contacts': [raw]})()
        ports = intake.Ports(self.carr, lambda: contacts, lambda *_: None, lambda: self.now)
        work = intake.Run(ports, self.state, self.now, self.home, self.settings)
        work.signature_findings([self.message], intake.Matcher(contacts, self.settings.internal_domain))
        return work

    def test_signature_change_creates_one_finding_and_never_overwrites_party(self):
        prior = copy.deepcopy(self.snapshot)
        self.run_review()
        self.assertEqual(len(self.receipts), 1)
        args = self.calls[0][1]
        self.assertEqual(args['proposes_correction'], {'field': 'phone', 'current': '555-0100', 'proposed': '555-0199'})
        self.assertEqual(args['value']['evidence'], 'Phone: 555-0199')
        self.assertFalse(args['value']['verified'])
        self.assertEqual(args['value']['verification'], 'unverified_signature_observation')
        self.assertEqual(args['value']['extraction_version'], 'email-contact-candidates/v3')
        self.assertIn('outlook-signature/', args['source'])
        self.assertEqual(self.snapshot, prior)
        self.assertFalse({'update-party-contact', 'correct-party-identity'} & {v for v, _ in self.calls})

    def test_html_signature_uses_proposal_journal_and_preserves_activity_body(self):
        self.message['text'] = 'Hello Pat Example Director 555-0199 pat@example.test'
        self.message['signature_html'] = ('<p>Hello</p><div>Pat Example</div><div>Director</div>'
                                          '<div>555-0199</div><div>pat@example.test</div>')
        before = copy.deepcopy(self.message)
        self.error = 'lost'
        self.run_review()
        self.error = None
        self.now += timedelta(minutes=2)
        self.run_review()
        self.run_review()
        self.assertEqual(self.message, before)
        self.assertEqual(len(self.receipts), 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], self.calls[1])
        args = next(iter(self.receipts.values()))
        self.assertEqual(args['value']['evidence'], '555-0199')
        self.assertEqual(args['value']['source_locator']['local_keys'], ['item-1'])
        self.assertEqual(args['value']['party_version'], 7)
        self.assertEqual(args['proposes_correction']['proposed'], '555-0199')
        self.assertEqual(args['epistemic_status'], 'proposed')
        self.assertEqual({verb for verb, _ in self.calls}, {'record-finding'})

    def test_html_only_changed_then_restored_evidence_holds_retry(self):
        self.message['text'] = 'Thanks 555-0199 pat@example.test'
        original = '<p>Thanks</p><div>555-0199</div><div>pat@example.test</div>'
        self.message['signature_html'] = original
        self.error = 'transient'
        self.run_review()
        frozen = copy.deepcopy(self.calls[0])
        self.state = json.loads(json.dumps(self.state))
        self.message['signature_html'] = ('<p>Thanks</p><blockquote><div>555-0199</div>'
                                          '<div>pat@example.test</div></blockquote>')
        self.error = None
        self.now += timedelta(minutes=2)
        work = self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.receipts)
        self.assertEqual(work.counts['signature_held'], 1)
        self.message['signature_html'] = original
        self.run_review()
        self.run_review()
        self.assertEqual(self.calls, [frozen, frozen])
        self.assertEqual(len(self.receipts), 1)

    def test_plain_only_change_with_html_also_holds_retry(self):
        self.message['signature_html'] = '<div>555-0199</div><div>pat@example.test</div>'
        self.error = 'transient'
        self.run_review()
        self.message['text'] += '\nnew provenance'
        self.error = None
        self.now += timedelta(minutes=2)
        self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.receipts)

    def test_policy_change_holds_pending_but_preserves_acknowledgements(self):
        from unittest.mock import patch
        for error in ('transient', None):
            with self.subTest(error=error):
                self.setUp()
                self.error = error
                self.run_review()
                self.error = None
                self.now += timedelta(minutes=2)
                with patch('lib.email_contact_candidates.VERSION', 'future-policy'):
                    self.run_review()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(len(self.receipts), int(error is None))

    def test_html_alias_requires_same_evidence_proof(self):
        from lib.email_contact_review import resolve_sources
        mailbox = {}
        original = {**self.message, 'signature_html': '<div>555-0199</div><div>pat@example.test</div>'}
        key = lambda ident: 'source/' + ident
        first = resolve_sources(mailbox, [original], key, 300)[original['ident']]
        alias = {**original, 'ident': 'new-alias', 'local_keys': []}
        alias['signature_html'] = '<blockquote>' + original['signature_html'] + '</blockquote>'
        self.assertIsNone(resolve_sources(mailbox, [alias], key, 300)[alias['ident']])
        alias['signature_html'] = original['signature_html']
        self.assertEqual(resolve_sources(mailbox, [alias], key, 300)[alias['ident']], first)

    def test_assistant_context_never_contaminates_sender_even_empty_fields(self):
        for context in ('For scheduling contact my assistant Bob.', 'Assistant Bob',
                        'Assistant contact', 'Delegate contact Bob',
                        'Please contact my delegate Bob for scheduling.'):
            for numbers in (['555-0199'], ['Phone: 555-0199', 'Mobile: 555-0188']):
                for empty in (False, True):
                    with self.subTest(context=context, numbers=numbers, empty=empty):
                        self.setUp()
                        if empty:
                            self.snapshot['values']['phone'] = None
                        lines = [context, 'Bob Synthetic', *numbers, 'pat@example.test']
                        self.message['text'] = ' '.join(lines)
                        self.message['signature_html'] = ''.join('<div>' + x + '</div>' for x in lines)
                        work = self.run_review()
                        self.assertFalse(self.calls)
                        self.assertEqual(work.counts['signature_held'], 1)

    def test_missing_fields_and_identity_observations_require_review(self):
        self.message['text'] += '\nTitle: Director\nName: Pat New\nCompany: New Org'
        self.run_review()
        rows = {a['proposes_correction']['field']: a for _, a in self.calls}
        self.assertEqual(set(rows), {'phone', 'title', 'name', 'company'})
        self.assertIsNone(rows['title']['proposes_correction']['current'])
        self.assertEqual(rows['company']['value']['prior_provenance']['party_version'], 19)
        self.assertEqual(rows['company']['value']['prior_provenance']['party_id'], 201)
        self.assertEqual(rows['company']['value']['party_version'], 7)

    def test_ambiguous_unmatched_suppressed_and_deal_only_are_held(self):
        for rows in ([], [intake.Contact('pat@example.test', 'C-901', 'Pat', False)],
                     [intake.Contact('pat@example.test', 'C-901', 'Pat')]):
            self.run_review(contacts=rows)
        self.run_review(snapshot=False)
        self.assertFalse(self.calls)

    def test_duplicate_roles_resolve_one_party_and_conflicts_are_held(self):
        rows = [{'address': 'pat@example.test', 'ref': ref, 'name': 'Pat', 'exact': True,
                 'contact_snapshot': copy.deepcopy(self.snapshot)} for ref in ('C-901', 'L-902')]
        contacts = intake._contacts(lambda *_: {'contacts': rows})()
        self.run_review(contacts=contacts)
        self.assertEqual(len(self.calls), 1)
        self.state.clear()
        self.calls.clear()
        rows[1]['contact_snapshot']['party_id'] = 102
        self.run_review(contacts=intake._contacts(lambda *_: {'contacts': rows})())
        self.assertFalse(self.calls)

    def test_replay_and_lost_response_use_frozen_arguments(self):
        self.error = 'lost'
        self.run_review()
        frozen = copy.deepcopy(self.calls[0][1])
        self.error = None
        self.now += timedelta(minutes=2)
        self.snapshot['values']['phone'] = 'human edit'
        self.snapshot['party_version'] = 8
        self.snapshot['provenance']['party_version'] = 8
        self.run_review()
        self.run_review()
        self.assertEqual([a for _, a in self.calls], [frozen, frozen])
        self.assertEqual(len(self.receipts), 1)
        self.state = json.loads(json.dumps(self.state))
        self.run_review()
        self.assertEqual(len(self.calls), 2)

    def test_changed_source_or_party_never_replays_or_rebinds(self):
        for change in ('text', 'party', 'sender'):
            with self.subTest(change=change):
                self.setUp()
                self.error = 'lost'
                self.run_review()
                self.now += timedelta(minutes=2)
                if change == 'text':
                    self.message['text'] += '\nTitle: Director'
                elif change == 'party':
                    self.snapshot['party_id'] = 102
                    self.snapshot['provenance']['party_id'] = 102
                else:
                    self.message['sender'] = 'other@example.test'
                work = self.run_review()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(work.counts['signature_held'], 1)

    def test_refusal_and_transient_failure_are_bounded(self):
        self.error = 'refuse'
        self.run_review()
        self.now += timedelta(days=1)
        self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.state.clear()
        self.calls.clear()
        self.error = 'transient'
        self.run_review()
        self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.now += timedelta(minutes=2)
        self.error = None
        self.run_review()
        self.assertEqual(len(self.receipts), 1)

    def test_joe_dell_attribution_and_counts_only_logs(self):
        for owner in ('joe', 'dell'):
            self.state = {}
            write_settings(self.home, Path(__file__).resolve().parents[1], owner=owner, internal_domain='internal.test')
            self.settings = load_settings(self.home)
            work = self.run_review()
            args = self.calls[-1][1]
            self.assertEqual(args['value']['mailbox_owner'], owner)
            self.assertEqual(args['value']['mailbox_account'], self.settings.account)
            output = intake.status_line(intake.Outcome('ok', 'complete', work.counts))
            self.assertNotIn('pat@', output)
            self.assertNotIn('555-', output)
        self.assertEqual(len(self.receipts), 2)

    def test_incomplete_unread_and_unchanged_observations_do_not_submit(self):
        self.message['full'] = False
        self.run_review()
        self.assertFalse(self.calls)
        self.message['full'] = True
        self.message['text'] = '-- \nEmail: pat@example.test\nCompany: New Org'
        self.snapshot['unread_fields'] = ['company']
        self.snapshot['values'].pop('company')
        self.run_review()
        self.assertFalse(self.calls)

    def test_request_receipts_survive_pruning_and_new_sources_get_new_keys(self):
        self.run_review()
        first = self.calls[0][1]['idempotency_key']
        self.now += timedelta(days=400)
        intake._prune(self.state, self.now)
        self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.message['ident'] = 'source:2'
        self.message['sent_unix'] = int(self.now.timestamp())
        self.run_review()
        self.assertNotEqual(self.calls[-1][1]['idempotency_key'], first)
        self.assertEqual(len(self.receipts), 2)

    def test_raw_evidence_and_mobile_field_are_preserved(self):
        self.message['body'] = '-- \nEmail: pat@example.test\n  Mobile: 555-0188  '
        self.message['text'] = self.message['body'].strip()
        self.run_review()
        args = self.calls[0][1]
        self.assertEqual(args['value']['evidence'], '  Mobile: 555-0188  ')
        self.assertEqual(args['proposes_correction'], {'field': 'cell', 'current': None, 'proposed': '555-0188'})

    def test_company_version_changes_request_identity(self):
        self.message['text'] += '\nCompany: New Org'
        self.run_review()
        first = next(a for _, a in self.calls if a['value']['field'] == 'company')
        self.state.clear()
        self.calls.clear()
        self.snapshot['provenance']['company']['party_version'] += 1
        self.run_review()
        second = next(a for _, a in self.calls if a['value']['field'] == 'company')
        self.assertNotEqual(first['idempotency_key'], second['idempotency_key'])

    def test_refusal_after_transient_failure_stops_retry(self):
        self.error = 'transient'
        self.run_review()
        self.now += timedelta(minutes=2)
        self.error = 'refuse'
        self.run_review()
        self.now += timedelta(hours=1)
        self.run_review()
        self.assertEqual(len(self.calls), 2)


    def test_roleless_party_uses_exact_party_ref(self):
        row = {'address': 'pat@example.test', 'ref': 'P-0101', 'name': 'Pat',
               'exact': True, 'contact_snapshot': self.snapshot}
        self.run_review(contacts=intake._contacts(lambda *_: {'contacts': [row]})())
        self.assertEqual(self.calls[0][1]['subject'], 'P-0101')
        self.assertEqual(self.calls[0][1]['subject_kind'], 'auto')

    def test_held_source_can_resolve_when_full_text_arrives(self):
        self.message['full'] = False
        self.run_review()
        self.message['full'] = True
        self.run_review()
        self.assertEqual(len(self.receipts), 1)


    def test_request_identity_satisfies_record_finding_envelope(self):
        self.run_review()
        key = self.calls[0][1]['idempotency_key']
        self.assertEqual(str(uuid.UUID(key)), key)


    def test_lost_response_after_replay_window_is_held_without_resubmission(self):
        self.error = 'lost'
        self.run_review()
        self.now += timedelta(days=31)
        self.error = None
        work = self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(work.counts['requests_blocked'], 1)
        self.assertEqual(len(self.receipts), 1)



if __name__ == '__main__':
    unittest.main()
