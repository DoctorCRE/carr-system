#!/usr/bin/env python3
"""Offline behavioral tests of class routing and paired judge evaluation."""
import importlib.util
import unittest
import io
import json
import sys
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/judge" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoutingTests(unittest.TestCase):
    def test_default_passes_exact_request_options_and_response(self):
        judge = load("interface")
        seen = []
        result = {"model": "jev-1.13.0", "answers": {}, "usage": {}}
        def transport(state, questions, **options):
            seen.append((state, questions, options))
            return result
        state, questions = {"subject": "code"}, {"q": {"type": "noul"}}
        self.assertIs(judge.ask(state, questions, jev=transport, timeout=7, caller="review"), result)
        self.assertEqual(seen, [(state, questions, {"timeout": 7, "caller": "review"})])

    def test_switch_fails_before_legacy_transport_and_runtime_stays_jev(self):
        judge = load("interface")
        routing = {"schema": "carr-judge-providers/v1", "providers": {
            "system_work": "decisions", "app_runtime": "jev"}}
        seen = []
        transport = lambda *a, **k: seen.append(a) or "legacy"
        with self.assertRaisesRegex(judge.JudgeUnavailable, "decisions contract not yet verified / no key"):
            judge.ask("state", {"q": {}}, jev=transport, config=routing)
        self.assertEqual(seen, [])
        self.assertEqual(judge.ask("state", {"q": {}}, jev=transport,
                                  config=routing, work_class="app_runtime"), "legacy")
        routing["providers"]["app_runtime"] = "decisions"
        with self.assertRaisesRegex(judge.JudgeUnavailable, "pinned"):
            judge.ask("state", {"q": {}}, jev=transport, config=routing, work_class="app_runtime")

    def test_legacy_public_client_uses_switch_and_preserves_wire_response(self):
        spec = importlib.util.spec_from_file_location("tsc_seam_test", ROOT / "ops/typesafe_client.py")
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        routing = {"schema": "carr-judge-providers/v1", "providers": {
            "system_work": "decisions", "app_runtime": "jev"}}
        class Response(io.BytesIO):
            status = 200
        # The client refuses a question with no instructions before sending it.
        QUESTION = {"type": "noul", "instructions": "Is this the fixture?"}
        seen = []
        def transport(request, **kwargs):
            seen.append(json.loads(request.data))
            return Response(b'{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.75}},"usage":{"input_tokens":10,"output_tokens":2}}')
        options = dict(api_key="offline-test-value", opener=transport, caller="review")
        with patch.object(client.JUDGE, "provider_for", side_effect=lambda cls, config=None: "decisions" if cls == "system_work" else "jev"):
            with self.assertRaisesRegex(client.TypeSafeError, "decisions contract not yet verified / no key"):
                client.ask("code", {"q": QUESTION}, **options)
            result = client.ask("deal", {"q": QUESTION}, work_class="app_runtime", **options)
        self.assertEqual(seen, [{"state": "deal", "model": "jev-latest", "questions": {"q": QUESTION}}])
        self.assertEqual(result["answers"]["q"]["noul"], 0.75)
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 2})

    def test_production_deal_reader_stays_on_jev_with_system_switched(self):
        sys.path.insert(0, str(ROOT))
        from ops import jev_deal_read as deals
        bundle = dict(has_evidence=True, evidence_chars=400, name="Practice",
                      client="Owner", phase="search", deal_type="lease", segment="dental",
                      city="Town", owner="Broker", next_step=None, next_step_due=None,
                      status_narrative="Offer submitted; landlord response pending", history=[],
                      days_since_record_touched=2)
        class Response(io.BytesIO):
            status = 200
        def transport(request, **options):
            return Response(json.dumps({"model": "jev-1.13.0", "answers": {
                "movement": {"type": "score", "score": 2, "confidence": .9, "probabilities": {"2": 1}},
                "waiting_on": {"type": "choice", "choice": "counterparty", "confidence": .9, "probabilities": {"counterparty": 1}},
                "silence_is_bad": {"type": "noul", "noul": .1}},
                "usage": {"input_tokens": 10, "output_tokens": 3}}).encode())
        with patch.object(deals.ts.JUDGE, "provider_for", side_effect=lambda cls, config=None: "decisions" if cls == "system_work" else "jev"):
            result = deals.read_deal(bundle, api_key="offline-test-value", opener=transport)
        self.assertTrue(result["judged"], result)


class EvaluationTests(unittest.TestCase):
    def test_reused_answers_are_excluded_from_provider_repeat_metrics(self):
        ev = load("paired_eval")
        request = {"state": "synthetic", "model": "jev-1.13.0", "questions": {"q": {"type": "noul"}}}
        corpus = ev.freeze([{"receipt_id": "cached-fixture", "request": request,
                             "request_sha256": ev.digest(request), "work_class": "system_work", "gold": {"q": True}}])
        def answer(*a, **k):
            return {"cache_hit": True, "model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": .9}},
                    "usage": {"input_tokens": 1000, "output_tokens": 10}, "latency_ms": 123}
        report = ev.run(corpus, answer, answer, repeats=2,
                        rates={name: {"input_usd_per_million": 2, "output_usd_per_million": 4} for name in ("jev", "decisions")})
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["paired_successes"], 0)
        for metrics in report["providers"].values():
            self.assertEqual(metrics["successes"], 0)
            self.assertEqual(metrics["cache_hits"], 2)
            self.assertEqual(metrics["cost_usd"], 0)
            self.assertIsNone(metrics["p50_latency_ms"])
            self.assertEqual(metrics["labelled_questions"], 0)

    def test_score_alias_collisions_and_shadowed_invalid_values_are_not_evidence(self):
        ev = load("paired_eval")
        request = {"state": "code", "model": "jev-1.13.0", "questions": {
            "q": {"type": "score", "criteria": ["low", "high"]}}}
        corpus = ev.freeze([{"receipt_id": "score-aliases", "request": request,
                             "request_sha256": ev.digest(request), "work_class": "system_work",
                             "gold": {"q": "1"}}])
        probabilities = [
            {"0": value, "1": 0, "low": 0, "high": 1}
            for value in (-1, "invalid", True, float("nan"), float("inf"), 2)
        ] + [
            {"0": 1, "1": 0, "low": 0, "high": 1},
            {"0": 0, "1": 1, "low": 0, "high": 1},
            {"low": 0, "high": 1, "0": -1, "1": 0},
        ]
        for raw in probabilities:
            with self.subTest(probabilities=raw):
                def answer(*args, **kwargs):
                    return {"model": "offline-provider", "answers": {"q": {
                        "type": "score", "score": 1, "confidence": 1,
                        "probabilities": raw}}, "usage": {"input_tokens": 1, "output_tokens": 1}}
                report = ev.run(corpus, answer, answer)
                self.assertEqual(report["status"], "incomplete")
                self.assertEqual(report["paired_successes"], 0)
                self.assertIsNone(report["agreement"])
                for provider in report["providers"].values():
                    self.assertEqual(provider["errors"], 1)
                    self.assertEqual(provider["successes"], 0)
                    self.assertEqual(provider["labelled_questions"], 0)
                    self.assertIsNone(provider["brier"])
                    self.assertIsNone(provider["ece"])

    def test_unique_score_aliases_preserve_calibration_and_provider_response(self):
        ev = load("paired_eval")
        request = {"state": "code", "model": "jev-1.13.0", "questions": {
            "q": {"type": "score", "criteria": ["low", "high"]}}}
        corpus = ev.freeze([{"receipt_id": "unique-aliases", "request": request,
                             "request_sha256": ev.digest(request), "work_class": "system_work",
                             "gold": {"q": "1"}}])
        for raw in ({"0": .25, "1": .75}, {"low": .25, "high": .75},
                    {"0": .25, "high": .75}, {"low": .25, "1": .75}, {"high": 1}):
            with self.subTest(probabilities=raw):
                response = {"model": "offline-provider", "answers": {"q": {
                    "type": "score", "score": 1, "confidence": 1, "probabilities": raw}},
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
                report = ev.run(corpus, lambda *a, **k: response, lambda *a, **k: response)
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["agreement"], 1)
                for provider in report["providers"].values():
                    self.assertEqual(provider["errors"], 0)
                    self.assertAlmostEqual(provider["brier"], 0 if raw == {"high": 1} else .125)
                    self.assertAlmostEqual(provider["ece"], 0 if raw == {"high": 1} else .25)
                self.assertEqual(response["answers"]["q"]["probabilities"], raw)
                self.assertEqual(report["rows"][0]["answers"], response["answers"])

    def test_out_of_domain_probability_support_never_counts_as_paired_evidence(self):
        ev = load("paired_eval")
        cases = [
            ({"type": "choice", "criteria": {"a": "fits", "b": "misses"}},
             {"type": "choice", "choice": "a", "confidence": 1}, "outside", "a"),
            ({"type": "score", "criteria": ["low", "high"]},
             {"type": "score", "score": 1, "confidence": 1}, "99", "1"),
        ]
        for question, selected, outside, valid_gold in cases:
            for gold in (None, valid_gold):
                for probabilities in ({outside: 1}, {valid_gold: 1, outside: 0}):
                    with self.subTest(kind=question["type"], gold=gold, probabilities=probabilities):
                        request = {"state": "code", "model": "jev-1.13.0", "questions": {"q": question}}
                        receipt = {"receipt_id": "bad-support", "request": request,
                                   "request_sha256": ev.digest(request), "work_class": "system_work",
                                   "gold": {} if gold is None else {"q": gold}}
                        def answer(*args, **kwargs):
                            return {"model": "offline-provider", "answers": {
                                "q": {**selected, "probabilities": probabilities}},
                                "usage": {"input_tokens": 1, "output_tokens": 1}}
                        report = ev.run(ev.freeze([receipt]), answer, answer)
                        self.assertEqual(report["status"], "incomplete")
                        self.assertEqual(report["paired_successes"], 0)
                        self.assertEqual(report["paired_questions"], 0)
                        self.assertIsNone(report["agreement"])
                        for provider in report["providers"].values():
                            self.assertEqual(provider["errors"], 1)
                            self.assertEqual(provider["successes"], 0)
                            self.assertEqual(provider["labelled_questions"], 0)
                            self.assertIsNone(provider["brier"])
                            self.assertIsNone(provider["ece"])

    def test_gold_outside_original_question_domain_is_rejected_before_provider_calls(self):
        ev = load("paired_eval")
        cases = [
            ({"type": "choice", "criteria": {"a": "fits", "b": "misses"}}, "q", "outside"),
            ({"type": "score", "criteria": ["low", "high"]}, "q", "99"),
            ({"type": "noul"}, "q", "outside"),
            ({"type": "noul"}, "unasked", True),
        ]
        for question, qid, gold in cases:
            with self.subTest(kind=question["type"], qid=qid, gold=gold):
                request = {"state": "code", "model": "jev-1.13.0", "questions": {"q": question}}
                receipt = {"receipt_id": "bad-gold", "request": request,
                           "request_sha256": ev.digest(request), "work_class": "system_work",
                           "gold": {qid: gold}}
                calls = []
                def provider(*args, **kwargs):
                    calls.append(args)
                    raise AssertionError("invalid corpus must not reach a provider")
                with self.assertRaisesRegex(ValueError, "gold.*requested"):
                    ev.run(ev.freeze([receipt]), provider, provider)
                self.assertEqual(calls, [])

    def test_frozen_real_receipt_binds_inputs_to_worker_receipt_and_is_replayable(self):
        ev = load("paired_eval")
        receipts = json.loads((ROOT / "ops/fixtures/judge-provider/frozen-receipts.v1.json").read_text())
        corpus = ev.freeze(receipts)
        self.assertTrue(corpus["cases"])
        for receipt in receipts:
            self.assertEqual(ev.digest(receipt["request"]["state"]), receipt["state_sha256"])
            self.assertEqual(receipt["baseline_response"]["model"], "jev-1.13.0")

    def test_choice_score_calibration_cost_and_latency_have_hand_calculated_values(self):
        ev = load("paired_eval")
        request = {"state": "code", "model": "jev-1.13.0", "questions": {
            "pick": {"type": "choice", "instructions": "best?", "criteria": {"a": "fits", "b": "misses"}},
            "level": {"type": "score", "instructions": "how much?", "criteria": ["low", "high"]}}}
        corpus = ev.freeze([{"receipt_id": "oracle", "request": request, "request_sha256": ev.digest(request),
                             "work_class": "system_work", "gold": {"pick": "a", "level": "1"}}])
        def answer(*args, **kwargs):
            return {"model": "offline-oracle", "answers": {
                "pick": {"type": "choice", "choice": "a", "confidence": .8, "probabilities": {"a": .8, "b": .2}},
                "level": {"type": "score", "score": .75, "confidence": .75, "probabilities": {"0": .25, "1": .75}}},
                "usage": {"input_tokens": 100, "output_tokens": 10}}
        ticks = iter([0, .1, 1, 1.2])
        report = ev.run(corpus, answer, answer, clock=lambda: next(ticks),
                        rates={"jev": {"input_usd_per_million": 2, "output_usd_per_million": 4}})
        self.assertAlmostEqual(report["providers"]["jev"]["brier"], .1025)
        self.assertAlmostEqual(report["providers"]["jev"]["ece"], .225)
        self.assertAlmostEqual(report["providers"]["jev"]["cost_usd"], .00024)
        self.assertAlmostEqual(report["providers"]["jev"]["p50_latency_ms"], 100)
        self.assertAlmostEqual(report["providers"]["decisions"]["p95_latency_ms"], 200)

    def test_paired_metrics_use_same_frozen_inputs_and_missing_cost_is_unknown(self):
        ev = load("paired_eval")
        request = {"state": "frozen code", "questions": {"q": {"type": "noul", "instructions": "fits?"}}, "model": "jev-1.13.0"}
        receipt = {"receipt_id": "real-1", "request": request, "request_sha256": ev.digest(request), "work_class": "system_work", "gold": {"q": True}}
        frozen = ev.freeze([receipt])
        seen = []
        def baseline(state, questions, **kwargs):
            seen.append((state, questions, kwargs))
            return {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.8}}, "usage": {"input_tokens": 100, "output_tokens": 10}}
        def candidate(state, questions, **kwargs):
            seen.append((state, questions, kwargs))
            return {"model": "candidate", "answers": {"q": {"type": "noul", "noul": 0.6}}, "usage": {"input_tokens": 100, "output_tokens": 10}}
        report = ev.run(frozen, baseline, candidate)
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(report["agreement"], 1)
        self.assertAlmostEqual(report["providers"]["jev"]["brier"], 0.04)
        self.assertAlmostEqual(report["providers"]["decisions"]["brier"], 0.16)
        self.assertIsNone(report["providers"]["decisions"]["cost_usd"])
        self.assertIsNotNone(report["providers"]["decisions"]["p95_latency_ms"])
        receipt["request"]["state"] = "tampered"
        with self.assertRaisesRegex(ValueError, "digest"):
            ev.freeze([receipt])

    def test_historical_digest_only_receipts_are_refused_and_provider_failure_is_not_agreement(self):
        ev = load("paired_eval")
        with self.assertRaisesRegex(ValueError, "input"):
            ev.freeze([{"receipt_id": "hash-only", "state_sha256": "a" * 64}])
        request = {"state": "code", "questions": {"q": {"type": "noul", "instructions": "fits?"}}, "model": "jev-1.13.0"}
        corpus = ev.freeze([{"receipt_id": "real-1", "request": request, "request_sha256": ev.digest(request), "work_class": "system_work"}])
        answer = lambda *a, **k: {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 1, "output_tokens": 1}}
        report = ev.run(corpus, answer, load("interface").provider_decisions)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["paired_successes"], 0)
        self.assertIsNone(report["agreement"])
        self.assertEqual(report["providers"]["decisions"]["errors"], 1)


class InventoryTests(unittest.TestCase):
    def test_ordered_classification_distinguishes_deal_reading_from_review_and_records_questions(self):
        inv = load("inventory")
        for path, expected in [("ops/jev_deal_read.py", "app_runtime"),
                               ("tools/dictation-rig/bin/post_call_jev.py", "app_runtime"),
                               ("hooks/lint-gate.py", "system_work"),
                               ("tools/room-bridge/queue_grammar.py", "system_work")]:
            kind, procedure = inv.classify(path)
            self.assertEqual(kind, expected)
            self.assertTrue(procedure)
            self.assertIn("answer", procedure[0])


if __name__ == "__main__":
    unittest.main()
