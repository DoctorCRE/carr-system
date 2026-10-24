#!/usr/bin/env python3
"""Validate closed D2 dispositions against the frozen phase-1 risk baseline.

This is intentionally a contract validator, not a dispatcher or policy engine.
It has no record-layer, network, or client-payload path: it only proves that the
known baseline NONE rows are owned and the selected historical remediation is
bound to that baseline. It does not establish current handler or risk coverage;
current handler existence belongs to scac-mutation-registry.current.generated.js.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = ROOT / "control-room" / "contracts" / "action-risk-dispositions.v1.json"
REGISTRY_PATH = ROOT / "control-room" / "contracts" / "action-risk-registry.v1.json"

BASELINE_NONE_ROWS = frozenset({
    "complete-action", "detach-decision", "end-deal-review", "link-parties",
    "measure-placement", "register-template", "resolve-conflict", "set-lead",
    "set-next-action", "set-next-step", "triage-item", "update-decision",
    "update-document-status",
})
ARTIFACT_FIELDS = {
    "contract", "version", "status", "artifact_type", "data_class", "action_risk_registry",
    "selected_slice", "dispositions",
}
BINDING_FIELDS = {"path", "sha256"}
ROW_FIELDS = {
    "tool_name", "baseline_protection", "owner", "target_control", "evidence_category",
    "evidence", "priority", "selected_slice",
}
PRIORITIES = {"P0", "P1", "P2"}
SELECTED_TOOL = "set-lead"
EXPECTED_ROWS = {
    "complete-action": ("workflow-actions", "atomic_state_predicate", "verified_runtime_handler", "mcp-server/src/tools.js#complete-action", "P1", False),
    "detach-decision": ("decision-governance", "native_optimistic_concurrency", "queued_native_guard", "mcp-server/src/tools.js#detach-decision", "P1", False),
    "end-deal-review": ("deal-review", "atomic_state_predicate", "verified_runtime_handler", "mcp-server/src/tools.js#end-deal-review", "P1", False),
    "link-parties": ("relationship-integrity", "native_optimistic_concurrency", "queued_native_guard", "mcp-server/src/tools.js#link-parties", "P1", False),
    "measure-placement": ("marketing-measurement", "atomic_state_predicate", "verified_runtime_handler", "mcp-server/src/tools.js#measure-placement", "P1", False),
    "register-template": ("document-governance", "native_optimistic_concurrency", "queued_native_guard", "mcp-server/src/tools.js#register-template", "P1", False),
    "resolve-conflict": ("dealroom-concurrency", "transaction_row_lock", "verified_runtime_handler", "mcp-server/src/tools.js#resolve-conflict", "P1", False),
    "set-lead": ("deal-ownership", "human_boundary_and_optimistic_concurrency", "verified_runtime_handler", "mcp-server/test/action-risk-guardrails.test.mjs", "P0", True),
    "set-next-action": ("workflow-actions", "native_optimistic_concurrency", "queued_native_guard", "mcp-server/src/tools.js#set-next-action", "P1", False),
    "set-next-step": ("dealroom-concurrency", "transaction_row_lock", "verified_runtime_handler", "mcp-server/src/tools.js#set-next-step", "P1", False),
    "triage-item": ("inbox-triage", "atomic_state_predicate", "verified_runtime_handler", "mcp-server/src/tools.js#triage-item", "P1", False),
    "update-decision": ("decision-governance", "native_optimistic_concurrency", "queued_native_guard", "mcp-server/src/tools.js#update-decision", "P1", False),
    "update-document-status": ("document-governance", "native_optimistic_concurrency", "queued_native_guard", "mcp-server/src/tools.js#update-document-status", "P1", False),
}


class DispositionError(ValueError):
    """Closed, non-payload-bearing disposition contract error."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact_object(value: Any, fields: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise DispositionError(message)
    return value


def _validate_row(row: Any) -> dict[str, Any]:
    item = _exact_object(row, ROW_FIELDS, "disposition row fields are invalid")
    if not isinstance(item["tool_name"], str) or not item["tool_name"]:
        raise DispositionError("disposition tool name is invalid")
    if item["baseline_protection"] != "NONE":
        raise DispositionError("baseline protection is invalid")
    if item["priority"] not in PRIORITIES:
        raise DispositionError("disposition priority is invalid")
    if not isinstance(item["selected_slice"], bool):
        raise DispositionError("selected slice marker is invalid")
    if not isinstance(item["evidence"], str) or not item["evidence"]:
        raise DispositionError("disposition evidence is invalid")
    expected = EXPECTED_ROWS.get(item["tool_name"])
    actual = (
        item["owner"], item["target_control"], item["evidence_category"],
        item["evidence"], item["priority"], item["selected_slice"],
    )
    if expected is None or actual != expected:
        raise DispositionError("disposition owner or control is invalid")
    return item


def validate_dispositions(artifact: Any, registry: Any, registry_bytes: bytes) -> dict[str, Any]:
    """Validate exact historical coverage against the frozen phase-1 baseline."""
    doc = _exact_object(artifact, ARTIFACT_FIELDS, "disposition artifact fields are invalid")
    if (doc["contract"] != "carr-action-risk-dispositions" or doc["version"] != "1.0.0"
            or doc["status"] != "phase1_guardrails"
            or doc["artifact_type"] != "carr_action_risk_dispositions"):
        raise DispositionError("disposition artifact version is invalid")
    if doc["data_class"] != "D2_internal_metadata_only":
        raise DispositionError("disposition artifact data class is invalid")
    if doc["selected_slice"] != SELECTED_TOOL:
        raise DispositionError("selected slice is invalid")
    binding = _exact_object(doc["action_risk_registry"], BINDING_FIELDS, "registry binding is invalid")
    if binding["path"] != "control-room/contracts/action-risk-registry.v1.json":
        raise DispositionError("registry path is invalid")
    if not isinstance(binding["sha256"], str) or binding["sha256"] != _digest(registry_bytes):
        raise DispositionError("registry digest is invalid")
    if not isinstance(registry, dict) or not isinstance(registry.get("verbs"), dict):
        raise DispositionError("generated registry is invalid")

    rows = doc["dispositions"]
    if not isinstance(rows, list):
        raise DispositionError("disposition rows are invalid")
    validated = [_validate_row(row) for row in rows]
    names = [row["tool_name"] for row in validated]
    if len(set(names)) != len(names):
        raise DispositionError("disposition rows contain duplicates")
    if set(names) != BASELINE_NONE_ROWS:
        raise DispositionError("baseline coverage is invalid")
    if [row["tool_name"] for row in validated if row["selected_slice"]] != [SELECTED_TOOL]:
        raise DispositionError("selected slice coverage is invalid")

    for row in validated:
        runtime = registry["verbs"].get(row["tool_name"])
        if not isinstance(runtime, dict) or runtime.get("write") is not True:
            raise DispositionError("baseline runtime row is invalid")
        if row["selected_slice"]:
            if runtime.get("protection") != "human_boundary" or runtime.get("binds_both_partners") is not True:
                raise DispositionError("selected runtime protection is invalid")
            if runtime.get("base_version_required") is not True:
                raise DispositionError("selected base_version protection is invalid")
        elif runtime.get("protection") != "NONE":
            raise DispositionError("unselected runtime protection drifted")
    return {"status": "valid", "selected_slice": SELECTED_TOOL, "rows": len(validated),
            "coverage": "historical_phase1_baseline",
            "current_handler_inventory": "mcp-server/src/scac-mutation-registry.current.generated.js"}


def load_and_validate() -> dict[str, Any]:
    """Read only the fixed repository contract files and return redacted status."""
    try:
        artifact = json.loads(ARTIFACT_PATH.read_text())
        raw_registry = REGISTRY_PATH.read_bytes()
        registry = json.loads(raw_registry)
    except (OSError, ValueError, TypeError) as exc:
        raise DispositionError("action-risk contract files cannot be loaded") from exc
    return validate_dispositions(artifact, registry, raw_registry)


if __name__ == "__main__":
    print(json.dumps(load_and_validate(), sort_keys=True))
