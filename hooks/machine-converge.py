#!/usr/bin/env python3
"""machine-converge.py — SessionStart converge for a SECONDARY machine.

JOE'S DIRECTIVE (2026-08-18): Dell's machine must pull and re-apply config
automatically at session start, so it converges to the repo state with no
manual updating. Before this hook, nothing at session start moved a stale
checkout: gate-integrity.py detects drift against the LOCAL repo and its
self-heal reinstalls from the LOCAL repo, so a machine whose CHECKOUT was the
stale thing faithfully reinstalled stale gates and reported success. Dell's
Mac did exactly that for a week after the 2026-08-11 migration.

WHAT IT DOES, on a secondary machine only:
  1. Fast-forwards the CANONICAL checkout's main from origin — ff-only, so a
     diverged tree fails LOUDLY in the session brief instead of merging.
     The refusals mirror bin/fleet-sync.sh (PR #333): never over local
     tracked changes, never off main, never a merge/rebase/reset.
  2. Re-applies machine config with `ops/config-as-code.py install --apply`,
     but only when the pull moved HEAD or `check` reports drift — so an
     already-converged machine boots silently and its launchd jobs are not
     reloaded on every session start.

NEVER `pull`, AND THAT IS THE LOAD-BEARING VERB CHOICE (decision #442,
00_Context/decision-history.md 2026-08-17). config-as-code's `pull` captures
whatever the machine has INTO the repo; run against a drifted live plist it
would have deleted the four bin/run-scheduled.sh wrapper lines that commit
4fb58a8a put in com.carr.dictation-consent.plist deliberately — criticality
HIGH, the wrapper is part of what keeps the recording announcement durable,
Florida is all-party-consent, a failure is legal exposure. `install` goes the
other way: it renders the TRACKED template (which carries the wrapper and the
still-recording five-minute tick, PR #328) onto the machine, so a converge can
only ever restore the wrapper, never strip it. ops/machine-converge-selftest.py
proves that end-to-end: a live plist seeded WITHOUT the wrapper comes out of a
converge WITH it.

The worktree/canonical catch-22 from the same decision does not bite here:
this hook is always invoked by its canonical path ("${HOME}/carr-system/..."
in .claude/settings.json, same convention as worktree-self-plumb.py), so it
runs the canonical checkout's config-as-code against the canonical templates
— the exact arrangement config-as-code's own {{REPO}} resolution assumes.

MACHINE GATING — the sponsor mechanism, not a hostname. Which human a machine
belongs to is recorded in ~/.config/carr/local-actor.json (written once per
machine by bin/set-local-actor.sh; it is what local-verb.mjs trusts, and the
machine door in mcp-server/src/identity.js maps the same partners server-side,
PR #334). slug "joe" → this is the primary machine and the hook is a NO-OP:
Joe's canonical tree is a shared work surface for live sessions, an unbidden
pull at session start can interleave with another session's push/reconcile,
and the scheduled fleet-sync job covers his staleness case. Any other slug →
converge. When the file is absent (Dell's door provisioning was still owed in
PR #334), fall back to the determinant config-as-code itself uses for
per-machine ownership: git user.email against OWNER_EMAIL in
ops/githooks/pre-push — parsed from that one home, never re-declared. Not
primary → converge, the same fail-closed direction as IS_PRIMARY=false.

CLAUDE-ONLY MACHINES STAY CLAUDE-ONLY. The converge delegates every write to
config-as-code, whose install path treats an absent ~/.codex as a supported
state and skips Codex without creating it (CLAUDE.md, Dell migration
contract). The selftest asserts no ~/.codex appears.

FAIL-SOFT, ALWAYS — same discipline as worktree-self-plumb.py: a boot-time
convenience must never fail or block a session. Refusals and failures are one
loud LINE in the brief, never a blocked boot.
"""
import json
import os
import re
import subprocess
import sys

# __file__ resolves through the absolute canonical path this hook is invoked
# by, so REPO is the canonical tree regardless of which worktree's session
# triggered it (same reasoning as worktree-self-plumb.py).
#
# CLOUD CONTAINERS (2026-09-27): the settings command runs this file only when
# ~/carr-system/hooks exists and exits 0 otherwise. A Claude Code cloud clone
# has no local actor, no launchd and no ~/.config/carr, and would otherwise
# read as an unidentified secondary machine and run `config-as-code install
# --apply` against the container. It must never converge there; ops/cloud-
# hook-paths-selftest.py pins that no-op.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
ACTOR_FILE = os.path.join(HOME, ".config", "carr", "local-actor.json")

# git location variables outrank -C, and git EXPORTS several of them to every
# hook it runs — so a git call from here can land in whatever repository the
# inherited variable names rather than in REPO (the 2026-08-14 incident, see
# ops/config-as-code.py's header). Scrubbed the one way every CARR git caller
# scrubs, ops/git_env.py (consolidated by loop #371), never re-declared here.
#
# The import is guarded because this file's contract is FAIL-SOFT AT BOOT: an
# ImportError at module scope would print a traceback into every single session
# start. Losing the scrub is not a reason to proceed unscrubbed — a converge
# that cannot pin which repository its git commands hit must not run them at
# all — so the fallback is to do nothing, which is also the direction that
# leaves the machine exactly as it was.
sys.path.insert(0, os.path.join(REPO, "ops"))
try:
    from git_env import scrubbed_env
except Exception:
    scrubbed_env = None


def git(*args, timeout=20):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                          text=True, timeout=timeout, env=scrubbed_env())


def local_actor_slug():
    try:
        with open(ACTOR_FILE, encoding="utf-8") as fh:
            return (json.load(fh).get("actor_slug") or "").strip()
    except Exception:
        return ""


def is_primary():
    """Same determinant as ops/config-as-code.py's IS_PRIMARY, from its one
    home, lib/machine_role.py: the per-machine marker when present, else git
    user.email against OWNER_EMAIL. Any failure returns False, the safe
    direction here too: an unidentified machine converges to the repo."""
    try:
        sys.path.insert(0, REPO)
        from lib import machine_role
        return machine_role.is_primary(
            REPO, git_email=git("config", "user.email").stdout.strip())
    except Exception:
        return False


def machine_role_marker():
    try:
        sys.path.insert(0, REPO)
        from lib import machine_role
        return machine_role.read_marker()
    except Exception:
        return None


def python_bin():
    venv = os.path.join(REPO, ".venv", "bin", "python")
    return venv if os.access(venv, os.X_OK) else sys.executable


def config_as_code(*args, timeout=120):
    """config-as-code is stdlib-only; stdin is closed so nothing can wait on
    a terminal (the migrate-dell contract's `</dev/null` discipline)."""
    return subprocess.run(
        [python_bin(), os.path.join(REPO, "ops", "config-as-code.py"), *args],
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL)


def say(line):
    print(f"machine-converge: {line}")


def converge():
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        say(f"SKIP — canonical checkout is on '{branch}', not main; leaving it alone")
        return

    moved = False
    fetch = git("fetch", "--quiet", "origin", "main", timeout=30)
    if fetch.returncode != 0:
        # Offline is ordinary; the LOCAL repo can still be re-applied below.
        say("fetch of origin/main failed (offline, or no credential) — "
            "converging from the local checkout only")
    else:
        head = git("rev-parse", "HEAD").stdout.strip()
        remote = git("rev-parse", "origin/main").stdout.strip()
        if head and remote and head != remote:
            # The three fleet-sync refusals, in its order. Tracked changes
            # only: untracked scratch is a session's business.
            dirty = git("status", "--porcelain", "--untracked-files=no").stdout.strip()
            if dirty:
                say("SKIP pull — local changes present, refusing to fast-forward "
                    "over them: " + ", ".join(
                        ln.split(None, 1)[-1] for ln in dirty.splitlines()[:5]))
            elif git("merge-base", "--is-ancestor", "HEAD", "origin/main").returncode != 0:
                say("DIVERGED — main and origin/main have both moved; a human "
                    "decides this one, nothing was merged")
            else:
                ff = git("merge", "--ff-only", "origin/main", timeout=60)
                if ff.returncode == 0:
                    moved = True
                    say(f"fast-forwarded {head[:8]} -> {remote[:8]}")
                else:
                    say("fast-forward FAILED: "
                        + (ff.stderr or ff.stdout).strip()[:120])

    drift = config_as_code("check", timeout=60).returncode != 0
    if not (moved or drift):
        return  # converged already — silent, like most session starts

    # `install --apply`, never `pull` — see the docstring for decision #442.
    r = config_as_code("install", "--apply")
    if r.returncode == 0:
        rev = git("rev-parse", "--short", "HEAD").stdout.strip()
        say(f"machine config re-applied from {rev or 'local checkout'}")
    else:
        tail = (r.stderr or r.stdout).strip().splitlines()
        say("config-as-code install FAILED: " + (tail[-1] if tail else "(no output)"))


def main():
    sys.path.insert(0, REPO)
    if os.environ.get("CARR_GROK_RUN_READ_ONLY") == "1":
        try:
            from hooks.grok_invocation import bounded_grok_read_only
            if bounded_grok_read_only():
                return 0
        except ImportError:
            pass  # an unavailable optional probe retains ordinary processing
    try:
        if scrubbed_env is None:
            return 0  # cannot pin which repository git would hit — see above
        # An explicit machine-role marker outranks the actor slug: Joe owns
        # more than one Mac, and only the one marked primary is the shared
        # work surface this no-op protects (lib/machine_role.py).
        marker = machine_role_marker()
        if marker == "primary":
            return 0
        if marker is None:
            slug = local_actor_slug()
            if slug == "joe" or (not slug and is_primary()):
                return 0  # the primary machine — deliberate no-op, see docstring
        converge()
    except Exception:
        pass  # fail-soft: this must never block or fail a session
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
