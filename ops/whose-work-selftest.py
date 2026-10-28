#!/usr/bin/env python3
"""ops/whose-work-selftest.py — acceptance test for bin/whose-work.py.

WHAT IT IS FOR. Rule 173119a8 binds at the moment a session is about to say
anything about uncommitted work, a dirty tree, or who is blocking whom: measure
against origin/<branch>, never against HEAD, and read the SIGN. Its own incident
was a session naming another session as the blocker off a HEAD-based diff — the
diff was accurate and the conclusion was backwards, because a local HEAD that is
BEHIND origin makes someone else's landed work look like your missing work.

Several sessions share one checkout here, so the question "is this mine, and is
it landed" comes up constantly and was answered by hand every time.

TESTS AGAINST REAL REPOSITORIES, never mocks: a bare remote plus working clones
in a temp dir, so ahead/behind/diverged are produced by git rather than asserted
about git. No network.

THE SIGN IS THE POINT. Ahead and behind are not symmetric and must never be
collapsed into one "out of sync" count: ahead means work that exists nowhere
else, behind means work someone else already landed. Confusing them is the
original incident.

RUN IT:
    python3 ops/whose-work-selftest.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

# The one scrubber (ops/git_env.py), not a local copy. GIT_DIR is exported by
# every git hook and overrides cwd for any child git process — the 2026-08-14
# incident where fixture commits landed on live main happened exactly this
# way, through a fixture repo built with plain cwd= and no scrub at all,
# which is what every git() call in this file did before this change.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "bin", "whose-work.py")

FAILED: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(detail="\n        {detail}", failures=FAILED)


def git(repo: str, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                       env=fixture_env())
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}:\n{p.stderr}")
    return p.stdout.strip()


def run(repo: str) -> dict:
    # bin/whose-work.py shells out to git itself; scrubbing here is what
    # keeps ITS git calls confined to the fixture too, not just this file's.
    p = subprocess.run([sys.executable, TOOL, "--repo", repo, "--json"],
                       capture_output=True, text=True, timeout=120, env=fixture_env())
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"_raw": p.stdout + p.stderr, "_rc": p.returncode}


print("whose-work-selftest — the sign is the answer, and it comes from origin")

check("the tool exists", os.path.exists(TOOL), TOOL)
if not os.path.exists(TOOL):
    sys.exit(1)

TMP = tempfile.mkdtemp(prefix="whose-work-selftest-")
try:
    bare = os.path.join(TMP, "origin.git")
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", bare],
                   capture_output=True, check=True, env=fixture_env())

    def clone(name: str) -> str:
        path = os.path.join(TMP, name)
        subprocess.run(["git", "clone", "-q", bare, path], capture_output=True,
                       check=True, env=fixture_env())
        git(path, "config", "user.email", "t@example.invalid")
        git(path, "config", "user.name", "t")
        return path

    a = clone("a")
    with open(os.path.join(a, "seed.txt"), "w") as fh:
        fh.write("seed\n")
    git(a, "add", "seed.txt")
    git(a, "commit", "-q", "-m", "seed")
    git(a, "push", "-q", "origin", "main")

    # ── in sync ────────────────────────────────────────────────────────────
    r = run(a)
    check("a synced clone reports ahead 0 / behind 0",
          r.get("ahead") == 0 and r.get("behind") == 0, str(r)[:250])
    check("...and says plainly that nothing is unlanded",
          r.get("unlanded") is False, str(r)[:250])

    # ── AHEAD: my own work exists nowhere else ─────────────────────────────
    with open(os.path.join(a, "mine.txt"), "w") as fh:
        fh.write("my fix\n")
    git(a, "add", "mine.txt")
    git(a, "commit", "-q", "-m", "my fix")
    r = run(a)
    check("a commit not pushed reads AHEAD, not behind",
          r.get("ahead") == 1 and r.get("behind") == 0, str(r)[:250])
    check("...and is named as work existing on no other machine",
          r.get("unlanded") is True, str(r)[:250])

    # ── BEHIND: someone else already landed work ───────────────────────────
    b = clone("b")
    with open(os.path.join(b, "theirs.txt"), "w") as fh:
        fh.write("their fix\n")
    git(b, "add", "theirs.txt")
    git(b, "commit", "-q", "-m", "their fix")
    git(b, "push", "-q", "origin", "main")

    r = run(a)
    check("THE ORIGINAL INCIDENT: another session's landed work reads BEHIND",
          r.get("behind") == 1, str(r)[:250])
    check("...and behind is never reported as my own unlanded work",
          r.get("ahead") == 1 and r.get("unlanded") is True, str(r)[:250])
    # DIVERGED is the case that must not collapse: one commit of mine unlanded
    # AND one of theirs unpulled, reported as two independent facts. An earlier
    # version of this assertion demanded the numbers DIFFER, which is wrong —
    # both are legitimately 1 here, and the property under test is that the two
    # are carried separately at all.
    check("a DIVERGED tree reports both counts independently, never as one number",
          r.get("ahead") == 1 and r.get("behind") == 1, str(r)[:250])

    # ── it FETCHES: a stale local ref must not produce a stale answer ───────
    c = clone("c")
    with open(os.path.join(b, "later.txt"), "w") as fh:
        fh.write("later\n")
    git(b, "add", "later.txt")
    git(b, "commit", "-q", "-m", "later")
    git(b, "push", "-q", "origin", "main")
    r = run(c)
    check("it fetches before measuring, so a never-fetched clone still sees behind",
          r.get("behind", 0) >= 1, str(r)[:250])

    # ── loose files are attributed, not just counted ───────────────────────
    with open(os.path.join(a, "seed.txt"), "a") as fh:
        fh.write("edited\n")
    r = run(a)
    check("a modified tracked file is listed by path",
          any("seed.txt" in str(x) for x in (r.get("loose") or [])), str(r)[:300])

    # ── a repo with no remote answers rather than erroring ─────────────────
    solo = os.path.join(TMP, "solo")
    os.makedirs(solo)
    git(solo, "init", "-q", "-b", "main")
    git(solo, "config", "user.email", "t@example.invalid")
    git(solo, "config", "user.name", "t")
    with open(os.path.join(solo, "x.txt"), "w") as fh:
        fh.write("x\n")
    git(solo, "add", "x.txt")
    git(solo, "commit", "-q", "-m", "x")
    r = run(solo)
    check("a repo with no remote says so instead of guessing",
          r.get("no_remote") is True, str(r)[:250])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

# ── the real repository ─────────────────────────────────────────────────────
# A worktree cut for one session sits on a branch that has never been pushed,
# so integer counts are the wrong thing to demand. What must always hold is a
# COHERENT answer: either both counts, or an explicit note saying why not.
r = run(REPO)
check("it answers coherently for the real repository",
      (isinstance(r.get("ahead"), int) and isinstance(r.get("behind"), int))
      or bool(r.get("note")) or r.get("no_remote") is True,
      str(r)[:300])
check("...and never reports a count alongside a reason it has none",
      not (r.get("note") and isinstance(r.get("ahead"), int)), str(r)[:300])

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for _label in FAILED:
        print(f"  - {_label}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
