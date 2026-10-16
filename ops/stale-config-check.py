#!/usr/bin/env python3
"""Refuse wholesale configuration rewrites based on an obsolete main revision."""
import os
import subprocess
import sys

# Generated files that tooling rewrites wholesale. A path belongs here only if a
# second writer would OVERWRITE rather than conflict — that is the whole test.
WATCHED = [
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
        "      #   db/schema.sql           -> bin/schema-snapshot.sh\n\n"
        "  Hand-merging a hash is how one of the two incidents happened. Regenerate.",
        file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
