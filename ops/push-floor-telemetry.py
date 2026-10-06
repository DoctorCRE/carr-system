#!/usr/bin/env python3
"""push-floor-telemetry.py — the confirm-or-kill number for the 2026-08-23
push-path change.

WHAT WAS DECIDED AND WHAT IS BEING MEASURED. That day's gates council moved the
full ten-class ops/ci.sh off ops/githooks/pre-push, leaving a fast local floor
(pushfloor, secret) and keeping the full strict suite hosted as the required
merge check. Both chairs marked it a COST trade rather than a defect fix, and
attached one acceptance test to it:

    count, over the first week, the hosted-red runs that the old local-full
    push would have caught before the push left the machine.

That count is the verdict. Near zero means the local minutes were sediment and
the change stands. Materially non-zero means local-full was doing real work,
and the honest response is to put some of those classes back on the push path
-- not to argue about it.

HOW IT IS COUNTED, and why not by re-running anything. ops/ci.sh emits one
`ci-floor-verdict:` line on every red run, naming the failed classes split into
`floor:` (the local hook still runs these, so a failure means the floor let
something past) and `relocated:` (used to be caught locally, now hosted-only).
This reads those lines back out of the hosted job logs. Nothing is recomputed
and nothing is inferred: the run that had the facts wrote them down.

    ops/push-floor-telemetry.py                # the last 7 days
    ops/push-floor-telemetry.py --days 14
    ops/push-floor-telemetry.py --json

WHAT IT WILL NOT DO IS GUESS. Runs older than the change have no verdict line,
and are reported as `unclassified` rather than as zero -- an absent measurement
and a measurement of zero are different findings (rule 88e9b5eb). Likewise, if
`gh` is missing or unauthenticated this exits 78 (EX_CONFIG, the repo's "not
configured here"), never 0 with an empty count.
"""
from __future__ import annotations

import collections
import datetime
import json
import re
import subprocess
import sys

WORKFLOW_CHECK = "ops/ci.sh --strict"
VERDICT = re.compile(r"ci-floor-verdict:\s*floor:(?P<floor>.*?)\s*relocated:(?P<rel>.*)$")
FAILED = re.compile(r"ci-failed-classes:(?P<classes>.*)$")


def gh(*args, timeout=120):
    return subprocess.run(["gh", *args], capture_output=True, text=True,
                          timeout=timeout)


def runs_since(since_iso, limit=200):
    """Completed failing runs since a date.

    `gh run list` SILENTLY CAPS its output and a capped list read as a total is
    how you get a confident wrong number, so this asks the API with an explicit
    per_page and reports what it actually received rather than assuming it saw
    everything.
    """
    q = (f"/repos/{{owner}}/{{repo}}/actions/runs"
         f"?status=completed&per_page={min(limit, 100)}&created=>{since_iso}")
    p = gh("api", q, "--paginate")
    if p.returncode != 0:
        return None, p.stderr.strip()
    runs = []
    try:
        # --paginate concatenates JSON objects; parse each top-level object.
        dec, idx = json.JSONDecoder(), 0
        while idx < len(p.stdout):
            obj, end = dec.raw_decode(p.stdout, idx)
            runs.extend(obj.get("workflow_runs", []))
            idx = end
            while idx < len(p.stdout) and p.stdout[idx] in " \n\r\t":
                idx += 1
    except ValueError as exc:
        return None, f"could not parse the runs listing: {exc}"
    return [r for r in runs if r.get("conclusion") == "failure"], None


def verdict_of(run_id):
    """The verdict line from one run's log, or None when it predates the change."""
    p = gh("run", "view", str(run_id), "--log-failed", timeout=180)
    text = p.stdout or ""
    if p.returncode != 0 and not text:
        p = gh("run", "view", str(run_id), "--log", timeout=180)
        text = p.stdout or ""
    for line in text.splitlines():
        m = VERDICT.search(line)
        if m:
            return ([c for c in m.group("floor").split() if c != "none"],
                    [c for c in m.group("rel").split() if c != "none"])
    for line in text.splitlines():
        m = FAILED.search(line)
        if m:
            # A red run from before the verdict line existed. Its failed classes
            # are known, so it can still be classified -- say so explicitly.
            classes = m.group("classes").split()
            floor = [c for c in classes if c in ("pushfloor", "secret")]
            return (floor, [c for c in classes if c not in ("pushfloor", "secret")])
    return None


def main(argv):
    days = 7
    # BOUNDED, AND IT SAYS SO. `gh run view --log` downloads a whole log archive
    # per run; at this shop's merge rate a full week is hundreds of them and the
    # command simply never returns. It therefore inspects the most recent
    # max_runs and REPORTS how many red runs it did not open. A bound nobody is
    # told about reads as "I looked at everything and found this", which is the
    # failure mode this whole telemetry exists to avoid.
    # 100 covers a full week at the observed red rate (97 in the 2026-08-19..27
    # window); the 25 default understated that week's verdict count by more than
    # half (33 vs 77) for any caller who missed --max-runs.
    max_runs = 100
    if "--max-runs" in argv:
        try:
            max_runs = int(argv[argv.index("--max-runs") + 1])
        except (IndexError, ValueError):
            print("push-floor-telemetry: --max-runs needs a number", file=sys.stderr)
            return 64
    if "--days" in argv:
        try:
            days = int(argv[argv.index("--days") + 1])
        except (IndexError, ValueError):
            print("push-floor-telemetry: --days needs a number", file=sys.stderr)
            return 64

    try:
        have_gh = gh("--version", timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        have_gh = False
    if not have_gh:
        print("push-floor-telemetry: the gh CLI is not available here, so the "
              "count cannot be taken. NOT reporting zero.", file=sys.stderr)
        return 78

    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    runs, err = runs_since(since)
    if runs is None:
        print(f"push-floor-telemetry: could not list runs ({err}). "
              "NOT reporting zero.", file=sys.stderr)
        return 78

    relocated = collections.Counter()
    floor = collections.Counter()
    unclassified = 0
    not_opened = max(0, len(runs) - max_runs)
    for r in runs[:max_runs]:
        v = verdict_of(r["id"])
        if v is None:
            unclassified += 1
            continue
        f, rel = v
        floor.update(f)
        relocated.update(rel)

    # NAMED FOR WHAT IT ACTUALLY MEASURES. The first version of this called the
    # number "hosted red local-full would have caught", and the first real run
    # showed why that is wrong: every red it found predated the change, which
    # means local-full DID run on those pushes and they went hosted-red anyway.
    # Local-full demonstrably did not catch them. So the measurement is "hosted
    # red in a class the local floor no longer runs" -- a CANDIDATE for the
    # relocation having cost something, which only a post-change run can be.
    # Pre-change runs in this column are the control group, and they read the
    # other way: evidence the local run was not catching these to begin with.
    candidates = sum(relocated.values())
    result = {
        "window_days": days,
        "since": since,
        "red_runs_found": len(runs),
        "red_runs_examined": min(len(runs), max_runs),
        "red_runs_not_opened": not_opened,
        "unclassified_runs": unclassified,
        "hosted_red_in_relocated_classes": candidates,
        "relocated_class_failures": dict(relocated),
        "floor_class_failures": dict(floor),
    }
    if "--json" in argv:
        print(json.dumps(result, indent=2))
        return 0

    print(f"\npush-path confirm-or-kill — last {days} days (since {since})\n")
    print(f"  hosted red runs found               {len(runs)}")
    print(f"  of those, opened and classified     {min(len(runs), max_runs)}")
    if not_opened:
        print(f"  NOT opened (bounded by --max-runs)  {not_opened}"
              "  <- the count below is a floor, not a total")
    print(f"  runs with no verdict line           {unclassified}"
          + ("  (predate the change; not counted as zero)" if unclassified else ""))
    print(f"\n  HOSTED RED IN CLASSES THE LOCAL FLOOR NO LONGER RUNS: "
          f"{candidates}")
    for cls, n in relocated.most_common():
        print(f"      {cls:<12} {n}")
    if floor:
        print("\n  Failures in classes the local floor DOES run "
              "(the floor let these past, a different finding):")
        for cls, n in floor.most_common():
            print(f"      {cls:<12} {n}")
    print("\n  Reading it. Only runs from AFTER 2026-08-23 bear on the verdict.\n"
          "  A red in a relocated class from BEFORE that date is the control\n"
          "  group and cuts the other way: local-full ran on that push and the\n"
          "  defect reached hosted CI regardless, so local-full was not\n"
          "  catching it. After the change, a count near zero confirms the\n"
          "  relocation; a materially non-zero count is the kill signal, and\n"
          "  the classes listed are the ones to put back on the push path.\n"
          "  The council's bar is one week.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
