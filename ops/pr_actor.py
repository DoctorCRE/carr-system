#!/usr/bin/env python3
"""ops/pr_actor.py — clears a stranded pull request with code, not a model.

WHY THIS IS CODE. Joe, 2026-08-15: "we are actively moving away from AI running
the system and more into code running the system", and before that, "rather than
another scheduled routine, i'd like to have a code script handle this". Both
restate the rule he taught at the 2026-08-13 architecture council: never spend a
model on state, recurrence, routing, validation or any decision already
expressible as a tested predicate, and write every workflow step whose correct
result can be finitely specified as deterministic code. A session proposed a
model-driven scheduled routine for this job anyway, which is the error that
produced this file.

WHAT IT PAIRS WITH. ops/pr-hygiene-check.py NOTICES a stranded pull request.
This one CLEARS it. Split on purpose: the noticing runs read-only in the nightly
health row, and only this half can write.

THE FOUR OUTCOMES, and there are only four:

  close-superseded  every commit is already in main by patch-id. The pull
                    request is finished and nobody noticed. PR #145 on
                    2026-08-14 was exactly this: its fix merged as #151 and the
                    original sat red, emailing Joe, for an hour.

  rebase            the branch is merely behind main and the rebase applies
                    clean. Push it and CI re-runs against current main.

  resolve-generated the ONLY conflicting paths are declared generated files —
                    written wholesale by tooling, so there is no human intent in
                    either copy to preserve. Take main's, continue, push. PR #79
                    on 2026-08-14 was exactly this: its sole conflict was
                    ops/config/gate-baseline.json, rewritten by an hourly job.

  report-only       anything else. A real source conflict, a red check, a draft,
                    or a conflict set that MIXES generated and source files. The
                    actor states what it found and stops.

WHY report-only IS THE DEFAULT AND NOT A FALLBACK. Every case above had to be
argued into existence. A red check is not auto-rebased because a red test may be
a real bug and rebasing it hides the signal under a fresh run. A mixed conflict
set is refused whole rather than partially resolved, because a partial mechanical
resolution is the most dangerous outcome here: it looks handled.

WHY MAIN'S COPY ALWAYS WINS A GENERATED-FILE CONFLICT. Generated files are
written wholesale, so a second writer does not get a merge conflict in the normal
sense — it silently replaces the first. Two pull requests did exactly that on
2026-08-14: one reverted a CI class to an older copy, and one restored a stale
gate hash over a fresh bless, leaving main shipping a gate whose recorded hash
did not match its own file until a human re-blessed it. Taking main's copy is the
only direction that cannot lose a newer write. The branch's own change to that
file is then reproduced by re-running its generator, which is the caller's job
and is why `regenerate` is part of the policy.

NOTHING HAPPENS WITHOUT --execute. The default is a plan on stdout.

RUN IT:
    python3 ops/pr_actor.py                 # plan only
    python3 ops/pr_actor.py --execute       # act
    python3 ops/pr_actor.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.github_reader import GitHubReader  # noqa: E402

# ── policy ──────────────────────────────────────────────────────────────────
# generated_files: paths written wholesale by tooling, where neither side of a
# conflict carries human intent. Adding one here is a real decision: it says
# "no human edits this by hand", and if that is ever false the actor will
# silently discard someone's work.
DEFAULT_POLICY: dict = {
    "base_branch": "main",
    # These are not a judgment call: they are exactly the OWNED list that
    # bin/sync-enforcement-map.py declares for itself — the two files the hourly
    # job rewrites wholesale. The selftest asserts this list still equals that
    # one, so adding a third owned file there without adding it here is caught
    # rather than silently leaving a mechanical conflict unresolvable.
    "generated_files": [
        # Derived render order; its own docstring says "active_rule_ids is
        # DERIVED DATA: its only correct value is the render order. Nothing is
        # decided here". The sole conflict on PR #166, 2026-08-15.
        "ops/config/rule-enforcement-map.json",
        # Rewritten by that same job and by every pull request that changes a
        # gate. The sole conflict on PR #79, 2026-08-14.
        "ops/config/gate-baseline.json",
    ],
    # Re-derive the branch's intent after taking main's copy. Empty means the
    # caller re-runs the generator itself; the actor never invents a recipe.
    "regenerate": {},
    "max_actions_per_run": 5,
}


@dataclass
class PrShape:
    """Everything the decision needs, and nothing else. No GitHub types here,
    so the decision function is testable without a network or a credential."""
    number: int
    is_draft: bool
    commits_in_main: bool
    behind: bool
    conflicts: list[str]
    checks: str  # SUCCESS | FAILURE | IN_PROGRESS | NONE


@dataclass
class Decision:
    action: str
    reason: str
    number: int = 0
    detail: str = ""


@dataclass
class GitResult:
    ok: bool
    detail: str = ""
    conflicts: list[str] = field(default_factory=list)
    # Which declared generated files this rebase actually had to resolve. Kept
    # separate from `conflicts` (which means REFUSED paths) so the plan Joe reads
    # can say "resolved a generated-file conflict" instead of the untrue "no
    # conflicts" — a rebase that silently fixed something should say so.
    resolved: list[str] = field(default_factory=list)


def decide(pr: PrShape, policy: dict) -> Decision:
    """Pure. This is the whole judgment, and it is finitely specified."""
    gen = set(policy.get("generated_files", []))

    if pr.is_draft:
        return Decision("report-only", "draft — the session that opened it says "
                        "it is not done", pr.number)

    # Checked before anything else: a superseded pull request needs no rebase and
    # no conflict resolution, because closing it ends both.
    if pr.commits_in_main:
        return Decision("close-superseded", "every commit is already in main by "
                        "patch-id — this landed elsewhere", pr.number)

    if pr.checks == "IN_PROGRESS":
        return Decision("report-only", "CI is still running — nothing to conclude "
                        "yet", pr.number)

    if pr.checks == "FAILURE":
        return Decision("report-only", "CI is red — a red test may be a real bug, "
                        "and rebasing would bury the signal under a fresh run",
                        pr.number)

    if pr.conflicts:
        unknown = [c for c in pr.conflicts if c not in gen]
        if unknown:
            which = "mixes generated and source files" if len(unknown) < len(pr.conflicts) \
                    else "is a real source conflict"
            return Decision("report-only",
                            f"the conflict {which}: {', '.join(sorted(unknown))} — "
                            f"a partial mechanical resolution is the dangerous case",
                            pr.number)
        return Decision("resolve-generated",
                        f"the only conflicts are declared generated files "
                        f"({', '.join(sorted(pr.conflicts))}), where neither copy "
                        f"carries human intent", pr.number)

    if pr.behind:
        return Decision("rebase", "behind main with no conflicts — rebase and let "
                        "CI re-run against current main", pr.number)

    return Decision("none", "green, current and unconflicted", pr.number)


# ── git mechanics ───────────────────────────────────────────────────────────
# WHY PUSH GETS ITS OWN TIMEOUT. Every git call here used a flat 180s, which is
# generous for a rebase and impossible for a push: ops/githooks/pre-push runs
# ops/ci.sh against the whole repo before a single byte leaves the machine, and
# that takes minutes by design (the reason is in that hook's own header — this
# repo is private on a free plan, so a red GitHub run cannot block a merge and
# the local hook is the only thing that can). On 2026-08-15 the actor rebased
# #191 and #179 correctly and then died on the push with TimeoutExpired,
# leaving both branches exactly as it found them and the traceback as the only
# report. A tool that cannot finish its own last step is worse than one that
# refuses: it burns the work and says nothing a reader can act on.
PUSH_TIMEOUT = 900


def _git(repo: str, *args: str,
         timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, timeout=timeout)


def _conflicting_paths(repo: str) -> list[str]:
    p = _git(repo, "diff", "--name-only", "--diff-filter=U")
    return [x for x in (p.stdout or "").splitlines() if x.strip()]


def rebase_branch(repo: str, branch: str, base: str, policy: dict) -> GitResult:
    """Rebase `branch` onto `base`, resolving ONLY declared-generated conflicts.

    Leaves no rebase in progress on any path out. A half-finished rebase in a
    shared checkout strands the next writer, which is the failure two-writer
    discipline exists to prevent.
    """
    gen = set(policy.get("generated_files", []))
    resolved: list[str] = []

    co = _git(repo, "checkout", branch)
    if co.returncode != 0:
        return GitResult(False, f"cannot check out {branch}: {co.stderr.strip()[:200]}")

    p = _git(repo, "rebase", base)
    if p.returncode == 0:
        return GitResult(True, f"{branch} rebased onto {base} with no conflicts")

    # Resolve, one conflicted stop at a time. A rebase can stop more than once.
    for _ in range(50):
        conflicts = _conflicting_paths(repo)
        if not conflicts:
            break
        unknown = [c for c in conflicts if c not in gen]
        if unknown:
            _git(repo, "rebase", "--abort")
            return GitResult(False,
                             f"refused: conflict outside the generated-file list "
                             f"({', '.join(sorted(unknown))})", unknown)
        for path in conflicts:
            # --ours during a rebase is the BASE being replayed onto, which is
            # main. That direction is deliberate and load-bearing; see the module
            # docstring on the 2026-08-14 lost-update incidents.
            r = _git(repo, "checkout", "--ours", "--", path)
            if r.returncode != 0:
                _git(repo, "rebase", "--abort")
                return GitResult(False, f"could not take main's copy of {path}: "
                                        f"{r.stderr.strip()[:200]}")
            _git(repo, "add", "--", path)
            if path not in resolved:
                resolved.append(path)
        cont = _git(repo, "-c", "core.editor=true", "rebase", "--continue")
        if cont.returncode == 0 and not _conflicting_paths(repo):
            break
        if "no rebase in progress" in (cont.stderr or "").lower():
            break
    else:
        _git(repo, "rebase", "--abort")
        return GitResult(False, "gave up after 50 conflicted stops — this is not a "
                                "mechanical rebase")

    if os.path.exists(os.path.join(repo, ".git", "rebase-merge")) or \
       os.path.exists(os.path.join(repo, ".git", "rebase-apply")):
        _git(repo, "rebase", "--abort")
        return GitResult(False, "rebase did not finish cleanly; aborted rather than "
                                "leave one in progress")

    return GitResult(True,
                     f"{branch} rebased onto {base}"
                     + (f"; took {base}'s copy of {', '.join(sorted(resolved))}"
                        if resolved else " with no conflicts"),
                     resolved=sorted(resolved))


# ── GitHub shell (thin on purpose; the decision above is where the logic is) ──
def _gh_json(args: list[str]) -> object:
    return GitHubReader(timeout=120).json(args)


def probe_conflicts(repo: str, branch: str, base: str, policy: dict) -> GitResult:
    """Find out what ACTUALLY conflicts, by attempting the rebase in isolation.

    This exists because GitHub reports `mergeStateStatus: DIRTY` and nothing
    else — it never names the conflicting paths. Without this, every conflicted
    pull request classifies as report-only and the resolve-generated case can
    never fire on the live path, which would make that case a tested capability
    the actor could not actually reach. That gap is worse than not having the
    case at all: the plan would read as though the conflict had been considered.

    The attempt runs in a throwaway worktree cut from the pushed tips, so the
    shared checkout never moves. The worktree is removed on every path out.
    """
    import tempfile
    scratch = tempfile.mkdtemp(prefix="pr-actor-probe-")
    wt = os.path.join(scratch, "wt")
    try:
        add = _git(repo, "worktree", "add", "--detach", wt, f"origin/{branch}")
        if add.returncode != 0:
            return GitResult(False, f"could not stage a probe worktree: "
                                    f"{add.stderr.strip()[:200]}")
        _git(wt, "checkout", "-B", f"probe/{branch}")
        return rebase_branch(wt, f"probe/{branch}", f"origin/{base}", policy)
    finally:
        _git(repo, "worktree", "remove", "--force", wt)
        import shutil as _sh
        _sh.rmtree(scratch, ignore_errors=True)


def commits_already_in_main(repo: str, branch: str, base: str) -> bool:
    """True when git finds no commit on `branch` missing from `base`.

    `git cherry` compares by patch-id, so a squash-merge of the same change under
    a different SHA still counts as present. That is the case that matters: this
    repo squash-merges, so a superseded branch never shares a SHA with main.
    """
    p = _git(repo, "cherry", base, branch)
    if p.returncode != 0:
        return False
    return not [ln for ln in (p.stdout or "").splitlines() if ln.startswith("+")]


def main() -> int:
    ap = argparse.ArgumentParser(description="clear stranded pull requests with code")
    ap.add_argument("--execute", action="store_true",
                    help="act; without it this prints a plan and changes nothing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo-path", default=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    args = ap.parse_args()

    policy = DEFAULT_POLICY
    base = policy["base_branch"]

    try:
        rows = _gh_json(["pr", "list", "--state", "open", "--limit", "100", "--json",
                         "number,title,headRefName,isDraft,mergeStateStatus,"
                         "statusCheckRollup"])
    except Exception as e:
        print(f"could not read pull requests ({type(e).__name__}: {e})")
        return 1

    plans: list[dict] = []
    assert isinstance(rows, list)
    for row in rows:
        roll = row.get("statusCheckRollup") or []
        states = [(c.get("conclusion") or c.get("status") or "").upper() for c in roll]
        if not states:
            checks = "NONE"
        elif any(s in ("IN_PROGRESS", "QUEUED", "PENDING", "") for s in states):
            checks = "IN_PROGRESS"
        elif any(s in ("FAILURE", "TIMED_OUT", "CANCELLED") for s in states):
            checks = "FAILURE"
        else:
            checks = "SUCCESS"

        branch = row["headRefName"]
        _git(args.repo_path, "fetch", "origin", branch, base)
        superseded = commits_already_in_main(args.repo_path, f"origin/{branch}",
                                             f"origin/{base}")
        merge = (row.get("mergeStateStatus") or "").upper()

        # GitHub never names the conflicting paths, so when it says DIRTY we
        # find out for real rather than guessing. Skipped entirely when the
        # pull request is superseded or a draft: both end in a decision that
        # does not care what conflicts, and a probe costs a worktree.
        conflicts: list[str] = []
        if merge == "DIRTY" and not superseded and not row.get("isDraft"):
            probe = probe_conflicts(args.repo_path, branch, base, policy)
            if probe.ok:
                # It rebases mechanically. Feed back the generated files it
                # actually had to resolve, so the plan says "resolved a
                # generated-file conflict" rather than the untrue "no conflicts".
                conflicts = probe.resolved
            else:
                conflicts = probe.conflicts or ["<rebase refused>"]

        d = decide(PrShape(number=row["number"], is_draft=bool(row.get("isDraft")),
                           commits_in_main=superseded,
                           behind=merge in ("BEHIND", "BLOCKED", "UNSTABLE",
                                            "CLEAN", "DIRTY"),
                           conflicts=conflicts,
                           checks=checks), policy)
        plans.append({"number": row["number"], "title": row.get("title", ""),
                      "branch": branch, "action": d.action, "reason": d.reason})

    actionable = [p for p in plans if p["action"] in
                  ("close-superseded", "rebase", "resolve-generated")]

    if args.execute:
        done = 0
        for p in actionable:
            if done >= policy["max_actions_per_run"]:
                print(f"  stopping at the per-run cap of "
                      f"{policy['max_actions_per_run']}; re-run to continue")
                break
            p["result"] = _execute_one(args.repo_path, p, base, policy)
            done += 1

    if args.json:
        print(json.dumps({"execute": args.execute, "plans": plans}, indent=2))
        return 0

    if not actionable:
        print(f"nothing to clear ({len(plans)} open pull request(s))")
    for p in plans:
        if p["action"] == "none":
            continue
        print(f"  [{p['action']}] #{p['number']} {p['title'][:60]}")
        print(f"      because {p['reason']}")
        if p.get("result"):
            print(f"      -> {p['result']}")
    if not args.execute and actionable:
        print("\nplan only — nothing was changed. Re-run with --execute to act.")
    return 0


def _execute_one(repo: str, plan: dict, base: str, policy: dict) -> str:
    """Carry out ONE plan. Every failure returns a string; none raise, because
    one unclearable pull request must not stop the rest of the run."""
    num, branch, action = plan["number"], plan["branch"], plan["action"]

    if action == "close-superseded":
        p = subprocess.run(
            ["gh", "pr", "close", str(num), "--comment",
             "Closed by the pull-request actor: every commit on this branch is "
             "already in main by patch-id, so this change landed elsewhere. "
             "Nothing here is abandoned. Reopen if that reading is wrong."],
            capture_output=True, text=True, timeout=120)
        return "closed as superseded" if p.returncode == 0 else \
               f"close failed: {(p.stderr or '').strip()[:160]}"

    # rebase and resolve-generated are the same mechanical operation; they
    # differ only in what rebase_branch had to do to get there. Both run in a
    # throwaway worktree so the shared checkout never moves (two-writer rule).
    import tempfile
    scratch = tempfile.mkdtemp(prefix="pr-actor-exec-")
    wt = os.path.join(scratch, "wt")
    try:
        add = _git(repo, "worktree", "add", "--detach", wt, f"origin/{branch}")
        if add.returncode != 0:
            return f"could not stage a worktree: {add.stderr.strip()[:160]}"
        # PLUMB IT, or the push cannot survive its own pre-push hook. That hook
        # runs ops/ci.sh, which needs .venv, out/ and mcp-server/node_modules —
        # all gitignored, so a bare `git worktree add` has none of them and CI
        # dies on a missing module rather than on anything about this branch.
        # bin/worktree.sh --plumb is the same code path a hand-made worktree
        # takes (rule a8c55a47: one job, one implementation), so the actor's
        # trees and a person's trees cannot drift apart.
        subprocess.run([os.path.join(repo, "bin", "worktree.sh"),
                        "--plumb", wt],
                       capture_output=True, text=True, timeout=120)
        _git(wt, "checkout", "-B", branch)
        res = rebase_branch(wt, branch, f"origin/{base}", policy)
        if not res.ok:
            return f"rebase refused, nothing pushed: {res.detail}"
        try:
            push = _git(wt, "push", "--force-with-lease", "origin",
                        f"{branch}:{branch}", timeout=PUSH_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Say which half succeeded. A bare traceback here reads as "the
            # actor is broken" when the rebase was fine and only the pre-push
            # CI ran long.
            return (f"rebased cleanly, but the push exceeded {PUSH_TIMEOUT}s "
                    f"(ops/githooks/pre-push runs ops/ci.sh first) — nothing "
                    f"was pushed, the branch is unchanged")
        if push.returncode != 0:
            return f"rebased but push failed: {(push.stderr or '').strip()[:160]}"
        return f"rebased onto {base} and pushed; CI re-runs against current main"
    finally:
        _git(repo, "worktree", "remove", "--force", wt)
        import shutil as _sh
        _sh.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
