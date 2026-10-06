#!/usr/bin/env python3
"""Regression tests for the Codex standing-context boot contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "ops" / "agent-boot-contract.py"
AGENTS = REPO / "AGENTS.md"


def run(text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as td:
        candidate = Path(td) / "AGENTS.md"
        candidate.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(CHECK), str(candidate)],
            text=True,
            capture_output=True,
            check=False,
        )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    source = AGENTS.read_text(encoding="utf-8")

    good = run(source)
    require(good.returncode == 0, f"real AGENTS.md failed: {good.stderr}")

    no_direct = run(source.replace("mcp__carr__standing_context", "standing-context", 1))
    require(no_direct.returncode != 0, "missing direct MCP tool was accepted")

    no_catalog = run(source.replace("deferred tool catalog", "tool list", 1))
    require(no_catalog.returncode != 0, "missing lazy-tool discovery instruction was accepted")

    no_sandbox_guard = run(source.replace("not a store outage", "a store outage", 1))
    require(no_sandbox_guard.returncode != 0, "sandbox failure could still be called an outage")

    fallback_first = source.replace(
        "Call `mcp__carr__standing_context` directly FIRST.",
        "Run `./run.sh call standing-context '{}'` directly FIRST.",
        1,
    )
    require(run(fallback_first).returncode != 0, "shell-first boot order was accepted")

    packets = REPO / "ops/config/task-boot"
    for target in sorted(packets.glob("*.json")):
        packet = json.loads(target.read_text())
        require(set(packet) == {"instructions", "instructions_sha256"},
                f"{target.name} carries fields nothing reads")
        require(hashlib.sha256(packet["instructions"].encode()).hexdigest()
                == packet["instructions_sha256"],
                f"{target.name} instructions changed without re-pinning their digest")
    for key, heading in (
        ("r09", "Active WR-000070 R09 executor recovery"),
        ("wr68", "Temporary supervised WR68 source execution"),
        ("wr69", "Temporary supervised WR69 registered Codex validation"),
        ("r06", "Temporary supervised R06 registered validation"),
    ):
        target = packets / f"{key}.json"
        require(target.exists(), f"missing task-loaded packet: {key}")
        packet = json.loads(target.read_text())
        require(heading in packet["instructions"], f"lost assignment instructions: {key}")
        require(f"ops/config/task-boot/{key}.json" in source,
                f"native entrypoint cannot resolve {key}")
        require("This block grants no" not in source, "validator body still always loaded")
    claude = (REPO / "CLAUDE.md").read_text()
    require("ops/config/task-boot/dell-migration.json" in claude,
            "Claude cannot resolve Dell migration packet")
    require("ops/config/task-boot/" not in claude.split("## Dell migration trigger")[0],
            "unrelated Claude worker loads assignment procedures")
    require("machine_migrated_pending_record_closeout" not in claude,
            "Dell procedure still always loaded")

    print("agent boot contract selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
