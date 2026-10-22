#!/usr/bin/env python3
"""Validate a disposable, exact historical source root for staging recovery.

This is intentionally a narrow internal building-block.  The current deploy
wrapper owns all policy and database writes; a detached source tree can supply
only the Worker/assets that its already-bound recovery step names.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                   stderr=subprocess.DEVNULL).strip()


def validate(root_arg: str, expected_sha: str) -> Path:
    if len(expected_sha) != 40 or any(c not in "0123456789abcdef" for c in expected_sha):
        raise ValueError("expected SHA must be an exact lowercase commit")
    root = Path(root_arg).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("exact source root is not a directory")
    try:
        top = Path(git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
        head = git(root, "rev-parse", "HEAD")
        branch = subprocess.run(["git", "-C", str(root), "symbolic-ref", "-q", "HEAD"],
                                text=True, capture_output=True, check=False)
        dirty = git(root, "status", "--porcelain")
        ignored = git(root, "ls-files", "--others", "--ignored", "--exclude-standard")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("exact source root is not a readable Git worktree") from exc
    if top != root:
        raise ValueError("exact source root must be the Git worktree root")
    if branch.returncode == 0:
        raise ValueError("exact source root must be detached")
    if head != expected_sha:
        raise ValueError("exact source root HEAD does not equal the DB-bound SHA")
    if dirty or ignored:
        raise ValueError("exact source root has uncommitted or ignored inputs")
    required_files = ("mcp-server/wrangler.toml", "mcp-server/src/tools.js")
    missing = [relative for relative in required_files if not (root / relative).is_file()]
    if missing:
        raise ValueError("exact source root is missing required inputs: " + ", ".join(missing))
    return root


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        print(validate(args.root, args.sha))
    except ValueError as exc:
        print(f"validate-exact-recovery-source: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
