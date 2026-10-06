#!/usr/bin/env python3
"""Proves a temp-repo git call is NOT hijacked by an inherited hook environment.

WHY THIS EXISTS — the bug it pins destroyed local main on 2026-08-14.

Git exports GIT_DIR (and usually GIT_INDEX_FILE) into every hook it runs. Those
variables OVERRIDE cwd for any child `git` process. ops/githooks/pre-push ran
ops/ci.sh directly, so CI inherited them, and every selftest that builds a
throwaway repository with tempfile.mkdtemp and calls git with cwd=<temp> was
silently operating on the LIVE repository instead.

The damage, found by a peer session: local main sitting on a commit whose tree held
THREE files, six commits ahead of origin, five of them selftest artifacts named
"seed", "seed unrelated" and one stamped +deadbeef, with all 818 paths staged as
additions — so any commit from any session would have swept the whole repository
under one message. Origin was never touched. Recovery was a mixed reset.

THE SELFTESTS WERE NEVER WRONG. The environment they inherited was. That is why the
fix is in the hook and not in the tests, and why this file asserts the property at
the environment level rather than re-checking anyone's sandboxing.

    .venv/bin/python ops/hook-env-isolation-selftest.py
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "ops", "githooks", "pre-push")
STRIPPED = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")


def hook_strips_git_env():
    """The hook must strip the git environment before it runs CI."""
    src = Path(HOOK).read_text(encoding="utf-8")
    # The env-stripping and the ci.sh invocation must be ONE logical statement.
    # Backslash continuations are followed explicitly, because a bare `env -u`
    # elsewhere in the file would satisfy a substring check and protect nothing —
    # which is the same shape as the bug this file exists to pin.
    joined = re.sub(r"\\\n\s*", " ", src)
    m = re.search(r"env(?:\s+-u\s+\w+)+[^\n]*ci\.sh", joined)
    missing = [v for v in STRIPPED if f"-u {v}" not in src]
    return (m is not None and not missing), missing


def hook_strips_git_env_for_jev_tolls():
    """The later Jev verifier can also launch temp-repo selftests."""
    src = Path(HOOK).read_text(encoding="utf-8")
    joined = re.sub(r"\\\n\s*", " ", src)
    match = re.search(r"env(?:\s+-u\s+\w+)+[^\n]*REPO_ROOT=\"\$REPO_ROOT\"[^\n]*TOLLS", joined)
    return match is not None and all(f"-u {name}" in match.group(0) for name in STRIPPED)


def temp_repo_is_hijacked_without_the_fix():
    """Demonstrate the failure mode itself, so the test proves a real hazard.

    With GIT_DIR pointed at repo A, a `git` run with cwd=B reports A's toplevel.
    That is the whole bug in one assertion.
    """
    a = tempfile.mkdtemp(prefix="hookenv-a-")
    b = tempfile.mkdtemp(prefix="hookenv-b-")
    for d in (a, b):
        subprocess.run(["git", "init", "-q", d], check=True, capture_output=True)
    env = dict(os.environ, GIT_DIR=os.path.join(a, ".git"))
    out = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=b,
                         env=env, capture_output=True, text=True).stdout.strip()
    hijacked = os.path.realpath(out).startswith(os.path.realpath(a))
    clean_env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
    out2 = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=b,
                          env=clean_env, capture_output=True, text=True).stdout.strip()
    isolated = not os.path.realpath(os.path.join(b, out2)).startswith(os.path.realpath(a))
    return hijacked, isolated


def main():
    failures = []

    ok, missing = hook_strips_git_env()
    if ok:
        print("ok   pre-push strips the git environment on the same statement that runs CI")
    else:
        failures.append(f"pre-push does not strip {missing or 'the git env on the ci.sh call'}")

    if hook_strips_git_env_for_jev_tolls():
        print("ok   pre-push also isolates the Jev toll verifier")
    else:
        failures.append("pre-push does not isolate the Jev toll verifier")

    hijacked, isolated = temp_repo_is_hijacked_without_the_fix()
    if hijacked:
        print("ok   demonstrated: with GIT_DIR set, a git call with cwd=<temp> targets the LIVE repo")
    else:
        failures.append("could not reproduce the hijack — this test no longer proves the hazard")
    if isolated:
        print("ok   demonstrated: with GIT_DIR stripped, the same call stays in its own repo")
    else:
        failures.append("stripping the git env did NOT isolate the temp repo")

    for f in failures:
        print(f"FAIL {f}")
    total = 4
    if failures:
        print(f"hook env isolation: {len(failures)} of {total} FAILED")
        return 1
    print(f"hook env isolation: {total}/{total} pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
