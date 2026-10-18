#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""Warn, never block, when a change set is too big or too mixed to review well.

engineering-workflow-sop section 15, "Small incremental changes: the soft
threshold": a PR over 300 changed code lines or 5 code files outside generated paths, or
one that mixes a refactor with a behavior change, gets a decomposition warning
at the pre-push floor. Warning, not block, until the weekly numbers show what
the distribution actually is.

  * The numbers are the escalation router's own (ops/jev_intake.py
    ROUTE_MAX_DIFF_LINES and ROUTE_MAX_FILES), imported, never copied, so a
    rebase of the threshold happens in one place.
  * Test evidence uses lib/review_tiers.py classification and is reported
    separately from code lines and files. It remains reviewable.
  * Changed lines are added plus deleted, from `git diff -M --numstat` over the
    range (default origin/main...HEAD). A binary file counts as a file with no
    lines.
  * Paths in DECOMPOSITION_EXCLUDED_PATHS are left out of both counts: nobody
    reviews a lockfile line by line. Migrations are NEVER left out, whatever
    their name, because a migration is the change most worth reading.
  * The refactor-mix signal is a predicate, not a judgment: the range holds at
    least one near-pure rename (git similarity >= NEAR_PURE_RENAME) AND some
    other content edit outside the excluded paths. Moves land best alone.
  * It prints nothing when the change set is within the standard, and a short
    stderr block when it is not. It ALWAYS exits 0 and swallows every failure
    inside itself: it is a reporter, not a gate, and the hook discards its exit
    status anyway.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib.review_tiers import is_test_file

# THE EXCLUSION LIST, in one place and in data form so a later shared
# review-tier map in ops/config/ can take it over row for row. Each row is
# (label, match kind, value):
#   basename  the file's name equals value
#   suffix    the path ends with value
#   contains  the file's name contains value
#   segment   some directory in the path equals value
#   prefix    the path starts with value
#   exact     the path equals value
DECOMPOSITION_EXCLUDED_PATHS: tuple[tuple[str, str, str], ...] = (
    ("lockfile", "basename", "package-lock.json"),
    ("lockfile", "basename", "npm-shrinkwrap.json"),
    ("lockfile", "basename", "pnpm-lock.yaml"),
    ("lockfile", "suffix", "-lock.json"),
    ("lockfile", "suffix", ".lock"),
    ("generated", "contains", ".generated."),
    ("generated", "exact", "db/schema.sql"),
    ("minified", "suffix", ".min.js"),
    ("minified", "suffix", ".min.mjs"),
    ("minified", "suffix", ".min.css"),
    ("dependency", "segment", "node_modules"),
    ("vendored", "segment", "vendor"),
    ("run output", "prefix", "out/"),
)
# Checked before the list above and wins over it.
NEVER_EXCLUDED_SEGMENTS: tuple[str, ...] = ("migrations",)

NEAR_PURE_RENAME = 90   # git rename similarity, percent
TOP_FILES = 5


def thresholds() -> tuple[int, int]:
    """(max changed lines, max files), read from the escalation router."""
    spec = importlib.util.spec_from_file_location(
        "jev_intake_thresholds", os.path.join(HERE, "jev_intake.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ROUTE_MAX_DIFF_LINES, module.ROUTE_MAX_FILES


def is_excluded(path: str) -> str | None:
    """The exclusion label that applies to path, or None when it counts."""
    parts = path.split("/")
    if any(p in NEVER_EXCLUDED_SEGMENTS for p in parts[:-1]):
        return None
    name = parts[-1]
    for label, kind, value in DECOMPOSITION_EXCLUDED_PATHS:
        if ((kind == "basename" and name == value)
                or (kind == "suffix" and path.endswith(value))
                or (kind == "contains" and value in name)
                or (kind == "segment" and value in parts[:-1])
                or (kind == "prefix" and path.startswith(value))
                or (kind == "exact" and path == value)):
            return label
    return None


@dataclass
class Change:
    path: str
    lines: int
    status: str             # git's letter: A, M, D, R, C, T
    similarity: int = 0     # rename/copy similarity, percent
    old_path: str = ""      # the source side of a rename or copy
    binary: bool = False    # numstat printed "-": lines unknown (git prints it even for a mode-only change)
    blob_changed: bool = True   # the old and new blob ids differ

    @property
    def counted(self) -> bool:
        """Left out only when EVERY path it touches is excluded.

        A rename is judged on both sides, so moving a migration into vendor/
        still counts (is_excluded never excludes a migration), and so does
        moving ordinary source into an excluded directory.
        """
        sides = [self.path] + ([self.old_path] if self.old_path else [])
        return any(is_excluded(p) is None for p in sides)

    @property
    def test_evidence(self) -> bool:
        return all(is_test_file(p) for p in [self.path] + ([self.old_path] if self.old_path else []))

    @property
    def content_edit(self) -> bool:
        """Changed bytes, not only a mode bit: lines, a binary change, an add or a delete.

        A binary marker counts only when the blob itself changed: git prints
        "-" "-" for a binary file whose only change is its mode.
        """
        return (self.lines > 0 or self.status in "AD"
                or (self.binary and self.blob_changed))


@dataclass
class Report:
    lines: int
    files: int
    max_lines: int
    max_files: int
    mixed: bool
    top: list[Change] = field(default_factory=list)
    test_lines: int = 0
    test_files: int = 0

    @property
    def over_lines(self) -> bool:
        return self.lines > self.max_lines

    @property
    def over_files(self) -> bool:
        return self.files > self.max_files

    @property
    def warn(self) -> bool:
        return self.over_lines or self.over_files or self.mixed


def _git_z(repo: str, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", "-z", *args],
        cwd=repo, capture_output=True, check=True, timeout=60).stdout
    return out.decode("utf-8", "replace").split("\0")


def changed_files(repo: str, rng: str) -> list[Change]:
    """Every file the range changes, keyed by its new path."""
    # --raw: ":<old mode> <new mode> <old blob> <new blob> <status>", then the
    # path(s). The blob ids separate a content change from a mode-only one.
    status: dict[str, tuple[str, int, bool]] = {}
    tok = _git_z(repo, "--raw", "--no-abbrev", rng)
    i = 0
    while i < len(tok) and tok[i]:
        _old_mode, _new_mode, old_blob, new_blob, code = tok[i].lstrip(":").split(" ")
        letter = code[0]
        if letter in "RC":
            status[tok[i + 2]] = (letter, int(code[1:] or 0), old_blob != new_blob)
            i += 3
        else:
            status[tok[i + 1]] = (letter, 0, old_blob != new_blob)
            i += 2
    changes: list[Change] = []
    tok = _git_z(repo, "--numstat", rng)
    i = 0
    while i < len(tok) and tok[i]:
        added, deleted, path = tok[i].split("\t", 2)
        old_path = ""
        if path == "":      # rename/copy: the old and new paths follow
            old_path, path = tok[i + 1], tok[i + 2]
            i += 3
        else:
            i += 1
        binary = added == "-" and deleted == "-"
        lines = (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
        letter, similarity, blob_changed = status.get(path, ("M", 0, True))
        changes.append(Change(path, lines, letter, similarity, old_path, binary, blob_changed))
    return changes


def assess(changes: list[Change], max_lines: int, max_files: int) -> Report:
    tests = [c for c in changes if c.test_evidence]
    counted = [c for c in changes if not c.test_evidence and c.counted]
    moves = [c for c in counted if c.status == "R" and c.similarity >= NEAR_PURE_RENAME]
    edits = [c for c in counted if c not in moves and c.content_edit]
    top = sorted((c for c in counted if c.lines), key=lambda c: (-c.lines, c.path))[:TOP_FILES]
    return Report(lines=sum(c.lines for c in counted), files=len(counted),
                  max_lines=max_lines, max_files=max_files,
                  mixed=bool(moves) and bool(edits), top=top,
                  test_lines=sum(c.lines for c in tests), test_files=len(tests))


def render(r: Report) -> str:
    out = ["", "  pre-push NOTE (warning only, the push continues): this change set is "
               "hard to review in one piece."]
    if r.over_lines or r.over_files:
        out.append(f"    {r.lines} changed lines (threshold {r.max_lines}) across {r.files} "
                   f"files of code (threshold {r.max_files}), generated and vendored paths left out")
    if r.test_files:
        out.append(f"    Tests (evidence): {r.test_lines} changed lines across {r.test_files} files; reviewed separately")
    if r.mixed:
        out.append("    mixes moves with edits; consider splitting")
    if r.top:
        out.append("    largest by lines changed:")
        out.extend(f"      {c.lines:>6}  {c.path}" for c in r.top)
    out.append("  Split by concern: renames and moves alone first, then each behavior change "
               "under the thresholds\n  (engineering-workflow-sop section 15, small "
               "incremental changes).")
    out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    try:
        rng = argv[1] if len(argv) > 1 else "origin/main...HEAD"
        repo = argv[2] if len(argv) > 2 else os.getcwd()
        max_lines, max_files = thresholds()
        report = assess(changed_files(repo, rng), max_lines, max_files)
        if report.warn:
            print(render(report), file=sys.stderr)
    except Exception:  # noqa: BLE001 - advisory only; it must never affect a push
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
