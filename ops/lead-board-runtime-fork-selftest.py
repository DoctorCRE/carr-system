#!/usr/bin/env python3
"""Guard the deliberate Lead Board runtime fork from a stale equality gate.

The primary generator is record-native; the shared helper is the cloud-only
file-mode runtime. The manifest records their deliberate fork.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = (ROOT / "manifest.tsv").read_text(encoding="utf-8")
FORK_DECLARATION = "# FORKED BY DESIGN 2026-08-11:"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    require(FORK_DECLARATION in MANIFEST,
            "manifest.tsv must declare the Lead Board runtime fork")
    print("lead-board runtime fork selftest: passed")


if __name__ == "__main__":
    main()
