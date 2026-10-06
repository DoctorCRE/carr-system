"""Offline suite for ops/jev_model_route.py and ops/flash_answer_checks.py. No credential, no network, no spend.

A fake judge stands in for Jev, so the suite checks what code decides from Jev's scores: the policy order and
cutoffs, the abstain fallback, the overflow when Flash is busy, failing open when Jev is down, the facts-only
hand-off, and the answer checks on the real cases from the 2026-09-24 Flash script tests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, OPS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = _load("jev_model_route")
checks = _load("flash_answer_checks")
POLICY = route.load_policy()


import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class FakeClient:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}


class FakeJudge:
    def __init__(self, scores=None, error=None):
        self.scores, self.error, self.subjects = scores or {}, error, []

    def _client(self):
        return FakeClient

    def judge(self, subject, questions, timeout=None, client=None, **kwargs):
        self.subjects.append((subject, questions))
        if self.error:
            raise self.error
        return {"answers": {k: {"type": "noul", "noul": self.scores.get(k, 0.0)} for k in questions}}


def decide(scores=None, error=None, **kw):
    return route.decide("a task", "ctx", policy=POLICY, judge=FakeJudge(scores, error), rng=lambda: 0.99,
                        log_path=None, **kw)


class PolicyFile(SemanticTestCase):
    def test_every_ordered_question_names_a_route_in_the_roster(self):
        for name in POLICY["order"]:
            self.assertIn(POLICY["questions"][name]["route"], POLICY["routes"])
        self.assertIn(POLICY["abstain_route"], POLICY["routes"])

    def test_every_route_carries_model_effort_and_protocol(self):
        for name, entry in POLICY["routes"].items():
            for key in ("model", "effort", "protocol"):
                self.assertTrue(entry.get(key), f"{name} lacks {key}")
            self.assertIn(entry["model"], POLICY["models"])

    def test_flash_routes_hand_off_somewhere(self):
        for name, entry in POLICY["routes"].items():
            if entry["model"] == "flash":
                self.assertTrue(entry.get("then", {}).get("desk"), name)


class Routing(SemanticTestCase):
    def test_proposed_policy_priority_and_cutoffs(self):
        for scores, expected in [({"code": .9,"beyond": .9}, "code"),
                                 ({"beyond": .56,"direct": .9}, "escalate"),
                                 ({"script": .45}, "script"),
                                 ({"script": .44,"direct": .4}, "direct")]:
            self.assertEqual(route.pick_route(scores, POLICY), expected)
            Path(os.environ["CARR_JEV_SEMANTIC_CACHE"]).unlink(missing_ok=True)
            row = decide(scores)
            self.assertEqual(row["advisory_route"], expected)
            self.assertEqual(row["route"], POLICY["abstain_route"])
            self.assertTrue(row["review_required"])
            self.assertTrue(row["fallback"])
    def test_repeat_uses_one_complete_batch_and_changed_context_invalidates(self):
        judge = FakeJudge({"direct": .99})
        for context in ["one", "one", "two"]:
            route.decide("a task", context, judge=judge, policy=POLICY, log_path=None)
        self.assertEqual(len(judge.subjects), 2)
        self.assertEqual(set(judge.subjects[0][1]), set(POLICY["questions"]))
    def test_unavailable_retains_policy_fallback(self):
        row = decide(error=TimeoutError("no route"))
        self.assertTrue(row["fallback"])
        self.assertIn("no route", row["jev_error"])
    def test_logs_advice_separately_from_executable_route(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d,"routes.jsonl")
            route.decide("t", judge=FakeJudge({"direct": .99}), log_path=path)
            row = json.loads(Path(path).read_text())
            self.assertEqual(row["advisory_route"], "direct")
            self.assertEqual(row["route"], POLICY["abstain_route"])

class Dispatch(SemanticTestCase):
    def test_pins_skip_judgment_and_unknown_pin_refuses(self):
        for pin, config in POLICY["pins"].items():
            if pin.startswith("_"): continue
            judge = FakeJudge(error=AssertionError("must not ask"))
            out = route.dispatch("task", pin=pin, judge=judge, policy=POLICY, log_path=None)
            self.assertEqual(out["target"], config["target"])
            self.assertEqual(judge.subjects, [])
        with self.assertRaises(ValueError): route.dispatch("task", pin="unknown", log_path=None)
    def test_semantic_advice_never_changes_executable_target(self):
        for scores in ({"direct": .99},{"code": .99},{"beyond": .99},{"script": .99}):
            for free in (True, False):
                out = route.dispatch("task", judge=FakeJudge(scores), flash_free=free, log_path=None)
                self.assertEqual(out["route"], POLICY["abstain_route"])
                self.assertEqual(out["target"], POLICY["queue_targets"]["fallback"])
                self.assertTrue(out["review_required"])
    def test_explicit_pin_audit_retains_advisory_route(self):
        judge = FakeJudge({"direct": .99})
        out = route.dispatch("review", pin="merge_review", audit_pin=True, judge=judge, log_path=None)
        self.assertEqual(out["advisory_route"], "direct")
        self.assertEqual(out["target"], POLICY["pins"]["merge_review"]["target"])
        self.assertEqual(len(judge.subjects), 1)


class Handoff(SemanticTestCase):
    def test_no_answer(self):
        self.assertEqual(route.handoff_reason("", []), "no_answer")

    def test_invented(self):
        self.assertEqual(route.handoff_reason("30.00", [{"support": "printed"}, {"support": "invented"}]), "invented")

    def test_two_consecutive_runaways(self):
        runaway = {"empty": True, "finish": "length"}
        self.assertEqual(route.handoff_reason("7", [runaway, runaway]), "runaway")
        self.assertIsNone(route.handoff_reason("7", [runaway, {"finish": "stop"}, runaway]))

    def test_grounded_answer_stays(self):
        self.assertIsNone(route.handoff_reason("7", [{"support": "printed"}]))


class AnswerChecks(SemanticTestCase):
    def test_rounded_printed_average_is_printed(self):
        # messy averages: the script printed avg 29.7393 and Flash answered 29.74, the truth
        self.assertEqual(checks.answer_support("29.74", ["parsed 3330\navg 29.7393"]), "printed")

    def test_made_up_average_is_invented(self):
        self.assertEqual(checks.answer_support("30.00", ["AVG all-perSF 30.07 n 3330\nrent 18.04 42.0"]),
                         "invented")

    def test_nearby_number_of_same_precision_is_not_a_source(self):
        self.assertEqual(checks.answer_support("30.00", ["30.01 30.02"]), "invented")

    def test_whole_number_never_sources_a_decimal(self):
        self.assertEqual(checks.answer_support("30.00", ["count 30"]), "invented")

    def test_sum_of_two_printed_counts_is_derived(self):
        self.assertEqual(checks.answer_support('{"kids": 300}', ["rules 276\nunclear 24"]), "derived")

    def test_even_split_is_invented_even_when_a_pair_matches(self):
        # varied wording, repeat 3: 300 each after a crash, and 300 = 1500 - 1200 by coincidence
        answer = '{"dental": 300, "eye care": 300, "skin": 300, "heart": 300, "kids": 300}'
        self.assertEqual(checks.answer_support(answer, ["lines 1500\nmatched 1200"]), "invented")

    def test_uneven_real_counts_pass(self):
        self.assertEqual(checks.answer_support('{"dental": 318, "eye care": 319, "skin": 280}',
                                               ["dental 318 eye care 319 skin 280"]), "printed")

    def test_timeout_marker_is_not_output(self):
        self.assertEqual(checks.answer_support("600", ["partial\n[timed out after 600s]"]), "invented")


if __name__ == "__main__":
    unittest.main()
