#!/usr/bin/env python3
"""worktree-attribution-selftest.py — prove ops/worktree-attribution.py names the
right session, and says UNKNOWN rather than guessing when it cannot.

WHY A HERMETIC FIXTURE AND NOT THE LIVE MACHINE. The first attempt at proving
this ran against the real worktrees on Joe's Mac: pick a file another session has
modified, attribute it, check the name. It passed once and then failed, because
between the two git calls that session committed and its tree went clean. With
thirty-five concurrent worktrees the live machine is not a fixture, it is
weather. So this builds its own repository with its own worktrees and asserts
against a state it controls.

The fixture is built OUTSIDE the checkout with ops/git_env.fixture_env(), per
ops/selftest-git-isolation-check.py: a test that creates a git repository must
scrub the location variables, because GIT_DIR is exported into every hook and
outranks cwd. That is not hypothetical here — this file is run BY ops/ci.sh,
which is run BY ops/githooks/pre-push, which is exactly the path that put 801
staged files on the shared main on 2026-08-13.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env                                # noqa: E402

# The hyphen in the filename means it cannot be imported by name; the asserts
# are for the type checker, and they are also the honest failure if the file
# moves -- better than an AttributeError sixty lines later.
_spec = importlib.util.spec_from_file_location(
    "worktree_attribution", ROOT / "ops" / "worktree-attribution.py")
assert _spec is not None and _spec.loader is not None, \
    "ops/worktree-attribution.py is missing or unreadable"
wa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wa)

FAILURES: list[str] = []
BEFORE: tuple = ()


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, check=True)


def build(tmp):
    """A canonical repo with two worktrees, each on its own branch."""
    env = fixture_env()
    canonical = tmp / "canonical"
    canonical.mkdir()
    git(canonical, "init", "-q", "-b", "main", env=env)
    git(canonical, "config", "user.email", "fixture@example.invalid", env=env)
    git(canonical, "config", "user.name", "fixture", env=env)
    (canonical / "shared.txt").write_text("on main\n")
    (canonical / "ops").mkdir()
    (canonical / "ops" / "tracked.txt").write_text("tracked everywhere\n")
    git(canonical, "add", "shared.txt", "ops/tracked.txt", env=env)
    git(canonical, "commit", "-qm", "base", env=env)
    # origin/main must resolve for the "branch" basis; a self-remote is the
    # cheapest way to give the fixture one without a network.
    git(canonical, "remote", "add", "origin", str(canonical), env=env)
    git(canonical, "fetch", "-q", "origin", env=env)

    trees = tmp / "trees"
    trees.mkdir()
    for name in ("alpha-session", "beta-session"):
        git(canonical, "worktree", "add", "-q", "-b", name,
            str(trees / name), env=env)
    return canonical, trees, env


def real_state():
    """HEAD, tracked state and index of the REAL checkout this test runs in."""
    def q(*args):
        r = subprocess.run(["git", "-C", str(ROOT), *args],
                           capture_output=True, text=True)
        return r.stdout
    return (q("rev-parse", "HEAD"),
            q("status", "--porcelain", "--untracked-files=no"),
            q("diff", "--cached", "--name-only"))


def main():
    global BEFORE
    BEFORE = real_state()
    with tempfile.TemporaryDirectory(prefix="wt-attr-") as td:
        tmp = pathlib.Path(td).resolve()
        canonical, trees, env = build(tmp)
        alpha, beta = trees / "alpha-session", trees / "beta-session"

        def attribute(rel):
            ts = wa.worktrees_of(canonical)
            rels = [rel]
            for t in ts:
                t["_dirty"] = wa.dirty_paths(t["worktree"], rels)
            return wa.attribute(rel, ts, canonical)

        # 1. UNTRACKED IN A PEER. The incident shape: a file sitting in canonical
        #    that another session is working on.
        (canonical / "migration.sql").write_text("-- orphan in canonical\n")
        (alpha / "migration.sql").write_text("-- orphan in canonical\n")
        rec = attribute("migration.sql")
        check("untracked peer copy is attributed",
              rec["session"] == "alpha-session", rec)
        check("byte-identical copy is reported as the stronger basis",
              rec["basis"] == "dirty+content", rec)

        # 2. DIFFERENT BYTES still attributes, at the weaker basis.
        (beta / "other.sql").write_text("beta's version\n")
        (canonical / "other.sql").write_text("canonical's different version\n")
        rec = attribute("other.sql")
        check("differing peer copy still names the session",
              rec["session"] == "beta-session", rec)
        check("differing copy is the weaker 'dirty' basis",
              rec["basis"] == "dirty", rec)

        # 3. A TRACKED, CLEAN FILE IS NOT ATTRIBUTED TO ANYONE. This is the
        #    regression that made the first version useless: every worktree is a
        #    full checkout, so "this path exists over there" named a random peer
        #    for every question and named it confidently.
        rec = attribute("ops/tracked.txt")
        check("a clean tracked file is NOT attributed to a random worktree",
              rec["basis"] == "unknown", rec)

        # 4. MODIFIED (not untracked) in a peer is still that peer's work.
        (beta / "shared.txt").write_text("beta is editing this\n")
        rec = attribute("shared.txt")
        check("a peer's MODIFIED tracked file is attributed",
              rec["session"] == "beta-session" and rec["basis"] == "dirty", rec)

        # 5. COMMITTED on a peer branch, absent from origin/main, peer tree clean.
        (alpha / "committed-only.txt").write_text("landed on alpha\n")
        git(alpha, "add", "committed-only.txt", env=env)
        git(alpha, "commit", "-qm", "alpha adds a file", env=env)
        rec = attribute("committed-only.txt")
        check("a file committed only on a peer branch is attributed",
              rec["session"] == "alpha-session" and rec["basis"] == "branch", rec)

        # 6. HONEST UNKNOWN. No worktree has it at all.
        (canonical / "nobody.txt").write_text("no session claims this\n")
        rec = attribute("nobody.txt")
        check("a file no worktree claims reports UNKNOWN, not a guess",
              rec["basis"] == "unknown" and rec["session"] is None, rec)
        check("the unknown answer still carries the mtime",
              "last modified" in rec["detail"], rec)

        # 6b. A PUSHER'S NEW FILE UNDER AN INHERITED GIT_DIR. Git exports
        #     GIT_DIR into ops/githooks/pre-push; inherited, it made `git -C beta
        #     status` read alpha's index against beta's disk and report alpha's
        #     new file as " D" in beta (2026-09-23, headroom false positive).
        import os
        (alpha / "pushed-new.py").write_text("print('new')\n")
        git(alpha, "add", "pushed-new.py", env=env)
        git(alpha, "commit", "-qm", "alpha adds pushed-new.py", env=env)
        alpha_gitdir = git(alpha, "rev-parse", "--path-format=absolute",
                           "--git-dir", env=env).stdout.strip()
        saved = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = alpha_gitdir
        try:
            beta_dirty = wa.dirty_paths(str(beta), ["pushed-new.py"])
        finally:
            if saved is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = saved
        check("an inherited GIT_DIR does not make a peer 'own' the pusher's new file",
              beta_dirty == {}, beta_dirty)

        # 7. THE REPORTER NEVER FAILS THE COMMAND IT EXPLAINS.
        rc = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "worktree-attribution.py"),
             "no/such/path/anywhere.txt"],
            capture_output=True, text=True).returncode
        check("an unattributable path still exits 0", rc == 0, f"rc={rc}")

        # 8. The fixture must not have touched the real repository. Compared
        #    against a snapshot taken BEFORE the fixture was built -- the first
        #    version of this check was `... or True`, which is not an assertion,
        #    it is a green line.
        check("the fixture did not move the real checkout",
              real_state() == BEFORE,
              "the real checkout's HEAD/index/tracked state changed")

    if FAILURES:
        print(f"\nworktree-attribution selftest: {len(FAILURES)} FAILED: "
              + ", ".join(FAILURES))
        return 1
    print("\nworktree-attribution selftest: attribution names the owning "
          "session on four bases and says UNKNOWN rather than guessing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
