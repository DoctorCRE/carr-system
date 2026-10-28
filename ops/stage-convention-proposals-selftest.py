#!/usr/bin/env python3
"""Offline checks for ops/stage-convention-proposals.py (WR-000562 S4).

The PostgreSQL half (every staged proposal passes the write gates and adopts
through adopt-rule) is mcp-server/test/org-conventions-postgres.test.mjs.
"""
import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stage', REPO / 'ops/stage-convention-proposals.py')
assert spec is not None and spec.loader is not None
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)

# The grammar ops.adopt_rule (migration 1027) and org-conventions.js parse.
CONVENTION = re.compile(r'^Convention: ([^\n]+)')
CARD = re.compile(r'\nCard: (\{[^\n]*\})[ \t]*(?:\n|$)')
PACKS = re.compile(r'\nPacks: ([^\n]*)')
# The production pack index (standing-context rule_delivery.pack_index,
# 2026-10-28). ops.adopt_rule refuses any other name.
PACK_INDEX = {'client-deal', 'delegation-council', 'engineering-git', 'governance-rules',
              'joe-comms', 'joe-development', 'records-intake', 'scheduled-automation',
              'source-study', 'surface-doctrine', 'vendor-intros'}


class StageTests(unittest.TestCase):
    def setUp(self):
        self.plan = stage.plan()
        self.cards = json.loads((REPO / 'ops/config/rule-cards.v1.json').read_text())['cards']
        self.ledger = json.loads((REPO / 'ops/config/convention-proposal-ledger.v1.json').read_text())['files']

    def test_every_card_and_every_uncovered_file_is_one_proposal(self):
        uncovered = [name for name, row in self.ledger.items() if not row['covered_by']]
        self.assertEqual(len(self.plan), len(self.cards) + len(uncovered))
        keys = {p['section_key'] for p in self.plan}
        self.assertTrue(all(k.startswith('conventions/proposed/') for k in keys))
        self.assertIn('conventions/proposed/card-k01', keys)
        for name in uncovered:
            self.assertIn(f'conventions/proposed/{name}', keys)

    def test_ledger_maps_every_org_and_dup_file(self):
        self.assertEqual(len(self.ledger), 54)
        self.assertEqual({row['class'] for row in self.ledger.values()}, {'ORG', 'DUP'})
        for name, row in self.ledger.items():
            self.assertTrue(row['covered_by'] or row.get('statement'), name)

    def test_each_body_parses_as_adopt_rule_reads_it(self):
        by_key = {p['section_key']: p['body_text'] for p in self.plan}
        for card in self.cards:
            text = by_key[f"conventions/proposed/card-{card['id'].lower()}"]
            self.assertEqual(CONVENTION.match(text)[1], card['wording'])
            meta = json.loads(CARD.search(text)[1])
            self.assertEqual({**meta, 'wording': card['wording'], 'word_count': card['word_count']}, card,
                             'statement plus card metadata rebuilds the card exactly')
        for p in self.plan:
            self.assertNotIn('—', CONVENTION.match(p['body_text'])[1])

    def test_every_card_less_convention_names_existing_packs(self):
        by_key = {p['section_key']: p['body_text'] for p in self.plan}
        for card in self.cards:
            self.assertIsNone(PACKS.search(by_key[f"conventions/proposed/card-{card['id'].lower()}"]))
        uncovered = {name: row for name, row in self.ledger.items() if not row['covered_by']}
        self.assertEqual(len(uncovered), 45)
        for name, row in uncovered.items():
            text = by_key[f'conventions/proposed/{name}']
            self.assertIsNone(CARD.search(text), name)
            packs = [p.strip() for p in PACKS.search(text)[1].split(',')]
            self.assertTrue(packs, name)
            self.assertEqual(packs, row['packs'], name)
            self.assertLessEqual(set(packs), PACK_INDEX, name)
        practice = {'team-territory', 'owners-contacted-are-not-leads', 'compliance-out-of-scope',
                    'client-portal-standard-site', 'client-sites-on-share-doctorcre',
                    'client-sites-flexible-and-spend-safe', 'post-call-e2e-review'}
        for name in practice:
            self.assertIn('client-deal', uncovered[name]['packs'], name)
        self.assertIn('joe-comms', uncovered['work-calendar-only-in-outlook']['packs'])

    def test_stage_writes_only_missing_keys_with_the_callers_attach(self):
        calls = []

        def call(verb, args):
            calls.append((verb, args))
            if verb == 'read-doctrine':
                return {'sections': [{'section_key': self.plan[0]['section_key']}]}
            return {'ok': True}

        written, retired = stage.stage(call, 'attach-1', self.plan[:3])
        self.assertEqual(written, [p['section_key'] for p in self.plan[1:3]])
        self.assertEqual(retired, [])
        writes = [args for verb, args in calls if verb == 'write-doctrine-section']
        self.assertEqual(len(writes), 2)
        for args in writes:
            self.assertEqual((args['document'], args['base_version'], args['attach_id']), ('team-memory', 0, 'attach-1'))
        self.assertEqual(len({args['idempotency_key'] for args in writes}), 2)

    def test_rerun_after_adoption_skips_retired_keys_and_still_fails_on_other_refusals(self):
        adopted = self.plan[0]['section_key']

        def call(verb, args):
            if verb == 'read-doctrine':
                return {'sections': []}   # the snapshot holds active sections only
            if args['section_key'] == adopted:
                raise stage.VerbRefused('version_conflict')
            return {'ok': True}

        written, retired = stage.stage(call, 'attach-1', self.plan[:2])
        self.assertEqual((written, retired), ([self.plan[1]['section_key']], [adopted]))

        def refusing(verb, args):
            if verb == 'read-doctrine':
                return {'sections': []}
            raise stage.VerbRefused('memory_attach_required')

        with self.assertRaises(stage.VerbRefused):
            stage.stage(refusing, 'attach-1', self.plan[:1])

    def test_run_sh_call_reads_the_tool_error_code(self):
        failed = mock.Mock(returncode=1, stdout='', stderr='local-verb identity -> x\nTOOL ERROR {\n  "error": "version_conflict",\n  "current_version": 2\n}\n')
        with mock.patch.object(stage.subprocess, 'run', return_value=failed):
            with self.assertRaises(stage.VerbRefused) as caught:
                stage.run_sh_call('write-doctrine-section', {})
        self.assertEqual(caught.exception.code, 'version_conflict')

    def test_statement_must_be_one_line(self):
        with self.assertRaises(ValueError):
            stage.body('two\nlines', packs=['client-deal'])

    def test_a_proposal_carries_a_card_or_packs_never_neither(self):
        with self.assertRaises(ValueError):
            stage.body('No delivery.')
        with self.assertRaises(ValueError):
            stage.body('Both.', {'id': 'K99', 'placement': 'CORE'}, packs=['client-deal'])


if __name__ == '__main__':
    unittest.main()
