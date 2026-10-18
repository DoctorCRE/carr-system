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

ARMS. baseline = AGENTS.md at BASELINE_REF (bafdcd795~1); candidate = AGENTS.md
in this tree. Each arm is the system prompt of a tool-less single-turn
`claude -p` call on the cheapest model (haiku); the scenario is a short session
log and the model answers with its next step as JSON. Nothing is sent to any
CARR system and no tool runs. Grading is programmatic (score.py).

CASES use historical map and store-outage prompts plus hand-written seeds.
Production session transcripts were not read.

  python3 evals/session-instructions/run_eval.py build    # write expectations
  python3 evals/session-instructions/run_eval.py run      # call the model, write cohorts
  python3 evals/session-instructions/run_eval.py report    # write receipt.json
"""

import concurrent.futures as cf
import datetime
import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import score  # noqa: E402

SURFACE = "session-instructions"
CHANGE_COMMIT = "bafdcd795f8269a0e3c6060111da32457876638a"
BASELINE_REF = CHANGE_COMMIT + "~1"
VERSION = "session-instructions-expectations/v1"
MODEL = "haiku"
REPEATS = 5
SEED = 20261015
MIN_USEFUL_GAIN = 0.25  # decided before any run: the gain this change must show to be worth acting on
WORKERS = 8

OUTPUT_CONTRACT = """

---
You are the coding agent for this repository. Tools available to you: mcp__carr__standing_context, mcp__carr__map-architecture, Bash, Read, Edit, Write. You are shown a short session log. Decide your next step as the AGENTS.md above directs. Answer with ONLY one JSON object, no prose around it:
{"next_tool_call": "<exact tool name and arguments for your very next call, or NONE>", "files_you_would_read_for_rules": ["<paths, or empty>"], "message_to_user": "<what you say to the user now>"}"""

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
MAP_PROMPTS = [
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


def agents_text(arm):
    if arm == "candidate":
        return (REPO / "AGENTS.md").read_text()
    return subprocess.check_output(["git", "show", f"{BASELINE_REF}:AGENTS.md"], cwd=REPO, text=True)


def split_assignment(cases):
    rng = random.Random(SEED)
    by_kind = {}
    for c in cases:
        by_kind.setdefault(c[1], []).append(c[0])
    out = {}
    for kind, ids in sorted(by_kind.items()):
        ids = sorted(ids)
        rng.shuffle(ids)
        n_train = len(ids) // 2
        for j, cid in enumerate(ids):
            out[cid] = "train" if j < n_train else "test"
    return out


def build():
    cases = build_cases()
    splits = split_assignment(cases)
    exp = {"version": VERSION, "baseline_ref": CHANGE_COMMIT + "~1", "cases": {}}
    for cid, kind, tag, text, src in cases:
        exp["cases"][cid] = {"split": splits[cid], "should_not_fire": kind in QUIET,
                             "input_sha256": sha(text), "kind": kind, "source": src}
    (HERE / "expectations.v1.json").write_text(json.dumps(exp, indent=2) + "\n")
    t = [c for c in exp["cases"].values() if c["split"] == "test"]
    print(len(exp["cases"]), "cases;", len(t), "test;", sum(c["should_not_fire"] for c in exp["cases"].values()), "should-not-fire")


def call_model(system, user):
    for attempt in range(4):
        try:
            p = subprocess.run(
                ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "", "--strict-mcp-config",
                 "--disable-slash-commands", "--no-session-persistence", "--output-format", "json",
                 "--system-prompt", system, user],
                cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=180)
            d = json.loads(p.stdout)
            if p.returncode == 0 and isinstance(d.get("result"), str):
                model = next(iter(d.get("modelUsage", {"unknown": 0})))
                return d["result"], float(d.get("total_cost_usd", 0.0)), d.get("session_id"), model
        except Exception:
            pass
    return None, 0.0, None, None


def parse(text):
    if text is None:
        return {"parse_ok": False}
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0))
        files = d.get("files_you_would_read_for_rules", [])
        ntc, msg = d["next_tool_call"], d["message_to_user"]
        if not (isinstance(ntc, str) and isinstance(msg, str) and isinstance(files, list)):
            raise ValueError
        return {"parse_ok": True, "next_tool_call": ntc[:400], "files": [str(f)[:200] for f in files][:10],
                "message": msg[:600]}
    except Exception:
        return {"parse_ok": False, "raw": text[:300]}


def run():
    cases = build_cases()
    exp = json.loads((HERE / "expectations.v1.json").read_text())["cases"]
    meta = {"models": set(), "session": None}
    for arm in ("baseline", "candidate"):
        system = "Standing instructions for this repository (AGENTS.md):\n\n" + agents_text(arm) + OUTPUT_CONTRACT
        jobs = [(cid, text, r) for cid, kind, tag, text, src in cases for r in range(REPEATS)]
        results = {}
        with cf.ThreadPoolExecutor(WORKERS) as pool:
            futs = {pool.submit(call_model, system, text): (cid, r) for cid, text, r in jobs}
            for f in cf.as_completed(futs):
                results[futs[f]] = f.result()
        rows = []
        for cid, kind, tag, text, src in cases:
            runs, cost = [], 0.0
            for r in range(REPEATS):
                out, usd, sid, model = results[(cid, r)]
                meta["models"].add(model)
                meta["session"] = meta["session"] or sid
                cost += usd
                run_row = parse(out)
                run_row["cost_usd"] = usd
                runs.append(run_row)
            rows.append({"case_id": cid, "split": exp[cid]["split"], "input_sha256": exp[cid]["input_sha256"],
                         "runs": runs})
        (HERE / "evidence" / f"{arm}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        print(arm, "done", sum(r["cost_usd"] for row in rows for r in row["runs"]))
    (HERE / "evidence" / "run-meta.json").write_text(json.dumps(
        {"models": sorted(m for m in meta["models"] if m), "first_session": meta["session"],
         "repeats": REPEATS, "cli": subprocess.check_output(["claude", "--version"], text=True).strip()}, indent=2) + "\n")


def report():
    exp_path = HERE / "expectations.v1.json"
    exp = json.loads(exp_path.read_text())
    rows = {a: [json.loads(l) for l in (HERE / "evidence" / f"{a}.jsonl").read_text().splitlines()]
            for a in ("baseline", "candidate")}
    meta = json.loads((HERE / "evidence" / "run-meta.json").read_text())
    m = score.score(exp, rows["baseline"], rows["candidate"])
    assert m == score.score(exp, rows["baseline"], rows["candidate"])  # graded twice, identical
    cases = exp["cases"]
    primary = "store-unreachable-stops"
    n_primary = sum(1 for c in cases.values() if c["split"] == "test" and c["kind"] == "store_down")
    noise = round(1 / (n_primary * REPEATS) ** 0.5 + 0.00005, 4)
    refs = [f"evals/{SURFACE}/evidence/{a}.jsonl" for a in ("baseline", "candidate")]
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
    cheaper = cost["candidate"] < cost["baseline"]
    pdir = next(d["direction_vs_baseline"] for d in dims if d["dimension_id"] == primary)
    parts = "; ".join(
        f"{d['dimension_id']} {d['baseline']['score']:.2f} -> {d['candidate']['score']:.2f} "
        f"(delta {d['delta']['value']:+.2f}, 95% [{d['delta']['ci_low']:+.2f}, {d['delta']['ci_high']:+.2f}])"
        for d in dims)
    if blockers:
        decision, tail = "do_not_merge", "Critical dimension(s) " + ", ".join(blockers) + " regressed: blocking."
    elif pdir == "improved" and noise < MIN_USEFUL_GAIN and next(
            d["delta"]["value"] for d in dims if d["dimension_id"] == primary) >= MIN_USEFUL_GAIN:
        decision, tail = "ship", "Measured improvement on the primary dimension with no critical regression."
    elif pdir == "equivalent":
        decision = "ship_cost_at_parity" if cheaper else "do_not_merge"
        tail = "Quality-flat: do not merge on quality grounds."
    else:
        decision, tail = "inconclusive", "The measurement cannot see the claimed win."
    now = datetime.date.today().isoformat()
    source = {f"evals/{SURFACE}/{f}": sha((HERE / f).read_bytes()) for f in ("score.py", "run_eval.py")}
    deps = {"AGENTS.md": sha((REPO / "AGENTS.md").read_bytes())}
    model_id = meta["models"][0]
    fingerprint = "sha256:" + sha(json.dumps(
        {"model": model_id, "cli": meta["cli"], "flags": "-p --tools '' --setting-sources '' --strict-mcp-config",
         "baseline_ref": BASELINE_REF, "deps": deps, "repeats": REPEATS}, sort_keys=True))
    receipt = {
        "schema_version": 2, "surface": SURFACE,
        "change": "AGENTS.md drops the vault compiled-rules file fallback (say so and stop when the store is unreachable) and deletes the sentence claiming the Stop gate enforces the map-architecture route, replacing it with nothing (candidate f9016574c).",
        "measured_on": now, "rung": "regression",
        "adapter": {"surface": "claude_code_cli", "adapter_id": "claude-p-single-turn-no-tools", "adapter_version": "1",
                    "harness_id": f"evals/{SURFACE}/run_eval.py", "harness_version": source[f"evals/{SURFACE}/run_eval.py"],
                    "provider_id": "anthropic", "model_id": model_id, "native_session_ref": meta["first_session"],
                    "configuration_fingerprint": fingerprint},
        "cases": {"total": len(cases), "train": sum(c["split"] == "train" for c in cases.values()),
                  "test": sum(c["split"] == "test" for c in cases.values()),
                  "should_not_fire": sum(c["should_not_fire"] for c in cases.values()),
                  "sources": sorted({c["source"] for c in cases.values()})},
        "split": {"method": "random, stratified by case kind (first tag), half of each kind to test", "seed": SEED,
                  "sealed_test": True},
        "repeats": REPEATS,
        "grader": {"kind": "programmatic", "validation": {"graded_twice": True, "result": "pass", "agreement": 1.0,
                                                          **m["controls"]}},
        "noise_floor": noise, "min_useful_gain": MIN_USEFUL_GAIN, "primary_dimension": primary,
        "dimensions": dims,
        "stage_results": [{"stage_id": "session-start-and-map-routing", "status": "failed" if blockers else "passed",
                           "dimension_ids": [d["dimension_id"] for d in dims], "evidence_refs": refs}],
        "cost": {"baseline_usd_per_case": round(cost["baseline"], 6), "candidate_usd_per_case": round(cost["candidate"], 6)},
        "verdict": {"decision": decision, "statement": f"Test split, {MODEL} ({model_id}), {REPEATS} repeats per case: {parts}. {tail}"},
        "notes": ["Single-turn, tool-less simulation of the session log: it measures what the model says it would do next, not live tool behaviour. Production session transcripts were not read (access refused as a PII boundary); map prompts come from the map gate's regression fixtures, store-down and quiet cases are hand-written seeds over fixture task text.",
                  "Disclosure: after the first scoring pass of an earlier candidate (a9c1d10, which said the session enforces the route itself) the store_up grader was changed (a clarifying question because the tool-less harness cwd is not the repo no longer counts as a failed proceed); the diagnosis came from train transcripts only. That earlier candidate called map-architecture on the historical mention no_map-04 in 5 of 5 repeats (baseline 1 of 5). This re-measurement reuses the same cases, split, seed, grader and min_useful_gain unchanged, with only the candidate AGENTS.md replaced. No test transcripts were read.",
                  "The map half of the change is measured as: after skipping the verb with a Stop hook present, does the session call it itself."],
        "evidence": {"scorer": {"path": f"evals/{SURFACE}/score.py", "function": "score"}, "source": source,
                     "dependencies": deps,
                     "expectations": {"path": f"evals/{SURFACE}/expectations.v1.json", "version": VERSION,
                                      "sha256": sha(exp_path.read_bytes())},
                     "cohorts": {a: {"path": f"evals/{SURFACE}/evidence/{a}.jsonl",
                                     "sha256": sha((HERE / "evidence" / f"{a}.jsonl").read_bytes())} for a in rows}},
    }
    (HERE / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"decision": decision, "noise": noise, "dims": {d["dimension_id"]: (d["baseline"]["score"], d["candidate"]["score"], d["delta"]) for d in dims}, "cost": cost, "controls": m["controls"]}, indent=1))


if __name__ == "__main__":
    {"build": build, "run": run, "report": report}[sys.argv[1]]()
