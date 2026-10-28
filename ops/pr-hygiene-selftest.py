#!/usr/bin/env python3
"""ops/pr-hygiene-selftest.py — the acceptance test for ops/pr-hygiene-check.py.

WHAT THE CHECK IS FOR. On 2026-08-14 Joe asked the question this whole
mechanism exists to answer: if he stops reading GitHub's failure emails,
what notices that a pull request has been abandoned? Nothing did. A pull
request opened at 13:09Z that day sat eight and a half hours with no CI run
on it at all and a merge conflict against main, and it was found only
because a session went looking by hand. Its change was still needed and it
merged the same evening once someone rebased it.

The classifier below is the thing that would have caught it on a schedule.
It is pure: it takes the pull-request rows GitHub already returns and a
clock, and returns findings. No network in this file, so the test is
deterministic and runs on any machine including a CI runner with no
GitHub credential.

WHY EACH THRESHOLD IS WHAT IT IS. A red or check-less pull request minutes
old is a session mid-iteration, and flagging it would train everyone to
ignore this check — the alarm-that-fires-every-week problem the drift watch
comments warn about twice. The thresholds are set so a normal build loop
never trips them and an abandoned branch always does.

RUN IT:
    python3 ops/pr-hygiene-selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import importlib.util

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pr-hygiene-check.py")
_spec = importlib.util.spec_from_file_location("pr_hygiene_check", _PATH)
# The hyphen in the filename rules out a plain import. These two asserts are
# what tells mypy the spec and its loader are real; they also fail loudly and
# immediately if the checker is ever renamed out from under this suite.
assert _spec is not None and _spec.loader is not None, f"cannot load {_PATH}"
prh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prh)

NOW = "2026-08-14T21:45:00Z"

FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="\n        {detail}", failures=FAILED)


def pr(number, *, age_min, updated_min=None, checks="SUCCESS", merge="CLEAN", draft=False):
    """Build one GitHub-shaped pull-request row `age_min` minutes old."""
    if updated_min is None:
        updated_min = age_min
    roll = [] if checks is None else [{"conclusion": checks, "status": "COMPLETED"}]
    if checks == "IN_PROGRESS":
        roll = [{"conclusion": None, "status": "IN_PROGRESS"}]
    return {
        "number": number,
        "headRefName": f"branch-{number}",
        "title": f"pull request {number}",
        "isDraft": draft,
        "createdAt": prh.minutes_before(NOW, age_min),
        "updatedAt": prh.minutes_before(NOW, updated_min),
        "mergeStateStatus": merge,
        "statusCheckRollup": roll,
    }


def find(rows):
    return prh.classify(rows, now_iso=NOW)


def kinds(rows):
    return sorted(f["kind"] for f in find(rows))


print("pr-hygiene-selftest — the classifier must catch an abandoned pull request "
      "and stay silent on a session mid-iteration")

# ── the case this exists for: PR #79 on 2026-08-14 ──────────────────────────
abandoned = pr(79, age_min=515, checks=None, merge="DIRTY")
check("the real 2026-08-14 case is caught: 8.5h old, no checks ever, conflicted",
      find([abandoned]) != [], "this is the case the whole check exists for")
check("...and it is reported as unowned rather than merely red",
      "no-checks" in kinds([abandoned]), kinds([abandoned]))

# ── silence on healthy, active work ─────────────────────────────────────────
check("a green pull request is not a finding",
      find([pr(1, age_min=200)]) == [])
check("a pull request still running CI is not a finding",
      find([pr(2, age_min=3, checks="IN_PROGRESS")]) == [])
check("a red pull request minutes old is not a finding — that is a session mid-iteration",
      find([pr(3, age_min=6, checks="FAILURE")]) == [])
check("a draft is never a finding, however old or red",
      find([pr(4, age_min=5000, checks="FAILURE", merge="DIRTY", draft=True)]) == [])

# ── each failing shape, once past its threshold ─────────────────────────────
check("a red pull request untouched past the red threshold IS a finding",
      "stale-red" in kinds([pr(5, age_min=400, checks="FAILURE")]))
check("a conflicted pull request past the conflict threshold IS a finding",
      "conflicted" in kinds([pr(6, age_min=400, checks="SUCCESS", merge="DIRTY")]))
check("an old pull request that never ran CI IS a finding",
      "no-checks" in kinds([pr(7, age_min=400, checks=None)]))

# ── the threshold boundaries are real, not decorative ───────────────────────
just_under = prh.RED_STALE_MINUTES - 5
just_over = prh.RED_STALE_MINUTES + 5
check("a red pull request just UNDER the threshold stays silent",
      find([pr(8, age_min=just_under, checks="FAILURE")]) == [],
      f"{just_under} minutes should be silent")
check("...and just OVER it speaks",
      find([pr(9, age_min=just_over, checks="FAILURE")]) != [],
      f"{just_over} minutes should be a finding")

# ── recency is measured on the last PUSH, not on when the PR was opened ─────
check("an OLD pull request pushed to a minute ago is not stale — someone is on it",
      find([pr(10, age_min=5000, updated_min=1, checks="FAILURE")]) == [],
      "updatedAt, not createdAt, is what says whether anyone is working it")

# ── every finding carries a bound action (rule 590b11e1) ────────────────────
allf = find([pr(11, age_min=400, checks="FAILURE"),
             pr(12, age_min=400, checks=None),
             pr(13, age_min=400, merge="DIRTY")])
check("every finding names what to DO about it, never just a count",
      len(allf) == 3 and all(f.get("action") for f in allf),
      str(allf))
check("every finding names the pull request in plain words, not a bare number",
      all(f.get("title") and f.get("number") for f in allf), str(allf))

# ── an empty repo is a real answer, not a broken one ────────────────────────
check("no open pull requests reports clean rather than erroring", find([]) == [])

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
print(f"all checks passed")
sys.exit(0)
