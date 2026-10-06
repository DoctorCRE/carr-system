#!/usr/bin/env python3
"""Hermetic checks that a LIVE export cannot resolve its vault root to the repo.

WHY THIS EXISTS. bin/routine-credential-env.sh runs every nightly step under
`env -i` and passes CARR_VAULT="${CARR_VAULT:-}", which is an EMPTY STRING when
the variable is unset — and it is unset under launchd. exporters/common.py read
that root with os.environ.get(name, default), which returns the empty string
rather than the default, so VAULT became Path("") and every path a LIVE export
called absolute resolved against the working directory instead: the repo.

Measured cost before the fix (2026-08-19 nightly run): two nights of exports
printed "ok — N rows -> DNA/... (LIVE)" while writing 126 files into untracked
~/carr-system/DNA and ~/carr-system/00_Context. The vault sat frozen at
2026-08-17 02:08 CDT, five generated files read STALE, 36 renders reported as
tampered (the recorded hash described the repo copy), and two export targets
failed to build against source files that exist perfectly well in the vault.

The three cases below are the whole contract: unset falls back, EMPTY falls back
the same way, and a real value is still honoured.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from git_env import fixture_env

REPO = Path(__file__).resolve().parents[1]
# The repo venv is what the nightly chain runs, so probe with it when it exists.
# A GitHub runner has no .venv — it installs requirements into the interpreter
# running this file — and hardcoding the venv path made this selftest die there
# with FileNotFoundError before it could assert anything. The interpreter is
# incidental to what is under test: the probe only has to import
# exporters.common and print the root it resolved.
_VENV = REPO / ".venv" / "bin" / "python"
PY = _VENV if _VENV.exists() else Path(sys.executable)
PROBE = "from exporters.common import VAULT; print(VAULT)"


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        raise AssertionError(label)


def vault_root(carr_vault: str | None) -> str:
    """Resolve exporters.common.VAULT in a clean child, as the chain does."""
    env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", "")}
    if carr_vault is not None:
        env["CARR_VAULT"] = carr_vault
    out = subprocess.run([str(PY), "-c", PROBE], cwd=REPO, env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main() -> int:
    print("export vault root")
    default = vault_root(None)
    check("unset CARR_VAULT resolves to an absolute root", default.startswith("/"))
    check("unset CARR_VAULT resolves outside the repo", not default.startswith(str(REPO)))

    empty = vault_root("")
    check("EMPTY CARR_VAULT falls back to the same root as unset", empty == default)
    check("EMPTY CARR_VAULT never resolves to the repo or a relative path",
          empty.startswith("/") and not empty.startswith(str(REPO)))

    with tempfile.TemporaryDirectory() as tmp:
        check("a real CARR_VAULT is still honoured", vault_root(tmp) == tmp)

    helper = (REPO / "bin" / "routine-credential-env.sh").read_text(encoding="utf-8")
    check("the clean child still passes CARR_VAULT through",
          'CARR_VAULT="${CARR_VAULT:-}"' in helper)

    # EVERY reader, not just the exporter. The empty-string bug was never
    # specific to exporters/common.py — it was the two-argument form of
    # os.environ.get, which 22 files used the same way, so the health check,
    # retrieve, report-card, the dossier renders and the brief pack all had it
    # latent and would have surfaced it one at a time as each was next run
    # under `env -i`. The two-argument form returns "" when the variable is set
    # empty; `or` treats empty and absent alike, which is the behaviour every
    # one of these callers actually wants. Static, so it holds for the files a
    # subprocess probe cannot import (missing credentials, heavy deps).
    # SCOPE IS THIS CHECKOUT'S OWN TRACKED PYTHON, nothing else. The first cut
    # skipped only .venv, out, node_modules and .claude, which is everything a
    # GitHub runner or a fresh worktree contains — so it passed CI and passed in
    # the worktree it was written in, then failed the moment it ran in Joe's
    # canonical tree. That tree also holds .worktrees/ and .codex-worktrees/,
    # which are OTHER SESSIONS' checkouts sitting at whatever commit they were
    # cut from, plus _to_delete/, which is where superseded files are staged
    # rather than deleted. Reporting another session's in-flight checkout as a
    # regression in this one is a false positive that would refuse everybody's
    # pushes, and it is the same mistake in kind as the bug this file exists to
    # catch: reading a path that belongs to somebody else as if it were ours.
    skip = (".venv/", "out/", "node_modules/", ".claude/",
            ".worktrees/", ".codex-worktrees/", "_to_delete/", ".tmp")
    bad = []
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", "*.py"],
        env=fixture_env(), capture_output=True, check=True,
    ).stdout.decode().split("\0")
    for rel in sorted(item for item in tracked if item):
        if rel.startswith(skip):
            continue
        path = REPO / rel
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "CARR_VAULT" not in text:
            continue
        if re.search(r'environ\.get\(\s*"CARR_VAULT"\s*,', text):
            bad.append(rel)
    check("no CARR_VAULT reader uses the two-argument environ.get form"
          + (f" (found: {', '.join(bad)})" if bad else ""), not bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
