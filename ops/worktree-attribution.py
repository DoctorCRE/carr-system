#!/usr/bin/env python3
"""worktree-attribution.py — name the worktree a foreign file belongs to.

WHY THIS EXISTS, from the incident rather than from theory. This Mac runs one
canonical checkout at ~/carr-system and, today, thirty-five registered worktrees
against it. A single untracked file in the canonical tree — `tools/dictation-rig/
vendor/quill`, which belonged to a session nobody could identify — refused
`git reset --hard` from ALL twelve tracked-clean worktrees at once. The refusal
listed the path and stopped there, so every session that hit it had the same two
choices: guess whether the file was safe to delete, or stop. Deleting another
session's in-flight work is the 2026-08-09 incident, so the honest choice was to
stop, and the Mac stayed blocked.

The council's move (2026-08-23 gates audit, ranked cluster R3) is not another
blocker and not a licence to delete. It is that the refusal must say WHOSE the
file is. "tools/… is dirty" is a fact a session cannot act on. "tools/… is
dirty, and worktree `festive-antonelli-56e0a8` has it too" is a fact that
routes: go ask that session, or read its branch.

WHAT IT WILL AND WILL NOT CLAIM. Ownership here is inferred from evidence on
disk, so every answer carries the evidence that produced it and its strength.
It reports `unknown` rather than guessing, because a confident wrong name is
worse than no name — the 2026-08-09 diagnosis was itself a wrong accusation, and
that cost the hour, not the missing files.

  certain       the path is physically inside a worktree
  dirty+content another worktree has it uncommitted AND byte-identical
  dirty         another worktree has it uncommitted (added, modified, untracked)
  branch        a worktree's branch carries the path and origin/main does not
  unknown       no worktree evidence; the file's mtime is reported instead

"ANOTHER WORKTREE ALSO HAS THIS FILE" IS NOT EVIDENCE, and an early version of
this script treated it as though it were. Every worktree is a full checkout, so
every worktree contains every tracked path; that test named a random peer for
every question and named it confidently. What actually distinguishes a session
working on a file is that the file is UNCOMMITTED in its tree — untracked,
added, or modified. That is the signal, and it is the one asked for here.

Usage:
    ops/worktree-attribution.py <path>...        # one line per path
    ops/worktree-attribution.py --porcelain <path>...   # tab-separated

Exit status is 0 whenever the question was answered, including "unknown". This
is a REPORTER wired into other gates' failure text; a reporter that fails the
command it was explaining would be worse than the silence it replaces.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))
from git_env import scrubbed_env                               # noqa: E402

# EVERY git call here runs with the location variables scrubbed. Git exports
# GIT_DIR to every hook, and this runs inside ops/githooks/pre-push. Inherited,
# GIT_DIR makes `git -C <peer> status` read the PUSHING tree's index and HEAD
# against the PEER's working directory, so every file the push adds reads as
# " D" in whichever peer is listed first. That is how a brand-new
# ops/hook-interpreter-pin-selftest.py was pinned on carr-system-wt-headroom
# ("uncommitted there (D)"), a tree that never had it.


def main_worktree():
    """The canonical checkout, wherever this copy of the script happens to live.

    THIS DISTINCTION IS THE WHOLE POINT. The script runs from inside a worktree
    (that is where sessions work), but the foreign file it is asked about is
    typically dirty in the CANONICAL tree — that is precisely why it is foreign.
    Resolving a relative path against the script's own worktree would look for
    the file in the one tree that provably does not have it. `git rev-parse
    --git-common-dir` names the shared .git directory; its parent is canonical.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO, env=scrubbed_env(), capture_output=True, text=True,
            timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return pathlib.Path(out.stdout.strip()).parent
    except (OSError, subprocess.SubprocessError):
        pass
    return REPO


# Reading a large file only to compare it against a peer copy is not worth it;
# above this size the answer degrades from "content" to "path", which is a
# weaker claim honestly labelled rather than a slow one.
MAX_HASH_BYTES = 2 * 1024 * 1024


def _run(args, cwd=None):
    return subprocess.run(args, cwd=str(cwd or REPO), env=scrubbed_env(),
                          capture_output=True,
                          text=True, timeout=20)


def worktrees_of(repo):
    """(path, branch) for every registered worktree of `repo` except itself.

    Parameterised rather than reading the live machine, so the selftest can
    build a repository it controls. With thirty-five concurrent sessions the
    real worktree list changes underneath a test between two git calls.
    """
    try:
        out = _run(["git", "worktree", "list", "--porcelain"], cwd=repo).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    trees, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur.get("worktree"):
                trees.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["worktree"] = line[len("worktree "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line.startswith("detached"):
            cur["branch"] = "(detached)"
    # Exclude the CANONICAL checkout, not merely the tree this script sits in.
    # This script normally runs from inside a worktree, so comparing against its
    # own location left canonical in the list — and canonical contains every
    # repo-relative path, so it matched "the path is inside that worktree" first
    # and every answer came back naming canonical. That is worse than no answer:
    # it is the anonymous refusal with a confident name bolted on.
    canonical = pathlib.Path(repo).resolve()
    return [t for t in trees
            if pathlib.Path(t["worktree"]).resolve() != canonical]


def worktrees():
    """The live machine's worktrees, canonical excluded."""
    return worktrees_of(main_worktree())


def dirty_paths(tree, paths):
    """Which of `paths` are uncommitted in `tree` — the ownership signal.

    One `git status` per worktree with every path as a pathspec, rather than one
    per (worktree, path): this runs inside a failure message on a machine with
    thirty-five worktrees, and a diagnostic that takes a minute is one nobody
    waits for. --untracked-files=all is required because the incident file was
    untracked; -z because these paths contain spaces.
    """
    if not paths:
        return {}
    try:
        out = subprocess.run(
            ["git", "-C", tree, "status", "--porcelain", "-z",
             "--untracked-files=all", "--"] + list(paths),
            env=scrubbed_env(), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    found, fields = {}, [f for f in out.stdout.split("\0") if f]
    for field in fields:
        # "XY path"; rename entries carry a second NUL-separated field which is
        # simply skipped, since either half is still that worktree's business.
        if len(field) > 3 and field[2] == " ":
            found[field[3:]] = field[:2].strip() or "??"
    return found


def _digest(path):
    try:
        if path.stat().st_size > MAX_HASH_BYTES:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# A checkout whose directory is named this tells the reader nothing; the branch
# is the better handle. /private/tmp/<random>/worktree is a real example on this
# Mac right now.
GENERIC_DIR_NAMES = {"worktree", "repo", "checkout", "carr-system", "src"}


def _session_of(tree_path, branch=None):
    """The session name IS the worktree directory name in this system."""
    name = pathlib.Path(tree_path).name
    if name.lower() in GENERIC_DIR_NAMES:
        return f"{branch or '?'} at {tree_path}"
    return name


def _relativise(raw, root):
    raw = raw.strip().replace("\\", "/")
    if raw.startswith("/"):
        try:
            return str(pathlib.Path(raw).resolve().relative_to(root))
        except ValueError:
            return raw
    return raw.lstrip("./")


def attribute(rel, trees=None, tree_root=None):
    """Evidence-carrying ownership record for one path.

    `rel` may be repo-relative or absolute; an absolute path is used as given,
    which is what lets a caller pass a path a gate has already resolved.
    """
    trees = worktrees() if trees is None else trees
    root = tree_root or main_worktree()
    rel = _relativise(rel, root)
    abs_target = (pathlib.Path(rel) if rel.startswith("/")
                  else root / rel).resolve()
    record = {"path": rel, "owner": None, "session": None,
              "basis": "unknown", "detail": ""}

    # (a) The path is physically inside a worktree. Nothing to infer.
    #
    # `wt_root`, NOT `root`. Reusing the outer name here rebound the canonical
    # tree to whichever worktree the loop last examined, so every path resolved
    # afterwards was resolved against the wrong tree. Same shape as the ci.sh
    # timing bug fixed in 0d9e0efe: a loop variable outliving its loop.
    for t in trees:
        wt_root = pathlib.Path(t["worktree"]).resolve()
        if abs_target == wt_root or wt_root in abs_target.parents:
            record.update(owner=str(wt_root),
                          session=_session_of(wt_root, t.get("branch")),
                          basis="certain",
                          detail="the path is inside that worktree")
            return record

    mine = _digest(abs_target)

    # (b) A peer worktree has it UNCOMMITTED. That is a session working on it.
    for t in trees:
        if "_dirty" not in t:
            # Lazy for importers that call attribute() directly; main() fills
            # this in once for every path, which is the cheap way round.
            t["_dirty"] = dirty_paths(t["worktree"], [rel])
        state = t["_dirty"].get(rel)
        if not state:
            continue
        peer = pathlib.Path(t["worktree"]) / rel
        identical = mine is not None and _digest(peer) == mine
        record.update(
            owner=t["worktree"], session=_session_of(t["worktree"], t.get("branch")),
            basis="dirty+content" if identical else "dirty",
            detail=("byte-identical and uncommitted there" if identical
                    else f"uncommitted there ({state})")
                   + f", on branch {t.get('branch', '?')}")
        return record

    # (d) a worktree's BRANCH carries the path and main does not. This is the
    # case where the peer session already committed and cleaned its tree.
    on_main = _run(["git", "cat-file", "-e", f"origin/main:{rel}"],
                   cwd=root).returncode == 0
    if not on_main:
        for t in trees:
            branch = t.get("branch")
            if not branch or branch == "(detached)":
                continue
            if _run(["git", "cat-file", "-e", f"{branch}:{rel}"],
                    cwd=root).returncode == 0:
                record.update(owner=t["worktree"], session=_session_of(t["worktree"], t.get("branch")),
                              basis="branch",
                              detail=f"committed on {branch}, absent from origin/main")
                return record

    # (e) No evidence. Say so, and give the one fact that is always available.
    try:
        mtime = abs_target.stat().st_mtime
        import datetime
        stamp = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        record["detail"] = f"no worktree claims it; last modified {stamp}"
    except OSError:
        record["detail"] = "no worktree claims it, and it is not on disk here"
    return record


def describe(record):
    """One human line. This text lands inside another gate's refusal."""
    if record["basis"] == "unknown":
        return f"    {record['path']}\n        owner UNKNOWN — {record['detail']}"
    return (f"    {record['path']}\n"
            f"        belongs to session {record['session']} "
            f"({record['basis']}: {record['detail']})")


def main(argv):
    porcelain = "--porcelain" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: ops/worktree-attribution.py [--porcelain] <path>...",
              file=sys.stderr)
        return 64
    try:
        trees, root = worktrees(), main_worktree()
        rels = [_relativise(a, root) for a in paths]
        for t in trees:
            t["_dirty"] = dirty_paths(t["worktree"], rels)
        for rel in paths:
            rec = attribute(rel, trees, root)
            if porcelain:
                print("\t".join([rec["path"], rec["basis"],
                                 rec["session"] or "-", rec["detail"]]))
            else:
                print(describe(rec))
    except Exception as exc:                                   # noqa: BLE001
        # See the module docstring: this explains other failures, so it must
        # never become one. Degrade to silence with a reason.
        print(f"worktree-attribution: could not attribute ({exc})",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
