#!/usr/bin/env python3
"""Eval report verdicts follow observed regressions and grader controls, offline."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "evals/jev-judgments"


class ReportTests(unittest.TestCase):
    def report(self, candidate, controls_fail=False):
        spec = importlib.util.spec_from_file_location("eval_report", HERE / "run_eval.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for name in ("run_eval.py", "score.py"):
                (output / name).write_bytes((HERE / name).read_bytes())
            if controls_fail:
                score = (output / "score.py").read_text().replace(
                    '"oracle_pass_rate": _rate(cases, test, oracle)', '"oracle_pass_rate": 0.0')
                (output / "score.py").write_text(score)
            def observe(arm, case, scratch):
                if arm == "baseline":
                    return {"paid": True, "refusal": None}
                return {"paid": candidate(case), "refusal": None}
            with patch.object(runner, "HERE", output), \
                    patch.object(runner, "_tree", return_value="baseline"), \
                    patch.object(runner, "_load", side_effect=lambda tree: tree), \
                    patch.object(runner, "observe", observe), contextlib.redirect_stdout(io.StringIO()):
                code = runner.main(["--report", "--base", "HEAD"])
            return code, json.loads((output / "receipt.json").read_text())

    def test_critical_retention_regression_blocks_ship_and_exits_failure(self):
        code, receipt = self.report(lambda case: False)
        retention = next(d for d in receipt["dimensions"] if d["dimension_id"] == "judgment-point-retention")
        self.assertEqual(retention["direction_vs_baseline"], "regressed")
        self.assertEqual(retention["status"], "failed")
        self.assertEqual(receipt["stage_results"][0]["status"], "failed")
        self.assertEqual(receipt["verdict"]["decision"], "do_not_merge")
        self.assertIn("judgment-point-retention", receipt["verdict"]["statement"])
        self.assertNotEqual(code, 0)

    def test_failed_grader_controls_cannot_ship(self):
        # A perfect candidate cannot compensate for a broken oracle control.
        code, receipt = self.report(lambda case: not runner_label(case), controls_fail=True)
        self.assertEqual(receipt["grader"]["validation"]["result"], "fail")
        self.assertEqual(receipt["stage_results"][0]["status"], "failed")
        self.assertEqual(receipt["verdict"]["decision"], "do_not_merge")
        self.assertNotEqual(code, 0)


def runner_label(case):
    # Independent audit labels, as held in the committed expectations.
    expectations = json.loads((HERE / "expectations.v2.json").read_text())
    return next(e["should_not_fire"] for e in expectations["cases"].values() if e["input"] == case)


if __name__ == "__main__":
    unittest.main()
