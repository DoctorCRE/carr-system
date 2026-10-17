"""Offline selection with independently frozen pre-cutover moment decisions.

This adapter tests migration plumbing. It does not calibrate the live moment
judge or claim that the synthetic catalog is the production scope catalog.
"""
import json
from functools import lru_cache
from pathlib import Path
from lib.rule_delivery_preuse import digest
from lib.rule_scope_lookup import select_rules

REPO = Path(__file__).resolve().parent.parent


class FrozenSelection:
    def __init__(self, repo=REPO):
        repo = Path(repo)
        self.fixture = json.loads((repo/'ops/fixtures/rule-scope-compatibility.v1.json').read_text())
        corpus = json.loads((repo/'ops/config/rule-selection-corpus.v1.json').read_text())['rules']
        scopes = json.loads((repo/'ops/fixtures/rule-delivery-eval/scope-backfills.json').read_text())['scopes']
        self.catalog = {'ok': True, 'schema': 'rule-scope-catalog/v1',
                        'corpus_digest': digest(corpus), 'identity': {
                            'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                            'sponsoring_human_id': 'joe'},
                        'rules': [dict(r, scope=dict(self.fixture['structural_scopes'].get(r['id'], {}),
                                                 moment=scopes.get(r['id'], {}).get('moment')
                                                 or r['statement']), personal_to=None)
                                  for r in corpus]}

    def select(self, event):
        return self._select(json.dumps(event, sort_keys=True))

    @lru_cache(maxsize=1024)
    def _select(self, serialized):
        event = json.loads(serialized)
        key = digest(event)
        if key not in self.fixture['moment_decisions']:
            raise ValueError('no_frozen_moment_decision')
        selected = set(self.fixture['moment_decisions'][key])
        def judge(state, questions):
            return {'model': 'jev-1.13.0', 'answers': {
                name: {'type': 'choice', 'confidence': 1.0,
                       'choice': 'applies' if name[5:] in selected else 'not_applicable'}
                for name in questions}}
        return select_rules(self.catalog, event, judge=judge)
