"""
health_submodule.py — decide whether a vendored submodule's dirt is the tracked
patches doing their job, or a real uncommitted edit.

WHY THIS IS A MODULE AND NOT THREE LINES INSIDE health-check.py: it needs a
selftest (ops/submodule-patch-dirt-selftest.py), health-check.py is a script
with a hyphen in its name, and the case that matters — a hand-edit on top of the
patch still alarming — cannot be proven by reading.

THE RULE. `bin/build-quill.sh` applies tracked patches from a sibling
`patches/` directory onto a vendored third-party checkout at a detached HEAD,
so after any build that submodule is permanently modified. That is expected and
permanent. A hand-edit inside the same submodule is NOT expected and must still
be reported. The two are told apart by comparing the live diff's changed lines
against the union of the patches' changed lines:

    equal      -> expected; the build did it
    anything else -> loose work; keep it on the clock

Deliberately NOT an allowlist of paths. Silencing by path would hide the
hand-edit exactly as well as it hides the patch, which turns a noisy-but-honest
row into a quiet dishonest one.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from datetime import datetime



def changed_lines(diff_text: str) -> set[tuple[str, str]]:
    """The set of (sign, content) pairs a diff actually changes.

    File headers, hunk headers and index lines are dropped: two diffs of the
    same change differ in blob hashes and line offsets, and comparing those
    would make every rebuild look like a new edit.
    """
    out: set[tuple[str, str]] = set()
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "diff ", "index ", "@@", "new file",
                            "deleted file", "similarity ", "rename ", "old mode",
                            "new mode", "Binary files")):
            continue
        if line.startswith(("+", "-")):
            # The content after the diff marker is evidence.  Do not strip it:
            # leading or trailing whitespace is a real source edit, and making
            # it disappear here would let that edit impersonate a tracked
            # patch.  splitlines() removes only the physical line ending.
            out.add((line[0], line[1:]))
    return out


def submodule_dirt_is_tracked_patch(diff_text: str, patches_dir: str) -> bool:
    """True only when the submodule's dirt is exactly what the patches apply.

    False when there is no dirt, no patches directory, no patches in it, or any
    changed line the patches do not account for — including a line that
    CONTRADICTS a patched one, which is what a hand-edit inside a patched hunk
    looks like.
    """
    dirt = changed_lines(diff_text)
    if not dirt:
        return False
    if not os.path.isdir(patches_dir):
        return False

    expected: set[tuple[str, str]] = set()
    found_any = False
    for path in sorted(glob.glob(os.path.join(patches_dir, "*.patch"))):
        found_any = True
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                expected |= changed_lines(fh.read())
        except OSError:
            return False        # cannot read a patch -> cannot vouch for the dirt
    if not found_any:
        return False

    return dirt == expected


def patches_dir_for(submodule_path: str) -> str:
    """Where a vendored submodule's patches live: a `patches/` directory beside
    the `vendor/` directory holding it. tools/dictation-rig/vendor/quill ->
    tools/dictation-rig/patches. Returns a path that may not exist; the caller
    treats a missing directory as "cannot explain this dirt".
    """
    parent = os.path.dirname(os.path.abspath(submodule_path))          # .../vendor
    return os.path.join(os.path.dirname(parent), "patches")            # .../patches


_WORKTREE_ROOTS = (".claude/worktrees", ".codex-worktrees", ".worktrees")
_GENERATED_PATHS = {
    "tools/doc-convo/assets/.render-daemon.pid",
    "tools/doc-convo/assets/render-daemon.log",
}


def _porcelain_path(row: str) -> str | None:
    """Return the live path from a v1 porcelain row, or None if malformed."""
    if len(row) < 4:
        return None
    path = row[3:].strip().strip('"')
    return path.split(" -> ", 1)[-1] if path else None


def _registered_worktrees(repo: str) -> set[str]:
    """Absolute paths registered to this repository's Git worktree inventory."""
    try:
        output = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=repo,
            capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {os.path.realpath(line.split(" ", 1)[1]) for line in output.splitlines()
            if line.startswith("worktree ")}


def _verified_worktree(repo: str, relpath: str, registered: set[str]) -> bool:
    """True only when a managed-root path belongs to a nested Git checkout.

    A name below a familiar root is insufficient: a source file under that
    root remains actionable unless the immediate worktree child has its .git
    directory or gitdir link.  This lets a managed checkout be reported without
    turning its root into a blanket ignore rule.
    """
    clean = relpath.rstrip("/")
    for root in _WORKTREE_ROOTS:
        if clean == root:
            try:
                children = list(os.scandir(os.path.join(repo, root)))
            except OSError:
                return False
            return bool(children) and all(
                child.is_dir() and os.path.exists(os.path.join(child.path, ".git"))
                and os.path.realpath(child.path) in registered
                for child in children)
        if clean.startswith(root + "/"):
            tail = clean[len(root):].lstrip("/").split("/", 1)[0]
            child = os.path.join(repo, root, tail)
            return (os.path.exists(os.path.join(child, ".git"))
                    and os.path.realpath(child) in registered)
    return False


def _generated_artifact(relpath: str) -> bool:
    """Known product-owned generated outputs, deliberately an exact set."""
    return relpath.rstrip("/") in _GENERATED_PATHS


def classify_loose_status(repo: str | os.PathLike[str], rows: list[str]) -> dict[str, list[str]]:
    """Split porcelain rows into actionable and non-actionable loose-work.

    Expected patched submodules are verified against their tracked patches;
    managed artifacts require either a verified nested worktree or an exact,
    source-backed generated-output path. Everything unknown remains actionable.
    """
    root = os.fspath(repo)
    registered = _registered_worktrees(root)
    buckets: dict[str, list[str]] = {
        "actionable_tracked": [], "actionable_untracked": [],
        "expected_patched_submodules": [], "managed_artifacts": []}
    for row in rows:
        if not row.strip():
            continue
        relpath = _porcelain_path(row)
        if not relpath:
            continue
        full = os.path.join(root, relpath)
        if row.startswith("??"):
            if _verified_worktree(root, relpath, registered) or _generated_artifact(relpath):
                buckets["managed_artifacts"].append(relpath)
            else:
                buckets["actionable_untracked"].append(relpath)
            continue
        if os.path.isdir(full) and os.path.exists(os.path.join(full, ".git")):
            try:
                diff = subprocess.run(["git", "diff"], cwd=full, capture_output=True,
                                      text=True, timeout=20).stdout
                if submodule_dirt_is_tracked_patch(diff, patches_dir_for(full)):
                    buckets["expected_patched_submodules"].append(relpath)
                    continue
            except (OSError, subprocess.SubprocessError):
                pass                    # cannot vouch for it -> actionable
        buckets["actionable_tracked"].append(relpath)
    return buckets


def loose_work_requires_attention(
    buckets: dict[str, list[str]], tracked_requires_attention: bool = True
) -> bool:
    """Whether a consumer must be non-green for this loose-work state.

    Canonical health has no age grace and takes the default. Recovery supplies
    its existing tracked-file age verdict; unknown untracked source is always
    actionable in either view.
    """
    return bool((tracked_requires_attention and buckets["actionable_tracked"])
                or buckets["actionable_untracked"])
