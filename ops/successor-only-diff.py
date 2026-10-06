#!/usr/bin/env python3
"""Exit zero iff approved and new heads have the same branch-owned domain patch.

Invoke from the target Git repository with two object IDs. Each patch is based
on its own unique merge-base with the same pinned origin/main. No worktree,
index, remote, or object is modified.
"""
import difflib
import re
import subprocess
import sys
from pathlib import Path

from git_env import scrubbed_env
from successor_ownership import MIGRATION, OwnershipError, domain_bytes, is_owned_file


class CheckError(ValueError):
    pass


def git(*args):
    result = subprocess.run(["git", *args], cwd=Path.cwd(), env=scrubbed_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise CheckError("git " + args[0] + " failed: " + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def resolve(value):
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
        raise CheckError("revision must be a hexadecimal commit object ID: " + value)
    return git("rev-parse", "--verify", value + "^{commit}").decode().strip()


def merge_base(main, head):
    bases = git("merge-base", "--all", main, head).decode().splitlines()
    if len(bases) != 1:
        raise CheckError("requires one unique merge base for " + head)
    return bases[0]


def blob(oid, path):
    if set(oid) == {"0"}:
        return None
    try:
        return git("cat-file", "blob", oid)
    except CheckError as exc:
        raise CheckError(f"{path}: cannot read source object: {exc}") from exc


def patch_changes(before, after):
    """Changes with source anchors: location binds, numeric offsets do not."""
    before, after = before or b"", after or b""
    if b"\0" in before or b"\0" in after:
        return ("binary", before, after)
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    changes = []
    source_context = b"\0" + b"\0".join(old) + b"\0"
    for kind, i, j, k, l in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if kind == "equal":
            continue
        # Bind the edit to source context, not numeric offsets. Grow anchors
        # when repeated bodies would otherwise permit moving the same edit to
        # a different function. Unrelated main edits outside the anchors can
        # shift these lines without changing the branch-owned patch.
        start, end = max(0, i - 3), min(len(old), j + 3)
        while start > 0 or end < len(old):
            anchor = b"\0" + b"\0".join(old[start:end]) + b"\0"
            first = source_context.find(anchor)
            if source_context.find(anchor, first + 1) == -1:
                break
            start, end = max(0, start - 1), min(len(old), end + 1)
        changes.append((tuple(old[start:i]), tuple(old[i:j]),
                        tuple(new[k:l]), tuple(old[j:end])))
    return tuple(changes)


def branch_patch(main, head):
    base = merge_base(main, head)
    raw = git("diff", "--raw", "--no-abbrev", "--no-renames", "-z", base, head, "--")
    parts = raw.split(b"\0")
    patches = {}
    names = {}
    for index in range(0, len(parts) - 1, 2):
        metadata = parts[index].decode("ascii").split()
        if len(metadata) != 5 or not metadata[0].startswith(":"):
            raise CheckError("malformed Git diff metadata")
        oldmode, newmode, oldoid, newoid, status = metadata
        oldmode = oldmode[1:]
        path = parts[index + 1].decode("utf-8", "surrogateescape")
        before, after = blob(oldoid, path), blob(newoid, path)
        regular = oldmode in ("000000", "100644", "100755") and newmode in ("000000", "100644", "100755")
        if regular and is_owned_file(path, before, after):
            # Content ownership never authorizes a new symlink or chmod.
            if oldmode == newmode or oldmode == "000000" and newmode == "100644" or newmode == "000000" and oldmode == "100644":
                continue
            value = (oldmode, newmode, ())
        else:
            if regular:
                before = domain_bytes(path, before)
                after = domain_bytes(path, after)
            changes = patch_changes(before, after)
            if not changes and oldmode == newmode:
                continue
            value = (oldmode, newmode, changes)
        match = MIGRATION.fullmatch(path)
        key = "migrations/<number>_" + match[2] if match and oldmode == "000000" else path
        if key in patches:
            raise CheckError("ambiguous migration ownership: " + names[key] + ", " + path)
        patches[key] = value
        names[key] = path
    return patches, names


def main():
    if len(sys.argv) != 3:
        raise CheckError("usage: successor-only-diff.py <approved_sha> <new_sha>")
    approved, new = (resolve(value) for value in sys.argv[1:])
    origin_main = git("rev-parse", "--verify", "refs/remotes/origin/main^{commit}").decode().strip()
    oldpatch, oldnames = branch_patch(origin_main, approved)
    newpatch, newnames = branch_patch(origin_main, new)
    differences = sorted(key for key in oldpatch.keys() | newpatch.keys() if oldpatch.get(key) != newpatch.get(key))
    if differences:
        for key in differences:
            print("domain patch changed: " + oldnames.get(key, newnames.get(key, key)), file=sys.stderr)
        return 1
    print("successor-only diff: domain patch preserved")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CheckError, OwnershipError, OSError, UnicodeError) as exc:
        print("successor-only diff refused: " + str(exc), file=sys.stderr)
        sys.exit(1)
