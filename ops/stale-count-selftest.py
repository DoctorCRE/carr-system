#!/usr/bin/env python3
"""ops/stale-count-selftest.py — acceptance test for ops/stale-count-check.py.

WHAT THE CHECK IS FOR. Rule b01edd26 bans a hardcoded count a later edit can
falsify. On 2026-08-14/15 that rule was violated four separate times inside the
enforcement layer itself, and each one needed its own pull request to fix: the
Stop door said "NINE times", the write door said "8 times", the audit-queue
check said "11 occurrences", and the drift selftest said "NINE times" — while
the ledger climbed to twelve. A gate whose whole job is catching a stale figure
quoted as present state was quoting one, in the sentence meant to persuade.

Three passes by hand still missed a fifth site, which is why this is code.

THE PREDICATE, and getting it narrow is the entire design. A first attempt
flagged any quantity next to a countable noun and produced 173 hits across the
repo, nearly all of them ordinary prose ("it reads two version strings", "fired
five times a week"). A baseline that size gets muted, and a muted check is worse
than none.

What actually goes stale is a count over an OPEN-ENDED window — one still
accumulating as the system runs. "has failed nine times", "11 occurrences",
"eight times since 2026-08-04". What does NOT go stale is a count over a CLOSED
window: "on the night this was written he was right five times out of five" is
true forever. The date is not the discriminator — the damaging lines carried
dates too, because `since <date>` opens a window rather than closing one.

Narrowed that way the same scan returns two hits across 382 files.

QUOTED HISTORY IS EXEMPT. A line that quotes a prior value while documenting its
correction — `carried "11 occurrences" and the ledger moved to twelve` — is a
record of the fix, not a live claim. The number sits inside quotes; a live claim
never does.

RUN IT:
    python3 ops/stale-count-selftest.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "ops", "stale-count-check.py")

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def scan(text: str):
    """Run the check over one throwaway file; return (rc, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "sample.py")
        with open(f, "w") as fh:
            fh.write(text + "\n")
        p = subprocess.run([sys.executable, CHECK, "--path", f],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr


print("stale-count-selftest — an open-ended count goes stale; a closed one does not")

check("the check exists", os.path.exists(CHECK), CHECK)
if not os.path.exists(CHECK):
    sys.exit(1)

# ── the four real 2026-08-14/15 sites, each verbatim in shape ───────────────
for label, line in [
    ("the Stop door's wording", 'print("This class has failed NINE times since 2026-08-04")'),
    ("the write door's wording", '# most common way this system has been wrong: 8 times since 2026-08-04'),
    ("the audit check's wording", '# `dated-artifact-read-as-present-state`, 11 occurrences, 6 of them caught'),
    ("the fifth site three hand passes missed",
     '# "Remember to check" is the same prose that has failed seven times before.'),
]:
    rc, out = scan(line)
    check(f"caught: {label}", rc != 0, out[:200])

# ── what must stay silent, or the check gets muted ──────────────────────────
for label, line in [
    ("a closed past window", '# On the night this was written he was right five times out of five.'),
    ("a structural fact", '# It reads two version strings and writes two files.'),
    ("a cadence", '# The audit task fired five times a week and cost a full opening act each time.'),
    ("a runtime-computed count", 'print(f"{len(rules)} rules loaded")'),
    ("a plain number with no countable noun", 'TIMEOUT = 60  # seconds'),
    ("quoted history documenting its own correction",
     '# This file carried "11 occurrences" and the ledger moved to twelve the same week.'),
    # A QUANTITY IS NOT A TALLY, and this one is real: it is a clause from the
    # red-team rule, reached on 2026-09-18 when the rule corpus brought rule
    # statements under this scan for the first time. "three instances" there
    # counts COPIES OF A MODEL, not events that accumulate -- the sentence is
    # about redundancy being correlated, and no later edit can falsify it. The
    # bare alternative had no open-window marker, so it fired on every use of
    # the phrase. It failed CI on run 35393298340 before it was tightened.
    ("a quantity of things rather than a running tally",
     '# Three instances of the same model family reviewing one claim are CORRELATED, not independent.'),
    ("a quantity with the countable noun but no counting verb",
     '# The reviewer sees two instances of the same argument and must judge them separately.'),
]:
    rc, out = scan(line)
    check(f"silent on: {label}", rc == 0, out[:200])

# ── shape coverage ──────────────────────────────────────────────────────────
rc, out = scan('# the class currently 12 defects and rising')
check("'currently N' is an open window", rc != 0, out[:200])
rc, out = scan('# the ledger now stands at 12')
check("'now stands at N' is an open window", rc != 0, out[:200])

rc, out = scan('# This has failed NINE times since 2026-08-04.')
check("a date does NOT excuse an open window — 'since' opens one", rc != 0, out[:200])

# ── the finding has to be usable ────────────────────────────────────────────
rc, out = scan('# it has failed nine times')
check("the finding names the file and line", "sample.py" in out and ":1" in out, out[:200])
check("...and says what to do instead",
      "shape" in out.lower() or "standing-context" in out.lower(), out[:300])

# ── the self-reference exemption may not quietly grow ───────────────────────
# The check and this suite carry the banned shape as SPECIMENS: the check quotes
# what it hunts, and every case above feeds it a real 2026-08-15 site verbatim.
# A specimen is not a claim, so those two files are skipped — by name, and only
# those two. Pinning the set here is what stops it becoming a place findings go
# to be hidden, which is the failure the coverage backlog was built to avoid.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("stale_count_check", CHECK)
assert _spec is not None and _spec.loader is not None
_mod = _ilu.module_from_spec(_spec)
sys.modules["stale_count_check"] = _mod
_spec.loader.exec_module(_mod)
check("the exempt set is exactly the check and this suite",
      _mod.SELF_REFERENTIAL == {"ops/stale-count-check.py",
                                "ops/stale-count-selftest.py"},
      str(sorted(_mod.SELF_REFERENTIAL)))

# ── the real repository, and this assertion IS the enforcement ──────────────
# Asserting only "it runs" would make this a report nobody reads. The repo is
# clean as of 2026-08-15 — every site found by this check was fixed in the same
# pull request — so demanding it STAY clean costs nothing today and fails CI the
# moment a new open-ended count is committed. That is the whole point: rule
# b01edd26 was violated four times while it was advisory prose.
p = subprocess.run([sys.executable, CHECK], capture_output=True, text=True,
                   cwd=REPO, timeout=180)
check("THE REPOSITORY CARRIES NO OPEN-ENDED COUNT — a new one fails CI here",
      p.returncode == 0, (p.stdout + p.stderr)[:600])

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for _label in FAILED:
        print(f"  - {_label}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
