#!/usr/bin/env python3
# doctrine: runbook
"""Fail if any client-deliverable path is tracked (WR-000049).

Joe's ruling of 2026-09-03 is that the repository stays public, so the tree
must carry no client record. PR #868 removed the tracked client deliverables
that existed at the time (two Ferris dental pre-tour packets and the
Pensacola industrial pre-tour) and closed the .gitignore gap that let them
in. This gate is the durable half of that fix: it fails the push if a path
matching a client-deliverable shape is ever tracked again, whether it comes
back through a revert, a rebase, or someone dropping a new packet in without
reading .gitignore first.

The patterns mirror .gitignore's client-material block exactly, on purpose —
this gate and that ignore rule must never drift apart. See .gitignore for why
the patterns are deliberately generic (naming a client here would put the
identifier this rule exists to keep out into a tracked file).

Checks the COMMITTED tree (``git ls-files``), not the staged index: unlike
the pre-commit path-hygiene check, this is a pushfloor/CI gate answering
"does anything tracked right now violate the rule", not "did this commit
introduce a new violation".
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys

# Kept identical in shape to the deliverables/ patterns in .gitignore.
FORBIDDEN_PATTERNS = [
    "deliverables/*-carr-branded-*/*",
    "deliverables/*-pretour-*/*",
    "deliverables/*-packet-*/*",
]


def tracked_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        text=True, capture_output=True, check=True,
    )
    return proc.stdout.splitlines()


def violations(paths: list[str]) -> list[str]:
    bad = []
    for path in paths:
        for pattern in FORBIDDEN_PATTERNS:
            if fnmatch.fnmatch(path, pattern):
                bad.append(path)
                break
    return bad


def main() -> int:
    bad = violations(tracked_paths())
    if bad:
        print(
            "no-client-deliverables-gate: client-deliverable path(s) are tracked "
            "(WR-000049 / Joe's 2026-09-03 public-repo ruling):",
            file=sys.stderr,
        )
        for path in bad:
            print(f"  {path}", file=sys.stderr)
        print(
            "Remove them (git rm) rather than gitignoring them after the fact — "
            "a tracked file is already in history.",
            file=sys.stderr,
        )
        return 1
    print("no-client-deliverables-gate: clean — no tracked client-deliverable paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
