#!/usr/bin/env python3
# doctrine: runbook
"""Warn, never block, when a push ADDS a line carrying a known client name.

Joe, 2026-09-24 (decision 4d6ce1c7 and follow-up): the git history stays as it
is and nothing is scrubbed in bulk. Ideally new files carry no sensitive
information, and the old ones convert gradually: when you touch a line that has
a client name, swap in a pseudonym in the same change.

This is that nudge and nothing more:

  * It reads only the lines this branch ADDS relative to main
    (`git diff origin/main...HEAD`), never the whole tree, so it costs O(diff).
  * The name list is the gitignored local file (ops/config/client-names.local.txt,
    or $CARR_CLIENT_NAMES, or the canonical checkout's copy), plus the dossier
    filenames from the local dossier roster. Neither is ever committed, and no
    digest of a name is committed either. If neither file exists the check is
    silent: there is nothing to compare against, and a machine without the list
    is not doing anything wrong.
  * It prints file:line and the KIND of match, never the name itself.
  * It ALWAYS exits 0. It is not a gate, it has no CI step and no secret, and
    any failure inside it (a bad range, a missing git) is swallowed silently.

Matching: lowercase, drop apostrophes, every run of characters outside [a-z0-9]
becomes one space; a name matches as a whole run of tokens, so "ann" never
matches inside "annual".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NAMES_ENV = "CARR_CLIENT_NAMES"
NAMES_BASENAME = os.path.join("ops", "config", "client-names.local.txt")
ROSTER_ENV = "CARR_DOSSIER_ROSTER"
ROSTER_BASENAME = os.path.join("exporters", "dossier-roster.local.json")
TOKEN = re.compile(r"[a-z0-9]+")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MAX_REPORTED = 40
GIT_TIMEOUT_SECONDS = 20


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower().replace("'", "").replace("’", ""))


def _first_existing(env: str, basename: str) -> str | None:
    override = os.environ.get(env)
    if override:
        return override if os.path.isfile(override) else None
    for base in (REPO, os.path.expanduser("~/carr-system")):
        p = os.path.join(base, basename)
        if os.path.isfile(p):
            return p
    return None


def load_names() -> dict[str, str]:
    """{canonical name: kind label}. Empty when no local list exists."""
    out: dict[str, str] = {}
    p = _first_existing(NAMES_ENV, NAMES_BASENAME)
    if p:
        with open(p, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if line.strip() and not line.lstrip().startswith("#"):
                    c = " ".join(tokens(line))
                    if c:
                        out.setdefault(c, f"client name, local list line {i}")
    r = _first_existing(ROSTER_ENV, ROSTER_BASENAME)
    if r:
        try:
            with open(r, encoding="utf-8") as fh:
                roster = json.load(fh).get("dossiers", {})
            for fname in roster:
                c = " ".join(tokens(fname[:-3] if fname.endswith(".md") else fname))
                if c:
                    out.setdefault(c, "dossier roster name")
        except (OSError, ValueError, AttributeError):
            pass
    return out


def added_lines(repo: str, rng: str) -> list[tuple[str, int, str]]:
    # --no-textconv: a repository's diff.<driver>.textconv is arbitrary code
    # that can take any amount of time, and this note reads raw added lines.
    # The timeout bounds everything else: a note must never hold a push.
    diff = subprocess.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-U0", rng],
        cwd=repo, capture_output=True, check=True,
        timeout=GIT_TIMEOUT_SECONDS).stdout.decode("utf-8", "replace")
    out, path, ln = [], None, 0
    for line in diff.split("\n"):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = HUNK.match(line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and path is not None:
            out.append((path, ln, line[1:]))
            ln += 1
    return out


def findings(names: dict[str, str], lines: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    firsts = {n.split()[0] for n in names}
    longest = max((len(n.split()) for n in names), default=1)
    out = []
    for path, ln, text in lines:
        toks = tokens(text)
        for i, tok in enumerate(toks):
            if tok not in firsts:
                continue
            hit = next((names[" ".join(toks[i:i + n])]
                        for n in range(min(longest, len(toks) - i), 0, -1)
                        if " ".join(toks[i:i + n]) in names), None)
            if hit:
                out.append((path, ln, hit))
                break
    return out


def main(argv: list[str]) -> int:
    try:
        rng = argv[1] if len(argv) > 1 else "origin/main...HEAD"
        repo = argv[2] if len(argv) > 2 else os.getcwd()
        names = load_names()
        if not names:
            return 0
        hits = findings(names, added_lines(repo, rng))
        if not hits:
            return 0
        print(f"\n  pre-push NOTE (warning only, the push continues): {len(hits)} added "
              f"line(s) carry a known client name.", file=sys.stderr)
        for path, ln, kind in hits[:MAX_REPORTED]:
            print(f"      {path}:{ln}  ({kind})", file=sys.stderr)
        if len(hits) > MAX_REPORTED:
            print(f"      ... and {len(hits) - MAX_REPORTED} more", file=sys.stderr)
        print("  When you touch a line that has a client name, swap in a pseudonym in the same\n"
              "  change. That is the whole conversion: gradual, nothing scrubbed in bulk.\n",
              file=sys.stderr)
    except Exception:  # noqa: BLE001 - advisory only; it must never affect a push
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
