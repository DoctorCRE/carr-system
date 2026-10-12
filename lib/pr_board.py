"""The PR janitor's board: every open pull request, one owned next action each.

WHY THIS EXISTS. Measured 2026-10-12 16:00Z: 40 open pull requests, 39 of them
reporting mergeable UNKNOWN because GitHub had stopped computing it, most with a
FAILURE or CANCELLED check rollup, a median age of about a week, about ten
drafts, many on Codex-lane branches. The fleet merged 100 pull requests in the
same 14 days, so the backlog was not throughput, it was ownership: nothing said,
per pull request, what state it was in and what the one next action was. Every
stale branch burns hosted CI minutes when someone retriggers it and conflicts
with the next pull request that touches the same files.

WHAT IT DOES. Reads every open pull request through `gh` (one GraphQL query per
50), computes a conflict-with-main verdict itself with `git merge-tree` against
main's current tip (GitHub's `mergeable` is used only when the local probe could
not run), and gives each pull request exactly one action from a fixed set:

  MERGE-READY  green, mergeable, approved (or no review required)
  RESTACK      conflicting with main, behind a strict-up-to-date base, or
               holding no CI verdict on its head (cancelled, none, stuck)
  FIX-CI       a red check while the branch merges cleanly: its own code fails
  STALE        no commit in 7 days and red, unverified or draft
  WAIT         a commit inside 24 hours, or green and clean but not yet
               mergeable for a reason only a person settles (draft, review)

Every row states the measured reason behind its action.

WHAT IT NEVER DOES. It never closes a pull request, never pushes to any branch,
never force-pushes, and never comments on a pull request. The conflict probe
fetches objects with no ref written for them and asks `git merge-tree`, which
needs no worktree and moves nothing. The ONLY write it can make is
`--file-loops`: one deduplicated record-layer loop per owner lane naming that
lane's STALE pull requests and the one-tap decision (restack, fix, or close).
Close is a human or orchestrator act, never the janitor's (rule 590b11e1's
bound action, printed on every run).

PAIRS WITH, AND DOES NOT REPLACE: ops/pr-hygiene-check.py notices a stranded
pull request for the health row and ops/pr_actor.py clears the mechanical
cases. This board is the per-PR decision surface those two never gave.

NO SHEBANG AND NO __main__ BLOCK ON PURPOSE: a new file with either is a new
script ingress on the sealed SCAC frontier. `./run.sh pr-board` imports main().
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "ops"))
from git_env import scrubbed_env  # noqa: E402
from lib.github_reader import GitHubReader, GitHubUnreadable  # noqa: E402

SCHEMA = "pr-board/v1"
ACTIONS = ("MERGE-READY", "RESTACK", "FIX-CI", "STALE", "WAIT")
STALE_DAYS = 7
WAIT_HOURS = 24
WINDOW_DAYS = 14
CLOSE_SUGGEST_DAYS = 3 * STALE_DAYS
DEFAULT_REPO = "jbookout/carr-system"
REPORT = SOURCE / "out" / "pr-board.json"
LOOP_MARKER = "[pr-board:"

# Every session pushes as the repository owner, so the author says nothing
# about who owns a pull request there; the branch prefix does. Any other author
# is a person and is their own lane.
SESSION_ACCOUNTS = frozenset({"jbookout"})
LANE_PREFIXES = (("codex/", "codex"), ("codex-", "codex"),
                 ("claude/", "claude"), ("worktree-", "claude"))
LANE_OWNERS = {"codex": "Joe/Codex", "claude": "Joe/Claude", "unattributed": "Joe/Claude"}
KNOWN_PEOPLE = {"dellmccraneycarrus-gif": "Dell"}

BOUND_ACTION = ("on STALE: one deduplicated loop per PR owner lane naming the PRs and the "
                "one-tap decision (restack, fix, or close); close is a human or orchestrator "
                "act, never the janitor's")
FILE_COMMAND = "./run.sh pr-board --file-loops"

RED = frozenset({"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"})
GREEN = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
NO_VERDICT = frozenset({"CANCELLED", "STALE"})

QUERY = """query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){
defaultBranchRef{name target{oid}}
pullRequests(states:OPEN,first:50,after:$cursor,orderBy:{field:CREATED_AT,direction:DESC}){
pageInfo{hasNextPage endCursor}
nodes{number title url isDraft createdAt mergeable mergeStateStatus reviewDecision
changedFiles headRefName headRefOid baseRefName author{login} files(first:100){nodes{path}}
commits(last:1){nodes{commit{oid committedDate statusCheckRollup{state contexts(first:100){nodes{
__typename ... on CheckRun{name conclusion status startedAt completedAt}
... on StatusContext{context state createdAt}}}}}}}}}}}"""


# ── reading ────────────────────────────────────────────────────────────────

def fetch_open_prs(reader: GitHubReader, repo: str, *, max_pages: int = 10) -> tuple[dict, list[dict]]:
    """(default branch {name, oid}, raw pull request nodes). Refuses a partial list."""
    owner, name = repo.split("/", 1)
    nodes: list[dict] = []
    cursor = None
    base: dict = {}
    for _ in range(max_pages):
        args = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        data = reader.json(args)
        if not isinstance(data, dict) or data.get("errors") or not (data.get("data") or {}).get("repository"):
            errors = (data or {}).get("errors") if isinstance(data, dict) else None
            raise GitHubUnreadable(f"pull request query refused: {json.dumps(errors)[:200]}",
                                   kind="invalid_response")
        repository = data["data"]["repository"]
        ref = repository.get("defaultBranchRef") or {}
        base = {"name": ref.get("name") or "main", "oid": (ref.get("target") or {}).get("oid")}
        page = repository["pullRequests"]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return base, nodes
        cursor = page["pageInfo"]["endCursor"]
    raise GitHubUnreadable(f"more than {max_pages * 50} open pull requests; no partial board",
                           kind="pagination_limit")


def _parse(iso: str | None) -> _dt.datetime | None:
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def lane_for(branch: str, author: str) -> str:
    if author and author not in SESSION_ACCOUNTS:
        return f"author:{author}"
    for prefix, lane in LANE_PREFIXES:
        if branch.startswith(prefix):
            return lane
    return "unattributed"


def owner_for(lane: str) -> str:
    if lane.startswith("author:"):
        return KNOWN_PEOPLE.get(lane.split(":", 1)[1], "Joe")
    return LANE_OWNERS.get(lane, "Joe/Claude")


def _checks(commit: dict, window_start: _dt.datetime) -> dict:
    """Conclusion counts, the verdict, failing names, and head-commit CI minutes.

    Minutes are per check run, ceil((completed - started) / 60s), the unit
    GitHub bills a job in; only runs completed inside the window count.
    """
    rollup = commit.get("statusCheckRollup") or {}
    contexts = ((rollup.get("contexts") or {}).get("nodes")) or []
    counts: dict[str, int] = {}
    failing: list[str] = []
    minutes = 0
    last_done: _dt.datetime | None = None
    for c in contexts:
        if c.get("__typename") == "StatusContext":
            state = (c.get("state") or "").upper()
            name = c.get("context") or "status"
            state = {"PENDING": "PENDING", "EXPECTED": "PENDING"}.get(state, state)
        else:
            name = c.get("name") or "check"
            status = (c.get("status") or "").upper()
            state = (c.get("conclusion") or "").upper() if status == "COMPLETED" else (status or "PENDING")
            started, completed = _parse(c.get("startedAt")), _parse(c.get("completedAt"))
            if started and completed and completed >= window_start and completed >= started:
                minutes += math.ceil((completed - started).total_seconds() / 60)
            if completed and (last_done is None or completed > last_done):
                last_done = completed
        state = state or "PENDING"
        counts[state] = counts.get(state, 0) + 1
        if state in RED:
            failing.append(name)
    if not counts:
        verdict = "none"
    elif any(s in RED for s in counts):
        verdict = "red"
    elif any(s not in GREEN and s not in NO_VERDICT for s in counts):
        verdict = "pending"
    elif any(s in NO_VERDICT for s in counts):
        verdict = "cancelled"
    else:
        verdict = "green"
    return {"counts": dict(sorted(counts.items())), "verdict": verdict,
            "failing": sorted(set(failing)), "head_ci_minutes": minutes,
            "last_completed_at": last_done.strftime("%Y-%m-%dT%H:%M:%SZ") if last_done else None}


def normalize(node: dict, *, now: _dt.datetime, window_days: int = WINDOW_DAYS) -> dict:
    """One raw GraphQL node to the facts the decision reads. No network."""
    commit = ((node.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}
    author = (node.get("author") or {}).get("login") or ""
    branch = node.get("headRefName") or ""
    last = commit.get("committedDate") or node.get("createdAt")
    lane = lane_for(branch, author)
    facts = {
        "number": node["number"], "title": node.get("title") or "", "url": node.get("url") or "",
        "branch": branch, "author": author, "lane": lane, "owner": owner_for(lane),
        "created_at": node.get("createdAt"), "last_commit_at": last,
        "idle_hours": round((now - _parse(last)).total_seconds() / 3600, 1) if last else None,
        "draft": bool(node.get("isDraft")),
        "github_mergeable": (node.get("mergeable") or "UNKNOWN").upper(),
        "merge_state": (node.get("mergeStateStatus") or "UNKNOWN").upper(),
        "review_decision": (node.get("reviewDecision") or "").upper() or None,
        "changed_files": node.get("changedFiles") or 0,
        "files": [f["path"] for f in ((node.get("files") or {}).get("nodes") or [])],
        "head_oid": commit.get("oid") or node.get("headRefOid"),
        "checks": _checks(commit, now - _dt.timedelta(days=window_days)),
    }
    facts["verdict_age_hours"] = _verdict_age(facts, now)
    return facts


def _verdict_age(p: dict, now: _dt.datetime) -> float | None:
    done = _parse(p["checks"]["last_completed_at"])
    return round((now - done).total_seconds() / 3600, 1) if done else None


# ── the conflict probe: objects only, no ref, no worktree, no push ─────────

def _git(repo_path: str, *args: str, env: Mapping[str, str] | None, timeout: int = 120):
    return subprocess.run(["git", "-C", repo_path, *args], env=dict(env) if env is not None else scrubbed_env(),
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def probe_conflicts(repo_path: str, base: dict, prs: list[dict], *, remote: str = "origin",
                    env: Mapping[str, str] | None = None, fetch: bool = True) -> dict[int, dict]:
    """{number: {verdict clean|conflict|unknown, files, behind, source}}.

    One `git fetch --no-write-fetch-head` brings main's tip and every
    refs/pull/N/head as objects; nothing names them afterwards, so no ref is
    left behind. `git merge-tree --write-tree` then answers per pull request
    whether it merges into main's CURRENT tip, which GitHub's mergeable only
    sometimes does (39 of 40 read UNKNOWN when this was built).
    """
    out: dict[int, dict] = {}
    base_oid = base.get("oid")
    if fetch:
        refspecs = [f"refs/heads/{base.get('name') or 'main'}"] + [f"refs/pull/{p['number']}/head" for p in prs]
        # --refmap= switches off git's opportunistic remote-tracking update,
        # so not even refs/remotes/origin/main moves.
        flags = ("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=")
        got = _git(repo_path, *flags, remote, *refspecs, env=env, timeout=600)
        if got.returncode != 0:
            # One unreadable PR ref (say, a deleted fork) fails the whole batch;
            # retry one at a time so the rest still get a real verdict.
            for spec in refspecs:
                _git(repo_path, *flags, remote, spec, env=env, timeout=120)
    for p in prs:
        head = p.get("head_oid")
        verdict = {"verdict": "unknown", "files": [], "behind": None, "source": "merge-tree"}
        if base_oid and head:
            mt = _git(repo_path, "merge-tree", "--write-tree", "--name-only", "--no-messages",
                      base_oid, head, env=env)
            lines = [ln for ln in mt.stdout.splitlines() if ln.strip()]
            if mt.returncode == 0:
                verdict["verdict"] = "clean"
            elif mt.returncode == 1 and lines:
                verdict.update(verdict="conflict", files=lines[1:])
            behind = _git(repo_path, "rev-list", "--count", f"{head}..{base_oid}", env=env)
            if behind.returncode == 0 and behind.stdout.strip().isdigit():
                verdict["behind"] = int(behind.stdout.strip())
        out[p["number"]] = verdict
    return out


def apply_conflicts(prs: list[dict], probed: Mapping[int, dict]) -> None:
    """The local probe wins; GitHub's mergeable fills in only where it could not run."""
    github = {"CONFLICTING": "conflict", "MERGEABLE": "clean"}
    for p in prs:
        verdict = dict(probed.get(p["number"]) or {"verdict": "unknown", "files": [], "behind": None})
        if verdict["verdict"] == "unknown" and p["github_mergeable"] in github:
            verdict.update(verdict=github[p["github_mergeable"]], source="github")
        verdict.setdefault("source", "merge-tree")
        p["conflict"] = verdict


# ── the decision: an ordered table, first match wins ───────────────────────

def _days(hours: float) -> str:
    return f"{hours / 24:.1f}d" if hours >= 24 else f"{hours:.0f}h"


def _ci_text(p: dict) -> str:
    c = p["checks"]
    if c["verdict"] == "red":
        names = ", ".join(c["failing"][:3]) + (" +%d" % (len(c["failing"]) - 3) if len(c["failing"]) > 3 else "")
        return f"red ({names})"
    return {"green": "green", "pending": "checks still running", "none": "no checks on head",
            "cancelled": "cancelled, no verdict"}[c["verdict"]]


def _conflict_text(p: dict) -> str:
    c = p["conflict"]
    behind = f", {c['behind']} behind main" if c.get("behind") else ""
    if c["verdict"] == "conflict":
        files = ", ".join(c["files"][:3]) + (" +%d" % (len(c["files"]) - 3) if len(c["files"]) > 3 else "")
        return f"conflicts with main{(' in ' + files) if files else ''} [{c['source']}]{behind}"
    if c["verdict"] == "clean":
        return f"merges clean [{c['source']}]{behind}"
    return "conflict unknown (probe and GitHub both silent)"


def _approved(p: dict) -> bool:
    return p["review_decision"] in (None, "APPROVED")


def _old_green(p: dict) -> bool:
    """A green verdict a week old was earned against a base that has since
    moved (100 merges in 14 days when this was built); it is not a merge signal."""
    age = p["verdict_age_hours"]
    return p["checks"]["verdict"] == "green" and (age is None or age >= STALE_DAYS * 24)


def _is_merge_ready(p: dict) -> bool:
    return (not p["draft"] and p["checks"]["verdict"] == "green" and not _old_green(p)
            and p["conflict"]["verdict"] == "clean"
            and _approved(p) and p["merge_state"] not in ("BEHIND", "BLOCKED", "DIRTY"))


def _is_wait(p: dict) -> bool:
    return p["idle_hours"] is not None and p["idle_hours"] < WAIT_HOURS


def _is_stale(p: dict) -> bool:
    return (p["idle_hours"] is not None and p["idle_hours"] >= STALE_DAYS * 24
            and (p["draft"] or p["checks"]["verdict"] != "green"))


def _is_restack(p: dict) -> bool:
    return (p["conflict"]["verdict"] == "conflict" or p["merge_state"] == "BEHIND"
            or p["checks"]["verdict"] in ("cancelled", "none", "pending") or _old_green(p))


def _is_fix_ci(p: dict) -> bool:
    return p["checks"]["verdict"] == "red" or p["review_decision"] == "CHANGES_REQUESTED"


def _always(p: dict) -> bool:
    return True


# Order is the policy. MERGE-READY first so a green, clean head is never held
# for being old; WAIT before STALE so a session mid-iteration is never nagged;
# STALE before RESTACK and FIX-CI because a week of silence is a question for
# the owner (restack, fix, or close), not a chore to do on their behalf.
RULES: tuple[tuple[str, Callable[[dict], bool]], ...] = (
    ("MERGE-READY", _is_merge_ready),
    ("WAIT", _is_wait),
    ("STALE", _is_stale),
    ("RESTACK", _is_restack),
    ("FIX-CI", _is_fix_ci),
    ("WAIT", _always),
)


def suggested_decision(p: dict) -> str:
    """The default the one-tap loop offers for a STALE pull request."""
    if p["idle_hours"] is not None and p["idle_hours"] >= CLOSE_SUGGEST_DAYS * 24:
        return "close"
    if p["checks"]["verdict"] == "red" and p["conflict"]["verdict"] != "conflict":
        return "fix"
    return "restack"


def classify(p: dict, rules=RULES) -> tuple[str, str]:
    action = next(a for a, test in rules if test(p))
    idle = _days(p["idle_hours"]) if p["idle_hours"] is not None else "unknown"
    draft = "draft, " if p["draft"] else ""
    if action == "MERGE-READY":
        why = f"green, {_conflict_text(p)}, review {p['review_decision'] or 'not required'}"
    elif action == "STALE":
        why = (f"{draft}no commit for {idle}; {_ci_text(p)}; {_conflict_text(p)}; "
               f"suggested: {suggested_decision(p)}")
    elif action == "RESTACK":
        if p["conflict"]["verdict"] == "conflict":
            why = f"{draft}{_conflict_text(p)}; {_ci_text(p)}"
        elif p["merge_state"] == "BEHIND":
            why = f"{draft}behind a strict up-to-date base; {_ci_text(p)}"
        elif _old_green(p):
            age = _days(p["verdict_age_hours"]) if p["verdict_age_hours"] is not None else "unknown"
            why = (f"{draft}green verdict is {age} old; a restack earns a fresh one on current main "
                   f"before merge; {_conflict_text(p)}")
        else:
            why = (f"{draft}{_ci_text(p)} for {idle}; a restack reruns CI on current main "
                   f"instead of the stale base; {_conflict_text(p)}")
    elif action == "FIX-CI":
        why = (f"{draft}{_ci_text(p)} while it {_conflict_text(p)}" if p["checks"]["verdict"] == "red"
               else f"{draft}review requested changes; {_ci_text(p)}")
    elif _is_wait(p):
        why = f"{draft}last commit {idle} ago; {_ci_text(p)}"
    else:
        reasons = [r for r, hit in (("draft", p["draft"]),
                                    (f"review {p['review_decision']}", not _approved(p)),
                                    (f"merge state {p['merge_state']}",
                                     p["merge_state"] in ("BLOCKED", "DIRTY"))) if hit]
        why = f"{_ci_text(p)}, {_conflict_text(p)}, held by {', '.join(reasons) or 'nothing measured'}"
    return action, why


# ── the board ──────────────────────────────────────────────────────────────

def build_board(base: dict, nodes: list[dict], probed: Mapping[int, dict] | None, *, now: _dt.datetime,
                repo: str = DEFAULT_REPO, window_days: int = WINDOW_DAYS, rules=RULES) -> dict:
    prs = [normalize(n, now=now, window_days=window_days) for n in nodes]
    apply_conflicts(prs, probed or {})
    for p in prs:
        p["action"], p["reason"] = classify(p, rules)
        if p["action"] == "STALE":
            p["decision"] = suggested_decision(p)
    prs.sort(key=lambda p: (ACTIONS.index(p["action"]), -(p["idle_hours"] or 0)))
    by_action = {a: sum(1 for p in prs if p["action"] == a) for a in ACTIONS}
    lanes: dict[str, dict[str, int]] = {}
    for p in prs:
        lanes.setdefault(p["lane"], {a: 0 for a in ACTIONS})[p["action"]] += 1
    stale = [p for p in prs if p["action"] == "STALE"]
    idle = sorted(p["idle_hours"] for p in prs if p["idle_hours"] is not None)
    return {
        "schema": SCHEMA, "ok": True, "repo": repo,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base": base, "window_days": window_days,
        "thresholds": {"stale_days": STALE_DAYS, "wait_hours": WAIT_HOURS,
                       "close_suggest_days": CLOSE_SUGGEST_DAYS},
        "totals": {
            "open": len(prs), "drafts": sum(p["draft"] for p in prs), "by_action": by_action,
            "by_lane": dict(sorted(lanes.items())),
            "conflicting": sum(p["conflict"]["verdict"] == "conflict" for p in prs),
            "github_mergeable_unknown": sum(p["github_mergeable"] == "UNKNOWN" for p in prs),
            "median_idle_days": round(idle[len(idle) // 2] / 24, 1) if idle else None,
            "head_ci_minutes": {"all": sum(p["checks"]["head_ci_minutes"] for p in prs),
                                "stale": sum(p["checks"]["head_ci_minutes"] for p in stale),
                                "basis": (f"head commit check runs completed in the last {window_days} days, "
                                          "ceil minutes per job; earlier pushes to the same branch are "
                                          "not counted, so this is a floor")},
        },
        "bound_action": BOUND_ACTION, "file_command": FILE_COMMAND,
        "rows": prs,
    }


def render(board: dict) -> str:
    rows = board["rows"]
    head = f"{'#':>5}  {'ACTION':<11} {'LANE':<13} {'IDLE':>6} {'CI':<9} {'MERGE':<8} {'FILES':>5}  REASON"
    out = [f"PR board · {board['repo']} · {board['generated_at']} · base "
           f"{board['base'].get('name')}@{(board['base'].get('oid') or '?')[:9]}", "", head]
    for p in rows:
        idle = _days(p["idle_hours"]) if p["idle_hours"] is not None else "?"
        lane = p["lane"] if len(p["lane"]) <= 13 else p["lane"][:12] + "…"
        out.append(f"{p['number']:>5}  {p['action']:<11} {lane:<13} {idle:>6} {p['checks']['verdict']:<9} "
                   f"{p['conflict']['verdict']:<8} {p['changed_files']:>5}  "
                   f"{'[draft] ' if p['draft'] else ''}{p['title'][:60]} — {p['reason']}")
    t = board["totals"]
    out += ["", "TOTALS  " + " · ".join(f"{a} {n}" for a, n in t["by_action"].items())
            + f" · open {t['open']} · drafts {t['drafts']} · conflicting {t['conflicting']}"
            + f" · GitHub mergeable UNKNOWN {t['github_mergeable_unknown']}"
            + f" · median idle {t['median_idle_days']}d",
            "LANES   " + " · ".join(f"{lane}: " + ", ".join(f"{a} {n}" for a, n in c.items() if n)
                                    for lane, c in t["by_lane"].items()),
            f"CI MIN  stale set {t['head_ci_minutes']['stale']} of {t['head_ci_minutes']['all']} "
            f"({t['head_ci_minutes']['basis']})",
            f"BOUND ACTION  {board['bound_action']} · run: {board['file_command']}",
            "NEVER  this board never closes, pushes, force-pushes or comments on a pull request."]
    return "\n".join(out)


# ── the one write: a deduplicated loop per owner lane ──────────────────────

def lane_marker(lane: str) -> str:
    return f"{LOOP_MARKER}{lane}]"


def loop_body(lane: str, stale: list[dict], generated_at: str) -> str:
    lines = [f"{lane_marker(lane)} {len(stale)} stale pull request(s) in the {lane} lane as of "
             f"{generated_at}. One tap each: restack, fix, or close. Close is a human or orchestrator "
             f"act; the PR janitor never closes, pushes or comments."]
    # No idle hours or behind-counts here: those move every night, and a body
    # that changes every night is refiled every night. Only the set, the
    # suggestion and the two verdicts are compared.
    for p in stale:
        lines.append(f"#{p['number']} {p['branch']} — suggested {p['decision']} "
                     f"(CI {p['checks']['verdict']}, {p['conflict']['verdict']} vs main"
                     f"{', draft' if p['draft'] else ''}) {p['url']}")
    lines.append(f"Refreshed by `{FILE_COMMAND}`; the board itself is out/pr-board.json.")
    return "\n".join(lines)


def mutation_key(verb: str, payload: dict) -> str:
    return "pr-board-" + hashlib.sha256(json.dumps([verb, payload], sort_keys=True).encode()).hexdigest()


def file_loops(board: dict, call: Callable[[str, dict], dict]) -> list[dict]:
    """add-loop / update-loop / close-loop, one loop per lane, deduped by marker.

    A lane with STALE pull requests gets its loop created or refreshed; a lane
    whose loop exists and has none left gets it closed (the LOOP, never a PR).
    """
    found = call("loop-board", {"kind": "open_loop", "domain": "system", "search": LOOP_MARKER, "limit": 300})
    inventory = found.get("loops")
    if not isinstance(inventory, list) or len(inventory) >= 300:
        raise RuntimeError("pr-board loop inventory unreadable or incomplete; nothing filed")
    existing: dict[str, dict] = {}
    for row in inventory:
        text = (row.get("label") or "") + " " + (row.get("body") or "")
        start = text.find(LOOP_MARKER)
        if start < 0:
            continue
        lane = text[start + len(LOOP_MARKER):text.index("]", start)]
        if lane in existing:
            raise RuntimeError(f"two open loops carry {lane_marker(lane)}; resolve by hand before filing")
        existing[lane] = call("read-loop", {"kind": "open_loop", "number": row["number"]})["loop"]
    stale_by_lane: dict[str, list[dict]] = {}
    for p in board["rows"]:
        if p["action"] == "STALE":
            stale_by_lane.setdefault(p["lane"], []).append(p)
    actions = []
    for lane in sorted(set(stale_by_lane) | set(existing)):
        stale, loop = stale_by_lane.get(lane, []), existing.get(lane)
        if not stale:
            verb = "close-loop"
            payload = {"loop_id": loop["loop_id"], "base_version": int(loop["version"]), "resolution": "done",
                       "outcome": f"No stale pull requests left in the {lane} lane ({board['generated_at']})."}
        else:
            # The marker line carries a timestamp; compare without it so an
            # unchanged stale set is not rewritten every night.
            body = loop_body(lane, stale, board["generated_at"])
            if loop and _same_set(loop.get("body") or "", body):
                actions.append({"lane": lane, "verb": "unchanged", "prs": [p["number"] for p in stale]})
                continue
            payload = {"body": body, "owner": owner_for(lane)}
            if loop:
                verb = "update-loop"
                payload.update(loop_id=loop["loop_id"], base_version=int(loop["version"]))
            else:
                verb = "add-loop"
                payload.update(kind="open_loop", domain="system", marker="none", blocker="ruling",
                               blocker_detail=(f"the {lane} lane owner's call per stale PR: restack, fix, "
                                               f"or close; close is a human or orchestrator act"),
                               unblocks="frees hosted CI minutes and the files these branches hold")
        payload["idempotency_key"] = mutation_key(verb, payload)
        result = call(verb, payload)
        if result.get("error") or result.get("ok") is False:
            raise RuntimeError(f"{verb} for {lane} refused: {str(result.get('error'))[:200]}")
        actions.append({"lane": lane, "verb": verb, "prs": [p["number"] for p in stale]})
    return actions


def _same_set(old: str, new: str) -> bool:
    return old.splitlines()[1:] == new.splitlines()[1:]


def record_verb(verb: str, payload: dict, *, timeout: int = 60) -> dict:
    """The allowlisted Bash door (`./run.sh call`), never the generic passthrough."""
    proc = subprocess.run([str(SOURCE / "run.sh"), "call", verb, json.dumps(payload)], cwd=SOURCE,
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    text = proc.stdout or ""
    if "{" not in text:
        raise RuntimeError(f"{verb} returned no JSON (exit {proc.returncode})")
    return json.loads(text[text.index("{"):])


# ── entry ──────────────────────────────────────────────────────────────────

def write_report(board: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pr-board-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(board, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None, *, reader: GitHubReader | None = None,
         call: Callable[[str, dict], dict] | None = None, now: _dt.datetime | None = None,
         git_env: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.sh pr-board", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the board as JSON")
    ap.add_argument("--out", default=str(REPORT), help="report path (default out/pr-board.json)")
    ap.add_argument("--no-write", action="store_true", help="print only; do not write the report")
    ap.add_argument("--file-loops", action="store_true",
                    help="file/refresh one deduplicated loop per owner lane for STALE PRs (the only write)")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the local git merge-tree probe; use GitHub's mergeable alone")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--repo-path", default=str(SOURCE), help="checkout used for the merge-tree probe")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    args = ap.parse_args(argv)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        base, nodes = fetch_open_prs(reader or GitHubReader(timeout=120), args.repo)
    except GitHubUnreadable as exc:
        # An unreadable GitHub is not an empty backlog (no negative finding from
        # a failed collection). Unauthenticated is "not configured", exit 78.
        msg = f"UNKNOWN: could not read open pull requests ({exc.kind}: {exc})"
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": msg}) if args.json else msg)
        return 78 if exc.kind == "authentication" else 1
    probed: dict[int, dict] = {}
    if not args.no_probe:
        stub = [normalize(n, now=now) for n in nodes]
        probed = probe_conflicts(args.repo_path, base, stub, env=git_env)
    board = build_board(base, nodes, probed, now=now, repo=args.repo, window_days=args.window_days)
    if args.file_loops:
        board["loops"] = file_loops(board, call or record_verb)
    if not args.no_write:
        write_report(board, Path(args.out))
    print(json.dumps(board, indent=2) if args.json else render(board))
    return 0
