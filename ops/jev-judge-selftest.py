#!/usr/bin/env python3
"""Offline tests for the judging layer.

NOTHING HERE REACHES THE NETWORK. Every judgment is served by a fake client, so
this runs on a hosted runner with no credential and no spend.

The load-bearing cases are the two that protect callers rather than this module:
an outage must never fail the caller, and a logging failure must never fail the
caller either. Both are ways a monitoring layer turns into an outage of its own,
and both are cheap to get wrong.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("jev_judge.py")
SPEC = importlib.util.spec_from_file_location("jev_judge", MODULE_PATH)
assert SPEC and SPEC.loader
judge_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(judge_mod)


class FakeClient:
    """Stands in for ops/typesafe_client.py."""

    def __init__(self, answers=None, raises=None):
        self.answers = answers or {"q": {"type": "noul", "noul": 0.9}}
        self.raises = raises
        self.calls = []

    def ask(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        if self.raises:
            raise self.raises
        return {"model": "jev-1.13.0", "answers": self.answers,
                "usage": {"input_tokens": 10, "output_tokens": 2}}


class LibraryShapeTests(unittest.TestCase):
    MAIN_GUARD = re.compile(r"""if\s+__name__\s*==\s*["']__main__["']\s*:""")

    def test_module_is_a_library_and_must_stay_one(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"),
                         "a shebang makes this a sealed script entrypoint and owes a registry successor")
        self.assertIsNone(self.MAIN_GUARD.search(source),
                          "a main guard has the same consequence as a shebang here")

    def test_the_detector_used_here_catches_a_real_entrypoint(self):
        self.assertIsNotNone(self.MAIN_GUARD.search('if __name__ == "__main__":'))
        self.assertIsNone(self.MAIN_GUARD.search("prose mentioning __main__"))


class JudgeTests(unittest.TestCase):
    def test_unavailability_retains_policy_vendor_and_local_failure_reasons(self):
        class AvailabilityError(RuntimeError):
            def __init__(self, code=None):
                self.code = code
        for error, reason in ((AvailabilityError("unattended_worker_off"), "unattended_worker_off"),
                              (AvailabilityError("hourly_paid_call_cap"), "hourly_paid_call_cap"),
                              (AvailabilityError(), "vendor_unavailable"),
                              (ValueError("broken inspection"), "inspection_error")):
            with self.subTest(reason=reason):
                fake = FakeClient(raises=error)
                fake.TypeSafeError = AvailabilityError
                with self.assertRaises(judge_mod.JudgeUnavailable) as caught:
                    judge_mod.judge({}, {}, client=fake)
                self.assertEqual(caught.exception.reason, reason)

    def test_explicit_evaluated_model_reaches_client(self):
        client = FakeClient()
        judge_mod.judge({"code": "x"}, {"q": {}}, client=client, model="jev-1.13.0")
        self.assertEqual(client.calls[0][2]["model"], "jev-1.13.0")

    def test_every_question_about_one_subject_travels_in_one_request(self):
        client = FakeClient({"a": {"type": "noul", "noul": 0.9},
                             "b": {"type": "noul", "noul": 0.1}})
        judge_mod.judge({"code": "x"}, {"a": {}, "b": {}}, client=client)
        self.assertEqual(len(client.calls), 1,
                         "one subject, one request: batching per subject is the measured shape")

    def test_elapsed_is_reported_so_a_slow_judgment_is_visible(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        self.assertIsInstance(answer["elapsed_ms"], int)

    def test_any_failure_surfaces_as_one_catchable_type(self):
        for boom in (RuntimeError("service down"), ValueError("bad json"), OSError("no key file")):
            with self.assertRaises(judge_mod.JudgeUnavailable):
                judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient(raises=boom))

    def test_the_failure_message_names_the_underlying_cause(self):
        with self.assertRaises(judge_mod.JudgeUnavailable) as caught:
            judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient(raises=RuntimeError("service down")))
        self.assertIn("service down", str(caught.exception))
        self.assertIn("RuntimeError", str(caught.exception))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.log = str(Path(self.dir) / "sub" / "jev-judge.jsonl")

    def _rows(self):
        return [json.loads(line) for line in Path(self.log).read_text().splitlines() if line.strip()]

    def test_a_shadow_row_carries_both_verdicts_side_by_side(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("gate_probe", "ops/thing.py", answer,
                         existing_decision="allowed", note="disagreed", log_path=self.log)
        row = self._rows()[0]
        self.assertEqual(row["existing_decision"], "allowed")
        self.assertEqual(row["answers"]["q"]["noul"], 0.9)
        self.assertEqual(row["kind"], "gate_probe")

    def test_an_outage_is_recorded_rather_than_reducing_coverage_silently(self):
        judge_mod.record("gate_probe", "ops/thing.py", None,
                         existing_decision="allowed", error="JudgeUnavailable: service down",
                         log_path=self.log)
        row = self._rows()[0]
        self.assertIn("service down", row["error"])
        self.assertNotIn("answers", row)

    def test_a_logging_failure_never_reaches_the_caller(self):
        # The monitoring layer must not become the outage. A directory where a
        # file is expected is the cheapest way to make the write fail for real.
        bad = str(Path(self.dir))
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("gate_probe", "ref", answer, log_path=bad)  # must not raise

    def test_agreement_counts_only_comparable_rows(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("k", "a", answer, existing_decision="allowed", note="agreed", log_path=self.log)
        judge_mod.record("k", "b", answer, existing_decision="allowed", note="disagreed", log_path=self.log)
        judge_mod.record("k", "c", answer, existing_decision=None, log_path=self.log)
        judge_mod.record("k", "d", None, existing_decision="allowed", error="down", log_path=self.log)
        stats = judge_mod.agreement(self.log, kind="k")
        self.assertEqual((stats["rows"], stats["errors"]), (4, 1))
        self.assertEqual((stats["comparable"], stats["agreed"], stats["disagreed"]), (2, 1, 1))

    def test_agreement_on_a_missing_log_is_empty_rather_than_an_error(self):
        self.assertEqual(judge_mod.agreement(str(Path(self.dir) / "nope.jsonl"))["rows"], 0)

    def test_agreement_ignores_a_corrupt_line_instead_of_dying_on_it(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("k", "a", answer, existing_decision="allowed", note="agreed", log_path=self.log)
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        self.assertEqual(judge_mod.agreement(self.log, kind="k")["rows"], 1)


class CalibrationRecordTests(unittest.TestCase):
    """record() keeps what a later calibration needs: a judgment id to join an
    outcome to, the question family and consequence class, the full
    distribution with entropy, and what the caller actually did."""

    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.log = str(Path(self.dir) / "jev-judge.jsonl")

    def _rows(self):
        return [json.loads(line) for line in Path(self.log).read_text().splitlines() if line.strip()]

    def test_row_carries_judgment_id_family_class_action_and_distribution(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient(
            {"q": {"type": "choice", "choice": "b", "confidence": 0.8,
                   "probabilities": {"a": 0.2, "b": 0.8}}}))
        row = judge_mod.record("gate_probe", "ops/thing.py", answer, log_path=self.log,
                               family="defect_class", consequence_class="commit_warning",
                               downstream_action={"q": "warned"}, receipt_id="r-1")
        self.assertRegex(row["judgment_id"], r"^[0-9a-f-]{36}$")
        stored = self._rows()[0]
        self.assertEqual(stored["judgment_id"], row["judgment_id"])
        self.assertEqual(stored["family"], "defect_class")
        self.assertEqual(stored["consequence_class"], "commit_warning")
        self.assertEqual(stored["downstream_action"], {"q": "warned"})
        self.assertEqual(stored["receipt_id"], "r-1")
        question = stored["calibration"]["questions"]["q"]
        self.assertEqual(question["distribution"], {"a": 0.2, "b": 0.8})
        self.assertAlmostEqual(question["entropy_bits"], 0.7219280948873623)
        self.assertEqual(stored["calibration"]["model_answered"], "jev-1.13.0")

    def test_client_calibration_block_is_kept_verbatim_when_present(self):
        block = {"schema": "carr.jev-calibration.v1", "model_requested": "jev-1.13.0",
                 "model_answered": "jev-1.13.0", "model_pinned": True,
                 "state_sha256": "0" * 64, "questions": {"q": {"entropy_bits": 0.1}}}
        answer = {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.99}},
                  "calibration": block}
        judge_mod.record("k", "ref", answer, log_path=self.log)
        self.assertEqual(self._rows()[0]["calibration"], block)

    def test_error_rows_still_get_a_judgment_id_and_no_calibration(self):
        row = judge_mod.record("k", "ref", None, error="down", log_path=self.log,
                               family="f", consequence_class="c", downstream_action="fell_back")
        self.assertIn("judgment_id", row)
        self.assertNotIn("calibration", row)
        self.assertEqual(row["downstream_action"], "fell_back")


class RouteTests(unittest.TestCase):
    """Code owns the act-or-review decision; Jev only supplies the entropy."""

    BANDS = {"schema": "carr.jev-calibrated-bands.v1", "bands": {
        "defect_class": {"commit_warning": {"max_entropy_bits": 0.6, "model": "jev-1.13.0"}}}}

    def _answer(self, p, model="jev-1.13.0"):
        return {"model": model, "answers": {"q": {"type": "noul", "noul": p}}}

    def _route(self, answer, family="defect_class", consequence_class="commit_warning", bands=None):
        return judge_mod.route(answer, "q", family=family, consequence_class=consequence_class,
                               bands=self.BANDS if bands is None else bands)

    def test_within_the_calibrated_band_acts(self):
        routed = self._route(self._answer(0.95))  # 0.286 bits
        self.assertEqual(routed["route"], "act")
        self.assertEqual(routed["reason"], "within_calibrated_band")

    def test_above_the_calibrated_band_goes_to_review(self):
        routed = self._route(self._answer(0.8))  # 0.722 bits
        self.assertEqual((routed["route"], routed["reason"]), ("review", "above_calibrated_band"))
        self.assertAlmostEqual(routed["entropy_bits"], 0.7219280948873623)
        self.assertEqual(routed["max_entropy_bits"], 0.6)

    def test_an_uncalibrated_family_or_class_goes_to_review(self):
        self.assertEqual(self._route(self._answer(0.99), family="other")["reason"], "uncalibrated")
        self.assertEqual(self._route(self._answer(0.99), consequence_class="client_document")["reason"],
                         "uncalibrated")

    def test_a_band_never_transfers_to_another_model(self):
        routed = self._route(self._answer(0.99, model="jev-1.14.0"))
        self.assertEqual((routed["route"], routed["reason"]), ("review", "model_mismatch"))

    def test_no_distribution_means_review(self):
        answer = {"model": "jev-1.13.0", "answers": {"q": {"type": "choice", "choice": "a",
                                                           "confidence": 0.99}}}
        self.assertEqual(self._route(answer)["reason"], "no_distribution")
        answer = {"model": "jev-1.13.0", "calibration": {
            "schema": "carr.jev-calibration.v1", "model_requested": "jev-1.13.0",
            "model_answered": "jev-1.13.0", "model_pinned": True,
            "questions": {"q": {"distribution": {"true": 0.99, "false": 0.01}}}}}
        self.assertEqual(self._route(answer)["route"], "review")

    def test_supplied_partial_or_forged_calibration_cannot_authorize_an_act(self):
        answer = self._answer(0.8)  # actual entropy is above the band
        answer["calibration"] = {"questions": {"q": {"entropy_bits": 0.01}}}
        self.assertEqual(self._route(answer)["route"], "review")
        low_entropy = self._answer(0.99)
        low_entropy["calibration"] = {"questions": {"q": {"entropy_bits": 0.01}}}
        self.assertEqual(self._route(low_entropy)["reason"], "calibration_mismatch")
        complete = judge_mod._client().answer_distribution(None, low_entropy["answers"]["q"])
        complete.pop("distribution")
        low_entropy["calibration"] = {
            "schema": "carr.jev-calibration.v1", "model_requested": "jev-1.13.0",
            "model_answered": "jev-1.13.0", "model_pinned": True,
            "questions": {"q": complete}}
        self.assertEqual(self._route(low_entropy)["reason"], "calibration_mismatch")
        answer["calibration"]["questions"]["q"] = {
            "entropy_bits": 0.01, "distribution_complete": True,
            "distribution": {"true": 0.99, "false": 0.01}}
        self.assertEqual(self._route(answer)["route"], "review")

    def test_moving_model_alias_never_acts_even_if_named_in_a_band(self):
        alias_bands = {"schema": "carr.jev-calibrated-bands.v1", "bands": {
            "defect_class": {"commit_warning": {"max_entropy_bits": 0.6,
                                                  "model": "jev-latest"}}}}
        self.assertEqual(self._route(self._answer(0.99, model="jev-latest"),
                                      bands=alias_bands)["route"], "review")

    def test_zero_probability_offered_choice_is_valid_in_a_full_block(self):
        raw = {"type": "choice", "choice": "b", "probabilities": {"a": 0.1, "b": 0.9}}
        answer = {"model": "jev-1.13.0", "answers": {"q": raw}}
        summary = judge_mod._client().answer_distribution(
            {"type": "choice", "criteria": {"a": "a", "b": "b", "c": "c"}}, raw)
        answer["calibration"] = {
            "schema": "carr.jev-calibration.v1", "model_requested": "jev-1.13.0",
            "model_answered": "jev-1.13.0", "model_pinned": True,
            "questions": {"q": summary}}
        self.assertEqual(self._route(answer)["route"], "act")

    def test_pooled_or_malformed_bands_are_refused_as_review(self):
        pooled = {"schema": "carr.jev-calibrated-bands.v1",
                  "bands": {"*": {"commit_warning": {"max_entropy_bits": 2, "model": "jev-1.13.0"}}}}
        self.assertEqual(self._route(self._answer(0.99), family="*", bands=pooled)["reason"],
                         "band_invalid")
        wrong = {"schema": "carr.jev-calibrated-bands.v1",
                 "bands": {"defect_class": {"commit_warning": {"max_entropy_bits": -1,
                                                               "model": "jev-1.13.0"}}}}
        self.assertEqual(self._route(self._answer(0.99), bands=wrong)["reason"], "band_invalid")

    def test_committed_bands_file_is_valid_and_calibrates_nothing_yet(self):
        bands = judge_mod.load_bands()
        self.assertEqual(bands["schema"], "carr.jev-calibrated-bands.v1")
        self.assertEqual(bands["bands"], {},
                         "a band enters only from a held-out calibration report, reviewed")
        self.assertEqual(judge_mod.route(self._answer(0.999), "q", family="defect_class",
                                         consequence_class="commit_warning")["route"], "review")


class DefaultsTests(unittest.TestCase):
    def test_the_thresholds_are_stated_and_pessimistic(self):
        # Pinned so a later edit that quietly loosens them is a visible diff in
        # a test, not a one-character change nobody reviews.
        self.assertEqual(judge_mod.YES_AT, 0.80)
        self.assertEqual(judge_mod.NO_AT, 0.20)
        self.assertEqual(judge_mod.MIN_CONFIDENCE, 0.60)

    def test_the_shadow_log_lives_under_the_repository(self):
        self.assertTrue(judge_mod.SHADOW_LOG.startswith(judge_mod.REPO))


if __name__ == "__main__":
    unittest.main()
