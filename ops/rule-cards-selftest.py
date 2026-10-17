#!/usr/bin/env python3
"""Offline card contract and replay checks, collected by ops/ci.sh gates."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.rule_cards import CardSelector, load_cards


class CardTests(unittest.TestCase):
    def setUp(self):
        self.cards = load_cards()
        self.core = {c['id'] for c in self.cards if c['placement'] == 'CORE'}

    def ids(self, selector, event, now=0):
        return {c['id'] for c in selector.select(event, now=now)}

    def test_approved_inventory(self):
        self.assertEqual(len(self.cards), 61)
        self.assertEqual(len(self.core), 12)
        self.assertEqual(len({c['id'] for c in self.cards}), 61)

    def test_core_always_available(self):
        selector = CardSelector(self.cards)
        event = {'hook_event_name': 'SessionStart'}
        self.assertEqual(self.ids(selector, event), self.core)
        self.assertEqual(self.ids(selector, event), self.core)

    def test_all_positive_and_negative_fixtures(self):
        for card in self.cards:
            if card['placement'] == 'CORE':
                continue
            for polarity, expected in [('positive', True), ('negative', False)]:
                with self.subTest(card=card['id'], polarity=polarity):
                    event = card['fixtures'][polarity]['event']
                    selector = CardSelector([*filter(lambda c: c['placement'] == 'CORE', self.cards), card])
                    if card['id'] == 'A15' and expected:
                        for now in (0, 1, 2):
                            self.assertNotIn('A15', self.ids(selector, event, now))
                    self.assertEqual(card['id'] in self.ids(selector, event, 3), expected)
            for prompt in card['fixtures'].get('example_prompts', []):
                with self.subTest(card=card['id'], prompt=prompt):
                    selector = CardSelector([card])
                    self.assertIn(card['id'], self.ids(selector, {'hook_event_name': 'UserPromptSubmit', 'prompt': prompt}))
            if 'negative_prompt' in card['fixtures']:
                selector = CardSelector([card])
                self.assertNotIn(card['id'], self.ids(selector, {'hook_event_name': 'UserPromptSubmit', 'prompt': card['fixtures']['negative_prompt']}))

    def test_collision_priority_and_deferred_delivery(self):
        selector = CardSelector(self.cards)
        card = next(c for c in self.cards if c['id'] == 'L07')
        event = card['fixtures']['positive']['event']
        self.assertEqual(self.ids(selector, event) - self.core, {'L07', 'L02', 'L04'})
        self.assertEqual(self.ids(selector, event) - self.core, {'L03', 'L01'})
        self.assertEqual(self.ids(selector, event) - self.core, set())
        selector.reset_boot()
        self.assertEqual(self.ids(selector, event) - self.core, {'L07', 'L02', 'L04'})

    def test_second_documented_collision(self):
        selector = CardSelector(self.cards)
        event = next(c for c in self.cards if c['id'] == 'L08')['fixtures']['positive']['event']
        self.assertEqual(self.ids(selector, event) - self.core, {'L07', 'L08', 'L05'})
        self.assertEqual(self.ids(selector, event) - self.core, {'L01'})

    def test_personal_scope(self):
        card = next(c for c in self.cards if c['id'] == 'R07')
        selector = CardSelector(self.cards, personal_scope='dell-personal')
        self.assertNotIn('R07', self.ids(selector, card['fixtures']['positive']['event']))

    def test_non_check_failures_do_not_advance_counter(self):
        card = next(c for c in self.cards if c['id'] == 'A15')
        selector = CardSelector([card])
        positive = card['fixtures']['positive']['event']
        negative = card['fixtures']['negative']['event']
        for now in (0, 1, 2):
            self.assertNotIn('A15', self.ids(selector, positive, now))
        self.assertNotIn('A15', self.ids(selector, negative, 3))
        self.assertIn('A15', self.ids(selector, positive, 4))

    def test_failure_window_expires(self):
        card = next(c for c in self.cards if c['id'] == 'A15')
        selector = CardSelector([card])
        event = card['fixtures']['positive']['event']
        for now in (0, 1, 2, 1803, 1804, 1805):
            self.assertNotIn('A15', self.ids(selector, event, now))
        self.assertIn('A15', self.ids(selector, event, 1806))


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('card_eval', REPO / 'evals/rule-delivery/run_eval.py')
        cls.eval = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.eval)

    def test_path_scoring_uses_source_rule_union(self):
        result = self.eval.score_card_paths(
            {'a', 'b'}, {'a', 'b', 'acceptable'}, {'a', 'b', 'acceptable', 'false'},
            {'boot': {'a', 'false'}, 'prompt': {'b', 'acceptable'}, 'tool': {'b'}})
        self.assertEqual(result['boot']['recall'], 0.5)
        self.assertEqual(result['boot']['precision'], 0.5)
        self.assertEqual(result['available']['hits'], 2)
        self.assertEqual(result['available']['true_deliveries'], 3)
        self.assertEqual(result['available']['false_deliveries'], 1)
        self.assertEqual(result['available']['precision'], 0.75)

    def test_pending_gold_is_neither_precise_nor_false(self):
        label = {'expected': ['a'], 'allowed': ['a']}
        obs = {'delivered': ['a', 'pending', 'false'], 'prompt_delivered': [],
               'events': 1, 'tokens': 1, 'over_cap_events': 0}
        grade = self.eval.grade_observation(label, obs, {'a', 'false'})
        summary = self.eval.summarize([{'detail': grade}])
        self.assertEqual(summary['precision'], 0.5)
        self.assertEqual(summary['ungraded_rules'], 1)

    def test_card_replay_isolates_epochs_between_cases(self):
        case = {'id': 'fixture', 'prompt': 'heres the token use it for the cf deploy', 'tool_calls': []}
        first = self.eval.replay(None, case, variant='cards')
        self.assertEqual(first, self.eval.replay(None, case, variant='cards'))
        self.assertIn('67e48aaf', first[0]['ids'])



if __name__ == '__main__':
    unittest.main()
