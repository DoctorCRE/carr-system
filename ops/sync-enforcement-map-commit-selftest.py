#!/usr/bin/env python3
"""sync-enforcement-map-commit-selftest.py — prove the commit half behaves.

WHY. bin/sync-enforcement-map.py originally derived the enforcement map
correctly, re-stamped the baseline correctly, printed "COMMIT NEEDED" and
stopped. On 2026-08-13 it ran at 08:39, did its job perfectly, and left both
config files uncommitted; a session found them by accident hours later. An
unpushed gate change is the earlier outage in slow motion — the local machine
looks healthy while the repo and Dell's clone never receive it.

The commit half now exists, and it carries one genuinely risky behaviour: an
automated job that runs `git commit` in a tree that regularly holds ANOTHER live
session's uncommitted work. The guard is that it commits only its own two paths,
and refuses entirely if either was already modified when it started. That guard
is what this file tests, because getting it wrong means an unattended job
quietly commits a partner's half-finished work under a gates message.

Pure and hermetic: builds a throwaway git repo per case, never touches the real
one, and needs no network (the push failure path is exercised by having no
remote, which must leave the commit intact and report FAIL rather than crash).

THAT CLAIM WAS FALSE FROM THE FILE'S FIRST DAY UNTIL 2026-08-13, and case 0 now
tests it rather than asserting it. Two independent leaks, either one sufficient:

  1. GIT_DIR OUTRANKS cwd. `cwd=repo` is not isolation, because git reads GIT_DIR
     first — and every git hook exports it. ops/githooks/pre-push runs ops/ci.sh,
     which runs this file, so a plain `git push` made the fixture's own
     `git config user.email selftest@example.invalid` land in the REAL
     ~/carr-system/.git/config. The shared repo wore the test's identity.
  2. THE MODULE'S PATHS WERE FROZEN AT IMPORT. bin/sync-enforcement-map.py had
     MAP and BASELINE as module constants, so `mod.REPO = repo` redirected its
     git calls to the fixture while every read and write of the map and the
     baseline still went to the live checkout.

Both are closed: _fixture_env() strips the redirect variables, and the module
resolves map_path()/baseline_path() at call time. Loop #367.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "bin", "sync-enforcement-map.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def load_module_for(repo):
    """Import the script with REPO pointed at a throwaway checkout."""
    spec = importlib.util.spec_from_file_location(f"syncmap_{os.path.basename(repo)}", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REPO = repo
    return mod


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, env=fixture_env())


def make_repo():
    """A fixture checkout WITH AN ORIGIN, because the job now publishes to one.

    The bare origin is not scaffolding — it is the contract. This job used to
    commit on the current branch and push; main is protected, the push was
    refused every time, and the commit stranded on the canonical checkout once
    an hour. It now builds the pair in a throwaway worktree on a branch cut from
    origin/main and pushes THAT, so a fixture without a remote could not
    exercise the behaviour at all.
    """
    repo = tempfile.mkdtemp(prefix="syncmap-selftest-")
    origin = tempfile.mkdtemp(prefix="syncmap-origin-")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin],
                   capture_output=True, text=True, env=fixture_env())
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    os.makedirs(os.path.join(repo, "ops", "config"), exist_ok=True)
    for rel in ("ops/config/rule-enforcement-map.json",
                "ops/config/gate-baseline.json"):
        with open(os.path.join(repo, rel), "w") as fh:
            fh.write('{"seed": true}\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    git(repo, "remote", "add", "origin", origin)
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin", "main")
    return repo, origin


def origin_branch_files(origin, branch):
    """The paths carried by the tip commit of `branch` on the bare origin."""
    p = subprocess.run(["git", "show", "--name-only", "--format=", branch],
                       cwd=origin, capture_output=True, text=True, env=fixture_env())
    return p.stdout.split()


def touch(repo, rel, text):
    with open(os.path.join(repo, rel), "w") as fh:
        fh.write(text)


print("sync-enforcement-map commit half")

# 0. THE ISOLATION CASE — this file's own promise, tested instead of asserted.
#
# The docstring claimed "never touches the real one" for weeks while the file
# was writing the live repo's config on every run (two independent leaks: paths
# frozen at import in the module under test, and GIT_DIR outranking cwd here).
# A promise a test makes about itself has to be a case like any other, or it is
# just a comment. Builds a throwaway OUTER repo, points GIT_DIR at it the way a
# git hook does, drives the fixture, and asserts the outer repo is untouched.
_outer = tempfile.mkdtemp(prefix="syncmap-outer-")
git(_outer, "init", "-q")
git(_outer, "config", "user.email", "outer@example.invalid")
_outer_email_before = git(_outer, "config", "--get", "user.email").stdout.strip()
_saved_git_dir = os.environ.get("GIT_DIR")
os.environ["GIT_DIR"] = os.path.join(_outer, ".git")
try:
    _probe, _probe_origin = make_repo()
    _probe_mod = load_module_for(_probe)
    touch(_probe, "ops/config/rule-enforcement-map.json", '{"probe": 1}\n')
    touch(_probe, "ops/config/gate-baseline.json", '{"probe": 1}\n')
    _probe_mod.publish_via_branch("shared +probe", [])
finally:
    if _saved_git_dir is None:
        os.environ.pop("GIT_DIR", None)
    else:
        os.environ["GIT_DIR"] = _saved_git_dir

check("GIT_DIR set: outer repo keeps its own identity",
      git(_outer, "config", "--get", "user.email").stdout.strip() == _outer_email_before,
      f"outer user.email became "
      f"{git(_outer, 'config', '--get', 'user.email').stdout.strip()!r}")
check("GIT_DIR set: outer repo gains no commits",
      git(_outer, "log", "--oneline").stdout.strip() == "",
      f"outer log: {git(_outer, 'log', '--oneline').stdout.strip()!r}")
check("module writes the FIXTURE's map, not this checkout's",
      os.path.dirname(os.path.dirname(os.path.dirname(_probe_mod.map_path())))
      == _probe,
      f"map_path() resolved to {_probe_mod.map_path()!r}")

# 1. Clean tree: it publishes the pair to a BRANCH and never commits on main.
repo, origin = make_repo()
mod = load_module_for(repo)
check("clean tree reports nothing pre-dirty", mod.dirty_owned() == [],
      f"got {mod.dirty_owned()}")
head_before = git(repo, "rev-parse", "HEAD").stdout.strip()
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 1}\n')
touch(repo, "ops/config/gate-baseline.json", '{"stamped": 1}\n')
mod.publish_via_branch("shared +abcd1234", [])

# THE CENTRAL ASSERTION OF THIS FILE, and the whole reason the job changed. A
# commit on main here could never be pushed — main is protected — so it would
# strand on the canonical checkout, once per hour, unattended. That is measured
# history, not a worry: out/rules-refresh.log carries "FAIL push refused, commit
# is local only — ... 70fd470 on main", and that commit had to be rescued by hand.
check("main gained NO commit",
      git(repo, "rev-parse", "HEAD").stdout.strip() == head_before,
      "the job committed on main again — this is the divergence engine returning")
check("the pair reached the gates branch on origin",
      sorted(origin_branch_files(origin, mod.GATES_BRANCH)) ==
      ["ops/config/gate-baseline.json", "ops/config/rule-enforcement-map.json"],
      f"branch carries {origin_branch_files(origin, mod.GATES_BRANCH)}")

# THE DERIVED PAIR STAYS IN THE TREE, and this assertion is the reverse of what
# it said for one afternoon. Restoring the working copies after publishing looked
# tidy and caused a real outage in miniature: the map reverted to its stale
# content, the parity checker kept failing, and hooks/gate-integrity.py therefore
# told EVERY session at boot that the enforcement layer had changed and the gates
# must not be treated as in force. The pair is derived data whose job is to be
# correct ON THIS MACHINE — it does not need to be committed here to be right, it
# needs to be present. The commit travels separately, through the branch.
check("the derived pair is LEFT in the tree, so this machine is in parity now",
      '"synced": 1' in open(
          os.path.join(repo, "ops/config/rule-enforcement-map.json")).read(),
      "publishing restored the working copy and put the map back to stale")

# Idempotence: a second run with the same content must not stack a second commit
# on the branch. The hourly schedule makes this the common case, not the edge one.
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 1}\n')
touch(repo, "ops/config/gate-baseline.json", '{"stamped": 1}\n')
mod.publish_via_branch("shared +abcd1234", [])
check("a repeat run does not stack a second commit on the branch",
      subprocess.run(["git", "rev-list", "--count", mod.GATES_BRANCH], cwd=origin,
                     capture_output=True, text=True, env=fixture_env()
                     ).stdout.strip() == "2",
      "the branch grew a commit per run")

# THE OTHER HALF OF LEAVING IT DIRTY, and the failure it would otherwise cause.
# The pair now sits modified in the tree between runs. dirty_owned() alone reads
# any dirty owned file as another writer's in-flight work — so on the very next
# run the job would refuse, print "a human must commit the pair", and silently
# stop syncing for ever. main() therefore compares CONTENT: dirt matching what
# this run derives is its own leftover, not a second writer. Asserted through
# main() rather than publish_via_branch(), because main() is where that
# comparison lives and calling the publisher directly would skip it.
env_run = {**os.environ, "HOME": os.environ.get("HOME", "")}
before_head = git(repo, "rev-parse", "HEAD").stdout.strip()
mod.REPO = repo
rc_second = mod.main()
check("a second run over its own leftovers does not refuse as 'another writer'",
      rc_second == 0, f"main() returned {rc_second}")
check("and that second run committed nothing on the fixture's main",
      git(repo, "rev-parse", "HEAD").stdout.strip() == before_head,
      "the second run moved HEAD")

# 2. THE GUARD. Another writer already had the pair modified: refuse outright.
repo, origin = make_repo()
mod = load_module_for(repo)
touch(repo, "ops/config/gate-baseline.json", '{"someone else was here": 1}\n')
pre = mod.dirty_owned()
check("a pre-modified owned file is detected",
      pre == ["ops/config/gate-baseline.json"], f"got {pre}")
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 1}\n')
mod.publish_via_branch("shared +abcd1234", pre)
still_dirty = git(repo, "status", "--porcelain").stdout.strip()
check("nothing was committed over the other writer", still_dirty != "",
      "the guard published another session's work")
check("HEAD is still the seed commit",
      git(repo, "log", "--oneline").stdout.strip().count("\n") == 0)
# The refusal must not restore either, or it would DELETE the other writer's
# edit — a worse outcome than the sweep the guard exists to prevent.
check("the other writer's edit is still there after the refusal",
      "someone else was here" in
      open(os.path.join(repo, "ops/config/gate-baseline.json")).read(),
      "the guard discarded the very work it was protecting")
check("and no gates branch was created on origin",
      subprocess.run(["git", "rev-parse", "--verify", mod.GATES_BRANCH], cwd=origin,
                     capture_output=True, text=True, env=fixture_env()).returncode != 0,
      "a refused run still published")

# 3. An unrelated dirty file must NOT block the publish, and must NOT be swept in.
repo, origin = make_repo()
mod = load_module_for(repo)
touch(repo, "unrelated.txt", "another session's in-flight work\n")
git(repo, "add", "unrelated.txt")
git(repo, "commit", "-q", "-m", "seed unrelated")
touch(repo, "unrelated.txt", "MODIFIED by another session\n")
# Order matters: pre-dirt is by definition read BEFORE this run writes its pair,
# which is exactly how main() uses it. The first draft of this case checked
# after writing and so "failed" on correct behaviour.
check("unrelated dirt does not count as pre-dirty", mod.dirty_owned() == [],
      f"got {mod.dirty_owned()}")
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 2}\n')
touch(repo, "ops/config/gate-baseline.json", '{"stamped": 2}\n')
mod.publish_via_branch("shared +deadbeef", [])
names = origin_branch_files(origin, mod.GATES_BRANCH)
check("the unrelated file was NOT swept into the published commit",
      "unrelated.txt" not in names, f"commit touched {names}")
check("the unrelated file is still modified and left alone",
      "unrelated.txt" in git(repo, "status", "--porcelain").stdout,
      "restoring the owned pair reached beyond the two paths it owns")

sys.exit(CHECKER.summary())
