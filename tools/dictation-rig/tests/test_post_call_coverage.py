"""Synthetic coverage contract tests. No provider calls or production records."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import post_call as pc


class CoverageGateTests(unittest.TestCase):
    def test_all_empty_cannot_be_ready(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/synthetic-call.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / fixture['context']['session']
            session.mkdir()
            pc.store_context(session, fixture['context'])
            pc.write_private(session / 'transcript.json', fixture['transcript'])
            empty = {**fixture['distillation'], **{k: [] for k in ('joe_tasks', 'dell_tasks', 'deal_updates', 'draft_proposals')}}
            status = pc.process_session(session, lambda _: empty, run_checks=False)
            self.assertNotEqual(status['state'], 'ready_review')
            self.assertFalse((session / pc.REPORT_FILE).exists())

import post_call_coverage as cov
from copy import deepcopy


def controlled_extraction(transcript):
    topics = [{'topic_id': key, 'identity': {'deal_id': key, 'participant_ids': []}}
              for key in ('harbor', 'cedar')]
    topics.append({'topic_id': 'oak', 'identity': {'mentioned_name': 'Oak office',
                   'candidate_deal_ids': [], 'reason': 'No exact deal in the supplied context'}})
    def fact(key, topic, kind, statement, value, indices, disposition=None):
        return {'fact_id': key, 'topic_id': topic, 'kind': kind, 'statement': statement,
                'asserted_value': value, 'source_spans': [cov.source_span(transcript, i) for i in indices],
                'disposition': disposition}
    return {'topics': topics, 'facts': [
        fact('friday', 'harbor', 'commitment', 'Joe calls the landlord Friday', 'Friday', [0, 2],
             {'superseded': {'replacement_fact_id': 'monday', 'correction_spans': [cov.source_span(transcript, 5)]}}),
        fact('survey', 'cedar', 'status', 'Survey complete', 'complete', [3]),
        fact('monday', 'harbor', 'commitment', 'Joe calls the landlord Monday', 'Monday', [5]),
        fact('phase', 'cedar', 'phase_change', 'Move to negotiation', 'negotiation', [6]),
        fact('oak', 'oak', 'commitment', 'Joe asks for availability', 'ask', [7]),
        fact('unchanged', 'cedar', 'no_change', 'Keep next step', 'unchanged', [8],
             {'no_change': {'decision_spans': [cov.source_span(transcript, 8)]}}),
        fact('send', 'cedar', 'commitment', 'Joe sends survey to architect', '2026-11-06', [9, 10, 11]),
    ], 'non_fact_spans': [{'span': cov.source_span(transcript, i), 'reason': 'Conversational filler'} for i in (1, 4)]}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/synthetic-call.json').read_text())
        self.case = fixture['coverage_case']
        self.transcript, self.context = self.case['transcript'], self.case['context']
        self.response = controlled_extraction(self.transcript)
        self.calls = []
        self.history_calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session = Path(self.tmp.name) / self.context['session']
        self.session.mkdir()
        pc.store_context(self.session, self.context)
        pc.write_private(self.session / 'transcript.json', self.transcript)

    def history(self, request):
        self.history_calls.append(deepcopy(request))
        dispositions = {f['fact_id']: f['disposition'] for f in self.response['facts']}
        return {'dispositions': [{'fact_id': f['fact_id'], 'disposition': deepcopy(dispositions[f['fact_id']])}
                                 for f in request['facts']]}

    def extract(self, request):
        self.calls.append(deepcopy(request))
        lo, hi = request['window']['overlap_range']
        sources = request['sources']
        result = deepcopy(self.response)
        for fact in result['facts']:
            fact['disposition'] = None
            fact['source_spans'] = [s for s in fact['source_spans'] if
                lo <= sources[s['segment_index']]['offset'] + s['char_start'] and
                sources[s['segment_index']]['offset'] + s['char_end'] <= hi]
        result['facts'] = [f for f in result['facts'] if f['source_spans']]
        clipped = []
        for entry in result['non_fact_spans']:
            s = entry['span']
            offset = sources[s['segment_index']]['offset']
            a, b = max(s['char_start'], lo-offset), min(s['char_end'], hi-offset)
            if a < b:
                entry['span'] = cov.source_span(self.transcript, s['segment_index'], a, b)
                clipped.append(entry)
        result['non_fact_spans'] = clipped
        return result

    def raw_report(self):
        return {'schema_version': 1, 'report': {'summary': 'Landlord timing and survey work discussed.', 'decisions': [], 'open_questions': []},
            'joe_tasks': [
                {'title': 'Call landlord Monday', 'deal_id': 'harbor', 'participant_ids': [], 'evidence': 'Monday replaces Friday', 'fact_ids': ['monday']},
                {'title': 'Send survey to architect', 'deal_id': 'cedar', 'participant_ids': [], 'evidence': 'survey to the architect on November 6, 2026', 'due_on': '2026-11-06', 'fact_ids': ['send']}],
            'dell_tasks': [], 'deal_updates': [
                {'deal_id': 'cedar', 'participant_ids': [], 'summary': 'Survey complete', 'evidence': 'the survey is complete', 'fact_ids': ['survey'],
                 'phase_move': {'value': 'negotiation', 'fact_ids': ['phase']}}],
            'draft_proposals': [], 'review_questions': []}

    def distill(self, request):
        if request.get('stage') == 'coverage_history':
            return self.history(request)
        return self.extract(request) if request.get('stage') == 'coverage' else self.raw_report()

    def build(self, **kwargs):
        ledger = cov.build_call_ledger(self.transcript, self.context, self.extract, **kwargs)
        return cov.reconcile_history(ledger, self.transcript, self.history)

    def test_scored_recall_dispositions_and_no_false_proposals(self):
        status = pc.process_session(self.session, self.distill, run_checks=False)
        self.assertEqual(status['state'], 'ready_review')
        ledger = pc.read_json(self.session / cov.FILE)
        report = pc.read_json(self.session / pc.REPORT_FILE)
        gold = {f['id']: f for f in self.case['gold']}
        actual = {f['fact_id']: f for f in ledger['facts']}
        self.assertEqual(set(actual), set(gold))
        for key, expected in gold.items():
            self.assertEqual(actual[key]['kind'], expected['kind'])
            self.assertEqual({s['segment_index'] for s in actual[key]['source_spans']}, set(expected['occurrences']))
            self.assertEqual(list(actual[key]['disposition']), [expected['disposition']])
        expected_proposals = {k for k, f in gold.items() if f['disposition'] == 'proposal'}
        proposed = {fid for _, item in cov.proposal_items(report) for fid in item['fact_ids']}
        self.assertEqual(proposed, expected_proposals)
        self.assertEqual(len(list(cov.proposal_items(report))), 4)
        self.assertGreaterEqual(len(ledger['windows']), 3)
        friday_windows = [f for w in ledger['windows'] for f in w['result']['facts'] if f['fact_id'] == 'friday']
        self.assertTrue(all(f['disposition'] is None for f in friday_windows))
        harbor_history = next(r for r in self.history_calls if r['topic']['topic_id'] == 'harbor')
        self.assertEqual({f['fact_id'] for f in harbor_history['facts']}, {'friday', 'monday'})
        self.assertTrue(ledger['history_receipts']['harbor']['validated_completion'])
        self.assertEqual((self.session / cov.FILE).stat().st_mode & 0o777, 0o600)
        payload = json.dumps(pc.sanitized_candidates(self.session))
        for segment in self.transcript['segments']:
            self.assertNotIn(segment['text'], payload)
        self.assertNotIn('source_spans', payload)
        before = len(self.calls)
        self.assertEqual(pc.process_session(self.session, self.distill, run_checks=False)['state'], 'ready_review')
        self.assertEqual(len(self.calls), before)
        self.assertEqual(len(pc.sanitized_candidates(self.session)['post_call_items']), 2)

    def test_missing_extraction_and_proposal_are_held(self):
        for mode in ('extraction', 'proposal', 'empty'):
            with self.subTest(mode=mode):
                for name in (pc.STATUS_FILE, pc.REPORT_FILE, cov.FILE):
                    (self.session / name).unlink(missing_ok=True)
                def incomplete(request):
                    if request.get('stage') == 'coverage_history':
                        return self.history(request)
                    if request.get('stage') == 'coverage':
                        result = self.extract(request)
                        if mode == 'extraction':
                            result['facts'] = [f for f in result['facts'] if f['fact_id'] != 'survey']
                        if mode == 'empty':
                            result = {'topics': [], 'facts': [], 'non_fact_spans': []}
                        return result
                    result = self.raw_report()
                    result['joe_tasks'] = []
                    return result
                status = pc.process_session(self.session, incomplete, run_checks=False, max_attempts=1)
                self.assertEqual(status['state'], 'needs_attention')
                self.assertEqual(status['coverage_counts']['sources'], len(self.transcript['segments']))
                self.assertEqual(status['coverage_source_ids'][0], 'segment-0')
                self.assertFalse((self.session / pc.REPORT_FILE).exists())
                self.assertIsNotNone(pc.context_from_session(self.session))
                self.assertIsNone(pc.retention_ready(self.session, 'a'*64))

    def test_proposal_retry_reuses_extraction_and_published_ids(self):
        def incomplete(request):
            if request.get('stage') == 'coverage_history':
                return self.history(request)
            if request.get('stage') == 'coverage':
                return self.extract(request)
            report = self.raw_report()
            report['joe_tasks'] = []
            return report
        self.assertEqual(pc.process_session(self.session, incomplete, run_checks=False)['state'], 'blocked')
        count = len(self.calls)
        history_count = len(self.history_calls)
        status = pc.read_json(self.session / pc.STATUS_FILE)
        status['next_attempt_at'] = None
        pc.write_private(self.session / pc.STATUS_FILE, status)
        self.assertEqual(pc.process_session(self.session, self.distill, run_checks=False)['state'], 'ready_review')
        self.assertEqual(len(self.calls), count)
        self.assertEqual(len(self.history_calls), history_count)
        shaped = pc.sanitized_candidates(self.session)
        pc.apply_candidate_ids(self.session, shaped['bindings'], ['remote-' + str(i) for i in range(4)])
        self.assertEqual(pc.sanitized_candidates(self.session)['post_call_items'], [])
        self.assertEqual(pc.sanitized_candidates(self.session)['legacy_items'], [])
        self.assertEqual(pc.process_session(self.session, self.distill, run_checks=False)['state'], 'ready_review')
        self.assertEqual(len(self.calls), count)

    def test_window_budget_survives_restarts(self):
        saved = []
        calls = []
        def invalid(request):
            calls.append(request)
            return {'topics': [], 'facts': [], 'non_fact_spans': []}
        for _ in range(4):
            with self.assertRaises(cov.CoverageError):
                cov.build_call_ledger(self.transcript, self.context, invalid,
                    previous=saved[-1] if saved else None, persist=lambda x: saved.append(deepcopy(x)))
        self.assertEqual(len(calls), 3)
        self.assertEqual(saved[-1]['failures'][0]['code'], 'window_budget_exhausted')

    def test_changed_proposal_content_and_context_refuse_publication(self):
        pc.process_session(self.session, self.distill, run_checks=False)
        report = pc.read_json(self.session / pc.REPORT_FILE)
        report['joe_tasks'][0]['title'] = 'Unsupported extra work'
        pc.write_private(self.session / pc.REPORT_FILE, report)
        with self.assertRaises(pc.ContractError):
            pc.sanitized_candidates(self.session)
        report['joe_tasks'][0]['title'] = 'Call landlord Monday'
        pc.write_private(self.session / pc.REPORT_FILE, report)
        pc.write_private(self.session / pc.CONTEXT_FILE, {'call_context': {'changed': True}})
        with self.assertRaises(pc.ContractError):
            pc.sanitized_candidates(self.session)

    def test_failed_history_retries_only_failed_topics(self):
        def interrupted(request):
            if request.get('stage') == 'coverage_history':
                if request['topic']['topic_id'] == 'cedar':
                    self.history_calls.append(deepcopy(request))
                    raise RuntimeError('interrupted history')
                return self.history(request)
            return self.distill(request)
        self.assertEqual(pc.process_session(self.session, interrupted, run_checks=False)['state'], 'blocked')
        count = len(self.calls)
        status = pc.read_json(self.session / pc.STATUS_FILE)
        status['next_attempt_at'] = None
        pc.write_private(self.session / pc.STATUS_FILE, status)
        self.assertEqual(pc.process_session(self.session, self.distill, run_checks=False)['state'], 'ready_review')
        self.assertEqual(len(self.calls), count)
        topics = [r['topic']['topic_id'] for r in self.history_calls]
        self.assertEqual(topics.count('harbor'), 1)
        self.assertEqual(topics.count('cedar'), 2)
        self.assertEqual(topics.count('oak'), 1)

    def test_incomplete_history_never_generates_proposals(self):
        proposal_calls = []
        def incomplete(request):
            if request.get('stage') == 'coverage':
                return self.extract(request)
            if request.get('stage') == 'coverage_history':
                return {'dispositions': []}
            proposal_calls.append(request)
            return self.raw_report()
        status = pc.process_session(self.session, incomplete, run_checks=False, max_attempts=1)
        self.assertEqual(status['state'], 'needs_attention')
        self.assertEqual(status['reason'], 'incomplete_history')
        self.assertEqual(proposal_calls, [])
        self.assertFalse((self.session / pc.REPORT_FILE).exists())

    def test_overlap_duplicates_merge_by_source_identity(self):
        transcript = {'segments': [{'text': ' ' * 40 + 'Joe will call Pat.' + ' ' * 40}]}
        span = cov.source_span(transcript, 0, 40, 58)
        def extract(request):
            lo, hi = request['window']['overlap_range']
            facts = []
            if lo <= 40 and 58 <= hi:
                facts = [{'fact_id': 'segment-0-assertion-0',
                    'topic_id': 'harbor', 'kind': 'commitment',
                    'statement': 'Joe calls Pat' if lo == 0 else 'Joe will telephone Pat',
                    'asserted_value': 'call Pat', 'source_spans': [span], 'disposition': None}]
            return {'topics': [{'topic_id': 'harbor', 'identity': {'deal_id': 'harbor', 'participant_ids': []}}],
                    'facts': facts, 'non_fact_spans': []}
        ledger = cov.build_call_ledger(transcript, self.context, extract, core_chars=40, overlap_chars=20)
        self.assertEqual(len(ledger['facts']), 1)
        self.assertEqual(ledger['facts'][0]['source_spans'], [span])

    def test_distinct_atomic_claims_can_share_a_segment(self):
        transcript = {'segments': [{'text': 'Joe will call Pat and Sam.'}]}
        facts = [{'fact_id': name, 'topic_id': 'harbor', 'kind': 'commitment',
                  'statement': 'Call ' + name, 'asserted_value': 'call',
                  'source_spans': [cov.source_span(transcript, 0)], 'disposition': None}
                 for name in ('Pat', 'Sam')]
        response = {'topics': [{'topic_id': 'harbor', 'identity': {'deal_id': 'harbor', 'participant_ids': []}}],
                    'facts': facts, 'non_fact_spans': []}
        ledger = cov.build_call_ledger(transcript, self.context, lambda _: response)
        self.assertEqual({f['fact_id'] for f in ledger['facts']}, {'Pat', 'Sam'})
        facts[1]['fact_id'] = 'Pat'
        with self.assertRaisesRegex(cov.CoverageError, 'duplicate_fact_id'):
            cov.build_call_ledger(transcript, self.context, lambda _: response)

    def test_transport_failure_preserves_existing_reason_and_coverage_receipt(self):
        def failing(request):
            raise cov.ExtractionTransportError('local post-call distiller failed')
        status = pc.process_session(self.session, failing, run_checks=False, max_attempts=1)
        self.assertEqual(status['state'], 'needs_attention')
        self.assertEqual(status['reason'], 'local post-call distiller failed')
        self.assertEqual(status['coverage_counts']['sources'], len(self.transcript['segments']))
        self.assertEqual(pc.read_json(self.session / cov.FILE)['failures'][0]['code'], 'extraction_failed')
        self.assertFalse((self.session / pc.REPORT_FILE).exists())

    def test_invalid_foreign_and_stale_spans(self):
        for field, value in (('segment_index', 999), ('segment_sha256', 'stale'), ('char_end', 99999), ('source_id', 'foreign')):
            with self.subTest(field=field):
                response = deepcopy(self.response)
                response['facts'][0]['source_spans'][0][field] = value
                with self.assertRaises(cov.CoverageError):
                    cov.build_call_ledger(self.transcript, self.context, lambda _: response, core_chars=100000)

    def test_resume_reuses_only_validated_windows(self):
        saved = []
        def interrupt(request):
            if request['window']['core_range'][0] > 0:
                raise RuntimeError('interrupted')
            return self.extract(request)
        with self.assertRaises(cov.CoverageError):
            cov.build_call_ledger(self.transcript, self.context, interrupt, persist=lambda x: saved.append(deepcopy(x)))
        self.calls.clear()
        ledger = self.build(previous=saved[-1])
        self.assertTrue(all(r['window']['core_range'][0] > 0 for r in self.calls))
        self.assertEqual(len(ledger['facts']), 7)
        self.assertEqual(ledger['windows'][1]['attempts'], 2)

    def test_normalization_cannot_hide_missing_or_unsupported_links(self):
        for mode in ('filtered', 'unlinked', 'duplicate', 'kind', 'identity'):
            with self.subTest(mode=mode):
                ledger = self.build()
                raw = self.raw_report()
                if mode == 'filtered':
                    raw['joe_tasks'][0]['deal_id'] = 'foreign'
                elif mode == 'unlinked':
                    raw['joe_tasks'][0]['fact_ids'] = []
                elif mode == 'duplicate':
                    raw['dell_tasks'] = [deepcopy(raw['joe_tasks'][0])]
                elif mode == 'kind':
                    raw['deal_updates'][0]['phase_move']['fact_ids'] = ['survey']
                else:
                    raw['joe_tasks'][0]['deal_id'] = 'cedar'
                report = pc.normalize_distillation(raw, self.context, self.context['session'], transcript=self.transcript)
                cov.add_identity_questions(ledger, report, pc._review_id)
                with self.assertRaises(cov.CoverageError):
                    cov.reconcile_coverage(ledger, report)

    def test_earlier_correction_cannot_dispose_of_a_later_repeated_assertion(self):
        self.transcript['segments'].append({
            'speaker': 'Joe', 'text': 'For Harbor Clinic, I will call the landlord Friday.'})
        self.response['facts'][0]['source_spans'].append(cov.source_span(self.transcript, 12))
        with self.assertRaisesRegex(cov.CoverageError, 'nonlater_correction'):
            self.build()

    def test_supersession_requires_correction_and_has_no_cycles(self):
        for mode in ('missing', 'cycle', 'recorded', 'no_change'):
            ledger = self.build()
            raw = self.raw_report()
            if mode == 'missing':
                ledger['facts'][0]['disposition']['superseded']['correction_spans'] = []
            elif mode == 'cycle':
                monday = next(f for f in ledger['facts'] if f['fact_id'] == 'monday')
                monday['disposition'] = {'superseded': {'replacement_fact_id': 'friday', 'correction_spans': ledger['facts'][0]['source_spans']}}
                raw['joe_tasks'].pop(0)
            elif mode == 'recorded':
                ledger['facts'][0]['disposition'] = {'already_recorded': {'record_ref': 'unverified'}}
            else:
                ledger['facts'][0]['disposition'] = {'no_change': {'decision_spans': ledger['facts'][0]['source_spans']}}
            report = pc.normalize_distillation(raw, self.context, self.context['session'], transcript=self.transcript)
            cov.add_identity_questions(ledger, report, pc._review_id)
            with self.subTest(mode=mode), self.assertRaises(cov.CoverageError):
                cov.reconcile_coverage(ledger, report)

    def test_publication_refuses_legacy_incomplete_and_changed_transcript(self):
        pc.write_private(self.session / pc.REPORT_FILE, self.raw_report())
        with self.assertRaises(pc.ContractError):
            pc.sanitized_candidates(self.session)
        (self.session / pc.REPORT_FILE).unlink()
        pc.process_session(self.session, self.distill, run_checks=False)
        transcript = deepcopy(self.transcript)
        transcript['segments'][0]['text'] += ' Changed'
        pc.write_private(self.session / 'transcript.json', transcript)
        with self.assertRaises(pc.ContractError):
            pc.sanitized_candidates(self.session)
        self.assertIsNone(pc.retention_ready(self.session, 'b'*64))

    def test_all_nonfact_windows_need_independent_agreement(self):
        transcript = {'segments': [{'text': 'Hello.'}]}
        response = {'topics': [], 'facts': [], 'non_fact_spans': [
            {'span': cov.source_span(transcript, 0), 'reason': 'Greeting'}]}
        calls = []
        def extract(request):
            calls.append(request)
            return deepcopy(response)
        ledger = cov.build_call_ledger(transcript, self.context, extract)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1]['second_check'])
        self.assertTrue(ledger['windows'][0]['validated_completion'])
        def disagree(request):
            return {**response, 'non_fact_spans': []} if request.get('second_check') else response
        with self.assertRaises(cov.CoverageError):
            cov.build_call_ledger(transcript, self.context, disagree)


if __name__ == '__main__':
    unittest.main()
