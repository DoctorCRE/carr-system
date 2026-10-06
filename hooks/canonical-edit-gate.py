#!/usr/bin/env python3
# doctrine: repo-hygiene-program-build-plan
"""canonical-edit-gate.py — PreToolUse DENY on editing a TRACKED file in the
SHARED canonical checkout, before the dirty state ever lands.

R02 PROPOSED TEXT (Repo Hygiene Program, WR-000040, plan PLAN-22ff72ae82c5-v2).
NOT INSTALLED. This file is a proposal routed to the assurance-machinery owner
as a Model Room CONTRACT_CHANGE. Nothing in the Repo Hygiene Program installs
it; the assurance owner implements it or explicitly hands it off.

READ THE BASE NOTE BEFORE THE REST. This gate was RETIRED from the repo of
record on 2026-08-27 by commit 65014000 (PR #722, WR-000019 slice S3), together
with git-writer-gate.py and staging-attribution-gate.py. It does not exist on
origin/main. It IS still executing on Joe's Mac: ~/.claude/settings.json wires
an absolute path at /Users/booko/carr-system/hooks/canonical-edit-gate.py, and
the canonical checkout is ~133 commits behind origin/main, so the deleted file
is still on disk and still runs for every session on the machine. Adopting this
text is therefore a REINSTATEMENT of a retired blocking gate, not an amendment
of a live one, and it is subject to whatever admission control the assurance
owner applies to a new blocking gate. See
out/repo-hygiene-program/r02-contract-change/00-BLOCKER-base-discrepancy.md.

WHY THIS EXISTS. Worktree-per-session has been this repo's active taught rule
since git-writer-gate.py was built: a session takes an isolated worktree
BEFORE its first write to tracked files in ~/carr-system, and the canonical
checkout is the integration lane only, never a scratchpad. That rule is
enforced at THREE later moments — commit (ops/githooks/pre-commit refuses a
commit on `main`), push (the server ruleset is PR-only) and branch-switch
(hooks/git-writer-gate.py, itself retired in #722) — and at NONE of the moment
that actually creates the problem: nothing stops an Edit or Write from landing
on a tracked canonical file in the first place. By the time pre-commit fires,
the dirty state already exists, another session may already be reading a
half-written file, and "just use a worktree" is advice about the past instead
of a rail on the present.

Joe, 2026-08-14, naming exactly this gap: "a rule should be impossible to
overlook." This is the edit-time rail — the same rule the other gates already
enforce, moved to the moment it actually bites.

WHAT IT DENIES, IN PRACTICE: an Edit / Write / MultiEdit whose target is a file
GIT ALREADY TRACKS, inside THIS canonical checkout — never a registered
worktree, never a file outside the repo. `git ls-files` is asked directly
rather than inferred.

Those three tools are what the registered matcher actually delivers. The tool
tuple below also names NotebookEdit, so a NotebookEdit PAYLOAD would be handled
if one arrived — but none can, because no matcher names it. That is
payload-level behaviour, not coverage, and the COVERAGE section below is the
authority on the difference. Do not read this paragraph as a fourth covered
tool.

WHAT IT ALLOWS, and why each one is not the hazard this rule exists for:

  1. REGISTERED WORKTREES (R02 disposition 1: KEPT, exemption WIDENED).
     The whole point of the remedy this gate points people toward. Two tests,
     OR'd, and the widening is purely additive:

       (a) the original prefix test — anything under REPO/.claude/worktrees/.
           Kept verbatim so nothing that is exempt today stops being exempt,
           including a directory that has been created there but not yet
           registered with git.
       (b) NEW — any path inside a worktree GIT ITSELF HAS REGISTERED for this
           repo, read straight out of git's own registry at
           REPO/.git/worktrees/<id>/gitdir. That covers the four
           .codex-worktrees/ trees living under canonical today, which test (a)
           misses purely because of where they sit on disk.

     HONEST SCOPE OF THIS WIDENING, because the plan of record overstates it
     and the overstatement must not be re-derived from this file. The plan says
     "7 registered worktrees under canonical are wrongly denied today". Measured
     live on 2026-09-01 against the executing gate, the true number of
     registered worktrees this gate denies an edit in is ZERO:
       - 7 registered worktrees sit OUTSIDE the canonical repo. The
         outside-repo test already allows every one of them; they were never
         denied. That 7 is where the plan's number comes from.
       - 4 registered worktrees sit under canonical but outside
         .claude/worktrees/ (the .codex-worktrees/ trees). Test (a) does miss
         them — but a nested worktree's files are never entries in the OUTER
         repo's index, so `git ls-files` reports them untracked and allowance 2
         allows the edit anyway. All 4 probed ALLOW.
     Evidence: r02-contract-change/tests/results/worktree-live-probe.json
     (39 registered worktrees probed, 0 denials).
     So (b) is DEFENCE IN DEPTH and a correctness fix to the exemption's stated
     intent — it is NOT the repair of a live denial, and adopting it changes no
     session's experience today. It stops mattering the moment allowance 2 is
     ever narrowed, which is the only reason to take it now.

  2. A brand-new, UNTRACKED file anywhere in the canonical checkout (R02
     disposition 2: KEPT) — out/, .claude/ scratch, a log, receipts, or a file
     a session is about to `git add` for the first time. THIS IS A DELIBERATE
     CHOICE, not an oversight: the object this gate protects is EXISTING SHARED
     STATE — content another session could be mid-editing, or that a
     destructive git command could lose. A path nothing has ever occupied has
     neither property. Receipts and out/ are load-bearing and must stay
     writable, so this allowance is kept unchanged.
     WHAT THE ALLOWANCE COSTS, and the compensating control R02 adds: untracked
     dirt accumulates unseen (40 untracked-nonignored paths in canonical on
     2026-09-01, 30 of them one selftest run's temp roots). The answer is
     visibility, not refusal — ops/untracked-anomaly-report.py, a NON-PAGING
     weekly-review artifact listing untracked paths outside the approved roots.
     It raises no alarm and blocks nothing. Spec:
     r02-contract-change/spec/untracked-anomaly-report.md.

  3. Anything outside this repo entirely.

WHAT IT NO LONGER ALLOWS (R02 disposition 3: REMOVED). The retired text carried
a single-use escape hatch, CARR_ALLOW_CANONICAL_EDIT=1, which allowed a scoped
edit to a tracked canonical file from a Claude session. It is GONE. A Claude
session now has exactly one route to a tracked canonical file, and the refusal
names it as a command that can be run as written:

    ./run.sh worktree <name> --from origin/main

STATE THIS PLAINLY RATHER THAN DISCOVER IT LATER: removing the hatch does not
make the tracked canonical tree unwritable. It makes it unwritable BY A CLAUDE
EDIT TOOL. The deliberate integration edit that the hatch existed for — the
"reconciling this very checkout" case pre-commit names for itself — now happens
through the shell, which this gate has never covered (see COVERAGE below). That
is a real, intended narrowing of THIS door, not a claim that the tree is
frozen, and it must not be described as one.

COVERAGE, stated so nobody has to find the edges by hitting them.

  EDIT TOOLS ONLY, AND NOT ALL OF THEM. This gate is registered on the existing
  "Write|Edit|MultiEdit" PreToolUse group in ops/config/hooks.json. Those three
  tools are what it actually sees.

  NOTEBOOKEDIT IS NOT COVERED, and the earlier draft of this package claimed it
  was. The tool tuple below names NotebookEdit, but NO MATCHER ANYWHERE in
  ops/config/hooks.json names NotebookEdit — not this group, not any other, and
  not in any of the three mirrored settings files. So the harness never
  dispatches a NotebookEdit call here and the tuple entry is unreachable
  defensive code, not coverage. A fixture proves the tuple would handle such a
  payload; that is a statement about the tuple, not about the world, and the
  matrix labels it that way.

  This is not a new mistake in this file — it is a pattern already present
  twice: hooks/gate-edit-gate.py and hooks/lint-gate.py both carry NotebookEdit
  in their own tool lists under the same Write|Edit|MultiEdit matcher, and are
  equally unreachable for it. That is worth someone's attention separately; it
  is not R02's to fix.

  WHY THE MATCHER IS NOT WIDENED HERE. It would be one word. It is not proposed
  because nothing in this repo establishes that the harness accepts NotebookEdit
  in a matcher: there is no such matcher anywhere to copy, no documentation of
  the legal matcher vocabulary, and no validator — hooks/gate-integrity.py's
  validate_expected_wiring() compares the rendered settings against this config
  byte-for-byte, so a matcher the harness silently drops or normalises would
  show up as chronic wiring drift rather than as a clear error. Proposing a
  widening this package cannot test would be trading a documented gap for an
  undocumented one. The gap is named instead, and
  ops/canonical-edit-gate-selftest.py carries a tripwire row that fails the
  moment the matcher IS widened, so this paragraph cannot quietly go stale.

  THE CODEX / SHELL GAP IS REAL AND IS NOT CLOSED HERE. A Bash tool call —
  `sed -i`, `> file`, `tee`, `python3 -c`, `git checkout` — writes the same
  tracked canonical file and this gate never sees it. Neither does a Codex
  session, which does not run Claude Code's PreToolUse hooks at all. Building a
  general shell-write detector is a materially larger gate than this one and is
  not in R02's scope. THE COMPENSATING CONTROL IS DETECTION, NOT PREVENTION —
  AND IT IS NOT BUILT YET. The R04 canonical-dirt alarm is PLANNED to page on
  tracked dirt in the canonical checkout whatever wrote it, which would catch
  the shell and Codex paths after the fact. R04 is a LATER SLICE and is not
  live: the accepted dependency graph puts it after R08's worktree-versus-clone
  ruling, which itself blocks R04's packet compilation. So today the shell and
  Codex paths are UNCOMPENSATED, not merely uncovered here, and that is the
  honest statement until R04 is enabled. This gate is one honestly-scoped door,
  not a perimeter, and must never be described or counted as complete coverage
  of AC-NOEDIT.

FAIL-OPEN, ON EVERY ERROR AND EVERY TIMEOUT. Deny is exit 2 and nothing else
is; a hook that errors, is cancelled, or is killed exits some other way and the
runner reads that as ALLOW. Three places this matters, all deliberate:
  - any internal exception -> exit 0.
  - the `git ls-files` subprocess carries its own timeout and returns "not
    tracked" (i.e. ALLOW) if it expires, so a wedged git never wedges a session.
  - THE BINDING BUDGET IS ops/config/hooks.json's `timeout` FOR THIS ENTRY, not
    anything this file sets. When the runner's ceiling expires the hook process
    is killed outright, whatever it was about to print is discarded, and no
    exit 2 is ever emitted — the call is allowed. Raising an in-hook deadline
    above the hooks.json ceiling is dead code. Proven, both directions, in
    r02-contract-change/tests/results/matrix.md rows T5a and T5b.
A wedged session is worse than one more dirty file in a tree that already has a
human to fix it.

THIS IS AN ACCIDENT-STOPPER, NOT A SECURITY CONTROL, stated as plainly as
pre-commit says it about itself. It exists to catch the edit nobody meant to
make in the shared tree, which is the only kind that has actually happened.

Fixtures: ops/canonical-edit-gate-selftest.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# Script-relative, matching every gate in this family — never
# expanduser("~/carr-system"), which points at a directory that does not exist
# on a CI runner or a second machine.
#
# REALPATH, not abspath — a defect fix, not a disposition (see the manifest's
# "changes beyond the three dispositions"). The target below is realpath'd
# before it is compared against REPO. The retired text left REPO as abspath, so
# the two were not commensurable: with ANY symlink anywhere in the checkout's
# own path, every target resolved to a realpath that does not start with the
# abspath REPO, the outside-repo test allowed it, and the gate silently never
# fired at all. /Users/booko/carr-system has no symlink in it today, so this
# changes no verdict on that machine; it fails loudly in a fixture and would
# fail silently on a machine laid out differently, which is the worse of the
# two. Caught by ops/canonical-edit-gate-selftest.py's fixture repo.
REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WT_ROOT = os.path.realpath(os.path.join(REPO, ".claude", "worktrees"))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")
GATE_LIFECYCLE_PATH = os.path.join(REPO, "ops", "config", "gate-lifecycle.json")
LIFECYCLE_KEY = "canonical-edit-gate.py"

# The tool names this gate ACTS on if a payload arrives. NotebookEdit is in the
# tuple and is NOT delivered by the registered matcher — see the COVERAGE
# section above. Keeping it here costs nothing and makes the widening a
# one-line matcher change; claiming it as coverage would be a lie, so the
# matrix does not, and the selftest has a tripwire row that fails if the
# matcher is ever widened without the coverage text being updated.
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

WORKTREE_COMMAND = "./run.sh worktree <name> --from origin/main"


def mode():
    """enforcing | announce | shadow, from ops/config/gate-lifecycle.json.

    WHY THIS EXISTS AT ALL. The re-bless plan offers a staged rollout — this
    gate has been off the repo for a week and binds on every edit-tool call, so
    landing it straight into enforcement is the shape that gets a gate switched
    off in its first day. An offer of "shadow first" that the code cannot honour
    is worse than not offering it, so it is implemented rather than promised.

    THE MECHANISM IS COPIED, NOT INVENTED. gate-lifecycle.json's `mode` is
    documentary for every gate but one: hooks/conduct-stop-gate.py reads its own
    key's mode directly in _shadow_mode_enabled(), and that file's `_doc` records
    the exception ("Its mode flag is read directly by that new code path"). This
    is the same pattern, same file, same shape, keyed on this gate's own entry.
    The repo's OTHER shadow mechanism — rule-pack-drift-gate.py reading
    ops.rule_delivery_policy out of a standing-context result — is deliberately
    NOT copied: there is no standing-context result in a PreToolUse edit payload,
    and a database read on the edit path is a cost this gate has no business
    paying.

    DEFAULT IS `enforcing`, INCLUDING ON EVERY READ FAILURE, and that direction
    is chosen rather than inherited. Defaulting to shadow would mean an
    unreadable or renamed config silently disables enforcement — which is
    exactly the 2026-08-08 failure where a plugin install wiped a hooks block
    and left five gates off for a day with nothing to find it by. A missing
    entry means "nobody staged this gate", and a gate nobody staged enforces.
    This does not weaken fail-open: a genuine runtime error still leaves main()
    through its outer handler and still exits 0.
    """
    try:
        with open(GATE_LIFECYCLE_PATH) as fh:
            data = json.load(fh)
        value = ((data.get("gates") or {}).get(LIFECYCLE_KEY) or {}).get("mode")
        return value if value in ("enforcing", "announce", "shadow") else "enforcing"
    except Exception:
        return "enforcing"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  canonical-edit-gate  {msg}\n")
    except Exception:
        pass


def audit(rec):
    if rec.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def git_common_dir():
    """REPO's shared .git directory, whether REPO is the canonical checkout
    (.git is a directory) or itself a worktree (.git is a file pointing into
    the canonical repo's .git/worktrees/<id>).

    Resolved by reading the filesystem rather than shelling out to git: this
    runs on the PreToolUse path for every edit, and an inherited GIT_DIR — set
    in every git hook's environment — would otherwise make the answer depend on
    who launched the session instead of on where the repo is.
    """
    dot = os.path.join(REPO, ".git")
    if os.path.isdir(dot):
        return os.path.realpath(dot)
    if os.path.isfile(dot):
        with open(dot) as fh:
            text = fh.read().strip()
        if text.startswith("gitdir:"):
            gitdir = text.split(":", 1)[1].strip()
            if not os.path.isabs(gitdir):
                gitdir = os.path.join(REPO, gitdir)
            marker = os.sep + "worktrees" + os.sep
            if marker in gitdir:
                gitdir = gitdir.split(marker)[0]
            return os.path.realpath(gitdir)
    return None


def registered_worktrees():
    """Every worktree GIT ITSELF has registered for this repo, as realpaths.

    Git's registry is one small file per worktree —
    <common>/worktrees/<id>/gitdir — holding the absolute path of that
    worktree's own .git file. Reading it directly is the registration, with no
    subprocess, no timeout to blow, and no way for a caller to nominate a path
    git does not already know about: exactly the bounding the original prefix
    test was reaching for.

    Returns () on any error. That is the fail-open direction here — losing this
    set can only fall through to the tracked/untracked test below, never
    manufacture a denial.
    """
    try:
        common = git_common_dir()
        if not common:
            return ()
        root = os.path.join(common, "worktrees")
        if not os.path.isdir(root):
            return ()
        found = []
        for entry in os.listdir(root):
            gitdir_file = os.path.join(root, entry, "gitdir")
            try:
                with open(gitdir_file) as fh:
                    pointer = fh.read().strip()
            except OSError:
                continue
            if not pointer:
                continue
            found.append(os.path.realpath(os.path.dirname(pointer)))
        return tuple(found)
    except Exception:
        return ()


def in_worktree(path):
    """True when `path` sits inside a worktree of THIS repo.

    Two tests, OR'd, and the second is purely additive to the first:
      (a) the original prefix test against REPO/.claude/worktrees — kept
          verbatim, so nothing exempt today loses its exemption, including a
          directory created there but not yet registered.
      (b) git's own worktree registry, which reaches the trees that live
          elsewhere under canonical (.codex-worktrees/ today).
    Neither requires the path to exist: a Write creating a brand-new file in a
    worktree is still, obviously, a worktree edit.
    """
    if path == WT_ROOT or path.startswith(WT_ROOT + os.sep):
        return True
    for wt in registered_worktrees():
        if path == wt or path.startswith(wt + os.sep):
            return True
    return False


def is_tracked(rel_path):
    """Does git already track this path in the canonical checkout?

    Asked directly rather than inferred. `git ls-files` prints the path back
    when it is tracked and nothing when it is not, so this needs no
    --error-unmatch exit-code handling on either answer. On timeout or any
    other failure it answers "not tracked", which ALLOWS: fail open.

    THE 5s BUDGET IS CHOSEN AGAINST THE hooks.json CEILING, not guessed. The
    retired text waited 15s while its hooks.json entry also allowed 15s, so the
    runner killed the process at the same moment this timeout was due to fire:
    the in-hook fail-open path was unreachable in practice and the only thing
    that ever actually happened was the harness kill. An in-hook deadline at or
    above the registered ceiling is dead code. 5s sits well inside the 15s
    entry, so a wedged git is handled HERE — quietly, with a logged reason —
    instead of costing the session the full ceiling. Both paths are still
    proven, separately, by the selftest's two failopen rows; if the hooks.json
    timeout is ever lowered, lower this first.
    """
    try:
        out = subprocess.run(
            ["git", "-C", REPO, "ls-files", "--", rel_path],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return False                                   # fail OPEN, like the rest
    return bool(out.strip())


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in EDIT_TOOLS:
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        path = (ti.get("file_path") or ti.get("filePath") or "") if isinstance(ti, dict) else ""
        if not path:
            sys.exit(0)

        ap = os.path.realpath(os.path.expanduser(path))

        if not (ap == REPO or ap.startswith(REPO + os.sep)):
            dlog(f"ALLOW(outside-repo) {tool} {path}")
            sys.exit(0)                                # not our tree at all

        if in_worktree(ap):
            dlog(f"ALLOW(worktree) {tool} {path}")
            sys.exit(0)                                # the remedy itself

        rel = os.path.relpath(ap, REPO)

        if not is_tracked(rel):
            dlog(f"ALLOW(untracked) {tool} {rel}")
            sys.exit(0)                                # nothing shared to clobber yet

        # From here the write is genuinely aimed at a tracked file in the
        # shared integration tree. There is no escape hatch on this path any
        # more (R02 disposition 3).
        reason = (
            "CANONICAL EDIT GATE — refused.\n\n"
            f"'{rel}' is tracked by git and sits in ~/carr-system itself — the "
            "SHARED integration tree, not a session's own copy. Worktree-per-"
            "session is this repo's active rule: a session takes its own "
            "worktree BEFORE its first write to a tracked canonical file, and "
            "this checkout is for merged, reviewed integration only.\n\n"
            "DO THIS INSTEAD — run this command as written, then work in the "
            "tree it prints:\n"
            f"    {WORKTREE_COMMAND}\n"
            "    cd .claude/worktrees/<name>\n"
            "Then edit, commit, push and open a PR from there. `--from "
            "origin/main` matters: the canonical checkout runs chronically "
            "behind origin, so branching from its HEAD starts you on stale "
            "code.\n\n"
            "This rule already binds at commit (ops/githooks/pre-commit refuses "
            "`main`) and at push (the server ruleset is PR-only) — this is the "
            "same rule, caught at the moment it actually starts: the first "
            "edit, before any of that dirty state exists.\n\n"
            "STILL ALLOWED, and unaffected by this refusal: new untracked "
            "files anywhere in this checkout (out/, receipts, scratch), and "
            "anything inside any registered worktree of this repo.\n\n"
            "THERE IS NO ENVIRONMENT-VARIABLE BYPASS FOR THIS GATE. The former "
            "CARR_ALLOW_CANONICAL_EDIT hatch was removed deliberately (Repo "
            "Hygiene Program R02): a scoped tracked-file edit from a session is "
            "the hole that let dirt accumulate in the shared tree. If you "
            "believe this specific edit must happen in the canonical checkout "
            "and not in a worktree, say so and let a human decide — do not "
            "route around this gate.\n"
        )

        current = mode()
        audit({"ts": now(), "hook": "canonical-edit-gate",
               "classes": ["canonical_tree_edit"],
               "patterns": ["canonical-edit:tracked-file"],
               "session": payload.get("session_id"), "path": rel,
               "mode": current,
               "decision": {"enforcing": "deny", "announce": "allow-announced",
                            "shadow": "allow-observed"}[current]})

        if current == "shadow":
            # Recorded, invisible to the session. The audit row above is the
            # entire output: this is what a week of "how often would it have
            # fired, and on what?" looks like before anything is refused.
            dlog(f"SHADOW(would-deny) {tool} {rel}")
            sys.exit(0)

        if current == "announce":
            note = (
                "CANONICAL EDIT GATE — announced, not refused.\n\n"
                f"This edit to the tracked canonical file '{rel}' was ALLOWED "
                "because the gate is in `announce` mode. Under `enforcing` it "
                "would have been refused. The remedy is the same either way:\n"
                f"    {WORKTREE_COMMAND}\n"
                "    cd .claude/worktrees/<name>\n"
                "Recorded to out/conduct-gate.jsonl."
            )
            dlog(f"ANNOUNCE(would-deny) {tool} {rel}")
            # Structured allow on STDOUT with exit 0 — the same announce shape
            # gate-edit-gate.py uses, so the two doors cannot drift.
            print(json.dumps({
                "systemMessage": note,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": note,
                },
            }))
            sys.exit(0)

        dlog(f"DENY {tool} {rel}")
        # Exit 2, not JSON: on a build that does not parse the structured
        # contract, exit 0 reads as ALLOW and the gate fails open silently —
        # same convention as every deny path in this family.
        print(reason, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
