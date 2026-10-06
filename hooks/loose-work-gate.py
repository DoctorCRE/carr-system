#!/usr/bin/env python3
"""loose-work-gate.py — a session does not end leaving its OWN work uncommitted.

WHY (Joe, 2026-08-14): "why the fuck am i having to tell every session to commit
its work? this is insane."

He was right, and the reason was structural rather than anyone being lazy.

DETECTION ALREADY EXISTED AND WAS IN THE WRONG PLACE. tools/health-check.py
reports loose tracked files — but it runs in the nightly chain, once a day, into
a report somebody has to read, long after the session that left the work has
ended. Five hooks already run at Stop (conduct, completion-evidence, ledger-sweep,
context-handoff, scheduled-run-record) and every one of them checks what a session
SAYS. Nothing checked what it LEFT. So the fastest detector on the machine was
Joe noticing, which is the one detector whose time is worth anything.

ONLY THIS SESSION'S FILES, and that limit is the whole design. Several sessions
share one working tree; on the day this was written it held three loose files
belonging to three different sessions. A gate that flagged all of them would make
every session responsible for the whole tree — which is how a nine-session
broadcast happened on this machine, and is strictly worse than the problem it
would be solving. The transcript already records what THIS session wrote, and
nothing else is its business.

REUSES THE TWO HELPERS THAT ALREADY DO THIS. delegation-gate.written_path()
extracts the target of a Write/Edit/apply_patch call, and ledger-sweep.read_tail()
walks a transcript. Neither is copied here: a second implementation of either
would drift from the original and be wrong in a different way (rule a8c55a47).

WHAT IT IS NOT. Not a security control and not a commit policy — it does not care
what you commit, only that you do not walk away from your own edits. It fails
open on every broken input, because a Stop hook that strands sessions is worse
than the thing it checks for.

IT ANNOUNCES, IT NO LONGER REOPENS (2026-08-23, the gates-audit council's
Stop-gate rationing, Joe's order). It used to exit 2, which forces a whole extra
assistant message. Eleven Stop hooks could do that, and the council measured one
real shipped session paying nine of those reopens for findings that changed
nothing — while every reopen is direct token spend against a system whose
standing constraint is no steady-state token ceremony. Only three keep the
power now: core conduct, completion-evidence, and drift-assertion.

WHY THIS ONE LOST IT, and it is not that the finding stopped mattering. Loose
work is real and Joe's complaint that birthed this gate stands. But the harm it
prevents is a file nobody else can see, and the honest question the council's
test asks is whether STOP is the last cheap reversible moment before that harm —
it is not. Nothing has been lost when the session ends: the tree still holds the
files, tools/health-check.py still reports them, and the next session inherits a
message that named them. A reopened turn buys a restatement, not a commit. So
the finding still reaches the model, in context, at the same moment; what it
stops doing is charging a turn for it.

WHAT WOULD REVERSE THIS. The demotion is measured, not permanent: if a week of
the hook telemetry shows loose work actually being ABANDONED at a higher rate
than before — the finding announced and then ignored — the register goes back
to a reopen and this paragraph is the record of why it was tried.
"""
import json
import os
import re
import subprocess
import sys

HOOKS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HOOKS)
# BOTH, and the second one is not decoration: lib.loadpy lives at the REPO root,
# so with only hooks/ on the path the import inside _helpers() raises, the
# fail-open swallows it, and the gate silently never fires. Caught by the
# selftest, which runs this as a script the way the hook actually runs — an
# in-process check passed because the test harness had already put REPO on the
# path itself.
sys.path.insert(0, HOOKS)
sys.path.insert(0, REPO)

from stop_latch import announce  # noqa: E402
from worktree_scope import worktree_root  # noqa: E402


def _helpers():
    """written_path() and read_tail(), imported from the gates that own them."""
    from lib.loadpy import load_module_from_path  # noqa: E402
    deleg = load_module_from_path("_lwg_deleg", os.path.join(HOOKS, "delegation-gate.py"))
    sweep = load_module_from_path("_lwg_sweep", os.path.join(HOOKS, "ledger-sweep.py"))
    return deleg.written_path, sweep.read_tail


def session_writes(transcript_path, written_path, read_tail):
    """Absolute paths this session wrote, in transcript order."""
    out = []
    for rec in read_tail(transcript_path, limit=4000):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            path = written_path(block.get("name") or "",
                                block.get("input") or {})
            if path and path not in out:
                out.append(path)
    return out


def loose_tracked(repo):
    """Paths in `repo` that git reports as changed — tracked-modified or added.

    Untracked files are deliberately EXCLUDED. A session's scratch output, a
    symlink made for tooling, a downloaded fixture: none of those are work being
    abandoned, and flagging them would make the common case noisy enough to mute
    the gate. What matters is a file the repository already knows about that this
    session changed and walked away from.
    """
    p = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                       cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        return None
    paths = []
    for line in p.stdout.splitlines():
        if len(line) < 4:
            continue
        rel = line[3:].strip()
        if " -> " in rel:            # a rename reports "old -> new"; the new one is live
            rel = rel.split(" -> ", 1)[1]
        paths.append(os.path.realpath(os.path.join(repo, rel)))
    return paths


def session_committed(transcript_path, read_tail):
    """True when THIS session ran `git commit` at least once.

    Scoped the same way session_writes() is, and for the same reason: several
    sessions share one working tree, and another session's unpushed commit is
    not this session's business to answer for at its own Stop.
    """
    for rec in read_tail(transcript_path, limit=4000):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            cmd = (block.get("input") or {}).get("command")
            if isinstance(cmd, str) and re.search(r"\bgit\b[^\n]*\bcommit\b", cmd):
                return True
    return False


def unpushed_count(repo):
    """Commits reachable from HEAD and from NO remote ref at all.

    REACHABILITY, not upstream tracking, is the right test. On 2026-08-14 the
    fix that mattered reached origin under a different branch name than the one
    it was committed on (`git push origin HEAD:other-name`), which leaves the
    local branch with no upstream and zero commits actually missing. Asking
    `--not --remotes` gets that right; asking about @{upstream} would have
    nagged about work that had already landed.

    Returns None when the question does not apply — no remotes configured at
    all, or git refuses to answer. A local-only repository is not drift.
    """
    r = subprocess.run(["git", "remote"], cwd=repo, capture_output=True, text=True)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    p = subprocess.run(["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
                       cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        return int((p.stdout or "0").strip())
    except ValueError:
        return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0                     # never block on a malformed payload

    # Already stopping: the other Stop gates use the same guard, and re-firing
    # would nag a session that is mid-way through answering the first nag.
    if payload.get("stop_hook_active"):
        return 0

    # Leaving work loose is sometimes correct — handing a tree to another
    # session, parking mid-investigation. Set it for one session, never export
    # it in a profile.
    if os.environ.get("CARR_ALLOW_LOOSE_WORK") == "1":
        return 0

    # WHICH TREE THIS SESSION IS ABOUT. REPO is this file's own parent, so it is
    # always the canonical checkout — hooks run from there in every worktree
    # (hooksPath is absolute). Judging canonical made this gate report ANOTHER
    # session's unpushed commit to a session whose own worktree was clean and
    # fully pushed; observed 2026-08-23, canonical HEAD 9c23a9ca. Council
    # recommendation 1 of the same date: a git gate evaluates the INVOKING
    # worktree. worktree_root() returns None when the session really is standing
    # in the canonical checkout, and then canonical is the honest answer.
    canonical = os.environ.get("CARR_LOOSE_WORK_REPO") or REPO
    repo = worktree_root(payload.get("cwd") or os.getcwd(), canonical) or canonical
    transcript = payload.get("transcript_path")
    if not transcript or not os.path.exists(transcript):
        return 0

    try:
        written_path, read_tail = _helpers()
        mine = session_writes(transcript, written_path, read_tail)
        loose = loose_tracked(repo) if mine else None
        left = [p for p in mine if p in set(loose)] if loose is not None else []

        # THE SECOND HALF, checked independently of writes: a session can commit
        # work it edited in an earlier turn, or in a worktree, and leave the
        # commit on this machine only. Rule bc9188b4 — "committed-but-unpushed is
        # the same drift as deployed-but-uncommitted". Uncommitted wins the
        # report when both are true, because committing is the first step out.
        ahead = 0
        if not left and session_committed(transcript, read_tail):
            ahead = unpushed_count(repo) or 0
    except Exception:
        return 0                     # fail open on anything unexpected

    if not left and ahead:
        return announce(
            f"LOOSE WORK GATE — this session committed {ahead} change(s) and never "
            f"pushed them.\n\n"
            "  They exist on no other machine. The repository looks finished from\n"
            "  inside this checkout and unchanged from everywhere else, which is\n"
            "  the most expensive shape this failure takes: on 2026-08-14 a correct,\n"
            "  verified fix for a red pull request sat exactly like this for an hour\n"
            "  while the pull request kept failing and kept emailing Joe, and his\n"
            "  read was that a session had fixed it and the fix had not worked.\n\n"
            "  PUSH THEM — main only moves through a pull request, so:\n\n"
            "      git push origin HEAD:refs/heads/<name>\n"
            "      gh pr create --base main --head <name> --title \"...\" --body \"...\"\n\n"
            "  ALREADY LANDED ANOTHER WAY? Then nothing here is missing from origin\n"
            "  and this gate would not have fired; re-read the count above.\n\n"
            "  GENUINELY PARKING THEM:  CARR_ALLOW_LOOSE_WORK=1\n\n"
            "  Say which it is, next time you speak.")

    if not left:
        return 0                     # the quiet, common case: say nothing

    rel = sorted(os.path.relpath(p, repo) for p in left)
    listing = "\n".join(f"      {r}" for r in rel)
    return announce(
        "LOOSE WORK GATE — this session edited these and never landed them:\n\n"
        f"{listing}\n\n"
        "  They are still in the working tree, uncommitted. Nobody else can see\n"
        "  them, they are not on any branch, and the next session to touch this\n"
        "  tree inherits them without knowing whose they are.\n\n"
        "  LAND THEM — main only moves through a pull request, so:\n\n"
        "      ./run.sh worktree <name>          # if you are not already in one\n"
        "      git add <these paths>             # never -A, never .\n"
        "      git commit -F <message-file>\n"
        "      git push origin HEAD:refs/heads/<name>\n"
        "      gh pr create --base main --head <name> --title \"...\" --body \"...\"\n\n"
        "  ALREADY LANDED ELSEWHERE? If these are superseded by something merged,\n"
        "  discard them deliberately rather than leaving them: git checkout -- <paths>\n\n"
        "  GENUINELY PARKING THEM — handing the tree on, or stopping mid-investigation:\n\n"
        "      CARR_ALLOW_LOOSE_WORK=1\n\n"
        "  Say which it is, next time you speak.")


if __name__ == "__main__":
    sys.exit(main())
