#!/usr/bin/env python3
"""Reject newly staged paths that violate the file-shape rule (0e22e34a).

The check runs at the only point a bad repository path can still be refused
without rewriting history: pre-commit.  It examines additions, copies and
renames in the index, not the whole repository. Declared vendored trees
preserve their third-party directory depth;
established third-party trees
and historical filenames are not silently reclassified as a new violation.

The mechanical boundary is intentionally narrow and explicit:
  * no more than four directory components below the repository root;
  * no filename component using human draft/final-version suffixes such as
    ``_v2``, ``-v2``, ``_final`` or ``-final``. Dot-versioned machine
    contracts such as ``policy.v1.json`` are schema identifiers, not drafts.

``--paths`` accepts explicit additions for CI and the hermetic selftest;
ordinary use has no arguments and reads the staged index.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

MAX_DIRECTORY_DEPTH = 4
BAD_VERSION_NAME = re.compile(r"(?:^|[_-])(?:final|v\d+)(?:$|[_.-])", re.I)
VENDORED_TREE_PREFIXES = ("plugins/pstack/skills/",)


def violations(paths: list[str]) -> list[str]:
    bad = []
    for path in paths:
        is_vendored = path.startswith(VENDORED_TREE_PREFIXES) and path == path.strip() and "\\" not in path
        path = path.strip().replace("\\", "/")
        if not path:
            continue
        parts = [part for part in path.split("/") if part and part != "."]
        if len(parts) < 1 or ".." in parts:
            bad.append(f"unsafe repository path: {path}")
            continue
        depth = len(parts) - 1
        if depth > MAX_DIRECTORY_DEPTH and not is_vendored:
            bad.append(f"{path}: {depth} folder levels (maximum is {MAX_DIRECTORY_DEPTH})")
        if BAD_VERSION_NAME.search(parts[-1]):
            bad.append(f"{path}: draft/final version filename is forbidden")
    return bad


def staged_paths() -> list[str]:
    # ACR, not ACMR, and the M is the whole point: this check judges the SHAPE
    # of a path, so only a path that is new to the repository can violate it.
    # With M in the filter, editing a file whose name predates the rule —
    # tools/doctorcre-v5-review.cjs, say — was refused as if the edit had just
    # created it, which is exactly the "silently reclassified as a new
    # violation" case the docstring above promises does not happen.
    # ops/ci-selftest.py's git stub already distinguishes the two filters and
    # names this checker as the ACR caller.
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACR"],
        capture_output=True, check=True,
    )
    paths = [os.fsdecode(path) for path in proc.stdout.split(b"\0") if path]
    merge = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        text=True, capture_output=True,
    )
    if merge.returncode == 1:  # no merge in progress
        return paths
    merge.check_returncode()
    # MERGE_HEAD can hold several incoming commits during an octopus merge.
    # Read its worktree-specific path instead of resolving only its first ID.
    merge_path = subprocess.run(
        ["git", "rev-parse", "--git-path", "MERGE_HEAD"],
        text=True, capture_output=True, check=True,
    )
    parents = Path(merge_path.stdout.strip()).read_text(encoding="ascii").splitlines()
    existing: set[str] = set()
    for parent in parents:
        incoming = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", parent],
            capture_output=True, check=True,
        )
        # Both sources use literal NUL-delimited filenames. fsdecode preserves
        # non-UTF-8 bytes through surrogateescape instead of skipping the check.
        existing.update(os.fsdecode(path) for path in incoming.stdout.split(b"\0") if path)
    return [path for path in paths if path not in existing]


def main(argv: list[str]) -> int:
    try:
        paths = argv[2:] if argv[1:2] == ["--paths"] else argv[1:] if len(argv) > 1 else staged_paths()
    except Exception as exc:  # accident-stopper must not wedge every commit
        print(f"path-hygiene-check: could not read staged paths ({exc}); allowing unchecked.",
              file=sys.stderr)
        return 0
    bad = violations(paths)
    if not bad:
        return 0
    print("\nCOMMIT REFUSED — path hygiene (rule 0e22e34a)\n", file=sys.stderr)
    for finding in bad:
        print(f"  - {finding}", file=sys.stderr)
    print("\nUse a descriptive final filename and keep new paths at four folder levels or less.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
