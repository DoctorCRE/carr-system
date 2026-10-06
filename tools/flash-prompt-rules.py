#!/usr/bin/env python3
"""flash-prompt-rules.py — UserPromptSubmit hook for interactive Flash sessions.

Joe's direction 2026-09-24: Flash should get the taught rules it needs at the right
time, picked by Jev. flash-run does that once per scripted task (pick_rules); this does
it for interactive sessions (the flash command, T3 Code, the Model Room seat), once per
message, because each message can be a different task. Both go through the same
pick_rules, so the roster and the failure reporting cannot drift apart.

Wired only in Flash's own config (~/.claude-local/settings.json), never in the Claude
adapter: Claude sessions already get rules through standing-context and JIT triggers.

It never blocks. A slash command, a message too short to judge, nothing binding, a Jev
outage or bad input all mean no output and exit 0. Every judged message leaves a row in
out/flash-prompt-rules.jsonl, carrying the degraded-judgment note when there is one, so
an outage reads differently from "no rule binds".
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "flash-prompt-rules.jsonl")
MIN_CHARS = 20
# Said to the rule picker. Unlike flash-run's disposable copy, an interactive session works
# in a real checkout and may use git, so session rules are allowed to bind here.
SITUATION = ("A local coding model (Flash) is in an interactive coding session for Joe, "
             "working in a real checkout: it may read, edit, run tests and use git. "
             "Joe's message: ")


def _flash_run():
    path = os.path.join(REPO, "tools", "flash-run.py")
    spec = importlib.util.spec_from_file_location("flash_run", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(row):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def build_output(prompt, **judge):
    """The hook's JSON output for one message, or None when nothing should be added.
    `judge` passes through to flash-run's pick_rules (tests inject the paid requests)."""
    text = (prompt or "").strip()
    if text.startswith("/") or len(text) < MIN_CHARS:
        return None
    row = {"at": datetime.now(timezone.utc).isoformat(), "prompt": text[:300]}
    try:
        flash_run = _flash_run()
        rules, note = flash_run.pick_rules(text[:2000], situation=SITUATION, **judge)
        block = flash_run.rules_block(rules)
    except Exception as exc:
        rules, note, block = [], f"{type(exc).__name__}: {exc}"[:300], None
    row["rules"] = [r.get("id") for r in rules]
    if note:
        row["error"] = note
    _log(row)
    if not block:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": block}}


def main(stdin_text=None, **judge):
    try:
        payload = json.loads(sys.stdin.read() if stdin_text is None else stdin_text)
        prompt = payload.get("prompt") if isinstance(payload, dict) else None
    except (ValueError, OSError):
        return 0
    out = build_output(prompt, **judge)
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
