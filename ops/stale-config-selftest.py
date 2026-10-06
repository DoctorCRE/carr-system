#!/usr/bin/env python3
"""
stale-config-selftest.py — acceptance test for ops/stale-config-check.py,
written before the check (rule e65efc68).

THE FAILURE IT PREVENTS, twice on 2026-08-14 and both times damaging.

  ops/ci.sh — pull request 60 added a type-check class that treated a missing
  mypy as SKIP. Pull request 65, cut BEFORE 60 existed, replaced the whole class
  with its own older copy that reported a false failure instead. It merged
  second and won. A machine without mypy then failed CI with "mypy found shape
  mistakes", which is both wrong and a wrong explanation.

  ops/config/gate-baseline.json — pull request 70 changed a gate and re-blessed
  its hash in the same commit, exactly as the baseline's own instructions
  require. A concurrent pull request cut before it blessed from the older base
  and merged after, carrying the pre-70 hash. Main then shipped a gate whose
  recorded hash did not match its own file, so EVERY session booted reporting
  GATE INTEGRITY FAILURE until it was re-blessed by hand.

WHY NOTHING CAUGHT EITHER. Each branch is internally consistent and passes CI on
its own. The conflict does not exist until the two are combined on main, and git
does not report a conflict because the second branch rewrote the whole region.
This is a lost update, not a merge conflict.

WHY THESE FILES SPECIFICALLY. They are GENERATED and rewritten wholesale by
tooling rather than hand-edited line by line, so two branches touching them
almost never conflict textually — they just silently overwrite each other.
ops/config/gate-baseline.json took THIRTY-SIX commits on main in twenty-four
hours. A hand-written source file would have conflicted and been noticed.

THE RULE THE CHECK ENFORCES: if your branch differs from origin/main for one
of these files, its newest upstream commit must already be in your history.
An exact committed copy of main is safe without matching commit ancestry.

WHAT IT MUST NOT DO, which is most of this file:
  - never fire on a branch that does not touch a watched file
  - never fire when the branch is already current for that file
  - never fire on main itself, where there is nothing to be stale against
  - fail OPEN when it cannot tell (no origin/main, shallow clone, not a repo),
    because a check that blocks every push when git is unavailable is worse than
    the lost update it guards
"""

import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHECK = os.path.join(REPO, "ops", "stale-config-check.py")

# Every git hook exports GIT_DIR, which outranks cwd and -C when git chooses
# a repository. The throwaway origin/clone this file builds under
# tempfile.TemporaryDirectory() would be vulnerable to exactly that if this
# selftest is itself invoked from a hook (e.g. ops/ci.sh under pre-push) —
# the 2026-08-13 incident ops/git_env.py documents. __file__'s directory is
# ops/, so this reaches the one scrub definition directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def git(repo, *args, must=False):
    # fixture_env() replaces a hand-rolled scrub dict that duplicated (and
    # under-covered — it was missing GIT_ALTERNATE_OBJECT_DIRECTORIES) the
    # list ops/git_env.py keeps in one place for exactly this reason.
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       env=fixture_env())
    if must and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def make_repo(tmp):
    """origin (bare) + a clone, with one watched file already committed."""
    origin = os.path.join(tmp, "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin],
                   capture_output=True, text=True, env=fixture_env())
    repo = os.path.join(tmp, "clone")
    git(tmp, "clone", "-q", origin, repo, must=True)
    git(repo, "config", "user.email", "t@example.invalid", must=True)
    git(repo, "config", "user.name", "t", must=True)
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "1"}}\n')
    write(repo, "readme.txt", "seed\n")
    git(repo, "add", "-A", must=True)
    git(repo, "commit", "-q", "-m", "seed", must=True)
    git(repo, "push", "-q", "origin", "main", must=True)
    git(repo, "fetch", "-q", "origin", "main", must=True)
    return origin, repo


def run(repo):
    # CHECK's own git() helper shells out to plain `git` with no env of its
    # own, so it inherits whatever we pass here — same GIT_DIR leak as the
    # git() helper above, one hop further down the stack.
    p = subprocess.run([sys.executable, CHECK], cwd=repo, capture_output=True,
                       text=True, timeout=60, env=fixture_env())
    return p.returncode, (p.stdout + p.stderr)


def advance_main(tmp, origin, content):
    """Somebody else lands a change to the watched file on main."""
    other = os.path.join(tmp, "other")
    if not os.path.exists(other):
        git(tmp, "clone", "-q", origin, other, must=True)
        git(other, "config", "user.email", "o@example.invalid", must=True)
        git(other, "config", "user.name", "o", must=True)
    git(other, "pull", "-q", "--ff-only", must=True)
    write(other, "ops/config/gate-baseline.json", content)
    git(other, "add", "ops/config/gate-baseline.json", must=True)
    git(other, "commit", "-q", "-m", "someone else re-blessed", must=True)
    git(other, "push", "-q", "origin", "main", must=True)


print("\nstale-config-check — you may not overwrite a generated file you have not seen")

if not os.path.exists(CHECK):
    print(f"  FAIL the check does not exist at {CHECK}")
    sys.exit(1)

# 1. THE REAL FAILURE: branch touches the file, main moved it since the branch cut.
with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "2"}}\n')      # someone else lands first
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "3"}}\n')
    git(repo, "add", "ops/config/gate-baseline.json", must=True)
    git(repo, "commit", "-q", "-m", "my bless", must=True)
    git(repo, "fetch", "-q", "origin", "main", must=True)
    rc, out = run(repo)
    check("a stale branch touching a watched file is refused", rc == 2, f"rc={rc}")
    check("the refusal names the file", "gate-baseline.json" in out, out[:200])
    check("the refusal says what to do", "rebase" in out.lower() or "merge" in out.lower(),
          out[:200])
    check("the refusal explains the damage, not just the rule",
          "overwrite" in out.lower(), out[:200])

# Cherry-picking main's exact bytes does not carry its commit ancestry. It
# cannot overwrite those bytes, but a subsequent local or main edit can.
with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    write(repo, "readme.txt", "branch-only work\n")
    git(repo, "add", "readme.txt", must=True)
    git(repo, "commit", "-q", "-m", "branch work", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "2"}}\n')
    git(repo, "fetch", "-q", "origin", "main", must=True)
    git(repo, "cherry-pick", "origin/main", must=True)
    check("copied source fixture lacks main commit ancestry",
          git(repo, "merge-base", "--is-ancestor", "origin/main", "HEAD").returncode != 0)
    rc, out = run(repo)
    check("byte-identical cherry-picked generated file is allowed", rc == 0,
          f"rc={rc}: {out[:200]}")
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "3"}}\n')
    git(repo, "add", "ops/config/gate-baseline.json", must=True)
    git(repo, "commit", "-q", "-m", "diverge after copy", must=True)
    rc, out = run(repo)
    check("a local difference after copying still refuses unseen ancestry", rc == 2,
          f"rc={rc}: {out[:200]}")

with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    write(repo, "readme.txt", "branch-only work\n")
    git(repo, "add", "readme.txt", must=True)
    git(repo, "commit", "-q", "-m", "branch work", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "2"}}\n')
    git(repo, "fetch", "-q", "origin", "main", must=True)
    git(repo, "cherry-pick", "origin/main", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "4"}}\n')
    git(repo, "fetch", "-q", "origin", "main", must=True)
    rc, out = run(repo)
    check("new upstream bytes after copying are still refused", rc == 2,
          f"rc={rc}: {out[:200]}")

# 2. UP TO DATE for that file: allowed, even though main moved for OTHER files.
with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "2"}}\n')
    git(repo, "fetch", "-q", "origin", "main", must=True)
    git(repo, "merge", "-q", "origin/main", "-m", "catch up", must=True)  # now current
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "3"}}\n')
    git(repo, "add", "ops/config/gate-baseline.json", must=True)
    git(repo, "commit", "-q", "-m", "my bless on top", must=True)
    rc, out = run(repo)
    check("a branch that already has main's latest change is allowed", rc == 0,
          f"rc={rc}: {out[:200]}")

# 3. NOT TOUCHING a watched file: never fires, however stale the branch is.
with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "2"}}\n')
    write(repo, "readme.txt", "unrelated edit\n")
    git(repo, "add", "readme.txt", must=True)
    git(repo, "commit", "-q", "-m", "unrelated", must=True)
    git(repo, "fetch", "-q", "origin", "main", must=True)
    rc, out = run(repo)
    check("a stale branch NOT touching a watched file is allowed", rc == 0,
          f"rc={rc}: {out[:200]}")
    check("...and says nothing", out.strip() == "" or "OK" in out, repr(out[:120]))

# 4. ON MAIN: nothing to be stale against.
with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "9"}}\n')
    git(repo, "add", "ops/config/gate-baseline.json", must=True)
    git(repo, "commit", "-q", "-m", "on main", must=True)
    rc, out = run(repo)
    check("on main itself the check does not fire", rc == 0, f"rc={rc}: {out[:200]}")

# 5. FAIL OPEN when it cannot tell. A check that blocks every push when git is
#    unavailable is worse than the lost update it guards.
with tempfile.TemporaryDirectory() as tmp:
    plain = os.path.join(tmp, "plain")
    os.makedirs(plain)
    p = subprocess.run([sys.executable, CHECK], cwd=plain, capture_output=True,
                       text=True, timeout=60, env=fixture_env())
    check("outside a git repository it never blocks", p.returncode == 0,
          f"rc={p.returncode}")

with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    git(repo, "remote", "remove", "origin", must=True)   # no origin/main to compare
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "3"}}\n')
    git(repo, "add", "ops/config/gate-baseline.json", must=True)
    git(repo, "commit", "-q", "-m", "no remote", must=True)
    rc, out = run(repo)
    check("with no origin/main to compare it never blocks", rc == 0,
          f"rc={rc}: {out[:200]}")

with tempfile.TemporaryDirectory() as tmp:
    origin, repo = make_repo(tmp)
    git(repo, "checkout", "-q", "-b", "mine", must=True)
    write(repo, "readme.txt", "branch work\n")
    git(repo, "add", "readme.txt", must=True)
    git(repo, "commit", "-q", "-m", "branch work", must=True)
    advance_main(tmp, origin, '{"gates": {"a": "2"}}\n')
    git(repo, "fetch", "-q", "origin", "main", must=True)
    git(repo, "cherry-pick", "origin/main", must=True)
    check("cherry-picked identical generated content is allowed", run(repo)[0] == 0)
    write(repo, "ops/config/gate-baseline.json", '{"gates": {"a": "3"}}\n')
    git(repo, "add", "ops/config/gate-baseline.json", must=True)
    git(repo, "commit", "-q", "-m", "stale overwrite", must=True)
    check("a differing stale overwrite still refuses after cherry-pick", run(repo)[0] == 2)

sys.exit(CHECKER.summary())
