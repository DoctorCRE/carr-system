"""Verify schedule relationships and region provenance through extraction and scoring."""

import copy
import json
from pathlib import Path
import runpy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.deal_extraction import validate_schedule


class ScheduleTests(unittest.TestCase):
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
            ocr_regions=lambda data: [region],
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
                ocr_regions=lambda d: [region],
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
        corpus = json.loads(Path("evals/deal-extraction/schedules.json").read_text())
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

    def test_rollout_is_fail_closed_with_strata_bounds_and_operational_limits(self):
        sys.path.insert(0, str(Path("evals/deal-extraction").resolve()))
        module = runpy.run_path("evals/deal-extraction/rollout.py")
        corpus = json.loads(Path("evals/deal-extraction/schedules.json").read_text())
        predictions = module["synthetic_predictions"](corpus)
        result = module["report"](
            corpus, predictions, queue_capacity=0, daily_documents=100
        )
        self.assertFalse(result["release_allowed"])
        self.assertEqual(result["status"], "insufficient corpus")
        self.assertIn("queue capacity inadequate", result["reasons"])
        self.assertIn("routing rate inadequate", result["reasons"])
        self.assertEqual(len(result["strata"]), 40)
        self.assertTrue(result["mutations"]["pass"])
        self.assertEqual(module["bounds"](0, 0), [0, 1])
        self.assertLess(module["bounds"](25, 25)[0], 0.95)
        predictions[0]["fields"]["rent_schedule"][0]["rate"]["value"] = "24.72"
        bad = module["report"](
            corpus, predictions, queue_capacity=100, daily_documents=100
        )
        row = next(
            s
            for s in bad["strata"]
            if s["field"] == "rent_schedule"
            and s["document_type"] == "loi"
            and not s["ocr"]
        )
        self.assertEqual(row["correct"], 0)
        self.assertFalse(row["pass"])
        with self.assertRaises(ValueError):
            module["report"](
                corpus,
                predictions + [predictions[0]],
                queue_capacity=100,
                daily_documents=100,
            )

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
