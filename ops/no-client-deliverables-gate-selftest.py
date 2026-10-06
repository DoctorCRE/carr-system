#!/usr/bin/env python3
"""Acceptance test for ops/no-client-deliverables-gate.py's pattern matching.

Exercises ``violations()`` directly against a synthetic path list — no repo
git state, so it stays hermetic and fast. The paths below are invented
shapes, not real client names, on purpose (see that gate's own docstring on
why the patterns are generic).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "no-client-deliverables-gate.py"
spec = importlib.util.spec_from_file_location("no_client_deliverables_gate", MODULE_PATH)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

CLEAN_PATHS = [
    "deliverables/instagram-grid-reset-2026-08-11/README.md",
    "deliverables/instagram-grid-reset-2026-08-11/index.html",
    "dealroom/data/board-seed.json",
    "ops/ci.sh",
    "README.md",
]
assert gate.violations(CLEAN_PATHS) == [], "marketing/engineering paths must never trip the gate"

DIRTY_PATHS = [
    "deliverables/example-clinic-pretour-2027-01-01/packet.html",
    "deliverables/example-clinic-carr-branded-2027-01-01/output.pdf",
    "deliverables/example-industrial-packet-2027-01-01/deal.json",
]
found = gate.violations(CLEAN_PATHS + DIRTY_PATHS)
assert sorted(found) == sorted(DIRTY_PATHS), (found, DIRTY_PATHS)

print("no-client-deliverables-gate-selftest: pattern matching holds for clean and dirty trees")
