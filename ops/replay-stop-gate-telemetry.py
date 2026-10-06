#!/usr/bin/env python3
"""Replay Claude Stop summaries into live, per-hook intervention telemetry.

The live meter starts counting only after its wrapper is installed.  This tool
reads the seven preceding days of transcript summaries so the first behaviour
change is based on real Stop outcomes, not selftest fixtures.  A summary names
every installed hook in ``hookInfos`` and carries the output that actually
reached Claude in ``hookErrors`` / ``hookAdditionalContext``.  The two must not
be conflated: an invocation is not an intervention, and an announcement is not
a turn reopen.

By default the result is printed and written as one replaceable JSONL snapshot
row per hook per UTC day in ``out/stop-gate-telemetry.jsonl``.  The output is a
machine artifact, not a status record; the caller reports consequential results
through the record layer.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_HOME = Path.home() / ".claude" / "projects"
DEFAULT_OUT = REPO / "out" / "stop-gate-telemetry.jsonl"
ERROR_PREFIX = re.compile(r"^\[(.+?)\]:\s*(.*)$", re.DOTALL)
PYTHON_TOKEN = re.compile(r"([^\s]+\.py)(?:\s|$)")

# Older Claude summaries omitted the command prefix for direct Stop output. The
# gate's own headline is stable enough to map that output back to the hook that
# produced it; an unmatched output remains visible instead of being guessed.
HEADLINES = {
    "CONDUCT GATE": "conduct-stop-gate.py",
    "COMPLETION EVIDENCE GATE": "completion-evidence-gate.py",
    "MAP ARCHITECTURE": "map-architecture-gate.py",
    "CONTEXT HANDOFF": "context-handoff-gate.py",
    "CHAT LINT": "chat-lint-gate.py",
    "STALE CLAIM": "stale-claim-gate.py",
    "LOOSE WORK": "loose-work-gate.py",
    "DRIFT ASSERTION": "drift-assertion-gate.py",
    "UNREAD ARTIFACT": "unread-artifact-gate.py",
}


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def hook_name(command: str) -> str | None:
    """Return the gate, skipping the meter wrapper and callback hooks."""
    candidates = [Path(match.group(1)).name for match in PYTHON_TOKEN.finditer(command or "")]
    candidates = [name for name in candidates if name != "hook-meter-run.py"]
    return candidates[0] if candidates else None


def output_hook(text: str, commands: dict[str, str]) -> tuple[str | None, str]:
    """Map one reported output to (hook, body), never inventing an owner."""
    match = ERROR_PREFIX.match(text or "")
    if match:
        command, body = match.groups()
        return hook_name(command), body
    upper = (text or "").upper()
    for headline, name in HEADLINES.items():
        if headline in upper:
            return name, text
    # A summary with exactly one real hook can safely attribute an unprefixed
    # line.  Anything wider is retained as unmapped evidence in the rollup.
    if len(commands) == 1:
        return next(iter(commands)), text
    return None, text


def register(body: str, *, error: bool) -> str:
    """The summary's output register; ``error`` is a hook crash, not a block."""
    if error:
        return "error"
    if not (body or "").strip():
        return "silent"
    # Claude writes this exact wrapper diagnostic when a meter-wrapped hook
    # exited without producing stderr.  It is evidence of an instrumentation
    # fault, never a gate intervention.
    if (body or "").strip() == "No stderr output":
        return "error"
    return "reopen"


def replay(home: Path, cutoff: datetime) -> tuple[dict[str, dict], dict[str, int]]:
    hooks: dict[str, dict] = defaultdict(lambda: {
        "invocations": 0, "reopens": 0, "announces": 0, "errors": 0,
        "sessions": set(), "days": defaultdict(Counter),
    })
    totals: Counter[str] = Counter()
    for path in home.rglob("*.jsonl") if home.exists() else ():
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if "stop_hook_summary" not in line:
                        continue
                    try:
                        summary = json.loads(line)
                    except json.JSONDecodeError:
                        totals["malformed_summaries"] += 1
                        continue
                    if summary.get("subtype") != "stop_hook_summary":
                        continue
                    when = parse_time(summary.get("timestamp"))
                    if when is None or when < cutoff:
                        continue
                    totals["stop_events"] += 1
                    session = summary.get("sessionId") or "?"
                    day = when.date().isoformat()
                    commands = {}
                    for info in summary.get("hookInfos") or []:
                        name = hook_name(info.get("command", ""))
                        if not name:
                            continue
                        commands[name] = info.get("command", "")
                        row = hooks[name]
                        row["invocations"] += 1
                        row["sessions"].add(session)
                        row["days"][day]["invocations"] += 1
                    for output in summary.get("hookErrors") or []:
                        name, body = output_hook(output, commands)
                        if name is None:
                            totals["unmapped_outputs"] += 1
                            continue
                        row = hooks[name]
                        kind = register(body, error=False)
                        row[kind + "s"] += 1
                        row["days"][day][kind + "s"] += 1
                    for output in summary.get("hookAdditionalContext") or []:
                        name, _ = output_hook(output, commands)
                        if name is None:
                            totals["unmapped_outputs"] += 1
                            continue
                        hooks[name]["announces"] += 1
                        hooks[name]["days"][day]["announces"] += 1
        except OSError as exc:
            totals["unreadable_files"] += 1
            print(f"warn: {path}: {exc}", file=sys.stderr)
    totals["hook_invocations"] = sum(row["invocations"] for row in hooks.values())
    totals["reopens"] = sum(row["reopens"] for row in hooks.values())
    totals["announces"] = sum(row["announces"] for row in hooks.values())
    totals["errors"] = sum(row["errors"] for row in hooks.values())
    return hooks, dict(totals)


def snapshot_rows(hooks: dict[str, dict]) -> list[dict]:
    rows = []
    for name, row in sorted(hooks.items()):
        for day, counts in sorted(row["days"].items()):
            rows.append({"day": day, "hook": name, "sessions": len(row["sessions"]),
                         **{key: counts.get(key, 0)
                            for key in ("invocations", "reopens", "announces", "errors")}})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--now", help="UTC timestamp for a deterministic replay")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least one")
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be an ISO-8601 timestamp")
    hooks, totals = replay(args.home, now - timedelta(days=args.days))
    rendered = {name: {**{key: value for key, value in row.items() if key not in ("sessions", "days")},
                        "sessions": len(row["sessions"])}
                for name, row in sorted(hooks.items())}
    print(json.dumps({"window_days": args.days, "totals": totals, "hooks": rendered}, indent=2))
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as stream:
            for row in snapshot_rows(hooks):
                stream.write(json.dumps(row, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
