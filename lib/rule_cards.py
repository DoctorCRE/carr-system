"""Deterministic offline card selection. No hooks, model calls or persistent state."""
from collections import deque
import json
from pathlib import Path
import re

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'ops/config/rule-cards.v1.json'
FAILURE_STATE = ('4th failed test/build/check command in the session within 30 minutes '
                 '(counter kept by the router, as hook_meter.py does; not a hook payload field)')


def load_cards(path=DEFAULT_PATH):
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    if document['schema'] != 'rule-cards/v1':
        raise ValueError('unsupported card schema')
    return document['cards']


class CardSelector:
    """One session, one boot epoch. select returns core plus fresh matched cards.

    now is caller-supplied monotonic seconds, so replay never reads a wall clock.
    reset_boot re-arms delivery, retaining the session's rolling failure history.
    inherited_rule_ids come from the existing deterministic routing adapter.
    cap=None is the offline uncapped counterfactual; normal delivery uses three.
    """
    def __init__(self, cards, personal_scope='joe-personal'):
        self.core = []
        self.routed = []
        self.seen = set()
        self.failures = deque()
        for card in cards:
            if card['scope'] not in ('shared', personal_scope):
                continue
            if card['placement'] == 'CORE':
                self.core.append(card)
                continue
            matchers = []
            for matcher in card['triggers']['matchers']:
                if matcher.get('router_state') not in (None, FAILURE_STATE):
                    raise ValueError(f"{card['id']}: unknown router state")
                compiled = {key: re.compile(value) for key, value in matcher.items()
                            if key.endswith('_regex')}
                matchers.append((matcher['event'], compiled, bool(matcher.get('router_state'))))
            self.routed.append((card, matchers))
        self.core.sort(key=lambda c: c['core_rank'])
        self.routed.sort(key=lambda row: (row[0]['delivery_priority'], row[0]['id']))

    def reset_boot(self):
        self.seen.clear()

    def select(self, event, *, now=0, inherited_rule_ids=(), cap=3):
        inherited_rule_ids = set(inherited_rule_ids)
        fields = {'tool_name_regex': event.get('tool_name', ''),
                  'input_regex': json.dumps(event.get('tool_input', {}), ensure_ascii=False, sort_keys=True),
                  'response_regex': json.dumps(event.get('tool_response', {}), ensure_ascii=False, sort_keys=True),
                  'prompt_regex': event.get('prompt', '')}
        matched = []
        counted_failure = False
        while self.failures and now - self.failures[0] > 1800:
            self.failures.popleft()
        for card, matchers in self.routed:
            for hook, patterns, needs_failures in matchers:
                if hook != event.get('hook_event_name'):
                    continue
                if not all(pattern.search(fields[key]) for key, pattern in patterns.items()):
                    continue
                if needs_failures:
                    if not counted_failure:
                        self.failures.append(now)
                        counted_failure = True
                    if len(self.failures) < 4:
                        continue
                matched.append(card)
                break
        inherited = {card['id'] for card, _ in self.routed
                     if set(card['source_rule_ids']) & inherited_rule_ids}
        pattern_ids = {card['id'] for card in matched}
        eligible = [card for card, _ in self.routed
                    if card['id'] in pattern_ids | inherited and card['id'] not in self.seen]
        fresh = eligible if cap is None else eligible[:cap]
        deferred = eligible[len(fresh):]
        self.last_selection = {
            'inherited_card_ids': sorted(inherited),
            'eligible_card_ids': [card['id'] for card in eligible],
            'deferred_card_ids': [card['id'] for card in deferred],
            'inherited_deferred_card_ids': [card['id'] for card in deferred if card['id'] in inherited],
        }
        self.seen.update(card['id'] for card in fresh)
        return self.core + fresh
