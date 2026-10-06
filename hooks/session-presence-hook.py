#!/usr/bin/env python3
"""session-presence-hook.py — the one-line hook adapter for session presence.

    <python> hooks/session-presence-hook.py <runtime>

Every runtime's hook config points here with its own runtime name: Claude Code
(ops/config/hooks.json, which Grok Build also reads), Codex Desktop and CLI
(ops/config/codex-hooks.json). The logic lives in one runtime-neutral place,
tools/room-bridge/session_presence.py; this file only hands it the payload.

It is not a gate and decides nothing. It always exits 0, prints nothing (a
SessionStart hook's stdout becomes model context), and never touches the
network on the session's own path: the room post runs in a detached child. A
Model Room that is unreachable costs one logged notice in
~/.config/carr/session-presence/presence.log.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools", "room-bridge"))

try:
    import session_presence
except Exception:  # noqa: BLE001 — a broken import must never block a session start
    raise SystemExit(0)

raise SystemExit(session_presence.run_hook(sys.argv[1] if len(sys.argv) > 1 else "claude"))
