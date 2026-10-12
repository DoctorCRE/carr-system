"""gate-scorecard-selftest.py — the scorecard's verdicts follow its numbers.

Hermetic: a synthetic telemetry file, never out/. Each verdict rule has a
case that reaches it and a mutant-shaped neighbour that must not.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gate_scorecard", REPO / "tools/gate_scorecard.py")
assert spec and spec.loader
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

FAILS: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else f" — {detail}"))
    if not ok:
        FAILS.append(name)


NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)


def row(hook, event="Stop", register="silent", ms=50.0, exit_code=0, age_hours=1, session="s1", reopen=False,
        tool=None, tool_use_id=None):
    return {"ts": (NOW - timedelta(hours=age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": event, "hook": hook, "elapsed_ms": ms, "outcome": "deny" if register in ("block", "reopen") else "allow",
            "exit": exit_code, "register": register, "reopen": reopen, "session": session,
            "tool": tool, "tool_use_id": tool_use_id}


def write(rows) -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="scorecard-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        fh.write("not json\n")
        fh.write(json.dumps({"no_hook": True}) + "\n")
    return path


rows = []
# a gate that caught things: KEEP
rows += [row("conduct-stop-gate.py", register="block") for _ in range(3)]
rows += [row("conduct-stop-gate.py") for _ in range(40)]
# a dead gate: errors on every fire -> FIX
rows += [row("stale-claim-gate.py", register="error", exit_code=1) for _ in range(30)]
# noisy and never catches: DEMOTE by announces
rows += [row("needs-review-gate.py", event="PostToolUse", register="announce", ms=20.0) for _ in range(60)]
# slow and never catches: DEMOTE by seconds
rows += [row("slow-gate.py", event="PreToolUse", ms=2000.0) for _ in range(25)]
# cheap and quiet: KEEP
rows += [row("quiet-gate.py", event="PreToolUse", ms=10.0, session=f"s{i % 7}") for i in range(200)]
# too few fires: MEASURE
rows += [row("rare-gate.py", ms=10.0) for _ in range(5)]
# a reopen counts as a catch even when the register is odd
rows += [row("reopen-gate.py", register="odd", reopen=True) for _ in range(25)]
# old rows for the --days filter
rows += [row("ancient-gate.py", age_hours=24 * 40) for _ in range(30)]
# the per-call tax: three Bash calls, each hit by three PreToolUse hooks (10+20+30 ms)
# and one PostToolUse hook; a row with no tool_use_id never counts as a call
for call in ("c1", "c2", "c3"):
    rows += [row(h, event="PreToolUse", ms=ms, tool="Bash", tool_use_id=call)
             for h, ms in (("a-gate.py", 10.0), ("b-gate.py", 20.0), ("c-gate.py", 30.0))]
    rows += [row("d-gate.py", event="PostToolUse", ms=5.0, tool="Bash", tool_use_id=call)]
rows += [row("a-gate.py", event="PreToolUse", ms=999.0, tool="Bash")]
path = write(rows)

card = sc.build([path], days=None, now=NOW)
by = {(r["hook"], r["event"]): r for r in card["rows"]}

check("a gate with catches is KEEP", by[("conduct-stop-gate.py", "Stop")]["verdict"] == "KEEP")
check("catches are counted from block registers", by[("conduct-stop-gate.py", "Stop")]["catches"] == 3)
check("a gate that errors on its fires is FIX", by[("stale-claim-gate.py", "Stop")]["verdict"] == "FIX")
check("FIX names the error count", "30 of 30" in by[("stale-claim-gate.py", "Stop")]["why"])
check("zero catches with many advisory lines is DEMOTE",
      by[("needs-review-gate.py", "PostToolUse")]["verdict"] == "DEMOTE")
check("zero catches with real waiting is DEMOTE", by[("slow-gate.py", "PreToolUse")]["verdict"] == "DEMOTE")
check("DEMOTE says what it cost", "50s of waiting" in by[("slow-gate.py", "PreToolUse")]["why"])
check("cheap and quiet is KEEP", by[("quiet-gate.py", "PreToolUse")]["verdict"] == "KEEP")
check("sessions are counted distinct", by[("quiet-gate.py", "PreToolUse")]["sessions"] == 7)
check("too few fires is MEASURE", by[("rare-gate.py", "Stop")]["verdict"] == "MEASURE")
check("a reopen flag is a catch", by[("reopen-gate.py", "Stop")]["catches"] == 25)
check("FIX rows sort first, then DEMOTE", [r["verdict"] for r in card["rows"][:3]] == ["FIX", "DEMOTE", "DEMOTE"])
check("malformed and hookless lines are skipped", sum(r["fires"] for r in card["rows"]) == len(rows))

recent = sc.build([path], days=30, now=NOW)
check("--days drops rows older than the window",
      ("ancient-gate.py", "Stop") not in {(r["hook"], r["event"]) for r in recent["rows"]}
      and ("quiet-gate.py", "PreToolUse") in {(r["hook"], r["event"]) for r in recent["rows"]})

text = sc.render(card)
check("the render carries every verdict and the bound action",
      all(v in text for v in ("FIX", "DEMOTE", "KEEP", "MEASURE")) and "on DEMOTE:" in text and "on FIX:" in text)
check("the render says what is not measured", "context characters" in text)

tax = {(r["tool"], r["event"]): r for r in card["call_tax"]}
check("the per-call tax counts one call per tool_use_id", tax[("Bash", "PreToolUse")]["calls"] == 3)
check("the per-call tax counts hook processes per call", tax[("Bash", "PreToolUse")]["hooks_per_call"] == 3.0)
check("the per-call tax sums hook ms per call", tax[("Bash", "PreToolUse")]["ms_per_call"] == 60.0)
check("the post side is tallied separately", tax[("Bash", "PostToolUse")] == {
    "tool": "Bash", "event": "PostToolUse", "calls": 3, "hooks_per_call": 1.0, "ms_per_call": 5.0})
check("a row without a tool_use_id is not a call", sum(r["calls"] for r in card["call_tax"]) == 6)
check("the render carries the per-call tax and its bound action",
      "per tool call" in text and "3.0 procs" in text and "chain the rest into one process" in text)

# telemetry discovery: one stream per registered checkout, links to the same
# file counted once, explicit paths untouched
wt_a = tempfile.mkdtemp(prefix="wt-a-"); wt_b = tempfile.mkdtemp(prefix="wt-b-"); wt_c = tempfile.mkdtemp(prefix="wt-c-")
os.makedirs(os.path.join(wt_a, "out")); os.makedirs(os.path.join(wt_b, "out"))
open(os.path.join(wt_a, "out", "hook-telemetry.jsonl"), "w").close()
open(os.path.join(wt_b, "out", "hook-telemetry.jsonl"), "w").close()
open(os.path.join(wt_b, "out", "hook-telemetry.jsonl.1"), "w").close()
os.symlink(os.path.join(wt_a, "out"), os.path.join(wt_c, "out"))
_roots = sc._worktree_roots
sc._worktree_roots = lambda repo: [wt_a, wt_b, wt_c]
try:
    found = sc.telemetry_paths()
    check("every registered checkout's telemetry is read", len(found) == 3
          and any(p.startswith(wt_a) for p in found) and sum(p.startswith(wt_b) for p in found) == 2)
    check("a linked out/ is the same stream and is read once", not any(p.startswith(wt_c) for p in found))
    check("explicit paths bypass discovery", sc.telemetry_paths([path]) == [path])
finally:
    sc._worktree_roots = _roots

empty = sc.build([write([])], days=None, now=NOW)
check("no rows renders the meter-missing remedy, not a blank card", "on breach:" in sc.render(empty))

# the live command line works end to end and --json is parseable
import io, contextlib  # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = sc.main(["--json", "--telemetry", path])
check("main --json exits 0 with a parseable card", rc == 0 and json.loads(buf.getvalue())["schema"] == "gate-scorecard/v1")

if FAILS:
    print(f"gate-scorecard-selftest: {len(FAILS)} FAILED")
    sys.exit(1)
print("gate-scorecard-selftest: all checks passed")
