#!/usr/bin/env python3
"""Lock the citation check's verdicts against four real bundles.

WHY THIS EXISTS, and it is not a hypothetical. On 2026-09-02 the a03c r3 build
filed three blind spots against ops/citation-trace-check.py. Two reproduced:
an absolute path was not matched at all, and a pipe inside a quoted grep
pattern split the command so the path after it was never credited. Both cause
FALSE FAILURES, which is the safe direction — a build that hits one re-opens
the file or explains, which is what that build did.

The fix for them did not ship, because it made the checker report ZERO
unopened citations on the two bundles an independent reviewer had already
proved cite unread evidence. A gate that goes green on known-bad input is
worse than a gate with known blind spots: the blind spot costs an argument,
the false green costs the thing the gate exists to prevent.

So the blind spots stay, recorded, and this selftest exists so the next
attempt at fixing them cannot land without proving these four verdicts
survive. Any change that moves a number here is a regression until argued
otherwise with new evidence, not a fix.

    ops/citation-trace-check-selftest.py

Exit 0 when every locked verdict holds, 1 otherwise, 2 on missing fixtures.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Derived from this file's own location, never a hardcoded home directory: the
# first version pinned /Users/booko/carr-system, which is not a path that
# exists on the Linux CI runner.
ROOT = os.environ.get("CARR_ROOT", os.path.dirname(HERE))
CHECK = os.path.join(HERE, "citation-trace-check.py")

# bundle, expected unopened count, and why that number is what it is.
LOCKED = [
    ("a03a-r2", 0,
     "its citation failures were prose-shaped — 'the r1 graph and the prose "
     "were read' names no path — which this checker documents it cannot see"),
    ("a03b-r2", 3,
     "includes a03a-v1/findings.md, which the independent reviewer found "
     "listed but never read; losing this number means losing the reviewer's "
     "own finding"),
    ("a03c-r2", 2,
     "two upstream registry artifacts cited and never opened"),
    ("a03c-r3", 0,
     "the rebuild, which passed on the same checker version that fails the "
     "two above — that is what makes its pass mean anything"),
]


def main():
    failures, missing = [], []
    for name, want, why in LOCKED:
        bundle = os.path.join(ROOT, "out/doctorcre-program", name)
        trace = os.path.join(ROOT, "out/doctorcre-program",
                             name + "-builder-trace.jsonl")
        if not os.path.isdir(bundle) or not os.path.isfile(trace):
            missing.append(name)
            continue
        p = subprocess.run([sys.executable, CHECK, bundle, trace],
                           capture_output=True, text=True)
        m = re.search(r"^UNOPENED:\s+(\d+)", p.stdout, re.M)
        got = int(m.group(1)) if m else -1
        ok = got == want
        print("  %-10s want %-3d got %-3d %s" % (name, want, got,
                                                 "ok" if ok else "REGRESSION"))
        if not ok:
            failures.append("%s: want %d, got %d — %s" % (name, want, got, why))

    if missing:
        # SKIP, NOT FAIL, and the distinction is the whole point. The fixtures
        # are real evidence bundles under out/, which is gitignored, so they
        # exist only on a machine that produced them. A runner that does not
        # have them has learned nothing about the checker and must not claim
        # to have — but neither has it found a regression, and failing there
        # would make every hosted run red for a reason unrelated to the code.
        # Where the fixtures ARE present this still locks every verdict.
        print("SKIPPED — fixtures not present on this machine, nothing judged: "
              + ", ".join(missing))
        print("This check locks the checker's verdicts against real bundles "
              "under out/, which is gitignored. It has regression value only "
              "where those bundles exist. Absent them it asserts nothing "
              "rather than passing by default.")
        return 0
    if failures:
        print("\ncitation-trace-check selftest FAILED")
        for f in failures:
            print("  " + f)
        print("\nA moved number is a regression until argued otherwise. If the "
              "change is right, say why with evidence and update the lock in "
              "the SAME commit.")
        return 1
    print("\ncitation-trace-check selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
