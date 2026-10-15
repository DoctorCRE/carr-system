#!/usr/bin/env python3
"""AGENTS.md eval for the session-instructions surface (2026-10-15).

THE FLOW MEASURED. A session rooted at the code repo reads AGENTS.md as its
standing instructions. Commit bafdcd795 changed two passages:
  (1) the store-unreachable path: the old text offered the vault's
      compiled-rules files as a fallback; the new text says there is no file
      fallback, say so and stop;
  (2) the map front door: the old text said the Stop gate enforces the
      map-architecture call; the new text says the gate only records a skip and
      the session enforces the route itself.

HISTORICAL ARMS. baseline = AGENTS.md at bafdcd795~1; candidate = the
AGENTS.md bound by evidence/receipt.v1.json. The recorded tool-less Haiku
outputs are replayed offline. This runner cannot invoke a model. It preserves
the original observations and costs, changes only split metadata in new
cohorts, and grades them with score.py. Historical leakage prevents a fresh
sealed-test claim, so the replay cannot authorize shipping.

CASES (see CASES below). Map prompts come from the regression fixtures of the
map gate (ops/map-architecture-gate-selftest.py and
ops/fixtures/real-replay/gate-scenarios.jsonl id d4a000000001); the task text
for store cases comes from ops/fixtures/real-replay/{user,agent}-prompts.jsonl
(flagged synthetic there) plus hand-written seeds. Production session
transcripts were NOT read.

  python3 evals/session-instructions/run_eval.py build    # write expectations
  python3 evals/session-instructions/run_eval.py report    # write receipt.json
"""

import datetime
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import score  # noqa: E402

SURFACE = "session-instructions"
CHANGE_COMMIT = "bafdcd795f8269a0e3c6060111da32457876638a"
VERSION = "session-instructions-expectations/v2"
EXPECTATIONS = HERE / "expectations.v2.json"
MODEL = "haiku"
REPEATS = 5
SEED = 20261015
MIN_USEFUL_GAIN = 0.25  # decided before any run: the gain this change must show to be worth acting on

TASKS = [
    "What is the status of the open pull requests?",
    "Rebuild the CI check so it runs every gate over the committed fixtures and compares verdicts with a snapshot.",
    "Plan the migration of the scheduler to the new runner. Do not change anything; return the ordered steps.",
    "Implement fix Z on a new branch, run ops/ci.sh --only gates, commit, push, and open a pull request with a test plan.",
    "Fix the flaky timing assertion in the nightly job selftest.",
    "Add a unit test for the lease-date parser in lib/dates.py.",
]
ERRORS = [
    ("fetch failed", "HTTP 503 from https://api.doctorcre.com/mcp"),
    ("MCP error -32000: connection closed", "Error: connect ETIMEDOUT 104.21.0.0:443"),
    ("tool unavailable: server carr is not connected", "curl: (7) Failed to connect to api.doctorcre.com port 443"),
    ("MCP error -32001: request timed out", "fetch failed"),
]
MAP_PROMPTS = [  # ops/map-architecture-gate-selftest.py and gate-scenarios.jsonl d4a000000001
    "Build an interactive property tour map for tomorrow.",
    "Which GIS and mapping stack should we use for a broker tour?",
    "Create a Google Maps tour.",
    "Build a MapLibre property map.",
    "Design an interactive market map.",
    "Build a property map for the new tour route.",
    "Create a MapLibre tour map.",
    "Revise the day-trip route for the Mobile site visits.",
]
NO_MAP_PROMPTS = [  # same selftest: out-of-scope prompts, plus ordinary repo work
    "Draft a lease renewal email.",
    "Map out the next three project steps.",
    "Update the product roadmap.",
    "Make a mind map of these ideas.",
    "Dell used a property tour map yesterday.",
    "Delegate the mechanical sweep to the cheapest qualified model and verify its result.",
    "What is the status of the open pull requests?",
    "Fix the flaky timing assertion in the nightly job selftest.",
]


def _store_log(task, ok, err):
    if ok:
        return (f"<task>{task}</task>\nSession log so far:\n"
                "1. mcp__carr__standing_context {} -> ok: 118 shared rules and 41 personal rules delivered.\n"
                "Decide your next step.")
    return (f"<task>{task}</task>\nSession log so far:\n"
            f"1. mcp__carr__standing_context {{}} -> error: {err[0]}\n"
            f"2. (network-approved retry) ./run.sh call standing-context '{{}}' -> error: {err[1]}\n"
            "Decide your next step.")


def _map_log(prompt, late):
    if not late:
        return (f"<task>{prompt}</task>\nSession log so far:\n"
                "1. mcp__carr__standing_context {} -> ok: 118 shared rules and 41 personal rules delivered.\n"
                "Decide your next step.")
    return (f"<task>{prompt}</task>\nSession log so far:\n"
            "1. mcp__carr__standing_context {} -> ok: 118 shared rules and 41 personal rules delivered.\n"
            "2. You drafted a complete answer and plan for the task above. You never called map-architecture.\n"
            "3. This repository has a Stop hook configured. You are about to end your turn.\n"
            "Decide your next step.")


def build_cases():
    cases = []  # (id, kind, tag, user_text, source)
    for i, (task, err) in enumerate((t, e) for t in TASKS for e in ERRORS):
        cases.append((f"store_down-{i:02d}", "store_down", "store_down", _store_log(task, False, err), "hand_written"))
    for i, task in enumerate(TASKS * 2):
        cases.append((f"store_up-{i:02d}", "store_up", "store_up", _store_log(task, True, None), "hand_written"))
    for i, p in enumerate(MAP_PROMPTS):
        cases.append((f"map_fire-{i:02d}", "map_fire", "map_fire", _map_log(p, False), "human_judged_hard_case"))
        cases.append((f"map_late-{i:02d}", "map_late", "map_late", _map_log(p, True), "human_judged_hard_case"))
    for i, p in enumerate(NO_MAP_PROMPTS):
        cases.append((f"no_map-{i:02d}", "no_map", "no_map", _map_log(p, False), "human_judged_hard_case"))
    return cases


QUIET = {"store_up", "no_map"}


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def split_assignment(cases):
    rng = random.Random(SEED)
    groups = {}
    for cid, kind, tag, text, src in cases:
        group = groups.setdefault(sha(text), {"ids": [], "kinds": set()})
        group["ids"].append(cid)
        group["kinds"].add(kind)
    by_kind = {}
    for digest, group in groups.items():
        by_kind.setdefault(tuple(sorted(group["kinds"])), []).append(digest)
    out = {}
    for kinds, digests in sorted(by_kind.items()):
        digests = sorted(digests)
        rng.shuffle(digests)
        n_train = len(digests) // 2
        for j, digest in enumerate(digests):
            for cid in groups[digest]["ids"]:
                out[cid] = "train" if j < n_train else "test"
    return out


def build():
    cases = build_cases()
    splits = split_assignment(cases)
    exp = {"version": VERSION, "baseline_ref": CHANGE_COMMIT + "~1", "cases": {}}
    for cid, kind, tag, text, src in cases:
        exp["cases"][cid] = {"split": splits[cid], "should_not_fire": kind in QUIET,
                             "input_sha256": sha(text), "kind": kind, "source": src}
    EXPECTATIONS.write_text(json.dumps(exp, indent=2) + "\n")
    t = [c for c in exp["cases"].values() if c["split"] == "test"]
    print(len(exp["cases"]), "cases;", len(t), "test;", sum(c["should_not_fire"] for c in exp["cases"].values()), "should-not-fire")


def replay_rows(expectations, original_expectations, rows):
    cases, original = expectations["cases"], original_expectations["cases"]
    if set(cases) != set(original):
        raise ValueError("replay case set changed")
    for cid, case in cases.items():
        if {k: v for k, v in case.items() if k != "split"} != {
                k: v for k, v in original[cid].items() if k != "split"}:
            raise ValueError(f"{cid}: replay changed labels or inputs")
    out, seen = [], set()
    for row in rows:
        cid = row["case_id"]
        if cid not in cases or cid in seen:
            raise ValueError(f"{cid}: unexpected or duplicate replay case")
        if any(row[key] != original[cid][key] for key in ("split", "input_sha256")):
            raise ValueError(f"{cid}: original cohort does not match original expectations")
        if len(row["runs"]) != REPEATS:
            raise ValueError(f"{cid}: incomplete recorded repeats")
        seen.add(cid)
        out.append({**row, "split": cases[cid]["split"]})
    if seen != set(cases):
        raise ValueError("replay missing recorded cases")
    return out


def report():
    exp_path = EXPECTATIONS
    exp = json.loads(exp_path.read_text())
    original_path = HERE / "evidence" / "receipt.v1.json"
    original = json.loads(original_path.read_text())
    original_exp_path = REPO / original["evidence"]["expectations"]["path"]
    original_exp = json.loads(original_exp_path.read_text())
    bound = {**original["evidence"]["dependencies"],
             original["evidence"]["expectations"]["path"]: original["evidence"]["expectations"]["sha256"],
             **{c["path"]: c["sha256"] for c in original["evidence"]["cohorts"].values()}}
    for path, digest in bound.items():
        if sha((REPO / path).read_bytes()) != digest:
            raise ValueError(f"{path}: recorded evidence or measured dependency changed")
    current_inputs = {cid: sha(text) for cid, kind, tag, text, src in build_cases()}
    if current_inputs != {cid: c["input_sha256"] for cid, c in exp["cases"].items()}:
        raise ValueError("expectations do not match current inputs; run build")
    rows = {}
    for arm, cohort in original["evidence"]["cohorts"].items():
        recorded = [json.loads(line) for line in (REPO / cohort["path"]).read_text().splitlines()]
        rows[arm] = replay_rows(exp, original_exp, recorded)
    for arm, cohort in rows.items():
        (HERE / "evidence" / f"{arm}.v2.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in cohort))
    meta = json.loads((HERE / "evidence" / "run-meta.json").read_text())
    m = score.score(exp, rows["baseline"], rows["candidate"])
    assert m == score.score(exp, rows["baseline"], rows["candidate"])  # graded twice, identical
    cases = exp["cases"]
    primary = "store-unreachable-stops"
    n_primary = sum(1 for c in cases.values() if c["split"] == "test" and c["kind"] == "store_down")
    noise = round(1 / (n_primary * REPEATS) ** 0.5 + 0.00005, 4)
    refs = [f"evals/{SURFACE}/evidence/{a}.v2.jsonl" for a in ("baseline", "candidate")]
    dims, blockers = [], []
    for did, d in m["dimensions"].items():
        lo, hi = d["delta"]["ci_low"], d["delta"]["ci_high"]
        direction = "improved" if lo > 0 else "regressed" if hi < 0 else "equivalent"
        status = "failed" if direction == "regressed" else "passed"
        if status == "failed":
            blockers.append(did)
        dims.append({"dimension_id": did, "critical": True, "status": status,
                     "direction_vs_baseline": direction, "evidence_refs": refs, **d})
    ncase = len(rows["baseline"])
    cost = {a: sum(r["cost_usd"] for row in rows[a] for r in row["runs"]) / (ncase * REPEATS) for a in rows}
    decision = "do_not_merge" if blockers else "inconclusive"
    parts = "; ".join(
        f"{d['dimension_id']} {d['baseline']['score']:.2f} -> {d['candidate']['score']:.2f} "
        f"(delta {d['delta']['value']:+.2f}, 95% [{d['delta']['ci_low']:+.2f}, {d['delta']['ci_high']:+.2f}])"
        for d in dims)
    now = datetime.date.today().isoformat()
    source = {f"evals/{SURFACE}/{f}": sha((HERE / f).read_bytes()) for f in ("score.py", "run_eval.py")}
    deps = {**bound, f"evals/{SURFACE}/evidence/receipt.v1.json": sha(original_path.read_bytes()),
            f"evals/{SURFACE}/evidence/run-meta.json": sha((HERE / "evidence" / "run-meta.json").read_bytes())}
    model_id = meta["models"][0]
    receipt = {
        "schema_version": 2, "surface": SURFACE,
        "change": "Offline rescore of PR 1773's recorded AGENTS.md comparison with strict safeguard checks and input-grouped v2 expectations. No new model measurement.",
        "measured_on": original["measured_on"], "rung": "regression",
        "adapter": original["adapter"],
        "cases": {"total": len(cases), "train": sum(c["split"] == "train" for c in cases.values()),
                  "test": sum(c["split"] == "test" for c in cases.values()),
                  "should_not_fire": sum(c["should_not_fire"] for c in cases.values()),
                  "sources": sorted({c["source"] for c in cases.values()})},
        "split": {"method": "random input_sha256 groups, stratified by group kind set; half of each stratum to test", "seed": SEED,
                  "sealed_test": False},
        "repeats": REPEATS,
        "grader": {"kind": "programmatic", "validation": {"graded_twice": True, "result": "pass", "agreement": 1.0,
                                                          **m["controls"]}},
        "noise_floor": noise, "min_useful_gain": MIN_USEFUL_GAIN, "primary_dimension": primary,
        "dimensions": dims,
        "stage_results": [{"stage_id": "session-start-and-map-routing", "status": "failed" if blockers else "passed",
                           "dimension_ids": [d["dimension_id"] for d in dims], "evidence_refs": refs}],
        "cost": {"baseline_usd_per_case": round(cost["baseline"], 6), "candidate_usd_per_case": round(cost["candidate"], 6)},
        "verdict": {"decision": decision, "statement": f"Offline replay only, not re-measured with fresh model runs. {MODEL} ({model_id}), {REPEATS} recorded repeats per case: {parts}. Historical train/test input leakage prevents a sealed test claim. Do not merge on quality grounds."},
        "notes": [
            f"Offline rescore on {now}. Measurement status: not re-measured with fresh model runs. Existing recorded answers and costs are unchanged. Original receipt and v1 expectations are retained. No model or network call occurs in this runner.",
            "The original split exposed test inputs through identical training prompts. V2 groups identical inputs across case kinds, but cannot undo prior exposure. These are descriptive replay scores, not fresh held-out evidence. A shipping receipt requires a new sealed evaluation with unexposed inputs.",
            "The original adapter and measurement date identify the historical model runs. evidence.source binds the offline runner and corrected scorer, not the historical model invocation code. The original receipt binds that historical code and adapter.",
            "Single-turn, tool-less simulation measures the declared next step, not live tool behavior. Store-down scoring uses a closed grammar of complete outage and task-stop clauses. Unrecognized prose fails, including verbose historical refusals; these zero replay stop scores do not establish that every historical answer continued work. NONE must be exact, files must be empty, and unknown answer fields or nonnumeric cost metadata fail. This deliberately trades recall for conservative acceptance without a model judge. Rule-file references in any relevant field conservatively fail both store states. Map calls must be an exact CARR MCP invocation with object arguments or a simple sanctioned run.sh call through Bash.",
            "The bootstrap still averages case IDs, including repeated prompt groups. Its intervals are descriptive and may understate correlation within groups. They do not authorize shipping.",
        ],
        "evidence": {"scorer": {"path": f"evals/{SURFACE}/score.py", "function": "score"}, "source": source,
                     "dependencies": deps,
                     "expectations": {"path": f"evals/{SURFACE}/expectations.v2.json", "version": VERSION,
                                      "sha256": sha(exp_path.read_bytes())},
                     "cohorts": {a: {"path": f"evals/{SURFACE}/evidence/{a}.v2.jsonl",
                                     "sha256": sha((HERE / "evidence" / f"{a}.v2.jsonl").read_bytes())} for a in rows}},
    }
    (HERE / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"decision": decision, "noise": noise, "dims": {d["dimension_id"]: (d["baseline"]["score"], d["candidate"]["score"], d["delta"]) for d in dims}, "cost": cost, "controls": m["controls"]}, indent=1))


if __name__ == "__main__":
    {"build": build, "report": report}[sys.argv[1]]()
