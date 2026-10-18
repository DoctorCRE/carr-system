#!/usr/bin/env python3
"""Offline card contract and replay checks, collected by ops/ci.sh gates."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

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
        self.assertEqual(len(self.cards), 60)
        self.assertEqual(len(self.core), 12)
        self.assertEqual(len({c['id'] for c in self.cards}), 60)

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

    def test_inherited_routing_matches_without_pattern(self):
        card = next(c for c in self.cards if c['id'] == 'R07')
        selector = CardSelector([card])
        event = card['fixtures']['negative']['event']
        selected = selector.select(event, inherited_rule_ids={'67e48aaf'})
        self.assertEqual([c['id'] for c in selected], ['R07'])
        self.assertEqual(selector.last_selection['inherited_card_ids'], ['R07'])
        self.assertEqual(selector.select(event, inherited_rule_ids={'67e48aaf'}), [])

    def test_cap_reports_inherited_deferrals_and_uncapped_counterfactual(self):
        ids = {'L01', 'L02', 'L03', 'L04'}
        cards = [c for c in self.cards if c['id'] in ids]
        sources = {rid for c in cards for rid in c['source_rule_ids']}
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Read', 'tool_input': {}}
        capped = CardSelector(cards)
        got = capped.select(event, inherited_rule_ids=sources)
        self.assertEqual([c['id'] for c in got], ['L02', 'L04', 'L03'])
        self.assertEqual(capped.last_selection['deferred_card_ids'], ['L01'])
        self.assertEqual(capped.last_selection['inherited_deferred_card_ids'], ['L01'])
        self.assertEqual([c['id'] for c in capped.select(event, inherited_rule_ids=sources)], ['L01'])
        uncapped = CardSelector(cards)
        self.assertEqual(len(uncapped.select(event, inherited_rule_ids=sources, cap=None)), 4)
        self.assertEqual(uncapped.last_selection['deferred_card_ids'], [])

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
        self.assertNotIn('R07', {c['id'] for c in selector.select(
            card['fixtures']['positive']['event'], inherited_rule_ids={'67e48aaf'})})

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
        case = next(case for case in self.eval.load_cases('all') if case['id'] == 'v2-disp-007')
        world = self.eval.World(self.eval.load_expectations())
        first = self.eval.replay(world, case, variant='cards')
        self.assertEqual(first, self.eval.replay(world, case, variant='cards'))
        self.assertIn('b1521526', first[0]['ids'])
        self.assertEqual(first[0]['inherited_rule_ids'],
                         world.selection.fixture['cases'][case['id']]['events'][0])

    def test_uncapped_inheritance_preserves_covered_baseline_availability(self):
        world = self.eval.World(self.eval.load_expectations())
        cards = load_cards()
        mapped = {rid for card in cards for rid in card['source_rule_ids']}
        core = {rid for card in cards if card['placement'] == 'CORE' for rid in card['source_rule_ids']}
        for case in self.eval.load_cases('all'):
            with self.subTest(case=case['id']):
                base = self.eval.replay(world, case)
                capped = self.eval.replay(world, case, 'cards')
                uncapped = self.eval.replay(world, case, 'cards-uncapped')
                baseline_ids = {rid for ev in base for rid in ev['ids']} & mapped
                available = core | {rid for ev in uncapped for rid in ev['ids']}
                self.assertLessEqual(baseline_ids, available)
                self.assertTrue(all(len(ev['card_ids']) <= 3 for ev in capped))
                for ev in capped:
                    self.assertLessEqual(set(ev['inherited_deferred_card_ids']), set(ev['deferred_card_ids']))

    def test_live_route_hook_delivers_exactly_the_offline_capped_cards(self):
        result = self.eval.card_live_parity()
        self.assertEqual(result['cases'], 339)
        self.assertEqual((result['mismatched_events'], result['events_resending_card_rules_as_rules'],
                          result['events_over_cap']), (0, 0, 0))
        world = self.eval.World(self.eval.load_expectations())
        delivered = sum(len(ev['card_ids']) for case in self.eval.load_cases('all')
                        for ev in self.eval.replay_cards_live(world, case))
        self.assertGreater(delivered, 0)


class CorePlacementTests(unittest.TestCase):
    """CORE cards ride the boot arm (startup, resume, clear, compact) and SubagentStart."""
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('boot_gate_hook', REPO / 'hooks/rule-boot-gate.py')
        cls.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hook)
        cls.core = [c for c in load_cards() if c['placement'] == 'CORE']

    def run_hook(self, payload, arm='RULE BOOT: arm text'):
        with mock.patch('lib.rule_boot_gate.arm_session', return_value=arm):
            return self.hook.decide(payload).stdout

    def assert_core(self, text):
        for card in self.core:
            self.assertIn(f"[{card['id']}] {card['title']}: {card['wording']}\n  {card['acknowledgement']}", text)

    def test_session_start_and_post_compaction_reboot_carry_core_cards(self):
        for source in ('startup', 'resume', 'clear', 'compact'):
            with self.subTest(source=source):
                out = self.run_hook({'hook_event_name': 'SessionStart', 'session_id': 's', 'source': source})
                self.assertTrue(out.startswith('RULE BOOT: arm text\n\nRULE CARDS (always-on core)'))
                self.assert_core(out)

    def test_core_page_fits_session_start_cap_beside_the_boot_instructions(self):
        from lib.rule_boot_gate import fetch_instructions
        out = self.run_hook({'hook_event_name': 'SessionStart', 'session_id': 's', 'source': 'startup'},
                            arm=fetch_instructions(list(range(1, 16)), 'sha256:' + 'a' * 64, 15))
        self.assertLess(len(out), 10000)

    def test_subagent_start_carries_core_cards(self):
        out = json.loads(self.run_hook({'hook_event_name': 'SubagentStart', 'session_id': 's',
                                        'agent_id': 'a1', 'agent_type': 'Explore'}))
        specific = out['hookSpecificOutput']
        self.assertEqual(specific['hookEventName'], 'SubagentStart')
        self.assertNotIn('permissionDecision', specific)
        self.assert_core(specific['additionalContext'])

    def test_core_card_failure_fails_open(self):
        with mock.patch('lib.rule_cards.core_text', side_effect=ValueError('broken cards')):
            self.assertEqual(self.run_hook({'hook_event_name': 'SessionStart', 'session_id': 's',
                                            'source': 'compact'}), 'RULE BOOT: arm text\n')
            self.assertEqual(self.run_hook({'hook_event_name': 'SubagentStart', 'session_id': 's'}), '')



if __name__ == '__main__':
    unittest.main()
