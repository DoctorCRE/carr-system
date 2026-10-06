#!/usr/bin/env python3
"""Offline tests for the Jev calibration layer (ops/jev_calibration.py) and its
report command (ops/jev-calibration-report.py).

NOTHING HERE REACHES THE NETWORK. Judgments are built in the test or replayed
from committed fixtures, so this runs with no TypeSafe credential and no spend.

The load-bearing cases: a threshold is never proposed from pooled numbers, an
unvalidated label never counts toward one, a band is chosen on the calibration
split and must be CONFIRMED on the held-out split, and a band is refused when
the model that answered was not recorded or was not one model.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "jev_calibration.py"
REPORT_CLI = HERE / "jev-calibration-report.py"
SPEC = importlib.util.spec_from_file_location("jev_calibration", MODULE_PATH)
assert SPEC and SPEC.loader
cal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cal)


def noul_row(judgment_id, subject_ref, p, *, family="defect_class",
             consequence_class="commit_warning", model="jev-1.13.0", question_id="q"):
    """A jev-judge.jsonl row as ops/jev_judge.record() writes it."""
    tsc = cal._client()
    return {"judgment_id": judgment_id, "kind": "unit", "subject_ref": subject_ref,
            "family": family, "consequence_class": consequence_class,
            "downstream_action": "warned", "model": model,
            "answers": {question_id: {"type": "noul", "noul": p}},
            "calibration": {"schema": "carr.jev-calibration.v1", "model_answered": model,
                            "questions": {question_id: tsc.answer_distribution(
                                None, {"type": "noul", "noul": p})}}}


def inline_fixture(cases, **overrides):
    doc = {"schema": cal.FIXTURE_SCHEMA, "family": "defect_class", "version": "2026-09-29.1",
           "question_type": "noul", "consequence_classes": ["commit_warning"],
           "cases": cases}
    doc.update(overrides)
    return doc


def case(case_id, label, split, *, validated=True, gold=True, consequence_class="commit_warning"):
    return {"case_id": case_id, "subject_ref": case_id, "question_id": "q",
            "consequence_class": consequence_class,
            "label": label, "label_status": "validated" if validated else "unvalidated",
            "validated_by": "joe" if validated else None,
            "gold_source_available": gold, "gold_source": "commit abc123" if gold else None,
            "split": split}


class LibraryShapeTests(unittest.TestCase):
    MAIN_GUARD = re.compile(r"""if\s+__name__\s*==\s*["']__main__["']\s*:""")

    def test_module_is_a_library_and_must_stay_one(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"))
        self.assertIsNone(self.MAIN_GUARD.search(source))


class FixtureTests(unittest.TestCase):
    def test_a_well_formed_inline_fixture_validates(self):
        doc = inline_fixture([case("a", True, "calibration"), case("b", False, "held_out")])
        self.assertEqual(cal.validate_fixture(doc), [])

    def test_fixture_rejects_duplicate_join_keys_even_with_distinct_case_ids(self):
        first = case("a", True, "calibration")
        second = dict(first, case_id="b")
        errors = "\n".join(cal.validate_fixture(inline_fixture([first, second])))
        self.assertIn("duplicate join key", errors)

    def test_malformed_fixtures_name_every_problem(self):
        bad = case("a", True, "train")
        bad["validated_by"] = None
        doc = inline_fixture([bad, case("a", True, "held_out", consequence_class="other")],
                             consequence_classes=["commit_warning", "*"])
        doc["cases"][1]["gold_source"] = None
        errors = "\n".join(cal.validate_fixture(doc))
        for needle in ("split", "validated_by", "duplicate case_id", "pooled",
                       "consequence_class 'other'", "gold_source"):
            self.assertIn(needle, errors)

    def test_directory_loader_skips_other_schemas_and_refuses_bad_fixtures(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "vectors.v1.json").write_text(json.dumps({"schema": "something-else"}))
            Path(d, "defect_class.v1.json").write_text(json.dumps(inline_fixture(
                [case("a", True, "calibration")])))
            loaded = cal.load_fixtures(d)
            self.assertEqual(list(loaded), ["defect_class"])
            Path(d, "broken.v1.json").write_text(json.dumps(inline_fixture([], family="broken")))
            with self.assertRaises(cal.FixtureError):
                cal.load_fixtures(d)

    def test_committed_rule_binding_fixture_is_pinned_to_its_sources(self):
        fixtures = cal.load_fixtures(cal.FIXTURE_DIR)
        rule = fixtures["rule_binding"]
        self.assertEqual(rule["version"], "2026-09-29.1")
        cases = rule["cases"]
        self.assertEqual(len(cases), 240 * 195)
        validated = [c for c in cases if c["label_status"] == "validated"]
        self.assertEqual(len(validated), 9821)
        self.assertTrue(all(c["gold_source_available"] for c in validated))
        self.assertFalse(any(c["gold_source_available"] for c in cases
                             if c["label_status"] == "unvalidated"))
        self.assertEqual({c["split"] for c in cases}, {"calibration", "held_out"})
        # The recorded first-pass probabilities are replayable judgments, and
        # the model that produced them was never recorded.
        self.assertTrue(all(c["judgment"]["model"] is None for c in cases))

    def test_a_derived_fixture_refuses_a_source_that_moved(self):
        raw = json.loads((cal.FIXTURE_DIR / "rule_binding.v1.json").read_text())
        raw["source"]["labels_sha256"] = "0" * 64
        with self.assertRaises(cal.FixtureError) as caught:
            cal.materialize_fixture(raw)
        self.assertIn("labels_sha256", str(caught.exception))


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.log = str(Path(self.dir) / "out" / "jev-outcomes.jsonl")

    def test_outcome_rows_append_with_status_derived_from_source(self):
        test = cal.record_outcome("j1", "q", "test_result", correct=True,
                                  gold_source="ops/foo-selftest.py", log_path=self.log)
        review = cal.record_outcome("j1", "q", "review_verdict", label="true", log_path=self.log)
        human = cal.record_outcome("j2", "q", "human_correction", label=False,
                                   validated_by="joe", log_path=self.log)
        self.assertEqual(test["label_status"], "validated")
        self.assertTrue(test["gold_source_available"])
        self.assertEqual(review["label_status"], "unvalidated")
        self.assertEqual(human["label_status"], "validated")
        self.assertEqual(human["label"], "false")
        rows = cal.read_jsonl(self.log)
        self.assertEqual([r["judgment_id"] for r in rows], ["j1", "j1", "j2"])

    def test_bad_outcomes_are_refused_loudly(self):
        with self.assertRaises(ValueError):
            cal.record_outcome("j", "q", "vibes", label="x", log_path=self.log)
        with self.assertRaises(ValueError):
            cal.record_outcome("j", "q", "human_correction", label="x", log_path=self.log)
        with self.assertRaises(ValueError):
            cal.record_outcome("j", "q", "review_verdict", log_path=self.log)
        with self.assertRaises(ValueError):
            cal.record_outcome("", "q", "review_verdict", label="x", log_path=self.log)


class JoinTests(unittest.TestCase):
    def test_outcome_joins_to_its_judgment_and_scores_the_top_option(self):
        judgments = [noul_row("j1", "s1", 0.9), noul_row("j2", "s2", 0.2)]
        outcomes = [{"judgment_id": "j1", "question_id": "q", "source": "human_correction",
                     "label": "false", "label_status": "validated", "gold_source_available": False},
                    {"judgment_id": "j2", "question_id": "q", "source": "test_result",
                     "correct": True, "label_status": "validated", "gold_source_available": True}]
        units = {u["judgment_id"]: u for u in cal.join(judgments, outcomes, {})["units"]}
        self.assertIs(units["j1"]["correct"], False)
        self.assertEqual(units["j1"]["label_source"], "human_correction")
        self.assertIs(units["j2"]["correct"], True)
        self.assertAlmostEqual(units["j1"]["entropy_bits"], 0.4689955935892812)
        self.assertEqual(units["j1"]["model"], "jev-1.13.0")

    def test_validated_label_wins_and_a_disagreement_is_flagged(self):
        judgments = [noul_row("j1", "s1", 0.9)]
        outcomes = [{"judgment_id": "j1", "question_id": "q", "source": "review_verdict",
                     "label": "true", "label_status": "unvalidated"},
                    {"judgment_id": "j1", "question_id": "q", "source": "human_correction",
                     "label": "false", "label_status": "validated"},
                    {"judgment_id": "j1", "question_id": "q", "source": "test_result",
                     "label": "true", "label_status": "validated"}]
        unit = cal.join(judgments, outcomes, {})["units"][0]
        self.assertEqual(unit["label_source"], "human_correction")
        self.assertEqual(unit["label_status"], "validated")
        self.assertTrue(unit["conflict"])

    def test_a_validated_outcome_with_self_contradictory_label_is_conflicted(self):
        outcome = {"judgment_id": "j1", "question_id": "q", "source": "test_result",
                   "label": "false", "correct": True, "label_status": "validated"}
        unit = cal.join([noul_row("j1", "s1", 0.9)], [outcome], {})["units"][0]
        self.assertTrue(unit["conflict"])

    def test_fixture_labels_apply_by_subject_and_supply_the_split(self):
        fixture = inline_fixture([case("s1", True, "held_out")])
        unit = cal.join([noul_row("j1", "s1", 0.9)], [], {"defect_class": fixture})["units"]
        live = [u for u in unit if u["judgment_id"] == "j1"][0]
        self.assertEqual((live["split"], live["label_source"], live["correct"]),
                         ("held_out", "fixture", True))

    def test_fixture_join_requires_exact_question_and_consequence_class(self):
        fixture = inline_fixture([case("s1", True, "held_out")])
        rows = [noul_row("j1", "s1", 0.9, question_id="other"),
                noul_row("j2", "s1", 0.9, consequence_class="client_document")]
        units = cal.join(rows, [], {"defect_class": fixture})["units"]
        self.assertEqual(len(units), 2)
        self.assertTrue(all(u["label_source"] is None for u in units))
        self.assertTrue(all(u["fixture_version"] is None for u in units))

    def test_choice_scores_the_answer_that_was_actually_returned(self):
        row = noul_row("j1", "s1", 0.9)
        row["answers"]["q"] = {"type": "choice", "choice": "b",
                               "probabilities": {"a": 0.8, "b": 0.2}}
        row["calibration"]["questions"]["q"] = cal._tsc().answer_distribution(
            None, row["answers"]["q"])
        outcome = {"judgment_id": "j1", "question_id": "q", "source": "test_result",
                   "label": "b", "label_status": "validated"}
        unit = cal.join([row], [outcome], {})["units"][0]
        self.assertEqual(unit["top"], "a")
        self.assertEqual(unit["selected_choice"], "b")
        self.assertIs(unit["correct"], True)
        self.assertAlmostEqual(unit["selected_probability"], 0.2)
        report = cal.report([dict(unit, split="calibration"), dict(unit, split="held_out")])
        cell = next(c for c in report["cells"] if c["family"] == "defect_class"
                    and c["consequence_class"] == "commit_warning")
        self.assertEqual(cell["calibration_split"]["probability_edges"], [0.2])
        self.assertAlmostEqual(cell["held_out"]["probability_bands"][0]["mean_selected_probability"], 0.2)
        rendered = cal.format_report(report)
        self.assertIn("by selected probability", rendered)
        self.assertIn("mean_p=0.200 gap=-0.800", rendered)

    def test_unlabeled_and_unfamilied_judgments_are_counted_not_dropped_silently(self):
        judgments = [noul_row("j1", "s1", 0.9), noul_row("j2", "s2", 0.9, family=None)]
        joined = cal.join(judgments, [], {})
        self.assertEqual(joined["skipped"]["no_family"], 1)
        self.assertIsNone(joined["units"][0]["correct"])

    def test_split_without_a_fixture_is_a_stable_hash_of_the_subject(self):
        first = cal.join([noul_row("j1", "same-subject", 0.9)], [], {})["units"][0]["split"]
        again = cal.join([noul_row("j9", "same-subject", 0.1)], [], {})["units"][0]["split"]
        self.assertEqual(first, again)

    def test_fixture_cases_with_a_recorded_judgment_replay_as_units(self):
        fixture = inline_fixture([dict(case("r1", True, "calibration"),
                                       judgment={"model": "jev-1.13.0",
                                                 "distribution": {"true": 0.7, "false": 0.3}})])
        units = cal.join([], [], {"defect_class": fixture})["units"]
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["recorded_in"], "fixture")
        self.assertIs(units[0]["correct"], True)


def synthetic_units(model="jev-1.13.0", confirm=True):
    """Low-entropy calls right, high-entropy calls mostly wrong, both splits."""
    units = []
    def add(split, entropy, correct, n, status="validated", family="defect_class",
            cc="commit_warning", unit_model=model):
        for i in range(n):
            units.append({"unit_id": f"{split}-{entropy}-{correct}-{i}-{family}-{cc}-{status}",
                          "family": family, "consequence_class": cc, "model": unit_model,
                          "entropy_bits": entropy, "top_probability": 1 - entropy / 2,
                          "distribution_complete": True, "correct": correct,
                          "label_status": status, "split": split, "conflict": False})
    for split in ("calibration", "held_out"):
        add(split, 0.1, True, 120)
        add(split, 0.3, True, 80)
        add(split, 0.9, True, 20)
        add(split, 0.9, False, 60)
    if not confirm:
        # The held-out split disagrees: its low-entropy calls are wrong.
        for unit in units:
            if unit["split"] == "held_out" and unit["entropy_bits"] <= 0.3:
                unit["correct"] = unit["unit_id"].endswith("0-defect_class-commit_warning-validated")
    return units


class ReportTests(unittest.TestCase):
    def cell(self, report, family, cc):
        return next(c for c in report["cells"]
                    if c["family"] == family and c["consequence_class"] == cc)

    def test_a_family_and_class_band_is_proposed_and_confirmed_on_held_out(self):
        report = cal.report(synthetic_units(), targets={"commit_warning": 0.95})
        proposal = self.cell(report, "defect_class", "commit_warning")["proposal"]
        self.assertEqual(proposal["status"], "proposed", proposal)
        self.assertEqual(proposal["max_entropy_bits"], 0.3)
        self.assertEqual(proposal["model"], "jev-1.13.0")
        self.assertEqual(proposal["held_out"]["n"], 200)
        self.assertGreaterEqual(proposal["held_out"]["accuracy_lower"], 0.95)

    def test_pooled_cells_print_numbers_but_never_propose(self):
        units = synthetic_units() + [dict(u, family="rule_binding", unit_id=u["unit_id"] + "-r")
                                     for u in synthetic_units()]
        report = cal.report(units, targets={"commit_warning": 0.95})
        for family, cc in (("*", "commit_warning"), ("*", "*"), ("defect_class", "*")):
            cell = self.cell(report, family, cc)
            self.assertGreater(cell["counts"]["validated"], 0)
            self.assertEqual(cell["proposal"]["status"], "refused")
            self.assertEqual(cell["proposal"]["reason"], "pooled")

    def test_no_target_means_no_proposal_and_no_invented_default(self):
        report = cal.report(synthetic_units(), targets={})
        proposal = self.cell(report, "defect_class", "commit_warning")["proposal"]
        self.assertEqual(proposal["status"], "no_target")

    def test_held_out_must_confirm_the_calibration_band(self):
        report = cal.report(synthetic_units(confirm=False), targets={"commit_warning": 0.95})
        proposal = self.cell(report, "defect_class", "commit_warning")["proposal"]
        self.assertEqual((proposal["status"], proposal["reason"]),
                         ("refused", "held_out_did_not_confirm"))

    def test_unvalidated_labels_never_reach_a_proposal(self):
        units = [dict(u, label_status="unvalidated") for u in synthetic_units()]
        report = cal.report(units, targets={"commit_warning": 0.95})
        cell = self.cell(report, "defect_class", "commit_warning")
        self.assertEqual(cell["counts"]["validated"], 0)
        self.assertEqual(cell["counts"]["unvalidated"], len(units))
        self.assertEqual(cell["proposal"]["reason"], "insufficient_validated_labels")
        self.assertEqual(cell["unvalidated"]["n"], len(units))

    def test_a_band_needs_one_recorded_model(self):
        mixed = synthetic_units()
        mixed[0]["model"] = "jev-1.14.0"
        self.assertEqual(self.cell(cal.report(mixed, targets={"commit_warning": 0.95}),
                                   "defect_class", "commit_warning")["proposal"]["reason"],
                         "mixed_models")
        unrecorded = synthetic_units(model=None)
        self.assertEqual(self.cell(cal.report(unrecorded, targets={"commit_warning": 0.95}),
                                   "defect_class", "commit_warning")["proposal"]["reason"],
                         "model_unrecorded")

    def test_a_moving_alias_cannot_supply_a_proposal(self):
        proposal = self.cell(cal.report(synthetic_units(model="jev-latest"),
                                        targets={"commit_warning": 0.95}),
                             "defect_class", "commit_warning")["proposal"]
        self.assertEqual((proposal["status"], proposal["reason"]),
                         ("refused", "model_unpinned"))
        units = synthetic_units()
        for unit in units:
            unit["model_requested"] = "jev-latest"
            unit["model_pinned"] = False
        proposal = self.cell(cal.report(units, targets={"commit_warning": 0.95}),
                             "defect_class", "commit_warning")["proposal"]
        self.assertEqual(proposal["reason"], "model_unpinned")

    def test_conflicting_validated_labels_refuse_a_proposal(self):
        units = synthetic_units()
        units[0]["conflict"] = True
        proposal = self.cell(cal.report(units, targets={"commit_warning": 0.95}),
                             "defect_class", "commit_warning")["proposal"]
        self.assertEqual((proposal["status"], proposal["reason"]),
                         ("refused", "conflicting_validated_labels"))

    def test_a_proposal_needs_a_scored_choice_probability(self):
        units = [dict(u, selected_probability=None) for u in synthetic_units()]
        proposal = self.cell(cal.report(units, targets={"commit_warning": 0.95}),
                             "defect_class", "commit_warning")["proposal"]
        self.assertEqual((proposal["status"], proposal["reason"]),
                         ("refused", "insufficient_validated_labels"))

    def test_bands_report_accuracy_and_reliability_on_held_out_only(self):
        report = cal.report(synthetic_units(), targets={}, bins=3)
        cell = self.cell(report, "defect_class", "commit_warning")
        entropy_bands = cell["held_out"]["entropy_bands"]
        self.assertEqual(sum(b["n"] for b in entropy_bands), 280)
        top = entropy_bands[-1]
        self.assertEqual((top["n"], top["correct"]), (80, 20))
        self.assertAlmostEqual(top["accuracy"], 0.25)
        self.assertAlmostEqual(top["mean_top_probability"], 0.55)
        self.assertAlmostEqual(top["gap"], 0.30)
        self.assertIn("probability_bands", cell["held_out"])
        self.assertIn("expected_calibration_error", cell["held_out"])

    def test_the_committed_real_fixture_reports_and_refuses_for_want_of_a_model(self):
        joined = cal.join([], [], cal.load_fixtures(cal.FIXTURE_DIR))
        report = cal.report(joined["units"], targets={"context_injection": 0.9})
        cell = self.cell(report, "rule_binding", "context_injection")
        self.assertEqual(cell["counts"]["validated"], 9821)
        self.assertGreater(cell["held_out"]["validated"], 0)
        self.assertEqual(cell["proposal"]["reason"], "model_unrecorded")
        self.assertIn("rule_binding", cal.format_report(report))

    def test_emit_bands_only_carries_proposed_cells(self):
        report = cal.report(synthetic_units(), targets={"commit_warning": 0.95})
        bands = cal.bands_from_report(report, measured_at="2026-09-29T00:00:00Z")
        self.assertEqual(list(bands["bands"]), ["defect_class"])
        band = bands["bands"]["defect_class"]["commit_warning"]
        self.assertEqual((band["max_entropy_bits"], band["model"]), (0.3, "jev-1.13.0"))
        judge = importlib.util.spec_from_file_location("jev_judge", HERE / "jev_judge.py")
        module = importlib.util.module_from_spec(judge)
        judge.loader.exec_module(module)
        routed = module.route({"model": "jev-1.13.0",
                               "answers": {"q": {"type": "noul", "noul": 0.99}}}, "q",
                              family="defect_class", consequence_class="commit_warning",
                              bands=bands)
        self.assertEqual(routed["route"], "act")


class ReportCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPORT_CLI), *args],
                              capture_output=True, text=True, timeout=120)

    def test_cli_reports_json_from_logs_and_fixtures(self):
        with tempfile.TemporaryDirectory() as d:
            judgments = Path(d, "judge.jsonl")
            outcomes = Path(d, "outcomes.jsonl")
            fixtures = Path(d, "fixtures")
            fixtures.mkdir()
            judgments.write_text("\n".join(json.dumps(noul_row(f"j{i}", f"s{i}", 0.9))
                                           for i in range(4)) + "\n{corrupt\n")
            outcomes.write_text(json.dumps({"judgment_id": "j0", "question_id": "q",
                                            "source": "test_result", "correct": True,
                                            "label_status": "validated"}) + "\n")
            done = self.run_cli("--judgments", str(judgments), "--outcomes", str(outcomes),
                                "--fixtures", str(fixtures), "--target", "commit_warning=0.9",
                                "--json")
            self.assertEqual(done.returncode, 0, done.stderr)
            report = json.loads(done.stdout)
            self.assertEqual(report["skipped"]["corrupt_lines"], 1)
            self.assertEqual(report["targets"], {"commit_warning": 0.9})
            text = self.run_cli("--judgments", str(judgments), "--outcomes", str(outcomes),
                                "--fixtures", str(fixtures))
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("defect_class", text.stdout)
            self.assertIn("no_target", text.stdout)

    def test_cli_refuses_a_bad_target_and_a_bad_fixture(self):
        self.assertEqual(self.run_cli("--target", "commit_warning").returncode, 2)
        self.assertEqual(self.run_cli("--target", "*=0.9").returncode, 2)
        self.assertEqual(self.run_cli("--target", "x=1.5").returncode, 2)
        with tempfile.TemporaryDirectory() as d:
            Path(d, "bad.v1.json").write_text(json.dumps(inline_fixture([], family="bad")))
            done = self.run_cli("--fixtures", d, "--judgments", str(Path(d, "none")),
                                "--outcomes", str(Path(d, "none")))
            self.assertEqual(done.returncode, 2)
            self.assertIn("bad", done.stderr)

    def test_cli_emit_bands_refuses_the_live_config(self):
        done = self.run_cli("--emit-bands", str(HERE / "config" / "jev-calibrated-bands.v1.json"))
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
