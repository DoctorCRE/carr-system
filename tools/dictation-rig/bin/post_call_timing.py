"""Validate source-bound semantic timing judgments and authorize exact dates."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal


WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
          'september', 'october', 'november', 'december')

ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CALENDAR_LITERAL = re.compile(r"(?:(?:by|on|before) )?(" + "|".join(MONTHS)
                              + r") (\d{1,2})(?:,? (\d{4}))?")


@dataclass(frozen=True)
class DeadlineResolution:
    state: Literal['unstated', 'resolved', 'ambiguous', 'contradictory']
    due_on: str | None
    review_reason: str | None = None


def timing_schema() -> dict[str, Any]:
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'sources': {'type': 'array', 'minItems': 1, 'items': {
                'type': 'object', 'additionalProperties': False,
                'properties': {
                    'segment_index': {'type': 'integer', 'minimum': 0},
                    'segment_sha256': {'type': 'string'},
                    'char_start': {'type': 'integer', 'const': 0},
                    'char_end': {'type': 'integer', 'minimum': 1},
                    'speaker': {'type': 'string', 'minLength': 1},
                    'quote': {'type': 'string', 'minLength': 1},
                    'status': {'enum': ['stated', 'unstated', 'uncertain']},
                    'expressions': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
                    'anchor_for': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}},
                }, 'required': ['segment_index', 'segment_sha256', 'char_start', 'char_end', 'speaker', 'quote', 'status', 'expressions']}}},
            'required': ['sources']}


def _exact_date(expression: str, call_date: str | None) -> str | None:
    text = expression.strip().lower()
    try:
        if ISO_DAY.fullmatch(text):
            return date.fromisoformat(text).isoformat()
        calendar = CALENDAR_LITERAL.fullmatch(text)
        if calendar:
            month, day, year = calendar.groups()
            if not year and call_date is None:
                return None
            anchor = date.fromisoformat(call_date) if call_date else None
            result = date(int(year) if year else date.fromisoformat(str(call_date)).year, MONTHS.index(month) + 1, int(day))
            if not year and anchor and result < anchor:
                result = result.replace(year=anchor.year + 1)
            return result.isoformat()
        if call_date is None:
            return None
        anchor = date.fromisoformat(call_date)
        event = re.fullmatch(r"before (" + '|'.join(WEEKDAYS) + r")[’']s lunch call", text)
        if event:
            return _exact_date(event[1], call_date)
        same_day = re.fullmatch(r'today, (' + '|'.join(WEEKDAYS) + r')', text)
        if same_day:
            return call_date if same_day[1] == WEEKDAYS[anchor.weekday()] else None
        relative = re.fullmatch(r'(?:(?:by|on|before) )?(today|tomorrow|day after tomorrow|next week|'
            r'(?:(?:next|this) )?(?:' + '|'.join(WEEKDAYS) + r')(?: next week)?)(?: (?:morning|afternoon|evening))?', text)
        if not relative:
            return None
        phrase = relative[1]
        if phrase.startswith('this ') and phrase.endswith(' next week'):
            return None
        if phrase in ('today', 'tomorrow', 'day after tomorrow'):
            offset = ('today', 'tomorrow', 'day after tomorrow').index(phrase)
        elif phrase == 'next week':
            offset = 7 - anchor.weekday()
        else:
            weekday = next(day for day in WEEKDAYS if day in phrase.split())
            offset = ((7 - anchor.weekday() + WEEKDAYS.index(weekday))
                      if phrase.startswith('next ') or phrase.endswith(' next week')
                      else (WEEKDAYS.index(weekday) - anchor.weekday()) % 7)
            if offset == 0:
                return None
        return (anchor + timedelta(days=offset)).isoformat()
    except (ValueError, OverflowError):
        return None


def task_deadline(item: dict[str, Any], transcript: dict[str, Any] | None,
                  call_date: str | None, ledger: dict[str, Any] | None) -> DeadlineResolution:
    hold = DeadlineResolution('ambiguous', None,
        'Timing evidence is missing, uncertain or unsupported; clarify the exact deadline.')
    timing = item.get('timing')
    if not isinstance(timing, dict) or set(timing) != {'sources'} or not transcript:
        return hold
    sources = timing['sources']
    if not isinstance(sources, list) or not sources:
        return hold
    segments = transcript.get('segments', [])
    if ledger:
        facts = {fact['fact_id']: fact for fact in ledger['facts']}
        ids = item.get('fact_ids', [])
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) or key not in facts for key in ids):
            return hold
        expected = {span['segment_index'] for key in ids for span in facts[key]['source_spans']}
    else:
        # Normalization without a ledger still requires a full, exact source turn.
        expected = {i for i, segment in enumerate(segments) if segment.get('text') == item['evidence']}
        expected.update(source['segment_index'] for source in sources
                        if isinstance(source, dict) and source.get('anchor_for')
                        and type(source.get('segment_index')) is int)
    if not expected or len(sources) != len(expected):
        return hold
    seen: set[int] = set()
    dates: set[str] = set()
    unsupported = False
    stated = False
    bindings: dict[str, list[str]] = {}
    expressions_to_resolve: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or set(source) - {'anchor_for'} != {
                'segment_index', 'segment_sha256', 'char_start', 'char_end', 'speaker', 'quote', 'status', 'expressions'}:
            return hold
        index = source['segment_index']
        if type(index) is not int or index not in expected or index in seen or not 0 <= index < len(segments):
            return hold
        seen.add(index)
        segment = segments[index]
        quote = segment.get('text')
        if (not isinstance(quote, str) or not quote or source['quote'] != quote
                or type(source['char_start']) is not int or source['char_start'] != 0
                or type(source['char_end']) is not int or source['char_end'] != len(quote)
                or not isinstance(source['speaker'], str) or not source['speaker']
                or source['speaker'] != segment.get('speaker')
                or source['segment_sha256'] != hashlib.sha256(quote.encode()).hexdigest()):
            return hold
        expressions = source['expressions']
        if not isinstance(expressions, list) or any(not isinstance(e, str) or not e or e not in quote for e in expressions):
            return hold
        references = source.get('anchor_for', [])
        if (not isinstance(references, list)
                or any(not isinstance(ref, str) or not ref for ref in references)
                or references and (source['status'] != 'stated' or len(expressions) != 1)):
            return hold
        for reference in references:
            bindings.setdefault(reference, []).extend(expressions)
        status = source['status']
        if status == 'unstated' and not expressions:
            continue
        if status != 'stated' or not expressions:
            return hold
        stated = True
        expressions_to_resolve.extend(expressions)
    if seen != expected:
        return hold
    if any(reference not in expressions_to_resolve or reference != 'before the lunch call'
           for reference in bindings):
        return hold
    for expression in expressions_to_resolve:
        if expression == 'before the lunch call':
            anchors = bindings.get(expression)
            if anchors:
                values = {_exact_date(anchor, call_date) for anchor in anchors}
                result = next(iter(values)) if len(values) == 1 and None not in values else None
            else:
                result = call_date if len(expressions_to_resolve) == 1 else None
        else:
            result = _exact_date(expression, call_date)
        if result is None:
            unsupported = True
        else:
            dates.add(result)
    if unsupported:
        return hold
    if len(dates) > 1:
        return DeadlineResolution('contradictory', None,
            'The cited turns give conflicting deadlines; clarify which timing applies.')
    if not stated:
        return DeadlineResolution('unstated', None)
    if len(dates) != 1:
        return hold
    resolved = dates.pop()
    if any(ISO_DAY.fullmatch(e.lower()) or CALENDAR_LITERAL.fullmatch(e.lower())
           for source in sources for e in source['expressions']) and item.get('due_on') != resolved:
        return hold
    return DeadlineResolution('resolved', resolved)
