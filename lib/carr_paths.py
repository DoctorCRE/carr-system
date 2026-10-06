"""carr_paths.py — the per-machine roots, derived once instead of typed in.

WHY (2026-09-23 audit). Seven hooks, six tools, four shell scripts and one
library each carried the literal "/Users/booko/..." as the canonical checkout
or the Drive vault. Every one of them was correct on the Mac Studio and wrong
on any other machine: Dell's launch machine, the CI runner, a fresh clone. Two
of them were the vault WRITE guards, which installed green and matched nothing.
hooks/gate_paths.vault_roots() already solved the vault half by globbing the
Drive mount; this module is the one place the rest of the tree gets the same
answer, so the next machine needs an environment variable, not a patch series.

The two overrides are the ones scripts already honoured: CARR_ROOT for the
checkout and CARR_VAULT for the vault. Nothing here touches the filesystem at
import time, and nothing here changes a result on the primary machine: for
HOME=/Users/booko every function returns the string it replaced.
"""

from __future__ import annotations

import glob
import os

DRIVE_ACCOUNT = "joe.bookout.carr.us@gmail.com"


def home() -> str:
    return os.path.expanduser("~")


def canonical_checkout() -> str:
    """The primary clone: CARR_ROOT, else ~/carr-system."""
    return os.environ.get("CARR_ROOT") or os.path.join(home(), "carr-system")


def legacy_vault() -> str:
    """The Drive File Stream spelling of the CARR AI vault for the primary
    account, under this machine's HOME. The .md renders that lived there were
    retired on 2026-08-19 (CLAUDE.md); the record-layer folders remain."""
    return os.path.join(home(), "Library", "CloudStorage",
                        f"GoogleDrive-{DRIVE_ACCOUNT}", "My Drive", "CARR AI")


def vault() -> str:
    """CARR_VAULT, else the primary spelling if it exists here, else the first
    Drive account mounted on this machine, else the primary spelling (paths
    simply will not match, which is the same as before)."""
    env = os.environ.get("CARR_VAULT")
    if env:
        return env
    primary = legacy_vault()
    if os.path.isdir(primary):
        return primary
    for hit in sorted(glob.glob(os.path.join(
            home(), "Library", "CloudStorage", "GoogleDrive-*", "My Drive", "CARR AI"))):
        if os.path.isdir(hit):
            return hit
    return primary


def my_drive_vault() -> str:
    """The ~/My Drive symlink spelling of the same vault."""
    return os.path.join(home(), "My Drive", "CARR AI")
