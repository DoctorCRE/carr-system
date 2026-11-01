#!/usr/bin/env python3
"""team-memory-index.py: SessionStart brief of the live team memory (WR-000562 S10).

Team memory, the record layer's doctrine document `team-memory`, replaced
Claude Code's private auto-memory folder. This repo's .claude/settings.json
turns that folder off (autoMemoryEnabled: false). The folder used to be pushed
into every session; this hook keeps that property for the live store. It
prints the attach instruction plus one line per active section (key and
title, never bodies), read fresh through the existing read-doctrine verb.

Fail-open: any error prints one CARR HOOK WARNING line and exits 0. A session
can still call attach-memory, which returns the same index.

Fixtures: ops/team-memory-index-selftest.py.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT_S = 12
# Claude Code caps injected hook context at 10,000 characters.
MAX_CHARS = 9000
TITLE_CHARS = 50

HEADER = (
    "TEAM MEMORY (record-layer doctrine document team-memory, generation {generation}, "
    "{count} sections). It replaces Claude Code's private auto-memory, which is off. "
    "OPENING ACT: call attach-memory once, first (surface claude_code; access read_write "
    "for build and lesson-writing sessions, read_only for research). Before starting, "
    "read any section below that bears on the task (doctrine-sections with the ids "
    "attach-memory returns) and skip work it records as done or ruled out. Write what "
    "you learn with write-doctrine-section document=team-memory attach_id=<your id> "
    "(base_version 0 to create, else the current version). Never write memory files "
    "under ~/.claude. Sections (key: title):"
)


def fetch():
    """Read the whole team-memory document through tools/call-verb.py."""
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "call-verb.py"),
         "read-doctrine", json.dumps({"document": "team-memory"})],
        capture_output=True, text=True, timeout=TIMEOUT_S, cwd=REPO)
    if proc.returncode != 0:
        raise RuntimeError(f"read-doctrine exited {proc.returncode}")
    out = proc.stdout
    return json.loads(out[out.index("{"):])


def brief(doc):
    if not doc.get("ok", True) or not isinstance(doc.get("sections"), list):
        raise ValueError("read-doctrine returned no sections")
    active = sorted((s for s in doc["sections"] if s.get("status", "active") == "active"),
                    key=lambda s: s["section_key"])
    text = HEADER.format(generation=doc.get("generation", "?"), count=len(active))
    for shown, section in enumerate(active):
        title = (section.get("title") or "").strip()
        if len(title) > TITLE_CHARS:
            title = title[:TITLE_CHARS - 3].rstrip() + "..."
        line = "\n- " + section["section_key"] + (f": {title}" if title else "")
        tail = f"\n- ... {len(active) - shown} more sections; attach-memory lists them all."
        if len(text) + len(line) + len(tail) > MAX_CHARS:
            return text + tail
        text += line
    return text


def main(fetch=fetch):
    try:
        print(brief(fetch()))
    except Exception as exc:
        reason = " ".join(f"{type(exc).__name__}: {exc}".split())[:160]
        print(f"CARR HOOK WARNING: team-memory index unavailable ({reason}); "
              "call attach-memory to get it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
