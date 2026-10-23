#!/usr/bin/env python3
"""Integrity tests for task-loaded native assignment packets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
AGENTS = REPO / "AGENTS.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    source = AGENTS.read_text(encoding="utf-8")

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
