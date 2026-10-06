#!/usr/bin/env python3
"""Regression cases for the historic Stop-summary replay."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("replay-stop-gate-telemetry.py")
spec = importlib.util.spec_from_file_location("replay", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"could not load {SCRIPT}")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def summary(timestamp, infos, errors=(), context=()):
    return {"subtype": "stop_hook_summary", "timestamp": timestamp,
            "sessionId": "real-session", "hookInfos": infos,
            "hookErrors": list(errors), "hookAdditionalContext": list(context)}


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    path = home / "real.jsonl"
    rows = [
        summary("2026-08-24T00:00:00Z", [
            {"command": "/usr/bin/env python3 /repo/hooks/conduct-stop-gate.py"},
            {"command": "/usr/bin/env python3 /repo/hooks/loose-work-gate.py"},
        ], errors=["CONDUCT GATE — turn is unfinished"],
           context=["[/repo/hooks/loose-work-gate.py]: file remains"]),
        summary("2026-08-24T01:00:00Z", [
            {"command": "/usr/bin/python3 /repo/hooks/hook-meter-run.py /repo/hooks/drift-assertion-gate.py"},
        ], errors=["[/usr/bin/python3 /repo/hooks/hook-meter-run.py /repo/hooks/drift-assertion-gate.py]: No stderr output"]),
        summary("2026-08-10T00:00:00Z", [
            {"command": "/usr/bin/env python3 /repo/hooks/completion-evidence-gate.py"},
        ], errors=["COMPLETION EVIDENCE GATE — stale window"]),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    hooks, totals = replay.replay(home, replay.datetime(2026, 8, 25, tzinfo=replay.timezone.utc) - replay.timedelta(days=7))
    assert totals["stop_events"] == 2, totals
    assert totals["hook_invocations"] == 3, totals
    assert hooks["conduct-stop-gate.py"]["reopens"] == 1, hooks
    assert hooks["loose-work-gate.py"]["announces"] == 1, hooks
    assert hooks["drift-assertion-gate.py"]["errors"] == 1, hooks
    assert "completion-evidence-gate.py" not in hooks, hooks
print("replay-stop-gate-telemetry selftest ok")
