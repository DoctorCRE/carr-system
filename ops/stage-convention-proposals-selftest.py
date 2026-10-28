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

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stage', REPO / 'ops/stage-convention-proposals.py')
assert spec is not None and spec.loader is not None
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)

# The grammar ops.adopt_rule (migration 1027) and org-conventions.js parse.
CONVENTION = re.compile(r'^Convention: ([^\n]+)')
CARD = re.compile(r'\nCard: (\{[^\n]*\})[ \t]*(?:\n|$)')


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

    def test_stage_writes_only_missing_keys_with_the_callers_attach(self):
        calls = []

        def call(verb, args):
            calls.append((verb, args))
            if verb == 'read-doctrine':
                return {'sections': [{'section_key': self.plan[0]['section_key']}]}
            return {'ok': True}

        written = stage.stage(call, 'attach-1', self.plan[:3])
        self.assertEqual(written, [p['section_key'] for p in self.plan[1:3]])
        writes = [args for verb, args in calls if verb == 'write-doctrine-section']
        self.assertEqual(len(writes), 2)
        for args in writes:
            self.assertEqual((args['document'], args['base_version'], args['attach_id']), ('team-memory', 0, 'attach-1'))
        self.assertEqual(len({args['idempotency_key'] for args in writes}), 2)

    def test_statement_must_be_one_line(self):
        with self.assertRaises(ValueError):
            stage.body('two\nlines')


if __name__ == '__main__':
    unittest.main()
