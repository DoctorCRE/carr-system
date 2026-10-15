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

All historical observations were exposed during scorer development and belong
in train. The scorer is frozen in evidence/scorer-freeze.json before any new
held-out task is authored. Historical replay cannot establish a test seal.

CASES (see CASES below). Map prompts come from the regression fixtures of the
map gate (ops/map-architecture-gate-selftest.py and
ops/fixtures/real-replay/gate-scenarios.jsonl id d4a000000001); the task text
for store cases comes from ops/fixtures/real-replay/{user,agent}-prompts.jsonl
(flagged synthetic there) plus hand-written seeds. Production session
transcripts were NOT read.

  python3 evals/session-instructions/run_eval.py build  # quarantine exposed inputs
"""

import datetime
import hashlib
import json
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
FREEZE = HERE / "evidence" / "scorer-freeze.json"
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
    return {cid: "train" for cid, *_ in cases}


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


def verify_frozen_scorer():
    record = json.loads(FREEZE.read_text())
    if record["scorer_sha256"] != sha((HERE / "score.py").read_bytes()):
        raise ValueError("score.py does not match the freeze hash; fresh evaluation refused")
    return record


if __name__ == "__main__":
    {"build": build}[sys.argv[1]]()
