"""Pins tools/jev-value-report.py: Jev cost against outcomes it can prove.

The report must never count an unlinked verdict as value, never invent a
price the repo does not carry, and never bill a call the vendor refused.
"""
import importlib.util
import ast
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("jev_value_report", HERE / "jev-value-report.py")
assert spec is not None and spec.loader is not None
jvr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jvr)

START = datetime(2026, 9, 27, tzinfo=timezone.utc)
END = datetime(2026, 10, 4, tzinfo=timezone.utc)
PRICE = {"price_usd_per_million_input_tokens": 0.042}


def call(caller, ts="2026-10-01T00:00:00Z", **extra):
    row = {"ts": ts, "caller": caller, "cache_hit": False, "usable": True, "ok": True,
           "input_tokens": 1_000_000, "output_tokens": 100_000}
    row.update(extra)
    return row


def judge(kind, at="2026-10-01T00:00:00+00:00", **extra):
    row = {"at": at, "kind": kind, "answers": {"q": {"noul": 0.5}}, "elapsed_ms": 300,
           "usage": {"input_tokens": 1000, "output_tokens": 10}, "subject_ref": None}
    row.update(extra)
    return row


def ci_run(branch, minutes, conclusion="success", started="2026-10-01T00:00:00Z", name="CI",
           event="pull_request"):
    start = datetime.fromisoformat(started.replace("Z", "+00:00"))
    end = start.timestamp() + minutes * 60
    return {"name": name, "event": event, "conclusion": conclusion, "head_branch": branch,
            "run_started_at": started,
            "updated_at": datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z")}


def sources(**over):
    base = {"calls": [], "judge": [], "gate": [], "commits": [], "ci": None, "price": PRICE}
    base.update(over)
    return base


class CostTests(unittest.TestCase):
    def test_vendor_refusals_and_cache_hits_are_not_billed(self):
        rows = [call("jev_code_review"),
                call("jev_code_review", cache_hit=True),
                call("jev_code_review", usable=False, ok=False, error="HTTP 402",
                     input_tokens=None, output_tokens=None),
                call("jev_code_review", usable=False, ok=False, error="daily_paid_call_cap",
                     input_tokens=None, output_tokens=None),
                call("jev_code_review", usable=False, ok=False, error="vendor_failed_at_worker",
                     input_tokens=None, output_tokens=None)]
        site = jvr.build_report(sources(calls=rows), START, END)["sites"]["review"]
        self.assertEqual(site["attempts"], 4)
        self.assertEqual(site["not_billed"], 2)
        self.assertEqual(site["paid"], 1)
        self.assertEqual(site["usable"], 1)
        self.assertEqual(site["billing_unknown"], 1)
        self.assertEqual(site["input_tokens"], 1_000_000)
        self.assertIsNone(site["usd_input"])
        self.assertAlmostEqual(site["usd_input_measured"], 0.042)

    def test_window_excludes_rows_outside_it(self):
        rows = [call("jev_code_review", ts="2026-09-20T00:00:00Z"), call("jev_code_review")]
        site = jvr.build_report(sources(calls=rows), START, END)["sites"]["review"]
        self.assertEqual(site["paid"], 1)

    def test_legacy_rows_without_caller_use_ok_and_usage(self):
        rows = [{"ts": "2026-10-01T00:00:00Z", "ok": True,
                 "usage": {"input_tokens": 500, "output_tokens": 5}}]
        site = jvr.build_report(sources(calls=rows), START, END)["sites"]["legacy_unattributed"]
        self.assertEqual((site["paid"], site["usable"], site["input_tokens"]), (1, 1, 500))

    def test_one_off_callers_group_into_review_or_ad_hoc(self):
        rows = [call("pr1465-full-review"), call("jev_code_review"), call("w5_builder")]
        report = jvr.build_report(sources(calls=rows), START, END)
        self.assertEqual(report["sites"]["review"]["paid"], 2)
        self.assertEqual(report["sites"]["ad_hoc_named"]["paid"], 1)

    def test_hub_attribution_requires_unique_matching_receipt(self):
        report = jvr.build_report(sources(calls=[call("jev_judge", server_receipt_id="receipt-a")],
                                         judge=[judge("build_advisory", receipt_id="receipt-a")]), START, END)
        self.assertEqual(report["sites"]["judge:build_advisory"]["paid"], 1)
        self.assertEqual(report["sites"]["judge:build_advisory"]["input_tokens"], 1_000_000)
        self.assertEqual(report["judge_hub"]["matched_judge_rows"], 1)
        self.assertEqual(report["totals"]["input_tokens"], 1_000_000)

    def test_output_price_is_reported_unknown_never_invented(self):
        report = jvr.build_report(sources(calls=[call("jev_code_review")]), START, END)
        self.assertIsNone(report["price"]["usd_per_million_output"])
        self.assertIn("ops/config/jev-cost-guard.v1.json", report["price"]["output_source"])
        text = jvr.render(report)
        self.assertIn("output-token price: UNKNOWN", text)

    def test_missing_price_config_leaves_dollars_unknown(self):
        report = jvr.build_report(sources(calls=[call("jev_code_review")], price=None), START, END)
        self.assertIsNone(report["sites"]["review"]["usd_input"])
        self.assertIn("dollars: UNKNOWN", jvr.render(report))

    def test_judge_hub_splits_by_kind_and_sums_wait_time(self):
        rows = [judge("supervise.stuck_and_drift"), judge("supervise.stuck_and_drift"),
                judge("supervise.stuck_and_drift", answers=None,
                      error="TypeSafeError: TypeSafe returned HTTP 402: billing", usage=None),
                judge("supervise.stuck_and_drift", answers=None, error="build_advisory:timeout",
                      usage=None)]
        report = jvr.build_report(sources(judge=rows), START, END)
        site = report["sites"]["judge:supervise.stuck_and_drift"]
        self.assertEqual((site["paid"], site["usable"], site["not_billed"]), (0, 0, 0))
        self.assertEqual(site["observations"], 4)
        self.assertEqual(site["input_tokens"], 0)
        self.assertEqual(site["wait_ms"], 1200)


    def test_residual_carries_usable_calls(self):
        report = jvr.build_report(sources(calls=[call("jev_judge"), call("jev_judge")],
                                          judge=[judge("build_advisory")]), START, END)
        residual = report["sites"]["judge:unattributed"]
        self.assertEqual((residual["paid"], residual["usable"]), (2, 2))

    def test_residual_usable_never_exceeds_paid(self):
        judges = [judge("build_advisory", answers=None, error="build_advisory:timeout", usage=None)]
        report = jvr.build_report(sources(calls=[call("jev_judge")], judge=judges), START, END)
        residual = report["sites"]["judge:unattributed"]
        self.assertEqual((residual["paid"], residual["usable"]), (1, 1))

    def test_judge_rows_before_the_hub_named_itself_are_already_in_legacy(self):
        calls = [{"ts": "2026-09-28T00:00:00Z", "ok": True, "usage": {"input_tokens": 700}},
                 call("jev_judge", ts="2026-10-01T00:00:00Z")]
        judges = [judge("build_advisory", at="2026-09-28T00:00:00+00:00"),
                  judge("build_advisory", at="2026-10-02T00:00:00+00:00")]
        report = jvr.build_report(sources(calls=calls, judge=judges), START, END)
        self.assertEqual(report["sites"]["judge:build_advisory"]["paid"], 0)
        self.assertEqual(report["judge_hub"]["unlinked_judge_rows"], 2)
        self.assertEqual(report["sites"]["judge:build_advisory"]["wait_ms"], 600)
        self.assertEqual(sum(s["input_tokens"] for s in report["sites"].values()),
                         report["totals"]["input_tokens"])


class ValueTests(unittest.TestCase):
    def test_temporal_marker_in_another_clause_does_not_erase_current_denial(self):
        for statement in (
                "Earlier suspicion was incorrect: this investigation did not find a bug.",
                "Previously we suspected a bug, but this investigation did not identify any bug.",
                "Earlier tests passed and this investigation did not confirm a bug.",
                "This investigation did not find a bug, though earlier tests failed.",
                "Before this review we suspected a bug; this investigation did not find a bug.",
                "Contrary to the suspicion raised previously, the investigation did not find a bug.",
                "Despite the suspicion raised earlier, our tests did not confirm a bug."):
            with self.subTest(statement=statement):
                commit = {"sha": "synthetic", "date": "2026-10-01T00:00:00Z",
                          "subject": "Fix suspected bug Jev flagged", "body": statement}
                self.assertIsNone(jvr.positive_attribution(commit))
                report = jvr.build_report(sources(commits=[commit]), START, END)
                self.assertEqual(report["totals"]["outcomes_verified"], 0)
                self.assertFalse(any(s["evidence"] for s in report["sites"].values()))

    def test_earlier_non_detection_preserves_explicit_positive_attribution(self):
        for statement in ("Our tests did not find any bug before this review.",
                          "Earlier tests did not identify a bug.",
                          "Investigation previously did not confirm any bug."):
            with self.subTest(statement=statement):
                commit = {"sha": "synthetic", "date": "2026-10-01T00:00:00Z",
                          "subject": "Fix bug Jev found", "body": statement}
                self.assertIsNotNone(jvr.positive_attribution(commit))
                report = jvr.build_report(sources(commits=[commit]), START, END)
                self.assertEqual(report["totals"]["outcomes_verified"], 1)

    def test_historical_adverb_before_test_subject_preserves_positive_attribution(self):
        for statement in ("Earlier, our tests did not find any bug.",
                          "Previously, our tests did not identify a bug."):
            with self.subTest(statement=statement):
                commit = {"sha": "synthetic", "date": "2026-10-01T00:00:00Z",
                          "subject": "Fix bug Jev found", "body": statement}
                report = jvr.build_report(sources(commits=[commit]), START, END)
                self.assertEqual((jvr.positive_attribution(commit) is not None,
                                  report["totals"]["outcomes_verified"]), (True, 1))

    def test_negative_investigation_and_coincident_verdict_do_not_earn_credit(self):
        for denial in ("No bug found", "Investigation did not find a bug",
                       "Investigation did not identify any bug",
                       "Before merging, investigation did not confirm any bug.",
                       "Our earlier tests did not find any bug. Investigation did not confirm a bug."):
            with self.subTest(denial=denial):
                report = jvr.build_report(sources(
                    judge=[judge("build_advisory", receipt_id="coincident")],
                    commits=[{"sha": "synthetic", "date": "2026-10-01T00:00:00Z",
                              "subject": "Fix suspected bug Jev flagged", "body": denial}]), START, END)
                self.assertEqual(report["totals"]["outcomes_verified"], 0)
                self.assertIsNone(report["totals"]["minutes_saved"])
                self.assertFalse(any(s["evidence"] for s in report["sites"].values()))

    def test_matching_times_without_causal_link_remain_unproven(self):
        report = jvr.build_report(sources(judge=[judge("build_advisory")], commits=[{
            "sha": "coincident", "date": "2026-10-01T00:00:00Z", "body": "Fix from local tests",
        }]), START, END)
        self.assertEqual(report["totals"]["outcomes_verified"], 0)
        self.assertIsNone(report["totals"]["minutes_saved"])
        self.assertIn("insufficient evidence", jvr.render(report))

    def test_commit_naming_jev_as_finder_is_a_verified_outcome(self):
        commits = [{"sha": "965c6991", "date": "2026-10-01T00:00:00Z",
                    "body": "Jev flagged a real bug in the re-raise condition"},
                   {"sha": "aaaa", "date": "2026-10-01T00:00:00Z",
                    "body": "Also fixes the gap Jev's semantic_creation flagged in round 1"},
                   {"sha": "bbbb", "date": "2026-10-01T00:00:00Z",
                    "body": "Stopgap for the Jev spend the operator flagged."},
                   {"sha": "cccc", "date": "2026-10-01T00:00:00Z",
                    "body": "relabelled it, which CI's jev-build-advisory selftest caught."}]
        report = jvr.build_report(sources(commits=commits), START, END)
        review = report["sites"]["review"]
        self.assertEqual(review["outcomes_verified"], 2)
        self.assertEqual(sorted(e["ref"] for e in review["evidence"]), ["965c6991", "aaaa"])

    def test_required_actions_gate_blocks_are_self_referential_not_value(self):
        gate = [{"ts": "2026-10-01T00:00:00Z", "status": "required", "missing": ["x"]},
                {"ts": "2026-10-01T00:00:00Z", "status": "required", "missing": []}]
        report = jvr.build_report(sources(gate=gate), START, END)
        site = report["sites"]["gate:jev_required_actions"]
        self.assertEqual(site["blocks"], 1)
        self.assertEqual(site["outcomes_verified"], 0)
        self.assertIn("forces a Jev call", site["note"])

    def test_unlinked_site_prints_insufficient_evidence_and_no_value(self):
        report = jvr.build_report(sources(judge=[judge("supervise.failure_triage")]), START, END)
        site = report["sites"]["judge:supervise.failure_triage"]
        self.assertEqual(site["outcomes_verified"], 0)
        self.assertIsNone(site["minutes_saved"])
        text = jvr.render(report)
        self.assertIn("judge:supervise.failure_triage: insufficient evidence", text)

    def test_value_range_uses_measured_ci_data(self):
        ci = {"runs": [ci_run("a", 10), ci_run("a", 20, "failure"), ci_run("a", 30),
                       ci_run("b", 20), ci_run("b", 20, "cancelled")], "pulls": [], "fetched_at": END.isoformat(),
              "coverage": {"start": START.isoformat(), "end": END.isoformat(),
                           "complete": True, "time_basis": "run_started_at"}}
        commits = [{"sha": "f1", "date": "2026-10-01T00:00:00Z", "body": "Jev caught a bug"}]
        report = jvr.build_report(sources(commits=commits, ci=ci), START, END)
        self.assertEqual(report["baseline"]["ci_round_minutes_median"], 20)
        self.assertEqual(report["baseline"]["ci_rounds_per_pr_median"], 2)
        review = report["sites"]["review"]
        self.assertEqual(review["minutes_saved"], (20, 40))

    def test_no_ci_snapshot_leaves_value_unknown_with_source(self):
        commits = [{"sha": "f1", "date": "2026-10-01T00:00:00Z", "body": "Jev caught a bug"}]
        report = jvr.build_report(sources(commits=commits), START, END)
        self.assertIsNone(report["sites"]["review"]["minutes_saved"])
        self.assertIn("--fetch-ci", jvr.render(report))


class RecommendationTests(unittest.TestCase):
    def rec(self, site_key, **sources_over):
        return jvr.build_report(sources(**sources_over), START, END)["sites"][site_key]["recommendation"]

    def test_mechanical_site_without_outcomes_is_replace_with_code(self):
        self.assertEqual(self.rec("judge:supervise.stuck_and_drift",
                                  judge=[judge("supervise.stuck_and_drift")]), "replace with code")

    def test_per_turn_judgment_without_outcomes_moves_to_judgment_point(self):
        self.assertEqual(self.rec("judge:build_advisory", judge=[judge("build_advisory")]),
                         "move to a judgment point")

    def test_fixture_traffic_on_live_budget_is_remove(self):
        self.assertEqual(self.rec("judge:supervise.effort_picker [fixture]",
                                  judge=[judge("supervise.effort_picker", subject_ref={"fixture": True})]), "remove")

    def test_verified_site_is_keep(self):
        commits = [{"sha": "f1", "date": "2026-10-01T00:00:00Z", "body": "Jev caught a bug"}]
        self.assertEqual(self.rec("review", commits=commits), "keep")


class BaselineTests(unittest.TestCase):
    def test_sample_size_for_holdout_uses_measured_spread(self):
        ci = {"runs": [ci_run(f"b{i}", 10) for i in range(4)]
                      + [ci_run("b0", 10), ci_run("b1", 10), ci_run("b1", 10)], "pulls": [], "fetched_at": END.isoformat(),
              "coverage": {"start": START.isoformat(), "end": END.isoformat(),
                           "complete": True, "time_basis": "run_started_at"}}
        base = jvr.build_report(sources(ci=ci), START, END)["baseline"]
        # rounds per PR: 2, 3, 1, 1 -> mean 1.75, sample sd 0.957
        self.assertAlmostEqual(base["ci_rounds_per_pr_mean"], 1.75)
        n = base["holdout_n_per_arm"]
        expected = 2 * (1.959964 + 0.841621) ** 2 * (0.9574271 ** 2) / ((0.25 * 1.75) ** 2)
        self.assertEqual(n, int(expected) + 1)

    def test_reverts_and_canary_failures_count_as_escaped_defects(self):
        commits = [{"sha": "r1", "date": "2026-10-01T00:00:00Z", "subject": 'Revert "x"', "body": ""}]
        ci = {"runs": [ci_run("main", 5, "failure", name="Main canary", event="schedule")], "pulls": [], "fetched_at": END.isoformat(),
              "coverage": {"start": START.isoformat(), "end": END.isoformat(),
                           "complete": True, "time_basis": "run_started_at"}}
        base = jvr.build_report(sources(commits=commits, ci=ci), START, END)["baseline"]
        self.assertEqual((base["reverts"], base["main_canary_failures"]), (1, 1))


class FetchTests(unittest.TestCase):
    def test_unfiltered_discovery_avoids_search_cap_and_checks_total_count(self):
        calls = []
        def fake_gh(args):
            calls.append(args)
            return json.dumps({"total_count": 0, "workflow_runs": []}) if "actions/runs?" in args[2] else "[]"
        with tempfile.TemporaryDirectory() as tmp:
            jvr.fetch_ci(Path(tmp), START, END, gh=fake_gh)
        self.assertTrue(any("actions/runs?" in a[2] for a in calls))
        self.assertFalse(any("created=" in a[2] or "--jq" in a for a in calls))


class CliTests(unittest.TestCase):
    def test_main_reads_out_dir_and_prints_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "jev-calls.jsonl").write_text(json.dumps(call("jev_code_review")) + "\nnot json\n")
            (out / "jev-judge.jsonl").write_text("")
            price = Path(tmp) / "price.json"
            price.write_text(json.dumps(PRICE))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = jvr.main(["--root", tmp, "--since", "2026-09-27", "--until", "2026-10-04",
                               "--price-config", str(price), "--no-git"])
            self.assertEqual(rc, 0)
            self.assertIn("review", buf.getvalue())
            self.assertIn("1 unreadable line", buf.getvalue())


class BlockingReviewTests(unittest.TestCase):
    def test_dates_are_dates_and_windows_have_positive_width(self):
        with self.assertRaises(ValueError):
            jvr._day("2026-10-01T01:00:00-05:00")
        for args in (["--days", "0"], ["--days", "-1"],
                     ["--since", "2026-10-04", "--until", "2026-10-04"],
                     ["--since", "2026-10-04", "--until", "2026-10-01"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                jvr.main(args + ["--no-git"])
            self.assertEqual(exc.exception.code, 2)

    def test_commit_evidence_uses_delivery_date_and_bounded_subprocesses(self):
        result = jvr.subprocess.CompletedProcess([], 0, stdout="synthetic\x1f2026-10-01T00:00:00Z\x1fJev caught a bug\x1f\x1e")
        with patch.object(jvr.subprocess, "run", return_value=result) as run:
            commits = jvr.read_commits(HERE, START, END)
        self.assertEqual(commits[0]["date"], "2026-10-01T00:00:00Z")
        self.assertIn("--format=%h%x1f%cI%x1f%s%x1f%b%x1e", run.call_args.args[0])
        self.assertTrue(all(c.kwargs.get("timeout", 0) > 0 for c in run.call_args_list))

    def test_gh_transport_is_bounded(self):
        with patch.object(jvr.subprocess, "run", return_value=jvr.subprocess.CompletedProcess([], 0, stdout="{}")) as run:
            jvr._gh(["gh", "api", "synthetic"])
        self.assertGreater(run.call_args.kwargs.get("timeout", 0), 0)

    def test_unlinked_billable_observation_has_unknown_site_attribution(self):
        report = jvr.build_report(sources(calls=[call("jev_judge")], judge=[judge("build_advisory")]), START, END)
        site = report["sites"]["judge:build_advisory"]
        self.assertIsNone(site["usd_input"])
        self.assertAlmostEqual(report["totals"]["usd_input"], 0.042)

    def test_unknown_error_text_cannot_turn_measured_usage_into_free_usage(self):
        for error in ("holdout_disabled", "post_holdout_timeout", "network_timeout"):
            with self.subTest(error=error):
                self.assertEqual(jvr._fields(call("review", error=error))["billing"], "measured")

    def test_cache_receipts_and_observations_do_not_double_count_cost_cache_hits(self):
        report = jvr.build_report(sources(calls=[call("jev_judge", cache_hit=True)],
                                         judge=[judge("build_advisory", cache_hit=True)]), START, END)
        self.assertEqual(report["totals"]["cache_hits"], 1)
        self.assertEqual(report["sites"]["judge:build_advisory"]["observation_cache_hits"], 1)

    def test_repeated_receipt_ids_do_not_invent_one_to_one_joins(self):
        report = jvr.build_report(sources(calls=[call("jev_judge", server_receipt_id="duplicate")],
                         judge=[judge("build_advisory", receipt_id="duplicate"),
                                judge("build_advisory", receipt_id="duplicate")]), START, END)
        self.assertEqual(report["judge_hub"]["matched_judge_rows"], 0)
        self.assertEqual(report["sites"]["judge:unattributed"]["paid"], 1)

    def test_fixture_and_production_of_same_kind_remain_separate(self):
        report = jvr.build_report(sources(judge=[judge("supervise.best_of", subject_ref={"fixture": True}),
                                               judge("supervise.best_of", traffic_class="production")]), START, END)
        self.assertEqual(len(report["sites"]), 2)
        self.assertEqual(report["sites"]["judge:supervise.best_of [fixture]"]["recommendation"], "remove")
        self.assertEqual(report["sites"]["judge:supervise.best_of [production]"]["traffic_class"], "production")

    def test_cap_size_listing_is_paginated_without_creation_filter(self):
        runs = [dict(ci_run("a", 10, started="2026-09-20T00:00:00Z"), id=i, run_attempt=1)
                for i in range(1200)]
        endpoints = []
        def gh(args):
            endpoint = args[2]
            endpoints.append(endpoint)
            if "actions/runs?" in endpoint:
                page = int(endpoint.split("page=")[1].split("&")[0])
                return json.dumps({"total_count": 1200, "workflow_runs": runs[(page-1)*100:page*100]})
            return "[]"
        with tempfile.TemporaryDirectory() as tmp:
            _, snapshot = jvr.fetch_ci(Path(tmp), START, END, gh=gh)
        self.assertTrue(snapshot["coverage"]["complete"])
        self.assertEqual(snapshot["coverage"]["retained_run_count"], 1200)
        self.assertEqual(sum("actions/runs?" in e for e in endpoints), 12)

    def complete_ci(self, runs):
        return {"runs": runs, "pulls": [], "fetched_at": END.isoformat(),
                "coverage": {"start": START.isoformat(), "end": END.isoformat(),
                             "time_basis": "run_started_at", "complete": True}}

    def test_01_receipts_conserve_spend_for_legacy_review_and_failed_judges(self):
        for calls, judges in [([call(None)], [judge("build_advisory")]),
                              ([call("jev_code_review")], [judge("post_write_task_fit")]),
                              ([call("jev_judge")], [judge("build_advisory"),
                               judge("build_advisory", error="missing credential", usage=None)])]:
            with self.subTest(calls=calls):
                report = jvr.build_report(sources(calls=calls, judge=judges), START, END)
                for field in ("paid", "input_tokens", "output_tokens", "billing_unknown"):
                    self.assertEqual(sum(s[field] for s in report["sites"].values()), report["totals"][field])
                    self.assertTrue(all(s[field] >= 0 for s in report["sites"].values()))

    def test_02_normalizes_free_refusals_and_unknown_billing(self):
        free = judge("supervise.best_of", note="deterministic_prefilter", model=None,
                     usage=None, elapsed_ms=0)
        cases = [free] + [judge("build_advisory", error=e, usage=None) for e in
                           ("build_advisory:network", "build_advisory:auth_failed", "missing credential")]
        for row in cases:
            with self.subTest(row=row):
                self.assertEqual(jvr._judge_fields(row)["billing"], "not_billed")
                self.assertEqual(jvr._fields(row)["billing"], "not_billed")
        self.assertEqual(jvr._fields(call("x", error="holdout", holdout=True))["billing"], "not_billed")
        report = jvr.build_report(sources(calls=[call("x", input_tokens=None,
                                 output_tokens=None, error="timeout", usage=None)]), START, END)
        self.assertEqual(report["totals"]["billing_unknown"], 1)
        self.assertEqual(report["totals"]["paid"], 0)
        self.assertIsNone(report["totals"]["usd_input"])

    def test_03_positive_complete_message_attribution_only(self):
        for message, expected in [("Jev found nothing; fix a typo found by local tests", 0),
                                  ("Jev caught a bug", 1), ("Jev caught no bug", 0),
                                  ("Jev flagged a concern; no fix needed", 0),
                                  ("Jev found a bug; deferred the fix", 0)]:
            with self.subTest(message=message):
                report = jvr.build_report(sources(commits=[{"sha": "synthetic", "date": START.isoformat(),
                                         "subject": message, "body": ""}]), START, END)
                self.assertEqual(sum(s["outcomes_verified"] for s in report["sites"].values()), expected)
                body_report = jvr.build_report(sources(commits=[{"sha": "synthetic", "date": START.isoformat(),
                                              "body": message}]), START, END)
                self.assertEqual(sum(s["outcomes_verified"] for s in body_report["sites"].values()), expected)

    def test_03_rejects_negation_of_every_accepted_defect_noun(self):
        messages = ["Jev found no regression; fix a typo found by local tests",
                    "Jev found no error; no action needed"]
        for noun in ("bug", "gap", "defect", "error", "regression"):
            article = "an" if noun == "error" else "a"
            messages.extend([f"Jev found no {noun}", f"Jev found no {noun}s",
                             f"Jev flagged a concern, not {article} {noun}"])
        for message in messages:
            for field in ("subject", "body"):
                with self.subTest(message=message, field=field):
                    report = jvr.build_report(sources(commits=[{
                        "sha": "synthetic", "date": START.isoformat(), field: message,
                    }]), START, END)
                    self.assertEqual(report["totals"]["outcomes_verified"], 0)
                    self.assertFalse(any(site["evidence"] for site in report["sites"].values()))
                    self.assertFalse(any(site["recommendation"] == "keep"
                                         for site in report["sites"].values()))

    def test_03_accepts_positive_attribution_for_every_defect_noun(self):
        for noun in ("bug", "gap", "defect", "error", "regression"):
            article = "an" if noun == "error" else "a"
            for field in ("subject", "body"):
                with self.subTest(noun=noun, field=field):
                    report = jvr.build_report(sources(commits=[{
                        "sha": "synthetic", "date": START.isoformat(),
                        field: f"Jev caught {article} {noun}",
                    }]), START, END)
                    self.assertEqual(report["totals"]["outcomes_verified"], 1)
                    self.assertEqual(report["sites"]["review"]["recommendation"], "keep")

    def test_03_validation_without_regressions_preserves_attributed_fix(self):
        self.assert_attributed_fix_survives_validation(
            "Validation confirms no regression in existing behavior.")

    def test_03_validation_without_errors_preserves_attributed_fix(self):
        self.assert_attributed_fix_survives_validation(
            "The corrected request now returns successfully with no error.")

    def test_03_same_noun_validation_keeps_attributed_fix(self):
        self.assert_attributed_fix_survives_validation("No bugs remain after the patch.")

    def test_03_body_denial_of_attributed_bug_excludes_fix(self):
        self.assert_cross_field_denial_excluded(
            "Fix suspected bug Jev flagged",
            "Investigation found no bug; the patch only fixes a typo.")

    def test_03_subject_denial_of_attributed_bug_excludes_fix(self):
        self.assert_cross_field_denial_excluded(
            "No bug found; fix typo", "Jev flagged a suspected bug.")

    def assert_cross_field_denial_excluded(self, subject, body):
        commit = {"sha": "synthetic", "date": START.isoformat(), "subject": subject, "body": body}
        report = jvr.build_report(sources(commits=[commit]), START, END)
        self.assertEqual(report["totals"]["outcomes_verified"], 0)
        self.assertFalse(any(site["evidence"] for site in report["sites"].values()))

    def assert_attributed_fix_survives_validation(self, validation):
        commit = {"sha": "synthetic", "date": START.isoformat(),
                  "subject": "Fix the bug Jev caught", "body": validation}
        report = jvr.build_report(sources(commits=[commit]), START, END)
        self.assertEqual(report["totals"]["outcomes_verified"], 1)
        self.assertEqual(report["sites"]["review"]["recommendation"], "keep")
        self.assertEqual(report["sites"]["review"]["evidence"][0]["quote"],
                         "Fix the bug Jev caught")

    def test_04_final_totals_match_text_and_sites(self):
        report = jvr.build_report(sources(calls=[call("review")], judge=[judge("build_advisory", elapsed_ms=1000)],
                   commits=[{"sha": "synthetic", "date": START.isoformat(), "subject": "Jev caught a bug"}],
                   ci=self.complete_ci([ci_run("a", 10)])), START, END)
        total = report["totals"]
        self.assertEqual(total["outcomes_verified"], 1)
        self.assertEqual(total["minutes_saved"], (10, 10))
        self.assertEqual(total["wait_ms"], 1000)
        self.assertIn("1 finding(s) worth 10–10 CI minutes", jvr.render(report))
        self.assertEqual(json.loads(json.dumps(report))["totals"]["minutes_saved"], [10, 10])

    def test_05_cache_latency_is_measured_even_without_billing(self):
        report = jvr.build_report(sources(judge=[judge("build_advisory", cache_hit=True, elapsed_ms=1000)]), START, END)
        site = report["sites"]["judge:build_advisory"]
        self.assertEqual((site["observation_cache_hits"], site["wait_ms"], site["paid"]), (1, 1000, 0))

    def test_06_attempt_history_counts_timeout_then_success_with_own_times(self):
        runs = [dict(ci_run("a", 10, "timed_out"), id=1, run_attempt=1),
                dict(ci_run("a", 20), id=1, run_attempt=2),
                dict(ci_run("a", 99, started="2026-09-20T00:00:00Z"), id=1, run_attempt=3)]
        base = jvr.build_report(sources(ci=self.complete_ci(runs)), START, END)["baseline"]
        self.assertEqual(base["ci_rounds_per_pr_mean"], 2)
        self.assertEqual(base["ci_failed_rounds_per_pr_mean"], 1)
        self.assertEqual(base["ci_round_minutes_median"], 15)

    def test_07_incomplete_wider_or_stale_snapshots_refuse_estimates(self):
        for ci in [{"runs": [ci_run("a", 10)], "since": "2026-10-03", "fetched_at": "2026-10-03T12:00:00Z"},
                   {**self.complete_ci([ci_run("a", 10)]), "fetched_at": "2026-10-03T12:00:00Z"},
                   {**self.complete_ci([ci_run("a", 10)]), "coverage":
                    {"start": "2026-10-01T00:00:00Z", "end": END.isoformat(), "complete": True,
                     "time_basis": "created_at"}}]:
            with self.subTest(ci=ci):
                report = jvr.build_report(sources(ci=ci), START, END)
                self.assertIsNone(report["baseline"]["ci_round_minutes_median"])
                self.assertIn("CI coverage incomplete", jvr.render(report))

    def fetcher(self, runs, attempts=None, total=None):
        def gh(args):
            endpoint = args[2]
            if "/attempts/" in endpoint:
                return json.dumps(attempts[int(endpoint.rsplit("/", 1)[1])])
            if "actions/runs?" in endpoint:
                return json.dumps({"total_count": len(runs) if total is None else total,
                                   "workflow_runs": runs if "page=1&" in endpoint else []})
            return "[]"
        return gh

    def test_07_fetch_includes_cross_window_rerun_attempts(self):
        latest = dict(ci_run("a", 20), id=1, run_attempt=2, created_at="2026-09-20T00:00:00Z", status="completed")
        earlier = dict(ci_run("a", 10, "failure"), id=1, run_attempt=1, status="completed")
        with tempfile.TemporaryDirectory() as tmp:
            _, ci = jvr.fetch_ci(Path(tmp), START, END, gh=self.fetcher([latest], {1: earlier, 2: latest}))
        self.assertEqual([r["run_attempt"] for r in ci["runs"]], [1, 2])
        self.assertTrue(ci["coverage"]["complete"])
        self.assertEqual(jvr.baseline(ci, [], START, END)["ci_rounds_per_pr_mean"], 2)

    def test_07_saturated_or_truncated_history_cannot_publish_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "incomplete|truncated"):
                jvr.fetch_ci(Path(tmp), START, END, gh=self.fetcher([], total=1000))
            self.assertFalse((Path(tmp) / "out" / jvr.CI_SNAPSHOT).exists())

    def test_08_snapshot_atomic_reader_and_failed_replace_preserve_good_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out" / jvr.CI_SNAPSHOT
            target.parent.mkdir()
            prior = self.complete_ci([])
            target.write_text(json.dumps(prior))
            original_replace = jvr.os.replace
            observed = []
            def replacing(src, dst):
                observed.append(json.loads(target.read_text()))
                self.assertIsInstance(json.loads(Path(src).read_text()), dict)
                original_replace(src, dst)
            with patch.object(jvr.os, "replace", side_effect=replacing):
                jvr.fetch_ci(Path(tmp), START, END, gh=self.fetcher([]))
            self.assertEqual(observed, [prior])
            good = target.read_bytes()
            with patch.object(jvr.os, "replace", side_effect=OSError("synthetic publication failure")):
                with self.assertRaises(OSError):
                    jvr.fetch_ci(Path(tmp), START, END, gh=self.fetcher([]))
            self.assertEqual(target.read_bytes(), good)

    def test_09_missing_empty_partial_and_unreadable_logs_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            def report():
                buf = io.StringIO()
                with redirect_stdout(buf):
                    jvr.main(["--root", tmp, "--since", "2026-09-27", "--until", "2026-10-04", "--no-git", "--json"])
                return json.loads(buf.getvalue())
            missing = report()
            self.assertIsNone(missing["totals"]["paid"])
            self.assertIsNone(missing["totals"]["usd_input"])
            path = out / "jev-calls.jsonl"
            path.write_text("")
            self.assertEqual(report()["totals"]["paid"], 0)
            path.write_text(json.dumps(call("review")) + "\nnot json\n")
            self.assertEqual(report()["source_status"][str(path)]["status"], "partial")
            self.assertIsNone(report()["totals"]["usd_input"])
            original = Path.open
            def opening(p, *args, **kwargs):
                if p == path:
                    raise PermissionError("synthetic read failure")
                return original(p, *args, **kwargs)
            with patch.object(Path, "open", opening):
                unreadable = report()
            self.assertEqual(unreadable["source_status"][str(path)]["status"], "read_failed")
            self.assertIsNone(unreadable["totals"]["paid"])

    def test_10_fixture_recommendation_requires_provenance(self):
        for provenance, expected in [("production", "keep (unproven; holdout decides)"),
                                     ("fixture", "remove"), (None, "keep (unproven; holdout decides)")]:
            for kind in ("supervise.effort_picker", "supervise.best_of"):
                with self.subTest(provenance=provenance, kind=kind):
                    row = judge(kind, traffic_class=provenance)
                    report = jvr.build_report(sources(judge=[row]), START, END)
                    site = next(iter(report["sites"].values()))
                    self.assertEqual(site["recommendation"], expected)
                    self.assertEqual(site["traffic_class"], provenance or "unknown")

    def test_11_fixture_uses_anonymous_actor(self):
        for node in ast.walk(ast.parse(Path(__file__).read_text())):
            if isinstance(node, ast.Dict):
                literals = {k.value: v.value for k, v in zip(node.keys, node.values)
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
                if literals.get("sha") == "bbbb":
                    self.assertEqual(literals["body"], "Stopgap for the Jev spend the operator flagged.")
                    return
        self.fail("anonymous non-finder fixture missing")


if __name__ == "__main__":
    unittest.main()
