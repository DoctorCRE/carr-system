#!/usr/bin/env python3
"""git-env-selftest.py — prove ops/git_env.py actually confines git.

This does not test the helper's return value in the abstract. It builds a real
victim repository, points GIT_DIR at it the way a git hook does, then drives
real git commands against a SEPARATE fixture and asserts the victim was not
touched. That is the only property anyone cares about, and it is the property
three selftests claimed in prose while not having it.

    .venv/bin/python ops/git-env-selftest.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env, scrubbed_env, GIT_LOCATION_VARS  # noqa: E402

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def make_repo(prefix, email):
    repo = tempfile.mkdtemp(prefix=prefix)
    env = fixture_env()
    for args in (["init", "-q"],
                 ["config", "user.email", email],
                 ["config", "user.name", "fixture"]):
        subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True)
    with open(os.path.join(repo, "f.txt"), "w") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, env=env, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, env=env,
                   capture_output=True)
    return repo


def git_out(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True,
                          text=True).stdout.strip()


print("git_env isolation")

# The victim stands in for ~/carr-system: a real repo that must not move.
victim = make_repo("gitenv-victim-", "victim@example.invalid")
victim_head = git_out(victim, "rev-parse", "HEAD", env=fixture_env())
victim_email = git_out(victim, "config", "--get", "user.email", env=fixture_env())

# THE CONTROL. Without the helper, cwd is not isolation — this is the bug, and
# if this case ever stops failing to isolate, the whole file is testing nothing.
hostile = dict(os.environ)
hostile["GIT_DIR"] = os.path.join(victim, ".git")
naive = tempfile.mkdtemp(prefix="gitenv-naive-")
subprocess.run(["git", "init", "-q"], cwd=naive, env=hostile, capture_output=True)
subprocess.run(["git", "config", "user.email", "leaked@example.invalid"],
               cwd=naive, env=hostile, capture_output=True)
leaked = git_out(victim, "config", "--get", "user.email", env=fixture_env())
check("CONTROL: an unscrubbed env DOES leak (proves this test can detect it)",
      leaked == "leaked@example.invalid",
      f"victim email is {leaked!r} — expected the leak; if this passes cleanly "
      f"the control is broken and every case below is vacuous")

# Put the victim's identity back before testing the real thing.
subprocess.run(["git", "config", "user.email", victim_email], cwd=victim,
               env=fixture_env(), capture_output=True)

# THE ACTUAL CASES. Same hostile environment, routed through the helper.
fixture = tempfile.mkdtemp(prefix="gitenv-fixture-")
fenv = fixture_env(hostile)
subprocess.run(["git", "init", "-q"], cwd=fixture, env=fenv, capture_output=True)
# BOTH halves of the identity, and the omission of user.name here failed on the
# ubuntu runner while passing on macOS. fixture_env() sets GIT_CONFIG_GLOBAL to
# /dev/null and GIT_CONFIG_NOSYSTEM, which is the point of it — so there is no
# global identity to fall back on, and git's auto-detection from the host account
# succeeds on a Mac and does not on a bare runner. A test for portability that
# itself depended on the host is the same defect it exists to catch.
subprocess.run(["git", "config", "user.email", "fixture@example.invalid"],
               cwd=fixture, env=fenv, capture_output=True)
subprocess.run(["git", "config", "user.name", "fixture"],
               cwd=fixture, env=fenv, capture_output=True)
with open(os.path.join(fixture, "x.txt"), "w") as fh:
    fh.write("x\n")
subprocess.run(["git", "add", "x.txt"], cwd=fixture, env=fenv, capture_output=True)
subprocess.run(["git", "commit", "-q", "-m", "fixture commit"], cwd=fixture,
               env=fenv, capture_output=True)

check("fixture_env: victim keeps its identity under a hostile GIT_DIR",
      git_out(victim, "config", "--get", "user.email", env=fixture_env()) == victim_email,
      f"became {git_out(victim, 'config', '--get', 'user.email', env=fixture_env())!r}")
check("fixture_env: victim gains no commits",
      git_out(victim, "rev-parse", "HEAD", env=fixture_env()) == victim_head)
check("fixture_env: the fixture got the commit instead",
      git_out(fixture, "log", "--oneline", env=fenv) != "")
check("fixture_env: victim's index is untouched",
      git_out(victim, "diff", "--cached", "--name-only", env=fixture_env()) == "")

# scrubbed_env keeps identity (a real commit needs a real author) but must still
# refuse to be relocated.
senv = scrubbed_env(hostile)
check("scrubbed_env: drops every location variable",
      all(v not in senv for v in GIT_LOCATION_VARS),
      f"left: {[v for v in GIT_LOCATION_VARS if v in senv]}")
check("scrubbed_env: KEEPS identity config (unlike fixture_env)",
      "GIT_CONFIG_NOSYSTEM" not in senv)

# The GIT_CONFIG_COUNT path: key/value pairs can set core.worktree and relocate
# a call with GIT_DIR never appearing at all.
sneaky = dict(os.environ)
sneaky["GIT_CONFIG_COUNT"] = "1"
sneaky["GIT_CONFIG_KEY_0"] = "core.worktree"
sneaky["GIT_CONFIG_VALUE_0"] = victim
cleaned = scrubbed_env(sneaky)
check("scrubbed_env: removes GIT_CONFIG_COUNT and its key/value pairs",
      all(k not in cleaned for k in
          ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")),
      f"left: {[k for k in ('GIT_CONFIG_COUNT','GIT_CONFIG_KEY_0','GIT_CONFIG_VALUE_0') if k in cleaned]}")

# THE ANTI-DRIFT CASE. ops/githooks/pre-push keeps its own literal `env -u ...`
# list, deliberately: ops/hook-env-isolation-selftest.py checks that the
# stripping and the ci.sh call are one statically readable statement, which a
# variable would defeat, and the push path should not depend on python. The cost
# of a second copy is drift, and drift here is SILENT — nothing fails, the wrong
# repository is simply modified. So the copy is allowed and the drift is not:
# this fails the moment the hook stops covering what git_env.py knows about.
_hook = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "ops", "githooks", "pre-push")
if os.path.exists(_hook):
    _src = open(_hook).read()
    _uncovered = [v for v in GIT_LOCATION_VARS if f"-u {v}" not in _src]
    check("ops/githooks/pre-push covers every location var git_env.py knows",
          not _uncovered,
          f"hook never unsets: {', '.join(_uncovered)} — add them to the `env -u` "
          f"line that runs ci.sh, or drop them from GIT_LOCATION_VARS")
else:
    check("ops/githooks/pre-push is present to check", False,
          f"expected it at {_hook}")

sys.exit(CHECKER.summary())
