#!/usr/bin/env python3
"""Refuse migration slot collisions without requiring merge order to match numbers.

Full filenames and checksums identify applied migrations. An unused number may
merge after a higher number; a number already allocated on the base cannot be
reused. Frozen historical collisions remain validated by the shared contract.

In merge_group runs the base is the group's base_sha, which includes groups
ahead of this one even when origin/main has not advanced. Missing Git refs or
incomplete event data fail closed.

Exit 0 clean, 1 collision, 2 unreadable input.
"""
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Iterable, Mapping

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from migration_number_contract import (  # noqa: E402
    SLOT_RE, MigrationNumberError, allocate_integration_successors, validate_migration_names)

from migration_source import migration_names  # noqa: E402

MIGRATIONS_DIR = "migrations"


def number(path: str) -> int | None:
    directory, _, name = path.rpartition("/")
    m = SLOT_RE.match(name) if directory == MIGRATIONS_DIR else None
    return int(m.group(1)) if m else None


def added(base_paths: set[str], fork_paths: set[str], head_paths: set[str]) -> dict[str, int]:
    """Migrations the change itself adds (in HEAD, in neither the fork nor the
    base), each with its number."""
    found = {p: number(p) for p in sorted(head_paths - fork_paths - base_paths)}
    return {p: n for p, n in found.items() if n is not None}


def violations(base_paths: set[str], fork_paths: set[str],
               head_paths: set[str]) -> list[str]:
    """Added migrations that reuse a number already allocated on the base."""
    occupied = {number(path) for path in base_paths}
    return [path for path, slot in added(base_paths, fork_paths, head_paths).items()
            if slot in occupied]


def default_base(environ: Mapping[str, str], event: Mapping | None = None) -> str:
    """The merge group's first parent in a merge_group run (KeyError if the
    event lacks it), else origin/<pull request base>, else origin/main."""
    if environ.get("GITHUB_EVENT_NAME") == "merge_group":
        return str((event or {})["merge_group"]["base_sha"])
    ref = (environ.get("GITHUB_BASE_REF") or "").strip()
    return f"origin/{ref}" if ref else "origin/main"


def renames(base_paths: set[str], kept: Iterable[str], bad: list[str]) -> dict[str, str]:
    """The next free filename above the base for each refused migration,
    skipping slots the change's other added migrations already hold."""
    occupied = {n for n in (number(p) for p in kept) if n is not None}
    try:
        return allocate_integration_successors(
            [p.rsplit("/", 1)[-1] for p in base_paths if number(p) is not None],
            [p.rsplit("/", 1)[-1] for p in bad], occupied_slots=occupied)
    except MigrationNumberError:
        return {}


def tree_paths(repo: pathlib.Path, ref: str) -> set[str]:
    return {f"{MIGRATIONS_DIR}/{name}" for name in migration_names(repo, ref)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)
    repo = pathlib.Path(args.repo)
    try:
        event = None
        if not args.base and os.environ.get("GITHUB_EVENT_NAME") == "merge_group":
            event = json.loads(pathlib.Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        base = args.base or default_base(os.environ, event)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"migration-order-gate: cannot read the merge group's base_sha: {exc!r}",
              file=sys.stderr)
        print("  This is not a pass.", file=sys.stderr)
        return 2

    try:
        base_paths = tree_paths(repo, base)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"migration-order-gate: cannot read migrations/ at base {base!r}: "
              f"{(getattr(exc, 'stderr', None) or str(exc)).strip()}", file=sys.stderr)
        print("  This is not a pass. Fetch the base (git fetch origin) and re-run.",
              file=sys.stderr)
        return 2
    try:
        head_paths = tree_paths(repo, args.head)
        fork = subprocess.run(["git", "merge-base", base, args.head], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        fork_paths = tree_paths(repo, fork)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"migration-order-gate: cannot read migrations/ at {args.head!r} or its "
              f"merge base with {base!r}: {(getattr(exc, 'stderr', None) or str(exc)).strip()}", file=sys.stderr)
        return 2

    try:
        validate_migration_names(path.rsplit("/", 1)[-1] for path in head_paths)
    except MigrationNumberError as exc:
        print(f"migration-order-gate: REFUSED — {exc}", file=sys.stderr)
        return 1

    bad = violations(base_paths, fork_paths, head_paths)
    if not bad:
        count = len(added(base_paths, fork_paths, head_paths))
        print(f"migration-order-gate: OK — {count} added migration(s), no base slot collisions")
        return 0

    print(f"migration-order-gate: REFUSED — {len(bad)} added migration(s) reuse "
          f"a slot allocated on {base}:", file=sys.stderr)
    targets = renames(base_paths, set(added(base_paths, fork_paths, head_paths)) - set(bad), bad)
    for path in bad:
        target = targets.get(path.rsplit("/", 1)[-1])
        print(f"  {path}" + (f" → next free slot: {MIGRATIONS_DIR}/{target}" if target else ""),
              file=sys.stderr)
    print(
        "  Choose an unused number above main and open PR allocations, rename each\n"
        "  conflicting file, and update its references before it applies anywhere.\n"
        "  Applied filenames and checksums are immutable.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
