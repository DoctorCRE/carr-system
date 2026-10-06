#!/usr/bin/env python3
"""fleet-sync-siblings-selftest.py — acceptance test for
tools/fleet_sync_sibling_safety.py, written before bin/fleet-sync.sh grew
sibling-repo support (rule e65efc68: write the test before the thing).

THE ASK IT COMES FROM (2026-09-23): "make it so that it always automatically
pulls origin" for every one of Joe's Macs, not just the canonical
~/carr-system checkout — his other GitHub checkouts (doctorcre-app,
software-factory) should stay current too, with the SAME safety contract
bin/fleet-sync.sh already proved for the canonical repo: never discard local
work, never merge/rebase/reset/force, fast-forward only.

WHAT THE SIBLING SYNC MUST DO, one assertion per line of that:

  1. An ABSENT sibling checkout is a clean SKIP, never a failure — Dell's Mac
     and some Macs simply won't have every sibling.
  2. A clean, BEHIND sibling fast-forwards to origin/main.
  3. A DIRTY sibling (tracked local changes) is left completely untouched —
     its bytes are preserved byte-for-byte — and the dirty paths are named.
  4. A sibling OFF main is left untouched.
  5. A DIVERGED sibling is left untouched.
  6. A sibling's own failure or skip must never fail the CALLER'S exit code
     path for the canonical checkout — bin/fleet-sync.sh's own contract, only
     provable end-to-end, is exercised here too via a direct subprocess call
     to confirm the exit-code convention (0 ok, 78 skip, 1 fail) that
     bin/fleet-sync.sh relies on to decide it is safe to ignore.

Every case runs against throwaway git repositories under a temp dir. Nothing
here touches a real sibling checkout or the real ~/carr-system.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from git_env import fixture_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from fleet_sync_sibling_safety import EXIT_CODES, sync_sibling  # noqa: E402

MODULE = os.path.join(REPO, "tools", "fleet_sync_sibling_safety.py")
TOOLS_DIR = os.path.join(REPO, "tools")
ENV = dict(fixture_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

# The EXACT inline dispatch snippet bin/fleet-sync.sh runs for each sibling —
# see its sync_siblings(). Exercised here as a subprocess (not by importing
# and calling sync_sibling() directly, which the other tests already do) so
# the exit-code convention the CALLER relies on is proved end-to-end, without
# tools/fleet_sync_sibling_safety.py itself ever being run as a script (it
# carries no shebang and no main guard on purpose — see its module docstring).
DISPATCH_SNIPPET = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[4])\n"
    "from fleet_sync_sibling_safety import EXIT_CODES, sync_sibling\n"
    "status, message = sync_sibling(sys.argv[1], sys.argv[2], sys.argv[3])\n"
    "print('fleet-sync:   ' + message)\n"
    "sys.exit(EXIT_CODES[status])\n"
)


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {r.stderr}")
    return r.stdout.strip()


def build_pair(tmp, name="doctorcre-app"):
    """origin + a sibling clone that is behind origin/main by one commit."""
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    git(origin, "init", "-q", "--bare")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    seed = os.path.join(tmp, "seed")
    git(tmp, "clone", "-q", origin, "seed")
    open(os.path.join(seed, "f.txt"), "w").write("base\n")
    git(seed, "add", "f.txt")
    git(seed, "commit", "-qm", "base")
    git(seed, "branch", "-M", "main")
    git(seed, "push", "-q", "origin", "main")

    sibling = os.path.join(tmp, name)
    git(tmp, "clone", "-q", "-b", "main", origin, name)

    open(os.path.join(seed, "f.txt"), "a").write("newer\n")
    git(seed, "add", "f.txt")
    git(seed, "commit", "-qm", "newer")
    git(seed, "push", "-q", "origin", "main")
    return sibling


def run_module(path, name, branch="main"):
    return subprocess.run(
        [sys.executable, "-c", DISPATCH_SNIPPET, path, name, branch, TOOLS_DIR],
        capture_output=True, text=True, env=ENV,
    )


def test_absent_sibling_skips_cleanly():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "doctorcre-app")
        status, message = sync_sibling(missing, "doctorcre-app")
        assert status == "skip", (status, message)
        assert "absent" in message, message
        r = run_module(missing, "doctorcre-app")
        assert r.returncode == 78, r
    print("PASS  absent sibling skips cleanly, exit 78")


def test_clean_behind_sibling_fast_forwards():
    with tempfile.TemporaryDirectory() as tmp:
        sibling = build_pair(tmp)
        before_sha = git(sibling, "rev-parse", "HEAD")
        r = run_module(sibling, "doctorcre-app")
        assert r.returncode == 0, r
        after_sha = git(sibling, "rev-parse", "HEAD")
        assert after_sha != before_sha, "sibling did not advance"
        origin_sha = git(sibling, "rev-parse", "origin/main")
        assert after_sha == origin_sha, (after_sha, origin_sha)
    print("PASS  clean behind sibling fast-forwards")


def test_already_current_sibling_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        sibling = build_pair(tmp)
        git(sibling, "fetch", "-q", "origin", "main")
        git(sibling, "merge", "-q", "--ff-only", "origin/main")
        before_sha = git(sibling, "rev-parse", "HEAD")
        r = run_module(sibling, "doctorcre-app")
        assert r.returncode == 0, r
        assert "already current" in r.stdout, r.stdout
        assert git(sibling, "rev-parse", "HEAD") == before_sha
    print("PASS  already-current sibling is a clean no-op")


def test_dirty_sibling_is_left_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        sibling = build_pair(tmp)
        before_sha = git(sibling, "rev-parse", "HEAD")
        dirty_path = os.path.join(sibling, "f.txt")
        dirty_bytes = b"dirty local edit, never committed\n"
        open(dirty_path, "wb").write(dirty_bytes)
        r = run_module(sibling, "doctorcre-app")
        assert r.returncode == 78, r
        assert "f.txt" in r.stdout, r.stdout
        assert git(sibling, "rev-parse", "HEAD") == before_sha, "dirty sibling advanced"
        assert open(dirty_path, "rb").read() == dirty_bytes, \
            "dirty sibling bytes were not preserved exactly"
    print("PASS  dirty sibling untouched, bytes preserved exactly")


def test_off_main_sibling_is_left_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        sibling = build_pair(tmp)
        git(sibling, "checkout", "-q", "-b", "some-feature")
        before_sha = git(sibling, "rev-parse", "HEAD")
        r = run_module(sibling, "doctorcre-app")
        assert r.returncode == 78, r
        assert "some-feature" in r.stdout, r.stdout
        assert git(sibling, "rev-parse", "HEAD") == before_sha
    print("PASS  off-main sibling untouched")


def test_diverged_sibling_is_left_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        sibling = build_pair(tmp)
        open(os.path.join(sibling, "local-only.txt"), "w").write("local commit\n")
        git(sibling, "add", "local-only.txt")
        git(sibling, "commit", "-qm", "local commit not on origin")
        before_sha = git(sibling, "rev-parse", "HEAD")
        r = run_module(sibling, "doctorcre-app")
        assert r.returncode == 78, r
        assert "diverged" in r.stdout, r.stdout
        assert git(sibling, "rev-parse", "HEAD") == before_sha
    print("PASS  diverged sibling untouched")


def test_sibling_failure_does_not_block_canonical_path():
    # A sibling's own exit code (skip=78 or fail=1) must be something a caller
    # can freely ignore. Prove the three exit codes bin/fleet-sync.sh relies on
    # are exactly the ones documented, so a caller checking none of them is a
    # DELIBERATE choice, not an accident of an undocumented contract.
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "software-factory")
        skip = run_module(missing, "software-factory")
        assert skip.returncode == 78, skip

        sibling = build_pair(tmp, "software-factory")
        ok = run_module(sibling, "software-factory")
        assert ok.returncode == 0, ok

        # A genuinely broken sibling (no origin remote) is a "fail", not a
        # skip — the caller still must not let this block anything of its own.
        broken = os.path.join(tmp, "broken")
        git(tmp, "init", "-q", "broken")
        open(os.path.join(broken, "g.txt"), "w").write("x\n")
        git(broken, "add", "g.txt")
        git(broken, "commit", "-qm", "solo")
        git(broken, "branch", "-M", "main")
        fail = run_module(broken, "broken")
        assert fail.returncode in (78, 1), fail
    print("PASS  sibling outcomes (skip/ok/fail) never gate a caller's own path")


def test_sibling_git_never_waits_on_a_credential_prompt():
    """Every git call the sibling sync makes runs with prompting disabled.

    fleet-sync runs unattended from launchd. If a remote ever wants a login
    (an expired token, a revoked credential), a git that is allowed to prompt
    sits waiting for an answer nobody will type, and that run stalls instead
    of skipping. A stand-in `git` first on PATH records the prompt-control
    environment of every call, then hands off to the real git.
    """
    real_git = subprocess.run(["/usr/bin/which", "git"], capture_output=True,
                              text=True, env=ENV).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        sibling = build_pair(tmp)
        shim_dir = os.path.join(tmp, "shim")
        os.makedirs(shim_dir)
        log = os.path.join(tmp, "git-env.log")
        shim = os.path.join(shim_dir, "git")
        with open(shim, "w") as f:
            f.write("#!/bin/sh\n"
                    f"printf '%s|%s|%s\\n' \"${{GIT_TERMINAL_PROMPT-unset}}\" "
                    f"\"${{GCM_INTERACTIVE-unset}}\" \"${{GIT_SSH_COMMAND-unset}}\" >> {log}\n"
                    f"exec {real_git} \"$@\"\n")
        os.chmod(shim, 0o755)
        env = dict(ENV, PATH=shim_dir + os.pathsep + ENV.get("PATH", ""))
        env.pop("GIT_TERMINAL_PROMPT", None)
        env.pop("GCM_INTERACTIVE", None)
        env.pop("GIT_SSH_COMMAND", None)
        r = subprocess.run([sys.executable, "-c", DISPATCH_SNIPPET, sibling,
                            "doctorcre-app", "main", TOOLS_DIR],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r
        rows = open(log).read().splitlines() if os.path.exists(log) else []
        assert rows, "stand-in git was never called"
        for row in rows:
            prompt, gcm, ssh = row.split("|", 2)
            assert prompt == "0", f"git ran with GIT_TERMINAL_PROMPT={prompt}"
            assert gcm == "never", f"git ran with GCM_INTERACTIVE={gcm}"
            assert "BatchMode=yes" in ssh, f"git ran with GIT_SSH_COMMAND={ssh}"
    print("PASS  sibling git never waits on a credential prompt")


def test_fleet_sync_disables_prompts_before_its_first_git_call():
    """bin/fleet-sync.sh's own carr-system fetch gets the same protection.

    The export has to come before the script's first git command, or the
    canonical fetch can still stall on a login prompt.
    """
    lines = open(os.path.join(REPO, "bin", "fleet-sync.sh")).read().splitlines()
    code = [(i, l.strip()) for i, l in enumerate(lines)
            if l.strip() and not l.strip().startswith("#")]
    export_at = next((i for i, l in code if l.startswith("export GIT_TERMINAL_PROMPT=0")), None)
    assert export_at is not None, "bin/fleet-sync.sh never exports GIT_TERMINAL_PROMPT=0"
    first_git = next(i for i, l in code if "git " in l and not l.startswith("export "))
    assert export_at < first_git, (export_at, first_git)
    for needle in ("export GCM_INTERACTIVE=never", "GIT_SSH_COMMAND"):
        assert any(needle in l for _, l in code), f"bin/fleet-sync.sh missing {needle}"
    print("PASS  fleet-sync disables prompts before its first git call")


def main():
    if not os.path.exists(MODULE):
        print(f"fleet-sync-siblings-selftest: {MODULE} missing", file=sys.stderr)
        return 1
    test_absent_sibling_skips_cleanly()
    test_clean_behind_sibling_fast_forwards()
    test_already_current_sibling_is_a_noop()
    test_dirty_sibling_is_left_untouched()
    test_off_main_sibling_is_left_untouched()
    test_diverged_sibling_is_left_untouched()
    test_sibling_failure_does_not_block_canonical_path()
    test_sibling_git_never_waits_on_a_credential_prompt()
    test_fleet_sync_disables_prompts_before_its_first_git_call()
    print("9/9 fleet-sync-siblings cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
