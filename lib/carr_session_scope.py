"""carr_session_scope.py — the ONE rule for which agent sessions are CARR's.

WHY THIS EXISTS. Two things on this machine read agent session transcripts:
the displacement baselines (and the corrections sweep that borrows their
roots) and the nightly session-trace archive. Every Claude Code project and
every Codex session on the Mac sits next to CARR's, including personal (Life
AI) and unrelated work, so "which sessions are CARR's" is a data-class
boundary, not a convenience filter. The first archive draft walked every
project directory and every Codex session; one shared predicate here is the
fix, so the boundary is written once and cannot drift between readers.

THE RULE. A Claude Code project directory (the path-encoded name Claude Code
derives from a session's launch directory: every character that is not a
letter or digit becomes "-") is CARR's when, and only when, it is one of:

  * a vault project: the name contains "CARR-AI" (the vault is reachable by
    two spellings, lib/carr_paths.my_drive_vault() and carr_paths.vault(),
    and each yields its own directory) and is not a scratchpad;
  * the repo project: exactly the encoded canonical checkout, which is
    "-Users-booko-carr-system" on the primary machine;
  * a repo worktree: that name followed by "-" (".claude/worktrees/<x>"
    encodes to "-Users-booko-carr-system--claude-worktrees-<x>").

A Codex session is CARR's when the working directory recorded in its own
session-metadata line is the canonical checkout, the vault, or inside one.

This module is a library: no shebang and no module-run guard, for the reason
ops/jev_judge.py gives (either would make it a registered script entrypoint in
the sealed source inventory). Nothing here touches the filesystem at import.
"""

from __future__ import annotations

import os
import posixpath
import re

from lib import carr_paths

VAULT_MARKER = "CARR-AI"
SCRATCHPAD_MARKER = "scratchpad"


def encode_project_dir(path: str) -> str:
    """Claude Code's project-directory name for a launch directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def repo_project_dirname(checkout: str | None = None) -> str:
    """The encoded canonical checkout: "-Users-booko-carr-system" for HOME=/Users/booko."""
    return encode_project_dir(checkout or carr_paths.canonical_checkout())


def is_carr_vault_project(name: str) -> bool:
    """The vault clause alone — exactly what displacement baselines always used."""
    return VAULT_MARKER in name and SCRATCHPAD_MARKER not in name


def is_carr_claude_project(name: str, *, checkout: str | None = None) -> bool:
    """True only for a CARR vault project, the repo project, or a repo worktree."""
    if is_carr_vault_project(name):
        return True
    repo = repo_project_dirname(checkout)
    return name == repo or name.startswith(repo + "-")


def _inside(path: str, root: str) -> bool:
    path = posixpath.normpath(path)
    root = posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def carr_cwd_roots(*, home: str | None = None, checkout: str | None = None) -> list[str]:
    """Directories a CARR Codex session may have been started in."""
    if home is None:
        return [checkout or carr_paths.canonical_checkout(),
                carr_paths.my_drive_vault(), carr_paths.vault()]
    return [checkout or os.path.join(home, "carr-system"),
            os.path.join(home, "My Drive", "CARR AI")]


def is_carr_cwd(cwd: object, *, home: str | None = None, checkout: str | None = None) -> bool:
    """True when a recorded working directory is inside the checkout or the vault.

    The CloudStorage spelling of the vault is matched structurally
    (<home>/Library/CloudStorage/<account>/My Drive/CARR AI[/...]) so any
    mounted Drive account counts, the same way hooks/gate_paths globs it.
    """
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        return False
    if any(_inside(cwd, root) for root in carr_cwd_roots(home=home, checkout=checkout)):
        return True
    cloud = posixpath.join(posixpath.normpath(home or carr_paths.home()),
                           "Library", "CloudStorage")
    if not _inside(cwd, cloud):
        return False
    rest = posixpath.normpath(cwd)[len(cloud):].strip("/").split("/")
    return len(rest) >= 3 and rest[1] == "My Drive" and rest[2] == "CARR AI"
