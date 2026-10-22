"""Does THIS branch own the config-as-code drift on this machine?

WHY THIS EXISTS. ops/ci.sh's binding class compares the LIVE MACHINE against the
repository's declarations, which makes it the one check whose subject is not the
branch. Its own comment already states the rule it means to apply: a
machine-global condition may open a loop, never veto unrelated work, and the
class fails "only when this branch touches the declarations IT IS ABOUT."

The implementation was wider than that sentence. It asked whether the branch had
touched ANY path under ops/launchd/ or ops/scheduled-tasks/, and if so made every
drifting item on the machine fatal — including items in the other directory, and
items nobody on this branch had ever seen.

That is not hypothetical. On 2026-08-22 a one-word comment repair to a launchd
file (a double hyphen inside an XML comment, which a strict parser refuses) made
two unrelated SCHEDULED TASKS another session had installed into that branch's
problem. The branch could not fix them either: capturing the machine into an
unrelated change means committing somebody else's in-flight work, which rule
308ef1de forbids and the git-writer gate blocks outright. The only remaining move
was to skip every check — the outcome the comment above it calls out as the bad
one. Both halves of the machine were wedged at once, because without the plist
repair the gates class failed on the unparsable file and with it the binding
class failed on the drift.

WHAT THIS NARROWS, AND WHAT IT DELIBERATELY DOES NOT.
  * Drift STILL RUNS on every push and is STILL printed in full. A silent drift
    is how five gates sat off for a day.
  * A branch that touches a drifting declaration still FAILS. That is the case
    the check exists for and it is untouched.
  * A branch that touches the MECHANISM — ops/config-as-code.py itself, or the
    settings declaration — still owns all of it, because a change to the thing
    that reconciles is in that business by definition.
  * Only the cross-family case is released: touching a launchd file does not
    make somebody else's scheduled-task drift yours.

ONE CODE PATH, TWO CALLERS (rule a8c55a47): ops/ci.sh asks this, and so does its
selftest. A second copy of the matching rule in shell would be free to drift from
the one that is tested.
"""

from __future__ import annotations

import re
import sys

# A change to the reconciler itself, or to the settings declaration it reconciles,
# puts a branch in this business whatever else drifted.
MECHANISM_PATHS = ("ops/config-as-code.py", "ops/config/settings")

# The families the drift report names. A drift line looks like
#     "  scheduled-task notes-sweep-hourly"
#     "  launchd com.carr.calendar-prebrief-joe.plist"
# ITEM LINES SIT AT TWO SPACES, DETAIL LINES AT SIX. Keying on indentation
# rather than on a closed list of families is what lets an UNRECOGNISED family
# be seen at all — and being seen is what lets it be treated as owned rather
# than silently dropped. A closed list here made that safety branch dead code.
_DRIFT_LINE = re.compile(r"^ {1,4}([a-z][a-z0-9-]*)\s+(\S+)\s*$")

FAMILY_DIR = {"launchd": "ops/launchd/", "scheduled-task": "ops/scheduled-tasks/"}


# A declaration is named on disk with an extension and in the drift report
# usually without one. Only these suffixes are stripped: chopping at the last
# dot unconditionally would mangle com.carr.calendar-prebrief-joe into
# com.carr.calendar-prebrief.
_SUFFIXES = (".plist", ".json", ".yaml", ".yml", ".toml")


def _stem(name: str) -> str:
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def drifting_items(report: str) -> list[tuple[str, str]]:
    """(family, item) for every item the drift report names."""
    out: list[tuple[str, str]] = []
    for line in report.splitlines():
        m = _DRIFT_LINE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def owned(changed_paths: list[str], report: str) -> list[str]:
    """The drifting items THIS branch is answerable for.

    Empty means the drift is on the machine and belongs to whoever installed it.
    """
    changed = [p.strip() for p in changed_paths if p.strip()]
    if any(p.startswith(MECHANISM_PATHS) for p in changed):
        return [f"{fam} {item}" for fam, item in drifting_items(report)] or ["the reconciler itself"]

    hits: list[str] = []
    for fam, item in drifting_items(report):
        directory = FAMILY_DIR.get(fam)
        if not directory:
            # An unrecognised family is treated as OWNED rather than ignored: a
            # new drift kind must not silently stop being anybody's problem.
            hits.append(f"{fam} {item}")
            continue
        stem = _stem(item)
        for p in changed:
            if not p.startswith(directory):
                continue
            base = p.rsplit("/", 1)[-1]
            if base == item or _stem(base) == stem:
                hits.append(f"{fam} {item}")
                break
    return hits


def main() -> int:
    """stdin is the drift report; argv[1:] are the branch's changed paths.

    Exit 0 = this branch owns nothing that drifted (report, do not veto).
    Exit 1 = this branch owns at least one drifting item; the names go to stdout.
    """
    hits = owned(sys.argv[1:], sys.stdin.read())
    if not hits:
        return 0
    for h in hits:
        print(h)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
