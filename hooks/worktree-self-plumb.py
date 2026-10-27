#!/usr/bin/env python3
"""worktree-self-plumb.py — SessionStart self-plumb for a carr-system
worktree Claude Code (or Codex, or a bare `git worktree add`) created without
ever calling bin/worktree.sh.

THE LIVE FAILURE THIS CLOSES. A session landed in a worktree created by
Claude Code's own native isolation (a bare `git worktree add` under
.claude/worktrees/, outside this repo's `run.sh worktree` command). It found
no mcp-server/node_modules there, could not run its own tooling, and fell
back to running from the shared CANONICAL checkout instead — the exact
multi-writer collision `run.sh worktree` exists to prevent (see that file's
header). bin/worktree.sh's create path already solves this for worktrees IT
creates, by symlinking .venv, out, and mcp-server/node_modules back to the
canonical tree. A worktree created by any other door gets none of that,
because nothing calls the linking step for it.

`bin/worktree.sh --plumb [path]` (added alongside this file) is the same
three links, made callable against a worktree bin/worktree.sh did not
create. THIS hook is what makes that automatic: at SessionStart, if the
session's cwd is inside a non-canonical carr-system worktree that is
missing any of the three links, it calls `--plumb` for that worktree and
says so in the brief.

WHY THIS IS A SEPARATE HOOK, NOT AN EXTENSION OF hooks/session-brief.py.
The task that produced this file named session-brief.py as the natural
extension point, on the reasonable assumption that it is the general
SessionStart entry point every local session runs, and that "cwd is not a
carr-system worktree" would just be the common case it skips. That assumption
does not hold: session-brief.py is wired ONLY into the two VAULT project
settings files (claude-tree/settings/my-drive-root.settings.json and
carr-ai-project.settings.json, deployed by bin/sync-settings.sh to the
"My Drive" and "My Drive/CARR AI" trees). It is never wired into any
carr-system checkout's own settings — this repo's tracked .claude/settings.json
carries no SessionStart hook at all today, and neither ~/.claude/settings.json
(user-level; SessionStart there is gate-integrity.py only) reaches it for a
session rooted in a carr-system worktree specifically. Concretely: 100% of
session-brief.py's actual invocations are vault-rooted sessions, 0% are
carr-system-worktree sessions — extending it would be correct code that
never runs for the failure this file exists to fix.

The fix that actually reaches the failure has to live somewhere every
carr-system worktree carries BY CONSTRUCTION, regardless of which door
created it — and that is exactly what a file tracked in the repo gives for
free: `git worktree add` (by us, by Claude Code's native isolation, by Codex,
by hand) always checks out HEAD, so this repo's own .claude/settings.json —
where this hook is registered as SessionStart, added in the same commit —
ships into every worktree automatically. No per-worktree deployment step, no
"most doors don't know to run it" gap. That is the same reasoning
bin/worktree.sh's own header gives for CREATE PATH FRESHNESS and --sweep:
the fix has to live on a path every case actually walks, not a path only the
already-correct case walks.

FAIL-SOFT, ALWAYS. A boot-time convenience must never fail or block a
session: any error anywhere in here is swallowed and the hook prints
nothing, same discipline as hooks/session-brief.py's own nightly/loose-work
lines.

Fixtures: ops/worktree-self-plumb-selftest.py.
"""
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run

# __file__ resolves through the absolute canonical path this hook is always
# invoked by ("${HOME}/carr-system/hooks/worktree-self-plumb.py" in
# .claude/settings.json — the same "always call the canonical copy"
# convention hooks/delegation-gate.py already uses), so REPO is the
# canonical tree regardless of which worktree's cwd triggered this hook.
#
# CLOUD CONTAINERS (2026-09-27): the settings command runs this file only when
# ~/carr-system/hooks exists and exits 0 otherwise. A Claude Code cloud clone
# has no canonical checkout, no sibling worktrees to plumb and no orphans to
# reap, so the hook does nothing there by design. The AGENTS.md policy block
# this hook prints is therefore not injected in the cloud; ops/cloud-hook-
# paths-selftest.py pins that no-op.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Must match the three names bin/worktree.sh links at create time and
# re-links under --plumb. Kept here only to decide WHETHER to bother calling
# --plumb (and what to say if we do) — the actual linking decision (what
# counts as already-plumbed, the tracked-real-dir guard) stays solely in
# bin/worktree.sh's link(), never duplicated here (rule a8c55a47).
PLUMB_LINKS = (".venv", "out", os.path.join("mcp-server", "node_modules"))

# One canonical source for the active operating policy. Codex reads
# AGENTS.md directly; Claude Code receives this exact block from its existing
# carr-system SessionStart hook. Keeping the prose in AGENTS.md and extracting
# it here prevents two boot copies from drifting while both look authoritative.
POLICY_START = "<!-- carr-product-first-policy:start -->"
POLICY_END = "<!-- carr-product-first-policy:end -->"


def delivery_policy_brief(repo):
    """Return the active AGENTS policy block, or empty on any mismatch.

    This is advisory boot delivery. It grants no mutation, production,
    destructive-action, or unattended authority, and it never blocks startup.
    """
    try:
        text = open(os.path.join(repo, "AGENTS.md"), encoding="utf-8").read()
        if text.count(POLICY_START) != 1 or text.count(POLICY_END) != 1:
            return ""
        start = text.index(POLICY_START) + len(POLICY_START)
        end = text.index(POLICY_END, start)
        body = text[start:end].strip()
        return body if body else ""
    except Exception:
        return ""


def emit_delivery_policy(repo):
    """Emit the advisory policy for a SessionStart hook when it is present."""
    policy = delivery_policy_brief(repo)
    if not policy:
        return False
    print(policy)
    return True

# ── orphan reaper thresholds — the 2026-08-18 sweep's proven rules ─────────
REAP_MIN_IDLE_S = 6 * 3600     # index younger than this = possibly-live session


def resolve_cwd(payload):
    return (payload.get("cwd") or payload.get("working_directory")
            or payload.get("workingDirectory") or os.getcwd())


def run_git(args, cwd, timeout=10):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
        from git_env import scrubbed_env
        env = scrubbed_env()
        env["GIT_OPTIONAL_LOCKS"] = "0"
        p = subprocess.run(["git", *args], cwd=cwd,
                            capture_output=True, text=True, timeout=timeout, env=env)
    except Exception:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def canonical_root(repo):
    """The canonical checkout, resolved through git rather than assumed.

    REPO is already canonical when this file is invoked by its wired absolute
    path, but a copy run from inside a worktree (a selftest, a hand test)
    would otherwise mistake that worktree for canonical — and for a REAPER
    that confusion must be impossible: the skip-canonical guard has to anchor
    on the tree git itself calls home. Same resolution bin/worktree.sh uses.
    """
    out = run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], repo)
    if out:
        return os.path.realpath(os.path.dirname(out))
    return os.path.realpath(repo)


# ── orphan reaper ──────────────────────────────────────────────────────────


@decision(failure="raise")
def decide(payload):
    try:
        cwd = resolve_cwd(payload)
        if not cwd or not os.path.isdir(cwd):
            return 0

        toplevel = run_git(["rev-parse", "--show-toplevel"], cwd)
        if not toplevel:
            return 0  # not inside any git working tree — most sessions (the vault) end here

        toplevel = os.path.realpath(toplevel)
        canon = canonical_root(REPO)

        emit_delivery_policy(toplevel)

        if toplevel != canon:
            # If this hook fired at all, cwd is under a worktree that carries
            # this repo's tracked .claude/settings.json (that is the only way
            # Claude Code would have run it) — so toplevel is necessarily a
            # carr-system checkout. Whether it is a REGISTERED worktree of THIS
            # canonical tree is still --plumb's own guard to enforce; if it
            # refuses, this hook just prints nothing (see except below).
            missing = [name for name in PLUMB_LINKS
                       if not os.path.islink(os.path.join(toplevel, name))
                       and os.path.isdir(os.path.join(canon, name))]
            if missing:
                plumb = subprocess.run(
                    ["zsh", os.path.join(canon, "bin", "worktree.sh"), "--plumb", toplevel],
                    cwd=canon, capture_output=True, text=True, timeout=20)
                if plumb.returncode == 0:
                    applied = [ln.strip() for ln in plumb.stdout.splitlines() if ln.strip()]
                    print(
                        "worktree self-plumb: this session's worktree "
                        f"({os.path.basename(toplevel)}) was missing {', '.join(missing)} "
                        "— a door other than run.sh worktree created it. Applied: "
                        + "; ".join(applied)
                    )
                else:
                    # 2026-10-12: a refusal used to be silent here, and that
                    # silence cost every session on this MacBook its .venv for
                    # weeks (out/worktree-venv-guard.jsonl: refused, refused,
                    # refused; nobody told). The session that pays for the
                    # missing runtime is the one that must hear why, in one
                    # line, with the remedy --plumb itself names.
                    said = [ln.strip() for ln in plumb.stdout.splitlines()
                            if ln.strip().startswith("!!") or "./run.sh worktree --install" in ln
                            or "requirements.lock" in ln or "npm --prefix" in ln]
                    print(
                        "worktree self-plumb REFUSED: this session's worktree "
                        f"({os.path.basename(toplevel)}) is missing {', '.join(missing)} "
                        "and the shared runtime could not be linked. Selftests and the "
                        "pre-push floor will fail here until one of these runs: "
                        + (" | ".join(said[:4]) if said else plumb.stdout.strip()[-400:] or "run ./run.sh worktree --plumb to see the reason")
                        + " | to repair the SHARED runtime instead: in the canonical checkout, "
                        ".venv/bin/python -m pip install -r requirements.lock and npm --prefix mcp-server ci"
                    )

    except Exception:
        pass  # fail-soft: this must never block or fail a session
    return 0


def main():
    sys.path.insert(0, REPO)
    return run(decide, invalid_event={})


if __name__ == "__main__":
    sys.exit(main() or 0)
