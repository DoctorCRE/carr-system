#!/usr/bin/env python3
"""Exercise the observer verdict with synthetic health rows and no database.

The subprocess fixtures in test/ci-noise-incident-test.py also cover the query
adapter and sanitized artifact. These fixtures do not execute PostgreSQL.
"""
import os
from contextlib import redirect_stdout
from io import StringIO
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


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="\n        {detail}", failures=FAILED)


def row(key: str, *, crit: str = "high", runtime: str = "launchd",
        fresh: str = "fresh", health: str = "healthy",
        observed: bool = True, hours: float = 0.5,
        cadence: int | None = None, overdue: bool = False) -> tuple[Any, ...]:
    return el.Observation(key, "production", crit, runtime, fresh, health,
            NOW if observed else None, hours if observed else None, cadence, overdue)


def main() -> int:
    print("edge-liveness-selftest — the alarm must fire, and must refuse to "
          "fire on the three things that are not failures\n")

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

    for crit in ("high", "critical"):
        output = StringIO()
        with redirect_stdout(output):
            rc = el.assess([row("required-missing", crit=crit, observed=False,
                                fresh="missing", health="unknown", cadence=3600,
                                overdue=True)])
        check(f"an overdue never-started {crit} service alarms", rc == 1)
        check("missing first completion is distinct from a stopped Mac",
              "never started" in output.getvalue() and "MAC HAS GONE DARK" not in output.getvalue())

    for cadence, overdue in ((3600, False), (None, True)):
        rc = el.assess([row("not-due", observed=False, fresh="missing",
                            health="unknown", cadence=cadence, overdue=overdue)])
        check("new or unscheduled services do not alarm", rc == 0)

    all_dark = [row(f"job-{i}", fresh="stale", hours=6.0 + i) for i in range(5)]
    rc = el.assess(all_dark)
    check("every launchd service stale together alarms", rc == 1, f"rc={rc}")

    partial = [row("job-a", fresh="stale", hours=6.0),
               row("job-b", fresh="fresh", hours=0.2)]
    rc = el.assess(partial)
    check("one launchd job stale while others are fresh still alarms",
          rc == 1, f"rc={rc}")

    rc = el.assess([row("brand-new", observed=False, fresh="missing",
                        health="unknown"),
                    row("also-new", observed=False, fresh="missing",
                        health="unknown")])
    check("unscheduled services that never reported do not alarm", rc == 0, f"rc={rc}")

    check("...and a never-observed service cannot make the Mac look dark, "
          "because it was never alive to go dark",
          el.assess([row("brand-new", observed=False, fresh="missing")]) == 0)

    mixed = [row("dark-local", fresh="stale", hours=9.0),
             row("failed-cloud", runtime="cloudflare-worker", health="unavailable"),
             row("missing-required", observed=False, fresh="missing", health="unknown",
                 cadence=3600, overdue=True)]
    output = StringIO()
    with redirect_stdout(output):
        rc = el.assess(mixed)
    check("Mac-dark collapse preserves independent failed and never-started findings",
          rc == 1 and "MAC HAS GONE DARK" in output.getvalue()
          and "failed-cloud" in output.getvalue() and "missing-required" in output.getvalue())

    for crit in ("low", "medium"):
        rc = el.assess([row("failed-noise", crit=crit, health="unavailable"),
                        row("missing-noise", crit=crit, observed=False, fresh="missing",
                            health="unknown", cadence=3600, overdue=True), row("healthy")])
        check(f"{crit} failed or never-started services do not alarm", rc == 0)
    rc = el.assess([row("expected-skip", health="degraded")])
    check("a fresh skipped or cancelled observation keeps its existing nonalarm outcome", rc == 0)

    rc = el.assess([row("logitech-keymap", crit="low", fresh="stale", hours=8.0),
                    row("capture-poll", crit="medium", fresh="stale", hours=8.0),
                    row("still-fine", crit="high", fresh="fresh")])
    check("low and medium criticality going quiet does NOT alarm",
          rc == 0, f"rc={rc}")

    rc = el.assess([row("a"), row("b", crit="critical"), row("c", crit="low")])
    check("everything fresh returns clean", rc == 0, f"rc={rc}")

    check("an empty result set alarms rather than reporting all-clear",
          el.assess([]) == 1)

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
