"""canonical_freshness — WR-000040 AC-FRESH, as a library with no door of its own.

AC-FRESH: "A scheduled fast-forward keeps canonical within one day of
origin/main and pages visibly when the tree is dirty or the fast-forward
fails."

WHY THIS EXISTS, measured 2026-09-11. The canonical checkout at ~/carr-system
sat at d75a4ab6 — 111 commits behind origin/main, 0 ahead — and had done for
nine days. Twenty of its twenty-four "modified" paths held bytes that are
already on origin/main, in commits canonical has never pulled: the tree was not
dirty with anyone's work, it was dirty with the past. Every derived thing —
dependency caches, repo-vs-production checks, the Dell migration precondition —
inherited that.

WHY IT IS A LIBRARY AND NOT TWO SCRIPTS, which is a fact about this repository
rather than a style preference. ops/scac-mutation-inventory.mjs discovers a SCAC
ingress from the git index: `isScriptEntrypoint` counts any tracked file with a
shebang, and any .py whose source carries a main guard. Two new executable
scripts would have been two new rows in a SEALED inventory whose review overlay
(`current_source_review`) can only re-digest ingresses it already knows —
`assertCurrentSourceInventoryMatchesFixture` throws "unknown ingress" on an
added key. A new row therefore costs a whole registry successor. This module
carries NO shebang and NO such guard on purpose: it is reached only through
tools/repo-hygiene-janitor.py, which is already an inventoried ingress, so the
machinery lands against one already-registered row. That is the same shape
tools/room-bridge/worktree_runtime_isolation.py already uses, and the janitor
already knows how to load such a module by path.

── AN UNOBSERVED FACT IS NOT A CLEAN ONE ────────────────────────────────────

THE RULE THIS MODULE IS BUILT AROUND, and the one its first revision got wrong:
a git command that FAILS produces no measurement, and the absence of a
measurement must never be spelled the same way as a good one. `_Git.out` used to
take a `default=`, so a failed `git status` came back as the empty string and
read as ZERO tracked modifications, a failed `git rev-list` came back as the
word "unknown", and a failed `git log` came back as age zero. A repository whose
state could not be read at all therefore looked exactly like a clean, current
one, and the watchdog returned OK about it.

So there are three states here, not two, and every caller must name which it is
acting on: OBSERVED-AND-FINE, OBSERVED-AND-WRONG, and UNOBSERVED. `_Git.out`
returns None for the third and has no `default=` parameter to convert it into
the first. The fast-forward treats UNOBSERVED as FAILED, because a job that
cannot prove the tree holds nobody's edit has not earned the right to move the
branch. The watchdog treats it as an ALARM in its own right, with its own code
and its own named commands, because "I could not look" is a page-worthy fact
about the machine that is supposed to be looking.

── CANONICAL MEANS main, AND THAT IS CHECKED, NOT ASSUMED ───────────────────

Both modes are about ONE branch. "Keep canonical within one day of origin/main"
is a statement about main; measuring `HEAD..origin/main` from some other branch
answers a different question and calls the answer freshness. Worse, a feature
branch that happens to be a clean ancestor of origin/main fast-forwards
SUCCESSFULLY — the job would quietly drag somebody's branch onto main's tip,
report OK, and leave the actual main exactly as stale as it found it.

So: `fast_forward` refuses unless the checked-out branch is exactly `main`, and
`watchdog` alarms when it is not. A detached HEAD reads as the branch name
"HEAD", which is not `main`, and is refused by the same test rather than by a
special case.

── THE PROPERTY fast_forward() MUST HOLD ────────────────────────────────────

IT NEVER DESTROYS ANYTHING. The only git command that moves the branch is
`git merge --ff-only`, which fails rather than rewriting history, and it is not
reached at all unless the tree is on main and clean of TRACKED modifications.
No reset, no checkout of a path, no clean, no stash — a fast-forward that had to
discard something to succeed would be the exact failure this job is supposed to
page about.

THE GUARD IS SAMPLED TWICE, once before the fetch and once in the instant before
the merge, and that is not belt-and-braces. A fetch is a network round trip:
seconds, sometimes longer. A single sample taken before it is a statement about
the tree as it WAS, and the merge acts on the tree as it IS. Somebody starting
an edit in canonical while the fetch is in flight is the ordinary case, not an
exotic one, and `git merge --ff-only` will happily overwrite an unstaged
modification to a file the fast-forward touches. So `_preflight` runs again with
nothing between it and the merge.

TRACKED DIRT ONLY, which is the accepted plan's wording and not a softening.
Untracked paths are a separate settlement question: WR-000040's AC-CLEAN routes
each one to Joe for a discard-or-land ruling, and a freshness job that refused
to run until that ruling arrived would simply never run. So untracked paths are
REPORTED and do not block the fast-forward; tracked modifications block it
absolutely, because those are somebody's edit.

── THE PROPERTY watchdog() MUST HOLD ────────────────────────────────────────

IT OBSERVES THE REPOSITORY, NEVER THE JOB. A freshness machine whose only alarm
lives inside the freshness job is silent in the one failure that matters most —
the job never fired at all. launchd drops an agent whose plist is unloaded,
whose program is missing, or whose machine slept through every window, and it
says nothing. So `watchdog()` reaches its verdict from the repository alone and
never calls the fast-forward or reads anything it wrote. Sharing a module with
the job it watches does not weaken that; calling it would, and a test asserts
the call is absent.

WHAT IT PAGES ON: an unobservable repository; tracked dirt; canonical sitting on
a branch other than main; staleness past the AC-FRESH bar; canonical holding
commits origin/main does not have; and an unverifiable fetch.
WHAT IT DOES NOT PAGE ON, deliberately: untracked paths. AC-CLEAN routes those
to Joe for a per-path ruling, and a watchdog that paged daily about four paths
awaiting a human decision would train its reader to ignore it. They are COUNTED
and NAMED in every report — visible, never alarming.

HOW IT PAGES, by two independent routes so one channel failing is not silence:

  1. A durable record-layer problem report through the Bash door,
     `./run.sh call report-problem`, with the COMPLETE registered payload —
     idempotency_key, situation, title, desired_outcome, acceptance_criteria.
     The verb's validator (`validate` in mcp-server/src/work-request-intake.js)
     takes an EXACT key set and refuses an incomplete call outright, so a
     situation-only page is not a page at all — it is a rejected call.
  2. A nonzero exit, so bin/run-scheduled.sh records a failed run and the
     service shows red in `ops-record health`.

Route 1 failing never suppresses route 2. A watchdog that could be silenced by
an unreachable store would be worse than none.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

# Exit codes. Each is a distinct thing a reader needs to tell apart, and they
# are named here so a changed code fails a test loudly rather than turning an
# assertion into a tautology.
OK = 0
REFUSED_TRACKED_DIRT = 3
REFUSED_AHEAD = 4
FAILED = 5
ALARM = 6
REFUSED_WRONG_BRANCH = 7
BAD_REPOSITORY = 64

DEFAULT_MAX_AGE_HOURS = 24

# The one branch both modes are about. See the header: measuring freshness from
# anywhere else answers a different question and calls the answer freshness.
CANONICAL_BRANCH = "main"

# How an absent measurement is SPELLED in reports and in the page. It is a word
# a reader cannot mistake for a number, and it is never the value logic branches
# on — logic branches on None, which no default can manufacture.
UNOBSERVED = "unobserved"

# SUBMODULES WHOSE CONTENT IS DIRTY BY DESIGN, named exactly (rule bd4a6d22: a
# state Joe chose permanently is read as accepted, never routed around). On
# 2026-09-23 the dictation rig's build applied its own tracked patch,
# tools/dictation-rig/patches/0001-menubar-visible-recording.patch, inside the
# quill submodule, and the running quill binary is built from that tree. Git
# reports it as ` M tools/dictation-rig/vendor/quill`, and this job refused
# every fast-forward for the rest of the day on it, so merged gate fixes never
# reached the hooks that run from canonical.
#
# What is accepted is NARROW. The submodule's checked-out COMMIT must still be
# the one the superproject records (only its working content differs), and a
# fast-forward that would MOVE that recorded commit still refuses. A dirty
# pointer, any other submodule, or any ordinary file edit refuses exactly as
# before. Every run prints what it accepted, so the state stays visible.
ACCEPTED_DIRTY_SUBMODULES = ("tools/dictation-rig/vendor/quill",)

# The verb's own caps, copied from mcp-server/src/work-request-intake.js so a
# page is never rejected for length. situation is capped at 1000 there; 900
# leaves room for the framing the server adds and matches the ~926 ceiling
# measured against the live verb.
SITUATION_CAP = 900
TITLE_CAP = 200
DESIRED_OUTCOME_CAP = 2000
CRITERION_TEXT_CAP = 500


class _Git:
    """Every git call this module makes, in one place, so a reader can confirm
    the list contains nothing that writes to the worktree or the index."""

    READ_ONLY = ("status", "rev-list", "rev-parse", "log", "fetch")

    def __init__(self, repository: Path):
        self.repository = Path(repository)

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        if args and args[0] not in self.READ_ONLY and args[0] != "merge":
            raise AssertionError(f"canonical_freshness does not run `git {args[0]}`")
        return subprocess.run(["git", "-C", str(self.repository), *args],
                              capture_output=True, text=True)

    def out(self, *args: str) -> str | None:
        """The command's trimmed stdout, or None when the command FAILED.

        THERE IS DELIBERATELY NO `default=`. A default is how a failed
        observation becomes a measurement, and every false green this module is
        capable of producing came from exactly that conversion. None is not a
        value; it is the absence of one, and each caller has to say out loud
        what the absence means for it.
        """
        done = self(*args)
        return done.stdout.strip() if done.returncode == 0 else None


def _is_checkout(repository: Path) -> bool:
    # A worktree's .git is a FILE, not a directory, so this tests for either.
    return (repository / ".git").exists()


def _counts(git: _Git) -> dict[str, list[str] | None]:
    """Tracked and untracked paths, or None for either that could not be read.

    None is not an empty list. An empty list means git looked and found nothing;
    None means git could not look, and a caller that renders the two the same
    way reports a broken repository as a clean one.
    """
    tracked = git.out("status", "--porcelain", "--untracked-files=no")
    untracked = git.out("status", "--porcelain", "--untracked-files=normal")
    lines = None if tracked is None else [line for line in tracked.splitlines() if line]
    accepted = _accepted_submodule_dirt(git) if lines else []
    return {
        "tracked": None if lines is None
        # `out` strips the whole output, so the first line can lose its leading
        # status space; the path is whatever follows the two status columns.
        else [line for line in lines if line[2:].lstrip() not in accepted],
        "untracked": None if untracked is None
        else [line for line in untracked.splitlines() if line.startswith("??")],
        "accepted": accepted,
    }


def _accepted_submodule_dirt(git: _Git) -> list[str]:
    """ACCEPTED_DIRTY_SUBMODULES paths whose dirt is content-only, proven by git.

    Porcelain v2 spells a submodule's state as `S<c><m><u>`: c is `C` when its
    checked-out commit differs from the recorded one, m is `M` for tracked
    content changes. Only `.` for c counts. An unreadable status accepts
    nothing, so a failed observation can never widen the exemption.
    """
    raw = git.out("status", "--porcelain=v2", "--untracked-files=no")
    if raw is None:
        return []
    accepted = []
    for line in raw.splitlines():
        parts = line.split(" ", 8)
        if len(parts) != 9 or parts[0] != "1":
            continue
        xy, sub, path = parts[1], parts[2], parts[8]
        if (path in ACCEPTED_DIRTY_SUBMODULES and xy == ".M"
                and len(sub) == 4 and sub[0] == "S" and sub[1] == "."):
            accepted.append(path)
    return accepted


def _count_text(paths: list[str] | None) -> Any:
    return UNOBSERVED if paths is None else len(paths)


def _commit_count(raw: str | None) -> int | None:
    """`git rev-list --count` as an integer, or None when it was not observed.

    Output that is not a plain number is treated as not observed too: a count
    this module cannot parse is a count it does not have.
    """
    return int(raw) if raw is not None and raw.isdigit() else None


# ─────────────────────────────────────────────────────────────────────────────
# The fast-forward.
# ─────────────────────────────────────────────────────────────────────────────


def _preflight(git: _Git, *, when: str, out: TextIO, err: TextIO) -> int | None:
    """The whole permission to move the branch, in one place, so it can be asked
    TWICE — before the fetch and again with nothing between it and the merge.

    Returns an exit code when the branch must not move, and None when it may.
    Every refusal here is a refusal to act, never a step toward acting anyway.
    """
    branch = git.out("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        print(f"canonical-fast-forward: FAILED ({when}) — could not read the checked-out "
              f"branch, so this job cannot prove it is on {CANONICAL_BRANCH}", file=err)
        return FAILED

    state = _counts(git)
    print(f"canonical-fast-forward: {when}: branch={branch} "
          f"tracked_modified={_count_text(state['tracked'])} "
          f"untracked={_count_text(state['untracked'])}", file=out)
    for path in state["accepted"] or []:
        print(f"canonical-fast-forward: accepted by name: {path} (submodule content "
              f"dirty by design, recorded commit unchanged)", file=out)

    if branch != CANONICAL_BRANCH:
        print(f"canonical-fast-forward: REFUSED — canonical is on {branch!r}, not "
              f"{CANONICAL_BRANCH}.", file=err)
        print("canonical-fast-forward: AC-FRESH is about main. Fast-forwarding another "
              "branch onto main's tip would succeed, report OK, and leave main exactly as "
              "stale as it was.", file=err)
        return REFUSED_WRONG_BRANCH

    if state["tracked"] is None:
        print(f"canonical-fast-forward: FAILED ({when}) — could not read `git status`, so "
              f"this job cannot prove the tree holds nobody's edit", file=err)
        return FAILED

    if state["tracked"]:
        print(f"canonical-fast-forward: REFUSED — {len(state['tracked'])} tracked path(s) "
              f"modified in canonical ({when}).", file=err)
        print("canonical-fast-forward: those are somebody's edit. Land or discard them; "
              "this job will not.", file=err)
        for line in state["tracked"]:
            print(line, file=err)
        return REFUSED_TRACKED_DIRT

    return None


def fast_forward(repository: str | Path, *, dry_run: bool = False,
                 out: TextIO, err: TextIO) -> int:
    repository = Path(repository)
    if not _is_checkout(repository):
        print(f"canonical-fast-forward: {repository} is not a git checkout", file=err)
        return BAD_REPOSITORY
    git = _Git(repository)

    print(f"canonical-fast-forward: repository={repository}", file=out)

    # Branch and tracked dirt first, BEFORE the fetch: a job that refuses should
    # refuse cheaply, and a fetch on a dirty tree invites the temptation to
    # "just tidy".
    refusal = _preflight(git, when="before fetch", out=out, err=err)
    if refusal is not None:
        return refusal

    if git("fetch", "--quiet", "origin", "main").returncode != 0:
        print("canonical-fast-forward: FAILED — could not fetch origin/main", file=err)
        return FAILED

    behind = _commit_count(git.out("rev-list", "--count", "HEAD..origin/main"))
    ahead = _commit_count(git.out("rev-list", "--count", "origin/main..HEAD"))
    print(f"canonical-fast-forward: behind={behind if behind is not None else UNOBSERVED} "
          f"ahead={ahead if ahead is not None else UNOBSERVED}", file=out)
    if behind is None or ahead is None:
        print("canonical-fast-forward: FAILED — could not compare against origin/main", file=err)
        return FAILED

    if ahead != 0:
        print(f"canonical-fast-forward: REFUSED — canonical is {ahead} commit(s) AHEAD of "
              f"origin/main.", file=err)
        print("canonical-fast-forward: a fast-forward cannot represent that, and this job "
              "never rewrites.", file=err)
        return REFUSED_AHEAD

    if behind == 0:
        print("canonical-fast-forward: already current with origin/main", file=out)
        return OK

    if dry_run:
        print(f"canonical-fast-forward: --dry-run, would fast-forward {behind} commit(s)",
              file=out)
        return OK

    # The second sample, with nothing between it and the merge. The fetch above
    # took real time, and `merge --ff-only` will overwrite an unstaged edit to a
    # file it advances — so the tree that is merged has to be the tree that was
    # checked, not the tree that was checked a network round trip ago.
    refusal = _preflight(git, when="before merge", out=out, err=err)
    if refusal is not None:
        return refusal

    # An accepted dirty submodule is only safe to carry across the merge while
    # the merge leaves its recorded commit alone. If main moved it, the edit
    # under it belongs to a build that is now out of date: refuse and say so.
    for path in _counts(git)["accepted"] or []:
        here = git.out("rev-parse", f"HEAD:{path}")
        there = git.out("rev-parse", f"origin/main:{path}")
        if here is None or there is None or here != there:
            print(f"canonical-fast-forward: REFUSED — origin/main moves the recorded "
                  f"commit of {path}, which carries accepted local content; rebuild it "
                  f"from the new commit first.", file=err)
            return REFUSED_TRACKED_DIRT

    merged = git("merge", "--ff-only", "origin/main")
    if merged.returncode != 0:
        print("canonical-fast-forward: FAILED — ff-only merge refused", file=err)
        print(merged.stderr.strip(), file=err)
        return FAILED

    head = git.out("rev-parse", "--short", "HEAD")
    print(f"canonical-fast-forward: fast-forwarded {behind} commit(s) to "
          f"{head or UNOBSERVED}", file=out)
    return OK


# ─────────────────────────────────────────────────────────────────────────────
# The page. Its whole job is to be a COMPLETE registered call.
# ─────────────────────────────────────────────────────────────────────────────


def page_payload(facts: Mapping[str, Any], reasons: Sequence[str], *,
                 idempotency_key: str | None = None) -> dict[str, Any]:
    """The exact argument object `report-problem` accepts — no more, no less.

    The verb's validator takes an EXACT key set and refuses an unknown field as
    hard as a missing one, so this function is the single place that knows the
    shape. A fresh idempotency_key is minted PER RUN rather than derived from
    the facts: two runs that observe the same dirty tree are two observations,
    and collapsing them onto one key would silently drop the second.

    THE MEASUREMENTS COME BEFORE THE PROSE because `situation` is capped, and a
    page truncated to its adjectives is worth less than one truncated to its
    numbers. Whatever the cap eats, it eats the explanation, never the facts.
    """
    codes = ", ".join(sorted(facts["codes"])) or "unclassified"
    title = f"Canonical checkout freshness alarm: {codes}"[:TITLE_CAP]
    measurements = (f"branch={facts['branch']} "
                    f"tracked_modified={facts['tracked_modified']} "
                    f"untracked={facts['untracked']} behind={facts['behind']} "
                    f"ahead={facts['ahead']} head_age_hours={facts['head_age_hours']} "
                    f"bar={facts['max_age_hours']}h repository={facts['repository']}")
    situation = (f"WR-000040 AC-FRESH. Measured: {measurements}. {' '.join(reasons)}"
                 )[:SITUATION_CAP]
    desired = (
        "Canonical returns to a state the freshness job can keep: readable by git, checked "
        f"out on {CANONICAL_BRANCH}, no tracked modification in the shared checkout, and "
        "HEAD within the AC-FRESH bar of origin/main. Whoever owns the tracked edits lands "
        "or discards them — this job never will, because a fast-forward bought with "
        "somebody's edit is the failure it exists to report. Untracked paths are listed for "
        "context only and stay for Joe's AC-CLEAN ruling."
    )[:DESIRED_OUTCOME_CAP]
    criteria = [
        {"id": "CANONICAL-OBSERVABLE",
         "text": ("`git status`, `git rev-list` and `git log` all succeed in the canonical "
                  "checkout, so its state is measured rather than assumed.")[:CRITERION_TEXT_CAP]},
        {"id": "CANONICAL-ON-MAIN",
         "text": (f"`git rev-parse --abbrev-ref HEAD` in the canonical checkout prints "
                  f"exactly `{CANONICAL_BRANCH}`.")[:CRITERION_TEXT_CAP]},
        {"id": "CANONICAL-CLEAN",
         "text": ("`git status --porcelain --untracked-files=no` in the canonical checkout "
                  "prints nothing.")[:CRITERION_TEXT_CAP]},
        {"id": "CANONICAL-CURRENT",
         "text": (f"`git rev-list --count HEAD..origin/main` is 0, or HEAD is younger than "
                  f"{facts['max_age_hours']}h.")[:CRITERION_TEXT_CAP]},
        {"id": "CANONICAL-NOT-AHEAD",
         "text": ("`git rev-list --count origin/main..HEAD` is 0, so no work exists only in "
                  "the shared checkout.")[:CRITERION_TEXT_CAP]},
    ]
    return {
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "situation": situation,
        "title": title,
        "desired_outcome": desired,
        "acceptance_criteria": criteria,
    }


def run_sh_pager(repository: str | Path) -> Callable[[Mapping[str, Any]], tuple[bool, str]]:
    """The real alarm channel: the Bash door named in WR-000040's plan.

    Returns (landed, detail). A nonzero exit is a rejection, and so is a
    zero-exit body carrying an `error` key — the verb reports a refused call in
    its payload, so reading only the exit status would call a rejection a page.
    """
    repository = Path(repository)

    def page(payload: Mapping[str, Any]) -> tuple[bool, str]:
        done = subprocess.run([str(repository / "run.sh"), "call", "report-problem",
                               json.dumps(payload)],
                              cwd=str(repository), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        body = (done.stdout or "").strip()
        if done.returncode != 0:
            return False, f"exit={done.returncode} {(done.stderr or body).strip()[:400]}"
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return True, body[:400]
        if isinstance(parsed, dict) and parsed.get("error"):
            return False, f"verb refused: {parsed['error']}"
        return True, body[:400]

    return page


# ─────────────────────────────────────────────────────────────────────────────
# The watchdog.
# ─────────────────────────────────────────────────────────────────────────────


def watchdog(repository: str | Path, *, max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
             page: bool = True,
             pager: Callable[[Mapping[str, Any]], tuple[bool, str]] | None = None,
             now: Callable[[], float] = time.time,
             out: TextIO, err: TextIO) -> int:
    repository = Path(repository)
    if not _is_checkout(repository):
        print(f"canonical-dirty-watchdog: {repository} is not a git checkout", file=err)
        return BAD_REPOSITORY
    git = _Git(repository)

    fetch_ok = git("fetch", "--quiet", "origin", "main").returncode == 0
    branch = git.out("rev-parse", "--abbrev-ref", "HEAD")
    state = _counts(git)
    behind = _commit_count(git.out("rev-list", "--count", "HEAD..origin/main"))
    ahead = _commit_count(git.out("rev-list", "--count", "origin/main..HEAD"))

    # Age of the tip canonical is sitting on, which is what "within one day"
    # means: not when the job last ran, but how old the code in the tree is.
    head_stamp = git.out("log", "-1", "--format=%ct", "HEAD")
    head_epoch = int(head_stamp) if head_stamp is not None and head_stamp.isdigit() else None
    age_hours = int((now() - head_epoch) // 3600) if head_epoch is not None else None

    codes: list[str] = []
    facts: dict[str, Any] = {
        "repository": str(repository),
        "branch": branch if branch is not None else UNOBSERVED,
        "tracked_modified": _count_text(state["tracked"]),
        "untracked": _count_text(state["untracked"]),
        "behind": behind if behind is not None else UNOBSERVED,
        "ahead": ahead if ahead is not None else UNOBSERVED,
        "head_age_hours": age_hours if age_hours is not None else UNOBSERVED,
        "max_age_hours": max_age_hours,
        "codes": codes,
    }

    print(f"canonical-dirty-watchdog: repository={repository}", file=out)
    print(f"canonical-dirty-watchdog: branch={facts['branch']} "
          f"tracked_modified={facts['tracked_modified']} "
          f"untracked={facts['untracked']} behind={facts['behind']} ahead={facts['ahead']} "
          f"head_age_hours={facts['head_age_hours']}", file=out)
    if state["untracked"]:
        print("canonical-dirty-watchdog: untracked paths (reported, not alarmed — "
              "AC-CLEAN is Joe's ruling):", file=out)
        for line in state["untracked"]:
            print(f"  {line}", file=out)

    # WHAT COULD NOT BE LOOKED AT, named command by command. This is first
    # because every number below it is conditional on it: a watchdog that cannot
    # read the repository knows nothing about the repository, and "nothing" has
    # to page rather than pass.
    unobserved: list[str] = []
    if branch is None:
        unobserved.append("`git rev-parse --abbrev-ref HEAD`")
    if state["tracked"] is None or state["untracked"] is None:
        unobserved.append("`git status --porcelain`")
    if behind is None or ahead is None:
        unobserved.append("`git rev-list --count` against origin/main")
    if age_hours is None:
        unobserved.append("`git log -1 --format=%ct HEAD`")

    reasons: list[str] = []
    if unobserved:
        codes.append("unobserved")
        reasons.append(f"Could not observe {', '.join(unobserved)} in canonical, so its "
                       f"state is UNKNOWN — which is not the same as clean.")
    if state["tracked"]:
        codes.append("tracked-dirt")
        reasons.append(f"{len(state['tracked'])} tracked path(s) modified in a tree no "
                       f"session may edit.")
    if branch is not None and branch != CANONICAL_BRANCH:
        codes.append("wrong-branch")
        reasons.append(f"Canonical is on {branch!r}, not {CANONICAL_BRANCH}, so nothing here "
                       f"is a statement about main's freshness and the fast-forward cannot "
                       f"run.")
    if ahead is not None and ahead != 0:
        codes.append("ahead-of-origin")
        reasons.append(f"Canonical is {ahead} commit(s) ahead of origin/main.")
    if age_hours is not None and behind is not None and age_hours > max_age_hours and behind != 0:
        codes.append("stale")
        reasons.append(f"Canonical HEAD is {age_hours}h old and {behind} commit(s) behind "
                       f"origin/main, past the {max_age_hours}h bar.")
    if not fetch_ok:
        codes.append("fetch-failed")
        reasons.append("Could not fetch origin/main, so freshness is unverifiable.")

    if not reasons:
        print(f"canonical-dirty-watchdog: OK — on {CANONICAL_BRANCH}, no tracked dirt, "
              f"within {max_age_hours}h of origin/main", file=out)
        return OK

    print(f"canonical-dirty-watchdog: ALARM — {' '.join(reasons)}", file=err)
    for line in state["tracked"] or []:
        print(line, file=err)

    if page:
        payload = page_payload(facts, reasons)
        # The store is a best-effort SECOND channel. Its failure is printed and
        # then ignored: the exit code below is the channel that cannot be
        # silenced. This is also why the pager is injected — the paging path is
        # driven end to end in the tests against a stand-in run.sh.
        landed, detail = (pager or run_sh_pager(repository))(payload)
        if landed:
            print(f"canonical-dirty-watchdog: paged report-problem "
                  f"idempotency_key={payload['idempotency_key']}", file=out)
        else:
            print(f"canonical-dirty-watchdog: the record-layer page did not land ({detail}); "
                  f"the nonzero exit below still stands", file=err)

    return ALARM


def run(mode: str, *, repository: str | Path, max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
        page: bool = True, dry_run: bool = False, out: TextIO, err: TextIO) -> int:
    """The one door tools/repo-hygiene-janitor.py calls."""
    if mode == "fast-forward":
        return fast_forward(repository, dry_run=dry_run, out=out, err=err)
    if mode == "watchdog":
        return watchdog(repository, max_age_hours=max_age_hours, page=page, out=out, err=err)
    raise ValueError(f"unknown canonical freshness mode {mode!r}")
