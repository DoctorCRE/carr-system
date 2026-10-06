#!/usr/bin/env python3
"""stale-config-check.py — you may not overwrite a generated file you have not seen.

THE FAILURE, twice on 2026-08-14 and damaging both times.

  ops/ci.sh. Pull request 60 added a type-check class that treated a missing
  mypy as SKIP. Pull request 65, cut BEFORE 60 existed, replaced the whole class
  with its own older copy that reported a false failure instead — and merged
  second, so it won. A machine without mypy then failed CI with "mypy found
  shape mistakes", which is wrong and misdirects whoever reads it.

  ops/config/gate-baseline.json. Pull request 70 changed a gate and re-blessed
  its hash in the same commit, exactly as the baseline's own instructions
  require. A concurrent pull request cut before it blessed from the older base
  and merged after, carrying the pre-70 hash. Main then shipped a gate whose
  recorded hash did not match its own file, so every session booted into GATE
  INTEGRITY FAILURE until somebody re-blessed it by hand.

WHY NOTHING CAUGHT EITHER, and why this is not solvable by reviewing harder.
Each branch is internally consistent and passes CI alone. The damage does not
exist until the two are combined on main, and git reports no conflict because
the second branch rewrote the whole region rather than editing around it. This
is a LOST UPDATE, not a merge conflict.

WHY ONLY THESE FILES. They are GENERATED — rewritten wholesale by tooling rather
than edited line by line — so two branches touching them almost never conflict
textually; they silently overwrite each other. ops/config/gate-baseline.json
took THIRTY-SIX commits on main in twenty-four hours. A hand-written source file
would have conflicted loudly and been noticed.

THE RULE: if your branch differs from origin/main for a watched file, the
newest commit on origin/main touching that file must already be in your
history. An exact copy of main cannot overwrite unseen bytes, even when
source was synchronized by cherry-pick rather than a merge.

WHAT THIS DOES NOT CLOSE, stated plainly because a guard oversold is worse than
none. It runs when CI runs. If main gains a new commit to a watched file AFTER
this passes and BEFORE the merge button, the same lost update can still happen in
that window. Closing it completely needs GitHub's own "require branches to be up
to date before merging" on the ruleset, which forces every pull request to
rebase whenever main moves at all — real protection at the cost of constant
churn across every branch, and a call for Joe rather than for this file. This
check buys the common case, which is a branch cut hours ago against a file that
changes many times a day.
"""
import os
import subprocess
import sys

# Generated files that tooling rewrites wholesale. A path belongs here only if a
# second writer would OVERWRITE rather than conflict — that is the whole test.
WATCHED = [
    "ops/config/gate-baseline.json",
    "ops/config/hooks.json",
    "ops/config/rule-enforcement-map.json",
    "ops/config/services.json",
    "db/schema.sql",
]

BASE = os.environ.get("CARR_STALE_CONFIG_BASE", "origin/main")


def git(*args):
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip()


def main() -> int:
    rc, _ = git("rev-parse", "--git-dir")
    if rc != 0:
        return 0                                  # not a repository: not our business

    rc, base_sha = git("rev-parse", "--verify", f"{BASE}^{{commit}}")
    if rc != 0 or not base_sha:
        return 0                                  # nothing to compare against

    rc, head = git("rev-parse", "HEAD")
    if rc != 0:
        return 0
    if head == base_sha:
        return 0                                  # on main itself: nothing to be stale against

    rc, merge_base = git("merge-base", "HEAD", base_sha)
    if rc != 0 or not merge_base:
        return 0

    # Already contains the tip of main: current by definition.
    if git("merge-base", "--is-ancestor", base_sha, "HEAD")[0] == 0:
        return 0

    rc, changed = git("diff", "--name-only", f"{merge_base}..HEAD")
    if rc != 0:
        return 0
    touched = [p for p in changed.splitlines() if p.strip() in WATCHED]
    if not touched:
        return 0                                  # the quiet, common case

    stale = []
    for path in touched:
        # A source copy can retain different ancestry while matching main.
        # Compare committed bytes/mode, with no external diff or text conversion;
        # errors and differences still take the existing ancestry-check path.
        if git("diff", "--quiet", "--no-ext-diff", "--no-textconv",
               base_sha, "HEAD", "--", path)[0] == 0:
            continue
        # The newest commit on main touching THIS path.
        rc, newest = git("rev-list", "-1", base_sha, "--", path)
        if rc != 0 or not newest:
            continue                              # main has never touched it
        # Already in our history? Then we are building on it, not over it.
        if git("merge-base", "--is-ancestor", newest, "HEAD")[0] == 0:
            continue
        rc, subject = git("log", "-1", "--format=%h %s", newest)
        stale.append((path, subject if rc == 0 else newest[:9]))

    if not stale:
        return 0

    lines = "\n".join(f"      {p}\n          main has: {s}" for p, s in stale)
    print(
        "STALE GENERATED CONFIG — this branch would overwrite work it never saw:\n\n"
        f"{lines}\n\n"
        "  These files are written wholesale by tooling, so a second writer does\n"
        "  not get a merge conflict — it silently replaces the first. Two pull\n"
        "  requests did exactly that on 2026-08-14: one reverted a CI class to an\n"
        "  older copy, the other restored a stale gate hash and left main shipping\n"
        "  a gate whose recorded hash did not match its own file. Every session\n"
        "  booted reporting the gates were not in force until it was fixed by hand.\n\n"
        "  Both branches passed CI on their own. The damage only exists once they\n"
        "  are combined, which is why this check and not review catches it.\n\n"
        "  FIX — take main's version first, then redo your change on top:\n\n"
        f"      git fetch origin main && git merge {BASE}\n"
        "      # then re-run whatever GENERATES the file, rather than hand-merging it:\n"
        "      #   gate-baseline.json      -> hooks/gate-integrity.py --bless <gate>\n"
        "      #   rule-enforcement-map    -> bin/sync-enforcement-map.py\n"
        "      #   db/schema.sql           -> bin/schema-snapshot.sh\n\n"
        "  Hand-merging a hash is how one of the two incidents happened. Regenerate.",
        file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
