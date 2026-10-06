#!/usr/bin/env python3
"""worktree-self-plumb.py — SessionStart self-plumb for a carr-system
worktree Claude Code (or Codex, or a bare `git worktree add`) created without
ever calling bin/worktree.sh — plus the orphan-worktree reaper (see below).

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

THE ORPHAN REAPER. The existing --reap door now uses lib/branch_retirement.py
for its census, ordered classification and actions. --fleet covers the three
authorized repositories. SessionStart still spawns this same reaper. The
watchdog also launches it hourly and registers its process and exit record.

Live processes, fresh writes, locked paths and uncertain ownership preserve a
worktree. Retired trees move to _to_delete with a manifest and keep their local
branch. Only merged remote branches can be deleted, with a pre-push check of
the advertised tip. A superseded PR closes only after its merged successor is
read back. Dry runs and action receipts go to out/orch. The shared maintenance
lock remains out/worktree-reap.lock.

hooks/*.py are covered by the gate-integrity.py baseline (rule: a session
that adds or edits a hook re-blesses the baseline in the same commit) — this
file was blessed alongside its addition; see ops/config/gate-baseline.json.

FAIL-SOFT, ALWAYS. A boot-time convenience must never fail or block a
session: any error anywhere in here is swallowed and the hook prints
nothing, same discipline as hooks/session-brief.py's own nightly/loose-work
lines.

Fixtures: ops/worktree-self-plumb-selftest.py (boot policy) and
ops/branch-janitor-selftest.py (retirement against isolated git state).
"""
import json
import os
import subprocess
import sys
from pathlib import Path
import time

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

def worktree_entries(repo):
    """`git worktree list --porcelain` as dicts; [] on any failure."""
    out = run_git(["worktree", "list", "--porcelain"], repo)
    if out is None:
        return []
    entries, cur = [], None
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": ln[len("worktree "):].strip()}
        elif cur is None:
            continue
        elif ln.startswith("HEAD "):
            cur["head"] = ln[len("HEAD "):].strip()
        elif ln.startswith("branch refs/heads/"):
            cur["branch"] = ln[len("branch refs/heads/"):].strip()
        elif ln == "bare":
            cur["bare"] = True
        elif ln == "detached":
            cur["detached"] = True
        elif ln == "locked" or ln.startswith("locked "):
            cur["locked"] = True
        elif ln == "prunable" or ln.startswith("prunable "):
            cur["prunable"] = True
    if cur:
        entries.append(cur)
    return entries


def index_gitdir(wt):
    """The worktree's private gitdir (…/.git/worktrees/<id>), or None."""
    dotgit = os.path.join(wt, ".git")
    try:
        if os.path.isdir(dotgit):
            return dotgit                      # a main checkout, not a worktree
        with open(dotgit) as fh:
            first = fh.read().strip()
        if not first.startswith("gitdir:"):
            return None
        gitdir = first.split(":", 1)[1].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(wt, gitdir))
        return gitdir
    except Exception:
        return None


def index_age_s(wt):
    """Seconds since the worktree's .git index moved; None when unknowable.

    The index is the one file every git operation a live session performs
    keeps warm, and it lives OUTSIDE the working tree — a session cannot
    fake it old, and reaping cannot be dodged by it. This is the 6h
    liveness signal the 2026-08-18 sweep used.
    """
    gitdir = index_gitdir(wt)
    if not gitdir:
        return None
    try:
        idx = os.path.join(gitdir, "index")
        st = os.stat(idx) if os.path.exists(idx) else os.stat(gitdir)
        return time.time() - st.st_mtime
    except Exception:
        return None


# A build seat can write files for hours without running a single git
# command, which leaves .git/index cold while the worktree is very much
# alive. That is exactly how defect a4abb972 happened: an automated sweep
# removed a paused build's in-flight evidence because the only liveness
# signal it had was a file the build never touched. So idleness is judged
# on BOTH signals and the youngest one wins.
TREE_SCAN_MAX_ENTRIES = 20000  # past this the tree is too big to judge cheaply


def tree_age_s(wt):
    """Seconds since ANY file inside the worktree moved; None when unknowable.

    Complements index_age_s, which only sees git operations. This sees the
    writes themselves — the signal a build seat actually produces.

    Symlinks are never followed: .venv, out and mcp-server/node_modules are
    plumbing links into the canonical repo, and walking them would read
    canonical's activity as this worktree's and keep every worktree forever.
    A tree too large to scan, or any error, returns None, which classify()
    reads as "do not judge it" — the keep direction, same as every other
    uncertain answer here.
    """
    newest = 0.0
    seen = 0
    skip = {".git", "node_modules", ".venv", "__pycache__"}
    try:
        for root, dirs, files in os.walk(wt, followlinks=False):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files + dirs:
                seen += 1
                if seen > TREE_SCAN_MAX_ENTRIES:
                    return None
                fp = os.path.join(root, name)
                try:
                    st = os.lstat(fp)
                except OSError:
                    continue
                if st.st_mtime > newest:
                    newest = st.st_mtime
    except Exception:
        return None
    if not newest:
        return None
    return time.time() - newest


def mark_alive(wt):
    """Touch this session's own index so the 6h rule reads it as live.

    A RESUMED session can land in a worktree whose index is days old, and
    nothing guarantees its first git operation beats another boot's reaper.
    Touching at SessionStart makes the liveness rule true by construction
    for every session this hook boots.
    """
    try:
        gitdir = index_gitdir(wt)
        if gitdir:
            idx = os.path.join(gitdir, "index")
            os.utime(idx if os.path.exists(idx) else gitdir, None)
    except Exception:
        pass


def fleet_reaper(root, **kwargs):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    from branch_retirement import FleetReaper
    return FleetReaper(root, sys.modules[__name__], **kwargs)


def reap_main(argv):
    def value(flag):
        return argv[argv.index(flag) + 1] if flag in argv else None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    from branch_retirement import health, repository_roots
    roots = repository_roots()
    canon = Path(canonical_root(value("--repo") or REPO))
    if "--fleet" in argv and canon.resolve() != roots["jbookout/carr-system"].resolve():
        print("fleet retirement requires the canonical CARR repository")
        return 1
    if "--health-row" in argv:
        line, failed = health(canon)
        print(line)
        return int(failed)
    roots = roots if "--fleet" in argv else {"jbookout/carr-system": canon}
    report = fleet_reaper(canon).run(roots, execute="--dry-run" not in argv,
                                    skip=[value("--skip")] if value("--skip") else [])
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, sort_keys=True))
    return int(bool(report["errors"]))


def maybe_spawn_reaper(canon, current_wt):
    """Detach a reaper when the cheap signals say there may be orphans.

    Cheap means list + stat only — no `git status` here, because this runs
    inside the boot hook's 20s budget. The count returned is CANDIDATES
    (registered, unlocked, idle 6h+); the background pass applies the
    dirty/ancestry rules and may well keep them all.
    """
    skip_paths = {canon, os.path.realpath(current_wt)}
    cands = 0
    for entry in worktree_entries(canon):
        wt = entry.get("path") or ""
        if not wt or os.path.realpath(wt) in skip_paths:
            continue
        if entry.get("bare") or entry.get("locked") or entry.get("prunable"):
            continue
        if not os.path.isdir(wt):
            cands += 1                       # prune fodder — worth a pass too
            continue
        age = index_age_s(wt)
        if age is not None and age >= REAP_MIN_IDLE_S:
            cands += 1
    if not cands:
        return 0
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    from branch_retirement import maintenance
    try:
        with maintenance(Path(canon)):
            pass
    except RuntimeError:
        return 0
    log = os.path.join(canon, "out", "worktree-reap.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    try:
        if os.path.getsize(log) > 1_000_000:
            os.replace(log, log + ".old")
    except OSError:
        pass
    with open(log, "a") as fh:
        subprocess.Popen(
            [sys.executable or "python3", os.path.abspath(__file__),
             "--reap", "--fleet", "--skip", current_wt],
            cwd=canon, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True)
    return cands


def main():
    sys.path.insert(0, REPO)
    if os.environ.get("CARR_GROK_RUN_READ_ONLY") == "1":
        try:
            from hooks.grok_invocation import bounded_grok_read_only
            if bounded_grok_read_only():
                return 0
        except ImportError:
            pass  # an unavailable optional probe retains ordinary processing
    if "--reap" in sys.argv[1:]:
        # Detached child (or a hand/selftest run) — no SessionStart payload.
        try:
            return reap_main(sys.argv[1:])
        except Exception:
            return 0                         # fail-soft, like the hook itself

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

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
            mark_alive(toplevel)
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
                # A non-zero exit (not a registered worktree, or some other
                # refusal) is deliberately silent here — the boot hook proposes
                # the fix, it does not surface --plumb's own refusal reasoning
                # at every session start; run `./run.sh worktree --plumb` by
                # hand to see it.

        # The orphan reaper runs for canonical AND worktree sessions — the
        # canonical tree is where a human most often sits, and its sessions
        # are exactly the ones that notice 4.4GB of dead checkouts.
        n = maybe_spawn_reaper(canon, toplevel)
        if n:
            print(
                f"worktree reaper: {n} idle worktree candidate(s) — sweeping in "
                "the background (clean + idle 6h + branch-safe rules; "
                "log: out/worktree-reap.log)"
            )
    except Exception:
        pass  # fail-soft: this must never block or fail a session
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
