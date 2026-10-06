#!/usr/bin/env python3
"""ci-skip-receipt-selftest.py — prove CARR_SKIP_CI now needs a reason and
leaves a receipt (bypass audit item 10, 2026-09-24).

Same philosophy as ops/guard-selftest.py: run the REAL ops/githooks/pre-push
file as a subprocess, fed the same stdin git streams a pre-push hook, and read
the outcome off its exit code, stderr, and the receipt file it writes — not by
importing and calling functions inside it. A leaked/reordered hooks.hooksPath
would falsify a version of this test that only checked the script's logic in
isolation; this one checks the artifact.

The main-branch owner check is unrelated to what this file proves, so every
case here pushes a feature branch, which that check ignores outright.

GIT_DIR SCRUB (ops/selftest-git-isolation-check.py's rule, rule set 2026-08-14).
git exports GIT_DIR/GIT_INDEX_FILE/GIT_CONFIG_* into every hook, and they
OVERRIDE cwd for any child git process -- a fixture built with cwd=<tempdir>
and no scrub can land in whatever repository GIT_DIR names instead of the
throwaway sandbox. Every git call here, including the pre-push subprocess
itself (which shells out to git internally), runs under ops/git_env.py's
fixture_env(), which also refuses to inherit a real git identity.
"""
from collections.abc import Callable
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRE_PUSH_SRC = os.path.join(REPO, "ops", "githooks", "pre-push")

ZERO = "0" * 40
FEATURE_REFS = f"{'a' * 40} {ZERO} refs/heads/some-feature {ZERO}\n"


def make_sandbox(ci_exit=0):
    """A throwaway git repo with a stub ops/ci.sh, so this never runs the
    real (slow, Postgres-needing) suite -- only the CARR_SKIP_CI decision
    logic and receipt-writing this PR added."""
    d = tempfile.mkdtemp(prefix="ci-skip-receipt-selftest-")
    env = fixture_env()
    subprocess.run(["git", "init", "-q"], cwd=d, env=env, check=True)
    subprocess.run(["git", "config", "user.email", "sandbox@example.invalid"], cwd=d, env=env, check=True)
    subprocess.run(["git", "config", "user.name", "sandbox"], cwd=d, env=env, check=True)
    os.makedirs(os.path.join(d, "ops", "githooks"), exist_ok=True)
    dest = os.path.join(d, "ops", "githooks", "pre-push")
    shutil.copy(PRE_PUSH_SRC, dest)
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC)
    ci_path = os.path.join(d, "ops", "ci.sh")
    with open(ci_path, "w", encoding="utf-8") as fh:
        fh.write(f"#!/bin/sh\nexit {ci_exit}\n")
    os.chmod(ci_path, 0o755)
    # one commit so HEAD/branch resolve
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("sandbox\n")
    subprocess.run(["git", "add", "README.md"], cwd=d, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, env=env, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "some-feature"], cwd=d, env=env, check=True)
    return d


def run(d, env_extra):
    env = fixture_env()
    env.pop("CARR_SKIP_CI", None)
    env.pop("CARR_SKIP_CI_REASON", None)
    env.pop("CARR_ALLOW_MAIN_PUSH", None)
    env.update(env_extra)
    p = subprocess.run(["sh", "ops/githooks/pre-push"], cwd=d, input=FEATURE_REFS,
                        capture_output=True, text=True, env=env, timeout=30)
    return p


def receipts(d):
    path = os.path.join(d, "out", "ci-skip-receipts.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


CASES: list[Callable[[], None]] = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def skip_without_reason_is_ignored_and_checks_run():
    d = make_sandbox(ci_exit=0)
    try:
        p = run(d, {"CARR_SKIP_CI": "1"})
        assert "requires a non-empty" in p.stderr, p.stderr
        assert "quality checks skipped" not in p.stderr, p.stderr
        assert not receipts(d), "no receipt should be written when the skip is refused"
        assert p.returncode == 0, p.stderr  # stub ci.sh exits 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case
def skip_with_whitespace_only_reason_is_rejected():
    """Redesign item 3 (Opus review, 2026-09-24): a reason of only spaces or
    tabs is not a reason."""
    d = make_sandbox(ci_exit=0)
    try:
        p = run(d, {"CARR_SKIP_CI": "1", "CARR_SKIP_CI_REASON": "   \t  "})
        assert "requires a non-empty" in p.stderr, p.stderr
        assert "quality checks skipped" not in p.stderr, p.stderr
        assert not receipts(d)
        assert p.returncode == 0, p.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case
def skip_fails_closed_when_the_receipt_cannot_be_written():
    """Redesign item 3: no receipt, no skip. Simulated by pre-creating
    out/ci-skip-receipts.jsonl as a DIRECTORY, so the append (`>>`) fails."""
    d = make_sandbox(ci_exit=0)
    try:
        out_dir = os.path.join(d, "out")
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(os.path.join(out_dir, "ci-skip-receipts.jsonl"), exist_ok=True)
        p = run(d, {"CARR_SKIP_CI": "1", "CARR_SKIP_CI_REASON": "should be refused"})
        assert "could not write the skip receipt" in p.stderr, p.stderr
        assert "quality checks skipped" not in p.stderr, p.stderr
        # ci_exit=0 here, so the fallback run of the real checks passes.
        assert p.returncode == 0, p.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case
def skip_with_reason_is_honoured_and_receipted():
    d = make_sandbox(ci_exit=0)
    try:
        p = run(d, {"CARR_SKIP_CI": "1", "CARR_SKIP_CI_REASON": "laptop has no postgres today"})
        assert "quality checks skipped" in p.stderr, p.stderr
        assert "laptop has no postgres today" in p.stderr, p.stderr
        rows = receipts(d)
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["reason"] == "laptop has no postgres today", row
        assert row["branch"] == "some-feature", row
        assert row["head"], row
        assert row["ts"], row
        assert p.returncode == 0, p.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case
def failing_checks_do_not_print_the_skip_command():
    d = make_sandbox(ci_exit=1)
    try:
        p = run(d, {})
        assert p.returncode == 1, p.stderr
        assert "CARR_SKIP_CI=1 git push" not in p.stderr, p.stderr
        assert "PUSH REFUSED" in p.stderr, p.stderr
        assert not receipts(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case
def no_skip_no_reason_env_is_a_normal_run():
    d = make_sandbox(ci_exit=0)
    try:
        p = run(d, {})
        assert p.returncode == 0, p.stderr
        assert not receipts(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            print(f"  FAIL {fn.__name__} :: {exc}")
            fails.append(fn.__name__)
        except Exception as exc:  # subprocess timeouts etc.
            print(f"  FAIL {fn.__name__} :: unexpected {exc!r}")
            fails.append(fn.__name__)
    print(f"\nci-skip-receipt-selftest: {len(CASES) - len(fails)}/{len(CASES)} passed")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
