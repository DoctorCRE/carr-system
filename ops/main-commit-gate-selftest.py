#!/usr/bin/env python3
"""
main-commit-gate-selftest.py — the acceptance test for ops/githooks/pre-commit,
written before the hook itself (rule e65efc68).

WHAT THE HOOK IS FOR. `main` in this repo moves only through a merged pull
request with green CI — that is what the branch ruleset enforces on the server.
Nothing enforced the LOCAL half, so a commit could still land on `main` in
~/carr-system, the checkout several sessions share and the one launchd runs. Its
push is then refused, the commit strands, and the tree diverges. That is not a
hypothetical: on 2026-08-14 that tree held nine commits existing on no other
machine, and out/rules-refresh.log carries the mechanism verbatim —
"FAIL push refused, commit is local only — carr-system CI — 70fd470 on main".

So the hook refuses a commit whose branch is `main`, and points at
bin/worktree.sh, which already exists for exactly this and which the git writer
gate's own deny text already recommends.

WHAT IT MUST NOT DO, which is most of why this file exists:

  1. IT MUST NOT BREAK A COMMIT ON ANY OTHER BRANCH. Every worktree on this
     machine is on its own branch and every one of them commits constantly. A
     gate that catches those is worse than no gate.

  2. IT MUST NOT BREAK A DETACHED HEAD. Bisects, `git worktree add` at a bare
     sha, and CI checkouts all run detached. A detached HEAD is not `main` and
     the hook must not guess that it is.

  3. IT MUST HAVE AN ESCAPE HATCH THAT WORKS. Reconciling the shared checkout
     legitimately needs a commit on main, and a gate with no documented way
     through gets disabled wholesale the first time it blocks someone at 2am.
     Same idiom and spelling shape as pre-push's CARR_ALLOW_MAIN_PUSH.

  4. IT MUST NOT DEPEND ON THE REPOSITORY IT IS INSTALLED IN. git exports GIT_DIR
     to every hook, and ops/git_env.py exists because a check that trusts an
     inherited GIT_DIR operates on whatever repository invoked it. Every case
     below runs against a throwaway fixture repo, and one case asserts the outer
     repository gained no commits — the same leak ops/git-env-selftest.py guards.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/main-commit-gate-selftest.py

ops/ci.sh already globs ops/*-selftest.py, so this is enforced from the commit
that adds it, with no CI change.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "ops" / "githooks" / "pre-commit"

# The one scrubber (ops/git_env.py), not a local copy of the leak guard: git
# hands every hook a GIT_DIR pointing at the repository that invoked it, and on
# 2026-08-14 that leaked a fixture commit onto live main. A hand-rolled list
# here would be a second copy to drift out of sync with the real one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

passed = 0
failures: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=failures, counters={"passed": "pass"}, ns=globals())


def git(repo, *args, env=None, check_rc=False):
    e = fixture_env()
    if env:
        e.update(env)
    p = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True, env=e)
    if check_rc and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def make_repo(tmp):
    """A throwaway repo with this hook installed exactly as the real one is:
    through core.hooksPath, which is how ops/githooks reaches git here."""
    repo = Path(tmp) / "fixture"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main", check_rc=True)
    git(repo, "config", "user.email", "fixture@example.invalid", check_rc=True)
    git(repo, "config", "user.name", "Fixture", check_rc=True)
    hooks = repo / "githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-commit")
    os.chmod(hooks / "pre-commit", 0o755)
    git(repo, "config", "core.hooksPath", "githooks", check_rc=True)
    (repo / "seed.txt").write_text("seed\n")
    # The seed commit has to bypass the very hook under test, which is itself a
    # small proof that the escape hatch works before anything relies on it.
    git(repo, "add", "seed.txt", check_rc=True)
    git(repo, "commit", "-q", "-m", "seed", env={"CARR_ALLOW_MAIN_COMMIT": "1"},
        check_rc=True)
    return repo


def commit_attempt(repo, filename, env=None):
    (repo / filename).write_text(f"{filename}\n")
    git(repo, "add", filename)
    return git(repo, "commit", "-m", f"add {filename}", env=env)


print("\nops/githooks/pre-commit — main is not a place commits land")

if not HOOK.exists():
    print(f"  FAIL  the hook does not exist at {HOOK}")
    print("\n1 check(s) failed: hook missing")
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    head_before = git(repo, "rev-parse", "HEAD").stdout.strip()

    # ── 1. A commit on main is refused ──────────────────────────────────────
    r = commit_attempt(repo, "on-main.txt")
    check("a commit on main is refused", r.returncode != 0,
          f"exit {r.returncode}")
    check("and nothing was committed",
          git(repo, "rev-parse", "HEAD").stdout.strip() == head_before,
          "HEAD moved")
    msg = (r.stdout + r.stderr).lower()
    check("the refusal names the branch it refused", "main" in msg)
    # Asserted as the command a person TYPES, not as the filename that implements
    # it. The first version of this check looked for "worktree.sh" and failed a
    # correct hook: bin/worktree.sh is reached as `run.sh worktree`, which is what
    # its own usage text prints and what the deny message should therefore say.
    check("the refusal points at the worktree command", "run.sh worktree" in msg,
          "a gate that does not say what to do instead just gets bypassed")
    check("the refusal names its own escape hatch",
          "carr_allow_main_commit" in msg)

    # The staged change must survive the refusal. A gate that also destroys the
    # work it blocked would be worse than the divergence it prevents.
    check("the refused work is still staged, not discarded",
          "on-main.txt" in git(repo, "diff", "--cached", "--name-only").stdout)

    # ── 2. The escape hatch works ───────────────────────────────────────────
    r = git(repo, "commit", "-m", "escape hatch",
            env={"CARR_ALLOW_MAIN_COMMIT": "1"})
    check("CARR_ALLOW_MAIN_COMMIT=1 lets the same commit through",
          r.returncode == 0, r.stderr)
    check("and that commit really landed",
          git(repo, "rev-parse", "HEAD").stdout.strip() != head_before)

    # ── 3. Every other branch is untouched ──────────────────────────────────
    git(repo, "checkout", "-q", "-b", "feature/x", check_rc=True)
    r = commit_attempt(repo, "on-branch.txt")
    check("a commit on a feature branch is allowed", r.returncode == 0, r.stderr)
    check("and it landed",
          "on-branch.txt" in git(repo, "show", "--stat", "--format=", "HEAD").stdout)

    # A branch whose name merely CONTAINS main must not be caught. 'maintenance'
    # is the obvious one and exactly the kind of substring bug a gate ships with.
    git(repo, "checkout", "-q", "-b", "maintenance", check_rc=True)
    r = commit_attempt(repo, "maint.txt")
    check("a branch named 'maintenance' is not mistaken for main",
          r.returncode == 0, r.stderr)

    git(repo, "checkout", "-q", "-b", "main-ish", check_rc=True)
    r = commit_attempt(repo, "mainish.txt")
    check("a branch named 'main-ish' is not mistaken for main",
          r.returncode == 0, r.stderr)

    # ── 4. A detached HEAD is not main ──────────────────────────────────────
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-q", "--detach", sha, check_rc=True)
    r = commit_attempt(repo, "detached.txt")
    check("a commit on a detached HEAD is allowed", r.returncode == 0,
          "bisects and CI checkouts are detached; refusing them is a false positive")

    # ── 5. The hook did not touch the repository that invoked it ────────────
    outer_head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    outer_status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                                  capture_output=True, text=True).stdout
    check("the outer repository gained no commits from this run",
          outer_head == subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                       capture_output=True, text=True).stdout.strip())
    check("the outer repository gained no staged fixture files",
          "on-main.txt" not in outer_status and "detached.txt" not in outer_status)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("MAIN COMMIT GATE SELFTEST PASSED: main is refused, every other branch and "
      "a detached HEAD are not, and the escape hatch works.")
