#!/usr/bin/env python3
"""Behavior checks for the read-only real-miss evaluator."""
import contextlib
import importlib.util
import io
import json
import tempfile
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
SPEC = importlib.util.spec_from_file_location('real_misses', REPO / 'evals/retrieval-real-misses/run_eval.py')
assert SPEC is not None and SPEC.loader is not None
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)


def case(case_id='a', cause='never-asked', **extra):
    return {'id': case_id, 'cause': cause, 'expected_refs': ['P-0055'], **extra}


def observation(case_id='a', refs=None, **extra):
    return {'case_id': case_id, 'candidates': [EVAL.candidate([r]) for r in (refs or [])], **extra}


class ScoringTests(unittest.TestCase):
    def test_public_fixtures_have_no_record_identities(self):
        from ops.pii_guard import identity_spans, load_corpus
        corpus = load_corpus(REPO / 'ops/config/public-source-identities.v1.json')
        for name in ['questions.v1.json', 'resolved-refs.json']:
            text = (REPO / 'evals/retrieval-real-misses' / name).read_text()
            self.assertEqual(identity_spans(text, corpus), [], name)

    def test_public_projection_cannot_measure_live_retrieval(self):
        fixture = json.loads((REPO / 'evals/retrieval-real-misses/questions.v1.json').read_text())
        fixture['requires_private_fixture'] = True
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            source.write_text(json.dumps(fixture))
            with patch.object(EVAL.sys, 'argv', ['eval', '--fixture', str(source), '--output', str(output)]), \
                    patch.object(EVAL, 'run_cases', return_value=[]) as run, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    EVAL.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()
                self.assertFalse(output.exists())

    def test_explicit_private_fixture_can_measure_live_retrieval(self):
        fixture = json.loads((REPO / 'evals/retrieval-real-misses/questions.v1.json').read_text())
        fixture.pop('requires_private_fixture', None)
        cases = EVAL.validate_fixture(fixture)
        rows = [{'case_id': c['id'], 'candidates': [EVAL.candidate(c['expected_refs'])],
                 'live_rows': c.get('expected_live_rows')} for c in cases]
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            source.write_text(json.dumps(fixture))
            with patch.object(EVAL.sys, 'argv', ['eval', '--fixture', str(source), '--output', str(output)]), \
                    patch.object(EVAL, 'run_cases', return_value=rows) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(EVAL.main(), 0)
                run.assert_called_once_with(cases)
                self.assertEqual(json.loads(output.read_text())['report']['overall']['hit_rate_at_5'], 1)

    def test_rank_five_hits_and_rank_six_misses(self):
        cases = [case(), case('b')]
        rows = [observation(refs=['P-1', 'P-2', 'P-3', 'P-4', 'P-0055']),
                observation('b', ['P-1', 'P-2', 'P-3', 'P-4', 'P-5', 'P-0055'])]
        summary = EVAL.score(cases, rows)['overall']
        self.assertEqual(summary['hits_at_5'], 1)
        self.assertEqual(summary['hit_rate_at_5'], 0.5)

    def test_tombstones_and_duplicates_consume_rank(self):
        rows = [EVAL.candidate(['P-0055'], False)] * 5 + [EVAL.candidate(['P-0055'])]
        result = EVAL.score([case()], [{'case_id': 'a', 'candidates': rows}])
        self.assertFalse(result['cases'][0]['hit_at_5'])
        result = EVAL.score([case()], [observation(refs=['P-1'] * 5 + ['P-0055'])])
        self.assertEqual(result['overall']['hits_at_5'], 0)

    def test_any_expected_ref_and_exact_matching(self):
        cases = [case(expected_refs=['P-0055', 'C-161']), case('b')]
        result = EVAL.score(cases, [observation(refs=['C-161']), observation('b', ['P-005'])])
        self.assertEqual(result['overall']['hits_at_5'], 1)

    def test_errors_remain_in_denominator_and_cannot_hit(self):
        cases = [case(), case('b', 'search-quality')]
        result = EVAL.score(cases, [observation(refs=['P-0055']),
                                   observation('b', ['P-0055'], error='timeout')])
        self.assertEqual(result['overall']['hit_rate_at_5'], 0.5)
        self.assertEqual(result['overall']['errors'], 1)
        self.assertEqual(result['by_cause']['search-quality']['hit_rate_at_5'], 0)

    def test_missing_extra_duplicate_observations_refused(self):
        for rows in [[], [observation(), observation()], [observation(), observation('b')]]:
            with self.assertRaises(ValueError):
                EVAL.score([case()], rows)

    def test_live_duplicate_drift_is_separate_from_identity_hit(self):
        cases = [case(expected_live_rows=1, forbidden_live_refs=['P-0099'])]
        row = observation(refs=['P-0055', 'P-0099'], live_rows=17)
        result = EVAL.score(cases, [row])
        self.assertTrue(result['cases'][0]['hit_at_5'])
        self.assertEqual(result['overall']['contract_failures'], 1)

    def test_fixture_oracle_null_and_repeat_agreement(self):
        fixture = json.loads((REPO / 'evals/retrieval-real-misses/questions.v1.json').read_text())
        cases = EVAL.validate_fixture(fixture)
        oracle = [{'case_id': c['id'], 'candidates': [EVAL.candidate(c['expected_refs'])],
                   'live_rows': c.get('expected_live_rows')} for c in cases]
        null = [{'case_id': c['id'], 'candidates': []} for c in cases]
        self.assertEqual(EVAL.score(cases, oracle)['overall']['hit_rate_at_5'], 1)
        self.assertEqual(EVAL.score(cases, null)['overall']['hit_rate_at_5'], 0)
        self.assertEqual(EVAL.score(cases, oracle), EVAL.score(cases, oracle))
        self.assertEqual(EVAL.score(cases, oracle)['overall']['rule_routing']['delivery_recall'], 1)
        self.assertEqual(EVAL.score(cases, null)['overall']['rule_routing']['delivery_recall'], 0)

    def test_unordered_rule_delivery_is_separate_from_top_five(self):
        c = case(measurement='rule_delivery', expected_refs=['rule:e313a3ca'])
        refs = ['rule:12345678'] * 5 + ['rule:e313a3ca']
        report = EVAL.score([c], [observation(refs=refs)])
        self.assertEqual(report['overall']['questions'], 0)
        self.assertEqual(report['overall']['rule_routing']['delivered'], 1)

    def test_boot_full_text_is_available_but_index_lines_are_not(self):
        def read(verb, args):
            page = args['page']
            return {'rule_boot': {'page': page, 'pages_total': 2, 'digest': 'one',
                                  'text': '### e313a3ca\nFull body' if page == 1
                                  else 'c864b8cb | A | An index summary'}}
        self.assertEqual(EVAL.boot_rule_ids(read), {'e313a3ca'})

    def test_boot_pool_cannot_bypass_missing_trigger_text(self):
        c = case(request={'args': {'prompt': 'an unrelated quasar sunrise'}})
        def read(verb, args):
            return {'shared_rules': [{'id': '3fa17fa0', 'statement': 'Not requested'}], 'personal_rules': []}
        refs = EVAL.routing_candidates(c, read, {'e313a3ca'})
        self.assertEqual(refs, [{'refs': ['rule:e313a3ca'], 'eligible': True}])

    def test_names_are_never_persisted_as_refs(self):
        self.assertEqual(EVAL.candidate(['Example Doctor', 'secret text', 'P-0055']),
                         {'refs': ['P-0055'], 'eligible': True})

    def test_write_verb_refused_before_subprocess(self):
        with self.assertRaisesRegex(EVAL.ReadError, 'verb_not_read_allowlisted'):
            EVAL.call_read('log-activity', {})

    def test_organization_retired_refs_never_become_live_candidates(self):
        payload = {'organizations': [{'refs': ['P-0055'], 'role_refs': [], 'all_retired': False,
                                      'retired_refs': ['P-0099'], 'live_rows': 1}]}
        self.assertEqual(EVAL.record_candidates('find', payload, 'organizations'),
                         [{'refs': ['P-0055'], 'eligible': True}])

    def test_only_returned_deals_are_resolved_by_exact_identity(self):
        payload = {'deals_via_link': [{'name': 'Example deal', 'client_ref': 'C-1'}]}
        index = {('C-1', 'Example deal'): ['11111111-1111-4111-8111-111111111111'], ('C-2', 'Other deal'): ['22222222-2222-4222-8222-222222222222']}
        self.assertEqual(EVAL.record_candidates('find', payload, 'deals_via_link', index),
                         [{'refs': ['deal:11111111-1111-4111-8111-111111111111'], 'eligible': True}])
        with self.assertRaises(EVAL.ReadError):
            EVAL.record_candidates('find', payload, 'deals_via_link', {})

    def test_doctor_query_hydration_does_not_insert_target_into_results(self):
        calls = []
        def read(verb, args):
            calls.append((verb, args))
            if verb == 'deal-board':
                return {'deals': [{'id': '11111111-1111-4111-8111-111111111111', 'name': 'Practice deal', 'client_ref': 'C-1',
                                   'client_name': 'Example Doctor'}]}
            return {'deals_via_link': []}
        cases = [case(request={'verb': 'find', 'args': {'query': '{name}'}},
                      lane='deals_via_link', name_from_ref='C-1', expected_refs=['deal:11111111-1111-4111-8111-111111111111'])]
        with contextlib.redirect_stderr(io.StringIO()):
            rows = EVAL.run_cases(cases, read)
        self.assertEqual(calls[1], ('find', {'query': 'Example Doctor'}))
        self.assertEqual(EVAL.score(cases, rows)['overall']['hits_at_5'], 0)

    def test_typed_precedent_cannot_count_as_settled_ruling(self):
        payload = {'rulings': [{'decision_id': '11111111-1111-4111-8111-111111111111', 'record_kind': 'typed_precedent'}]}
        rows = EVAL.record_candidates('find-precedent', payload, 'rulings')
        self.assertEqual(rows, [{'refs': ['decision:11111111-1111-4111-8111-111111111111'], 'eligible': False}])

    def test_malformed_reads_remain_case_errors(self):
        for verb, lane, payload in [('find', 'parties', {'parties': [None]}),
                                    ('find-and-catch-up', 'candidates', {}),
                                    ('who-do-we-know', 'resolved', {})]:
            c = case(request={'verb': verb, 'args': {}}, lane=lane)
            with contextlib.redirect_stderr(io.StringIO()):
                rows = EVAL.run_cases([c], lambda *_: payload)
            self.assertEqual(EVAL.score([c], rows)['overall']['errors'], 1)

    def test_process_start_failure_becomes_fixed_error(self):
        with patch.object(EVAL.subprocess, 'run', side_effect=OSError('private environment detail')):
            with self.assertRaisesRegex(EVAL.ReadError, '^read_command_unavailable$'):
                EVAL.call_read('find', {})

    def test_invalid_board_cannot_leave_partial_hit(self):
        deal_id = '11111111-1111-4111-8111-111111111111'
        def read(verb, args):
            if verb == 'deal-board':
                return {'deals': [{'id': deal_id, 'name': 'Example', 'client_ref': 'C-1'}, {}]}
            return {'deals_via_link': [{'name': 'Example', 'client_ref': 'C-1'}]}
        c = case(request={'verb': 'find', 'args': {}}, lane='deals_via_link',
                 expected_refs=['deal:' + deal_id])
        with contextlib.redirect_stderr(io.StringIO()):
            rows = EVAL.run_cases([c], read)
        report = EVAL.score([c], rows)
        self.assertEqual(report['overall']['errors'], 1)
        self.assertFalse(report['cases'][0]['hit_at_5'])

    def test_live_metadata_rejects_prose_without_persisting_it(self):
        c = case(request={'verb': 'find', 'args': {}}, lane='parties')
        for key in ('state', 'fallback'):
            payload = {'parties': [], key: {'private': 'Private prose'}}
            with contextlib.redirect_stderr(io.StringIO()):
                rows = EVAL.run_cases([c], lambda *_: payload)
            self.assertEqual(rows[0]['error'], 'invalid_result_contract')
            self.assertNotIn('Private', json.dumps(rows))

    def test_replay_projection_refuses_untyped_content(self):
        for extra in ({'state': 'Private prose'}, {'fallback': {'private': 'Private prose'}},
                      {'error': 'Private error prose'}):
            with self.assertRaises((ValueError, TypeError)):
                EVAL.project_observations([observation(**extra)])
        rows = EVAL.project_observations([observation(unrecognized_notes='Private prose')])
        self.assertNotIn('Private', json.dumps(rows))

    def test_replay_preserves_measurement_provenance(self):
        saved = {'measured_at': 'original time', 'runner_sha256': 'original collector',
                 'source_revision': 'original revision', 'routing_sha256': {'routes': 'original hash'}}
        current = {k: 'rescore' for k in saved}
        with patch.object(EVAL, 'provenance', return_value=current):
            result = EVAL.build_result([case()], [observation()], 'fixture digest', saved)
        for key, value in saved.items():
            self.assertEqual(result[key], value)
        self.assertEqual(result['rescored_with'], current)


if __name__ == '__main__':
    unittest.main()
