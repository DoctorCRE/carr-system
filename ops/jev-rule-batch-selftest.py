#!/usr/bin/env python3
"""Offline contract for one shared-state rule-binding request."""
import importlib.util
import hashlib
import json
import unittest
from pathlib import Path

import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from lib.rule_scope_lookup import select_rules
path = Path(__file__)


class Client:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


class SemanticTestCase(unittest.TestCase):
    def run(self,result=None):
        import os,tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,CARR_JEV_SEMANTIC_CACHE=tmp+'/cache'):
            return super().run(result)

class BatchContract(SemanticTestCase):
    def test_heldout_serial_labels_match_control_receipts(self):
        fixture = json.loads(path.with_name("fixtures").joinpath("rule-batch-parity.v1.json").read_text())
        client = __import__('ops.typesafe_client', fromlist=['state_sha256'])
        receipts = {receipt["state_sha256"]: receipt for receipt in fixture["serial_control_receipts"]}
        cases = {case["id"]: case for case in fixture["cases"]}
        for row in fixture["fresh_replays"][-1]["heldout"]:
            for rule_id, probability in row["serial"].items():
                rule = fixture["serial_control_rules"][rule_id]
                state = {"situation": cases[row["case"]]["prompt"], "rule_title": rule["gist"],
                         "rule": rule["statement"] or rule["gist"], "rule_context": rule["context"]}
                receipt = receipts[client.state_sha256(state)]
                self.assertEqual(receipt["model"], fixture["model"])
                self.assertEqual(receipt["answers"]["binds"]["noul"], probability)

    def test_variant_tables_recompute_from_frozen_scores(self):
        fixture = json.loads(path.with_name("fixtures").joinpath("rule-batch-parity.v1.json").read_text())
        def measure(rows, floor, serial=False):
            tp = fp = fn = 0
            losses = []
            for row in rows:
                for rule_id, score in row["batch"].items():
                    gold = row["serial"][rule_id] >= .75 if serial else rule_id == row["target"]
                    predicted = score >= floor
                    tp += int(gold and predicted)
                    fp += int(not gold and predicted)
                    fn += int(gold and not predicted)
                    losses.append((score - int(gold)) ** 2)
            return {"tp": tp, "fp": fp, "fn": fn, "recall": tp / (tp + fn),
                    "precision": tp / (tp + fp) if tp + fp else 0,
                    "brier": sum(losses) / len(losses)}
        for replay in fixture["fresh_replays"]:
            with self.subTest(variant=replay["variant"]):
                self.assertEqual(measure(replay["training"], replay["floor"]), replay["train_metrics"])
                self.assertEqual(measure(replay["heldout"], replay["floor"], True), replay["test_metrics"])
                for grid in replay["threshold_scores"]:
                    actual = measure(replay["training"], grid["floor"])
                    self.assertEqual({key: actual[key] for key in grid if key != "floor"},
                                     {key: value for key, value in grid.items() if key != "floor"})

    def test_shared_request_preserves_typed_moment_decisions_and_never_retries(self):
        source = dict(ok=True, schema='rule-scope-catalog/v1', corpus_digest='a'*64,
                      identity=dict(agent_principal_id='joe-local', runtime_principal='joe-local', sponsoring_human_id='joe'),
                      rules=[dict(id=rid, statement=text, personal_to=None, scope={'moment': text})
                             for rid,text in [('11111111','Before sending email'),('22222222','Before reviewing code')]])
        calls=[]
        def judge(state, questions):
            calls.append(state)
            return {'model':'jev-1.13.0','answers':{k:{'type':'choice','choice':'applies' if k=='bind_11111111' else 'not_applicable','confidence':.99} for k in questions}}
        result=select_rules(source, {'task':'send email'}, judge=judge)
        self.assertEqual([r['id'] for r in result['rules']], ['11111111'])
        self.assertEqual(len(calls),1)
        calls.clear()
        def unavailable(state, questions):
            calls.append(state)
            raise RuntimeError('offline')
        result=select_rules(source, {'task':'send email'}, judge=unavailable)
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['unresolved']),2)
        self.assertEqual(len(calls),1)


if __name__ == '__main__':
    unittest.main()
