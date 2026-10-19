#!/usr/bin/env python3
"""Fail-closed eligibility check for ``bin/fleet-sync.sh``.

The canonical checkout deliberately carries the reproducible Quill patch after
``build-quill.sh``.  The health classifier proves that exact dirt byte-by-byte;
this small adapter lets fleet-sync use the same proof without turning the
submodule path into an ignore rule.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile

from health_submodule import classify_loose_status


QUILL = "tools/dictation-rig/vendor/quill"
QUILL_PATCHES = "tools/dictation-rig/patches"
SUBMODULE_CONFIG = ".gitmodules"


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=False)


def _git_with_index(repo: str, index: str,
                    *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index
    return subprocess.run(["git", *args], cwd=repo, env=env,
                          capture_output=True, text=True, check=False)


def submodule_tree_is_exact_patch(repo: str) -> tuple[bool, str]:
    """Prove Quill's tracked worktree is exactly HEAD plus canonical patches.

    A temporary index preserves Git's own path, content, multiplicity, binary,
    and executable-mode semantics.  Updating that index with ``git add -u``
    intentionally excludes untracked scratch, matching fleet-sync's established
    contract.  The temporary directory is removed on every return and error.
    """
    tree_row = _git(repo, "ls-tree", "HEAD", "--", QUILL)
    if tree_row.returncode != 0 or not tree_row.stdout.strip():
        return False, "could not read the recorded Quill gitlink"
    try:
        metadata, recorded_path = tree_row.stdout.strip().split("\t", 1)
        mode, object_type, recorded_head = metadata.split()
    except ValueError:
        return False, "malformed recorded Quill gitlink"
    if (mode, object_type, recorded_path) != ("160000", "commit", QUILL):
        return False, "recorded Quill entry is not the expected submodule gitlink"

    quill = os.path.join(repo, QUILL)
    live_head = _git(quill, "rev-parse", "HEAD")
    if live_head.returncode != 0:
        return False, "could not read checked-out Quill HEAD"
    if live_head.stdout.strip() != recorded_head:
        return False, "checked-out Quill HEAD differs from the superproject gitlink"

    patch_dir = os.path.join(repo, QUILL_PATCHES)
    patches = sorted(glob.glob(os.path.join(patch_dir, "*.patch")))
    if not patches:
        return False, "no canonical Quill patches found"

    with tempfile.TemporaryDirectory(prefix="carr-fleet-sync-index-") as temp_dir:
        index = os.path.join(temp_dir, "index")
        read_tree = _git_with_index(quill, index, "read-tree", "HEAD")
        if read_tree.returncode != 0:
            return False, "could not seed temporary Quill index"
        for patch_path in patches:
            applied = _git_with_index(quill, index, "apply", "--cached", "--",
                                      patch_path)
            if applied.returncode != 0:
                return False, "canonical Quill patch set does not apply to recorded HEAD"
        expected = _git_with_index(quill, index, "write-tree")
        if expected.returncode != 0 or not expected.stdout.strip():
            return False, "could not materialize expected patched Quill tree"

        # Rebuild the live index from RECORDED HEAD, not from the expected tree.
        # Otherwise a HEAD-tracked path deleted by a patch becomes untracked in
        # the expected index and its resurrection in the worktree is invisible
        # to `git add -u`.  Starting from HEAD keeps every deletion/rename source
        # visible.  Only paths the expected patch tree ADDS are then admitted;
        # unrelated untracked scratch remains intentionally outside the gate.
        live_base = _git_with_index(quill, index, "read-tree", "HEAD")
        if live_base.returncode != 0:
            return False, "could not rebuild temporary live Quill index"
        live_tracked = _git_with_index(quill, index, "add", "-u", "--", ".")
        if live_tracked.returncode != 0:
            return False, "could not stage live HEAD-tracked Quill paths"

        additions = _git_with_index(
            quill, index, "diff", "--name-only", "-z", "--no-renames",
            "--diff-filter=A", "HEAD", expected.stdout.strip(), "--")
        if additions.returncode != 0:
            return False, "could not enumerate canonical patch-added paths"
        raw_additions = additions.stdout
        if raw_additions and not raw_additions.endswith("\0"):
            return False, "malformed canonical patch-added path list"
        added_paths = raw_additions[:-1].split("\0") if raw_additions else []
        if any(not path for path in added_paths):
            return False, "malformed empty canonical patch-added path"
        if added_paths:
            live_added = _git_with_index(quill, index, "add", "--", *added_paths)
            if live_added.returncode != 0:
                return False, "could not stage canonical patch-added Quill paths"

        live_tree = _git_with_index(quill, index, "write-tree")
        if live_tree.returncode != 0 or not live_tree.stdout.strip():
            return False, "could not materialize live tracked Quill tree"
        if live_tree.stdout.strip() == expected.stdout.strip():
            return True, "tracked Quill tree exactly equals recorded HEAD plus canonical patches"
        return False, "tracked Quill tree differs from recorded HEAD plus canonical patches"


def landed_residue(repo: str, incoming: str, paths: list[str]) -> list[str]:
    """Diagnose unstaged bytes found upstream without changing files or index."""
    landed = []
    for path in paths:
        full = os.path.join(repo, path)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        if _git(repo, "diff", "--cached", "--quiet", "HEAD", "--", path).returncode:
            continue
        live = _git(repo, "hash-object", "--", path)
        commits = _git(repo, "rev-list", f"HEAD..{incoming}", "--", path)
        if live.returncode or commits.returncode:
            continue
        for commit in commits.stdout.split():
            blob = _git(repo, "rev-parse", "--verify", "--quiet", f"{commit}:{path}")
            if blob.returncode == 0 and blob.stdout.strip() == live.stdout.strip():
                landed.append(path)
                break
    return landed


def eligible_for_fast_forward(repo: str, incoming: str) -> tuple[bool, str]:
    """Return whether a checkout may fast-forward without discarding work.

    The sole permitted tracked state is the exact Quill patch dirt classified by
    ``health_submodule``.  Even that state is safe only when the incoming range
    changes neither the recorded Quill gitlink, the patch source that explains
    the dirt, nor the repository's submodule configuration.  Every uncertainty
    is a refusal.
    """
    # Fence identity inputs even for a currently clean checkout.  A changed
    # gitlink, patch source, or .gitmodules entry needs a reviewed submodule
    # transition; fleet-sync cannot prove that a blind fast-forward preserves
    # the installed Quill build.
    changed = _git(repo, "diff", "--quiet", "HEAD", incoming, "--", QUILL,
                   QUILL_PATCHES, SUBMODULE_CONFIG)
    if changed.returncode == 1:
        return (False,
                "incoming update changes Quill gitlink, tracked patches, or .gitmodules")
    if changed.returncode != 0:
        return False, "could not compare incoming Quill identity inputs"

    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if status.returncode:
        return False, "could not read tracked checkout status"
    rows = status.stdout.splitlines()
    if not rows:
        return True, "no tracked local changes"

    buckets = classify_loose_status(repo, rows)
    if buckets["actionable_tracked"]:
        landed = landed_residue(repo, incoming, buckets["actionable_tracked"])
        landed_note = "; landed upstream: " + ", ".join(landed) if landed else ""
        return False, "actionable local changes: " + ", ".join(
            buckets["actionable_tracked"]) + "; expected patched submodules: " + ", ".join(
                buckets["expected_patched_submodules"]) + landed_note
    expected = buckets["expected_patched_submodules"]
    if expected != [QUILL]:
        return False, "unverifiable tracked submodule state: " + ", ".join(expected)

    exact, exact_reason = submodule_tree_is_exact_patch(repo)
    if not exact:
        return False, exact_reason
    return True, exact_reason


def fast_forward(repo: str, incoming: str) -> tuple[int, str]:
    """Exclude canonical index writers until the checked merge is published."""
    located = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")
    if located.returncode:
        return 1, "could not locate the canonical index"
    index = located.stdout.strip()
    lock = index + ".lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return 78, f"another Git writer holds {lock}; refusing to fast-forward"
    os.close(descriptor)
    try:
        with tempfile.TemporaryDirectory(prefix="fleet-sync-index-", dir=os.path.dirname(index)) as tmp:
            private = os.path.join(tmp, "index")
            shutil.copyfile(index, private)
            allowed, reason = eligible_for_fast_forward(repo, incoming)
            if not allowed:
                return 78, reason
            # Keep the canonical lock present until publication. Git takes its
            # own lock on the private index, never releasing the canonical one.
            merged = _git_with_index(repo, private, "merge", "--ff-only",
                                     "--no-overwrite-ignore", incoming)
            if merged.returncode:
                allowed, reason = eligible_for_fast_forward(repo, incoming)
                return (1 if allowed else 78,
                        merged.stdout + merged.stderr + ("\n" + reason if not allowed else ""))
            os.replace(private, index)
            return 0, merged.stdout + merged.stderr
    finally:
        os.unlink(lock)


def main() -> int:
    args = sys.argv[1:]
    merge = args[:1] == ["--fast-forward"]
    if merge:
        args = args[1:]
    if len(args) != 2:
        print("usage: fleet_sync_safety.py [--fast-forward] REPO INCOMING", file=sys.stderr)
        return 2
    repo, incoming = os.path.abspath(args[0]), args[1]
    if merge:
        code, reason = fast_forward(repo, incoming)
    else:
        allowed, reason = eligible_for_fast_forward(repo, incoming)
        code = 0 if allowed else 78
    print(reason)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
