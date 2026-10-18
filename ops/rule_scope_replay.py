"""Offline selection with independently frozen pre-cutover moment decisions.

This adapter tests migration plumbing. It does not calibrate the live moment
judge or claim that the synthetic catalog is the production scope catalog.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from lib.rule_delivery_preuse import digest
from lib.rule_scope_lookup import select_rules

REPO = Path(__file__).resolve().parent.parent


class RetiredRules:
    """Apply confirmed retirements without changing historical replay inputs."""

    def __init__(self, repo=REPO):
        path = Path(repo)/'ops/config/rule-retirements.v1.json'
        doc = json.loads(path.read_text())
        if (doc.get('schema') != 'rule-retirements/v1'
                or not isinstance(doc.get('rules'), dict)
                or any(not re.fullmatch(r'[0-9a-f]{8}', rid)
                       or not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date)
                       for rid, date in doc['rules'].items())):
            raise ValueError(f'{path}: invalid rule-retirements/v1 document')
        self.ids = frozenset(doc['rules'])

    def active(self, ids):
        return [rid for rid in ids if rid not in self.ids]

    def label(self, label):
        expected = self.active(label['expected'])
        active = dict(label, expected=expected, allowed=self.active(label['allowed']))
        if expected != label['expected']:
            active['should_not_fire'] = not expected
        return active

    def expectations(self, expectations):
        return dict(expectations, labelled=self.active(expectations['labelled']),
                    cases={cid: self.label(label) for cid, label in expectations['cases'].items()})


def _portable(value, root):
    if isinstance(value, str):
        return value.replace(root, '{{REPO}}')
    if isinstance(value, dict):
        return {key: _portable(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable(item, root) for item in value]
    return value


class FrozenSelection:
    def __init__(self, repo=REPO):
        repo = Path(repo)
        self.repo = repo
        self.retirements = RetiredRules(repo)
        self.fixture = json.loads((repo/'ops/fixtures/rule-scope-compatibility.v1.json').read_text())
        corpus = json.loads((repo/'ops/config/rule-selection-corpus.v1.json').read_text())['rules']
        corpus = [rule for rule in corpus if rule['id'] not in self.retirements.ids]
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
        key = digest(_portable(event, str(self.repo)))
        if key not in self.fixture['moment_decisions']:
            raise ValueError('no_frozen_moment_decision')
        selected = set(self.retirements.active(self.fixture['moment_decisions'][key]))
        def judge(state, questions):
            return {'model': 'jev-1.13.0', 'answers': {
                name: {'type': 'choice', 'confidence': 1.0,
                       'choice': 'applies' if name[5:] in selected else 'not_applicable'}
                for name in questions}}
        return select_rules(self.catalog, event, judge=judge)
