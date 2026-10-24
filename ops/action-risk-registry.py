#!/usr/bin/env python3
"""Emit the frozen phase-1 action-risk baseline; never reclassify current handlers.

v1 preserves historical verb and disposition evidence. Its provenance identifies
its last artifact change, not the original classification date. Delegated handler
SQL was not traced by the old classifier, so rerunning that heuristic could label
writes append-only with zero SQL counts. It is retired rather than presented as
current risk coverage. Current handler existence belongs to
mcp-server/src/scac-mutation-registry.current.generated.js.

Run: python3 ops/action-risk-registry.py
"""
from pathlib import Path
import sys

BASELINE = Path(__file__).resolve().parent.parent / "control-room/contracts/action-risk-registry.v1.json"


def main():
    sys.stdout.buffer.write(BASELINE.read_bytes())


if __name__ == "__main__":
    main()
