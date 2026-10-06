#!/usr/bin/env python3
"""Offline suite for ops/jev_intake.py. No credential, no network, no spend.

Every judgment arrives through FakeClient, which reproduces the REAL response
envelope ops/typesafe_client.py's ask() returns — answers keyed by question
id under "answers", each body carrying its own "type" and a value under that
same key — because ops/jev_judge.py's read() always loads the REAL
typesafe_client.py to decode an answer (decide() is pure computation, no
network), so a fake that gets the envelope shape wrong is caught here rather
than in production. FakeClient's canned answers are keyed by question id, set
per test, exactly as the shared brief asks for.

Covers, per check: the deterministic-trigger skip (no Jev call at all), the
Jev-reached path for each verdict, and the unavailable path (JudgeUnavailable
or any other failure, never raised past the check).
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_intake.py"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
SPEC = importlib.util.spec_from_file_location("jev_intake", MODULE_PATH)
assert SPEC and SPEC.loader
intake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(intake)


import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class FakeClient:
    """Stands in for ops/typesafe_client.py: question builders plus ask().

    `answers` maps question id -> the raw answer BODY as the real service
    would return it (e.g. {"type": "noul", "noul": 0.9}), never the whole
    envelope — ask() wraps every body under "answers" itself, the same way
    the real client's response is decoded one level down by jev_judge.judge().
    """

    def __init__(self, answers=None, fail=False):
        self.answers = answers or {}
        self.fail = fail
        self.calls = []

    def noul(self, instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}

    def choice(self, instructions, options):
        return {"type": "choice", "instructions": instructions,
                "criteria": dict(options)}

    def score(self, instructions, levels):
        return {"type": "score", "instructions": instructions,
                "criteria": list(levels)}

    def ask(self, state, questions, **kwargs):
        self.calls.append((state, questions))
        if self.fail:
            raise RuntimeError("synthetic network failure")
        body = {}
        for qid in questions:
            if qid not in self.answers:
                body[qid] = (_score(0.0) if questions[qid]["type"] == "score" else _noul(0.1))
                continue
            body[qid] = self.answers[qid]
        return {"answers": body, "model": "jev-1.13.0", "usage": {"input_tokens": 1}}


def _noul(value):
    return {"type": "noul", "noul": value}


def _choice(chosen, probabilities, confidence=0.8):
    return {"type": "choice", "choice": chosen, "confidence": confidence,
            "probabilities": probabilities}


def _score(value, confidence=0.8):
    return {"type": "score", "score": value, "confidence": confidence}


class _TempRepo:
    """A throwaway git repo with a few tracked files, for pick_context."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, env=ENV)
        (self.root / "ops").mkdir()
        (self.root / "ops" / "widget_loader.py").write_text(
            '"""Loads a widget from disk."""\ndef load_widget():\n    pass\n')
        (self.root / "ops" / "unrelated_thing.py").write_text(
            '"""Does something else entirely."""\n')
        (self.root / "README.md").write_text("# repo\n")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, env=ENV)
        return self.root

    def __exit__(self, *exc):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# #1 pick_context
# ---------------------------------------------------------------------------

class PickContextTests(SemanticTestCase):
    def test_skips_jev_when_task_already_names_a_file(self):
        client = FakeClient()
        with _TempRepo() as root:
            out = intake.pick_context("fix ops/widget_loader.py please", root,
                                      client=client)
        self.assertEqual(out["check"], "context_picker")
        self.assertEqual(out["verdict"], "picked")
        self.assertEqual(client.calls, [])
        self.assertIn("ops/widget_loader.py", out["detail"]["paths"])

    def test_skips_jev_when_repo_has_no_tracked_files(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as empty:
            out = intake.pick_context("load the widget from disk", empty, client=client)
        self.assertEqual(out["verdict"], "none_found")
        self.assertEqual(client.calls, [])

    def test_central_plus_needed_files_are_returned_ordered(self):
        with _TempRepo() as root:
            client = FakeClient(answers={
                "central": _choice("ops/widget_loader.py",
                                   {"ops/widget_loader.py": 0.7,
                                    "ops/unrelated_thing.py": 0.2,
                                    "README.md": 0.05,
                                    intake.CONTEXT_NONE: 0.05},
                                   confidence=0.9),
                "needs::ops/widget_loader.py": _noul(0.95),
                "needs::ops/unrelated_thing.py": _noul(0.1),
                "needs::README.md": _noul(0.05),
            })
            out = intake.pick_context("load the widget from disk", root, client=client)
        self.assertEqual(out["verdict"], "picked")
        self.assertEqual(out["detail"]["advisory_paths"][0], "ops/widget_loader.py")
        self.assertNotIn("README.md", out["detail"]["paths"])
        self.assertTrue(out["escalate"])

    def test_none_of_these_and_no_yes_files_reports_none_found(self):
        with _TempRepo() as root:
            client = FakeClient(answers={
                "central": _choice(intake.CONTEXT_NONE,
                                   {"ops/widget_loader.py": 0.1,
                                    "ops/unrelated_thing.py": 0.1,
                                    "README.md": 0.1,
                                    intake.CONTEXT_NONE: 0.7}, confidence=0.6),
                "needs::ops/widget_loader.py": _noul(0.1),
                "needs::ops/unrelated_thing.py": _noul(0.1),
                "needs::README.md": _noul(0.1),
            })
            out = intake.pick_context("something unrelated entirely", root,
                                      client=client)
        self.assertEqual(out["verdict"], "none_found")
        self.assertTrue(out["escalate"])

    def test_unavailable_on_failure(self):
        with _TempRepo() as root:
            client = FakeClient(fail=True)
            out = intake.pick_context("load the widget from disk", root, client=client)
        self.assertEqual(out["verdict"], "unavailable")
        self.assertTrue(out["escalate"])
        self.assertIsNone(out["confidence"])


# ---------------------------------------------------------------------------
# #2 pick_effort
# ---------------------------------------------------------------------------

class PickEffortTests(SemanticTestCase):
    def test_skips_jev_for_a_deterministically_trivial_task(self):
        client = FakeClient()
        out = intake.pick_effort("fix a typo in the README", client=client)
        self.assertEqual(out["detail"].get("advisory_verdict", out["verdict"]), "low")
        self.assertEqual(client.calls, [])

    def test_high_needs_both_a_high_score_and_high_confidence(self):
        client = FakeClient(answers={"difficulty": _score(1.9, confidence=0.9)})
        out = intake.pick_effort("design a new distributed consensus protocol",
                                 client=client)
        self.assertEqual(out["detail"].get("advisory_verdict", out["verdict"]), "high")
        self.assertTrue(out["escalate"])

    def test_high_score_but_low_confidence_does_not_become_high(self):
        # THE MEASURED FACT THIS GUARDS: high effort hurt on hard tasks for the
        # local model, so a near-top score with shaky confidence must NOT be
        # rounded up to "high" — it falls back to "low" and flags escalate.
        client = FakeClient(answers={"difficulty": _score(1.9, confidence=0.3)})
        out = intake.pick_effort("design a new distributed consensus protocol",
                                 client=client)
        self.assertEqual(out["detail"].get("advisory_verdict", out["verdict"]), "low")
        self.assertTrue(out["escalate"])

    def test_moderate_score_is_medium(self):
        client = FakeClient(answers={"difficulty": _score(1.0, confidence=0.9)})
        out = intake.pick_effort("refactor the retry loop to share one helper",
                                 client=client)
        self.assertEqual(out["detail"].get("advisory_verdict", out["verdict"]), "medium")

    def test_low_score_is_low(self):
        client = FakeClient(answers={"difficulty": _score(0.1, confidence=0.9)})
        out = intake.pick_effort("adjust a constant used in three places",
                                 client=client)
        self.assertEqual(out["detail"].get("advisory_verdict", out["verdict"]), "low")
        self.assertTrue(out["escalate"])

    def test_unavailable_on_failure(self):
        client = FakeClient(fail=True)
        out = intake.pick_effort("design a new distributed consensus protocol",
                                 client=client)
        self.assertEqual(out["verdict"], "unavailable")


# ---------------------------------------------------------------------------
# #3 check_ambiguity
# ---------------------------------------------------------------------------

class CheckAmbiguityTests(SemanticTestCase):
    def test_skips_jev_for_a_long_targeted_task(self):
        client = FakeClient()
        out = intake.check_ambiguity(
            "In ops/widget_loader.py, make load_widget() retry twice on a "
            "network timeout before giving up, and log each retry.",
            client=client)
        self.assertIn(out["verdict"], ("clear", "review_required"))
        self.assertEqual(client.calls, [])

    def test_short_task_triggers_and_can_be_ambiguous(self):
        client = FakeClient(answers={
            "missing_target": _noul(0.9),
            "conflicting_requirements": _noul(0.1),
            "unstated_acceptance_test": _noul(0.8),
            "unclear_scope": _noul(0.2),
        })
        out = intake.check_ambiguity("fix the thing", client=client)
        self.assertEqual(out["verdict"], "review_required")
        self.assertTrue(out["detail"]["kinds"]["missing_target"]["applies"])
        self.assertTrue(out["detail"]["kinds"]["unstated_acceptance_test"]["applies"])
        self.assertFalse(out["detail"]["kinds"]["conflicting_requirements"]["applies"])

    def test_short_task_can_still_come_back_clear(self):
        client = FakeClient(answers={
            "missing_target": _noul(0.05),
            "conflicting_requirements": _noul(0.05),
            "unstated_acceptance_test": _noul(0.05),
            "unclear_scope": _noul(0.05),
        })
        out = intake.check_ambiguity("fix bug #42", client=client)
        self.assertIn(out["verdict"], ("clear", "review_required"))
        self.assertTrue(out["escalate"])

    def test_unavailable_on_failure(self):
        client = FakeClient(fail=True)
        out = intake.check_ambiguity("fix the thing", client=client)
        self.assertEqual(out["verdict"], "unavailable")


# ---------------------------------------------------------------------------
# #5 route_task
# ---------------------------------------------------------------------------

class RouteTaskTests(SemanticTestCase):
    def test_security_work_escalates_deterministically(self):
        client = FakeClient()
        out = intake.route_task("harden the password validation parser",
                                client=client)
        self.assertEqual(out["verdict"], "escalate")
        self.assertTrue(out["detail"]["deterministic"])
        self.assertEqual(client.calls, [])

    def test_untested_algorithm_work_escalates(self):
        client = FakeClient()
        out = intake.route_task("implement a new scheduling algorithm",
                                has_tests=False, client=client)
        self.assertEqual(out["verdict"], "escalate")
        self.assertEqual(client.calls, [])

    def test_large_diff_escalates(self):
        client = FakeClient()
        out = intake.route_task("touch up formatting", diff_size_estimate=500,
                                client=client)
        self.assertEqual(out["verdict"], "escalate")

    def test_too_many_files_escalates(self):
        client = FakeClient()
        out = intake.route_task("rename a shared helper",
                                files=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"],
                                client=client)
        self.assertEqual(out["verdict"], "escalate")

    def test_no_test_command_escalates(self):
        client = FakeClient()
        out = intake.route_task("adjust a config value", has_tests=False,
                                client=client)
        self.assertEqual(out["verdict"], "escalate")

    def test_clears_every_rule_then_asks_jev_and_can_stay_local(self):
        client = FakeClient(answers={"hard_enough_to_escalate": _noul(0.1)})
        out = intake.route_task("adjust a config value", has_tests=True,
                                files=["config.py"], client=client)
        self.assertEqual(out["verdict"], "review_required")
        self.assertFalse(out["detail"]["deterministic"])
        self.assertEqual(len(client.calls), 1)

    def test_clears_every_rule_but_jev_still_escalates(self):
        client = FakeClient(answers={"hard_enough_to_escalate": _noul(0.95)})
        out = intake.route_task("adjust a config value", has_tests=True,
                                files=["config.py"], client=client)
        self.assertEqual(out["verdict"], "review_required")

    def test_unavailable_on_failure(self):
        client = FakeClient(fail=True)
        out = intake.route_task("adjust a config value", has_tests=True,
                                files=["config.py"], client=client)
        self.assertEqual(out["verdict"], "unavailable")


# ---------------------------------------------------------------------------
# #18 split_plan
# ---------------------------------------------------------------------------

class SplitPlanTests(SemanticTestCase):
    def test_empty_plan_skips_jev(self):
        client = FakeClient()
        out = intake.split_plan([], client=client)
        self.assertEqual(out["detail"]["steps"], [])
        self.assertEqual(client.calls, [])

    def test_one_request_carries_a_score_per_step(self):
        client = FakeClient(answers={
            "step_0": _score(0.1, confidence=0.9),
            "step_1": _score(1.9, confidence=0.9),
            "step_2": _score(1.0, confidence=0.9),
        })
        out = intake.split_plan(
            ["bump a constant", "invent a new consensus algorithm",
             "refactor the retry helper"], client=client)
        self.assertEqual(len(client.calls), 1)
        steps = out["detail"]["steps"]
        self.assertEqual(steps[0]["difficulty"], "low")
        self.assertEqual(steps[0]["advisory_route"], "local")
        self.assertEqual(steps[1]["difficulty"], "high")
        self.assertEqual(steps[1]["advisory_route"], "escalate")
        self.assertEqual(steps[2]["difficulty"], "medium")
        self.assertEqual(steps[2]["advisory_route"], "local")
        self.assertTrue(out["escalate"])

    def test_unavailable_on_failure(self):
        client = FakeClient(fail=True)
        out = intake.split_plan(["do a thing"], client=client)
        self.assertEqual(out["verdict"], "unavailable")


# ---------------------------------------------------------------------------
# #23 pick_example
# ---------------------------------------------------------------------------

EXAMPLES = [
    {"id": "ex-1", "title": "Retry a flaky network call",
     "summary": "Wrapped a call in a bounded retry with backoff."},
    {"id": "ex-2", "title": "Rename a shared helper",
     "summary": "Renamed and updated every call site."},
]


class PickExampleTests(SemanticTestCase):
    def test_empty_examples_skips_jev(self):
        client = FakeClient()
        out = intake.pick_example("do something", [], client=client)
        self.assertEqual(out["verdict"], "none_fits")
        self.assertEqual(client.calls, [])

    def test_a_matching_example_is_returned(self):
        client = FakeClient(answers={"best_example": _choice(
            "ex-1", {"ex-1": 0.8, "ex-2": 0.1, intake.EXAMPLE_NONE: 0.1},
            confidence=0.85)})
        out = intake.pick_example("retry the flaky upstream call", EXAMPLES,
                                  client=client)
        self.assertEqual(out["verdict"], "matched")
        self.assertEqual(out["detail"]["advisory_example_id"], "ex-1")

    def test_none_fits_when_jev_says_so(self):
        client = FakeClient(answers={"best_example": _choice(
            intake.EXAMPLE_NONE,
            {"ex-1": 0.1, "ex-2": 0.1, intake.EXAMPLE_NONE: 0.8}, confidence=0.85)})
        out = intake.pick_example("write a brand new parser from scratch", EXAMPLES,
                                  client=client)
        self.assertEqual(out["verdict"], "none_fits")
        self.assertIsNone(out["detail"]["example_id"])

    def test_unavailable_on_failure(self):
        client = FakeClient(fail=True)
        out = intake.pick_example("do something", EXAMPLES, client=client)
        self.assertEqual(out["verdict"], "unavailable")


if __name__ == "__main__":
    unittest.main()
