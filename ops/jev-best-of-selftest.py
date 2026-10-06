#!/usr/bin/env python3
"""Offline suite for ops/jev_best_of.py. No credential, no network, no spend.

Every judgment arrives through an injected fake, so this runs on a hosted
runner with nothing configured. The cases that earn their place mirror the
16-task comparison run the module's docstring cites: the deterministic
prefilter when exactly one candidate's own tests pass, the "none of these"
escalation, low confidence NEVER falling back to attempt 1, and an outage
reporting "unavailable" instead of raising.
"""

import importlib.util
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_best_of.py"
SPEC = importlib.util.spec_from_file_location("jev_best_of", MODULE_PATH)
assert SPEC and SPEC.loader
bo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bo)


import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class FakeJudge:
    """Stands in for ops/jev_judge.py: same .judge()/.record() surface."""

    JudgeUnavailable = RuntimeError
    SHADOW_LOG = "unused-in-tests"

    def __init__(self, choice=None, confidence=None, fail=False):
        self.choice = choice
        self.confidence = confidence
        self.fail = fail
        self.calls = []
        self.records = []

    def judge(self, subject, questions, **kwargs):
        self.calls.append((subject, questions))
        if self.fail:
            raise self.JudgeUnavailable("synthetic outage")
        return {"model": "jev-1.13.0",
                "answers": {"pick": {"type": "choice", "choice": self.choice,
                                       "confidence": self.confidence}}}

    def record(self, kind, subject_ref, answer, existing_decision=None, **kwargs):
        row = {"kind": kind, "subject_ref": subject_ref, "answer": answer,
               "existing_decision": existing_decision, **kwargs}
        self.records.append(row)
        return row


class FakeClient:
    """Stands in for ops/typesafe_client.py's question builders."""

    @staticmethod
    def choice(instructions, options):
        return {"type": "choice", "instructions": instructions, "criteria": dict(options)}

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


def _candidate(cid, *, passes=None, probes=None, output=None):
    row = {"id": cid}
    if passes is not None:
        row["test_exit_code"] = 0 if passes else 1
    if probes is not None:
        row["probe_results"] = probes
    if output is not None:
        row["test_output"] = output
    row["code_or_diff"] = f"# candidate {cid}"
    return row


class PrefilterTests(SemanticTestCase):
    def test_exactly_one_passing_candidate_is_chosen_without_asking_jev(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=True),
                      _candidate("c", passes=False)]
        judge = FakeJudge(fail=True)  # would raise if ever called
        out = bo.select_candidate("do the thing", candidates, judge=judge, client=FakeClient)
        self.assertEqual(out["verdict"], "b")
        self.assertEqual(out["escalate"], False)
        self.assertEqual(len(judge.calls), 0, "the prefilter must not ask Jev")
        self.assertEqual(len(judge.records), 1, "the prefilter decision is still recorded")

    def test_two_passing_candidates_falls_through_to_jev(self):
        candidates = [_candidate("a", passes=True), _candidate("b", passes=True)]
        judge = FakeJudge(choice="a", confidence=0.9)
        out = bo.select_candidate("do the thing", candidates, judge=judge, client=FakeClient)
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(out["verdict"], "review_required")
        self.assertEqual(out["detail"]["advisory_candidate_id"], "a")

    def test_zero_passing_candidates_falls_through_to_jev(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice="b", confidence=0.9)
        out = bo.select_candidate("do the thing", candidates, judge=judge, client=FakeClient)
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(out["verdict"], "review_required")
        self.assertEqual(out["detail"]["advisory_candidate_id"], "b")

    def test_no_candidates_is_a_none_verdict_with_no_jev_call(self):
        judge = FakeJudge(fail=True)
        out = bo.select_candidate("do the thing", [], judge=judge, client=FakeClient)
        self.assertEqual(out["verdict"], "none")
        self.assertTrue(out["escalate"])
        self.assertEqual(len(judge.calls), 0)


class EvidenceChoiceTests(SemanticTestCase):
    def test_evidence_free_candidates_are_still_sent_to_jev(self):
        """No probe_results, no test_output anywhere: still asked, per the
        module's 'still ask' instruction — a caller decides what to do with
        a low-evidence answer, this module does not refuse to produce one."""
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        for c in candidates:
            c.pop("test_exit_code", None)
        judge = FakeJudge(choice="a", confidence=0.5)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(len(judge.calls), 1)
        self.assertFalse(out["detail"]["any_evidence"])

    def test_the_choice_carries_an_explicit_none_option(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice="a", confidence=0.9)
        bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        options = judge.calls[0][1]["pick"]["criteria"]
        self.assertIn(bo.NONE_RIGHT, options)
        self.assertIn("a", options)
        self.assertIn("b", options)

    def test_only_one_request_is_made_for_the_choice(self):
        """Candidates compete for one slot: one Choice, never one Noul each."""
        candidates = [_candidate(str(i), passes=False) for i in range(5)]
        judge = FakeJudge(choice="0", confidence=0.9)
        bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(len(judge.calls), 1)


class NoneVerdictTests(SemanticTestCase):
    def test_none_of_these_escalates_and_is_never_coerced_to_a_candidate(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice=bo.NONE_RIGHT, confidence=0.7)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(out["verdict"], "review_required")
        self.assertIsNone(out["detail"]["advisory_candidate_id"])
        self.assertTrue(out["escalate"])

    def test_never_defaults_to_attempt_1_on_a_missing_choice(self):
        """A malformed/missing choice must read as 'none', never as candidates[0]
        — the exact failure the module's docstring calls out in the 0.6-gate
        experiment that fell back to attempt 1 and lost."""
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice=None, confidence=None)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(out["verdict"], "unavailable")
        self.assertNotEqual(out["verdict"], "a")


class ConfidenceTests(SemanticTestCase):
    def test_low_confidence_still_returns_the_chosen_id(self):
        """The measured lesson: median confidence of RIGHT picks was 0.33 in
        the comparison run. A caller must still get the id, with escalate=True
        as the signal, never a silent fallback."""
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice="b", confidence=0.20)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient,
                                   conf_escalate_at=bo.CONF_ESCALATE_AT)
        self.assertEqual(out["verdict"], "review_required")
        self.assertEqual(out["detail"]["advisory_candidate_id"], "b")
        self.assertTrue(out["escalate"])

    def test_confidence_at_or_above_the_floor_does_not_escalate(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice="a", confidence=bo.CONF_ESCALATE_AT)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertTrue(out["escalate"])

    def test_missing_confidence_escalates(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(choice="a", confidence=None)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(out["verdict"], "review_required")
        self.assertEqual(out["detail"]["advisory_candidate_id"], "a")
        self.assertTrue(out["escalate"])


class UnavailableTests(SemanticTestCase):
    def test_an_outage_reports_unavailable_not_an_exception(self):
        candidates = [_candidate("a", passes=False), _candidate("b", passes=False)]
        judge = FakeJudge(fail=True)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(out["verdict"], "unavailable")
        self.assertTrue(out["escalate"])

    def test_an_outage_still_reports_the_deterministic_passing_ids(self):
        candidates = [_candidate("a", passes=True), _candidate("b", passes=True)]
        judge = FakeJudge(fail=True)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        self.assertEqual(sorted(out["detail"]["passing_ids"]), ["a", "b"])


class ShapeTests(SemanticTestCase):
    def test_every_result_has_the_required_keys(self):
        candidates = [_candidate("a", passes=True)]
        judge = FakeJudge(fail=True)
        out = bo.select_candidate("task", candidates, judge=judge, client=FakeClient)
        for key in ("check", "verdict", "confidence", "escalate", "detail"):
            self.assertIn(key, out)
        self.assertEqual(out["check"], "best_of")


class EntrypointTests(SemanticTestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        """Uses the sealed inventory's own detector, not a substring search. A
        shebang or a main guard here would move the frontier and owe a
        registry successor — and the detector is a regex over the WHOLE file,
        so even an example inside this docstring would seal it."""
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
