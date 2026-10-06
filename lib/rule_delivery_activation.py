"""Validated, policy-aware view of the eight rule-delivery activation changes.

The reviewed base map is pinned by the pending situation-curation review.  The
activation decision therefore lives in a small overlay instead of rewriting
that reviewed artifact.  Shadow mode returns the base map byte-for-byte in
meaning; enforced mode changes only the exact reviewed eight controls.

581cb3fe was one of the original nine reviewed ids until the WR-000019 batch
retired it 2026-08-27 (superseded_by aa411351); it is no longer an active
rule at all, so it dropped out of both EXPECTED_IDS and the checked-in
overlay's targets in the same change that removed it from the map.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
MAP_PATH = REPO / "ops" / "config" / "rule-enforcement-map.json"
OVERLAY_PATH = REPO / "ops" / "config" / "rule-delivery-activation-overlay.v1.json"
EXPECTED_IDS = {
    "25fcddee", "3fa17fa0", "72e06bdf", "113b3833",
    "57d13061", "c66dc739", "49533583", "557838a5",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top level must be an object")
    return value


def validate_overlay(base: dict[str, Any], overlay: dict[str, Any],
                     map_bytes: bytes) -> list[str]:
    errors: list[str] = []
    if overlay.get("schema") != "rule-delivery-activation-overlay/v1":
        errors.append("overlay schema is not rule-delivery-activation-overlay/v1")
    actual_digest = hashlib.sha256(map_bytes).hexdigest()
    if overlay.get("base_map_sha256") != actual_digest:
        errors.append(
            f"base map digest drifted: overlay={overlay.get('base_map_sha256')} "
            f"actual={actual_digest}")
    targets = overlay.get("targets")
    if not isinstance(targets, list):
        return errors + ["overlay targets must be a list"]
    ids = [row.get("short_id") for row in targets if isinstance(row, dict)]
    if len(targets) != 8 or set(ids) != EXPECTED_IDS or len(ids) != len(set(ids)):
        errors.append("overlay must name the exact eight activation targets once each")
    scopes = {rid: scope for scope, rule_ids in base.get("active_rule_ids", {}).items()
              for rid in rule_ids}
    for row in targets:
        if not isinstance(row, dict):
            errors.append("every activation target must be an object")
            continue
        rid = row.get("short_id")
        control = base.get("rule_controls", {}).get(rid, {})
        layer = base.get("rule_load_layers", {}).get(rid, {})
        for field in ("scope", "pack", "from_control", "from_enforcement_class",
                      "to_control", "to_enforcement_class"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{rid}: overlay {field} must be non-empty")
        if scopes.get(rid) != row.get("scope"):
            errors.append(f"{rid}: scope preimage differs from the reviewed map")
        if layer.get("load_layer") != "pack" or layer.get("packs") != [row.get("pack")]:
            errors.append(f"{rid}: must be a single-pack delivery rule in {row.get('pack')!r}")
        if control.get("control") != row.get("from_control"):
            errors.append(f"{rid}: control preimage differs from the reviewed map")
        if control.get("enforcement_class") != row.get("from_enforcement_class"):
            errors.append(f"{rid}: class preimage differs from the reviewed map")
        if row.get("to_control") != "pack_delivery" or row.get("to_enforcement_class") != "stop_gate":
            errors.append(f"{rid}: activation target must be pack_delivery/stop_gate")
    return errors


def load_validated(map_path: Path = MAP_PATH,
                   overlay_path: Path = OVERLAY_PATH) -> tuple[dict[str, Any], dict[str, Any]]:
    map_bytes = map_path.read_bytes()
    base = json.loads(map_bytes)
    overlay = load_json(overlay_path)
    errors = validate_overlay(base, overlay, map_bytes)
    if errors:
        raise ValueError("; ".join(errors))
    return base, overlay


def effective_map(mode: str, map_path: Path = MAP_PATH,
                  overlay_path: Path = OVERLAY_PATH) -> dict[str, Any]:
    if mode not in {"shadow", "enforced"}:
        raise ValueError(f"unknown rule-delivery mode {mode!r}")
    base, overlay = load_validated(map_path, overlay_path)
    if mode == "shadow":
        return base
    result = copy.deepcopy(base)
    for row in overlay["targets"]:
        entry = result["rule_controls"][row["short_id"]]
        entry["control"] = row["to_control"]
        entry["enforcement_class"] = row["to_enforcement_class"]
    return result
