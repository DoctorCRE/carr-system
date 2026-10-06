#!/usr/bin/env python3
"""Offline contract for one shared-state rule-binding request."""
import importlib.util
import hashlib
import json
import unittest
from pathlib import Path

path = Path(__file__).with_name("rule_trigger_delivery.py")
spec = importlib.util.spec_from_file_location("rule_trigger_delivery_batch_test", path)
assert spec and spec.loader
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


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
        client = delivery._sibling("typesafe_client")
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

    def test_pinned_high_scores_are_advisory_with_one_shared_request(self):
        rules=[{"id":"r1","statement":"send email"},{"id":"r2","statement":"review code"}]
        calls=[]
        def fake(state,questions,**kwargs):
            calls.append((state,questions))
            return {"model":"jev-1.13.0","answers":{k:{"type":"noul","noul":.99} for k in questions}}
        selected,report=delivery.judge_budgeted("send email review code",rules,[],ask=fake,client=Client,titles={})
        self.assertEqual(selected,{})
        self.assertEqual(set(report["advisory_candidates"]),{"r1","r2"})
        self.assertTrue(report["review_required"])
        self.assertEqual(len(calls),1)

    def test_shortlist_uses_one_request_with_scoped_questions(self):
        rules = [
            {"id": "rule0001", "statement": "Before send email, verify recipient."},
            {"id": "rule0002", "statement": "When reviewing code, check tests."},
        ]
        calls = []

        def ask(state, questions, **kwargs):
            calls.append((state, questions, kwargs))
            return {"model": "jev-1.13.0", "answers": {
                "bind_rule0001": {"noul": 0.91},
                "bind_rule0002": {"noul": 0.14},
            }}

        selected, report = delivery.judge_budgeted(
            "send email review code", rules, [], ask=ask, client=Client,
            titles={"rule0001": ("send rule", "outbound"),
                    "rule0002": ("review rule", "code")},
            deadline=10**12)
        self.assertEqual(report["calls"], 1)
        self.assertEqual(selected,{})
        self.assertEqual(set(report["advisory_candidates"]), {"rule0001"})
        self.assertEqual(len(calls), 1)
        state, questions, _ = calls[0]
        self.assertEqual(state["situation"], "send email review code")
        self.assertEqual(set(questions), {"bind_rule0001", "bind_rule0002"})
        for rule_id in ("rule0001", "rule0002"):
            self.assertIn(f"rules.{rule_id}", questions[f"bind_{rule_id}"]["instructions"])
        self.assertIn("rule0001", state["rules"])

    def test_batch_failure_never_retries_serially(self):
        calls = []
        def ask(state, questions, **kwargs):
            calls.append(questions)
            raise RuntimeError("offline")
        selected, report = delivery.judge_budgeted(
            "send", [{"id": "rule0001", "statement": "send"}], [],
            ask=ask, client=Client, deadline=10**12)
        self.assertEqual(selected, {})
        self.assertEqual(report["bind_status"], "unavailable")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
