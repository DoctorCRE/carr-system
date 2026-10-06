#!/usr/bin/env python3
"""inherited-from-main.py — is this red the branch's fault, or main's?

WHAT HAPPENED ON 2026-08-22. Between 01:38 and 02:17 UTC, six unrelated
branches — a tour packet, a cost gate, a retrieval fix, a CI experiment,
build-discipline and two others — all failed the SAME gates-class selftest.
Not one of them had touched it. Something that merged to main broke that
selftest, and because the required check builds refs/pull/N/merge (the PR head
merged into CURRENT main), main's break was inside every one of those runs.
Six sessions then spent six full CI runs and six sessions' worth of diagnostic
tokens reading a stack trace for a defect none of them wrote.

That is the failure this file exists to stop. It is the 2026-08-23 CI-failures
council's layer 1, the one both chairs marked SAFE and told us to ship
regardless of the other two: SuperGrok, "Ship this regardless of layers 2-3";
Codex, the same move as the attribution backstop.

WHAT IT DOES NOT DO: it does not make the run green. The run stays red and the
exit code stays nonzero, deliberately and on both chairs' explicit instruction
— a victim of a broken main must not merge onto that broken main. Codex put the
kill criterion on this exact point: "Kill any implementation under which a
skipped or neutral check accidentally satisfies branch protection." What
changes is the TIME (seconds instead of six minutes), the billed minutes, and
above all the victim session's tokens, because the run now says whose defect it
is looking at.

THE VERDICT COMES FROM RE-RUNNING THE CHECK AT THE MERGE BASE, not from the
diff. This is the whole safety argument and it is worth being precise about,
because the way this mechanism could do damage is by telling a session "not
your fault" about a defect that IS their fault.

  - The merge base contains NONE of this branch's changes. If the same check
    fails there, the branch cannot be the cause. That is a proof, not a heuristic.
  - The diff is only a CHEAP PRE-FILTER, and it is deliberately weak: it asks
    whether the change touched the check's own file, and skips the expensive
    re-run when it did. A branch can break a check without touching the check
    (change hooks/record-home-gate.py, watch its selftest go red), and in that
    case the pre-filter says "not touched", the re-run happens, the check PASSES
    at the merge base, and this file reports NOT INHERITED. Correct answer,
    reached by the re-run rather than by the diff. ops/inherited-from-main-selftest.py
    seeds exactly that case and fails if this file ever answers it wrong.

EVERY UNCERTAINTY RESOLVES TO "CANNOT TELL", never to "inherited". No merge
base, a shallow clone, a check that did not exist at the merge base, a check
ops/ci.sh only started COLLECTING on this branch (see newly_collected: it fails
at the base, but main has never run it, so the failure is not main's), a check
that declined to run there (exit 78), a command that could not be executed
(126/127), a timeout, a worktree that would not materialise — all of them exit
2, and ops/ci.sh carries on with its normal full run. The failure mode of this
file is "you paid for a full run you could have skipped", which is the state we
are already in, and never "you were told to stop looking at a real defect".

Exit 0  INHERITED  — the same check fails at the merge base; this branch is a victim
Exit 1  NOT INHERITED — it passes at the merge base; this branch owns the failure
Exit 2  CANNOT TELL — refused to answer; the caller must behave as it did before

  ops/inherited-from-main.py --check <name> -- <cmd> [args...]
  ops/inherited-from-main.py --explain
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile

INHERITED, NOT_INHERITED, CANNOT_TELL = 0, 1, 2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import scrubbed_env  # noqa: E402

# WHY scrubbed_env AND NOT A LIST OF OUR OWN. git reads GIT_DIR before it reads
# the working directory, and git EXPORTS GIT_DIR into every hook — so a
# subprocess started with cwd=<the merge-base tree> and GIT_DIR still set acts on
# whatever repository GIT_DIR names, not on the tree we materialised. That is the
# 2026-08-13 finding, and ops/git_env.py exists precisely so this list is not
# copied into a fifth file and left to drift. It matters twice here: for our own
# git calls, and for the check we re-run at the merge base, which may shell out
# to git itself.
#
# scrubbed_env rather than fixture_env: the merge-base tree is a REAL worktree of
# a real repository, and the check we run in it may legitimately want the
# caller's git identity and global config, exactly as it would in a normal run.


def _clean_env() -> dict:
    return scrubbed_env()


def git(repo: str, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", repo, *args],
                       capture_output=True, text=True, env=_clean_env())
    return p.returncode, (p.stdout or "").strip()


def refuse(reason: str) -> int:
    """Say why we are not answering. A silent decline is how a mechanism rots."""
    print(f"inherited-from-main: cannot tell — {reason}")
    return CANNOT_TELL


def resolve_base(repo: str) -> str | None:
    """The main-side ref to measure against, most trustworthy first."""
    for ref in (os.environ.get("CARR_INHERIT_BASE"), "origin/main", "main"):
        if ref and git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")[0] == 0:
            return ref
    return None


def check_file_of(cmd: list[str], repo: str) -> str | None:
    """The repo-relative path of the check being run, if one of the args is one.

    Used ONLY for the pre-filter and for the exists-at-the-merge-base test.
    Deliberately tolerant: an argument list we cannot read a path out of means
    no pre-filter, which costs one re-run and cannot produce a wrong verdict.
    """
    for arg in cmd:
        cand = os.path.normpath(os.path.join(repo, arg))
        if os.path.isfile(cand) and cand.startswith(repo + os.sep):
            return os.path.relpath(cand, repo)
    return None


def collection_globs(ci_sh: str) -> list[str]:
    """The selftest collection patterns, read out of ops/ci.sh's own source.

    Deliberately derived rather than restated, for the reason
    ops/ci-selftest.py's collection invariant already gives: a copy of the globs
    here would be a second contract to keep in sync, which is the same failure
    one level up. Shell variables (`$eligible`) name no path and are dropped.
    """
    patterns: list[str] = []
    for match in re.finditer(r"for t in ([^;]+); do", ci_sh):
        patterns += [p for p in match.group(1).split() if "$" not in p]
    return patterns


def glob_collects(pattern: str, rel: str) -> bool:
    """Shell-glob semantics: `*` matches inside a path segment, never across /.

    fnmatch alone would translate `*` to `.*` and let `tools/test_*.py` swallow
    `tools/room-bridge/test_x.py` — the exact file the base does NOT collect,
    and the one case this has to get right.
    """
    parts, path = pattern.split("/"), rel.split("/")
    return len(parts) == len(path) and all(
        fnmatch.fnmatchcase(segment, part) for part, segment in zip(parts, path))


def newly_collected(repo: str, mb: str, rel: str) -> str | None:
    """Refusal text if THIS BRANCH is what makes ops/ci.sh run <rel>, else None.

    2026-09-10. A branch that widened ci.sh's collection globs to reach
    tools/<subdir>/test_*.py was told, run after run, "INHERITED FROM MAIN —
    wait for main to go green" about a suite main's own globs had never
    collected once. Every word of that was unactionable: main was green, the
    canary had nothing to name, no merge freeze was going to lift, and the
    branch could not merge. The re-run was not wrong about the exit code — the
    suite really did exit 1 at the merge base — it was wrong about whose break
    that is. A check nothing on main runs cannot be a break inherited FROM main,
    and the branch that starts running it is the only place it can be diagnosed.

    This is the guard above it one step later: a check this branch ADDED cannot
    have failed at the merge base, and a check this branch newly COLLECTED is
    that same case with the file already sitting in the tree.

    DELIBERATELY NARROW, because the cost of over-reaching here is the mechanism
    quietly switching itself off. It refuses only when NO glob at the merge base
    collects the check AND one in this tree does — that is, only when the
    branch's own change to ci.sh is what makes the check run at all. Everything
    ci.sh invokes by name rather than by glob (hooks/gate-integrity.py, the
    inventory checks) is collected by neither side and keeps its verdict, a
    check both sides collect keeps its verdict, and a ci.sh that cannot be read
    on either side yields no opinion rather than a refusal.
    """
    rc, base_ci = git(repo, "show", f"{mb}:ops/ci.sh")
    if rc != 0:
        return None
    try:
        with open(os.path.join(repo, "ops", "ci.sh"), encoding="utf-8") as handle:
            head_ci = handle.read()
    except OSError:
        return None
    base_globs, head_globs = collection_globs(base_ci), collection_globs(head_ci)
    if not base_globs or not head_globs:
        return None
    if any(glob_collects(p, rel) for p in base_globs):
        return None
    if not any(glob_collects(p, rel) for p in head_globs):
        return None
    return (f"this branch is what makes {rel} run — no collection glob in "
            f"ops/ci.sh at the merge base matches it, so main has never run it "
            f"once and a failure there is not main's to fix — diagnose it here")


def missing_replay_prerequisite(repo: str, tree: str, output: str) -> str | None:
    """Name a caller-only runtime path that made detached replay non-equivalent.

    A Git worktree materialises tracked source, not ignored installed runtimes
    such as node_modules. If a failed replay names a path absent from the
    detached tree but present and untracked in the caller checkout, the replay
    did not reproduce the caller's environment. That is uncertainty, never
    evidence that the base is broken.
    """
    roots = ((tree, repo), (os.path.realpath(tree), os.path.realpath(repo)))
    seen: set[str] = set()
    for replay_root, caller_root in roots:
        pattern = re.compile(re.escape(replay_root) + r"/[^\s'\"():,\[\]]+")
        for match in pattern.finditer(output):
            replay_path = match.group(0).rstrip(".;")
            if replay_path in seen:
                continue
            seen.add(replay_path)
            if os.path.lexists(replay_path):
                continue
            rel = os.path.relpath(replay_path, replay_root)
            if rel == os.pardir or rel.startswith(os.pardir + os.sep):
                continue
            caller_path = os.path.join(caller_root, rel)
            if not os.path.lexists(caller_path):
                continue
            if git(repo, "ls-files", "--error-unmatch", "--", rel)[0] == 0:
                continue
            return rel
    return None


# Directories `npm install` / `python -m venv` populate that a plain
# `git worktree add` never brings along, because they are ignored rather than
# tracked (see .gitignore: `.venv`, `node_modules/`). Without this, EVERY
# node- or python-dependent check in ops/ci.sh fails at the merge base for a
# reason that has nothing to do with main, and missing_replay_prerequisite()
# above only catches the cases where the failing output happens to print an
# absolute path under the tree — which a bare `require('pkg')` specifier does
# not (Node's MODULE_NOT_FOUND names the missing package, not a path; the
# only tree-rooted path in that message is the require-stack ENTRY, i.e.
# where the failing require was called FROM, not what is missing). PR #1195 /
# defect 71c7c3f2 is exactly that: ci-selftest.py's "ci.yml parses" check
# shells out to `node -e "require('js-yaml')..."`, the merge-base worktree
# had no mcp-server/node_modules, and the resulting MODULE_NOT_FOUND was
# reported INHERITED FROM MAIN even though main's own gate had passed on its
# own PR. Symlinking the caller's installed dependencies in BEFORE the replay
# fixes the common case outright, rather than merely detecting it after the
# fact.
INSTALL_DIRS = (
    ".venv",
    "mcp-server/node_modules",
    "control-room/node_modules",
    "workspace/node_modules",
)


def link_install_dirs(repo: str, tree: str) -> None:
    """Symlink the caller's installed runtimes into the detached merge-base tree.

    Best-effort and silent on failure: a symlink that cannot be made leaves the
    replay exactly as unreproducible as it was before this function existed,
    and environment_class_signal() below is the backstop for whatever this
    does not cover — it must never be the reason a verdict comes out wrong.
    Only directories that already exist in the caller's checkout are linked,
    and only into a destination that is not already there (a tracked
    `mcp-server/node_modules` would never occur, but a re-run must not clobber
    anything the worktree checkout itself materialised).
    """
    for rel in INSTALL_DIRS:
        src = os.path.join(repo, rel)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(tree, rel)
        if os.path.lexists(dst):
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(os.path.realpath(src), dst)
        except OSError:
            continue


# Text signatures of "the tool this check needed is not here", independent of
# whether the message happens to spell out a tree-rooted path.
# missing_replay_prerequisite() is the path-based, precise detector; this is
# the pattern-based backstop for the messages that name a missing PACKAGE or
# COMMAND instead of a missing PATH. Kept deliberately small and specific —
# broad patterns like a bare "No such file or directory" would swallow real
# defects (a check that legitimately asserts a file must exist), so every
# entry here is a signature that names the runtime itself, never the
# subject under test.
_ENVIRONMENT_SIGNATURES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bMODULE_NOT_FOUND\b"),
     "a missing Node module (MODULE_NOT_FOUND)"),
    (re.compile(r"Cannot find module '([^']+)'"),
     "a missing Node module"),
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
     "a missing Python module"),
    (re.compile(r"\bcommand not found\b"),
     "a missing command (command not found)"),
    (re.compile(r"No such file or directory.*\.venv/bin/(python3?|pip3?)"),
     "a missing virtualenv (.venv/bin not present)"),
    (re.compile(r"\.venv/bin/(python3?|pip3?): No such file or directory"),
     "a missing virtualenv (.venv/bin not present)"),
)


def environment_class_signal(output: str) -> str | None:
    """Name the environment-class failure in a base replay, if the output is one.

    A base re-run that fails because a dependency was never installed in the
    detached tree is not evidence that main is broken — it is evidence that
    `git worktree add` does not bring along ignored install directories.
    link_install_dirs() fixes the common case before the check ever runs;
    this is what catches what that missed, so the failure still never gets
    read as INHERITED FROM MAIN. Matches by MESSAGE SIGNATURE rather than by
    path, because Node's own MODULE_NOT_FOUND text names the missing package
    ('js-yaml'), not a filesystem path under the tree — see the INSTALL_DIRS
    comment above for the incident (PR #1195 / defect 71c7c3f2) this exists
    to stop from recurring.
    """
    for pattern, label in _ENVIRONMENT_SIGNATURES:
        m = pattern.search(output)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if groups:
            return f"{label}: {groups[0]}"
        return label
    return None


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--check", default="", help="the failing check's name, for the message")
    # 120s, not "however long the check takes". ops/ci.sh relies on this
    # default, and the point of the whole mechanism is failing in seconds
    # rather than six minutes; a merge-base re-run that outruns the budget
    # resolves to cannot-tell and the normal full run proceeds.
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.help or a.explain:
        print(__doc__)
        return CANNOT_TELL

    cmd = [x for x in a.cmd if x != "--"]
    if not cmd:
        return refuse("no command to re-run was given")
    if os.environ.get("CARR_CI_NO_INHERIT_CHECK") == "1":
        return refuse("CARR_CI_NO_INHERIT_CHECK=1 — the short-circuit is switched off")

    rc, repo = git(os.getcwd(), "rev-parse", "--show-toplevel")
    if rc != 0 or not repo:
        return refuse("not inside a git repository")

    if git(repo, "rev-parse", "--is-shallow-repository")[1] == "true":
        return refuse("shallow clone — there is no merge base to compare against")

    base = resolve_base(repo)
    if not base:
        return refuse("no origin/main or main to compare against")

    rc, mb = git(repo, "merge-base", "HEAD", base)
    if rc != 0 or not mb:
        return refuse(f"HEAD and {base} share no merge base")

    rc, head = git(repo, "rev-parse", "HEAD")
    if rc != 0:
        return refuse("could not resolve HEAD")
    if head == mb:
        # Nothing sits between this tree and the base, so there is no "branch"
        # to exonerate. On main itself this is the normal case, and main's own
        # verdict is main's own — the canary names it, not this file.
        return refuse("HEAD is the merge base — this run IS the base; nothing to attribute")

    name = a.check or (check_file_of(cmd, repo) or " ".join(cmd))
    rel = check_file_of(cmd, repo)

    # PRE-FILTER (an optimisation, never the verdict — see the header).
    rc, diff = git(repo, "diff", "--name-only", mb, "HEAD")
    if rc != 0:
        return refuse(f"could not diff {mb[:8]}..HEAD")
    changed = set(diff.split("\n")) if diff else set()
    if rel and rel in changed:
        return refuse(f"the change touches {rel} — diagnose it here")

    # A check this branch ADDED cannot have failed at the merge base; without
    # this, python would exit 2 on the missing file and that would read as a
    # failure there, which is the exact misattribution this file must not make.
    if rel and git(repo, "cat-file", "-e", f"{mb}:{rel}")[0] != 0:
        return refuse(f"{rel} does not exist at the merge base — this branch added it")

    # A check the merge base never RAN cannot be a break inherited from main,
    # even when it does fail there. See newly_collected() for the case.
    if rel:
        never_ran_on_main = newly_collected(repo, mb, rel)
        if never_ran_on_main:
            return refuse(never_ran_on_main)

    tmp = tempfile.mkdtemp(prefix=f"carr-mergebase-{os.getpid()}-")
    tree = os.path.join(tmp, "base")
    try:
        rc, out = git(repo, "worktree", "add", "--detach", "--quiet", tree, mb)
        if rc != 0:
            return refuse(f"could not materialise the merge base: {out[:160]}")
        link_install_dirs(repo, tree)
        try:
            p = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                               env=_clean_env(), timeout=a.timeout)
        except subprocess.TimeoutExpired:
            return refuse(f"{name} did not finish within {a.timeout}s at the merge base")
        except OSError as exc:
            return refuse(f"could not execute {name} at the merge base: {exc}")

        if p.returncode == 0:
            print(f"inherited-from-main: NOT inherited — {name} passes at the merge base "
                  f"({mb[:8]}), so this branch introduced the failure.")
            return NOT_INHERITED
        if p.returncode == 78:
            return refuse(f"{name} declined to run at the merge base (exit 78, not configured)")
        if p.returncode in (126, 127):
            return refuse(f"{name} could not be executed at the merge base (exit {p.returncode})")

        replay_output = (p.stdout or "") + (p.stderr or "")
        prerequisite = missing_replay_prerequisite(repo, tree, replay_output)
        if prerequisite:
            return refuse(
                "runtime prerequisite is absent from the merge-base worktree "
                f"but present only in the caller checkout: {prerequisite}"
            )

        # THE PATTERN-BASED BACKSTOP. link_install_dirs() already symlinked in
        # whatever install directories the caller checkout had, and the guard
        # above catches whatever still names a tree-rooted path. This is for
        # what neither reaches: a message that names the missing PACKAGE or
        # COMMAND rather than a path (Node's MODULE_NOT_FOUND, a Python
        # ModuleNotFoundError, a shell "command not found", an absent
        # .venv/bin). Attribution unavailable, never inherited and never the
        # branch's fault — see environment_class_signal()'s docstring.
        env_signal = environment_class_signal(replay_output)
        if env_signal:
            return refuse(
                "the merge-base replay failed on an environment-class signature, "
                f"not a code defect — attribution unavailable: {env_signal}"
            )

        # THE ANSWER. It fails on a tree that contains none of this branch.
        print(f"INHERITED FROM MAIN — do not diagnose this branch; "
              f"the break is {name} on main")
        print(f"  {name} exits {p.returncode} at the merge base {mb[:12]} "
              f"({base}), which carries none of this branch's {len(changed)} changed file(s).")
        print(f"  THE MOVE: nothing on this branch. Do not read the trace below as yours. "
              f"Wait for main to go green — the main canary names the break and the "
              f"merge freeze holds until it is fixed — then re-run this check.")
        print(f"  If you mean to be the one who fixes it, fix it ON MAIN in its own change; "
              f"a fix smuggled into this branch merges a second unrelated thing.")
        # Wide enough to carry the failing assertion itself, not just the runner's
        # closing summary: at six lines the FAIL line of a suite that fails only on
        # the hosted runner never once reached the log, and the case below it could
        # not be diagnosed from any run.
        tail = replay_output.strip().splitlines()[-60:]
        for line in tail:
            print(f"    | {line}")
        return INHERITED
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", tree],
                       capture_output=True, text=True, env=_clean_env())
        subprocess.run(["git", "-C", repo, "worktree", "prune"],
                       capture_output=True, text=True, env=_clean_env())
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never let a crash here become an "inherited" verdict
        print(f"inherited-from-main: cannot tell — {exc!r}")
        sys.exit(CANNOT_TELL)
