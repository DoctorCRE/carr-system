"""Source accounting and private post-call-coverage/v1 reconciliation."""
from copy import deepcopy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from math import ceil

SCHEMA = 'post-call-coverage/v1'
FILE = 'post-call-coverage.json'
KINDS = ('commitment', 'status', 'phase_change', 'next_step', 'operating_state',
         'workspace_change', 'rename', 'communication', 'no_change', 'other')
MAX_CONCURRENT_CALLS = 8
MAX_MODEL_WAVES = 6
TERMINAL_FAILURES = {'topic_history_too_large', 'coverage_time_budget_exceeded'}
LISTS = ('joe_tasks', 'dell_tasks', 'deal_updates', 'draft_proposals')
REVIEW_OPERATIONS = {'operating_state': 'operating_state', 'rename': 'rename',
                     'national_account_reassignment': 'workspace_change',
                     'prospect_routing': 'workspace_change', 'business_task': 'commitment',
                     'client_lifecycle': 'other'}


class CoverageError(RuntimeError):
    pass


class ExtractionTransportError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _require(condition, code):
    if not condition:
        raise CoverageError(code)


def _layout(transcript):
    rows = []
    offset = 0
    for index, segment in enumerate(transcript.get('segments', [])):
        _require(isinstance(segment, dict) and isinstance(segment.get('text'), str), 'invalid_segment')
        text = segment['text']
        rows.append({'segment_index': index, 'source_id': f'segment-{index}',
                     'sha256': hashlib.sha256(text.encode()).hexdigest(),
                     'speaker_label': segment.get('speaker'), 'start_ms': segment.get('start_ms'),
                     'end_ms': segment.get('end_ms'), 'offset': offset, 'length': len(text)})
        offset += len(text) + 1
    return rows, max(0, offset - 1)


def source_span(transcript, index, start=0, end=None):
    text = transcript['segments'][index]['text']
    return {'source_id': f'segment-{index}', 'segment_index': index,
            'char_start': start, 'char_end': len(text) if end is None else end,
            'segment_sha256': hashlib.sha256(text.encode()).hexdigest()}


def _validate_span(span, sources):
    _require(isinstance(span, dict), 'invalid_span')
    _require(set(span) == {'source_id', 'segment_index', 'char_start', 'char_end', 'segment_sha256'}, 'invalid_span')
    index = span.get('segment_index')
    _require(type(index) is int and 0 <= index < len(sources), 'foreign_span')
    source = sources[index]
    a, b = span.get('char_start'), span.get('char_end')
    _require(type(a) is int and type(b) is int and 0 <= a < b <= source['length'], 'invalid_offsets')
    _require(span.get('source_id') == source['source_id'] and
             span.get('segment_sha256') == source['sha256'], 'stale_span')
    return source['offset'] + a, source['offset'] + b


def _windows(sources, length, core_chars, overlap_chars):
    for start in range(0, length, core_chars):
        end = min(start + core_chars, length)
        lo, hi = max(0, start - overlap_chars), min(length, end + overlap_chars)
        ranges = []
        for row in sources:
            a, b = max(lo, row['offset']), min(hi, row['offset'] + row['length'])
            if a < b:
                ranges.append({'segment_index': row['segment_index'], 'char_start': a - row['offset'],
                               'char_end': b - row['offset']})
        yield {'core_range': [start, end], 'overlap_range': [lo, hi], 'ranges': ranges,
               'validated_completion': False, 'attempts': 0, 'unresolved_ranges': [[start, end]]}


def _window_result(raw, receipt, ledger, transcript, context):
    _require(isinstance(raw, dict) and set(raw) == {'topics', 'facts', 'non_fact_spans'}, 'invalid_extraction')
    _require(all(isinstance(raw[k], list) for k in raw), 'invalid_extraction')
    sources = ledger['sources']
    topics = {}
    deals = {row['id']: row for row in context['deals']}
    for topic in raw['topics']:
        _require(isinstance(topic, dict) and isinstance(topic.get('topic_id'), str) and bool(topic['topic_id']) and
                 set(topic) == {'topic_id', 'identity'}, 'invalid_topic')
        identity = topic.get('identity', {})
        _require(isinstance(identity, dict), 'invalid_identity')
        if 'deal_id' in identity:
            _require(set(identity) == {'deal_id', 'participant_ids'} and
                     isinstance(identity['deal_id'], str) and identity['deal_id'] in deals, 'unknown_identity')
            participants = {p['party_id'] for p in deals[identity['deal_id']]['participants']}
            _require(isinstance(identity.get('participant_ids'), list) and
                     set(identity['participant_ids']) <= participants, 'unknown_identity')
        else:
            _require(set(identity) == {'mentioned_name', 'candidate_deal_ids', 'reason'} and
                     isinstance(identity.get('mentioned_name'), str) and bool(identity['mentioned_name']) and
                     isinstance(identity.get('reason'), str) and bool(identity['reason']) and
                     isinstance(identity.get('candidate_deal_ids'), list) and
                     set(identity['candidate_deal_ids']) <= deals.keys(), 'invalid_unresolved_identity')
        _require(topic['topic_id'] not in topics, 'duplicate_topic')
        topics[topic['topic_id']] = topic
    facts = []
    fact_ids = set()
    covered = []
    lo, hi = receipt['overlap_range']
    for fact in raw['facts']:
        _require(isinstance(fact, dict) and set(fact) == {
            'fact_id', 'topic_id', 'kind', 'statement', 'asserted_value', 'source_spans', 'disposition'}, 'invalid_fact')
        _require(isinstance(fact['fact_id'], str) and bool(fact['fact_id']) and fact['topic_id'] in topics and
                 fact['kind'] in KINDS and isinstance(fact['statement'], str) and bool(fact['statement']) and
                 isinstance(fact['source_spans'], list) and bool(fact['source_spans']), 'invalid_fact')
        _require(fact['fact_id'] not in fact_ids, 'duplicate_fact_id')
        fact_ids.add(fact['fact_id'])
        for span in fact['source_spans']:
            a, b = _validate_span(span, sources)
            _require(lo <= a < b <= hi, 'foreign_window_span')
            covered.append((a, b))
        disposition = fact['disposition']
        _require(disposition is None, 'premature_disposition')
        facts.append(deepcopy(fact))
    for non_fact in raw['non_fact_spans']:
        _require(isinstance(non_fact, dict) and set(non_fact) == {'span', 'reason'}, 'invalid_non_fact')
        _require(isinstance(non_fact.get('reason'), str) and bool(non_fact['reason']), 'missing_non_fact_reason')
        a, b = _validate_span(non_fact['span'], sources)
        _require(lo <= a < b <= hi, 'foreign_window_span')
        covered.append((a, b))
    start, end = receipt['core_range']
    for source in sources:
        text = transcript['segments'][source['segment_index']]['text']
        a, b = max(start, source['offset']), min(end, source['offset'] + len(text))
        if a >= b:
            continue
        intervals = sorted((max(a, x), min(b, y)) for x, y in covered if x < b and y > a)
        cursor = a
        for x, y in intervals:
            _require(not text[cursor-source['offset']:x-source['offset']].strip(), 'unaccounted_source')
            cursor = max(cursor, y)
        _require(not text[cursor-source['offset']:b-source['offset']].strip(), 'unaccounted_source')
    return {'topics': list(topics.values()), 'facts': facts, 'non_fact_spans': raw['non_fact_spans']}


def incomplete_ledger(ledger, code):
    return {**ledger, 'state': 'incomplete', 'failures': [{'code': code, 'ids': []}]}


def _parallel_model_work(items, work, model):
    lock = RLock()

    def invoke(request):
        lock.release()
        try:
            return model(request)
        finally:
            lock.acquire()

    def run(item):
        with lock:
            return work(item, invoke)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS) as pool:
        futures = [pool.submit(run, item) for item in items]
    for future in futures:
        future.result()


def _require_time_budget(ledger, topic_count=0, *, persist):
    waves = 2 * ceil(len(ledger['windows']) / MAX_CONCURRENT_CALLS)
    if waves + ceil(topic_count / MAX_CONCURRENT_CALLS) + 1 > MAX_MODEL_WAVES:
        persist(incomplete_ledger(ledger, 'coverage_time_budget_exceeded'))
        raise CoverageError('coverage_time_budget_exceeded')


def build_call_ledger(transcript, context, extractor, *, previous=None, persist=lambda _: None,
                      core_chars=8000, overlap_chars=2000, max_attempts=3, recording_started_at=None):
    sources, length = _layout(transcript)
    binding = {'schema': SCHEMA, 'session': context['session'], 'transcript_sha256': digest(transcript),
               'context_sha256': digest(context), 'extraction_contract_version': SCHEMA}
    reuse = previous and all(previous.get(k) == v for k, v in binding.items())
    ledger = deepcopy(previous) if reuse else {
        **binding, 'recording_started_at': recording_started_at, 'sources': sources, 'topics': [], 'facts': [],
        'segment_accounting': [], 'windows': list(_windows(sources, length, core_chars, overlap_chars)),
        'state': 'building', 'failures': []}
    ledger.update(state='building', failures=[])
    _require_time_budget(ledger, persist=persist)

    def extract_window(receipt, invoke):
        if receipt['validated_completion']:
            return
        if receipt['attempts'] >= max_attempts:
            ledger.update(state='incomplete', failures=[{'code': 'window_budget_exhausted', 'ids': receipt['core_range']}])
            persist(ledger)
            raise CoverageError('window_budget_exhausted')
        request = {'stage': 'coverage', 'schema': SCHEMA, 'context': context, 'window': receipt,
                   'sources': sources, 'segments': [
                       {**r, 'text': transcript['segments'][r['segment_index']]['text'][r['char_start']:r['char_end']]}
                       for r in receipt['ranges']]}
        try:
            receipt['attempts'] += 1
            persist(ledger)
            checked = _window_result(invoke(request), receipt, ledger, transcript, context)
            if not checked['facts']:
                _require(receipt['attempts'] < max_attempts, 'empty_window_unverified')
                receipt['attempts'] += 1
                persist(ledger)
                second = _window_result(invoke({**request, 'second_check': True}), receipt, ledger, transcript, context)
                _require(not second['facts'] and digest(second) == digest(checked), 'empty_window_disagreement')
            receipt.update(validated_completion=True, unresolved_ranges=[], result=checked)
            persist(ledger)
        except (CoverageError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
            code = str(exc) if isinstance(exc, CoverageError) else 'extraction_failed'
            ledger.update(state='incomplete', failures=[{'code': code, 'ids': receipt['core_range']}])
            persist(ledger)
            if isinstance(exc, ExtractionTransportError):
                raise
            raise CoverageError(code) from exc
    _parallel_model_work(ledger['windows'], extract_window, extractor)
    topics, facts = {}, {}
    accounting = {s['segment_index']: {'segment_index': s['segment_index'], 'fact_ids': [],
                                      'non_fact_spans': [], 'non_fact_reason': []} for s in sources}
    for receipt in ledger['windows']:
        result = receipt['result']
        for topic in result['topics']:
            key = topic['topic_id']
            _require(key not in topics or topics[key] == topic, 'conflicting_topic')
            topics[key] = topic
        for fact in result['facts']:
            key = fact['fact_id']
            match = next((old for old in facts.values() if
                old['topic_id'] == fact['topic_id'] and old['kind'] == fact['kind'] and
                old['asserted_value'] == fact['asserted_value'] and
                (old['statement'] == fact['statement'] or (old['fact_id'] == fact['fact_id'] and
                 fact['asserted_value'] is not None and any(span in old['source_spans'] for span in fact['source_spans'])))), None)
            if match is not None:
                key = match['fact_id']
                fact = {**fact, 'fact_id': key, 'statement': match['statement']}
            if key in facts:
                old = facts[key]
                _require(all(old[k] == fact[k] for k in fact if k != 'source_spans'), 'conflicting_fact')
                old['source_spans'] = list({digest(s): s for s in old['source_spans'] + fact['source_spans']}.values())
            else:
                facts[key] = deepcopy(fact)
            for span in fact['source_spans']:
                row = accounting[span['segment_index']]
                if key not in row['fact_ids']:
                    row['fact_ids'].append(key)
        for entry in result['non_fact_spans']:
            row = accounting[entry['span']['segment_index']]
            if entry['span'] not in row['non_fact_spans']:
                row['non_fact_spans'].append(entry['span'])
                row['non_fact_reason'].append(entry['reason'])
    ledger.update(topics=list(topics.values()), facts=list(facts.values()), segment_accounting=list(accounting.values()))
    persist(ledger)
    return ledger


def _validate_dispositions(ledger, facts, questions, *, allow_pending=False, proposals=()):
    topics = {t['topic_id']: t for t in ledger['topics']}
    for fact in facts.values():
        disposition = fact['disposition']
        if disposition is None and allow_pending:
            continue
        _require(isinstance(disposition, dict) and len(disposition) == 1, 'undisposed_fact')
        kind, detail = next(iter(disposition.items()))
        _require(isinstance(detail, dict), 'invalid_disposition')
        if kind == 'proposal':
            _require(not allow_pending and set(detail) == {'proposal_id'} and
                     detail['proposal_id'] in proposals, 'unverified_proposal')
        elif kind == 'review_intent':
            _require(not allow_pending and set(detail) == {'review_question_id'} and
                     detail['review_question_id'] in questions, 'missing_intent_question')
        elif kind == 'superseded':
            _require(set(detail) == {'replacement_fact_id', 'correction_spans'}, 'invalid_supersession')
            target = detail.get('replacement_fact_id')
            _require(isinstance(target, str) and target in facts and target != fact['fact_id'] and facts[target]['topic_id'] == fact['topic_id'] and
                     facts[target]['kind'] == fact['kind'], 'invalid_supersession')
            spans = detail.get('correction_spans')
            _require(isinstance(spans, list) and bool(spans), 'missing_correction')
            for span in spans:
                _validate_span(span, ledger['sources'])
                _require(span in facts[target]['source_spans'], 'foreign_correction')
                _require(_validate_span(span, ledger['sources'])[0] >=
                         max(_validate_span(s, ledger['sources'])[1] for s in fact['source_spans']), 'nonlater_correction')
        elif kind == 'no_change':
            _require(set(detail) == {'decision_spans'}, 'invalid_no_change')
            _require(fact['kind'] == 'no_change' and isinstance(detail.get('decision_spans'), list) and bool(detail['decision_spans']), 'unspoken_no_change')
            for span in detail['decision_spans']:
                _validate_span(span, ledger['sources'])
                _require(span in fact['source_spans'], 'foreign_decision')
        elif kind == 'unresolved_identity':
            _require(set(detail) == {'review_question_id'}, 'invalid_unresolved_identity')
            _require('deal_id' not in topics[fact['topic_id']]['identity'] and
                     isinstance(detail.get('review_question_id'), str) and detail['review_question_id'] in questions, 'missing_identity_question')
        else:
            raise CoverageError('unverified_disposition')
    for fact in facts.values():
        seen = set()
        while fact.get('disposition') and 'superseded' in fact['disposition']:
            _require(fact['fact_id'] not in seen, 'supersession_cycle')
            seen.add(fact['fact_id'])
            fact = facts[fact['disposition']['superseded']['replacement_fact_id']]


def reconcile_history(ledger, transcript, reconciler, *, persist=lambda _: None, max_attempts=3):
    ledger = deepcopy(ledger)
    receipts = ledger.setdefault('history_receipts', {})
    _require_time_budget(ledger, len(ledger['topics']), persist=persist)

    def reconcile_topic(topic, invoke):
        topic_id = topic['topic_id']
        facts = [f for f in ledger['facts'] if f['topic_id'] == topic_id]
        if not facts:
            return
        input_sha256 = digest([topic, [{k: v for k, v in f.items() if k != 'disposition'} for f in facts]])
        receipt = receipts.get(topic_id)
        if receipt is None or receipt['input_sha256'] != input_sha256:
            receipt = {'input_sha256': input_sha256, 'attempts': 0, 'validated_completion': False}
            receipts[topic_id] = receipt
        try:
            if not receipt['validated_completion']:
                _require(receipt['attempts'] < max_attempts, 'history_budget_exhausted')
                receipt['attempts'] += 1
                persist(ledger)
                evidence = [{'fact_id': f['fact_id'], 'occurrences': [
                    {'span': span, 'text': transcript['segments'][span['segment_index']]['text'][span['char_start']:span['char_end']]}
                    for span in f['source_spans']]} for f in facts]
                _require(len(json.dumps([facts, evidence], ensure_ascii=False)) <= 24000, 'topic_history_too_large')
                response = invoke({'stage': 'coverage_history', 'schema': SCHEMA,
                    'topic': topic, 'facts': facts, 'evidence': evidence})
                _require(isinstance(response, dict) and set(response) == {'dispositions'} and
                         isinstance(response['dispositions'], list), 'invalid_history')
                rows = response['dispositions']
                _require(all(isinstance(r, dict) and set(r) == {'fact_id', 'disposition'} and
                             isinstance(r['fact_id'], str) for r in rows), 'invalid_history')
                by_id = {r['fact_id']: r['disposition'] for r in rows}
                _require(len(rows) == len(by_id) and set(by_id) == {f['fact_id'] for f in facts}, 'incomplete_history')
                resolved = {f['fact_id']: {**f, 'disposition': by_id[f['fact_id']]} for f in facts}
                _validate_dispositions(ledger, resolved, set(), allow_pending=True)
                receipt.update(validated_completion=True, dispositions=by_id)
                persist(ledger)
            for fact in facts:
                fact['disposition'] = deepcopy(receipt['dispositions'][fact['fact_id']])
        except (CoverageError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
            code = str(exc) if isinstance(exc, CoverageError) else 'history_failed'
            ledger.update(state='incomplete', failures=[{'code': code, 'ids': []}])
            persist(ledger)
            if isinstance(exc, ExtractionTransportError):
                raise
            raise CoverageError(code) from exc
    _parallel_model_work(ledger['topics'], reconcile_topic, reconciler)
    persist(ledger)
    return ledger



def proposal_items(report):
    for name in LISTS:
        for item in report.get(name, []):
            yield name, item
            if name == 'deal_updates':
                for field in ('phase_move', 'next_step'):
                    if item.get(field):
                        yield field, item[field]


def reconcile_coverage(ledger, report):
    ledger = deepcopy(ledger)
    facts = {f['fact_id']: f for f in ledger['facts']}
    topics = {t['topic_id']: t for t in ledger['topics']}
    for topic in ledger['topics']:
        inventory = [{k: v for k, v in f.items() if k != 'disposition'} for f in ledger['facts'] if f['topic_id'] == topic['topic_id']]
        if inventory:
            receipt = ledger.get('history_receipts', {}).get(topic['topic_id'], {})
            _require(receipt.get('validated_completion') and receipt.get('input_sha256') == digest([topic, inventory]), 'history_audit_required')
    linked, bindings, intent_facts = {}, {}, set()
    for question in report.get('review_questions', []):
        if question.get('kind') != 'review_intent':
            continue
        ids = question.get('fact_ids', [])
        _require(bool(ids) and all(i in facts for i in ids), 'unsupported_intent')
        spans = []
        for fact_id in ids:
            fact = facts[fact_id]
            _require(fact_id not in intent_facts and (fact['disposition'] is None or
                     fact['disposition'] == {'review_intent': {'review_question_id': question['id']}}),
                     'conflicting_intent_disposition')
            identity = topics[fact['topic_id']]['identity']
            _require(question.get('deal_id') == identity.get('deal_id'), 'intent_identity_mismatch')
            _require('deal_id' in identity or bool(question.get('identity_question')), 'missing_identity_question')
            _require(fact['kind'] == REVIEW_OPERATIONS.get(question.get('operation')), 'intent_kind_mismatch')
            _require(fact['asserted_value'] == question.get('desired_value'), 'intent_value_mismatch')
            for span in fact['source_spans']:
                if span not in spans:
                    spans.append(span)
            fact['disposition'] = {'review_intent': {'review_question_id': question['id']}}
            intent_facts.add(fact_id)
        _require('source_spans' not in question or question['source_spans'] == spans, 'intent_source_mismatch')
        question['source_spans'] = spans
        bindings[question['id']] = digest({k: v for k, v in question.items()
                                         if k not in ('resolved', 'resolution')})
    for fact in facts.values():
        identity = topics[fact['topic_id']]['identity']
        requires_intent = (fact['kind'] in ('operating_state', 'rename', 'workspace_change', 'other') or
                           (fact['kind'] == 'commitment' and 'deal_id' not in identity))
        if requires_intent and 'superseded' not in (fact['disposition'] or {}):
            _require(fact['fact_id'] in intent_facts, 'missing_review_intent')
    for kind, item in proposal_items(report):
        ids = item.get('fact_ids')
        _require(isinstance(ids, list) and bool(ids) and all(isinstance(i, str) for i in ids) and len(ids) == len(set(ids)) and
                 all(i in facts for i in ids), 'unsupported_proposal')
        proposal_id = digest([kind, sorted(ids)])[:24]
        _require(item.get('proposal_id', proposal_id) == proposal_id, 'proposal_id_mismatch')
        item['proposal_id'] = proposal_id
        fields = ('deal_id', 'participant_ids', 'title', 'due_on', 'summary', 'value', 'started_on',
                  'text', 'next_date', 'subject', 'body', 'recipient_party_id', 'fact_ids', 'evidence',
                  'counterpart', 'recipient_ref', 'content_hash', 'due_defaulted')
        bindings[proposal_id] = digest({k: item[k] for k in fields if k in item})
        for fact_id in ids:
            fact = facts[fact_id]
            _require(fact_id not in linked and fact_id not in intent_facts, 'multiple_dispositions')
            _require(fact['disposition'] is None or 'proposal' in fact['disposition'], 'conflicting_disposition')
            identity = topics[fact['topic_id']]['identity']
            _require('deal_id' in identity, 'unresolved_proposal_identity')
            parent = next((u for u in report['deal_updates'] if u.get(kind) is item), item) if kind in ('phase_move', 'next_step') else item
            _require(parent.get('deal_id') == identity['deal_id'] and
                     set(parent.get('participant_ids', [])) == set(identity['participant_ids']), 'proposal_identity_mismatch')
            if kind in ('phase_move', 'next_step'):
                _require(fact['kind'] == {'phase_move': 'phase_change', 'next_step': 'next_step'}[kind], 'proposal_kind_mismatch')
            if kind == 'phase_move':
                _require(item['value'] == fact['asserted_value'], 'proposal_value_mismatch')
            if kind not in ('phase_move', 'next_step'):
                _require(fact['kind'] not in ('phase_change', 'next_step', 'operating_state', 'rename', 'workspace_change'), 'proposal_kind_mismatch')
            linked[fact_id] = proposal_id
    for fact_id, proposal_id in linked.items():
        facts[fact_id]['disposition'] = {'proposal': {'proposal_id': proposal_id}}
    _validate_dispositions(ledger, facts, {q['id'] for q in report.get('review_questions', [])},
                           proposals=set(linked.values()))
    _require(all(w['validated_completion'] for w in ledger['windows']), 'incomplete_windows')
    ledger.update(state='complete', failures=[], proposal_bindings=bindings)
    return ledger


def require_coverage(session_dir, report, read_json):
    ledger = read_json(session_dir / FILE)
    transcript = read_json(session_dir / 'transcript.json')
    _require(ledger and ledger.get('schema') == SCHEMA and ledger.get('state') == 'complete', 'coverage_audit_required')
    _require(ledger.get('session') == session_dir.name and transcript is not None and
             ledger.get('transcript_sha256') == digest(transcript) and
             report.get('coverage_sha256') == digest(ledger), 'coverage_binding_mismatch')
    stored = read_json(session_dir / 'call-context.json') or {}
    if stored.get('call_context') is not None:
        _require(digest(stored['call_context']) == ledger['context_sha256'], 'coverage_context_mismatch')
    _require(reconcile_coverage(ledger, deepcopy(report)) == ledger, 'coverage_report_mismatch')
    return ledger


def add_identity_questions(ledger, report, review_id):
    topics = {t['topic_id']: t for t in ledger['topics']}
    intent_facts = {i for q in report.get('review_questions', []) if q.get('kind') == 'review_intent'
                    for i in q.get('fact_ids', [])}
    for number, fact in enumerate(ledger['facts'], 1):
        if fact['fact_id'] in intent_facts:
            continue
        identity = topics[fact['topic_id']]['identity']
        if 'deal_id' not in identity:
            question_id = review_id(ledger['session'], 'coverage_identity', number)
            fact['disposition'] = {'unresolved_identity': {'review_question_id': question_id}}
            report['review_questions'].append({'id': question_id, 'resolved': False,
                'question': f"Identify {identity['mentioned_name']}: {identity['reason']}"})


def extraction_schema():
    span = {'type': 'object', 'properties': {
        'source_id': {'type': 'string'}, 'segment_index': {'type': 'integer'},
        'char_start': {'type': 'integer'}, 'char_end': {'type': 'integer'},
        'segment_sha256': {'type': 'string'}}, 'required': [
            'source_id', 'segment_index', 'char_start', 'char_end', 'segment_sha256'], 'additionalProperties': False}
    return {'type': 'object', 'additionalProperties': False, 'required': ['topics', 'facts', 'non_fact_spans'],
        'properties': {
            'topics': {'type': 'array', 'items': {'type': 'object', 'required': ['topic_id', 'identity'],
                'properties': {'topic_id': {'type': 'string'}, 'identity': {'type': 'object'}}}},
            'facts': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                'required': ['fact_id', 'topic_id', 'kind', 'statement', 'asserted_value', 'source_spans', 'disposition'],
                'properties': {'fact_id': {'type': 'string'}, 'topic_id': {'type': 'string'},
                    'kind': {'enum': list(KINDS)}, 'statement': {'type': 'string'},
                    'asserted_value': {}, 'source_spans': {'type': 'array', 'items': span},
                    'disposition': {'type': 'null'}}}},
            'non_fact_spans': {'type': 'array', 'items': {'type': 'object', 'required': ['span', 'reason'],
                'properties': {'span': span, 'reason': {'type': 'string'}}}}}}


def extraction_instruction(data_sentence):
    return (
        'Extract every atomic spoken fact before generating proposals. Parking is operating_state, card naming is rename, '
        'account reassignment and prospect routing are workspace_change, owner business work is commitment, and client lifecycle is other. '
        'Set asserted_value to the exact desired string for these operations. ' + data_sentence +
        ' Return topics, facts and non_fact_spans following the schema. Use full transcript segment coordinates, '
        'never substring-relative coordinates. Copy source_id and sha256 from sources into each span. '
        'Account for every nonwhitespace character in the core range with a fact span or a non-fact span and reason. '
        'Overlap provides context. Include all facts intersecting the core and their supporting occurrences in this window. '
        'Use stable topic_id names. Anchor fact_id names to global segment index and atomic assertion number, not window-local numbering. '
        'Keep distinct commitments separate. Keep status and phase_change separate. Join split-turn facts with multiple spans. '
        'Never classify a status, commitment or instruction as non-fact. Non-facts are greetings, filler and questions with no assertion. '
        'Exact identity is {deal_id,participant_ids}, using context IDs. Otherwise identity is '
        '{mentioned_name,candidate_deal_ids,reason}. Use null disposition for proposal facts and unresolved identity. '
        'Leave every disposition null. A separate complete-topic history pass resolves corrections and no-change decisions. '
        'Classify explicit no-change decisions as kind no_change, preserving their source spans. '
        'Never use already_recorded without verified record evidence, which this extraction does not have. '
        'statement paraphrases one assertion. asserted_value stores its value. Never copy raw segments into statements. '
        'Return only the JSON object. A second_check requests an independent audit of an empty first extraction.'
    )


def history_schema():
    return {'type': 'object', 'additionalProperties': False, 'required': ['dispositions'],
        'properties': {'dispositions': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['fact_id', 'disposition'],
            'properties': {'fact_id': {'type': 'string'}, 'disposition': {'type': ['object', 'null']}}}}}}


def history_instruction(data_sentence):
    return ('Reconcile the complete ordered history of one call topic before generating proposals. ' + data_sentence +
        ' Return exactly one disposition entry per supplied fact_id. Keep null for facts needing a proposal or unresolved identity. '
        'Use {superseded:{replacement_fact_id,correction_spans}} only for an explicit later correction, citing the replacement fact source spans. '
        'A correction must follow every occurrence of the earlier assertion. Reinstatement is a later distinct assertion, never an old repeat. '
        'Use {no_change:{decision_spans}} only for a spoken no-change decision. Never use already_recorded or proposal. '
        'Do not invent facts, change statements or omit facts. Status and phase_change remain separate. Evidence contains exact local spans. '
        'Return only {dispositions:[{fact_id,disposition},...]}.')
