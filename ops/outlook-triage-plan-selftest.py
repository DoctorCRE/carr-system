#!/usr/bin/env python3
"""Synthetic contract tests for the pure triage planner."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.outlook_triage_plan import (
    Observation, ReadState, RoutingContext, Folder, FolderCatalog, Policy, plan_triage,
)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.message = Observation(
            account='account-001', message_id='message-001', revision='revision-001',
            source_folder_id='inbox-001', read_state=ReadState.READ,
            sender='client-001 folder-A', subject='subject-001', body='body-001',
            recipients=('recipient@example.test',), content_complete=True,
            snapshot_digest='snapshot-001', decoder_pin='decoder-001',
            observed_at='2026-11-03T20:00:00-06:00',
        )
        self.raw = json.loads((ROOT / 'ops/fixtures/outlook-triage/routing.json').read_text())
        self.context = RoutingContext.from_find_results(
            'policy-001', self.raw['guide'], self.raw['find'],
            deal_prefix='Deals/', vendor_prefix='Vendors/', complete=True,
        )
        self.catalog = FolderCatalog('catalog-001', tuple(
            Folder('account-001', f'folder-{n}', path)
            for n, path in enumerate(self.raw['paths'])
        ))
        self.policy = Policy('account-001', 'inbox-001', 'policy-001')

    def plan(self, message=None, **kwargs):
        return plan_triage((message or self.message,), kwargs.get('context', self.context),
                           kwargs.get('catalog', self.catalog), kwargs.get('policy', self.policy))

    def test_unknown_read_or_incomplete_message_has_no_move_intent(self):
        for change in ({'read_state': ReadState.UNKNOWN}, {'content_complete': False},
                       {'body': None}, {'sender': None}, {'subject': None}, {'recipients': None}):
            with self.subTest(change=change):
                result = self.plan(replace(self.message, **change))
                self.assertEqual(result.moves, ())
                self.assertEqual(len(result.leaves), 1)

    def make_context(self, raw=None):
        raw = raw or self.raw
        return RoutingContext.from_find_results(
            'policy-001', raw['guide'], raw['find'], deal_prefix='Deals/',
            vendor_prefix='Vendors/', complete=True)

    def assert_leave(self, reason, message=None, **kwargs):
        result = self.plan(message, **kwargs)
        self.assertEqual(result.moves, ())
        self.assertEqual([leave.reason for leave in result.leaves], [reason])

    def test_read_policy_both_values(self):
        unread = replace(self.message, read_state=ReadState.UNREAD)
        self.assert_leave('unread', unread)
        permissive = replace(self.policy, require_read_before_move=False)
        self.assertEqual(len(self.plan(unread, policy=permissive).moves), 1)
        self.assertEqual(len(self.plan(policy=permissive).moves), 1)
        self.assert_leave('unknown_read_state', replace(unread, read_state=ReadState.UNKNOWN),
                          policy=permissive)

    def test_every_missing_content_field_holds_under_both_policies(self):
        for strict in (True, False):
            for change in ({'body': ''}, {'body': '  '}, {'sender': ''}, {'subject': None},
                           {'recipients': ()}, {'recipients': ('',)}, {'content_complete': False}):
                with self.subTest(strict=strict, change=change):
                    self.assert_leave('incomplete_content', replace(self.message, **change),
                                      policy=replace(self.policy, require_read_before_move=strict))

    def test_deal_precedes_vendor_and_map(self):
        result = self.plan()
        self.assertEqual([(m.rule, m.destination.path) for m in result.moves],
                         [('deal', 'Deals/folder-A')])
        self.assertEqual(result.leaves, ())

    def test_vendor_precedes_map(self):
        result = self.plan(replace(self.message, sender='vendor-001'))
        self.assertEqual([(m.rule, m.destination.path) for m in result.moves],
                         [('vendor', 'Vendors/vendor-001')])

    def test_map_and_no_match(self):
        result = self.plan(replace(self.message, sender='sender-map <sender@example.test>'))
        self.assertEqual([(m.rule, m.destination.path) for m in result.moves],
                         [('map', 'Reference/map-001')])
        self.assert_leave('no_rule_matched', replace(self.message, sender='sender-none'))

    def test_each_ambiguous_rule_stops_fallback(self):
        variants = [
            ('deal', 'client-001 folder-A', 'Deals/folder-B'),
            ('vendor', 'vendor-001', 'Vendors/group/vendor-001'),
            ('map', 'sender-map <sender@example.test>', 'Other/map-001'),
        ]
        for rule, sender, path in variants:
            with self.subTest(rule=rule):
                raw = json.loads(json.dumps(self.raw))
                if rule == 'deal':
                    raw['find'][sender]['parties'][0]['org_name'] = 'folder-B'
                elif rule == 'vendor':
                    raw['guide'] += '\n' + path
                else:
                    raw['guide'] += '\nmap: sender@example.test -> ' + path
                self.assert_leave(rule + '_ambiguous', replace(self.message, sender=sender),
                                  context=self.make_context(raw))

    def test_legacy_party_matching_and_client_ref_relationship(self):
        raw = json.loads(json.dumps(self.raw))
        party = raw['find']['client-001 folder-A']['parties'][0]
        party['name'] = 'client-001 sender-001'
        raw['find']['client-001 sender-001'] = raw['find'].pop('client-001 folder-A')
        raw['find']['client-001 sender-001']['deals'] = [
            {'name': 'folder-B', 'client_ref': 'C-001'},
            {'name': 'folder-A', 'client_ref': 'C-999'},
        ]
        result = self.plan(replace(self.message, sender='sender-001, client-001'),
                           context=self.make_context(raw))
        self.assertEqual(result.moves[0].destination.path, 'Deals/folder-B')
        self.assertEqual(result.moves[0].rule, 'deal')

    def test_nonexact_merged_vendor_and_ambiguous_parties_never_invent_deal(self):
        for change in ('nonexact', 'merged', 'vendor', 'ambiguous'):
            raw = json.loads(json.dumps(self.raw))
            parties = raw['find']['client-002 folder-B']['parties']
            if change == 'nonexact':
                parties[0]['name'] += '-different'
            elif change == 'merged':
                parties[0]['merged'] = True
            elif change == 'vendor':
                parties[0]['kind'] = 'vendor'
            else:
                parties.append(dict(parties[0], ref='C-999'))
            self.assert_leave('no_rule_matched', replace(self.message, sender='client-002 folder-B'),
                              context=self.make_context(raw))

    def test_full_path_resolves_duplicate_leaf_names(self):
        self.assertEqual(self.plan().moves[0].destination.path, 'Deals/folder-A')
        message = replace(self.message, sender='sender-map <sender@example.test>')
        self.assertEqual(self.plan(message).moves[0].destination.path, 'Reference/map-001')

    def test_cross_account_and_source_binding(self):
        self.assert_leave('wrong_account', replace(self.message, account='account-002'))
        self.assert_leave('not_inbox', replace(self.message, source_folder_id='other-001'))
        catalog = replace(self.catalog, folders=tuple(replace(f, account='account-002')
                                                     for f in self.catalog.folders))
        self.assert_leave('destination_unresolved', catalog=catalog)

    def test_missing_renamed_ambiguous_or_reused_folder_identity_holds(self):
        target = self.catalog.folders[0]
        variants = [self.catalog.folders[1:],
                    (replace(target, path='Deals/renamed'),) + self.catalog.folders[1:],
                    self.catalog.folders + (replace(target, folder_id='duplicate-path'),),
                    self.catalog.folders + (replace(target, path='Other/reused-id'),),
                    (replace(target, folder_id=''),) + self.catalog.folders[1:]]
        for folders in variants:
            self.assert_leave('destination_unresolved', catalog=replace(self.catalog, folders=folders))

    def test_already_at_target_has_no_intent(self):
        catalog = replace(self.catalog, folders=(replace(self.catalog.folders[0],
                          folder_id='inbox-001'),) + self.catalog.folders[1:])
        self.assert_leave('already_at_destination', catalog=catalog)

    def test_stale_or_unavailable_routing_never_falls_back(self):
        self.assert_leave('stale_policy', context=replace(self.context, version='policy-old'))
        self.assert_leave('routing_unavailable', context=replace(self.context, complete=False))
        self.assert_leave('routing_unavailable', context=replace(self.context, find_results={}))
        self.assert_leave('routing_unavailable', catalog=replace(self.catalog, version=''))

    def test_identity_provenance_is_required(self):
        for field in ('message_id', 'revision', 'snapshot_digest', 'decoder_pin', 'observed_at'):
            self.assert_leave('identity_unverified', replace(self.message, **{field: ''}))
        for stamp in ('not-a-date', '2026-11-03T20:00:00'):
            self.assert_leave('identity_unverified', replace(self.message, observed_at=stamp))

    def test_duplicate_observations_are_all_held(self):
        result = plan_triage((self.message, replace(self.message, revision='different')),
                             self.context, self.catalog, self.policy)
        self.assertEqual(result.moves, ())
        self.assertEqual([d.reason for d in result.leaves], ['duplicate_identity'] * 2)

    def test_bounded_ordered_batch_and_tenfold_overflow(self):
        messages = tuple(replace(self.message, message_id=f'message-{i}') for i in range(1500))
        result = plan_triage(messages, self.context, self.catalog, self.policy)
        self.assertEqual(len(result.moves), 150)
        self.assertEqual(len(result.leaves), 1350)
        self.assertEqual(sum(d.reason == 'intent_cap_reached' for d in result.leaves), 850)
        self.assertEqual(sum(d.reason == 'observation_cap_reached' for d in result.leaves), 500)
        self.assertEqual([m.observation.message_id for m in result.moves],
                         [f'message-{i}' for i in range(150)])

    def test_intent_binds_all_executor_preconditions(self):
        intent = self.plan().moves[0]
        self.assertEqual(intent.observation, self.message.reference())
        self.assertFalse(hasattr(intent.observation, "body"))
        self.assertEqual(intent.destination, self.catalog.folders[0])
        self.assertEqual(intent.routing_version, self.policy.routing_version)
        self.assertEqual(intent.catalog_version, self.catalog.version)
        self.assertIs(intent.require_read_before_move, True)

    def test_pure_repeatable_planning_and_frozen_results(self):
        from dataclasses import FrozenInstanceError
        with patch('subprocess.run', side_effect=AssertionError('no external calls')), \
                patch('builtins.open', side_effect=AssertionError('no filesystem')):
            first = self.plan()
            self.assertEqual(first, self.plan())
        with self.assertRaises(FrozenInstanceError):
            first.moves[0].rule = 'map'
        with self.assertRaises(TypeError):
            self.context.find_results['new'] = {}
        with self.assertRaises(TypeError):
            self.context.find_results[self.message.sender]['parties'][0]['merged'] = True

    def test_find_snapshot_copies_input_and_rejects_incomplete_shapes(self):
        self.raw['find'][self.message.sender]['parties'][0]['merged'] = True
        self.assertEqual(self.plan().moves[0].rule, 'deal')
        for invalid in ({}, {'parties': [], 'deals': None},
                        {'parties': [{'name': 'client-001'}], 'deals': []}):
            raw = json.loads(json.dumps(self.raw))
            raw['find'][self.message.sender] = invalid
            with self.assertRaises(ValueError):
                self.make_context(raw)

    def test_find_snapshot_requires_boolean_completeness_and_live_identity_fields(self):
        for complete in ('false', 1, None):
            with self.assertRaises(ValueError):
                RoutingContext.from_find_results(
                    'policy-001', self.raw['guide'], self.raw['find'],
                    deal_prefix='Deals/', vendor_prefix='Vendors/', complete=complete)
        for field in ('ref', 'name', 'kind'):
            raw = json.loads(json.dumps(self.raw))
            raw['find'][self.message.sender]['parties'][0][field] = None
            with self.assertRaises(ValueError):
                self.make_context(raw)

    def test_invalid_policy_is_rejected(self):
        for change in ({'max_intents': 0}, {'max_observations': -1},
                       {'require_read_before_move': 'false'}, {'routing_version': ''}):
            with self.assertRaises(ValueError):
                replace(self.policy, **change)


if __name__ == '__main__':
    unittest.main()
