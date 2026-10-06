#!/usr/bin/env python3
"""Selftest for hooks/session-presence-hook.py, the presence recorder.

The hook must never block or speak: every path exits 0 with empty stdout.
Its one effect is a per-session outbox file. This drives the real hook as a
subprocess, the way Claude Code and Codex run it, with the room post
disabled (CARR_GATE_REPLAY_ROOT set, so no detached child reaches the live
Model Room), then runs the module's unit suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "session-presence-hook.py"
UNIT = ROOT / "tools" / "room-bridge" / "test_session_presence_unit.py"
SID = "3f1c2a9e-0000-4000-8000-00000000abcd"


def run(runtime: str, payload, presence: Path, extra: dict | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_")}
    env.update({"CARR_SESSION_PRESENCE_DIR": str(presence), "CARR_GATE_REPLAY_ROOT": "selftest"})
    env.update(extra or {})
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK), runtime], input=data, env=env,
                          capture_output=True, text=True, timeout=20)


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        presence = Path(tmp) / "presence"
        start = {"hook_event_name": "SessionStart", "session_id": SID, "cwd": tmp, "source": "startup"}
        r = run("claude", start, presence, {
            "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/selftest.sock",
            "CLAUDE_CODE_MESSAGING_TOKEN": "selftest-token-must-not-leak"})
        check("SessionStart exits 0", r.returncode == 0, r.stderr[-300:])
        check("SessionStart prints nothing", r.stdout == "", repr(r.stdout[:200]))
        box = presence / "outbox" / "claude-0000abcd.json"
        check("SessionStart writes the outbox", box.is_file(), str(sorted(presence.rglob("*"))))
        text = box.read_text() if box.is_file() else ""
        check("the messaging token never reaches disk", "selftest-token-must-not-leak" not in text)
        rec = json.loads(text)["record"] if text else {}
        check("record is an announce with a token-gated socket address",
              rec.get("event") == "announce" and rec.get("address", {}).get("auth") == "token", str(rec)[:300])

        end = {"hook_event_name": "SessionEnd", "session_id": SID, "cwd": tmp}
        r = run("claude", end, presence)
        check("SessionEnd exits 0 silently", r.returncode == 0 and r.stdout == "", r.stderr[-300:])
        rec = json.loads(box.read_text())["record"] if box.is_file() else {}
        check("SessionEnd records a depart", rec.get("event") == "depart", str(rec)[:300])

        codex = {"hook_event_name": "SessionStart", "session_id": "01a0e2da-0000-7000-8000-0000feedbeef",
                 "cwd": tmp, "model": "gpt", "source": "startup"}
        r = run("codex", codex, presence)
        cbox = presence / "outbox" / "codex-feedbeef.json"
        check("Codex SessionStart writes a codex-thread address",
              r.returncode == 0 and r.stdout == "" and cbox.is_file()
              and json.loads(cbox.read_text())["record"]["address"]["kind"] == "codex-thread",
              r.stderr[-300:])

        for label, bad in (("malformed stdin", "{not json"), ("empty stdin", ""), ("no session id", {"hook_event_name": "SessionStart"})):
            r = run("claude", bad, Path(tmp) / f"p-{label.replace(' ', '-')}")
            check(f"{label} fails open (exit 0, silent)", r.returncode == 0 and r.stdout == "", r.stderr[-300:])

        blocked = Path(tmp) / "not-a-dir"
        blocked.write_text("a file where the presence dir should be")
        r = run("claude", start, blocked)
        check("unwritable presence dir fails open", r.returncode == 0 and r.stdout == "", r.stderr[-300:])

    u = subprocess.run([sys.executable, str(UNIT)], capture_output=True, text=True, timeout=120)
    check("unit suite test_session_presence_unit passes", u.returncode == 0, (u.stdout + u.stderr)[-600:])

    print(f"session-presence-hook selftest: {'OK' if not failures else 'FAIL'} "
          f"({len(failures)} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
