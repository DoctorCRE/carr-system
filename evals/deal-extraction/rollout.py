"""Evaluate frozen rollout thresholds; synthetic evidence can never release extraction."""

import argparse
from collections import defaultdict
import copy
import hashlib
import json
from math import sqrt
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lib.deal_extraction import CONTRACTS, regional_facts
from score import score_schedule
from schedules import build_corpus

POLICY = {
    "version": "deal-regions-rollout.v2",
    "minimum_real_documents": 25,
    "minimum_per_stratum": 2,
    "minimum_stratum_accuracy": 0.80,
    "precision_lower_bound": 0.85,
    "recall_lower_bound": 0.85,
    "schedule_lower_bound": 0.85,
    "routing_lower_bound": 0.85,
    "maximum_abstention_upper_bound": 0.30,
    "confidence_level": 0.95,
    "maximum_high_priority_rate": 0.50,
    "minimum_mutations": 4,
}
FIELDS = (*CONTRACTS, "rent_schedule")
TYPES = ("loi", "lease", "counter", "amendment")


def bounds(success, total):
    if not total:
        return [0.0, 1.0]
    z = 1.959963984540054
    rate = success / total
    center = (rate + z * z / (2 * total)) / (1 + z * z / total)
    width = (
        z
        * sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / (1 + z * z / total)
    )
    return [max(0.0, center - width), min(1.0, center + width)]


def correct(field, predicted, expected):
    return (
        score_schedule(predicted, expected)
        if field == "rent_schedule"
        else predicted == expected
    )


def mutation_check():
    base = [
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
            "escalation_percent": "3",
        },
    ]
    mutants = []
    for field in ("rate", "start_month", "expense_basis", "escalation_percent"):
        value = copy.deepcopy(base)
        value[0][field], value[1][field] = value[1][field], value[0][field]
        if value == base:
            value[1][field] = "gross"
        mutants.append(value)
    killed = sum(not score_schedule(value, base) for value in mutants)
    return {
        "executed": len(mutants),
        "killed": killed,
        "pass": killed == len(mutants) and killed >= POLICY["minimum_mutations"],
    }


def report(corpus, predictions, *, queue_capacity, daily_documents):
    if (
        type(queue_capacity) is not int
        or type(daily_documents) is not int
        or queue_capacity < 0
        or daily_documents < 1
    ):
        raise ValueError("positive traffic and nonnegative queue capacity required")
    cases = corpus["cases"]
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate corpus identity")
    submitted = {}
    for prediction in predictions:
        identity = prediction["case_id"]
        if identity not in by_id or identity in submitted:
            raise ValueError("unknown or duplicate prediction identity")
        if set(prediction["fields"]) - set(FIELDS) or set(
            prediction.get("methods", {})
        ) != set(prediction["fields"]):
            raise ValueError("unknown predicted field")
        if prediction["route"] not in ("high", "normal", "none"):
            raise ValueError("invalid route")
        submitted[identity] = prediction
    buckets = defaultdict(list)
    for case in cases:
        if case["document_type"] not in TYPES or type(case["ocr"]) is not bool:
            raise ValueError("invalid corpus stratum")
        prediction = submitted.get(case["id"], {"fields": {}, "route": "none"})
        for field in FIELDS:
            expected = case["expected"].get(field)
            observed = prediction["fields"].get(field)
            emitted = observed is not None
            expected_method = case.get("expected_methods", {}).get(
                field, "ocr" if case["ocr"] else "text_layer"
            )
            match = (
                emitted
                and expected is not None
                and correct(field, observed, expected)
                and prediction.get("methods", {}).get(field) == expected_method
            )
            buckets[(field, case["document_type"], expected_method == "ocr")].append(
                (
                    expected is not None,
                    emitted,
                    match,
                    prediction["route"] == case["expected_route"],
                )
            )
    pooled = []
    for field in FIELDS:
        rows = [r for (key, _, _), group in buckets.items() if key == field for r in group]
        support, emitted, matched = (sum(r[i] for r in rows) for i in range(3))
        precision, recall = bounds(matched, emitted), bounds(matched, support)
        routing = bounds(sum(r[3] for r in rows), len(rows))
        abstention = bounds(sum(r[0] and not r[1] for r in rows), support)
        pooled.append({"field": field, "support": support, "precision_95": precision,
                       "recall_95": recall, "routing_95": routing, "abstention_95": abstention,
                       "pass": support >= POLICY["minimum_real_documents"]
                       and precision[0] >= POLICY["precision_lower_bound"]
                       and recall[0] >= POLICY["recall_lower_bound"]
                       and (field != "rent_schedule" or recall[0] >= POLICY["schedule_lower_bound"])
                       and routing[0] >= POLICY["routing_lower_bound"]
                       and abstention[1] <= POLICY["maximum_abstention_upper_bound"]})
    strata = []
    for field in FIELDS:
        for kind in TYPES:
            for ocr in (False, True):
                rows = buckets[(field, kind, ocr)]
                support = sum(r[0] for r in rows)
                emitted = sum(r[1] for r in rows)
                matched = sum(r[2] for r in rows)
                precision = bounds(matched, emitted)
                recall = bounds(matched, support)
                routing = bounds(sum(r[3] for r in rows), len(rows))
                abstention = bounds(sum(r[0] and not r[1] for r in rows), support)
                supported = (
                    len(rows) >= POLICY["minimum_per_stratum"]
                    and support >= 1
                )
                passed = (supported and emitted > 0
                          and matched / emitted >= POLICY["minimum_stratum_accuracy"]
                          and matched / support >= POLICY["minimum_stratum_accuracy"]
                          and sum(r[3] for r in rows) / len(rows) >= POLICY["minimum_stratum_accuracy"])
                strata.append(
                    {
                        "field": field,
                        "document_type": kind,
                        "ocr": ocr,
                        "documents": len(rows),
                        "support": support,
                        "emitted": emitted,
                        "correct": matched,
                        "precision_95": precision,
                        "recall_95": recall,
                        "routing_95": routing,
                        "abstention_95": abstention,
                        "supported": supported,
                        "pass": passed,
                    }
                )
    high = sum(submitted.get(case["id"], {}).get("route") == "high" for case in cases)
    rate = high / len(cases) if cases else 1
    queue_required = daily_documents * rate
    corpus_ok = (
        corpus.get("synthetic_only") is False
        and corpus.get("frozen_holdout") is True
        and corpus.get("adjudicated_real_documents", 0)
        >= POLICY["minimum_real_documents"]
        and len(cases) >= POLICY["minimum_real_documents"]
    )
    mutation = mutation_check()
    reasons = []
    if not corpus_ok or any(not s["supported"] for s in strata):
        reasons.append("insufficient corpus")
    if queue_capacity < queue_required:
        reasons.append("queue capacity inadequate")
    if rate > POLICY["maximum_high_priority_rate"]:
        reasons.append("routing rate inadequate")
    if any(not s["pass"] for s in strata + pooled):
        reasons.append("field accuracy or routing confidence inadequate")
    if not mutation["pass"]:
        reasons.append("mutation checks inadequate")
    return {
        "schema_version": POLICY["version"],
        "policy": POLICY,
        "corpus_sha256": hashlib.sha256(
            json.dumps(corpus, sort_keys=True).encode()
        ).hexdigest(),
        "synthetic_only": corpus.get("synthetic_only"),
        "strata": strata,
        "pooled": pooled,
        "quality_pass": all(s["pass"] for s in strata + pooled),
        "mutations": mutation,
        "high_priority_rate": rate,
        "queue_capacity": queue_capacity,
        "daily_documents": daily_documents,
        "required_queue_capacity": queue_required,
        "release_allowed": not reasons,
        "status": "insufficient corpus"
        if "insufficient corpus" in reasons
        else "blocked"
        if reasons
        else "pass",
        "reasons": reasons,
        "remaining": [
            "25-document adjudicated real corpus with approved document access"
        ],
    }


def synthetic_predictions(corpus):
    predictions = []
    for case in corpus["cases"]:
        periods, scalars, disagreements, _ = regional_facts(
            case["pages"], case["regions"]
        )
        fields = {k: v[0]["value"] for k, v in scalars.items() if len(v) == 1}
        if periods:
            fields["rent_schedule"] = periods
        methods = {k: v[0]["method"] for k, v in scalars.items() if len(v) == 1}
        if periods:
            methods["rent_schedule"] = (
                "ocr"
                if any(
                    f["source"]["method"] == "ocr" for p in periods for f in p.values()
                )
                else "text_layer"
            )
        predictions.append(
            {
                "case_id": case["id"],
                "fields": fields,
                "methods": methods,
                "route": "high" if disagreements else "normal",
            }
        )
    return predictions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path
    )
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--queue-capacity", type=int, required=True)
    parser.add_argument("--daily-documents", type=int, required=True)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text()) if args.corpus else build_corpus()
    predictions = (
        json.loads(args.predictions.read_text())
        if args.predictions
        else synthetic_predictions(corpus)
    )
    result = report(
        corpus,
        predictions,
        queue_capacity=args.queue_capacity,
        daily_documents=args.daily_documents,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["release_allowed"] else 1)
