#!/usr/bin/env python3
"""ops/stale-count-check.py — a count over an open-ended window goes stale.

WHY THIS EXISTS. Rule b01edd26 bans a hardcoded count a later edit can falsify.
On 2026-08-14/15 it was violated four separate times inside the ENFORCEMENT
LAYER, each needing its own pull request:

    hooks/drift-assertion-gate.py      "NINE times"
    hooks/drift-claim-gate.py          "8 times"
    ops/audit-queue-freshness-check.py "11 occurrences"
    ops/drift-assertion-gate-selftest.py "NINE times"

The ledger read twelve. The gate whose entire job is catching a stale figure
quoted as present state was quoting one, in the sentence written to persuade a
session to stop and check. A session that spots the wrong number has been handed
a reason to discount everything after it.

Three passes by hand still missed a fifth site — the same file, a different
wording. That is why this is a predicate and not a habit (rule 5e89c211).

THE DISCRIMINATOR, and getting it narrow is the whole design. A first attempt
flagged any quantity beside a countable noun: 173 hits across the repo, nearly
all ordinary prose ("it reads two version strings", "fired five times a week").
A baseline that size gets muted on day one, and a muted check is worse than none.

What goes stale is a count over an OPEN-ENDED window, still accumulating as the
system runs:
    "has failed nine times"     "11 occurrences"     "eight times since <date>"
    "currently 12 defects"      "now stands at 12"

What does NOT go stale is a count over a CLOSED window. "On the night this was
written he was right five times out of five" is true permanently.

THE DATE IS NOT THE DISCRIMINATOR, which was the first wrong guess. Every
damaging line carried one, because `since <date>` OPENS a window rather than
closing it. Narrowed to open-endedness, the same scan returns two hits.

QUOTED HISTORY IS EXEMPT. A line quoting a prior value while documenting its
correction is a record of the fix, not a live claim, and the number sits inside
quotes. A live claim never does.

RUN IT:
    python3 ops/stale-count-check.py            # whole repo, exit 1 on findings
    python3 ops/stale-count-check.py --path F   # one file
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = {"hooks", "ops", "bin", "tools", "exporters", "pipelines", "docs"}

# THE ONLY EXEMPT FILES, named rather than pattern-matched, because a silent
# filter is how a real finding gets excluded by accident. These two carry the
# banned shape as SPECIMENS — the check quotes what it looks for, and the
# selftest feeds it the four real 2026-08-15 sites verbatim as fixtures. A
# specimen is not a claim. The selftest asserts this set is exactly these two,
# so it cannot quietly grow into a place findings go to be hidden.
SELF_REFERENTIAL = {"ops/stale-count-check.py", "ops/stale-count-selftest.py"}
SCAN_EXT = (".py", ".sh", ".md", ".json")

_NUM = (r"(?:\d{1,4}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
        r"twelve|thirteen|fourteen|fifteen|twenty)")

# Each alternative is one way of naming a still-accumulating total.
OPEN_WINDOW = re.compile(
    rf"\b{_NUM}\s+(?:times|occurrences|instances|cases)\b[^.\n]{{0,40}}"
    rf"\b(?:since|so far|to date|and counting|and rising)\b"
    rf"|\bhas\s+(?:failed|happened|occurred|fired|been\s+caught)\s+{_NUM}\s+times\b"
    # A TALLY, NOT A QUANTITY. This alternative carries no open-window
    # marker, so on its own it fired on any use of "N instances" at all --
    # including "three instances of the same model family reviewing one
    # claim are CORRELATED", which is a structural statement about
    # redundancy and can never go stale. Requiring a counting verb nearby
    # keeps the running-total sense ("three instances were found") and
    # drops the quantity sense. The four real 2026-08-15 sites are fixtures
    # in the selftest, so a tightening that lets any of them through fails
    # there rather than quietly shrinking the gate.
    rf"|\b{_NUM}\s+(?:occurrences|instances)\b[^.\n]{{0,40}}"
    rf"\b(?:found|recorded|logged|caught|seen|observed|reported|counted)\b"
    rf"|\b(?:found|recorded|logged|caught|seen|observed|reported|counted)"
    rf"[^.\n]{{0,40}}\b{_NUM}\s+(?:occurrences|instances)\b"
    rf"|\bcurrently\s+{_NUM}\s+\w+"
    rf"|\bnow\s+(?:at|stands at|reads)\s+{_NUM}\b",
    re.I)

# Quoted history: a comment recording what a file USED to say while documenting
# the correction. Quotes alone are not enough — a print() or a message string is
# a live claim that happens to be a string literal, and the first version of this
# check exempted exactly those, which would have let all four real sites through.
# The line must also carry a past-tense marker naming it as a former value.
_HISTORY = r"(?:carried|used to (?:say|read)|previously|formerly|said|read|was|were|its own copy|old(?:er)? (?:wording|copy))"
QUOTED = re.compile(rf"{_HISTORY}[^\n]{{0,60}}[\"'][^\"'\n]*"
                    rf"{_NUM}\s+(?:times|occurrences|instances|cases)"
                    r"[^\"'\n]*[\"']"
                    rf"|[\"'][^\"'\n]*{_NUM}\s+(?:times|occurrences|instances|cases)"
                    rf"[^\"'\n]*[\"'][^\n]{{0,60}}{_HISTORY}", re.I)


def offenders(text: str) -> list[tuple[int, str]]:
    """Findings as (line number, line).

    The quoted-history test reads the line JOINED WITH THE ONE BEFORE IT,
    because a wrapped comment routinely leaves the past-tense marker on one line
    and the quoted value on the next — which is how this check's own first run
    flagged the very comment documenting the fix it was written for.
    """
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines, 1):
        if not OPEN_WINDOW.search(line):
            continue
        context = (lines[i - 2] + " " + line) if i >= 2 else line
        if QUOTED.search(context):
            continue
        out.append((i, line.strip()))
    return out


def tracked_files() -> list[str]:
    p = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                       text=True, timeout=120)
    return [f for f in (p.stdout or "").split()
            if f.endswith(SCAN_EXT) and f.split("/")[0] in SCAN_DIRS]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", help="scan one file instead of the repo")
    args = ap.parse_args()

    if args.path:
        targets = [args.path]
        root = ""
    else:
        targets = tracked_files()
        root = REPO

    findings: list[str] = []
    for rel in targets:
        if rel in SELF_REFERENTIAL:
            continue
        full = os.path.join(root, rel) if root else rel
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for lineno, line in offenders(text):
            findings.append(f"  {rel}:{lineno}: {line[:110]}")

    if not findings:
        print(f"stale-count: OK — no open-ended counts in {len(targets)} file(s)")
        return 0

    print(f"stale-count: {len(findings)} hardcoded count(s) over an OPEN-ENDED "
          f"window — each will be wrong the next time the thing it counts moves:\n")
    print("\n".join(findings))
    print("\n  Rule b01edd26 bans a count a later edit can falsify. This shape has "
          "\n  already cost four pull requests inside the enforcement layer alone."
          "\n\n  FIX: state the SHAPE, which does not go stale (\"the most frequent "
          "\n  failure class on record\"), and point at the live source — "
          "`standing-context`"
          "\n  returns the ledger with current counts. Do not update the integer: that "
          "\n  restages the same failure for whoever reads it next."
          "\n\n  A count over a CLOSED window (\"on the night this was written, five of "
          "\n  five\") is fine and is not flagged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
