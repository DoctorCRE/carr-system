#!/usr/bin/env python3
"""Deliver deterministic store-scoped rules to interactive Flash sessions.

Uses the same selection interface as scripted tasks. Slash commands, empty
messages, catalog outages, and unmatched scopes produce no blocking output.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "flash-prompt-rules.jsonl")


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


def build_output(prompt):
    """Return context for matched rules; selection gaps remain advisory."""
    text = (prompt or "").strip()
    if text.startswith("/") or not text:
        return None
    row = {"at": datetime.now(timezone.utc).isoformat(), "prompt": text[:300]}
    try:
        flash_run = _flash_run()
        rules, note = flash_run.pick_rules(text)
        block = flash_run.rules_block(rules)
    except Exception:
        rules, note, block = [], "rule scope catalog unavailable", None
    row["rules"] = [r.get("id") for r in rules]
    if note:
        row["error"] = note
    _log(row)
    if not block:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": block}}


def main(stdin_text=None):
    try:
        payload = json.loads(sys.stdin.read() if stdin_text is None else stdin_text)
        prompt = payload.get("prompt") if isinstance(payload, dict) else None
    except (ValueError, OSError):
        return 0
    out = build_output(prompt)
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
