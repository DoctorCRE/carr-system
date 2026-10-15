#!/usr/bin/env python3
"""SessionEnd/Stop adapter for the disk reaper; explicit creation receipts register scratch.

Use --register PATH when creating scratch, or supply scratch_paths in a hook
event. PostToolUse can observe successful mktemp -d output. Unknown ownership
never becomes permission to reap another session's directory.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("session_disk_reaper", ROOT / "ops/disk-reaper.py")
if spec is None or spec.loader is None:
    raise ImportError("cannot load disk reaper")
reaper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reaper
spec.loader.exec_module(reaper)


def created_paths(event):
    paths = list(event.get("scratch_paths", []))
    if event.get("hook_event_name") == "PostToolUse":
        inputs = event.get("tool_input") or {}
        command = inputs.get("command", inputs.get("cmd", ""))
        try:
            words = shlex.split(command)
        except ValueError:
            return paths
        # Restrict inference to one mktemp command with a successful response.
        # Shell pipelines, substitutions and chained commands need --register.
        if (words and Path(words[0]).name == "mktemp" and "-d" in words
                and not any(c in command for c in (";", "|", "&", "$", "`", "\n"))):
            response = event.get("tool_response") or {}
            if isinstance(response, dict) and response.get("exit_code", response.get("exitCode")) == 0:
                output = response.get("stdout", response.get("output", "")).strip()
                if output.startswith("/") and "\n" not in output:
                    paths.append(output)
    return paths


def main(argv=None, event=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="append", default=[])
    parser.add_argument("--session")
    parser.add_argument("--config", type=Path, default=ROOT / "ops/config/disk-reaper.v1.json")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if event is None:
            event = {} if sys.stdin.isatty() else json.loads(sys.stdin.read() or "{}")
        session = args.session or event.get("session_id") or os.environ.get("CODEX_SESSION_ID")
        if not session:
            raise ValueError("no session identity; scratch preserved")
        engine = reaper.Reaper(args.root, reaper.load_config(args.config))
        registered = [*args.register, *created_paths(event)]
        hook = event.get("hook_event_name")
        if registered:
            engine.register(registered, session)
        if hook in {"Stop", "SessionEnd"}:
            report = engine.run(execute=not args.dry_run, session=session)
            print(json.dumps({"session": session, "freed_bytes": report["freed_bytes"], "errors": report["errors"]}))
        return 0
    except Exception as exc:
        print(f"session scratch preserved: {exc}", file=sys.stderr)
        return 0  # Cleanup must never prevent session completion.


if __name__ == "__main__":
    raise SystemExit(main())
