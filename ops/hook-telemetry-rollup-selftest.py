#!/usr/bin/env python3
"""hook-telemetry-rollup-selftest.py — acceptance test for ops/hook-telemetry-rollup.py.

THE CHECK THAT MATTERS MOST is the one about what the rollup must NOT say. Its
retire-candidate rule can end with a gate being deleted, so a false positive
here is a control removed on the strength of a number. The council's rule has
three clauses — zero live denies in 7 days, catch-class owned by pre-commit or
ops/ci.sh, and on the hot path — and cases 3 through 6 below take a gate that
satisfies two of the three and require that it is NOT reported. In particular a
gate whose owner is still "unverified" is never a candidate, because unverified
means nobody has checked, not that nobody is watching.

The rest is arithmetic with a purpose: percentiles computed by nearest rank so a
handful of firings does not silently become a p95 of nothing; live-only reading,
so fixture traffic can never re-enter the number; reopens attributed per gate
per day, because a reopen spends tokens and the question is which gate spends
them; and a torn last line — which a log being appended to by 13 concurrent
processes will eventually have — skipped rather than fatal.

The rollup is driven as a subprocess against a fixture repo, because it resolves
its paths at import.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLLUP = os.path.join(REPO, "ops", "hook-telemetry-rollup.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def stamp(days_ago, hour=12):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hour)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ"), when.strftime("%Y-%m-%d")


def record(hook, days_ago, elapsed, outcome="allow", event="PreToolUse",
           reopen=False, meter=0.8, arg=None, deny_class=None, register=None):
    ts, _ = stamp(days_ago)
    if register is None:
        register = "reopen" if reopen else ("block" if outcome == "deny" else "silent")
    return {
        "ts": ts, "event": event, "hook": hook, "arg": arg, "tool": "Bash",
        "session": "s", "elapsed_ms": elapsed, "outcome": outcome, "exit":
        2 if outcome == "deny" else 0, "reopen": reopen, "deny_class": deny_class,
        "deny_headline": None, "pid": 1, "meter_ms": meter, "source": "live",
        "register": register,
    }


WIRING = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/hot-with-owner.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/hot-unverified.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/hot-with-denies.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/run-record-gate.py drift-claim-gate.py"},
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/never-fires.py"},
        ]},
    ],
    "SessionStart": [
        {"matcher": None, "hooks": [
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/cold-with-owner.py"},
        ]},
    ],
    "Stop": [
        {"matcher": None, "hooks": [
            {"type": "command",
             "command": "/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
                        "{{REPO}}/hooks/stopper.py"},
        ]},
    ],
}

CATCH = {"hooks": {
    "hot-with-owner.py": {"catch_class": "a class CI also owns",
                          "also_caught_by": "ops-ci:gates",
                          "note": "prevention here, detection there"},
    "hot-unverified.py": {"catch_class": "nobody has checked this one",
                          "also_caught_by": "unverified"},
    "hot-with-denies.py": {"catch_class": "still catching things",
                           "also_caught_by": "pre-commit:path-hygiene-check.py"},
    "cold-with-owner.py": {"catch_class": "owned, but not on the hot path",
                           "also_caught_by": "ops-ci:gates"},
    "stopper.py": {"catch_class": "turn-end discipline", "also_caught_by": "none-known"},
}}


def build_fixture(tmp, rows, unclassified=0, fixture_lines=0, torn=False):
    out = os.path.join(tmp, "out")
    os.makedirs(os.path.join(out, "fixtures"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "ops", "config"), exist_ok=True)
    with open(os.path.join(out, "hook-telemetry.jsonl"), "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
        if torn:
            fh.write('{"ts":"2026-08-2')          # a half-written concurrent append
    with open(os.path.join(out, "hook-telemetry-unclassified.jsonl"), "w",
              encoding="utf-8") as fh:
        for _ in range(unclassified):
            fh.write(json.dumps(record("stray.py", 1, 10)) + "\n")
    with open(os.path.join(out, "fixtures", "hook-telemetry-fixture.jsonl"), "w",
              encoding="utf-8") as fh:
        for _ in range(fixture_lines):
            fh.write(json.dumps(record("hot-with-denies.py", 1, 10, "deny")) + "\n")
    with open(os.path.join(tmp, "ops", "config", "hooks.json"), "w", encoding="utf-8") as fh:
        json.dump(WIRING, fh)
    with open(os.path.join(tmp, "ops", "config", "hook-catch-classes.json"), "w",
              encoding="utf-8") as fh:
        json.dump(CATCH, fh)
    return tmp


def run_rollup(tmp, *args):
    env = dict(os.environ)
    env["CARR_ROLLUP_REPO"] = tmp
    proc = subprocess.run([sys.executable, ROLLUP] + list(args),
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def main():
    print("hook-telemetry-rollup-selftest")
    tmp = tempfile.mkdtemp(prefix="rollup-selftest-")
    try:
        rows = []
        # a hot gate with a verified owner and no denies — the ONE candidate
        for day in range(7):
            rows += [record("hot-with-owner.py", day, 10 + day) for _ in range(5)]
        # same, but nobody has verified who else catches it
        for day in range(7):
            rows += [record("hot-unverified.py", day, 12) for _ in range(5)]
        # hot, owned elsewhere, but still catching things this week
        for day in range(7):
            rows += [record("hot-with-denies.py", day, 15) for _ in range(4)]
        rows.append(record("hot-with-denies.py", 2, 15, "deny", deny_class="vault-write"))
        # owned and silent, but SessionStart — not the hot path
        rows += [record("cold-with-owner.py", day, 900, event="SessionStart")
                 for day in range(7)]
        # a Stop gate that reopened turns on two different days
        rows += [record("stopper.py", 1, 40, "deny", event="Stop", reopen=True)
                 for _ in range(3)]
        rows.append(record("stopper.py", 3, 40, "deny", event="Stop", reopen=True))
        rows += [record("stopper.py", day, 30, event="Stop") for day in range(7)]
        # a Stop event well over the provisional 300ms budget, so --strict has
        # something real to fail on rather than being asserted against nothing
        rows += [record("stopper.py", 0, 2500, event="Stop") for _ in range(10)]
        # the twice-wired gate, distinguished only by its argument
        rows += [record("run-record-gate.py", 1, 20, arg="drift-claim-gate.py")
                 for _ in range(3)]
        rows += [record("run-record-gate.py", 1, 20, arg="drift-assertion-gate.py")
                 for _ in range(2)]
        # outside the window entirely
        rows += [record("hot-with-owner.py", 30, 5000, "deny") for _ in range(50)]
        # a latency spike on the most recent day, for the trend column
        rows += [record("hot-with-owner.py", 0, 400) for _ in range(3)]

        build_fixture(tmp, rows, unclassified=4, fixture_lines=9, torn=True)

        rc, out, err = run_rollup(tmp, "--json")
        check("the rollup runs against a fixture repo", rc == 0, err[:300])
        report = json.loads(out)
        hooks = report["hooks"]

        check("a torn final line is skipped, not fatal", rc == 0, err[:200])
        check("records older than the window are excluded",
              hooks["hot-with-owner.py"]["denies"] == 0,
              hooks["hot-with-owner.py"]["denies"])
        check("the 50 out-of-window records are not counted",
              hooks["hot-with-owner.py"]["fires"] == 38,
              hooks["hot-with-owner.py"]["fires"])

        check("fixture traffic never enters the live numbers",
              hooks["hot-with-denies.py"]["denies"] == 1,
              f"{hooks['hot-with-denies.py']['denies']} (9 fixture denies exist)")
        check("the fixture stream is still counted, so its volume is visible",
              report["streams"]["fixture"] == 9, report["streams"]["fixture"])
        check("unclassified traffic is reported rather than averaged in",
              report["streams"]["unclassified"] == 4, report["streams"]["unclassified"])

        check("a deny class is carried through to the rollup",
              hooks["hot-with-denies.py"]["deny_classes"].get("vault-write") == 1,
              hooks["hot-with-denies.py"]["deny_classes"])

        # ── the retire rule, and mostly what it must refuse to say ──
        check("zero denies + verified owner + hot path IS a candidate",
              hooks["hot-with-owner.py"]["retire_candidate"] is True)
        check("zero denies + UNVERIFIED owner is NOT a candidate",
              hooks["hot-unverified.py"]["retire_candidate"] is False,
              hooks["hot-unverified.py"])
        check("a gate that still denies is NOT a candidate",
              hooks["hot-with-denies.py"]["retire_candidate"] is False,
              hooks["hot-with-denies.py"])
        check("an owned, silent gate OFF the hot path is NOT a candidate",
              hooks["cold-with-owner.py"]["retire_candidate"] is False,
              hooks["cold-with-owner.py"])
        check("hot-path membership is derived from the wiring, not declared",
              hooks["hot-with-owner.py"]["hot_path"] is True
              and hooks["cold-with-owner.py"]["hot_path"] is False)
        check("the unverified-owner gap is counted so it cannot be mistaken for clean",
              report["unverified_owner_count"] >= 1, report["unverified_owner_count"])

        # ── reopens, per gate per day ──
        reopens = report["reopens_by_day"]
        _, day1 = stamp(1)
        _, day3 = stamp(3)
        check("reopens are attributed to the day they happened",
              reopens.get(day1, {}).get("stopper.py") == 3
              and reopens.get(day3, {}).get("stopper.py") == 1, reopens)
        check("a gate's reopen total is separate from its deny total",
              hooks["stopper.py"]["reopens"] == 4 and hooks["stopper.py"]["denies"] == 4,
              hooks["stopper.py"])

        # ── percentiles and the trend column ──
        check("p95 is nearest-rank, not an average",
              hooks["hot-unverified.py"]["p95_ms"] == 12,
              hooks["hot-unverified.py"]["p95_ms"])
        check("a latency spike on the last day shows in the 7-day trend",
              (hooks["hot-with-owner.py"]["p95_trend_pct"] or 0) > 100,
              hooks["hot-with-owner.py"]["p95_trend_pct"])
        check("the twice-wired gate is kept apart by its argument",
              "run-record-gate.py drift-claim-gate.py" in hooks
              and "run-record-gate.py drift-assertion-gate.py" in hooks,
              [k for k in hooks if k.startswith("run-record")])
        check("a wired gate that never fired is named, not omitted",
              "never-fires.py" in report["wired_but_never_fired"],
              report["wired_but_never_fired"])

        # ── the printed form ──
        rc, text, _ = run_rollup(tmp)
        check("the human report prints", rc == 0)
        check("it names the retire candidate", "hot-with-owner.py" in text)
        check("a candidate never appears without its caveat",
              "prevention here, detection there" in text, text[-900:])
        check("it says how many gates have no verified owner",
              "no verified second owner" in text)
        check("it flags unclassified traffic as a wiring problem",
              "should be 0" in text, text[:400])
        check("it prints the reopen day and gate",
              "stopper.py" in text and day1 in text)

        # ── budgets ──
        rc, _, err = run_rollup(tmp, "--strict")
        check("--strict fails when a provisional budget is exceeded", rc == 1, err[:200])
        check("--strict says which budget", "Stop p95" in err or "PreToolUse p95" in err, err[:200])
        rc, _, _ = run_rollup(tmp)
        check("the default run never fails the nightly chain", rc == 0)

        # ── the budget is about an EVENT, not a hook ──
        # In its own fixture, because the shared one above deliberately stamps
        # many records with the same timestamp and would blur the grouping this
        # section exists to pin. A Bash call fires 13 hooks; a table of per-hook
        # p95s all reading 80ms would report OK while the event costs ten times
        # the budget, which is the false green this whole exercise is about.
        events = tempfile.mkdtemp(prefix="rollup-events-")
        try:
            rows = []
            for n, (ts_hour, hooks_ms) in enumerate((
                    (1, [10, 20, 30]), (2, [10, 20, 300]), (3, [10, 20, 30]))):
                ts, _ = stamp(1, hour=ts_hour)
                for i, elapsed in enumerate(hooks_ms):
                    rec = record(f"gate{i}.py", 1, elapsed)
                    rec["ts"] = ts
                    rec["session"] = f"session-{n}"
                    rows.append(rec)
            build_fixture(events, rows)
            rc, out, _ = run_rollup(events, "--json")
            report = json.loads(out)
            cost = report["event_cost"]["PreToolUse"]
            check("firings are grouped back into the event that caused them",
                  cost["events_seen"] == 3, cost)
            check("the report says how many hooks one event fires",
                  cost["hooks_per_event"] == 3.0, cost)
            check("the event's cost is the SUM of its hooks, not one hook",
                  cost["sum_p95_ms"] == 330, cost)
            check("the slowest single hook is reported alongside, as the wall floor",
                  cost["max_p95_ms"] == 300, cost)
            rc, text, _ = run_rollup(events)
            check("the budget line names the event and its hook count",
                  "of hook CPU across 3.0 hooks" in text, text[:600])
            check("330ms of hook CPU is over the 150ms Bash budget",
                  "OVER" in text, text[:600])
            check("a per-hook p95 that would have read OK is still available",
                  report["hook_p95_ms"]["PreToolUse"] == 300, report["hook_p95_ms"])
        finally:
            shutil.rmtree(events, ignore_errors=True)

        # ── a gate demoted from blocking to announcing is still working ──
        # Five Stop gates were demoted on 2026-08-23: same firing rate, no more
        # blocking, no more token cost. Every one of them has zero denies. Read
        # against the council's literal "zero live denies" clause they would all
        # be nominated for retirement for exactly the reason they were improved,
        # so candidacy here asks for zero INTERVENTIONS and says so in the report.
        demoted = tempfile.mkdtemp(prefix="rollup-demoted-")
        try:
            rows = []
            for day in range(7):
                rows += [record("hot-with-owner.py", day, 20, event="Stop",
                                register="announce") for _ in range(4)]
            build_fixture(demoted, rows)
            rc, out, _ = run_rollup(demoted, "--json")
            entry = json.loads(out)["hooks"]["hot-with-owner.py"]
            check("an announcement is counted, not discarded as an allow",
                  entry["announces"] == 28, entry.get("announces"))
            check("announcing costs no reopen", entry["reopens"] == 0, entry)
            check("a gate that announces is NOT nominated for retirement",
                  entry["retire_candidate"] is False, entry)
            rc, text, _ = run_rollup(demoted)
            check("the report says the clause was tightened and why",
                  "said zero DENIES" in text, text[-800:])
        finally:
            shutil.rmtree(demoted, ignore_errors=True)

        # ── the correlation key, and the sentence it makes answerable ──
        # tool_use_id is identical across every hook of one invocation, so the
        # grouping stops being an inference. What that buys is the OUTER
        # decision: what the harness did with the call once every gate had
        # spoken, which no single gate can know because it returns before the
        # others are collected. Derived here by the harness's own precedence.
        sent = tempfile.mkdtemp(prefix="rollup-sentence-")
        try:
            rows = []
            # call 1: three gates allow, one denies -> the stack denied, and
            # exactly one gate is credited with the sentence
            for gate, outcome in (("gate0.py", "allow"), ("gate1.py", "deny"),
                                  ("gate2.py", "allow"), ("gate3.py", "allow")):
                rec = record(gate, 1, 20, outcome)
                rec["tool_use_id"] = "toolu_A"
                rows.append(rec)
            # call 2: TWO gates refuse the same call. Only one sentence was
            # needed, so crediting both would make every gate look load-bearing.
            for gate, outcome in (("gate0.py", "deny"), ("gate1.py", "deny"),
                                  ("gate2.py", "allow")):
                rec = record(gate, 1, 20, outcome)
                rec["tool_use_id"] = "toolu_B"
                rows.append(rec)
            # call 3: everyone allows
            for gate in ("gate0.py", "gate1.py", "gate2.py"):
                rec = record(gate, 1, 20)
                rec["tool_use_id"] = "toolu_C"
                rows.append(rec)
            build_fixture(sent, rows)
            rc, out, _ = run_rollup(sent, "--json")
            report = json.loads(out)
            cost = report["event_cost"]["PreToolUse"]
            check("three tool calls are seen as three, not ten firings",
                  cost["events_seen"] == 3, cost)
            check("grouping by tool_use_id needs no timestamp guessing",
                  report["approximate_groups"] == 0, report["approximate_groups"])
            check("the outer decision is the harness's precedence, not a per-gate view",
                  cost["outer_decisions"].get("deny") == 2
                  and cost["outer_decisions"].get("allow") == 1,
                  cost["outer_decisions"])
            sentences = report["sentenced_by"]["PreToolUse"]
            check("one refusal per call is credited, however many gates refused",
                  sum(sentences.values()) == 2, sentences)
            hooks = report["hooks"]
            check("a gate's deny count and its sentence count can differ, and do",
                  hooks["gate1.py"]["denies"] == 2
                  and hooks["gate1.py"]["sentenced"] < hooks["gate1.py"]["denies"],
                  {k: (v["denies"], v["sentenced"]) for k, v in hooks.items()})
            check("a gate that never refuses anything sentences nothing",
                  hooks["gate2.py"]["sentenced"] == 0, hooks["gate2.py"])
            rc, text, _ = run_rollup(sent)
            check("the printed report names how many calls the stack refused",
                  "were refused by the stack as a whole" in text, text[:600])
            check("a fully-correlated window does not warn about grouping",
                  "grouped by timestamp instead" not in text, text[:600])
        finally:
            shutil.rmtree(sent, ignore_errors=True)

        # ── a window shortened by rotation must not pass as a full one ──
        rotated = tempfile.mkdtemp(prefix="rollup-rotated-")
        try:
            fresh = [record("gate0.py", 1, 10) for _ in range(3)]
            build_fixture(rotated, fresh)
            # the mere existence of a rotation generation is what makes a short
            # window suspicious rather than merely quiet
            open(os.path.join(rotated, "out", "hook-telemetry.jsonl.1"), "w").close()
            rc, text, _ = run_rollup(rotated)
            check("a window truncated by rotation says so", "rotated away" in text,
                  text[:500])
            rc, out, _ = run_rollup(rotated, "--json")
            trunc = json.loads(out)["window_truncated_by_rotation"]
            check("and reports what it actually covers",
                  trunc and trunc["requested_days"] == 7 and trunc["covered_days"] < 7,
                  trunc)
        finally:
            shutil.rmtree(rotated, ignore_errors=True)

        # ── an empty window says so instead of printing zeros ──
        empty = tempfile.mkdtemp(prefix="rollup-empty-")
        try:
            build_fixture(empty, [])
            rc, text, _ = run_rollup(empty)
            check("an empty window is reported as no data, not as healthy", rc == 0
                  and "NO LIVE TELEMETRY YET" in text, text[:300])
        finally:
            shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return CHECKER.summary(limit=8)


if __name__ == "__main__":
    raise SystemExit(main())
