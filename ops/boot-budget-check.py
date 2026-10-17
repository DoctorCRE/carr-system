#!/usr/bin/env python3
"""Report standing instruction sizes. This report does not gate CI."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for name in ("AGENTS.md", "CLAUDE.md"):
    path = root / name
    if path.is_file():
        size = len(path.read_bytes())
        print(f"{name}: {size} bytes, approximately {(size + 3) // 4} tokens")
