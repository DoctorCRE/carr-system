"""Synthetic evidence routing at the offline SDK seam."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import post_call_coverage as coverage
import post_call_jev as jev


@pytest.fixture(autouse=True)
def synthetic_budget(monkeypatch):
    monkeypatch.setenv('CALL_JEV_DAILY_DOLLARS', '1')


class Judge:
    def __init__(self):
        self.states = []

    def system_one(self, *, state, questions, **kwargs):
        self.states.append(state)
        text = ' '.join(s['text'] for s in state['transcript_excerpt'])
        claim = ' '.join(str(v) for v in state['item']['fields'].values())
        supported = all(word.lower() in text.lower() for word in claim.rstrip('.').split())
        return SimpleNamespace(answers={
            key: SimpleNamespace(choice='synthetic', confidence=.9) if key == 'deal_match'
            else SimpleNamespace(noul=.9 if key != 'details_supported' or supported else .1)
            for key in questions})


def run_check(tmp_path, segments, item, *, ledger=None):
    judge = Judge()
    result = {'joe_tasks': [item]}
    jev.check_distillation(result, {'deals': [{'id': 'synthetic', 'name': 'Synthetic'}]},
                           {'segments': segments}, client=judge,
                           budget_ledger=tmp_path / 'budget.json', **({'coverage_ledger': ledger} if ledger else {}))
    return item['checks'], judge.states


@pytest.mark.parametrize('claim,passed', [('Approve synthetic renewal', True), ('Reject synthetic renewal', False)])
def test_exact_quote_beats_earlier_token_overlap(tmp_path, claim, passed):
    segments = [{'speaker': 'Joe', 'text': 'We now synthetic renewal approve synthetic renewal'},
                {'speaker': 'Joe', 'text': 'We now approve synthetic renewal.'}]
    checks, states = run_check(tmp_path, segments, {'deal_id': 'synthetic', 'title': claim,
                                                  'evidence': 'We now approve synthetic renewal'})
    assert checks['details']['pass'] is passed
    assert states[0]['transcript_excerpt'][0]['text'] == segments[1]['text']


def test_full_source_span_after_character_600(tmp_path):
    text = 'Background. ' * 70 + 'Approve synthetic renewal.'
    checks, states = run_check(tmp_path, [{'speaker': 'Joe', 'text': text}],
                              {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal',
                               'evidence': 'Approve synthetic renewal'})
    assert checks['details']['pass']
    assert states[0]['transcript_excerpt'][0]['text'] == text


def ledger_for(transcript, spans):
    return {'facts': [{'fact_id': f'f{i}', 'source_spans': [span]} for i, span in enumerate(spans)]}


def test_coverage_reference_overrides_misleading_quote(tmp_path):
    transcript = {'segments': [{'speaker': 'Joe', 'text': 'Reject synthetic renewal'},
                               {'speaker': 'Joe', 'text': 'Approve synthetic renewal'}]}
    ledger = ledger_for(transcript, [coverage.source_span(transcript, 1)])
    checks, states = run_check(tmp_path, transcript['segments'],
                              {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal',
                               'evidence': 'Reject synthetic renewal', 'fact_ids': ['f0']}, ledger=ledger)
    assert checks['details']['pass']
    assert states[0]['transcript_excerpt'][0]['text'] == 'Approve synthetic renewal'


@pytest.mark.parametrize('failure', ['stale', 'missing', 'oversized'])
def test_incomplete_evidence_never_calls_model(tmp_path, failure):
    transcript = {'segments': [{'speaker': 'Joe', 'text': 'x' * 5000 if failure == 'oversized' else 'Approve synthetic renewal'}]}
    span = coverage.source_span(transcript, 0)
    if failure == 'stale':
        span['segment_sha256'] = 'bad'
    ledger = ledger_for(transcript, [span])
    checks, states = run_check(tmp_path, transcript['segments'],
                              {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal',
                               'evidence': 'Approve synthetic renewal',
                               'fact_ids': ['missing' if failure == 'missing' else 'f0']}, ledger=ledger)
    assert checks['evidence_status'] == 'insufficient'
    assert checks['flagged'] and not states


def test_separate_turns_and_structured_mutations(tmp_path):
    transcript = {'segments': [{'speaker': 'Joe', 'text': 'Synthetic renewal approved.'},
                               {'speaker': 'Dell', 'text': 'Synthetic plans postponed.'},
                               {'speaker': 'Joe', 'text': 'negotiation'}]}
    ledger = ledger_for(transcript, [coverage.source_span(transcript, i) for i in range(3)])
    item = {'deal_id': 'synthetic', 'summary': 'Synthetic renewal approved. Synthetic plans postponed.',
            'evidence': 'Synthetic renewal', 'fact_ids': ['f0', 'f1'],
            'phase_move': {'value': 'closing', 'started_on': None, 'fact_ids': ['f2']}}
    judge = Judge()
    jev.check_distillation({'deal_updates': [item]}, {'deals': [{'id': 'synthetic', 'name': 'Synthetic'}]},
                           transcript, coverage_ledger=ledger, client=judge, budget_ledger=tmp_path / 'budget.json')
    assert len(judge.states) == 3
    assert all(len(s['transcript_excerpt']) == 1 for s in judge.states)
    assert not item['phase_move']['checks']['details']['pass']
    assert item['checks']['flagged']
    assert item['checks']['evidence_chars'] == sum(len(s['transcript_excerpt'][0]['text']) for s in judge.states)


def test_ambiguous_speaker_stays_for_review(tmp_path):
    checks, _ = run_check(tmp_path, [{'speaker': 'unknown', 'text': 'Approve synthetic renewal'}],
                           {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal', 'evidence': 'Approve synthetic renewal'})
    assert not checks['speaker']['pass']


def test_span_offsets_do_not_send_other_text(tmp_path):
    transcript = {'segments': [{'speaker': 'Joe', 'text': 'PRIVATE PREFIX. Approve synthetic renewal. PRIVATE SUFFIX.'}]}
    text = transcript['segments'][0]['text']
    start = text.index('Approve')
    span = coverage.source_span(transcript, 0, start, start + len('Approve synthetic renewal.'))
    checks, states = run_check(tmp_path, transcript['segments'],
                              {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal',
                               'evidence': 'PRIVATE PREFIX', 'fact_ids': ['f0']},
                              ledger=ledger_for(transcript, [span]))
    assert checks['details']['pass']
    assert 'PRIVATE' not in str(states)
    assert checks['request_chars'] <= jev.MAX_REQUEST_CHARS


def test_complete_request_size_is_bounded(tmp_path):
    judge = Judge()
    item = {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal', 'evidence': 'Approve synthetic renewal'}
    jev.check_distillation({'joe_tasks': [item]},
                           {'deals': [{'id': 'synthetic', 'name': 'name' * 3000}]},
                           {'segments': [{'speaker': 'Joe', 'text': 'Approve synthetic renewal'}]},
                           client=judge, budget_ledger=tmp_path / 'budget.json')
    assert item['checks']['evidence_status'] == 'insufficient'
    assert not judge.states


def test_insufficient_model_evidence_is_distinct_from_contradiction(tmp_path):
    judge = Judge()
    original = judge.system_one
    def insufficient(**kwargs):
        response = original(**kwargs)
        response.answers['evidence_sufficient'].noul = .1
        return response
    judge.system_one = insufficient
    item = {'deal_id': 'synthetic', 'title': 'Approve synthetic renewal', 'evidence': 'Approve synthetic renewal'}
    jev.check_distillation({'joe_tasks': [item]}, {'deals': []},
                           {'segments': [{'speaker': 'Joe', 'text': 'Approve synthetic renewal'}]},
                           client=judge, budget_ledger=tmp_path / 'budget.json')
    assert item['checks']['evidence_status'] == 'insufficient'
    assert not item['checks']['details']['pass']
