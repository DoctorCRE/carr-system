#!/usr/bin/env python3
"""Mutation tests for the policy-aware rule-delivery activation overlay."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_activation import EXPECTED_IDS, effective_map, validate_overlay  # noqa:E402

MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
OVERLAY = REPO / "ops" / "config" / "rule-delivery-activation-overlay.v1.json"
base_bytes = MAP.read_bytes()
base = json.loads(base_bytes)
overlay = json.loads(OVERLAY.read_text())
failures: list[str] = []


def expect(label: str, errors: list[str], fragment: str | None) -> None:
    matched = not errors if fragment is None else any(fragment in e for e in errors)
    if not matched:
        failures.append(f"{label}: expected {fragment!r}, got {errors}")


expect("checked-in overlay", validate_overlay(base, overlay, base_bytes), None)
shadow = effective_map("shadow")
enforced = effective_map("enforced")
if shadow != base:
    failures.append("shadow mode changed the reviewed base map")
changed = {rid for rid in base["rule_controls"]
           if base["rule_controls"][rid] != enforced["rule_controls"][rid]}
if changed != EXPECTED_IDS:
    failures.append(f"enforced mode changed {sorted(changed)}, not the exact eight")
for rid in EXPECTED_IDS:
    row = enforced["rule_controls"][rid]
    if (row.get("control"), row.get("enforcement_class")) != ("pack_delivery", "stop_gate"):
        failures.append(f"{rid}: effective control is not pack_delivery/stop_gate")

mutated = copy.deepcopy(overlay)
mutated["targets"].pop()
expect("missing target", validate_overlay(base, mutated, base_bytes), "exact eight")
mutated = copy.deepcopy(overlay)
mutated["targets"][0]["pack"] = "joe-comms"
expect("wrong pack", validate_overlay(base, mutated, base_bytes), "single-pack")
mutated = copy.deepcopy(overlay)
mutated["base_map_sha256"] = "0" * 64
expect("stale digest", validate_overlay(base, mutated, base_bytes), "digest drifted")
mutated_base = copy.deepcopy(base)
mutated_base["rule_controls"]["25fcddee"]["control"] = "something_else"
mutated_bytes = json.dumps(mutated_base).encode()
mutated_overlay = copy.deepcopy(overlay)
mutated_overlay["base_map_sha256"] = hashlib.sha256(mutated_bytes).hexdigest()
expect("wrong preimage", validate_overlay(mutated_base, mutated_overlay, mutated_bytes),
       "control preimage")
try:
    effective_map("invalid")
except ValueError:
    pass
else:
    failures.append("unknown policy mode was accepted")

if failures:
    print("rule-delivery-activation-overlay-selftest: FAIL")
    for failure in failures:
        print("  " + failure)
    raise SystemExit(1)
print("rule-delivery-activation-overlay-selftest: 9 cases passed")
