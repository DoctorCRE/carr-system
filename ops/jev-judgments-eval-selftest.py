#!/usr/bin/env python3
"""Exercise the paid-call admission scorer against actual case labels."""
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1] / "evals/jev-judgments"
spec = importlib.util.spec_from_file_location("admission_score", HERE / "score.py")
assert spec and spec.loader
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


class ScorerTests(unittest.TestCase):
    def setUp(self):
        self.expectations = json.loads((HERE / "expectations.v2.json").read_text())
        self.correct = [{"case_id": key, "paid": not case["should_not_fire"]}
                        for key, case in self.expectations["cases"].items()]

    def test_losing_required_judgments_is_a_regression(self):
        candidate = [{"case_id": row["case_id"], "paid": False} for row in self.correct]
        result = scorer.score(self.expectations, self.correct, candidate)
        retention = result["dimensions"]["judgment-point-retention"]
        self.assertEqual(retention["candidate"]["score"], 0)
        self.assertLess(retention["delta"]["ci_high"], 0)

    def test_controls_separate_correct_answers_from_missing_results(self):
        result = scorer.score(self.expectations, self.correct, [])
        self.assertEqual(result["controls"], {"oracle_pass_rate": 1, "null_pass_rate": 0})
        for dimension in result["dimensions"].values():
            self.assertEqual(dimension["candidate"]["score"], 0)


if __name__ == "__main__":
    unittest.main()
