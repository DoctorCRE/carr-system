"""Verify schedule relationships and region provenance through extraction and scoring."""

import copy
from pathlib import Path
import runpy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ScheduleTests(unittest.TestCase):
    def test_ocr_failure_keeps_text_receipt_and_replays(self):
        from lib.deal_extraction import run_job, ExtractionJob
        from subprocess import CompletedProcess, TimeoutExpired
        from unittest.mock import patch
        from lib.deal_ocr import ocr_regions
        from uuid import UUID
        base = runpy.run_path("tools/test-deal-extraction.py")
        failures = [{"return_value": CompletedProcess([], 1, b"", b"crashed")},
                    {"side_effect": TimeoutExpired("swift", 180)},
                    {"side_effect": OSError("swift unavailable")}]
        for failure in failures:
            with self.subTest(failure=failure), patch("lib.deal_ocr.subprocess.run", **failure) as runner:
                store = base["Source"]()
                job = ExtractionJob(UUID(base["KEY"]), UUID(base["ENTITY"]),
                                    UUID(base["DOC"]), "lease", "haiku")
                kwargs = dict(call=store, read_bytes=lambda d: base["TEXT"],
                              text_pages=lambda d: [base["TEXT"].decode(), ""],
                              model=lambda *a, **k: {"output": base["output"](),
                                                    "cost_usd": "0", "model": "haiku"},
                              ocr_regions=ocr_regions)
                result = run_job(job, **kwargs)
                self.assertEqual(runner.call_args.args[0][-1], "2")
                submitted = store.submissions[0]
                self.assertEqual([(o["fact_key"], o["value"]) for o in submitted["observations"]],
                                 [("base_rent_per_sf", "24"), ("term_months", "60")])
                self.assertEqual({a["reason"] for a in submitted["receipt"]["abstentions"]},
                                 {"unsupported_ocr"})
                self.assertEqual(run_job(job, **kwargs), result)
                self.assertEqual(len(store.submissions), 1)

    def test_text_only_never_needs_vision(self):
        from lib.deal_extraction import run_job, ExtractionJob
        from uuid import UUID
        base = runpy.run_path("tools/test-deal-extraction.py")
        store = base["Source"]()
        run_job(ExtractionJob(UUID(base["KEY"]), UUID(base["ENTITY"]), UUID(base["DOC"]),
                              "lease", "haiku"), call=store,
                read_bytes=lambda d: base["TEXT"], text_pages=lambda d: [base["TEXT"].decode()],
                model=lambda *a, **k: {"output": base["output"](), "cost_usd": "0", "model": "haiku"},
                ocr_regions=lambda *a, **k: self.fail("Vision ran on a native text page"))
        self.assertEqual(store.submissions[0]["receipt"]["abstentions"][0]["reason"], "absent")

    def test_region_job_keeps_text_scalar_and_flags_scanned_schedule(self):
        base = runpy.run_path("tools/test-deal-extraction.py")
        store = base["Source"]()
        from lib.deal_extraction import run_job, ExtractionJob
        from uuid import UUID

        region = {
            "page": 2,
            "region_id": "scan-table",
            "method": "ocr",
            "text": "Year 1 | $24 | USD/SF/year | NNN | none",
        }
        run_job(
            ExtractionJob(
                UUID(base["KEY"]),
                UUID(base["ENTITY"]),
                UUID(base["DOC"]),
                "lease",
                "haiku",
            ),
            call=store,
            read_bytes=lambda d: base["TEXT"],
            text_pages=lambda d: [base["TEXT"].decode(), ""],
            model=lambda *a, **k: {
                "output": base["output"](),
                "cost_usd": "0",
                "model": "haiku",
            },
            ocr_regions=lambda data, **kwargs: [region],
        )
        submitted = store.submissions[0]
        rent = next(
            o for o in submitted["observations"] if o["fact_key"] == "base_rent_per_sf"
        )
        self.assertEqual((rent["value"], rent["method"]), ("24", "text_layer"))
        schedule = next(
            o for o in submitted["observations"] if o["fact_key"] == "rent_schedule"
        )
        self.assertEqual(schedule["method"], "ocr")
        self.assertEqual(
            schedule["value"][0]["rate"]["source"]["region_id"], "scan-table"
        )
        self.assertEqual(schedule["confidence_tier"], "human_review")

    def test_scan_scalar_and_dual_engine_disagreement_are_review_only(self):
        base = runpy.run_path("tools/test-deal-extraction.py")
        from lib.deal_extraction import run_job, ExtractionJob
        from uuid import UUID

        for disagree in (False, True):
            store = base["Source"]()
            region = {
                "page": 1,
                "region_id": "scan-term",
                "method": "ocr",
                "text": "Term is 60 months.",
            }
            if disagree:
                region["alternative_text"] = "Term is 80 months."
            run_job(
                ExtractionJob(
                    UUID(base["KEY"]),
                    UUID(base["ENTITY"]),
                    UUID(base["DOC"]),
                    "lease",
                    "haiku",
                ),
                call=store,
                read_bytes=lambda d: base["TEXT"],
                text_pages=lambda d: [""],
                model=lambda *a, **k: self.fail("scan-only job spent model money"),
                ocr_regions=lambda d, **kwargs: [region],
            )
            submitted = store.submissions[0]
            if disagree:
                self.assertEqual(submitted["observations"], [])
                self.assertEqual(
                    {
                        a["reason"]
                        for a in submitted["receipt"]["abstentions"]
                        if a["fact_key"] == "term_months"
                    },
                    {"ocr_disagreement"},
                )
            else:
                term = submitted["observations"][0]
                self.assertEqual(
                    (term["value"], term["method"], term["confidence_tier"]),
                    ("60", "ocr", "human_review"),
                )
            self.assertEqual(submitted["receipt"]["cost_usd"], "0")

    def test_all_synthetic_schedules_keep_relationships_and_per_field_methods(self):
        from lib.deal_extraction import regional_facts

        score = runpy.run_path("evals/deal-extraction/score.py")["score_schedule"]
        corpus = runpy.run_path("evals/deal-extraction/schedules.py")["build_corpus"]()
        sys.path.insert(0, str(Path("evals/deal-extraction").resolve()))
        predictions = runpy.run_path("evals/deal-extraction/rollout.py")["synthetic_predictions"](corpus)
        self.assertEqual([p["route"] for p in predictions], [c["expected_route"] for c in corpus["cases"]])
        for case in corpus["cases"]:
            periods, _, disagreements, _ = regional_facts(
                case["pages"], case["regions"]
            )
            if case["expected"]:
                self.assertTrue(
                    score(periods, case["expected"]["rent_schedule"]), case["id"]
                )
                self.assertEqual(
                    {
                        field["source"]["method"]
                        for row in periods
                        for field in row.values()
                    },
                    {"ocr" if case["ocr"] else "text_layer"},
                )
            else:
                self.assertEqual(periods, [])
                self.assertIn("rent_schedule", disagreements)

    def test_planned_corpus_can_pass_quality_gate_but_synthetic_never_releases(self):
        sys.path.insert(0, str(Path("evals/deal-extraction").resolve()))
        module = runpy.run_path("evals/deal-extraction/rollout.py")
        schedule = [{"start_month": 1, "end_month": 12, "rate": "24",
                     "unit": "USD/SF/year", "expense_basis": "nnn", "escalation_percent": None}]
        cases = [{"id": str(n), "document_type": ("loi", "lease", "counter", "amendment")[n % 4],
                  "ocr": n % 8 >= 4, "expected_route": "high" if n == 0 else "normal",
                  "expected": {"rent_schedule": schedule, "base_rent_per_sf": "24",
                               "landlord_ti_per_sf": "50", "term_months": "60", "free_rent_months": "3"}}
                 for n in range(25)]
        corpus = {"synthetic_only": True, "frozen_holdout": False,
                  "adjudicated_real_documents": 0, "cases": cases}
        predictions = [{"case_id": c["id"], "fields": copy.deepcopy(c["expected"]),
                        "methods": {k: "ocr" if c["ocr"] else "text_layer" for k in c["expected"]},
                        "route": c["expected_route"]} for c in cases]
        def report(c=corpus, p=predictions, capacity=10):
            return module["report"](c, p, queue_capacity=capacity, daily_documents=100)
        synthetic = report()
        self.assertFalse(synthetic["release_allowed"])
        self.assertTrue(all(s["pass"] for s in synthetic["strata"]))
        self.assertTrue(synthetic["quality_pass"])
        # Exercise the real-holdout predicate without claiming these fixtures are real evidence.
        holdout = {**corpus, "synthetic_only": False, "frozen_holdout": True,
                   "adjudicated_real_documents": 25}
        self.assertTrue(report(holdout)["release_allowed"])
        self.assertFalse(report(holdout, capacity=0)["release_allowed"])
        bad = copy.deepcopy(predictions)
        for prediction in bad[:5]:
            prediction["fields"]["rent_schedule"][0]["rate"] = "99"
        self.assertFalse(report(holdout, bad)["quality_pass"])
        self.assertFalse(report(holdout, [dict(p, route="high") for p in predictions])["release_allowed"])
        sparse = {**holdout, "cases": [{**c, "ocr": False} for c in cases]}
        self.assertFalse(report(sparse)["release_allowed"])
        self.assertTrue(synthetic["mutations"]["pass"])
        self.assertEqual(module["bounds"](0, 0), [0, 1])
        self.assertGreater(module["bounds"](25, 25)[0], 0.85)
        with self.assertRaises(ValueError):
            report(corpus, predictions + [predictions[0]])

    def test_schedule_scorer_rejects_right_digits_attached_to_wrong_years(self):
        score = runpy.run_path("evals/deal-extraction/score.py")["score_schedule"]
        periods = [
            {
                "start_month": 1,
                "end_month": 12,
                "rate": "24",
                "unit": "USD/SF/year",
                "expense_basis": "nnn",
                "escalation_percent": None,
            },
            {
                "start_month": 13,
                "end_month": 24,
                "rate": "25",
                "unit": "USD/SF/year",
                "expense_basis": "nnn",
                "escalation_percent": "4.1667",
            },
        ]
        self.assertTrue(score(periods, periods))
        wrong = copy.deepcopy(periods)
        wrong[0]["rate"], wrong[1]["rate"] = wrong[1]["rate"], wrong[0]["rate"]
        self.assertFalse(score(wrong, periods))
        wrong = copy.deepcopy(periods)
        wrong[1]["expense_basis"] = "gross"
        self.assertFalse(score(wrong, periods))


if __name__ == "__main__":
    unittest.main()
