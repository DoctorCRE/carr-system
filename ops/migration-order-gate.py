#!/usr/bin/env python3
"""migration-order-gate.py — a change may only add migrations ABOVE the base.

THE RULE. Every migration file this change adds must carry a number strictly
greater than the highest migration number already on the base branch at the
moment the check runs. A branch that minted 0783 when main stood at 0782, and
then fell behind a main that merged 0785..0787, fails here until it is
renumbered above main's new maximum.

WHY, measured. The release pipeline applies migrations to the staging candidate
and to production in filename order, and both then require the source ledger to
EXTEND what they applied: tools/staging-project-replacement.py refuses with
"partial candidate ledger is not an exact source prefix", and tools/migrate.py
refuses a reordered ledger in production. A number below an applied one can
never apply cleanly again. It happened three times in two days — 0757 (fixed by
#1464), the 0769/0770 pair (#1500) and 0783 (#1498, renumbered to 0800) — and
each time every worker release stopped at staging-prepare until a human noticed
and renumbered by hand. The reservation ledger (tools/reserve-migration.py)
prevents two branches minting the SAME number; nothing stopped a branch merging
a number main had already moved past. This is that check.

WHY THE BASE BRANCH AND NOT THE APPLIED LEDGER. CI has no production credential
by construction, and db/schema.sql's committed ledger lags production by
hundreds of migrations, so neither can answer "what is applied". Main's tree is
an upper bound on what any release can have applied — the pipeline only ever
releases main — so "above main's maximum" implies "above anything applied".

WHERE IT FIRES. ops/ci.sh's gates class, which runs in the required
`ops/ci.sh --strict` context on every pull_request event. Strict status checks
are on (ruleset 20824501), so when main moves, a PR must be brought up to date
before it can merge; the merge queue's update-branch call pushes a merge commit
to the PR branch, which raises a `synchronize` event, which re-runs this gate
against the NEW main. A PR that fell behind therefore turns red at exactly the
moment it would otherwise have merged a stale number.

WHAT COUNTS AS ADDED: migration filenames present in HEAD's tree and absent
from BOTH the merge-base tree and the base tree. Measuring against the merge
base is what keeps a branch that is merely behind from owning main's later
moves: when main renames or deletes a migration after the fork, the old name is
still in HEAD's tree but also in the merge base, so it never counts. On a merge
ref or an updated branch the merge base is the base tip, so the two trees agree.
A rename on the branch counts as the new name, which is how a fix forward
passes.

DELIBERATELY STRICTER THAN THE RELEASE INVARIANT. The pipeline only needs an
added number above what staging and production have APPLIED, and main's
maximum may not have applied anywhere yet. This gate still refuses any number
at or below main's maximum, because it cannot read what is applied (below), so
a PR that would in fact have been harmless must renumber too.

Exit 0 clean · 1 an added migration is not above the base maximum · 2 the base
could not be read, which is not a pass.

  ops/migration-order-gate.py                       # base = origin/$GITHUB_BASE_REF or origin/main
  ops/migration-order-gate.py --base <ref> [--head <ref>]
"""
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
from typing import Iterable, Mapping

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
# The one slot grammar the allocator and the runner share, so a lettered
# interstitial slot (0532a_...) is a migration here exactly as it is there.
from migration_number_contract import SLOT_RE, MigrationNumberError, validate_migration_names  # noqa: E402

MIGRATIONS_DIR = "migrations"


def number(path: str) -> int | None:
    directory, _, name = path.rpartition("/")
    m = SLOT_RE.match(name) if directory == MIGRATIONS_DIR else None
    return int(m.group(1)) if m else None


def highest_number(paths: Iterable[str]) -> int | None:
    nums = [n for n in (number(p) for p in paths) if n is not None]
    return max(nums) if nums else None


def added(base_paths: set[str], fork_paths: set[str], head_paths: set[str]) -> dict[str, int]:
    """Migrations the change itself adds (in HEAD, in neither the fork nor the
    base), each with its number."""
    found = {p: number(p) for p in sorted(head_paths - fork_paths - base_paths)}
    return {p: n for p, n in found.items() if n is not None}


def violations(base_paths: set[str], fork_paths: set[str],
               head_paths: set[str]) -> list[str]:
    """Added migrations whose number is not strictly above the base maximum."""
    ceiling = highest_number(base_paths)
    if ceiling is None:
        return []
    return [p for p, n in added(base_paths, fork_paths, head_paths).items() if n <= ceiling]


def default_base(environ: Mapping[str, str]) -> str:
    ref = (environ.get("GITHUB_BASE_REF") or "").strip()
    return f"origin/{ref}" if ref else "origin/main"


def tree_paths(repo: pathlib.Path, ref: str) -> set[str]:
    out = subprocess.run(
        ["git", "ls-tree", "--name-only", f"{ref}:{MIGRATIONS_DIR}"],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    return {f"{MIGRATIONS_DIR}/{line}" for line in out.splitlines() if line}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)
    repo = pathlib.Path(args.repo)
    base = args.base or default_base(os.environ)

    try:
        base_paths = tree_paths(repo, base)
    except subprocess.CalledProcessError as exc:
        print(f"migration-order-gate: cannot read migrations/ at base {base!r}: "
              f"{(exc.stderr or '').strip()}", file=sys.stderr)
        print("  This is not a pass. Fetch the base (git fetch origin) and re-run.",
              file=sys.stderr)
        return 2
    try:
        head_paths = tree_paths(repo, args.head)
        fork = subprocess.run(["git", "merge-base", base, args.head], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        fork_paths = tree_paths(repo, fork)
    except subprocess.CalledProcessError as exc:
        print(f"migration-order-gate: cannot read migrations/ at {args.head!r} or its "
              f"merge base with {base!r}: {(exc.stderr or '').strip()}", file=sys.stderr)
        return 2

    try:
        validate_migration_names(path.rsplit("/", 1)[-1] for path in head_paths)
    except MigrationNumberError as exc:
        print(f"migration-order-gate: REFUSED — {exc}", file=sys.stderr)
        return 1

    bad = violations(base_paths, fork_paths, head_paths)
    ceiling = highest_number(base_paths)
    if not bad:
        count = len(added(base_paths, fork_paths, head_paths))
        print(f"migration-order-gate: OK — {count} added migration(s), all above "
              f"{base}'s highest ({ceiling:04d})" if ceiling is not None
              else "migration-order-gate: OK — base has no migrations")
        return 0

    print(f"migration-order-gate: REFUSED — {len(bad)} added migration(s) not above "
          f"{base}'s highest number {ceiling:04d}:", file=sys.stderr)
    for path in bad:
        print(f"  {path}", file=sys.stderr)
    print(
        "  The release pipeline applies migrations in filename order and requires the\n"
        "  source ledger to extend what staging and production already applied; a\n"
        "  number at or below main's maximum can never apply in order.\n"
        "  Fix: ./run.sh reserve-migration --name <slug>, then git mv each file to the\n"
        "  reserved number, update every reference to the old filename (tests, SCAC\n"
        "  seals, tools/migrate.py atomic groups), and push. Renumbering is safe only\n"
        "  while the file has applied nowhere — this gate exists so that stays true.",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
