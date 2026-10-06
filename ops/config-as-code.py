#!/usr/bin/env python3
"""config-as-code.py — the machine config belongs in the repo, same as the code.

WHY (Joe, 2026-08-03: "shouldnt all code be in the repo? .json is code").

He is right, and the exposure was worse than the one file he was pointing at.
Measured that night:

    ~/.claude/settings.json      1 on disk,  0 in the repo   <- fires all 5 hooks
    launchd plists               2 on disk,  0 in the repo   <- incl. the hourly
                                                                rule refresh
    scheduled tasks             15 on disk,  4 in the repo

So the five hook SCRIPTS were version-controlled and the thing that makes them
run was not. That is the two-homes disease the whole system is built to avoid:
the same night, hooks/SETTINGS-BLOCK.md was found to have silently drifted — it
documented two hooks and the live file had four. A document DESCRIBING config
drifts. Config in the repo does not.

WHAT THIS DOES NOT DO, and the reason is not squeamishness. It does not put
`~/.claude/settings.json` in the repo wholesale. That file also carries 77
permission entries and notification prefs that are Joe's, machine-shaped, and
churn weekly — committing them would create a second home for something Claude
Code only ever reads from ~/.claude/, and a baseline would go stale exactly the
way SETTINGS-BLOCK.md did. Only the CARR-OWNED `hooks` block is tracked, and
`install` merges it back leaving every other key untouched.

THE PATTERN IS THE ONE THE SYSTEM ALREADY USES: source in the repo, render on
the machine. settings.json becomes a render, the same way clients-active.md is.

PORTABILITY IS THE POINT, NOT A BONUS. Repo copies store {{HOME}}, {{REPO}} and
{{VAULT}} instead of /Users/booko. That is what lets the same source install on
Dell's machine — and 54 of the 70 active rules are SHARED scope, binding him
exactly as they bind Joe, with zero mechanical enforcement on his side today.

    ops/config-as-code.py check      # drift report; exit 1 if any. THE DEFAULT.
    ops/config-as-code.py pull       # machine -> repo (capture what is live)
    ops/config-as-code.py install    # repo -> machine (deploy; needs --apply)
    ops/config-as-code.py reinstall-launchd-calendar [--apply] [--kickstart]
    ops/config-as-code.py install-codex-continuity --apply
    ops/config-as-code.py verify-codex-continuity
    ops/config-as-code.py install-codex-continuity-mcp --apply
    ops/config-as-code.py verify-codex-continuity-mcp
    ops/config-as-code.py install-progress-board [--repo CANONICAL] --apply
    ops/config-as-code.py install-flash-on-demand [--apply]
    ops/config-as-code.py verify-progress-board [--repo CANONICAL]
    ops/config-as-code.py check-launchd-main-paths
    ops/config-as-code.py remove-codex-continuity --apply

`check` is what belongs in run.sh health: it answers "is the live config still
the config we think we have", which is the question nobody could answer tonight.
"""

import copy
import json
import os
import plistlib
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
import secrets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.machine_prerequisites import machine_prerequisites, prerequisite_failure_report
from lib import claude_continuity_config as continuity_config
from lib import machine_role
from lib import launchd_calendar

HOME = os.path.expanduser("~")
# THE CHECKOUT THIS FILE SITS IN — the source of the tracked copies to compare.
REPO_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHD_DEPENDENCY_CHECKOUTS = (
    "/opt/homebrew", "/usr/local/Homebrew", "/home/linuxbrew/.linuxbrew/Homebrew",
)


def _canonical_repo(here):
    """The MAIN checkout, even when this runs from a linked worktree.

    WHY THIS IS NOT just `here` (fixed 2026-08-13). {{REPO}} tokenizes paths
    inside the MACHINE's installed config — launchd plists, settings.json — and
    the machine has exactly ONE installation, which always points at the main
    checkout. Deriving the token from this file's own location meant that running
    from a linked worktree tokenized to the WORKTREE path, so every live item
    referencing the real checkout compared unequal. Measured the same day: the
    identical command reported `OK — 36 items, repo matches machine` in the main
    checkout and `DRIFT — 18 of 36 items` from a worktree, on an unchanged
    machine. Nothing had drifted.

    That false positive was not cosmetic. It failed the config-as-code class in
    pre-push CI, which blocked pushing from a worktree — and working from a
    worktree is the standing remedy when the shared tree is busy, so the check
    broke the escape hatch. Worse, its stated remedy is `config-as-code.py pull`,
    which would have captured the machine's absolute paths INTO the repo,
    destroying the {{REPO}} templating that exists so Dell's clone works at a
    different path. Following the advice would have made the repo unportable.

    git's own answer is authoritative and cheap: --git-common-dir resolves to the
    MAIN repository's .git from inside any linked worktree (a worktree's own
    --git-dir points into .git/worktrees/<name>). Falls back to `here` when git
    is unavailable or this is not a checkout at all, which is the pre-existing
    behaviour and correct for the non-worktree case."""
    try:
        out = subprocess.run(
            ["git", "-C", here, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            common = out.stdout.strip()          # .../carr-system/.git
            root = os.path.dirname(common)
            if root and os.path.isdir(root):
                return os.path.abspath(root)
    except (OSError, subprocess.SubprocessError):
        pass
    return here


REPO = _canonical_repo(REPO_HERE)
PREREQUISITE_CHECK = machine_prerequisites

# `git -C <path>` IS NOT A GUARANTEE OF WHICH REPOSITORY YOU HIT. Every variable
# below outranks both -C and the working directory, and git exports several of
# them to every hook it runs — so anything reached from a hook inherits them.
# That is the 2026-08-14 incident: a selftest whose fixture used cwd=mkdtemp()
# ran its git commands against the live checkout instead, rewrote local main
# onto its own seed commits, and marked the repository core.bare true.
#
# THIS FILE WAS ASSESSED AS READ-ONLY AND THAT WAS WRONG. It used to run
# `git -C REPO config core.hooksPath ops/githooks`, which is a WRITE: under an
# inherited GIT_DIR it lands in whatever repository the variable names, not in
# REPO, so `config-as-code.py install` invoked from a hook could point a
# different repository's hooksPath at this repo's ops/githooks. That write is
# GONE — core.hooksPath is no-touch now — which removes the worst case but not
# the reason for this scrubber: the remaining git calls are reads, and
# misdirected they yield a wrong drift verdict, which is a lie in a checker
# whose entire job is detecting drift.
#
# GIT_CONFIG_COUNT is the subtle one and is why this list is not just GIT_DIR:
# its KEY_<n>/VALUE_<n> pairs can set core.worktree and relocate a call with
# GIT_DIR never appearing anywhere.
#
# A shared ops/git_env.py with the same job is in flight in another session's
# PR #19. It is not merged and not on origin/main, so this stands alone rather
# than importing something that does not exist yet; consolidate onto that helper
# when it lands, and delete this.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import scrubbed_env as _git_env  # noqa: E402

# CONSOLIDATED into ops/git_env.py (loop #371). This file carried its own
# _GIT_LOCATION_VARS tuple and _git_env() because git_env.py was not yet on main
# when the scrub landed here in PR #20 — importing something unmerged would have
# traded a real hole for an ImportError, so the local copy was correct at the
# time and is redundant now. The bounded GIT_CONFIG_COUNT loop that lived here
# was the better implementation and went the other way: git_env.scrubbed_env()
# now uses it, because the original spun on a hostile count.
#
# Aliased to _git_env so the five call sites below are untouched by this change.


def _find_vault():
    """The vault sits under a Drive mount named for the ACCOUNT, so a hardcoded
    default is Joe-only by construction — the exact defect this file exists to
    fix, one level down. Glob for it instead, so Dell's machine resolves his own
    mount. CARR_VAULT overrides."""
    env = os.environ.get("CARR_VAULT")
    if env:
        return env
    import glob
    hits = sorted(glob.glob(os.path.join(
        HOME, "Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI")))
    return hits[0] if hits else ""


VAULT = _find_vault()

SETTINGS = os.path.join(HOME, ".claude", "settings.json")
TASKS_SRC = os.path.join(HOME, ".claude", "scheduled-tasks")
TASKS_REPO = os.path.join(REPO, "ops", "scheduled-tasks")
# A quarantined definition is deliberately outside Claude's active discovery
# directory.  It is recoverable evidence of what this reconciler removed, not
# a second scheduler source of truth.
TASKS_QUARANTINE = os.path.join(
    HOME, ".claude", "scheduled-tasks-quarantine", "carr-primary-only"
)
# Same idea for launch agents: a primary-only plist found on a secondary is
# unloaded and moved here, never deleted, so demoting a Mac is reversible.
LAUNCHD_QUARANTINE = os.path.join(
    HOME, "Library", "LaunchAgents-quarantine", "carr-primary-only"
)
LAUNCHD_SRC = os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHD_REPO = os.path.join(REPO, "ops", "launchd")
LAUNCHD_ALT_REPO = {
    "com.carr.call-mode.plist": os.path.join(
        REPO, "tools", "dictation-rig", "launchd", "com.carr.call-mode.plist"
    ),
}
HOOKS_REPO = os.path.join(REPO, "ops", "config", "hooks.json")
CODEX_HOOKS_SRC = os.path.join(HOME, ".codex", "hooks.json")
CODEX_HOOKS_REPO = os.path.join(REPO, "ops", "config", "codex-hooks.json")
CODEX_CONFIG = os.path.join(HOME, ".codex", "config.toml")
CLAUDE_CONTINUITY_MODE_FILE = os.path.join(
    HOME, ".config", "carr", "claude-continuity-mode.json"
)
CLAUDE_MCP_CONFIG = os.path.join(HOME, ".claude.json")
CODEX_CONTINUITY_EVENTS = ("PreCompact", "PostCompact", "SessionStart", "UserPromptSubmit")
CODEX_CONTINUITY_APP_EVENTS = {
    "PreCompact": "preCompact",
    "PostCompact": "postCompact",
    "SessionStart": "sessionStart",
    "UserPromptSubmit": "userPromptSubmit",
}
CODEX_APP_SERVER_TIMEOUT_SECONDS = 15
CODEX_APP_SERVER_OUTPUT_LIMIT = 4 * 1024 * 1024
CODEX_PERMISSIONS_REPO = os.path.join(REPO, "ops", "config", "codex-permissions.toml")
CODEX_PERMISSIONS_BEGIN = "# >>> CARR managed permissions >>>"
CODEX_PERMISSIONS_END = "# <<< CARR managed permissions <<<"

# Longest first: REPO and VAULT both sit under HOME, so substituting HOME first
# would leave "{{HOME}}/carr-system" and the REPO token would never match.
# EMPTY VALUES ARE FILTERED OUT, and that is not defensive padding: str.replace
# with an empty needle inserts the token between every character of the file.
# _find_vault() returns "" when no Drive mount matches, so one unmounted Drive
# would otherwise shred every tracked config on the next pull.
TOKENS = [(tok, real) for tok, real in
          (("{{VAULT}}", VAULT), ("{{REPO}}", REPO), ("{{HOME}}", HOME)) if real]

from lib.launchd_scope import PRIMARY_ONLY, SECONDARY_ONLY




# Versioned definitions that deliberately must not become live merely because
# config-as-code reconciles the rest of the machine.  These adapters have their
# own evidence/approval cutover gates; installing one early would turn a source
# artifact into an active schedule before those gates pass.
DEFINITION_ONLY: dict[str, str] = {
    # com.carr.control-plane-tick.plist held here until 2026-08-26: its gate was
    # "accepted shadow/canary evidence and cutover approval". Joe approved the
    # cutover that evening (decision f4af0c87, "Yes I approve cutover") with the
    # first accepted shadow receipt on record; the wrapper pins --mode shadow,
    # so installing activates evidence production only — legacy schedules keep
    # running until each workflow's replacement is accepted at its own tier.
    "com.carr.repo-hygiene-janitor.plist":
        "the repo-hygiene janitor plans branch, worktree and cache cleanup; its "
        "gate is a separately reviewed live-effect packet, so the definition is "
        "written down and left uninstalled until that packet is approved",
    # com.carr.gate-zero-canary.plist was held here from 2026-09-11 to
    # 2026-09-12 with the reason "starting a schedule is Joe's act, so the
    # definition is written down and left uninstalled until he takes it off this
    # list deliberately". THAT ACT IS TAKEN. Joe's blanket approval (decision
    # idempotency 5e2b8c1a-9f47-4d63-b0e5-7a3d1c9f2e84, "I approve everything")
    # together with his 2026-09-13 ruling that the orchestrator runs release and
    # activation commands itself is the deliberate removal the reason asked for,
    # so the canary reconciles like any other agent and the next `install
    # --apply` loads it. What this buys is the only question Gate Zero's fourth
    # predecessor actually asks: a hand dispatch through bin/run-scheduled.sh
    # proves the WRAPPER mints a receipt, and only launchd firing on its own
    # proves the SCHEDULER does -- which is what `step:scheduler-active-receipt`
    # reads. ops/config-as-code-selftest.py now asserts this release, the way it
    # already asserts the 2026-08-26 control-plane tick cutover, so putting the
    # canary back on the list is a change a test refuses rather than a silent
    # revert.
}

# A LaunchAgent that invokes this installer cannot unload its own label and
# still return to bin/run-scheduled.sh: launchd terminates the wrapper process
# tree, so the run never reaches its durable receipt.  The caller may identify
# exactly that one active label.  Every other plist retains the ordinary
# unload/load convergence below.  If the active plist changed, install fails
# closed without writing a misleading new body over the still-old loaded job;
# an external installer is the named remedy.  If unchanged, it remains loaded.
ACTIVE_LAUNCHD_LABEL_ENV = "CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL"


# Claude scheduled-task definitions are not merely configuration files: their
# presence asks a local AI client to perform work later.  Every tracked task is
# therefore primary-only until its *own* machine scope has been reviewed and
# deliberately listed here.  The empty allow-list is intentional.  It keeps a
# Dell migration from turning Joe's existing task catalogue into Dell's queue,
# while still leaving a narrow, auditable path for a future Dell-specific task.
#
# This policy is fail-closed in both directions:
#   * a tracked primary task missing from a secondary machine is not drift;
#   * a CARR-managed task found on a secondary machine is visible drift and is
#     never pulled into the shared baseline; unrelated personal tasks are not
#     CARR configuration and this tool never claims, moves, or counts them; and
#   * secondary install never creates ~/.claude/scheduled-tasks; primary install
#     renders the tracked definitions it owns.
# The actual scheduler registration is outside this config reconciler, so
# copying a SKILL.md would be both insufficient and unsafe.
#
# STILL EMPTY, deliberately, and here is the near-miss that tried to change it.
# On 2026-08-18 source-of-truth copies of Dell's three personal tasks were added
# to ops/scheduled-tasks/, which made them CARR-owned by this file's own rule —
# "the repository is the ownership registry" — and dell-social-batch-weekly
# immediately reported as a secondary scope violation. The first fix was to list
# the three here. That was the wrong lever: it widens the machine-scope policy to
# paper over a filing mistake, and it also left ops/control-plane-selftest.py
# failing, because that selftest requires every *.SKILL.md directly under
# ops/scheduled-tasks/ to be a registered control-plane workflow, which personal
# tasks are not and should not be.
#
# The copies now live in ops/scheduled-tasks/dell/. Both this reconciler and the
# control-plane selftest read only the top level, so the subdirectory keeps the
# repo copies Dell asked for while leaving his tasks classified as what they are:
# personal configuration this tool does not claim, move, or count. Anything added
# to the TOP level is a CARR-managed primary task and must be registered as a
# control-plane workflow.
SECONDARY_SCHEDULED_TASKS: set[str] = set()


# EPHEMERAL SCAFFOLDING IS NOT CONFIGURATION, and treating it as such made this
# check chronically red — the exact failure the comment in cmd_check() warns
# about, arriving from a direction nobody anticipated.
#
# Sessions legitimately create throwaway scheduled tasks: handoff continuations,
# one-time catch-up runs, drills. On 2026-08-21 two of them blocked unrelated
# pushes within one hour. The second was created BY a session whose own
# description read "clear the config drift blocking the push" — it was stuck
# behind this gate and its attempt to hand the problem on became the next
# instance of the problem. No session could resolve either one correctly:
# capturing a throwaway into the repository pollutes it permanently and then
# inverts into MISSING drift the moment the task is removed, while moving or
# deleting it takes another live session's work.
#
# tracked_scheduled_task_paths() already states the governing rule in its own
# docstring: a task in Claude's user-owned directory that CARR does not own is
# personal, and must not "make CARR health falsely red for it". This is that
# rule applied to the drift comparison, which had never honoured it.
#
# A MARKER, NOT A HEURISTIC. Guessing from the name would be silently wrong in
# both directions. The task itself declares what it is, so a genuine CARR task
# that someone forgot to commit still shows up as drift — which is the part of
# this check worth keeping.
EPHEMERAL_MARKER = "ephemeral:true"


def is_ephemeral_scheduled_task(text):
    """True when a task's own frontmatter declares it session scaffolding.

    Only the frontmatter block is read, so the phrase appearing in prose lower
    down the file cannot exempt a real task by accident.
    """
    if not text:
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        if line.strip().lower().replace(" ", "") == EPHEMERAL_MARKER:
            return True
    return False


def scheduled_task_allowed(name):
    """Whether this machine may host the named CARR scheduled task.

    Unknown task names deliberately resolve to false on a non-primary machine.
    A new secondary task must be added to the explicit allow-list with its
    safety case, rather than becoming installable because it happened to appear
    in the repository.
    """
    return IS_PRIMARY or name in SECONDARY_SCHEDULED_TASKS


def tracked_scheduled_task_paths():
    """Tracked CARR task definitions, keyed by their scheduler directory name.

    The repository is the ownership registry.  A random task in Claude's
    user-owned directory is personal configuration, not CARR state: do not
    pull it, delete it, quarantine it, or make CARR health falsely red for it.
    """
    if not os.path.isdir(TASKS_REPO):
        return {}
    return {
        filename[:-9]: os.path.join(TASKS_REPO, filename)
        for filename in os.listdir(TASKS_REPO)
        if filename.endswith(".SKILL.md")
        and os.path.isfile(os.path.join(TASKS_REPO, filename))
    }


def secondary_scheduled_task_state():
    """CARR-owned task definitions active on a secondary, by safe disposition.

    Exact tracked renders are safe to quarantine.  A tracked name whose body
    differs is intentionally a hard stop: it might be a local change and this
    reconciler must not silently discard it.  Names outside the tracked CARR
    registry are personal tasks and are intentionally absent from this result.
    """
    state = {"exact": [], "modified": []}
    if IS_PRIMARY or not os.path.isdir(TASKS_SRC):
        return state
    tracked = tracked_scheduled_task_paths()
    for name in sorted(os.listdir(TASKS_SRC)):
        local = os.path.join(TASKS_SRC, name, "SKILL.md")
        source = tracked.get(name)
        if not source or not os.path.isfile(local) or scheduled_task_allowed(name):
            continue
        if portable(read(local)) == read(source):
            state["exact"].append(name)
        else:
            state["modified"].append(name)
    return state


def secondary_scheduled_task_violations():
    """Installed, disallowed CARR tasks for the drift report only."""
    state = secondary_scheduled_task_state()
    return state["exact"] + state["modified"]


def scheduled_task_install_plan():
    """Return a fail-closed repo-to-machine scheduled-task reconciliation plan."""
    if IS_PRIMARY:
        return {"install": sorted(tracked_scheduled_task_paths()), "exact": [], "modified": []}
    return {"install": [], **secondary_scheduled_task_state()}


def _is_primary():
    """Primary is decided in ONE place, lib/machine_role.py: the per-machine
    marker ~/.config/carr/machine-role.json when present, else git user.email
    against OWNER_EMAIL in ops/githooks/pre-push (the determinant this file
    used alone until 2026-09-23). Anything unprovable returns False, and false
    is the safe direction: a machine that cannot prove it is primary installs
    only the per-machine jobs."""
    me = subprocess.run(["git", "-C", REPO, "config", "user.email"],
                        capture_output=True, text=True, env=_git_env()).stdout.strip()
    return machine_role.is_primary(REPO, git_email=me)


IS_PRIMARY = _is_primary()


def missing_targets(body):
    """Absolute paths a plist needs that do not exist on THIS machine.

    A launchd job whose program was never built loads fine and then fails on
    every fire, throttling and filling the log — the failure mode the dictation
    and doc-engine jobs would have hit on a fresh clone, where the Swift binary
    and the doc-convo tree are not built. Skipping is the safe direction: the
    job installs later, on the next run, once the thing it runs exists.
    """
    try:
        d = plistlib.loads(body.encode())
    except Exception:
        return []          # unparseable is the drift check's problem, not ours
    args = d.get("ProgramArguments") or ([d["Program"]] if d.get("Program") else [])
    return [a for a in args
            if isinstance(a, str) and a.startswith("/") and not os.path.exists(a)]


def portable(text):
    for tok, real in TOKENS:
        text = text.replace(real, tok)
    return text


def concrete(text):
    for tok, real in TOKENS:
        text = text.replace(tok, real)
    return text


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def launchd_texts_match(live_text, repo_text):
    """Compare launchd plist bodies with token portability normalized on both sides."""
    return portable(live_text or "") == portable(repo_text or "")


def tracked_text_match(label, live, repo_text):
    """Config equality that keeps launchd normalization symmetrical."""
    if label.startswith("launchd "):
        return launchd_texts_match(live, repo_text)
    return live == repo_text


def hook_block_script_paths(block):
    """Every absolute .py path a hooks block's commands invoke, real and sorted.

    The extraction half shared by hook_scripts_untracked (which asks git about
    the LIVE block) and cmd_install's missing-script refusal (which asks the
    filesystem about the PLANNED block before writing it)."""
    paths = set()
    for groups in (block or {}).values():
        if not isinstance(groups, list):
            continue
        for grp in groups:
            for hook in (grp or {}).get("hooks", []) or []:
                for tok in re.findall(r"(/[^\s'\"]+\.py)", hook.get("command", "") or ""):
                    paths.add(os.path.realpath(concrete(tok)))
    return sorted(paths)


def hook_scripts_untracked():
    """Hook scripts that settings.json points at but git does not track.

    THE GAP THIS CLOSES, found 2026-08-09. This tool verifies that the hooks
    BLOCK inside settings.json matches the repo. It never checked whether the
    SCRIPTS that block points at are committed. Those are different failures and
    only one of them was covered: on this machine the live settings referenced
    hooks/conduct-stop-gate.py, hooks/conduct_patterns.py and
    hooks/escalation-gate.py, all created the same afternoon and none of them in
    git. The check reported "OK — repo matches machine" the whole time, and it
    was telling the truth about the only thing it was looking at.

    WHY IT MATTERS RATHER THAN BEING TIDINESS. A hook that exists only in one
    working tree is one `git clean`, one disk failure or one fresh clone from
    being gone, and the settings block that survives will then point at files
    that are not there. It also cannot reach Dell: he pulls the repo, so an
    untracked gate binds Joe's sessions and silently binds nothing of his, which
    is the twin-parity failure rule 61c64d91 exists to prevent. The 2026-08-08
    wipe proved the settings block is worth versioning; the scripts it invokes
    are the other half of the same control.

    Returns a list of (path, why) — repo-relative where possible.
    """
    block = raw_live_hooks_block()
    if not block:
        return []
    out = []
    for p in hook_block_script_paths(block):
        if not os.path.exists(p):
            out.append((p, "settings.json invokes it and IT DOES NOT EXIST"))
            continue
        # Ask git about the file IN ITS OWN checkout, not in this script's.
        # The live settings point at the primary checkout (~/carr-system), while
        # this script may be running from a worktree — comparing against the
        # script's own REPO made every hook look "outside the repo" and the
        # check silently found nothing, which is worse than not having it.
        d = os.path.dirname(p)
        inside = subprocess.run(["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, env=_git_env())
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            continue                       # not in any git checkout: not ours to version
        # `git ls-files --error-unmatch` is the exact question: is this path in
        # the index? A file that is merely present is not a file that survives.
        rc = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", p],
                            capture_output=True, env=_git_env()).returncode
        if rc != 0:
            top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, env=_git_env()).stdout.strip()
            rel = os.path.relpath(p, top) if top else p
            out.append((rel, "settings.json invokes it but git DOES NOT TRACK it"))
    return out


def carr_plists():
    try:
        return sorted(f for f in os.listdir(LAUNCHD_SRC)
                      if f.startswith("com.carr.") and f.endswith(".plist"))
    except FileNotFoundError:
        return []


def launchd_repo_path(name):
    """Canonical tracked source for one installed CARR LaunchAgent."""
    return LAUNCHD_ALT_REPO.get(name, os.path.join(LAUNCHD_REPO, name))


def raw_live_hooks_block():
    raw = read(SETTINGS)
    if raw is None:
        return None
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise RuntimeError("Claude settings root must be an object")
    return document.get("hooks")


def _read_claude_mcp_config():
    raw = read(CLAUDE_MCP_CONFIG)
    if raw is None:
        return {}
    if len(raw.encode("utf-8")) > continuity_config.MAX_CONFIG_BYTES:
        raise RuntimeError(f"Claude MCP configuration is too large: {CLAUDE_MCP_CONFIG}")
    try:
        document = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"Claude MCP configuration is invalid: {CLAUDE_MCP_CONFIG}") from exc
    if not isinstance(document, dict):
        raise RuntimeError("Claude MCP configuration root must be an object")
    return document


def claude_continuity_state(live_hooks, *, require_complete):
    """Validate the independent continuity receipt, hooks, and MCP binding."""
    contract = continuity_config.load(REPO)
    mode = continuity_config.read_mode(CLAUDE_CONTINUITY_MODE_FILE, contract)
    installed = mode in continuity_config.MODES
    continuity_config.validate_hooks(
        live_hooks, contract, require_complete=installed and require_complete
    )
    mcp = _read_claude_mcp_config()
    continuity_config.validate_mcp(mcp, contract, required=installed)
    servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
    has_mcp = isinstance(servers, dict) and continuity_config.MCP_SERVER_NAME in servers
    if not installed and (continuity_config.has_overlay(live_hooks) or has_mcp):
        raise RuntimeError(
            "Claude continuity hooks or MCP binding exist without a valid installed mode; "
            "use install-claude-continuity.py remove --apply"
        )
    return contract, mode


def live_hooks_block():
    """Return the base hook projection after validating the live overlay."""
    live = raw_live_hooks_block()
    contract, mode = claude_continuity_state(
        {} if live is None else live, require_complete=True
    )
    if live is None:
        return None
    return continuity_config.strip_installed_overlay(live, contract, mode)


def live_codex_hooks():
    """Only the CARR-owned Codex tuples are config-as-code state.

    Codex's global hooks document is shared with unrelated projects.  Comparing
    or installing it wholesale would turn another project's hook into CARR
    drift, then overwrite it on the next install.
    """
    raw = read(CODEX_HOOKS_SRC)
    if raw is None:
        return None
    try:
        live = json.loads(raw)
        desired = json.loads(concrete(read(CODEX_HOOKS_REPO)))
    except Exception:
        return None
    return portable(json.dumps(carr_owned_hooks_document(
        live, (desired.get("hooks") or {}).keys()), indent=2) + "\n")


def is_carr_hook_command(command):
    if not isinstance(command, str):
        return False
    candidate = command.replace("\\", "/").lower()
    return ("/carr-system/hooks/" in candidate or
            "/my drive/carr ai/hooks/" in candidate or
            "/carr-system/ops/codex-continuity-hook.py" in candidate or
            "/my drive/carr ai/ops/codex-continuity-hook.py" in candidate or
            "{{repo}}/hooks/" in candidate or
            "{{repo}}/ops/codex-continuity-hook.py" in candidate)


def carr_owned_hooks_document(document, include_events=()):
    """Extract CARR commands, retaining their event/matcher grouping exactly."""
    hooks = document.get("hooks") if isinstance(document, dict) else {}
    # A first-run Codex document may be absent or may not have a hooks key yet.
    # Both are empty hook collections, not iterables named None.
    if not isinstance(hooks, dict):
        hooks = {}
    out = {}
    for event in list(include_events) + [e for e in hooks if e not in include_events]:
        groups = hooks.get(event, []) if isinstance(hooks, dict) else []
        kept = []
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            commands = [h for h in group.get("hooks", [])
                        if isinstance(h, dict) and is_carr_hook_command(h.get("command"))]
            if commands:
                clone = dict(group)
                clone["hooks"] = commands
                kept.append(clone)
        if kept or event in include_events:
            out[event] = kept
    return {"hooks": out}


def merge_codex_carr_hooks(live, desired):
    """Replace only CARR-owned tuples, preserving every unrelated Codex hook."""
    result = json.loads(json.dumps(live if isinstance(live, dict) else {}))
    live_hooks = result.get("hooks")
    if not isinstance(live_hooks, dict):
        live_hooks = {}
    desired_hooks = desired.get("hooks") if isinstance(desired, dict) else {}
    for event in set(live_hooks) | set(desired_hooks or {}):
        retained = []
        for group in live_hooks.get(event, []) if isinstance(live_hooks.get(event, []), list) else []:
            if not isinstance(group, dict):
                retained.append(group)
                continue
            non_carr = [h for h in group.get("hooks", [])
                        if not (isinstance(h, dict) and is_carr_hook_command(h.get("command")))]
            if non_carr:
                clone = dict(group)
                clone["hooks"] = non_carr
                retained.append(clone)
        desired_groups = (desired_hooks or {}).get(event, [])
        retained.extend(json.loads(json.dumps(desired_groups)) if isinstance(desired_groups, list) else [])
        live_hooks[event] = retained
    result["hooks"] = live_hooks
    return result


def is_codex_continuity_hook_command(command):
    """Recognize only the continuity wrapper owned by the narrow installer."""
    if not isinstance(command, str):
        return False
    candidate = command.replace("\\", "/").lower()
    return ("/carr-system/ops/codex-continuity-hook.py" in candidate or
            "/my drive/carr ai/ops/codex-continuity-hook.py" in candidate or
            "{{repo}}/ops/codex-continuity-hook.py" in candidate)


def merge_codex_continuity_hooks(live, desired):
    """Merge continuity groups while preserving all other Codex configuration."""
    result = json.loads(json.dumps(live if isinstance(live, dict) else {}))
    live_hooks = result.get("hooks")
    if not isinstance(live_hooks, dict):
        live_hooks = {}
    desired_hooks = desired.get("hooks") if isinstance(desired, dict) else {}
    if not isinstance(desired_hooks, dict):
        desired_hooks = {}
    for event in CODEX_CONTINUITY_EVENTS:
        retained = []
        groups = live_hooks.get(event, [])
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                retained.append(group)
                continue
            non_continuity = [hook for hook in group.get("hooks", [])
                              if not (isinstance(hook, dict) and
                                      is_codex_continuity_hook_command(hook.get("command")))]
            if non_continuity:
                clone = dict(group)
                clone["hooks"] = non_continuity
                retained.append(clone)
        desired_groups = desired_hooks.get(event, [])
        if isinstance(desired_groups, list):
            retained.extend(json.loads(json.dumps(desired_groups)))
        live_hooks[event] = retained
    result["hooks"] = live_hooks
    return result


def codex_permissions_source():
    """Return the canonical default line and managed TOML body, or None."""
    raw = read(CODEX_PERMISSIONS_REPO)
    if raw is None:
        return None
    lines = raw.splitlines()
    if not lines or not re.fullmatch(r'default_permissions\s*=\s*"[^"]+"', lines[0]):
        raise ValueError("Codex permissions source must begin with default_permissions")
    return lines[0], "\n".join(lines[1:]).strip() + "\n"


def canonical_codex_permissions(raw):
    """Read reserved semantic paths, independent of comments and TOML syntax."""
    import tomllib
    import tomlkit
    try:
        source = codex_permissions_source()
        if source is None:
            return None
        default_line, body = source
        expected = tomllib.loads(default_line + "\n" + body)
        parsed = tomllib.loads(portable(raw))
        default = parsed.get('default_permissions')
        permissions = parsed.get('permissions')
        if not isinstance(default, str) or default not in expected['permissions'] \
                or not isinstance(permissions, dict):
            return None
        profiles = {}
        for name in expected['permissions']:
            profile = permissions.get(name)
            if not isinstance(profile, dict):
                return None
            profiles[name] = profile
        observed = {'default_permissions': default, 'permissions': profiles}
        # Equal semantics render in the source's form. Changed values remain
        # visible to drift checks and pull; unrelated settings stay outside it.
        if observed == expected:
            return default_line + "\n\n" + body
        return tomlkit.dumps(observed)
    except (tomllib.TOMLDecodeError, ValueError, TypeError, AttributeError):
        return None


def live_codex_permissions():
    """Render the CARR-owned portion of config.toml in canonical source form."""
    raw = read(CODEX_CONFIG)
    return None if raw is None else canonical_codex_permissions(raw)


def codex_permission_syntax(raw):
    """Locate real headers and marker comments, excluding strings and arrays.

    Recovery removes standalone reserved tables, including old duplicates.
    TOML Kit owns key/value interpretation after that recovery.
    """
    import tomllib
    headers, markers = [], []
    multiline = None
    depth = 0
    offset = 0
    tokens = re.compile(r'''"""|''' + "'''" + r'''|"(?:\\.|[^"\\])*"|'[^']*'|#[^\n]*|[\[\]{}]''')
    for line in raw.splitlines(keepends=True):
        if multiline is None and depth == 0:
            if line.strip() in (CODEX_PERMISSIONS_BEGIN, CODEX_PERMISSIONS_END):
                markers.append((offset, offset + len(line), line.strip()))
            if re.match(r'^\s*\[', line):
                try:
                    table = tomllib.loads(line + '\n__carr_slice__ = true\n')
                except tomllib.TOMLDecodeError:
                    pass  # An array continuation is not a table boundary.
                else:
                    permissions = table.get('permissions', {})
                    owned = bool(set(permissions) & {'carr_unattended', 'carr_drive_readonly'})
                    headers.append((offset, owned))
        cursor = 0
        while cursor < len(line):
            if multiline is not None:
                end = line.find(multiline, cursor)
                if end < 0:
                    break
                # An escaped quote cannot close a multiline basic string.
                escapes = len(line[:end]) - len(line[:end].rstrip('\\'))
                cursor = end + 3
                if multiline == '"""' and escapes % 2:
                    continue
                # One or two content quotes may precede the closing delimiter.
                while cursor < min(end + 5, len(line)) and line[cursor] == multiline[0]:
                    cursor += 1
                multiline = None
            else:
                token = tokens.search(line, cursor)
                if token is None:
                    break
                cursor = token.end()
                value = token.group()
                if value in ('"""', "'''"):
                    multiline = value
                elif value.startswith('#'):
                    break
                elif value in ('[', '{'):
                    depth += 1
                elif value in (']', '}'):
                    depth -= 1
        offset += len(line)
    spans = [(start, headers[i + 1][0] if i + 1 < len(headers) else len(raw))
             for i, (start, owned) in enumerate(headers) if owned]
    return spans, markers


def install_codex_permissions(raw, default_line, body):
    """Repair reserved semantic paths; preserve every unrelated TOML value."""
    import tomllib
    import tomlkit
    from tomlkit.items import InlineTable

    expected = tomllib.loads(default_line + "\n" + body)
    spans, markers = codex_permission_syntax(raw)
    # Marker ownership covers comment lines only, never enclosed user data.
    spans.extend((start, end) for start, end, _ in markers)
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    pieces, cursor = [], 0
    for start, end in merged:
        pieces.append(raw[cursor:start])
        cursor = end
    pieces.append(raw[cursor:])
    document = tomlkit.parse("".join(pieces))
    document['default_permissions'] = expected['default_permissions']
    permissions = document.get('permissions')
    if permissions is not None:
        for name in expected['permissions']:
            permissions.pop(name, None)
        if not permissions:
            del document['permissions']
        elif isinstance(permissions, InlineTable):
            # Inline parents are sealed in TOML; open the retained siblings so
            # canonical profile headers can be declared alongside them.
            retained = tomlkit.table()
            for name, value in permissions.items():
                retained.add(name, value)
            document['permissions'] = retained
    planned = (tomlkit.dumps(document).rstrip() + "\n\n" + CODEX_PERMISSIONS_BEGIN
               + "\n" + body.rstrip() + "\n" + CODEX_PERMISSIONS_END + "\n")
    parsed = tomllib.loads(planned)
    if parsed.get('default_permissions') != expected['default_permissions'] or any(
            parsed.get('permissions', {}).get(name) != profile
            for name, profile in expected['permissions'].items()):
        raise ValueError('Codex permission repair did not produce the required profiles/default')
    return planned


def codex_configuration_state():
    """Return configured, absent, or partial for this machine's Codex client.

    Codex is optional on secondary machines.  The absence of both user-owned
    files means there is no Codex surface to manage.  A hooks file without the
    config file is different: silently skipping that partial surface could
    leave a real client ungoverned, so callers must fail visibly.
    """
    has_hooks = os.path.exists(CODEX_HOOKS_SRC)
    has_config = os.path.exists(CODEX_CONFIG)
    if has_config:
        return "configured"
    if has_hooks:
        return "partial"
    return "absent"


def codex_app_server_request(method, params):
    """Call one bounded experimental Codex app-server method over JSONL stdio."""
    command = [os.environ.get("CARR_CODEX_CLI", "codex"), "app-server", "--stdio"]
    messages = [
        {"method": "initialize", "id": 1, "params": {
            "clientInfo": {"name": "carr-continuity-installer", "version": "1.0.0"},
            "capabilities": {"experimentalApi": True},
        }},
        {"method": "initialized", "params": {}},
        {"method": method, "id": 2, "params": params},
    ]
    process = None
    try:
        process = subprocess.Popen(
            command, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("Codex app-server stdio pipes were not created")
        payload = b"".join((json.dumps(message) + "\n").encode() for message in messages)
        process.stdin.write(payload)
        process.stdin.flush()
        deadline = time.monotonic() + CODEX_APP_SERVER_TIMEOUT_SECONDS
        buffer = b""
        stderr_buffer = b""
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [process.stdout, process.stderr], [], [],
                min(0.25, max(0, deadline - time.monotonic())))
            if not ready:
                if process.poll() is not None:
                    break
                continue
            for stream in ready:
                chunk = os.read(stream.fileno(), 65536)
                if stream is process.stderr:
                    stderr_buffer += chunk
                    if len(stderr_buffer) > CODEX_APP_SERVER_OUTPUT_LIMIT:
                        raise RuntimeError("Codex app-server stderr exceeded the 4 MiB limit")
                    continue
                if not chunk:
                    continue
                buffer += chunk
                if len(buffer) > CODEX_APP_SERVER_OUTPUT_LIMIT:
                    raise RuntimeError("Codex app-server response exceeded the 4 MiB limit")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    response = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"Codex app-server returned invalid JSON ({exc})") from exc
                if response.get("id") != 2:
                    continue
                if "error" in response:
                    error = response.get("error") or {}
                    message = str(error.get("message") or "request refused")[:500]
                    raise RuntimeError(f"Codex app-server {method} failed: {message}")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"Codex app-server {method} returned no object result")
                return result
        raise RuntimeError(f"Codex app-server {method} did not answer within "
                           f"{CODEX_APP_SERVER_TIMEOUT_SECONDS}s")
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Codex app-server {method} unavailable ({exc})") from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def canonical_codex_continuity_hooks():
    """Load and validate the four exact rendered hook contracts from the repo."""
    source = read(CODEX_HOOKS_REPO)
    if source is None:
        raise RuntimeError(f"no tracked Codex hooks at {CODEX_HOOKS_REPO}")
    try:
        document = json.loads(concrete(source))
    except Exception as exc:
        raise RuntimeError(f"{CODEX_HOOKS_REPO} is not valid JSON ({exc})") from exc
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{CODEX_HOOKS_REPO} must contain a hooks object")
    desired = {"hooks": {event: hooks.get(event, [])
                          for event in CODEX_CONTINUITY_EVENTS}}
    contracts = []
    for event in CODEX_CONTINUITY_EVENTS:
        groups = desired["hooks"][event]
        if (not isinstance(groups, list) or len(groups) != 1 or
                not isinstance(groups[0], dict)):
            raise RuntimeError(f"{event} must contain exactly one continuity group")
        group = groups[0]
        handlers = group.get("hooks")
        if (not isinstance(handlers, list) or len(handlers) != 1 or
                not isinstance(handlers[0], dict)):
            raise RuntimeError(f"{event} must contain exactly one continuity handler")
        handler = handlers[0]
        if (handler.get("type") != "command" or
                not isinstance(handler.get("command"), str) or
                not isinstance(handler.get("timeout"), int) or
                isinstance(handler.get("timeout"), bool)):
            raise RuntimeError(f"{event} continuity handler shape is invalid")
        contracts.append({
            "eventName": CODEX_CONTINUITY_APP_EVENTS[event],
            "command": handler["command"],
            "matcher": group.get("matcher"),
            "handlerType": handler["type"],
            "timeoutSec": handler["timeout"],
        })
    return desired, contracts


def codex_continuity_hook_entries(contracts, require_trusted=False):
    """Return the exact four user hook instances observed by Codex itself."""
    response = codex_app_server_request("hooks/list", {"cwds": [REPO]})
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise RuntimeError("hooks/list did not return exactly one requested working directory")
    listing = data[0]
    if listing.get("errors"):
        raise RuntimeError("hooks/list reported hook configuration errors")
    hooks = listing.get("hooks")
    if not isinstance(hooks, list):
        raise RuntimeError("hooks/list returned no hook array")
    source_path = os.path.realpath(CODEX_HOOKS_SRC)
    candidates = []
    for contract in contracts:
        matches = [hook for hook in hooks if isinstance(hook, dict) and
                   os.path.realpath(str(hook.get("sourcePath") or "")) == source_path and
                   all(hook.get(field) == value for field, value in contract.items())]
        if len(matches) != 1:
            raise RuntimeError("hooks/list did not uniquely match the canonical "
                               f"{contract['eventName']} continuity hook")
        candidates.append(matches[0])
    keys = set()
    for hook in candidates:
        key = hook.get("key")
        current_hash = hook.get("currentHash")
        if (hook.get("source") != "user" or hook.get("handlerType") != "command" or
                hook.get("enabled") is not True or not isinstance(key, str) or not key or
                not isinstance(current_hash, str) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", current_hash)):
            raise RuntimeError("hooks/list returned malformed continuity hook metadata")
        if key in keys:
            raise RuntimeError("hooks/list returned a duplicate continuity hook key")
        keys.add(key)
        if require_trusted and hook.get("trustStatus") != "trusted":
            raise RuntimeError(f"{hook.get('eventName')} continuity hook is "
                               f"{hook.get('trustStatus') or 'not trusted'}")
    return candidates


def _codex_user_config_layer():
    response = codex_app_server_request(
        "config/read", {"cwd": REPO, "includeLayers": True})
    layers = response.get("layers")
    if not isinstance(layers, list):
        raise RuntimeError("config/read returned no configuration layers")
    expected_path = os.path.realpath(CODEX_CONFIG)
    matches = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        name = layer.get("name")
        if (isinstance(name, dict) and name.get("type") == "user" and
                os.path.realpath(str(name.get("file") or "")) == expected_path):
            matches.append(layer)
    if len(matches) != 1:
        raise RuntimeError("config/read did not uniquely identify the user config layer")
    layer = matches[0]
    if (not isinstance(layer.get("config"), dict) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(layer.get("version") or ""))):
        raise RuntimeError("config/read returned malformed user config metadata")
    return layer


def _without_continuity_trust(config, keys):
    """Mask only selected trust tables so every unrelated value can be compared."""
    masked = copy.deepcopy(config)
    hooks = masked.get("hooks")
    if not isinstance(hooks, dict):
        return masked
    state = hooks.get("state")
    if isinstance(state, dict):
        for key in keys:
            state.pop(key, None)
        if not state:
            hooks.pop("state", None)
    if not hooks:
        masked.pop("hooks", None)
    return masked


def _hook_trust_state(config):
    hooks = config.get("hooks") if isinstance(config, dict) else None
    state = hooks.get("state") if isinstance(hooks, dict) else None
    return state if isinstance(state, dict) else {}


def _write_codex_config_edits(edits, expected_version):
    result = codex_app_server_request("config/batchWrite", {
        "edits": edits,
        "expectedVersion": expected_version,
        "filePath": CODEX_CONFIG,
        "reloadUserConfig": True,
    })
    if (result.get("status") != "ok" or
            os.path.realpath(str(result.get("filePath") or "")) !=
            os.path.realpath(CODEX_CONFIG)):
        raise RuntimeError("config/batchWrite did not confirm an effective user-config write")
    return _codex_user_config_layer()


def _restore_codex_continuity_trust(before_config, keys):
    """Restore selected trust tables without reverting unrelated concurrent config."""
    current = _codex_user_config_layer()
    current_config = current["config"]
    before_state = _hook_trust_state(before_config)
    current_state = _hook_trust_state(current_config)
    edits = []
    for key in keys:
        prior = before_state.get(key)
        if key in before_state and current_state.get(key) != prior:
            edits.append({"keyPath": f"hooks.state.{json.dumps(key)}",
                          "value": copy.deepcopy(prior), "mergeStrategy": "replace"})
        elif key not in before_state and key in current_state:
            edits.append({"keyPath": f"hooks.state.{json.dumps(key)}",
                          "value": None, "mergeStrategy": "upsert"})
    if not edits:
        return
    restored = _write_codex_config_edits(edits, current["version"])
    if (_without_continuity_trust(current_config, keys) !=
            _without_continuity_trust(restored["config"], keys)):
        raise RuntimeError("trust rollback changed unrelated Codex configuration")
    restored_state = _hook_trust_state(restored["config"])
    if any((key in before_state) != (key in restored_state) or
           (key in before_state and restored_state.get(key) != before_state.get(key))
           for key in keys):
        raise RuntimeError("trust rollback did not restore prior continuity entries")


def persist_codex_continuity_trust(entries, contracts, remove=False):
    """Atomically upsert or delete only four app-server-derived trust tables."""
    before = _codex_user_config_layer()
    config = before["config"]
    state = _hook_trust_state(config)
    expected = {entry["key"]: entry["currentHash"] for entry in entries}
    if remove:
        if all(key not in state for key in expected):
            print("  Codex continuity hook trust already absent")
            return 0
    elif (all(state.get(key) == {"trusted_hash": current_hash}
              for key, current_hash in expected.items()) and
          all(entry.get("trustStatus") == "trusted" for entry in entries)):
        print("  Codex continuity hooks already trusted")
        return 0

    edits = []
    for key, current_hash in expected.items():
        quoted = json.dumps(key)
        edits.append({
            "keyPath": (f"hooks.state.{quoted}" if remove else
                        f"hooks.state.{quoted}.trusted_hash"),
            "value": None if remove else current_hash,
            "mergeStrategy": "upsert",
        })
    try:
        after = _write_codex_config_edits(edits, before["version"])
        after_state = _hook_trust_state(after["config"])
        if _without_continuity_trust(config, expected) != _without_continuity_trust(
                after["config"], expected):
            raise RuntimeError("config/batchWrite changed unrelated Codex configuration")
        if remove:
            if any(key in after_state for key in expected):
                raise RuntimeError("config/batchWrite left continuity trust entries behind")
            print("  REMOVED   four Codex continuity hook trust entries")
            return 0
        if any(after_state.get(key) != {"trusted_hash": current_hash}
               for key, current_hash in expected.items()):
            raise RuntimeError("config/batchWrite did not persist exact continuity hook hashes")
        verified = codex_continuity_hook_entries(contracts, require_trusted=True)
        observed = {entry["key"]: entry["currentHash"] for entry in verified}
        if observed != expected:
            raise RuntimeError("hooks/list changed continuity identity during trust installation")
        print("  TRUSTED   four Codex continuity hooks using authoritative current hashes")
        return 0
    except RuntimeError as exc:
        try:
            _restore_codex_continuity_trust(config, expected)
        except RuntimeError as rollback_exc:
            raise RuntimeError(f"{exc}; trust rollback failed ({rollback_exc})") from exc
        raise


def cmd_install_codex_continuity_mcp(apply=False):
    """Install the existing adapter in explicit Codex mode with scoped permissions."""
    import copy
    import hashlib
    import shutil
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    DEST = Path.home() / '.config/carr/codex-continuity'
    SERVER = 'carr-codex-continuity'
    TOOLS = ['codex-checkpoint', 'codex-read-recovery']
    FILES = ['continuity-stdio-proxy.mjs', 'continuity-reference-manifest.mjs',
             'local-client-auth.mjs']
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node runtime unavailable')
    before = _codex_user_config_layer()
    expected = copy.deepcopy(before['config'])
    servers = expected.setdefault('mcp_servers', {})
    servers[SERVER] = {'command': node, 'args': [str(DEST / FILES[0]), '--codex'],
                       'enabled_tools': TOOLS,
                       'tools': {name: {'approval_mode': 'approve'} for name in TOOLS}}
    # Remove the two broken duplicate routes in Codex only. All other tools and
    # authentication settings retain their previous values.
    for name in ('carr', 'carr-records'):
        if name in servers:
            disabled = servers[name].setdefault('disabled_tools', [])
            for tool in TOOLS:
                if tool not in disabled:
                    disabled.append(tool)
    if apply:
        DEST.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in FILES:
            source = ROOT / 'mcp-server' / name
            target = DEST / name
            if not target.exists() or target.read_bytes() != source.read_bytes():
                temporary = DEST / (name + '.tmp')
                temporary.write_bytes(source.read_bytes())
                temporary.replace(target)
        edits = [{'keyPath': 'mcp_servers.' + SERVER, 'value': servers[SERVER], 'mergeStrategy': 'replace'}]
        for name in ('carr', 'carr-records'):
            if name in servers:
                edits.append({'keyPath': 'mcp_servers.' + name + '.disabled_tools',
                              'value': servers[name]['disabled_tools'], 'mergeStrategy': 'replace'})
        after = _write_codex_config_edits(edits, before['version'])
    else:
        after = before
    if after['config'] != expected:
        raise RuntimeError('Codex continuity MCP configuration differs from the expected scoped update')
    for name in FILES:
        if (DEST / name).read_bytes() != (ROOT / 'mcp-server' / name).read_bytes():
            raise RuntimeError('Installed adapter differs from source: ' + name)
    print(json.dumps({'ok': True, 'server': SERVER, 'tools': TOOLS,
                      'credential': 'existing dedicated Codex credential, never copied into configuration',
                      'adapter_sha256': hashlib.sha256((DEST / FILES[0]).read_bytes()).hexdigest()}))

    return 0


def cmd_verify_codex_continuity():
    """Read-only proof that Codex will automatically execute all four hooks."""
    try:
        _, contracts = canonical_codex_continuity_hooks()
        entries = codex_continuity_hook_entries(contracts, require_trusted=True)
    except RuntimeError as exc:
        print(f"ERROR: Codex continuity trust verification failed ({exc}).")
        return 1
    for entry in entries:
        print(f"  TRUSTED   {entry['eventName']}: {entry['currentHash']}")
    print("  Codex hooks/list confirms all four continuity hooks are trusted")
    return 0


def _write_codex_hooks_text(raw):
    """Atomically write or restore hooks.json after validating its object shape."""
    if raw is None:
        if os.path.exists(CODEX_HOOKS_SRC):
            os.unlink(CODEX_HOOKS_SRC)
        return
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Codex hooks restore content is not a JSON object")
    parent = os.path.dirname(CODEX_HOOKS_SRC)
    os.makedirs(parent, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent,
                                         prefix=".codex-continuity-", delete=False) as fh:
            temp_path = fh.name
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, CODEX_HOOKS_SRC)
        check = json.loads(read(CODEX_HOOKS_SRC))
        if not isinstance(check, dict):
            raise RuntimeError("written Codex hooks are not a JSON object")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def cmd_install_codex_continuity(apply, remove=False):
    """Install only the four CARR continuity hook groups owned by Codex.

    This intentionally has no relationship to the broad machine reconciler:
    it never reads or writes Claude settings, Codex permissions, LaunchAgents,
    scheduled tasks, git configuration, or any other global client state.
    """
    try:
        canonical_desired, contracts = canonical_codex_continuity_hooks()
    except RuntimeError as exc:
        print(f"ERROR: Codex continuity hook source is invalid ({exc}).")
        return 1
    desired = ({"hooks": {event: [] for event in CODEX_CONTINUITY_EVENTS}}
               if remove else canonical_desired)

    raw_live = read(CODEX_HOOKS_SRC)
    if raw_live is None:
        if remove:
            print("  No Codex continuity hook file exists; nothing to remove")
            return 0
        live = {}
        print(f"  Codex continuity hooks: {'WILL CREATE' if apply else 'would create'} {CODEX_HOOKS_SRC}")
    else:
        try:
            live = json.loads(raw_live)
        except Exception as exc:
            print(f"ERROR: {CODEX_HOOKS_SRC} is not valid JSON ({exc}) — refusing to touch it.")
            return 1
        if not isinstance(live, dict):
            print(f"ERROR: {CODEX_HOOKS_SRC} must contain a JSON object — refusing to touch it.")
            return 1

    merged = merge_codex_continuity_hooks(live, desired)
    rendered = json.dumps(merged, indent=2) + "\n"
    unchanged = raw_live is not None and raw_live == rendered
    if unchanged and (remove or not apply):
        print("  Codex continuity hooks already match the repo (unrelated configuration preserved)")
        return 0
    if not apply:
        print(f"  Codex continuity hooks: would write {CODEX_HOOKS_SRC}")
        action = "remove-codex-continuity" if remove else "install-codex-continuity"
        print(f"\nDRY RUN — nothing written. Re-run with `{action} --apply`.")
        return 0

    # Removal must capture Codex's exact keys and prior trust state before
    # hooks.json stops exposing them. Its trust deletion is phase one; if the
    # hook rewrite fails, only those four trust tables are restored. Install is
    # the inverse: hooks.json is phase one and is restored if trust phase two
    # refuses.
    entries = None
    removal_config = None
    if remove:
        try:
            entries = codex_continuity_hook_entries(contracts)
            removal_config = _codex_user_config_layer()["config"]
            persist_codex_continuity_trust(entries, contracts, remove=True)
        except RuntimeError as exc:
            print(f"ERROR: Codex continuity trust removal failed ({exc}).")
            return 1

    if not unchanged:
        parent = os.path.dirname(CODEX_HOOKS_SRC)
        os.makedirs(parent, exist_ok=True)
        backup = CODEX_HOOKS_SRC + ".bak-codex-continuity"
        had_live = raw_live is not None
        if had_live:
            shutil.copy2(CODEX_HOOKS_SRC, backup)
        try:
            _write_codex_hooks_text(rendered)
        except Exception as exc:
            rollback_errors = []
            try:
                _write_codex_hooks_text(raw_live)
            except Exception as rollback_exc:
                rollback_errors.append(f"hooks rollback failed ({rollback_exc})")
            if remove and removal_config is not None and entries is not None:
                try:
                    _restore_codex_continuity_trust(
                        removal_config, {entry["key"] for entry in entries})
                except RuntimeError as rollback_exc:
                    rollback_errors.append(f"trust rollback failed ({rollback_exc})")
            suffix = ("; " + "; ".join(rollback_errors)) if rollback_errors else ""
            print(f"ERROR: Codex continuity hook write failed ({exc}){suffix}.")
            return 1
        print(f"  WROTE OK  {CODEX_HOOKS_SRC} "
              f"(backup: {backup if had_live else 'none; new file'})")
    else:
        print("  Codex continuity hooks already match the repo "
              "(unrelated configuration preserved)")

    if remove:
        return 0
    try:
        entries = codex_continuity_hook_entries(contracts)
        persist_codex_continuity_trust(entries, contracts)
    except RuntimeError as exc:
        try:
            _write_codex_hooks_text(raw_live)
        except Exception as rollback_exc:
            print("ERROR: Codex continuity trust update failed "
                  f"({exc}); hooks rollback failed ({rollback_exc}).")
            return 1
        action = "removal" if remove else "installation"
        print(f"ERROR: Codex continuity trust {action} failed ({exc}); "
              "prior hooks restored.")
        return 1
    return 0


# A DEFINITION-ONLY TASK IS NOT A MISSING JOB. Four calendar-prebrief contracts
# live in the repository and say, in their own bodies, "This definition is
# disabled. Do not create, enable, or invoke any scheduler." Their activation
# needs Joe's allowlist approval, EventKit permission and device evidence that
# do not exist yet. The repository holds them as CONTRACTS, deliberately ahead
# of the machine — config-as-code-selftest even asserts a definition-only tick
# is not installed before cutover.
#
# Reporting them as MISSING FROM MACHINE told every session to install four
# jobs whose own text forbids installing them, and blocked the pre-push gate
# until somebody did. That is the ephemeral-task problem from the other
# direction: the repository and the machine legitimately differ, and the check
# could not express it.
def is_definition_only_task(text):
    """True when a repo-side task declares itself disabled rather than deployed."""
    return bool(text) and "This definition is disabled" in text


def pairs():
    """(label, live_text, repo_path) for every tracked item. live_text is
    already portable; repo contents are compared verbatim against it."""
    out = []

    hooks = live_hooks_block()
    out.append(("hooks block (settings.json)",
                None if hooks is None else portable(json.dumps(hooks, indent=2) + "\n"),
                HOOKS_REPO))
    if codex_configuration_state() == "configured":
        out.append(("Codex CARR hooks (hooks.json)", live_codex_hooks(), CODEX_HOOKS_REPO))
        out.append(("Codex CARR permissions (config.toml)", live_codex_permissions(),
                    CODEX_PERMISSIONS_REPO))

    seen = set()
    for name in sorted(os.listdir(TASKS_SRC)) if os.path.isdir(TASKS_SRC) else []:
        skill = os.path.join(TASKS_SRC, name, "SKILL.md")
        if os.path.isfile(skill) and scheduled_task_allowed(name):
            if is_ephemeral_scheduled_task(read(skill)):
                continue
            seen.add(f"{name}.SKILL.md")
            out.append((f"scheduled-task {name}", portable(read(skill)),
                        os.path.join(TASKS_REPO, f"{name}.SKILL.md")))
    # A task deleted from the machine but still in the repo is drift too — the
    # repo would otherwise quietly claim a job that no longer runs anywhere.
    for name, source in sorted(tracked_scheduled_task_paths().items()):
        filename = f"{name}.SKILL.md"
        if scheduled_task_allowed(name) and filename not in seen:
            if is_definition_only_task(read(source)):
                continue
            out.append((f"scheduled-task {name} (IN REPO, NOT ON MACHINE)",
                        None, source))

    for f in carr_plists():
        # A DEFINITION_ONLY agent is deliberately absent from the machine, so it
        # is not an ordinary tracked pair in either direction. Its absence is the
        # intended state rather than drift, and a copy that HAS been installed
        # must not be waved through merely because its body matches the repo —
        # matching bytes are exactly what an unauthorized install would have.
        # It is reported separately, by presence, in cmd_check.
        if f in DEFINITION_ONLY:
            continue
        out.append((f"launchd {f}", portable(read(os.path.join(LAUNCHD_SRC, f))),
                    launchd_repo_path(f)))
    return out


def definition_only_installed_plists():
    """DEFINITION_ONLY agents that are on the machine and must not be.

    Body equality is deliberately not consulted: the failure being detected is
    that an agent whose activation gate has not passed exists in LaunchAgents at
    all, and an install performed from this very repo is the likeliest way for
    that to happen.
    """
    return [f for f in carr_plists() if f in DEFINITION_ONLY]


def pending_launchd_reloads():
    """CARR jobs whose disk render has not been verified as loaded."""
    if not os.path.isdir(LAUNCHD_SRC):
        return []
    suffix = ".plist.pending-reload"
    return sorted(name[:-len(".pending-reload")]
                  for name in os.listdir(LAUNCHD_SRC)
                  if name.startswith("com.carr.") and name.endswith(suffix))


# STARTINTERVAL IS REFUSED IN EVERY CARR LAUNCHAGENT TEMPLATE (2026-09-26).
# On the Mac Studio, macOS 27.0, launchd never fires an agent scheduled with
# StartInterval: `launchctl print` shows `runs = 0` and `pended nondemand spawn
# = speculative|interval`, RunAtLoad does not fire either, and only a manual
# kickstart runs it. StartCalendarInterval agents on the same machine fire on
# time. Fourteen CARR jobs were silently dead there while this check reported
# "repo matches machine", because a dead schedule installed from the repo's own
# bytes is not drift. So the template itself is judged: a live StartInterval is
# refused (check reports it, install will not render it), and a converted
# template must still hold exactly what lib/launchd_calendar.py renders for the
# interval its marker names. Convert with
# `python3 -m lib.launchd_calendar rewrite <template>`.
def refused_launchd_templates(repo=None):
    """(repo-relative path, problem) for every CARR template the converter refuses.

    Judges the checkout this file sits in (REPO_HERE), not the canonical one the
    machine installs from: a template's soundness is a property of the source
    under review, so a worktree carrying the fix must not be failed by the main
    checkout it has not reached yet. In the main checkout the two are the same
    tree, and install separately refuses the exact source it would render."""
    root = repo or REPO_HERE
    out = []
    for path in launchd_calendar.carr_templates(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            out.append((os.path.relpath(path, root), f"unreadable: {exc}"))
            continue
        for problem in launchd_calendar.audit_template(text):
            out.append((os.path.relpath(path, root), problem))
    return out


def launchd_path_refusal(body):
    """Verify runtime checkouts for installation and installed-path audits."""
    try:
        definition = plistlib.loads(body.encode("utf-8"))
    except (ValueError, plistlib.InvalidFileException) as exc:
        return f"invalid LaunchAgent: {exc}"
    if not isinstance(definition, dict):
        return "invalid LaunchAgent: expected a dictionary"
    arguments = definition.get("ProgramArguments") or []
    if not isinstance(arguments, list):
        return "invalid LaunchAgent: ProgramArguments must be an array"
    working_directory = definition.get("WorkingDirectory") or "/"
    if not isinstance(working_directory, str) or not os.path.isabs(working_directory):
        return "invalid LaunchAgent: WorkingDirectory must be absolute"
    paths = [working_directory, definition.get("Program"), *arguments]
    for path in paths:
        if not isinstance(path, str) or not path or path.startswith("-"):
            continue
        resolved = os.path.realpath(os.path.join(working_directory, path))
        directory = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
        # A declared script may not exist yet; inspect its closest existing
        # ancestor so a missing file cannot hide a feature checkout.
        while not os.path.isdir(directory) and directory != os.path.dirname(directory):
            directory = os.path.dirname(directory)
        try:
            top = subprocess.run(["git", "-C", directory, "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, env=_git_env(), timeout=15)
            if top.returncode:
                if top.returncode == 128 and top.stderr.strip() == "fatal: not a git repository (or any of the parent directories): .git":
                    continue
                return f"cannot verify repository identity for {path}: {top.stderr.strip()}"
            checkout = top.stdout.strip()
            dirs = subprocess.run(["git", "-C", checkout, "rev-parse", "--path-format=absolute",
                                   "--git-dir", "--git-common-dir"],
                                  capture_output=True, text=True, env=_git_env(), timeout=15)
            identities = dirs.stdout.strip().splitlines()
            if dirs.returncode or len(identities) != 2:
                return f"cannot verify main/worktree identity for {path}"
            # Only named package-manager checkouts may use release branches.
            # Standalone session clones have the same Git directory shape.
            if identities[0] == identities[1] and os.path.realpath(checkout) in {
                    os.path.realpath(root) for root in LAUNCHD_DEPENDENCY_CHECKOUTS}:
                continue
            branch = subprocess.run(["git", "-C", checkout, "symbolic-ref", "--short", "HEAD"],
                                    capture_output=True, text=True, env=_git_env(), timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"cannot verify main checkout for {path}: {exc}"
        if identities[0] != identities[1] or branch.returncode or branch.stdout.strip() != "main":
            return (f"runtime path {path} selects {branch.stdout.strip() or 'detached HEAD'}; "
                    "point the LaunchAgent at the canonical main checkout and reinstall")
    return None


def cmd_check_launchd_main_paths():
    """Read installed CARR agents without changing or reloading any plist."""
    names = carr_plists()
    board = "local.carr-progress-board.plist"
    if os.path.isfile(os.path.join(LAUNCHD_SRC, board)):
        names.append(board)
    failures = []
    for name in names:
        refusal = launchd_path_refusal(read(os.path.join(LAUNCHD_SRC, name)) or "")
        if refusal:
            failures.append((name, refusal))
    for name, refusal in failures:
        print(f"launchd main-path check: REFUSED {name}: {refusal}")
    if not failures:
        print("launchd main-path check: runtime paths verified")
    return 1 if failures else 0


def launchd_template_refusal(source_text):
    """The first reason install must not render this template, or None."""
    problems = launchd_calendar.audit_template(source_text or "")
    return problems[0] if problems else launchd_path_refusal(concrete(source_text or ""))


def cmd_check():
    # THE OBSERVATION TRAILS THE VERDICT. _cmd_check returns this command's
    # whole judgement; the core.hooksPath line is appended after it because it
    # is an observation about a no-touch setting, so it changes neither the exit
    # code nor the first line the health row reads.
    verdict = _cmd_check()
    print(git_hooks_path_report())
    return verdict


def _cmd_check():
    # SEVERITY IS NOT COSMETIC HERE, and the 2026-08-08 incident is why.
    # A tracked item MISSING from the machine means a protection that was
    # supposed to be running is not running. A tracked item merely DIFFERENT
    # usually means the repo baseline lagged a deliberate live change. Those are
    # not the same event and must not print the same line.
    #
    # WHAT ACTUALLY FAILED. On 2026-08-06 the guard's matcher was widened to
    # "Bash|WebFetch" on the machine and nobody pulled, so this check went red
    # for a benign reason and STAYED red. On 2026-08-08 15:46 a plugin install
    # rewrote ~/.claude/settings.json and deleted the entire hooks block — all
    # five gates off. The headline both days: "DRIFT — 2 of 28 items". Identical
    # string, and the health row prints only that first line, so the difference
    # between a matcher tweak and total gate annihilation was invisible. It went
    # unnoticed for a day and was found by accident.
    #
    # The lesson is rule 590b11e1's, arriving the other way round: a check that
    # is chronically red detects nothing, because a reader who has learned to
    # skip a red row will skip the one that matters. Keeping this row green when
    # nothing is wrong is therefore part of the control, not tidiness.
    if codex_configuration_state() == "partial":
        print(f"config-as-code: CODEX PARTIAL — {CODEX_HOOKS_SRC} exists but "
              f"{CODEX_CONFIG} does not; refusing to treat this client as absent")
        return 1

    try:
        configured_pairs = pairs()
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"config-as-code: CLAUDE CONTINUITY INVALID — {exc}")
        return 1

    missing, untracked, different = [], [], []
    for label, live, repo_path in configured_pairs:
        have = read(repo_path)
        if live is None:
            missing.append((label, "on disk: MISSING; in repo: present"))
        elif have is None:
            untracked.append((label, "on disk: present; in repo: NOT TRACKED"))
        elif not tracked_text_match(label, live, have):
            different.append((label, "TRACKED BUT DIFFERENT from the live copy"))
    # A hook script the settings block invokes but git does not track is a
    # separate failure from a settings mismatch, and it used to be invisible
    # because this tool only ever compared the block itself. It is reported as
    # UNVERSIONED rather than folded into `untracked`, because the remedy is a
    # commit rather than a `pull`.
    unversioned = hook_scripts_untracked()
    secondary_task_violations = secondary_scheduled_task_violations()
    disallowed = [
        (f"scheduled-task {name} (NOT ALLOWED ON SECONDARY)",
         "present on disk; this machine has no approved scope for it")
        for name in secondary_task_violations
    ]
    # An agent held as a definition is expected to be absent, so its absence is
    # silence. Its PRESENCE is the finding, and it is a finding whatever the
    # body says: an activation that skipped its gate installs the repo's own
    # bytes, so byte equality is the shape the failure takes rather than
    # evidence against it.
    disallowed += [
        (f"launchd {name} (DEFINITION ONLY, MUST NOT BE INSTALLED)",
         f"installed in {LAUNCHD_SRC}; {DEFINITION_ONLY[name]}")
        for name in definition_only_installed_plists()
    ]
    # A template launchd would load and then never fire. Reported whatever the
    # machine holds, because the machine matching it is exactly the failure.
    refused = [
        (f"launchd template {rel} (SCHEDULE REFUSED)", problem)
        for rel, problem in refused_launchd_templates()
    ]
    pending_reloads = [
        (f"launchd {name} (PENDING RELOAD)",
         "disk bytes do not prove the new definition is loaded; retry installation "
         "from an external process and verify launchd registration")
        for name in pending_launchd_reloads()
    ]
    drift = missing + untracked + different + disallowed + refused + pending_reloads
    if not drift and not unversioned:
        prerequisite_report = prerequisite_failure_report(PREREQUISITE_CHECK(REPO))
        if prerequisite_report:
            print(prerequisite_report)
            return 1
        mode = continuity_config.read_mode(
            CLAUDE_CONTINUITY_MODE_FILE, continuity_config.load(REPO)
        )
        if mode in continuity_config.MODES:
            print(f"  Claude continuity overlay and dedicated MCP binding verified; mode={mode}")
        print(f"config-as-code: OK — {len(configured_pairs)} items, repo matches machine")
        return 0
    if not drift and unversioned:
        print(f"config-as-code: UNVERSIONED HOOKS — {len(unversioned)} script(s) the live "
              f"settings invoke are not in git: " + ", ".join(p for p, _ in unversioned))
        for p, why in unversioned:
            print(f"  {p}\n      {why}")
        print("\n  A gate that exists in one working tree only is one `git clean` or one\n"
              "  fresh clone from gone, and it can never reach Dell. Commit them:\n"
              "      git -C ~/carr-system add " + " ".join(p for p, _ in unversioned))
        return 1
    # The headline carries the severity, because callers that summarise this
    # tool (tools/health-check.py) read the FIRST LINE ONLY.
    # The denominator includes a CARR-owned disallowed task even though it is
    # intentionally omitted from normal pairs() on a secondary.  Otherwise
    # "16 of 4" could claim to have checked only four items while reporting
    # sixteen violations, which is operationally misleading.
    checked_items = (len(configured_pairs) + len(disallowed) + len(refused)
                     + len(pending_reloads))
    headline = f"config-as-code: DRIFT — {len(drift)} of {checked_items} items"
    if missing:
        headline += f" — {len(missing)} MISSING FROM MACHINE: " + ", ".join(
            label for label, _ in missing)
    print(headline)
    for label, why in drift:
        print(f"  {label}\n      {why}")
    if missing:
        print("\n  MISSING means a tracked config is NOT ON THE MACHINE. If it is the\n"
              "  hooks block, every gate is currently off — restore it FIRST:\n"
              "      python3 ops/config-as-code.py install --apply\n"
              "  then prove it with a denial that should fail, e.g. a WebFetch to a\n"
              "  host outside KNOWN_HOSTS.")
    if untracked or different:
        print("\n  `ops/config-as-code.py pull` to capture the machine into the repo.")
    if disallowed:
        print("\n  A secondary machine must not run CARR's primary-only scheduled-task "
              "catalogue. `install --apply` can quarantine an exact tracked render; "
              "a modified tracked task needs review and is never overwritten.")
    if refused:
        print("\n  A REFUSED template would load and never fire on macOS 27. Convert it in\n"
              "  the repo, then re-render the installed agents:\n"
              "      python3 -m lib.launchd_calendar rewrite <template>\n"
              "      python3 ops/config-as-code.py reinstall-launchd-calendar --apply")
    # Reported even when settings drift is also present: the two have different
    # remedies (a pull versus a commit), so folding them together would hide one.
    if unversioned:
        print(f"\n  ALSO — {len(unversioned)} hook script(s) the live settings invoke are not in git:")
        for p, why in unversioned:
            print(f"  {p}\n      {why}")
        print("      git -C ~/carr-system add " + " ".join(p for p, _ in unversioned))
    return 1


def cmd_pull(apply):
    if codex_configuration_state() == "partial":
        print(f"ERROR: partial Codex configuration — {CODEX_HOOKS_SRC} exists but "
              f"{CODEX_CONFIG} does not; refusing to omit it from the captured baseline.")
        return 1
    disallowed = secondary_scheduled_task_violations()
    if disallowed:
        print("ERROR: unapproved scheduled task(s) on this secondary machine: "
              + ", ".join(disallowed) + "; refusing to capture them into the repo.")
        return 1
    try:
        configured_pairs = pairs()
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Claude continuity configuration is invalid ({exc}); refusing to capture it.")
        return 1
    wrote = 0
    for label, live, repo_path in configured_pairs:
        if live is None:
            print(f"  SKIP  {label} (not on this machine; left in the repo)")
            continue
        if read(repo_path) == live:
            continue
        print(f"  {'WRITE' if apply else 'would write'}  {os.path.relpath(repo_path, REPO)}")
        if apply:
            os.makedirs(os.path.dirname(repo_path), exist_ok=True)
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write(live)
        wrote += 1
    print(f"\n{wrote} item(s) {'written' if apply else 'would be written'}."
          + ("" if apply else " Re-run with --apply."))
    return 0


def retire_primary_only_plist(filename, live, apply):
    """Unload a primary-only job found on a secondary and move its plist aside.

    Happens when a Mac that used to be primary is marked secondary. Returns
    ``retired``, ``planned`` (dry run), or ``failed``. Refuses to overwrite an
    earlier quarantined copy, same rule as the scheduled-task quarantine.
    """
    dest = os.path.join(LAUNCHD_QUARANTINE, filename)
    if not apply:
        print(f"  would retire  {filename} (primary-only job on a secondary) -> {dest}")
        return "planned"
    if os.path.exists(dest):
        print(f"  ERROR  quarantine already has {dest}; refusing to overwrite it")
        return "failed"
    subprocess.run(["launchctl", "unload", "-w", live],
                   capture_output=True, check=False)
    os.makedirs(LAUNCHD_QUARANTINE, exist_ok=True)
    shutil.move(live, dest)
    print(f"  RETIRED  {filename} (primary-only job on a secondary) -> {dest}")
    return "retired"


# SELF-RELOAD HAND-OFF (2026-09-26). Hourly fleet-sync runs `install --apply`
# from inside its own LaunchAgent. When fleet-sync's OWN plist changes (as it
# does when the calendar conversion lands), reloading it here would kill the
# wrapper mid-receipt, so this used to refuse and exit 1 -- every hour, on
# every Mac, until someone ran install by hand. Instead the new body is staged
# outside LaunchAgents and a detached one-shot (its own session, so launchd's
# process-group cleanup of the finished job does not take it down) waits for
# this job's whole process group to be gone, refuses if the installed plist
# changed since staging (a newer install must never be overwritten by an
# older staged body), then boots the old definition out, moves the staged
# body into place, and bootstraps it. A failed bootstrap puts
# the previous body back and loads that; if even that fails the log says
# "RESTORE FAILED" with the manual command. Nothing is kickstarted.
SELF_RELOAD_HANDOFF_DIR = os.path.join(HOME, ".config", "carr", "launchd-handoff")
SELF_RELOAD_WAIT_SECONDS = 3600
SELF_RELOAD_SHELL = "/bin/bash"
LAUNCHCTL_BIN = "/bin/launchctl"
SMOKE_LABEL_PREFIX = "com.carr.handoff-smoke-"
SELF_RELOAD_SCRIPT = r"""
pg="$1"; launchctl="$2"; domain="$3"; label="$4"; staged="$5"; dest="$6"; log="$7"; wait_max="$8"
expected="$9"
exec >>"$log" 2>&1
installed_sha() {
  if [ ! -e "$dest" ]; then echo absent
  elif [ -x /usr/bin/shasum ]; then /usr/bin/shasum -a 256 "$dest" | cut -d' ' -f1
  else /usr/bin/sha256sum "$dest" | cut -d' ' -f1; fi
}
# Wait on the WHOLE process group (-pg), not just its leader: a wrapper that
# has exited can leave children still running under launchd's job. Spelled
# `kill -0 -"$pg"`, never `kill -0 -- "-$pg"`: dash's builtin kill rejects
# `--` (rc 2), which read as "the group is gone" and reloaded mid-run on the
# Ubuntu CI runner. The helper is also started with /bin/bash explicitly, and
# ops/config-as-code-launchd-selftest.py runs this script under bash and dash.
waited=0
while kill -0 -"$pg" 2>/dev/null; do
  waited=$((waited + 1))
  if [ "$waited" -ge "$wait_max" ]; then
    echo "self-reload $label: GAVE UP waiting for process group $pg; staged body left at $staged"
    exit 1
  fi
  sleep 1
done
# The installed plist must still be the one this body was staged against.
# Anything newer (a later install, a hand edit) wins; the staged body is stale.
found=$(installed_sha)
if [ "$found" != "$expected" ]; then
  echo "self-reload $label: REFUSED, $dest changed since staging (expected $expected, found $found); nothing booted out or loaded; stale staged body left at $staged"
  exit 1
fi
cp -p "$dest" "$staged.previous" || { echo "self-reload $label: cannot back up $dest"; exit 1; }
"$launchctl" bootout "$domain/$label" >/dev/null 2>&1
found=$(installed_sha)
if [ "$found" != "$expected" ]; then
  echo "self-reload $label: REFUSED, $dest changed during bootout (expected $expected, found $found); loading what is installed, not the stale staged body"
  if "$launchctl" bootstrap "$domain" "$dest"; then exit 1; fi
  echo "self-reload $label: RESTORE FAILED, $label is unloaded; fix by hand: launchctl bootstrap $domain $dest"
  exit 1
fi
mv -f "$staged" "$dest" || {
  echo "self-reload $label: cannot move the staged body into place"
  "$launchctl" bootstrap "$domain" "$dest" || echo "self-reload $label: RESTORE FAILED, $label is unloaded; fix by hand: launchctl bootstrap $domain $dest"
  exit 1
}
if "$launchctl" bootstrap "$domain" "$dest"; then
  echo "self-reload $label: loaded the new definition"
  exit 0
fi
echo "self-reload $label: BOOTSTRAP FAILED; restoring the previous body"
mv -f "$staged.previous" "$dest"
if "$launchctl" bootstrap "$domain" "$dest"; then
  echo "self-reload $label: restored the previous definition"
else
  echo "self-reload $label: RESTORE FAILED, $label is unloaded; fix by hand: launchctl bootstrap $domain $dest"
fi
exit 1
"""


def installed_sha256(path):
    """sha256 of the installed file, or ``absent`` (the one-shot compares the same way)."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return "absent"


def hand_off_self_reload(filename, dest, body, label, launchctl=LAUNCHCTL_BIN):
    """Stage ``body`` and start the detached one-shot; ``deferred`` or ``failed``."""
    try:
        expected = installed_sha256(dest)
        os.makedirs(SELF_RELOAD_HANDOFF_DIR, exist_ok=True)
        staged = os.path.join(SELF_RELOAD_HANDOFF_DIR, filename + ".staged")
        with open(staged, "w", encoding="utf-8") as fh:
            fh.write(body)
        log = os.path.join(SELF_RELOAD_HANDOFF_DIR, filename + ".log")
        subprocess.Popen(
            [SELF_RELOAD_SHELL, "-c", SELF_RELOAD_SCRIPT, "carr-self-reload",
             str(os.getpgrp()), launchctl, f"gui/{os.getuid()}", label, staged, dest,
             log, str(SELF_RELOAD_WAIT_SECONDS), expected],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
    except OSError as exc:
        print(f"      SELF-RELOAD HAND-OFF FAILED ({filename}: {exc}); destination left unchanged")
        print("      remedy: run `python3 ops/config-as-code.py install --apply` "
              "from an external process")
        return "failed"
    print(f"      self-reload deferred ({filename}: active installer job {label}; a detached "
          f"one-shot reloads it after this run exits; log {log})")
    return "deferred"


def launchd_registration(label):
    """Read the job's registered plist path, or distinguish absence from error."""
    inspected = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                               capture_output=True, text=True, check=False)
    if inspected.returncode == 0:
        path_match = re.search(r"(?m)^\s*path = (.+)$", inspected.stdout or "")
        if path_match:
            return "loaded", path_match.group(1).strip()
        return "failed", "launchctl print omitted the registered path"
    detail = ((inspected.stderr or "") + "\n" + (inspected.stdout or "")).strip()
    if inspected.returncode == 113 and f'Could not find service "{label}"' in detail:
        return "absent", ""
    return "failed", detail[:80] or "unknown launchctl error"


def install_launchd_plist(filename, dest, body, body_matches):
    """Render and load one plist without letting an active job unload itself.

    Returns ``loaded``, ``kept``, ``deferred`` or ``failed``.  A changed active
    plist cannot be rendered honestly without also reloading it, and reloading
    it here kills the receipt wrapper.  That case leaves the destination
    untouched and hands the reload to a detached one-shot that runs only after
    this job has exited (hand_off_self_reload); if the hand-off cannot be
    started it fails with the exact external-install remedy. For other jobs,
    a pending marker survives interrupted or failed reloads until launchd is
    observed absent before load and registered at the installed path after it.
    """
    try:
        label = plistlib.loads(body.encode("utf-8")).get("Label", "")
    except (AttributeError, plistlib.InvalidFileException, ValueError):
        label = ""
    active_label = os.environ.get(ACTIVE_LAUNCHD_LABEL_ENV, "").strip()
    is_active_self = bool(label and active_label == label)
    pending = dest + ".pending-reload"

    if not label:
        print(f"      INSPECT FAILED ({filename} has no valid launchd label); "
              "destination left unchanged")
        return "failed"

    if is_active_self:
        if os.path.exists(pending):
            print(f"      PENDING RELOAD ({label}; active installer cannot verify its own "
                  "loaded definition); run install from an external process")
            return "failed"
        if body_matches:
            print(f"      kept loaded (active installer job {label}; body unchanged)")
            return "kept"
        return hand_off_self_reload(filename, dest, body, label)

    # Every non-self mutation first proves this label is absent or belongs to
    # this destination. A pending retry is an obligation to reconcile, not
    # authority to unload a same-label job registered from another path.
    state, detail = launchd_registration(label)
    if state == "loaded" and detail != dest:
        print(f"      INSPECT FAILED ({label} is loaded from an unexpected path); "
              "destination left unchanged")
        return "failed"
    if state == "failed":
        print(f"      INSPECT FAILED ({detail}); destination left unchanged")
        return "failed"
    if body_matches and not os.path.exists(pending) and state == "loaded":
        # The hourly installer must not disturb a definition that is already
        # loaded. Repeated unload/load cycles can strand a RunAtLoad/KeepAlive
        # job in launchd's pending-spawn state even though its plist is right.
        print(f"      kept loaded ({label}; body unchanged)")
        return "kept"

    # This marker is written before the disk plist changes. A failed or
    # interrupted reload leaves it behind across installer processes, so a
    # matching file and matching launchctl path cannot mask an old definition.
    with open(pending, "w", encoding="utf-8") as fh:
        fh.write(hashlib.sha256(body.encode("utf-8")).hexdigest() + "\n")
    if not body_matches:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)

    subprocess.run(["launchctl", "unload", "-w", dest],
                   capture_output=True, check=False)
    state, detail = launchd_registration(label)
    if state != "absent":
        print(f"      UNLOAD FAILED ({detail if state == 'failed' else 'job remains loaded'}); "
              "pending reload retained")
        return "failed"
    r = subprocess.run(["launchctl", "load", "-w", dest],
                       capture_output=True, text=True, check=False)
    if r.returncode == 0:
        state, detail = launchd_registration(label)
        if (state == "loaded" and detail == dest
                and launchd_texts_match(read(dest), body)):
            os.unlink(pending)
            print("      loaded")
            return "loaded"
        print(f"      LOAD UNVERIFIED ({detail if state == 'failed' else state}); "
              "pending reload retained")
        return "failed"
    print(f"      LOAD FAILED ({(r.stderr or r.stdout).strip()[:80]}) "
          "— pending reload retained; migration will remain incomplete")
    return "failed"


def cmd_install_progress_board(apply=False, repo=None):
    """Migrate the existing board agent to the repository wrapper, then read
    launchd's arguments back. This does not create a new schedule or label.
    Runtime and state belong to the canonical main checkout maintained by
    bin/fleet-sync.sh. --repo may name that checkout explicitly, but cannot
    select a feature tree even for pre-merge verification.
    """
    runtime_repo = os.path.abspath(os.path.expanduser(repo)) if repo else REPO
    if os.path.realpath(runtime_repo) != os.path.realpath(REPO):
        print("progress-board: runtime must use the canonical main checkout; "
              "feature checkout installation is refused")
        return 1
    wrapper = os.path.join(runtime_repo, "ops", "progress-board-render.sh")
    python = os.path.join(runtime_repo, ".venv", "bin", "python")
    if not os.path.isfile(wrapper) or not os.access(python, os.X_OK):
        print("progress-board: selected checkout wrapper or repository interpreter unavailable; "
              "select a repository checkout containing the wrapper and interpreter")
        return 1
    label = "local.carr-progress-board"
    dest = os.path.join(HOME, "Library", "LaunchAgents", label + ".plist")
    try:
        with open(dest, "rb") as handle:
            current = plistlib.load(handle)
        if not isinstance(current, dict) or current.get("Label") != label:
            raise ValueError("unexpected board agent label")
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        print(f"progress-board: existing agent unavailable: {exc}; no schedule created")
        return 1
    desired = dict(current)
    desired["ProgramArguments"] = ["/bin/bash", wrapper]
    desired["WorkingDirectory"] = runtime_repo
    desired["EnvironmentVariables"] = dict(current.get("EnvironmentVariables", {}))
    desired["EnvironmentVariables"]["PROGRESS_BOARD_ROOT"] = os.path.join(REPO, "out")
    refusal = launchd_path_refusal(plistlib.dumps(desired).decode("utf-8"))
    if refusal:
        print(f"progress-board: REFUSED {refusal}")
        return 1
    def registered_arguments():
        observed = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                                  capture_output=True, text=True, check=False, timeout=15)
        args = re.search(r"(?ms)^\s*arguments = \{\n(.*?)^\s*\}", observed.stdout or "")
        return ([line.strip() for line in args.group(1).splitlines()]
                if observed.returncode == 0 and args else [])

    matches = desired == current and registered_arguments() == desired["ProgramArguments"]
    if apply:
        outcome = install_launchd_plist(os.path.basename(dest), dest,
                                       plistlib.dumps(desired).decode("utf-8"), matches)
        if outcome not in {"loaded", "kept"}:
            return 1
    elif not matches:
        print("progress-board: existing agent needs migration: "
              "ops/config-as-code.py install-progress-board --apply")
        return 1
    if registered_arguments() != desired["ProgramArguments"]:
        print("progress-board: launchd arguments unverified; migration is incomplete")
        return 1
    print(f"progress-board: verified registered repository wrapper: {wrapper}")
    return 0


def write_claude_settings(path, document, before, sink=None):
    """Write the settings render, optionally exposing one redacted fake witness.

    ``sink`` is a callback used by the R06 fixture only.  Production supplies no
    callback, and this function contains no notification or target transport.
    The callback runs before overwrite and receives hashes/counts, never config
    values or paths.
    """
    import hashlib
    body = json.dumps(document, indent=2) + "\n"
    before = before if before is not None else ""
    permissions = document.get("permissions", {}) if isinstance(document, dict) else {}
    permission_count = sum(
        len(value) for value in permissions.values() if isinstance(value, list)
    ) if isinstance(permissions, dict) else 0
    event = {
        "schema_version": "r06-config-pre-overwrite.v1",
        "writer": "ops/config-as-code.py:write_claude_settings",
        "target_class": "claude-settings",
        "before_sha256": "sha256:" + hashlib.sha256(before.encode("utf-8")).hexdigest(),
        "after_sha256": "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "preserved_top_level_key_count": len([
            key for key in document if key != "hooks"
        ]) if isinstance(document, dict) else 0,
        "preserved_permission_entry_count": permission_count,
        "actual_notification": False,
    }
    if sink is not None:
        sink(copy.deepcopy(event))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return event


# The conventional relative value, kept as the name of a shape this file
# RECOGNISES. Nothing here writes it: see git_hooks_path_report.
GIT_HOOKS_RELATIVE = "ops/githooks"


def git_hooks_path_conformant(configured: str, hooks_dir: str) -> bool:
    """Does an ALREADY-CONFIGURED core.hooksPath point at these same hooks?

    A CLASSIFIER FOR A REPORT, NOT A TEST THAT PRECEDES A WRITE. core.hooksPath
    lives in the single .git/config that every worktree on this machine reads,
    and install writes it under no condition whatever (see cmd_install). The
    only caller is git_hooks_path_report, which prints what is observed so a
    human can decide; a future caller that uses this answer to write the setting
    would reintroduce exactly the hazard the no-touch contract exists for.

    Install used to write the relative "ops/githooks" unconditionally, which
    turned the ABSOLUTE canonical path — the designed state, as
    ops/prepush-floor-selftest.py says in as many words ("always canonical's —
    core.hooksPath is one shared path for every worktree") — into a value git
    resolves against whichever worktree is running the hook. On a Mac carrying
    ~50 worktrees that silently changed WHICH pre-push runs in every one of
    them, as a side effect of a job about plists. Found and undone by hand
    during the 2026-09-12 Gate Zero activation.

    The test is RESOLUTION, not string equality, so two shapes count as pointing
    at these hooks:

      * the literal relative default, which git resolves per worktree but which
        names this repository's own hooks directory in a canonical checkout;
      * an absolute path whose realpath is this repo's ops/githooks.

    A relative value that is NOT the default is non-conformant: git resolves it
    per worktree, so it names no single directory this function could honestly
    compare. Unset is non-conformant too — that is the fresh-machine case, and
    it is now reported rather than repaired.
    """
    if not configured:
        return False
    if configured == GIT_HOOKS_RELATIVE:
        return True
    if not os.path.isabs(configured):
        return False
    return os.path.realpath(configured) == os.path.realpath(hooks_dir)


def git_hooks_path_report() -> str:
    """One INFORMATIONAL line for `check`: what core.hooksPath is. Nothing else.

    This is the whole of what this tool has to say about a setting it may not
    touch. It never writes, and it never contributes to the check's exit code —
    a machine whose hooks are off is not drift this installer may silently
    repair, because the repair would land in the one .git/config every worktree
    shares. It is printed AFTER the verdict on purpose: the health row reads
    only the first line of this command's output, so an observation must never
    take the headline from a real finding.
    """
    hooks_dir = os.path.join(REPO, "ops", "githooks")
    observed = subprocess.run(
        ["git", "-C", REPO, "config", "--get", "core.hooksPath"],
        capture_output=True, text=True, env=_git_env()).stdout.strip()
    if not observed:
        note = (f"unset, so git runs .git/hooks and the guards in {hooks_dir} "
                "are not active; enabling them is a human's own command")
    elif observed == GIT_HOOKS_RELATIVE:
        note = ("the relative default, which git resolves against whichever "
                "worktree runs the hook")
    elif git_hooks_path_conformant(observed, hooks_dir):
        note = f"resolves to {hooks_dir}"
    else:
        note = (f"does not resolve to {hooks_dir}; this machine's hook "
                "resolution is someone else's deliberate setting")
    return f"  git core.hooksPath: {observed or '(unset)'} — {note} [informational]"


def cmd_install(apply):
    """repo -> machine. The half that makes a second machine possible."""
    settings_existed = os.path.exists(SETTINGS)
    raw = read(SETTINGS) if settings_existed else "{}"
    if not settings_existed:
        print(f"  Claude settings: WILL BE CREATED at {SETTINGS}")
    try:
        cfg = json.loads(raw)
    except Exception as exc:
        print(f"ERROR: {SETTINGS} is not valid JSON ({exc}) — refusing to touch it.")
        return 1

    src = read(HOOKS_REPO)
    if src is None:
        print(f"ERROR: no tracked hooks block at {HOOKS_REPO}. Run `pull` first.")
        return 1

    try:
        base_planned = json.loads(concrete(src))
        live_hooks = cfg.get("hooks", {})
        contract, continuity_mode = claude_continuity_state(
            live_hooks, require_complete=False
        )
        planned = continuity_config.render_effective_hooks(
            base_planned, live_hooks, contract, continuity_mode
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Claude continuity configuration is invalid ({exc}); "
              "settings left untouched.")
        return 1

    if continuity_mode in continuity_config.MODES:
        present = all(
            continuity_config.observed_entries(live_hooks.get(event)) == wanted
            for event, wanted in contract.hooks.items()
        )
        action = "PRESERVE" if present else "RESTORE"
        print(f"  Claude continuity overlay: WILL {action} five canonical entries; "
              f"mode={continuity_mode}; dedicated MCP binding verified")

    # REFUSE A BLOCK WHOSE SCRIPTS ARE NOT THERE (added after 2026-08-24).
    # Settings apply on the very next prompt of every session, so a hooks block
    # invoking a script this machine does not have blocks EVERY session the
    # moment it lands. That is not hypothetical: on 2026-08-24 install ran from
    # a worktree that had the freshly merged hook wrapper while {{REPO}} still
    # expanded to a canonical checkout one commit behind, and every session on
    # this Mac was dead overnight. cmd_check's hook_scripts_untracked() sees
    # this class only AFTER the write; install is the moment it is preventable.
    absent = [p for p in hook_block_script_paths(planned) if not os.path.exists(p)]
    if absent:
        print("ERROR: the tracked hooks block invokes script(s) this machine does not have:")
        for p in absent:
            print(f"    {p}")
        print("  Installing anyway would block every session at its next prompt.")
        print("  Most likely the checkout those paths point into is behind the branch")
        print("  that shipped them — fast-forward it, then re-run install.")
        return 1

    if cfg.get("hooks") == planned:
        print("  hooks block already matches the repo")
    else:
        print("  hooks block: WILL BE REPLACED with the repo's version")
        print(f"    preserved top-level keys: {sorted(k for k in cfg if k != 'hooks')}")
        p = cfg.get("permissions", {})
        print(f"    preserved permission entries: "
              f"{sum(len(v) for v in p.values() if isinstance(v, list))}")
        cfg["hooks"] = planned

    codex_state = codex_configuration_state()
    merged_codex = permission_config = None
    if codex_state == "partial":
        print(f"ERROR: partial Codex configuration — {CODEX_HOOKS_SRC} exists but "
              f"{CODEX_CONFIG} does not; refusing to skip or overwrite it.")
        return 1
    if codex_state == "absent":
        print("  SKIP  Codex configuration (Codex is not configured on this machine)")
    else:
        codex_src = read(CODEX_HOOKS_REPO)
        if codex_src is None:
            print(f"ERROR: no tracked Codex hooks at {CODEX_HOOKS_REPO}. Run `pull` first.")
            return 1
        codex_body = concrete(codex_src)
        try:
            desired_codex = json.loads(codex_body)
        except Exception as exc:
            print(f"ERROR: {CODEX_HOOKS_REPO} is not valid JSON ({exc}) — refusing to deploy it.")
            return 1
        live_codex_raw = read(CODEX_HOOKS_SRC)
        try:
            live_codex = json.loads(live_codex_raw) if live_codex_raw is not None else {}
        except Exception as exc:
            print(f"ERROR: {CODEX_HOOKS_SRC} is not valid JSON ({exc}) — refusing to touch it.")
            return 1
        merged_codex = merge_codex_carr_hooks(live_codex, desired_codex)
        desired_events = (desired_codex.get("hooks") or {}).keys()
        if carr_owned_hooks_document(live_codex, desired_events) == desired_codex:
            print("  Codex CARR hooks already match the repo (unrelated hooks preserved)")
        else:
            print("  Codex CARR hooks: WILL BE RECONCILED with the repo; unrelated hooks preserved")

        try:
            permission_source = codex_permissions_source()
        except Exception as exc:
            print(f"ERROR: {CODEX_PERMISSIONS_REPO} is invalid ({exc}) — refusing to deploy it.")
            return 1
        if permission_source is None:
            print(f"ERROR: no tracked Codex permissions at {CODEX_PERMISSIONS_REPO}. Run `pull` first.")
            return 1
        code_config = read(CODEX_CONFIG)
        permission_default, permission_body = permission_source
        permission_config = install_codex_permissions(
            code_config, concrete(permission_default), concrete(permission_body)
        )
        if permission_config == code_config:
            print("  Codex CARR permissions already match the repo")
        else:
            print("  Codex CARR permissions: WILL MAKE THE CURRENT WORKSPACE READ-ONLY")

    task_plan = scheduled_task_install_plan()
    if task_plan["modified"]:
        print("ERROR: modified CARR scheduled task(s) active on this secondary: "
              + ", ".join(task_plan["modified"])
              + "; refusing to move or overwrite them.")
        return 1
    if IS_PRIMARY:
        for name in task_plan["install"]:
            source = tracked_scheduled_task_paths()[name]
            destination = os.path.join(TASKS_SRC, name, "SKILL.md")
            # Same rule the launchd loop below already applies to
            # DEFINITION_ONLY plists, which this loop had never honoured. A task
            # whose body says "do not create, enable, or invoke any scheduler"
            # must not be written INTO the scheduler directory by the installer
            # that claims to be converging the machine to the repository.
            if is_definition_only_task(read(source)):
                print(f"  SKIP  scheduled task {name} (definition only: "
                      f"awaits its own stated activation approval)")
                continue
            if read(destination) == concrete(read(source)):
                print(f"  scheduled task already matches: {name}")
            else:
                print(f"  {'WRITE' if apply else 'would write'}  scheduled task {name}")
    elif task_plan["exact"]:
        for name in task_plan["exact"]:
            destination = os.path.join(TASKS_QUARANTINE, name)
            if os.path.exists(destination):
                print(f"ERROR: recovery quarantine already has {destination}; refusing to overwrite it.")
                return 1
            print(f"  {'QUARANTINE' if apply else 'would quarantine'}  scheduled task {name} "
                  f"-> {destination}")
    else:
        print("  SKIP  scheduled tasks (secondary machines install none; no approved "
              "secondary task scope is configured)")

    launchd_activation_failures = []
    if apply:
        os.makedirs(LAUNCHD_SRC, exist_ok=True)
    for f in sorted(os.listdir(LAUNCHD_REPO)) if os.path.isdir(LAUNCHD_REPO) else []:
        if f in DEFINITION_ONLY:
            print(f"  SKIP  {f} (definition only: {DEFINITION_ONLY[f]})")
            continue
        if f in PRIMARY_ONLY and not IS_PRIMARY:
            live = os.path.join(LAUNCHD_SRC, f)
            if os.path.exists(live):
                if retire_primary_only_plist(f, live, apply) == "failed":
                    launchd_activation_failures.append(f)
            else:
                print(f"  SKIP  {f} (writes shared state; runs on the primary machine only)")
            continue
        if f in SECONDARY_ONLY and IS_PRIMARY:
            print(f"  SKIP  {f} (the nightly chain already does this here)")
            continue
        dest = os.path.join(LAUNCHD_SRC, f)
        source = read(launchd_repo_path(f))
        if source is None:
            print(f"  ERROR  cannot render {f} because its tracked source is missing")
            return 1
        refusal = launchd_template_refusal(source)
        if refusal:
            # Never install a schedule launchd will load and then never fire:
            # the job would look installed and be dead (see the block above
            # refused_launchd_templates). The installed copy is left as it is.
            print(f"  REFUSED  {f}: {refusal}")
            launchd_activation_failures.append(f)
            continue
        body = concrete(source)
        body_matches = launchd_texts_match(read(dest), source)
        if body_matches and not apply and not os.path.exists(dest + ".pending-reload"):
            continue
        gone = missing_targets(body)
        if gone:
            print(f"  SKIP  {f} (not built on this machine: {gone[0]})")
            continue
        if body_matches:
            print(f"  VERIFY LOADED  {dest}")
        else:
            print(f"  {'WRITE' if apply else 'would write'}  {dest}")
        if apply:
            # Load it, do not print a command for a human to paste (rule
            # e313a3ca). Writing the plist and stopping leaves the job on disk
            # and dead: on a fresh machine that means the nightly never runs,
            # so the record-derived fetch allowlist is generated once by the
            # migration and then never refreshed as clients are added. A
            # pending marker keeps an interrupted reload visible until an
            # absent-before/load/registered-after sequence verifies it.
            outcome = install_launchd_plist(f, dest, body, body_matches)
            if outcome == "failed":
                launchd_activation_failures.append(f)

    # Git hooks. Added 2026-08-03, when Dell was granted WRITE and it turned out
    # branch protection is unavailable on a private free-plan repo — so the pull
    # request review team-loops T39 relied on has no server-side replacement.
    # ops/githooks/pre-push refuses a direct push to main from any identity but
    # the owner's. The hooks ship with the code and this block makes them
    # executable. That is ALL it does.
    #
    # core.hooksPath IS NO-TOUCH, UNCONDITIONALLY. Install does not set it, does
    # not reconcile it, and does not read it in order to decide whether to write
    # it — not when it is unset, not when it is relative, not when it is
    # absolute, not when it names another repository's hooks. ONE .git/config
    # holds that value for every worktree on this machine, so any write here
    # changes which pre-push runs in all of them as a side effect of a job about
    # plists: an apply run for two launch agents re-pointed ~50 worktrees away
    # from the canonical hooks ops/prepush-floor-selftest.py relies on, and the
    # absolute canonical path had to be restored by hand during the 2026-09-12
    # Gate Zero activation. A conditional write is the same hazard with a
    # narrower trigger, so there is no condition under which this code writes.
    #
    # WHICH LEAVES THE FRESH-MACHINE CASE TO A HUMAN, deliberately. Whether hook
    # resolution is enabled at all is REPORTED by `config-as-code.py check`
    # (git_hooks_path_report below) as an observation that never changes the
    # value and never changes the exit code. Setting it on a new clone is one
    # documented command a person runs once, which is a smaller cost than an
    # installer that can silently re-aim every worktree on the machine.
    hooks_dir = os.path.join(REPO, "ops", "githooks")
    if os.path.isdir(hooks_dir):
        if apply:
            for h in sorted(os.listdir(hooks_dir)):
                p = os.path.join(hooks_dir, h)
                if os.path.isfile(p):
                    os.chmod(p, os.stat(p).st_mode | 0o111)
            print("  git hooks made executable "
                  "(core.hooksPath untouched — `check` reports the observed value)")
        else:
            print("  would make ops/githooks executable "
                  "(core.hooksPath untouched — `check` reports the observed value)")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    # Scheduled task definitions are reconciled as definitions only.  Scheduler
    # registration stays outside this tool; an on-disk SKILL.md must never be
    # mistaken for proof that a task is enabled.
    if IS_PRIMARY:
        for name, source in sorted(tracked_scheduled_task_paths().items()):
            # THE SKIP PRINTED ABOVE IS AN ANNOUNCEMENT; THIS IS THE WRITE IT
            # DESCRIBES. Guarding only the announcing loop left `install
            # --apply` reporting "SKIP scheduled task X (definition only)" and
            # then creating X anyway, which is precisely the outcome that loop's
            # comment says must not happen. The plan loop and the write loop
            # both walk the same registry, so a rule applied to one and not the
            # other is not a partial fix — it is a fix that prints.
            if is_definition_only_task(read(source)):
                continue
            destination = os.path.join(TASKS_SRC, name, "SKILL.md")
            desired = concrete(read(source))
            if read(destination) != desired:
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with open(destination, "w", encoding="utf-8") as fh:
                    fh.write(desired)
    elif task_plan["exact"]:
        os.makedirs(TASKS_QUARANTINE, exist_ok=True)
        for name in task_plan["exact"]:
            source_dir = os.path.join(TASKS_SRC, name)
            destination = os.path.join(TASKS_QUARANTINE, name)
            # The full directory moves so task-local state stays together and
            # the active scheduler directory contains no CARR definition.
            shutil.move(source_dir, destination)

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    backup = SETTINGS + ".bak-config-as-code"
    if settings_existed:
        shutil.copy2(SETTINGS, backup)
    write_claude_settings(SETTINGS, cfg, raw)
    try:
        json.loads(read(SETTINGS))
    except Exception as exc:
        if settings_existed:
            shutil.copy2(backup, SETTINGS)
            remedy = f"restored {backup}"
        else:
            os.unlink(SETTINGS)
            remedy = "removed the invalid new file"
        print(f"ERROR: write produced unparseable JSON ({exc}) — {remedy}")
        return 1
    written_backups = [backup] if settings_existed else []
    if codex_state == "configured":
        os.makedirs(os.path.dirname(CODEX_HOOKS_SRC), exist_ok=True)
        codex_backup = CODEX_HOOKS_SRC + ".bak-config-as-code"
        if os.path.exists(CODEX_HOOKS_SRC):
            shutil.copy2(CODEX_HOOKS_SRC, codex_backup)
            written_backups.append(codex_backup)
        with open(CODEX_HOOKS_SRC, "w", encoding="utf-8") as fh:
            json.dump(merged_codex, fh, indent=2)
            fh.write("\n")
        try:
            json.loads(read(CODEX_HOOKS_SRC))
        except Exception as exc:
            if os.path.exists(codex_backup):
                shutil.copy2(codex_backup, CODEX_HOOKS_SRC)
            print(f"ERROR: Codex hook write produced unparseable JSON ({exc}) — restored backup")
            return 1
        code_config_backup = CODEX_CONFIG + ".bak-config-as-code"
        shutil.copy2(CODEX_CONFIG, code_config_backup)
        written_backups.append(code_config_backup)
        with open(CODEX_CONFIG, "w", encoding="utf-8") as fh:
            fh.write(permission_config)
        try:
            import tomllib
            tomllib.loads(read(CODEX_CONFIG))
        except Exception as exc:
            shutil.copy2(code_config_backup, CODEX_CONFIG)
            print(f"ERROR: Codex config write produced invalid TOML ({exc}) — restored backup")
            return 1
    # NO RESTART NEEDED, and the old message here said otherwise for months.
    # Live-tested 2026-08-09 with two independent confirmations: git-writer-gate
    # and gate-edit-gate were both installed MID-SESSION and both fired in a
    # session that started before either existed. Claude Code reads the hooks
    # block per tool call, not once at session start. This matters — the old
    # wording implied every other running session stayed unguarded until it was
    # restarted, which would have made a gate install nearly useless on a machine
    # running five sessions. The opposite is true: an install takes effect
    # everywhere immediately. Rule 97326357 — a claim about a surface becomes
    # doctrine only after a live test from that surface.
    if launchd_activation_failures:
        print("ERROR: LaunchAgent install/reload failed for: "
              + ", ".join(launchd_activation_failures))
        return 1
    backup_note = ", ".join(written_backups) if written_backups else "none; new files"
    if codex_state == "absent":
        client_note = "Codex was not configured and was left absent."
    else:
        client_note = ("Codex must trust a changed non-managed hook definition before it "
                       "runs. Both configured clients are protected.")
    print(f"\nWROTE OK (backups: {backup_note}). Claude Code reads its hooks block "
          f"per tool call; {client_note}")
    return 0


def config_selftest():
    """Regression proof that Codex install touches only CARR-owned tuples."""
    desired = {"hooks": {"Stop": [{"hooks": [{
        "type": "command", "command": "/Users/booko/carr-system/hooks/completion-evidence-gate.py", "timeout": 15,
    }]}]}}
    live = {"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "/Users/booko/other/hooks/keep.py", "timeout": 5}]},
        {"hooks": [{"type": "command", "command": "/Users/booko/carr-system/hooks/old.py", "timeout": 10}]},
        {"hooks": [
            {"type": "command", "command": "/Users/booko/other/hooks/mixed.py", "timeout": 5},
            {"type": "command", "command": "/Users/booko/carr-system/hooks/old2.py", "timeout": 10},
        ]},
    ]}, "user_setting": {"keep": True}}
    merged = merge_codex_carr_hooks(live, desired)
    commands = [hook.get("command") for group in merged["hooks"]["Stop"]
                for hook in group.get("hooks", []) if isinstance(hook, dict)]
    cases = [
        ("unrelated Codex hook preserved", "/Users/booko/other/hooks/keep.py" in commands),
        ("mixed unrelated Codex hook preserved", "/Users/booko/other/hooks/mixed.py" in commands),
        ("stale CARR hook removed", "/Users/booko/carr-system/hooks/old.py" not in commands),
        ("desired CARR hook installed once", commands.count(
            "/Users/booko/carr-system/hooks/completion-evidence-gate.py") == 1),
        ("unrelated top-level key preserved", merged.get("user_setting") == {"keep": True}),
        ("CARR snapshot exact", carr_owned_hooks_document(merged, ["Stop"]) == desired),
    ]
    for label, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    print(f"config-as-code-selftest: {sum(ok for _, ok in cases)}/{len(cases)} passed")
    return 0 if all(ok for _, ok in cases) else 1


# REINSTALL-LAUNCHD-CALENDAR: the one-off repair for agents installed before
# their templates moved from StartInterval to StartCalendarInterval (see
# refused_launchd_templates). An installed agent keeps its dead StartInterval
# body until it is rewritten AND bootstrapped again, and `install --apply` does
# far more than that (hooks, tasks, every other agent). This mode touches only
# an INSTALLED agent whose template carries the converter's marker and whose
# installed body differs from the rendered template:
#
#   * an agent that already matches is not rewritten and not reloaded;
#   * an agent that is not installed is not installed here (install owns
#     machine scope and first installs);
#   * definition-only, primary-only-on-a-secondary and not-built agents are
#     skipped by the same rules install applies;
#   * the label in CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL is refused, since
#     reloading the job running this would kill it mid-write.
#
# NOTHING IS KICKSTARTED unless --kickstart is given; a re-bootstrapped agent
# with RunAtLoad true runs once at bootstrap because that is what RunAtLoad
# means. DRY RUN unless --apply. A failed bootstrap restores the previous body
# and bootstraps it again, and the exit status is 1.
#
#     ops/config-as-code.py reinstall-launchd-calendar            # plan only
#     ops/config-as-code.py reinstall-launchd-calendar --apply
#     ops/config-as-code.py reinstall-launchd-calendar --apply --kickstart
#
# --templates, --launch-agents and --launchctl exist for the hermetic selftest
# (ops/reinstall-launchd-calendar-selftest.py).
def launchd_calendar_reinstall_plan(templates_dir, agents_dir):
    """One row per CARR template: what reinstall-launchd-calendar does with it and why."""
    rows = []
    active = os.environ.get(ACTIVE_LAUNCHD_LABEL_ENV, "").strip()
    for name in sorted(os.listdir(templates_dir)) if os.path.isdir(templates_dir) else []:
        if not (name.startswith("com.carr.") and name.endswith(".plist")):
            continue
        source = read(LAUNCHD_ALT_REPO.get(name, os.path.join(templates_dir, name))) or ""
        dest = os.path.join(agents_dir, name)
        row = {"name": name, "dest": dest, "action": "skip", "why": ""}
        rows.append(row)
        if launchd_calendar.MARKER not in source:
            row["why"] = "not a converted interval schedule"
            continue
        refusal = launchd_template_refusal(source)
        if refusal:
            row.update(action="fail", why=f"template refused: {refusal}")
            continue
        if name in DEFINITION_ONLY:
            row["why"] = "definition only"
            continue
        if name in PRIMARY_ONLY and not IS_PRIMARY:
            row["why"] = "primary-only job on a secondary (install retires it)"
            continue
        if name in SECONDARY_ONLY and IS_PRIMARY:
            row["why"] = "secondary-only job on the primary (install skips it)"
            continue
        installed = read(dest)
        if installed is None:
            row["why"] = "not installed here (install owns first installs)"
            continue
        if launchd_texts_match(installed, source):
            row["why"] = "installed plist already matches"
            continue
        body = concrete(source)
        gone = missing_targets(body)
        if gone:
            row["why"] = f"not built on this machine: {gone[0]}"
            continue
        label = launchd_calendar.plist_label(source)
        if active and label == active:
            row.update(action="fail", why=f"{label} is the job running this; run it from outside")
            continue
        row.update(action="reinstall", why="installed body differs from the calendar template",
                   label=label, body=body, previous=installed)
    return rows


def _launchctl(launchctl, *args):
    return subprocess.run([launchctl, *args], capture_output=True, text=True, check=False)


def _atomic_write(path, text):
    """Write ``text`` to ``path`` by rename, so a failure leaves the old file whole.

    Refuses a read-only destination rather than replacing it: a plist someone
    made read-only was protected on purpose, and os.replace would silently
    defeat that. Raises OSError; nothing has been changed when it does."""
    if os.path.exists(path) and not os.access(path, os.W_OK):
        raise PermissionError(f"{path} is read-only; left as it is")
    folder = os.path.dirname(path) or "."
    fd, staged = tempfile.mkstemp(prefix=".carr-staged-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if os.path.exists(path):
            shutil.copymode(path, staged)
        os.replace(staged, path)
    except BaseException:
        if os.path.exists(staged):
            os.unlink(staged)
        raise


def _launchctl_detail(result):
    return (result.stderr or result.stdout or "").strip()[:120]


class AgentLeftUnloaded(RuntimeError):
    """A restore failed: the agent is not loaded and needs a human."""


def _restore_calendar_agent(row, launchctl, domain):
    """Put the previous body back and load it. True only when it is loaded again."""
    dest, label = row["dest"], row["label"]
    try:
        _atomic_write(dest, row["previous"])
    except OSError as exc:
        print(f"      RESTORE FAILED, {label} is unloaded: could not rewrite {dest}: {exc}")
        print(f"      fix by hand: put the previous body back, then "
              f"`launchctl bootstrap {domain} {dest}`")
        return False
    back = _launchctl(launchctl, "bootstrap", domain, dest)
    if back.returncode != 0:
        print(f"      RESTORE FAILED, {label} is unloaded ({_launchctl_detail(back)})")
        print(f"      fix by hand: `launchctl bootstrap {domain} {dest}`")
        return False
    print(f"      restored the previous body; {label} is loaded as it was")
    return True


def reinstall_calendar_agent(row, launchctl, domain, kickstart):
    """Stage the new body, then bootout, bootstrap and verify; restore on failure.

    The write happens FIRST and atomically: a write that cannot happen (a
    read-only plist, a full disk) raises before launchd is touched, so the job
    stays loaded exactly as it was. Only then is the old job booted out."""
    dest, label = row["dest"], row["label"]
    target = f"{domain}/{label}"
    _atomic_write(dest, row["body"])
    _launchctl(launchctl, "bootout", target)   # fails when not loaded; fine
    booted = _launchctl(launchctl, "bootstrap", domain, dest)
    if booted.returncode != 0:
        print(f"      BOOTSTRAP FAILED: {_launchctl_detail(booted)} — restoring the previous body")
        if not _restore_calendar_agent(row, launchctl, domain):
            raise AgentLeftUnloaded(label)
        return False
    shown = _launchctl(launchctl, "print", target)
    if shown.returncode != 0:
        # launchd accepted the NEW definition; it must be booted out before the
        # old file goes back, or launchd keeps running what the disk no longer says.
        print(f"      PRINT FAILED after a successful bootstrap: {_launchctl_detail(shown)}"
              " — booting the new definition out and restoring the previous body")
        _launchctl(launchctl, "bootout", target)
        if not _restore_calendar_agent(row, launchctl, domain):
            raise AgentLeftUnloaded(label)
        return False
    if kickstart:
        kicked = _launchctl(launchctl, "kickstart", target)
        if kicked.returncode != 0:
            print(f"      kickstart failed: {_launchctl_detail(kicked)}")
            return False
    return True


def _option(argv, flag, default):
    if flag in argv:
        index = argv.index(flag)
        if index + 1 < len(argv):
            return argv[index + 1]
    return default


def cmd_reinstall_launchd_calendar(argv):
    apply = "--apply" in argv
    kickstart = "--kickstart" in argv
    templates_dir = _option(argv, "--templates", LAUNCHD_REPO)
    agents_dir = _option(argv, "--launch-agents", LAUNCHD_SRC)
    launchctl = _option(argv, "--launchctl", "/bin/launchctl")
    domain = f"gui/{os.getuid()}"

    rows = launchd_calendar_reinstall_plan(templates_dir, agents_dir)
    failures = [r for r in rows if r["action"] == "fail"]
    todo = [r for r in rows if r["action"] == "reinstall"]
    for row in rows:
        if row["action"] == "skip":
            print(f"  ok    {row['name']}: {row['why']}")
        elif row["action"] == "fail":
            print(f"  FAIL  {row['name']}: {row['why']}")
    done = 0
    not_attempted = []
    for index, row in enumerate(todo):
        print(f"  {'REINSTALL' if apply else 'would reinstall'}  {row['name']}: {row['why']}")
        if not apply:
            continue
        # An ordinary failure is reported and the run moves on: the job was
        # restored and is loaded as before. A FAILED RESTORE is different --
        # that agent is now unloaded, and whatever broke it (launchd refusing
        # every bootstrap, say) would do the same to every agent after it. So
        # the run stops there and names what it did not touch.
        try:
            ok = reinstall_calendar_agent(row, launchctl, domain, kickstart)
        except AgentLeftUnloaded:
            failures.append(row)
            not_attempted = todo[index + 1:]
            print(f"  STOPPING: {row['name']} is unloaded after a failed restore; "
                  "not touching any other agent")
            break
        except Exception as exc:  # noqa: BLE001 - reported per job, run continues
            print(f"      FAILED before launchd was touched: {exc}")
            ok = False
        if ok:
            done += 1
        else:
            failures.append(row)
    for row in not_attempted:
        print(f"  not attempted  {row['name']}")
    left_alone = sum(1 for r in rows if r["action"] == "skip")
    if apply:
        head = f"{done} reinstalled"
    else:
        head = f"{len(todo)} to reinstall (dry run; --apply to act)"
    print(f"reinstall-launchd-calendar: {head}, {left_alone} left alone, "
          f"{len(failures)} failed; kickstart {'on' if kickstart else 'off'}")
    if failures:
        print("  FAILED: " + ", ".join(r["name"] for r in failures))
    if not_attempted:
        print("  NOT ATTEMPTED: " + ", ".join(r["name"] for r in not_attempted))
    return 1 if failures or not_attempted else 0


# LAUNCHD-HANDOFF-SMOKE: an opt-in proof, on a real Mac, of the one thing the
# hermetic tests cannot show -- that the detached one-shot survives launchd
# booting out the job that started it, and then reloads that job. It uses a
# throwaway label (com.carr.handoff-smoke-<random>) whose plist lives in its own
# directory under the hand-off dir, never in ~/Library/LaunchAgents, and it
# touches no other label. Version 1 of the plist runs this file's
# `launchd-handoff-smoke-job`, which hands its own reload off exactly as
# fleet-sync does and then sleeps; version 2 runs /bin/sleep. The smoke boots
# the label out while the job is running (killing the job's process group), then
# waits for the one-shot to load version 2. It always cleans up: bootout of the
# throwaway label, then unlink of every file it created. Without --run it only
# prints what it would do.
def _smoke_plist(label, arguments, out_path):
    return plistlib.dumps({
        "Label": label, "ProgramArguments": arguments, "RunAtLoad": True,
        "StandardOutPath": out_path, "StandardErrorPath": out_path,
    }).decode("utf-8")


def cmd_launchd_handoff_smoke(argv):
    label = f"{SMOKE_LABEL_PREFIX}{secrets.token_hex(4)}"
    domain = f"gui/{os.getuid()}"
    work = os.path.join(SELF_RELOAD_HANDOFF_DIR, label)
    dest = os.path.join(work, f"{label}.plist")
    v2_path = os.path.join(work, "v2.plist")
    job_out = os.path.join(work, "job.out")
    staged = os.path.join(work, f"{label}.plist.staged")
    log = os.path.join(work, f"{label}.plist.log")
    print(f"launchd-handoff-smoke: throwaway label {label}; files under {work}")
    if "--run" not in argv:
        print("  dry run: pass --run to bootstrap the throwaway label for real")
        return 0
    v1 = _smoke_plist(label, [sys.executable, os.path.abspath(__file__),
                              "launchd-handoff-smoke-job", label, dest, v2_path, work], job_out)
    v2 = _smoke_plist(label, ["/bin/sleep", "600"], job_out)
    verdict, why = 1, "did not finish"
    try:
        os.makedirs(work, exist_ok=False)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(v1)
        with open(v2_path, "w", encoding="utf-8") as fh:
            fh.write(v2)
        boot = subprocess.run([LAUNCHCTL_BIN, "bootstrap", domain, dest],
                              capture_output=True, text=True, check=False)
        if boot.returncode != 0:
            why = f"bootstrap of the throwaway label failed: {_launchctl_detail(boot)}"
            return 1
        deadline = time.monotonic() + 60
        while not os.path.exists(staged) and time.monotonic() < deadline:
            time.sleep(0.5)
        if not os.path.exists(staged):
            why = "the job never staged its hand-off"
            return 1
        print("  job is running and has handed off its reload; booting it out")
        subprocess.run([LAUNCHCTL_BIN, "bootout", f"{domain}/{label}"],
                       capture_output=True, check=False)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            logged = read(log) or ""
            if "self-reload" in logged:
                break
            time.sleep(1)
        logged = read(log) or ""
        shown = subprocess.run([LAUNCHCTL_BIN, "print", f"{domain}/{label}"],
                               capture_output=True, text=True, check=False)
        if ("loaded the new definition" in logged and read(dest) == v2
                and shown.returncode == 0 and "/bin/sleep" in shown.stdout):
            verdict, why = 0, "the one-shot outlived the bootout and loaded version 2"
        else:
            why = (f"helper log {logged.strip()!r}; installed is v2: {read(dest) == v2}; "
                   f"print rc {shown.returncode}")
        return verdict
    finally:
        subprocess.run([LAUNCHCTL_BIN, "bootout", f"{domain}/{label}"],
                       capture_output=True, check=False)
        for path in (dest, v2_path, job_out, staged, staged + ".previous", log):
            if os.path.exists(path):
                os.unlink(path)
        if os.path.isdir(work):
            os.rmdir(work)
        print(f"launchd-handoff-smoke: {'PASS' if verdict == 0 else 'FAIL'} — {why}; "
              f"{label} booted out and its files removed")


def smoke_job_refusal(label, dest, v2_path, work, handoff_root=None):
    """Why the smoke job must not act on these arguments, or None.

    The job reloads a label through launchctl, so it is held to exactly the
    throwaway it was built for: a com.carr.handoff-smoke-* label whose files
    all sit in its own directory directly under the hand-off root."""
    root = os.path.realpath(handoff_root or SELF_RELOAD_HANDOFF_DIR)
    if not (isinstance(label, str) and label.startswith(SMOKE_LABEL_PREFIX)
            and re.fullmatch(r"[A-Za-z0-9.-]+", label)
            and len(label) > len(SMOKE_LABEL_PREFIX)):
        return f"label {label!r} is not a {SMOKE_LABEL_PREFIX}* throwaway"
    own = os.path.join(root, label)
    if os.path.realpath(work) != own:
        return f"work directory {work!r} is not {own}"
    if os.path.realpath(dest) != os.path.join(own, f"{label}.plist"):
        return f"plist {dest!r} is not {label}.plist inside {own}"
    if os.path.dirname(os.path.realpath(v2_path)) != own:
        return f"replacement body {v2_path!r} is outside {own}"
    return None


def cmd_launchd_handoff_smoke_job(argv):
    """Runs AS the throwaway launchd job: hand off its own reload, then keep running."""
    global SELF_RELOAD_HANDOFF_DIR
    if len(argv) < 4:
        print("launchd-handoff-smoke-job: REFUSED — expects label dest v2 work")
        return 64
    label, dest, v2_path, work = argv[:4]
    refusal = smoke_job_refusal(label, dest, v2_path, work)
    if refusal:
        print(f"launchd-handoff-smoke-job: REFUSED — {refusal}")
        return 64
    SELF_RELOAD_HANDOFF_DIR = work
    outcome = hand_off_self_reload(os.path.basename(dest), dest, read(v2_path) or "", label)
    if outcome != "deferred":
        return 1
    time.sleep(300)      # still running when the smoke boots the label out
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    apply = "--apply" in sys.argv
    if mode == "--selftest":
        return config_selftest()
    if mode == "check":
        return cmd_check()
    if mode == "pull":
        return cmd_pull(apply)
    if mode == "install-codex-continuity-mcp":
        return cmd_install_codex_continuity_mcp(apply)
    if mode == "verify-codex-continuity-mcp":
        return cmd_install_codex_continuity_mcp(False)
    if mode == "install-codex-continuity":
        return cmd_install_codex_continuity(apply)
    if mode == "remove-codex-continuity":
        return cmd_install_codex_continuity(apply, remove=True)
    if mode == "verify-codex-continuity":
        return cmd_verify_codex_continuity()
    if mode == "install":
        return cmd_install(apply)
    if mode == "install-flash-on-demand":
        import argparse
        from tools import flash_install
        parser = argparse.ArgumentParser(prog=f"config-as-code.py {mode}")
        parser.add_argument("--apply", action="store_true")
        options = parser.parse_args(sys.argv[2:])
        return flash_install.configure(REPO_HERE, apply=options.apply)
    if mode == "check-launchd-main-paths":
        return cmd_check_launchd_main_paths()
    if mode in {"install-progress-board", "verify-progress-board"}:
        import argparse
        parser = argparse.ArgumentParser(prog=f"config-as-code.py {mode}")
        parser.add_argument("--repo", help="repository checkout to run; defaults to canonical checkout")
        parser.add_argument("--apply", action="store_true")
        options = parser.parse_args(sys.argv[2:])
        return cmd_install_progress_board(options.apply if mode == "install-progress-board" else False,
                                          repo=options.repo)
    if mode == "reinstall-launchd-calendar":
        return cmd_reinstall_launchd_calendar(sys.argv[2:])
    if mode == "launchd-handoff-smoke":
        return cmd_launchd_handoff_smoke(sys.argv[2:])
    if mode == "launchd-handoff-smoke-job":
        return cmd_launchd_handoff_smoke_job(sys.argv[2:])
    if mode == "set-role":
        # Writes ~/.config/carr/machine-role.json, then installs in a fresh
        # process: IS_PRIMARY is fixed at import, so this one would still
        # carry the old role. A secondary retires its primary-only jobs.
        role = sys.argv[2] if len(sys.argv) > 2 else ""
        if role not in machine_role.ROLES:
            print("usage: ops/config-as-code.py set-role primary|secondary")
            return 64
        print(f"machine role: {role} ({machine_role.write_marker(role)})")
        return subprocess.run([sys.executable, os.path.abspath(__file__),
                               "install", "--apply"], stdin=subprocess.DEVNULL).returncode
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
