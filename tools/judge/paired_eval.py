"""Paired, provider-neutral runner over hash-bound, frozen real judge inputs.

Library entrypoints: freeze(receipts), run(corpus, jev, decisions), cli(argv).
CLI via python -c 'from tools.judge.paired_eval import cli; cli()'. Receipts
need request (state/questions/model), its digest and provenance. Digest-only
usage logs cannot be replayed. No synthetic reconstruction of missing inputs.
No promotion side effects: a report never changes the provider switch.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def _client():
    spec = importlib.util.spec_from_file_location("judge_eval_client", ROOT / "ops/typesafe_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offered_labels(question):
    """Canonical metric labels derived only from the requested answer domain."""
    kind = question.get("type")
    if kind == "noul":
        return {"true", "false"}
    if kind == "choice":
        return set(question.get("criteria", {}))
    if kind == "score":
        return {str(i) for i in range(len(question.get("criteria", [])))}
    return set()


def freeze(receipts):
    cases, seen = [], set()
    for receipt in receipts:
        request = receipt.get("request")
        if (not isinstance(request, dict) or set(request) != {"state", "questions", "model"}
                or not isinstance(request["questions"], dict) or not request["questions"]
                or not isinstance(request["model"], str) or not request["model"]):
            raise ValueError("receipt lacks complete frozen input state/questions/model")
        if receipt.get("work_class") != "system_work":
            raise ValueError("paired evaluation accepts system_work only; runtime is pinned")
        if receipt.get("request_sha256") != digest(request):
            raise ValueError("frozen input digest mismatch")
        for qid, gold in receipt.get("gold", {}).items():
            target = str(gold).lower() if isinstance(gold, bool) else str(gold)
            if target not in offered_labels(request["questions"].get(qid, {})):
                raise ValueError("gold label outside requested answer domain")
        rid = receipt.get("receipt_id")
        if not isinstance(rid, str) or not rid or rid in seen:
            raise ValueError("receipt id missing or duplicated")
        seen.add(rid)
        cases.append(copy.deepcopy(receipt))
    if not cases:
        raise ValueError("no frozen real inputs")
    return {"schema": "carr-judge-paired-corpus/v1", "cases": cases,
            "corpus_sha256": digest(cases)}


def percentile(values, q):
    return sorted(values)[max(0, math.ceil(q * len(values)) - 1)] if values else None


def evaluation_distribution(ts, question, answer):
    """Validate supplied mass before resolving aliases into the metric domain."""
    kind = question["type"]
    if kind in ("choice", "score"):
        raw = answer.get("probabilities")
        if not isinstance(raw, dict):
            raise ValueError("missing probability distribution")
        if any(type(value) not in (int, float) or not math.isfinite(value) or
               not 0 <= value <= 1 for value in raw.values()):
            raise ValueError("invalid supplied probability")
        if kind == "choice":
            if not set(raw) <= offered_labels(question):
                raise ValueError("probability support outside requested criteria")
        else:
            aliases = {}
            for index, label in enumerate(question["criteria"]):
                for key in (str(index), label):
                    aliases.setdefault(key, set()).add(str(index))
            canonical = {}
            for key, value in raw.items():
                targets = aliases.get(key, set())
                if len(targets) != 1:
                    raise ValueError("unknown or ambiguous Score probability key")
                target = next(iter(targets))
                if target in canonical:
                    raise ValueError("duplicate Score probability aliases")
                canonical[target] = value
            # The legacy formatter prefers text labels. Give it only canonical
            # labels so a supplied numeric alias can never be shadowed again.
            question = {**question, "criteria": [str(i) for i in range(len(question["criteria"]))]}
            answer = {**answer, "probabilities": canonical}
    return ts.answer_distribution(question, answer)


def run(corpus, jev, decisions, *, rates=None, repeats=1, clock=time.perf_counter):
    """Both adapters receive identical independent copies, in alternating order.

    Agreement is top-distribution agreement (Noul threshold 0.5). Calibration
    uses gold labels only: binary Brier for Noul, multiclass Brier otherwise,
    plus ten-bin expected calibration error. No gold means null, not accuracy.
    Cost requires supplied USD/million token rates for each named provider.
    Latency is measured afresh per successful adapter call, never historical.
    """
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if corpus.get("corpus_sha256") != digest(corpus.get("cases")):
        raise ValueError("corpus digest mismatch")
    freeze(corpus["cases"])
    ts = _client()
    rows = []
    aggregates = {name: {"latency": [], "tokens": [], "brier": [], "confidence": [],
                         "errors": 0, "cache_hits": 0, "models": set()} for name in ("jev", "decisions")}
    same = paired = total_questions = 0
    for repeat in range(repeats):
        for index, case in enumerate(corpus["cases"]):
            request = case["request"]
            answers = {}
            order = [("jev", jev), ("decisions", decisions)]
            if (repeat + index) % 2:
                order.reverse()
            for name, adapter in order:
                metrics = aggregates[name]
                started = clock()
                try:
                    result = adapter(copy.deepcopy(request["state"]), copy.deepcopy(request["questions"]), model=request["model"])
                    elapsed = max(0, (clock() - started) * 1000)
                    if not ts.usable_judgment(result, request["questions"]):
                        raise ValueError("invalid typed judgment or unmeasured usage")
                    distributions = {qid: evaluation_distribution(ts, q, result["answers"][qid])
                                     for qid, q in request["questions"].items()}
                    if not all(d["distribution_complete"] for d in distributions.values()):
                        raise ValueError("incomplete probability distribution")
                    if result.get("cache_hit"):
                        metrics["cache_hits"] += 1
                        metrics["models"].add(result["model"])
                        rows.append({"receipt_id": case["receipt_id"], "repeat": repeat, "provider": name,
                                     "status": "reused_observation", "model": result["model"],
                                     "cache_read_ms": elapsed, "usage": {"input_tokens": 0, "output_tokens": 0},
                                     "cached_observation": result.get("cached_observation") or {
                                         "usage": result["usage"], "latency_ms": result.get("latency_ms")},
                                     "answers": result["answers"]})
                        continue
                    briers, confidence = [], []
                    for qid, gold in case.get("gold", {}).items():
                        d = distributions[qid]
                        target = str(gold).lower() if isinstance(gold, bool) else str(gold)
                        if target not in d["distribution"]:
                            raise ValueError("gold label not in offered distribution")
                        if d["type"] == "noul":
                            brier = (d["distribution"]["true"] - (target == "true")) ** 2
                        else:
                            brier = sum((p - (label == target)) ** 2 for label, p in d["distribution"].items())
                        briers.append(brier)
                        confidence.append((d["top_probability"], d["top"] == target))
                    answers[name] = distributions
                    metrics["latency"].append(elapsed)
                    metrics["tokens"].append(result["usage"])
                    metrics["models"].add(result["model"])
                    metrics["brier"].extend(briers)
                    metrics["confidence"].extend(confidence)
                    rows.append({"receipt_id": case["receipt_id"], "repeat": repeat, "provider": name,
                                 "status": "ok", "model": result["model"], "latency_ms": elapsed,
                                 "usage": result["usage"], "answers": result["answers"]})
                except Exception as error:
                    metrics["errors"] += 1
                    # Provider exceptions can quote a request or key. Only the
                    # stub's constant is safe; never serialize arbitrary text.
                    reason = ("decisions contract not yet verified / no key" if
                              str(error) == "decisions contract not yet verified / no key" else type(error).__name__)
                    rows.append({"receipt_id": case["receipt_id"], "repeat": repeat,
                                 "provider": name, "status": "error", "reason": reason})
            if len(answers) == 2:
                paired += 1
                for qid in request["questions"]:
                    total_questions += 1
                    same += answers["jev"][qid]["top"] == answers["decisions"][qid]["top"]
    providers = {}
    for name, m in aggregates.items():
        confidences = m["confidence"]
        ece = None
        if confidences:
            ece = 0
            for bucket in range(10):
                group = [(p, correct) for p, correct in confidences if min(9, int(p * 10)) == bucket]
                if group:
                    ece += abs(sum(p for p, _ in group) - sum(c for _, c in group)) / len(confidences)
        rate = (rates or {}).get(name)
        cost = None
        if rate is not None:
            if (set(rate) != {"input_usd_per_million", "output_usd_per_million"} or
                    any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in rate.values())):
                raise ValueError("invalid explicit token prices")
            cost = sum(u["input_tokens"] * rate["input_usd_per_million"] +
                       u["output_tokens"] * rate["output_usd_per_million"] for u in m["tokens"]) / 1e6
        providers[name] = {"models": sorted(m["models"]), "successes": len(m["latency"]), "errors": m["errors"],
                           "cache_hits": m["cache_hits"],
                           "p50_latency_ms": percentile(m["latency"], .5), "p95_latency_ms": percentile(m["latency"], .95),
                           "brier": sum(m["brier"]) / len(m["brier"]) if m["brier"] else None,
                           "labelled_questions": len(m["brier"]), "ece": ece, "cost_usd": cost,
                           "usage": m["tokens"], "rates": rate}
    return {"schema": "carr-judge-paired-report/v1", "corpus_sha256": corpus["corpus_sha256"],
            "status": "complete" if paired == len(corpus["cases"]) * repeats else "incomplete",
            "paired_successes": paired, "paired_questions": total_questions,
            "agreement": same / total_questions if total_questions else None,
            "providers": providers, "rows": rows, "promotion": "not_performed"}


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Paired judge evaluation; never flips routing")
    parser.add_argument("receipts", help="JSON array of frozen, input-bearing real receipts")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rates", help="JSON provider token prices, supplied explicitly")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args(argv)
    ts = _client()
    def live_jev(state, questions, **options):
        if options.get("model") != "jev-1.13.0":
            raise ValueError("paired eval requires pinned jev-1.13.0 corpus")
        spec = importlib.util.spec_from_file_location("jev_semantic", ROOT / "ops" / "jev_semantic.py")
        semantic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(semantic)
        return semantic.ask(state, questions, transport=ts._ask_jev, caller="judge_paired_eval", version="paired-v1")
    report = run(freeze(json.loads(Path(args.receipts).read_text())), live_jev,
                 ts.JUDGE.provider_decisions, repeats=args.repeats,
                 rates=json.loads(Path(args.rates).read_text()) if args.rates else None)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    return report
