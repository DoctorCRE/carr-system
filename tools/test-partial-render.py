#!/usr/bin/env python3
"""Prove the partial-render contract for the CLAUDE.md gist block (PASS 1).

Four properties, each one a way the block could quietly corrupt a hand-authored
file if it were not tested:

  1. WITH markers  — everything outside them comes back byte-identical, and the
     marker lines themselves are preserved verbatim.
  2. NO markers    — hard failure, nothing written, and the message carries the
     exact lines to paste.
  3. TWO markers   — hard failure. Two blocks means one starts going stale today.
  4. IDEMPOTENT    — splicing into an already-spliced file replaces the block
     rather than nesting it, which is what makes the hourly re-run safe.

Read-only: works on temp copies, touches no vault file and no database.
Usage:  ./.venv/bin/python tools/test-partial-render.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exporters.partial import PartialRenderError, marker_lines, splice  # noqa: E402

NAME = "rule-gist-index"
BEGIN, END = marker_lines(NAME)
BLOCK = ["", "**generated line one**", "- gist a", "- gist b", ""]

HOST_HEAD = ["# CLAUDE.md: standing context", "", "Hand-authored paragraph one.", ""]
HOST_TAIL = ["", "## A later hand-authored section", "", "Hand-authored paragraph two.", ""]

fails: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(ok="  PASS  {label}", detail="  — {detail}", detail_on_ok=True, failures=fails)


def write(tmp, lines):
    p = tmp / "CLAUDE.md"
    p.write_text("\n".join(lines))
    return p


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("1. markers present — the hand-authored bytes survive")
        host = write(tmp, HOST_HEAD + [BEGIN, "stale content", "more stale", END] + HOST_TAIL)
        before = host.read_text()
        out = splice(host, NAME, BLOCK)
        lines = out.split("\n")
        b, e = lines.index(BEGIN), lines.index(END)
        check("head byte-identical", lines[:b] == HOST_HEAD)
        check("tail byte-identical", lines[e + 1:] == HOST_TAIL)
        check("marker lines verbatim", lines[b] == BEGIN and lines[e] == END)
        check("block replaced", lines[b + 1:e] == BLOCK)
        check("stale content gone", "stale content" not in out)
        check("host file untouched by splice", host.read_text() == before)

        print("2. markers absent — loud failure, no append")
        host = write(tmp, HOST_HEAD + HOST_TAIL)
        try:
            splice(host, NAME, BLOCK)
            check("raises PartialRenderError", False, "it returned instead")
        except PartialRenderError as exc:
            msg = str(exc)
            check("raises PartialRenderError", True)
            check("names the missing marker", "no BEGIN marker" in msg)
            check("hands over the exact lines", BEGIN in msg and END in msg)
        check("host file unchanged", host.read_text() == "\n".join(HOST_HEAD + HOST_TAIL))

        print("2b. BEGIN present, END missing — still a failure")
        host = write(tmp, HOST_HEAD + [BEGIN] + HOST_TAIL)
        try:
            splice(host, NAME, BLOCK)
            check("raises on half a pair", False, "it returned instead")
        except PartialRenderError:
            check("raises on half a pair", True)

        print("3. duplicate markers — refuses to pick one")
        host = write(tmp, HOST_HEAD + [BEGIN, END] + HOST_TAIL + [BEGIN, END])
        try:
            splice(host, NAME, BLOCK)
            check("raises on two blocks", False, "it returned instead")
        except PartialRenderError as exc:
            check("raises on two blocks", True)
            check("reports both line numbers", "2 BEGIN markers" in str(exc), str(exc)[:60])

        print("3b. host file missing entirely — never creates one")
        try:
            splice(tmp / "nope.md", NAME, BLOCK)
            check("raises on a missing host", False, "it returned instead")
        except PartialRenderError:
            check("raises on a missing host", True)
            check("created nothing", not (tmp / "nope.md").exists())

        print("4. idempotent — a second splice replaces, never nests")
        host = write(tmp, HOST_HEAD + [BEGIN, END] + HOST_TAIL)
        once = splice(host, NAME, BLOCK)
        host.write_text(once)
        twice = splice(host, NAME, BLOCK)
        check("second run byte-identical to first", once == twice)
        check("still exactly one BEGIN", twice.count(BEGIN) == 1)

    print()
    if fails:
        print(f"{len(fails)} FAILED: {', '.join(fails)}")
        return 1
    print("all partial-render properties hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
