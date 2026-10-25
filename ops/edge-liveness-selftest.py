#!/usr/bin/env python3
"""ops/edge-liveness-selftest.py — prove the alarm fires, and prove what it
refuses to fire on.

WHY THIS FILE HAS TO EXIST. ops/edge-liveness.py returned green on its first run
against production and will keep returning green for as long as the Mac behaves,
which is most of the time. A detector that has only ever been observed agreeing
that nothing is wrong has demonstrated nothing at all — it would look identical
if `stale` were misspelled, if the criticality filter excluded everything, or if
the whole verdict were `return 0`. The alarm paths cannot be triggered against
production on demand without lying to the ledger, so they are triggered here
against fabricated rows through the REAL decision function.

WHAT IT DOES NOT TEST, said plainly: the SQL. assess() is a pure function of the
rows and this file hands it rows directly, so a query that selects the wrong
column would pass every check below. The query is exercised by running the tool
against production, which is done and recorded in the pull request; these checks
own the decision, that run owns the read.

Row shape, matching the query's select list exactly:
    (service_key, environment, criticality, runtime,
     freshness_state, health, observed_at, hours_since)
"""
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "edge_liveness", os.path.join(os.path.dirname(__file__), "edge-liveness.py"))
if spec is None or spec.loader is None:
    sys.exit("cannot import edge-liveness.py")
el = importlib.util.module_from_spec(spec)
spec.loader.exec_module(el)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def row(key: str, *, crit: str = "high", runtime: str = "launchd",
        fresh: str = "fresh", health: str = "healthy",
        observed: bool = True, hours: float = 0.5) -> tuple[Any, ...]:
    return (key, "production", crit, runtime, fresh, health,
            NOW if observed else None, hours if observed else None)


def main() -> int:
    print("edge-liveness-selftest — the alarm must fire, and must refuse to "
          "fire on the three things that are not failures\n")

    # ── it fires ─────────────────────────────────────────────────────────────
    rc = el.assess([row("carr-mcp", fresh="stale", health="unknown", hours=9.0),
                    row("capture-poll", runtime="launchd")])
    check("a high-criticality service gone stale alarms", rc == 1, f"rc={rc}")

    rc = el.assess([row("neon-record-layer", crit="critical", runtime="neon-postgres",
                        fresh="stale", hours=30.0)])
    check("a critical non-launchd service gone stale alarms", rc == 1, f"rc={rc}")

    for crit in ("high", "critical"):
        rc = el.assess([row("fresh-failed", crit=crit, runtime="cloudflare-worker",
                            health="unavailable")])
        check(f"a fresh {crit} failed service alarms", rc == 1, f"rc={rc}")

    # ── the dead-Mac collapse ────────────────────────────────────────────────
    all_dark = [row(f"job-{i}", fresh="stale", hours=6.0 + i) for i in range(5)]
    rc = el.assess(all_dark)
    check("every launchd service stale together alarms", rc == 1, f"rc={rc}")

    partial = [row("job-a", fresh="stale", hours=6.0),
               row("job-b", fresh="fresh", hours=0.2)]
    rc = el.assess(partial)
    check("one launchd job stale while others are fresh still alarms",
          rc == 1, f"rc={rc}")

    # ── AND THE THREE REFUSALS, which are the point ──────────────────────────
    # 1. Never observed is not gone quiet. On the day this shipped, 17 of 33
    #    production rows had never reported. Alarming on those would have fired
    #    17 alarms about services that were working.
    rc = el.assess([row("brand-new", observed=False, fresh="missing",
                        health="unknown"),
                    row("also-new", observed=False, fresh="missing",
                        health="unknown")])
    check("services that have NEVER reported do not alarm", rc == 0, f"rc={rc}")

    check("...and a never-observed service cannot make the Mac look dark, "
          "because it was never alive to go dark",
          el.assess([row("brand-new", observed=False, fresh="missing")]) == 0)

    # 2. Low and medium criticality are noted, never alarmed. logitech-keymap is
    #    a keyboard remapper; an alarm for it is an alarm nobody reads when the
    #    record layer stops.
    rc = el.assess([row("logitech-keymap", crit="low", fresh="stale", hours=8.0),
                    row("capture-poll", crit="medium", fresh="stale", hours=8.0),
                    row("still-fine", crit="high", fresh="fresh")])
    check("low and medium criticality going quiet does NOT alarm",
          rc == 0, f"rc={rc}")

    # 3. Fresh is fresh.
    rc = el.assess([row("a"), row("b", crit="critical"), row("c", crit="low")])
    check("everything fresh returns clean", rc == 0, f"rc={rc}")

    # ── an empty catalog is a failure, not a clean bill ──────────────────────
    # A reader that says "nothing is wrong" when it can see nothing at all is
    # the false-green mechanism the Control Room premortem names by name.
    check("an empty result set alarms rather than reporting all-clear",
          el.assess([]) == 1)

    # ── the low/medium case must not silently swallow a high one ─────────────
    rc = el.assess([row("noise", crit="low", fresh="stale", hours=8.0),
                    row("real", crit="high", fresh="stale", hours=8.0)])
    check("a high-criticality alarm survives alongside low-criticality noise",
          rc == 1, f"rc={rc}")

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
