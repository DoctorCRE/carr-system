#!/usr/bin/env python3
"""one-repo-gate.py — the PreToolUse DENY gate on code written outside the repo.

WHY THIS EXISTS. CARR's CLAUDE.md rev 10: "the code lives in ONE repo:
jbookout/carr-system (~/carr-system locally) ... If a session cannot reach that
repo it must STOP and say so, not improvise a home." That rule is prose, so it
binds only sessions that read it and obey it — which is precisely how it failed.
A cloud session that could not see carr-system filed an entire system audit plus
two specs into an empty scaffold repo and hand-wrote a DECISIONS.md duplicating
what the add-loop verb already does. Nothing refused it. It was found by a human.

THE MECHANISM THE ORIGINATING LOOP NAMED DOES NOT WORK, recorded here so nobody
re-proposes it. DirectoryAdded is a real event and fires on /add-dir and on the
SDK's register_repo_root, but it is POST-ACTION: the directory is already
registered when the hook runs, and exit 2 — like every other non-zero code and
stderr with it — goes to the debug log only. A hook there is an audit line, not
a control. Verified against the published hooks reference, not assumed.

WHAT ACTUALLY ENFORCES IT is the door the failure came through. The shape is not
"a directory got registered", it is "engineering work got WRITTEN somewhere that
is not the repo" — a PreToolUse Write gate, the same door record-home-gate.py and
gate-edit-gate.py already sit on, which does deny.

WHAT IT DENIES, and every test is PATH-STRUCTURAL rather than content-sniffing,
for the reason lint-gate.py's doctrine gives and the originating loop repeats:
under-parsing leaves the hole, over-parsing blocks legitimate work and breeds the
habit of switching the gate off.

  CREATING a file whose extension is code (.py .js .mjs .ts .sql .sh) inside a
  git working tree that is not carr-system.

WHAT IT DELIBERATELY ALLOWS, each for a reason that cost something to learn:

  A. WORKTREES. `run.sh worktree` is the house default for any session that will
     commit (rule 4a53ff82), and a worktree's .git is a FILE pointing back at the
     canonical repo. A prefix comparison would deny every worktree session's
     first Python file, and a gate that fires on the normal path is one that gets
     turned off the same day. Resolved through the git COMMON dir, so a worktree
     anywhere on disk is recognised as the repo it belongs to.
  B. EDITING SOMETHING THAT ALREADY EXISTS. The failure shape is new work landing
     in a foreign home. Patching a file already present in another checkout is
     ordinary, and denying it is the over-parsing above.
  C. ANYTHING OUTSIDE A GIT TREE. Scratchpads, /tmp and the vault are owned by
     other controls or by nothing, and this gate has no business there. The vault
     specifically belongs to record-home-gate.py.
  D. EVERY NON-CODE EXTENSION. Markdown has its own gate and its own rules.
  E. THE OTHER TWO AUTHORIZED CODE HOMES. Decision 1ceee300 replaced the single
     home with three on 2026-09-14 — carr-system, doctorcre-app and
     software-factory — and a tree is one of them only if its ORIGIN REMOTE says
     so. Everything else, including a clone NAMED doctorcre-app that points
     somewhere else, and every other repo of Joe's own, is refused as before.

KNOWN LIMIT, stated rather than discovered later: this covers Write, Edit and
MultiEdit. A `python3 -c` heredoc from Bash writes the same file and is not seen
here — the identical tool-shaped hole loop #287 documents in record-home-gate.py,
whose fix is a real design question about finding write targets inside arbitrary
shell. This gate is not complete coverage and must not be described as such.

FAILS CLOSED ON DENY, OPEN ON ERROR. Exit 2 plus stderr is the deny path, the
same contract guard-unattended.py and record-home-gate.py use: the structured
JSON contract needs exit 0, so on any build that does not parse it an exit 0
reads as ALLOW and the gate would fail open silently. Any INTERNAL error allows
the call, because a gate that wedges a session costs more than the marginal
safety of failing closed on a single-operator machine.
"""

import json
import os
import sys

# Script-relative, never a ~/carr-system literal — the same fix record-home-gate.py
# carries. On a CI runner the checkout is /home/runner/work/carr-system/carr-system,
# outside $HOME, and a literal would silently name a path that does not exist,
# which here would mean the repo never matches itself and the gate denies its own
# tree. CARR_ONE_REPO_ROOT overrides it for the selftest's fixtures.
REPO = os.environ.get("CARR_ONE_REPO_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), ".."))

CODE_EXTENSIONS = (".py", ".js", ".mjs", ".ts", ".sql", ".sh")

# THE AUTHORIZED CODE HOMES, and there are three of them since 2026-09-14.
# Decision 1ceee300-7627-426f-b729-ab339d6984fc superseded the single-code-home
# rule after Gate Zero; AGENTS.md "Authorized code homes and repository
# boundaries" is the projection of it, and STORE doctrine
# doctorcre-v5-astra-integration-review section
# 3bb51d3e-2661-4ea2-a585-053540545b5d is the contract itself.
#
# WHY LEAVING THIS AT ONE COST SOMETHING. Until this list existed the gate
# refused every NEW .ts/.py/.sql file inside a doctorcre-app or software-factory
# clone, so slice V5-UX-S01 authored its files in a scratch directory on
# 2026-09-16 and copied them in with digest verification. That is the gate
# teaching a session to route around it, which its own docstring names as the
# failure mode to avoid — and the workaround defeats the gate for every write,
# not just the authorized ones.
#
# IDENTITY IS THE ORIGIN REMOTE, never the directory name: a clone sits wherever
# somebody put it and is called whatever they called it. The HOST is part of the
# identity too, so the same owner and repo served from a different host is not
# this repo and is refused.
AUTHORIZED_REMOTES = {
    ("github.com", "jbookout/carr-system"),
    ("github.com", "jbookout/doctorcre-app"),
    ("github.com", "jbookout/software-factory"),
}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")

# THE ESCAPE, and it is not optional. This gate installs at USER level, so it sees
# every session on the machine — including Life AI and any ordinary side project
# that is a git repo and has every right to hold a .py file. A control that fires
# on legitimate work and offers no way through is the one the originating loop
# warned about by name: it "breeds the habit of disabling the gate", and a gate
# somebody switched off protects nothing. Same idiom and same shape as
# CARR_ALLOW_CANONICAL_EDIT and CARR_ALLOW_LOOSE_WORK — one variable, deliberate,
# and logged every time so the escape is visible rather than silent.
ESCAPE_VAR = "CARR_ALLOW_FOREIGN_REPO"

DENY = (
    "BLOCKED by the CARR one-repo gate: {path}\n"
    "That is a NEW {ext} file inside {tree}, which is a git working tree and is "
    "not one of CARR's authorized code homes. Code lives in three repos and "
    "nowhere else (decision 1ceee300, AGENTS.md): jbookout/carr-system "
    "(~/carr-system locally), jbookout/doctorcre-app and "
    "jbookout/software-factory. A tree is one of those only if its origin "
    "remote says so.\n"
    "If you cannot reach that repo, STOP and say so rather than improvising a "
    "home: work filed into another repo is not preserved, it is stranded "
    "somewhere nobody reads. That is not hypothetical — an entire system audit "
    "and two specs were lost that way once already.\n"
    "Editing a file that already exists here is allowed; creating new code is not.\n"
    "If this genuinely is not CARR work — a side project, Life AI, an ordinary "
    "repo of your own — set {escape}=1 for that command and it will be allowed "
    "and logged."
)


def log(line):
    """Best effort. A gate that cannot write its log still has to gate."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"one-repo-gate {line}\n")
    except Exception:
        pass


def git_common_dir(start):
    """The canonical .git directory for whatever working tree contains `start`,
    or None when nothing above it is a git tree.

    Walks up rather than shelling out to git, so the gate stays fast enough for
    a PreToolUse hook and cannot be confused by inherited GIT_DIR — which is set
    in every git hook's environment and would otherwise make this answer depend
    on who invoked the session rather than on where the file is going.

    A worktree's .git is a FILE reading `gitdir: /path/to/repo/.git/worktrees/x`.
    Trimming at /worktrees/ yields the shared .git, which is what makes a
    worktree resolve to the repo it belongs to instead of to itself.
    """
    current = os.path.realpath(start)
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.isdir(candidate):
            return os.path.realpath(candidate)
        if os.path.isfile(candidate):
            try:
                with open(candidate) as fh:
                    text = fh.read().strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                gitdir = text.split(":", 1)[1].strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.join(current, gitdir)
                marker = os.sep + "worktrees" + os.sep
                if marker in gitdir:
                    gitdir = gitdir.split(marker)[0]
                return os.path.realpath(gitdir)
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def origin_identity(gitdir):
    """(host, "owner/repo") for the tree's `origin` remote, or None.

    Reads <gitdir>/config directly rather than shelling out to git, for the same
    two reasons git_common_dir walks the tree by hand: a PreToolUse hook must
    stay fast, and an inherited GIT_DIR — set in the environment of every git
    hook — would otherwise make the answer depend on who invoked the session
    instead of on where the file is going.

    Hand-parsed rather than handed to configparser: git indents its keys with a
    tab, and configparser reads an indented line as a continuation of the
    previous value, so the url would come back attached to whatever preceded it.

    Both spellings of one remote normalise to one identity: the scp-like form
    (user, then host, then a colon, then owner and repo) and the full URL form
    carrying a scheme. A remote that is a plain local path has no identity and
    is treated as none.
    """
    try:
        with open(os.path.join(gitdir, "config")) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None

    url, in_origin = None, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = (stripped.replace('"', "").replace(" ", "").lower()
                         == "[remoteorigin]")
            continue
        if in_origin and stripped.lower().startswith("url"):
            _, _, value = stripped.partition("=")
            url = value.strip()
            break
    if not url:
        return None

    rest = url
    if "://" in rest:                        # a scheme, then host, then path
        rest = rest.split("://", 1)[1]
        if "@" in rest.split("/", 1)[0]:
            rest = rest.split("@", 1)[1]
        host, _, path = rest.partition("/")
    elif ":" in rest:                        # scp-like: the host precedes ':'
        hostpart, _, path = rest.partition(":")
        host = hostpart.split("@", 1)[-1]
    else:
        return None                          # a local path — no remote identity

    host = host.split(":", 1)[0].lower()     # drop any port
    parts = [seg for seg in path.strip("/").split("/") if seg]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return (host, f"{owner}/{repo}".lower())


def check(tool_input, cwd):
    """The refusal reason, or None to allow."""
    raw = tool_input.get("file_path") or tool_input.get("filePath") or ""
    if not raw:
        return None
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    path = os.path.abspath(path)

    extension = os.path.splitext(path)[1].lower()
    if extension not in CODE_EXTENSIONS:
        return None
    if os.path.exists(path):
        return None                      # editing what exists is allowed (B)

    tree = git_common_dir(os.path.dirname(path))
    if tree is None:
        return None                      # not a git tree at all (C)
    home = git_common_dir(REPO) or os.path.realpath(os.path.join(REPO, ".git"))
    if tree == home:
        return None                      # the repo, worktrees included (A)

    identity = origin_identity(tree)
    if identity and identity in AUTHORIZED_REMOTES:
        log(f"ALLOW(authorized-home {identity[1]}) {path}")
        return None                      # a sibling code home (E)

    if os.environ.get(ESCAPE_VAR) == "1":
        log(f"ALLOW(escape-hatch) {path}")
        return None

    return DENY.format(path=path, ext=extension,
                       tree=os.path.dirname(tree) or tree, escape=ESCAPE_VAR)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                                   # fail OPEN
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)
    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        if tool not in ("Write", "Edit", "MultiEdit") or not isinstance(tool_input, dict):
            sys.exit(0)
        reason = check(tool_input, payload.get("cwd"))
        if reason:
            log(f"DENY {tool} :: {reason.splitlines()[0][:200]}")
            print(reason, file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:                                   # fail OPEN
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
