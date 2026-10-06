#!/usr/bin/env python3
"""make_report.py — produce receipt.json from observed cohorts, and the rounds report.

  make_report.py receipt --baseline-ref SHA [--session-ref ID]
      Replays every case through the working tree (candidate) and through the
      tree at SHA (baseline), both with THIS harness, writes the two raw
      observation cohorts under evidence/, and writes receipt.json with every
      measured field recomputed by run_eval.score_receipt: dimensions, case
      counts, oracle/null controls, and the evidence block that binds source,
      dependencies (every tracked file the candidate replay read), expectations
      and cohorts by sha256. Authored fields (change, verdict, notes, stage
      bindings, critical flags) carry over from the current receipt.json.
      ops/check-eval-receipt.py re-runs the same scorer and refuses any drift.

  make_report.py rounds [--builder PATH]
      The hillclimb rounds report: runs/_state.json, runs/results.md, and the
      claude-api skill's report.html when its lite builder path is given."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run_eval as R  # noqa: E402

SURFACE = "rule-delivery"
REL = f"evals/{SURFACE}"
RECEIPT = os.path.join(HERE, "receipt.json")
EVIDENCE = f"{REL}/evidence"
COHORTS = {arm: f"{EVIDENCE}/{arm}.jsonl" for arm in ("baseline", "candidate")}
SOURCE = (f"{REL}/run_eval.py", f"{REL}/make_report.py")
SCORER = {"path": f"{REL}/run_eval.py", "function": "score_receipt"}
KEPT = "kept"


def _gate():
    """ops/check-eval-receipt.py, which owns the receipt rules the producer must not restate."""
    spec = importlib.util.spec_from_file_location("check_eval_receipt", os.path.join(REPO, "ops", "check-eval-receipt.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def manifest(paths):
    return {path: sha(os.path.join(REPO, path)) for path in sorted(paths)}


def jsonl(rows):
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in sorted(rows, key=lambda r: r["case_id"]))


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ----------------------------------------------------------------- measured source

def tracked(paths):
    out = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=REPO, capture_output=True,
                         check=True).stdout.decode()
    return {p for p in out.split("\0") if p}



# ----------------------------------------------------------------- assembling

def assemble_receipt(template, expectations, baseline, candidate, source, deps, *,
                     measured_on, session_ref, baseline_evidence=None):
    """receipt.json from its evidence. Pure: the selftest reassembles the checked-in
    receipt from its own cohorts and requires the same bytes."""
    measured = R.score_receipt(expectations, baseline, candidate)
    cases = expectations["cases"]
    exp_rel = os.path.relpath(R.EXPECTATIONS, REPO)
    evidence = {
        "scorer": dict(SCORER),
        "source": source,
        "dependencies": deps,
        "expectations": {"path": exp_rel, "version": expectations["version"], "sha256": sha(R.EXPECTATIONS)},
        "cohorts": {arm: {"path": COHORTS[arm],
                          "sha256": hashlib.sha256(jsonl(rows).encode()).hexdigest()}
                    for arm, rows in (("baseline", baseline), ("candidate", candidate))},
    }
    evidence["baseline"] = baseline_evidence or template["evidence"]["baseline"]
    refs = [COHORTS["baseline"], COHORTS["candidate"]]
    authored = {d["dimension_id"]: d for d in template["dimensions"]}
    if set(authored) != set(measured["dimensions"]):
        raise SystemExit(f"refusing: the template names dimensions {sorted(authored)} but the scorer "
                         f"measures {sorted(measured['dimensions'])}")
    direction = _gate()._direction
    dimensions = [{"dimension_id": dim_id, "critical": authored[dim_id]["critical"],
                   "status": authored[dim_id]["status"], "direction_vs_baseline": direction(m["delta"]),
                   "evidence_refs": refs, "baseline": m["baseline"], "candidate": m["candidate"],
                   "delta": m["delta"]}
                  for dim_id, m in measured["dimensions"].items()]
    fingerprint = hashlib.sha256(json.dumps({"source": source, "dependencies": deps,
                                             "expectations": evidence["expectations"]["sha256"]},
                                            sort_keys=True).encode()).hexdigest()
    validation = dict(template["grader"]["validation"], **measured["controls"])
    return {
        "schema_version": 2,
        "surface": SURFACE,
        "change": template["change"],
        "measured_on": measured_on,
        "rung": template["rung"],
        "adapter": dict(template["adapter"], harness_version=source[SCORER["path"]],
                        configuration_fingerprint="sha256:" + fingerprint, native_session_ref=session_ref),
        "cases": {"total": len(cases),
                  "train": sum(1 for c in cases.values() if c["split"] == "train"),
                  "test": sum(1 for c in cases.values() if c["split"] == "test"),
                  "should_not_fire": sum(1 for c in cases.values() if c["should_not_fire"]),
                  "sources": template["cases"]["sources"]},
        "split": template["split"],
        "repeats": template["repeats"],
        "grader": dict(template["grader"], validation=validation),
        "noise_floor": template["noise_floor"],
        "min_useful_gain": template["min_useful_gain"],
        "primary_dimension": template["primary_dimension"],
        "dimensions": dimensions,
        "stage_results": [dict(st, evidence_refs=refs) for st in template["stage_results"]],
        "cost": template["cost"],
        "verdict": template["verdict"],
        "notes": template.get("notes", []),
        "evidence": evidence,
    }


def write_text(rel, text):
    with open(os.path.join(REPO, rel), "w", encoding="utf-8") as handle:
        handle.write(text)


def receipt(args):
    with open(RECEIPT, encoding="utf-8") as handle:
        template = json.load(handle)
    expectations = R.load_expectations()
    world = R.World(expectations)
    drift = R.expectation_drift(world, R.load_cases("all"), expectations)
    if drift:
        raise SystemExit(f"refusing: {len(drift)} case label(s) drifted from {R.EXPECTATIONS_VERSION} "
                         f"(first {drift[0]}); freeze a new expectations version before measuring")
    gate = _gate()
    ref = subprocess.run(["git", "rev-parse", args.baseline_ref + "^{commit}"], cwd=REPO,
                         check=True, capture_output=True, text=True).stdout.strip()
    fresh = gate.replay_rule_delivery(__import__("pathlib").Path(REPO), ref)
    candidate, baseline = fresh["candidate"]["rows"], fresh["baseline"]["rows"]
    # Every measured candidate input must have a durable repository home.
    deps = fresh["candidate"]["dependencies"]
    untracked = set(deps) - tracked(list(deps))
    if untracked:
        raise SystemExit(f"refusing: replay read untracked inputs: {sorted(untracked)}")
    doc = assemble_receipt(template, expectations, baseline, candidate, manifest(SOURCE), deps,
                           measured_on=datetime.date.today().isoformat(),
                           session_ref=args.session_ref or template["adapter"]["native_session_ref"],
                           baseline_evidence={"ref": ref, "dependencies": fresh["baseline"]["dependencies"]})
    os.makedirs(os.path.join(REPO, EVIDENCE), exist_ok=True)
    for arm, rows in (("baseline", baseline), ("candidate", candidate)):
        write_text(COHORTS[arm], jsonl(rows))
    write_text(os.path.relpath(RECEIPT, REPO), json.dumps(doc, indent=2, sort_keys=True) + "\n")
    for d in doc["dimensions"]:
        print(f"{d['dimension_id']:32s} {d['baseline']['score']:.4f} -> {d['candidate']['score']:.4f}  "
              f"delta {d['delta']['value']:+.4f} [{d['delta']['ci_low']:+.4f}, {d['delta']['ci_high']:+.4f}]  "
              f"{d['direction_vs_baseline']}")


# ----------------------------------------------------------------- rounds report

def ledger():
    rows = []
    path = os.path.join(R.RUNS, "ledger.jsonl")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    return rows


def state(best):
    with open(os.path.join(HERE, "split.json"), "r", encoding="utf-8") as handle:
        frozen = json.load(handle)
    train = sorted(i for i, s in frozen["split"].items() if s == "train")
    test = sorted(i for i, s in frozen["split"].items() if s == "test")
    doc = {"current_round": max([r["round"] for r in ledger()] or [0]), "reps": 1,
           "goal": {"target": "recall", "direction": "higher", "hold": ["false_deliveries"]},
           "best": {"round": best, "test_score": None},
           "train_ids": train, "test_ids": test,
           "metrics": [{"id": "recall", "kind": "float", "label": "Recall"},
                       {"id": "clean", "kind": "float", "label": "Quiet clean"},
                       {"id": "false_deliveries", "kind": "float", "label": "False deliv.",
                        "better": "lower"},
                       {"id": "tokens", "kind": "float", "label": "Tokens", "better": "lower"}],
           "perf_fields": []}
    with open(os.path.join(R.RUNS, "_state.json"), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=1)


def table(variants):
    lines = ["| variant | split | recall (micro) | recall (macro) | false deliveries / event | "
             "quiet cases with a false delivery | tokens / event | precision |",
             "|---|---|---|---|---|---|---|---|"]
    for name in variants:
        for split in ("train", "test"):
            s = R.summarize(R.load_run(name, split))
            lines.append(
                f"| {name} | {split} | {s['recall_micro']:.3f} | {s['recall_macro']:.3f} | "
                f"{s['false_per_event']:.3f} | {s['sn_dirty_cases']}/{s['should_not_fire_cases']} | "
                f"{s['tokens_per_event']:.0f} | {s['precision']:.3f} |")
    return "\n".join(lines)


def rounds(args):
    history = ledger()
    state(max([r["round"] for r in history if r["decision"] == KEPT] or [0]))
    if args.builder:
        subprocess.run(["node", args.builder, R.RUNS], check=True)
    variants = ["baseline"] + [f"v{r['round']}" for r in history]
    md = ["# Rule-delivery eval results", "", table(variants), "", "## Rounds", ""]
    for r in history:
        md.append(f"* v{r['round']} ({r['decision']}, goal {r['goal']}): {r['change']}. {r['gate']}")
    with open(os.path.join(R.RUNS, "results.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(md) + "\n")
    print(table(variants))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rc = sub.add_parser("receipt", help="measure baseline and candidate, write evidence/ and receipt.json")
    rc.add_argument("--baseline-ref", required=True, help="commit whose tree is the baseline arm")
    rc.add_argument("--session-ref", default=None, help="the measuring session, for adapter.native_session_ref")
    rr = sub.add_parser("rounds", help="hillclimb rounds report")
    rr.add_argument("--builder", default=None)
    args = parser.parse_args(argv)
    (receipt if args.command == "receipt" else rounds)(args)


if __name__ == "__main__":
    main()
