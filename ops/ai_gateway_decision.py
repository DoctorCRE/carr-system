#!/usr/bin/env python3
"""Validate the fixed, D1-only evidence decision for an absent AI gateway.

This is deliberately a decision record, not a provider adapter or gateway.  It
does not prepare requests, read credentials, choose a fallback, or contact any
service.  A future live boundary needs both new pinned evidence and a new shape
decision before this declined record can change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DECISION_PATH = ROOT / "evals" / "ai" / "gateway-decision.v1.json"
DECISION_FIXTURE_SHA256 = "5c64fd4a7f5f42be36215a4244f349285642df9eb26e01195c3d01a97b0cb5ca"
REQUIRED_BINDING_PATHS = (
    "evals/ai/synthetic-observed-run.v1.json",
    "evals/ai/model-boundary.v1.json",
)
ABSENT_RUNTIME_PATHS = (
    "ops/ai_gateway.py",
    "ops/ai_provider_transport.py",
    "ops/ai_gateway_fallback.py",
)
ARTIFACT_FIELDS = {
    "schema_version",
    "artifact_type",
    "data_class",
    "execution",
    "calls_models",
    "writes_records",
    "allowed_actions",
    "decision_id",
    "evidence_bindings",
    "provider_evidence",
    "comparison",
    "decision",
    "runtime_absence",
    "future_gate",
}
BINDING_FIELDS = {"path", "sha256"}
PROVIDER_EVIDENCE_FIELDS = {
    "source_path", "source_digest", "run_id", "provider_id", "model_id", "route_id",
}
COMPARISON_FIELDS = {"status", "reason", "provider_evidence_count"}
RUNTIME_ABSENCE_FIELDS = {"gateway_runtime", "provider_transport", "fallback_runtime"}
SHA256_HEX_LENGTH = 64


class GatewayDecisionError(ValueError):
    """The evidence-only gateway decision is invalid or stale."""


def _exact_object(value: Any, fields: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GatewayDecisionError(message)
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_digest(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _exact_integer(value: Any, expected: int) -> bool:
    """JSON booleans and floats must not satisfy integer contract fields."""
    return type(value) is int and value == expected


def _repo_file(relative_path: str) -> Path:
    target = (ROOT / relative_path).resolve()
    if ROOT.resolve() not in target.parents or not target.is_file():
        raise GatewayDecisionError("gateway evidence binding path is invalid")
    return target


def _validate_bindings(value: Any) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != len(REQUIRED_BINDING_PATHS):
        raise GatewayDecisionError("gateway evidence bindings are invalid")
    bindings: dict[str, str] = {}
    for row in value:
        binding = _exact_object(row, BINDING_FIELDS, "gateway evidence binding fields are invalid")
        path, digest = binding["path"], binding["sha256"]
        if not isinstance(path, str) or not _is_sha256(digest) or path in bindings:
            raise GatewayDecisionError("gateway evidence bindings are invalid")
        target = _repo_file(path)
        if _sha256_bytes(target.read_bytes()) != digest:
            raise GatewayDecisionError("gateway evidence bindings are stale")
        bindings[path] = digest
    if tuple(bindings) != REQUIRED_BINDING_PATHS:
        raise GatewayDecisionError("gateway evidence bindings do not cover the fixed evidence set")
    return bindings


def _load_bound_json(relative_path: str) -> dict[str, Any]:
    try:
        value = json.loads(_repo_file(relative_path).read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise GatewayDecisionError("gateway bound evidence cannot be loaded") from exc
    if not isinstance(value, dict):
        raise GatewayDecisionError("gateway bound evidence is invalid")
    return value


def _validate_provider_evidence(value: Any, bindings: dict[str, str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 1:
        raise GatewayDecisionError("gateway provider comparison requires exactly one pinned provider evidence row")
    entry = _exact_object(value[0], PROVIDER_EVIDENCE_FIELDS, "gateway provider evidence fields are invalid")
    if not all(_nonempty_string(entry[field]) for field in PROVIDER_EVIDENCE_FIELDS):
        raise GatewayDecisionError("gateway provider evidence is invalid")
    source_path = entry["source_path"]
    if source_path != REQUIRED_BINDING_PATHS[0] or entry["source_digest"] != bindings.get(source_path):
        raise GatewayDecisionError("gateway provider evidence does not bind the observed run")
    observed_run = _load_bound_json(source_path)
    attribution = observed_run.get("attribution")
    expected = {
        "source_path": source_path,
        "source_digest": bindings[source_path],
        "run_id": observed_run.get("run_id"),
        "provider_id": attribution.get("provider_id") if isinstance(attribution, dict) else None,
        "model_id": attribution.get("model_id") if isinstance(attribution, dict) else None,
        "route_id": attribution.get("route_id") if isinstance(attribution, dict) else None,
    }
    if entry != expected:
        raise GatewayDecisionError("gateway provider evidence does not exactly project the observed run")
    return [entry]


def _validate_bound_d1_evidence(bindings: dict[str, str]) -> None:
    observed_run = _load_bound_json(REQUIRED_BINDING_PATHS[0])
    model_boundary = _load_bound_json(REQUIRED_BINDING_PATHS[1])
    for artifact in (observed_run, model_boundary):
        if (
            artifact.get("data_class") != "synthetic_only"
            or artifact.get("execution") != "offline_deterministic"
            or artifact.get("calls_models") is not False
            or artifact.get("writes_records") is not False
            or artifact.get("allowed_actions") != []
        ):
            raise GatewayDecisionError("gateway bound evidence is not D1 evidence-only")
    if (
        observed_run.get("suite_id") != model_boundary.get("suite_id")
        or observed_run.get("suite_digest") != _canonical_digest(model_boundary)
    ):
        raise GatewayDecisionError("gateway observed run does not bind the model boundary")
    if bindings[REQUIRED_BINDING_PATHS[0]] != _sha256_bytes(_repo_file(REQUIRED_BINDING_PATHS[0]).read_bytes()):
        raise GatewayDecisionError("gateway observed evidence is stale")


def _validate_runtime_absence(value: Any) -> dict[str, bool]:
    absence = _exact_object(value, RUNTIME_ABSENCE_FIELDS, "gateway runtime absence fields are invalid")
    if any(absence[field] is not False for field in RUNTIME_ABSENCE_FIELDS):
        raise GatewayDecisionError("gateway runtime absence claim is invalid")
    if any((ROOT / path).exists() for path in ABSENT_RUNTIME_PATHS):
        raise GatewayDecisionError("gateway runtime absence claim is stale")
    return absence


def validate_gateway_decision(artifact: Any) -> dict[str, Any]:
    """Fail closed unless a decision exactly records the current D1 evidence."""
    document = _exact_object(artifact, ARTIFACT_FIELDS, "gateway decision fields are invalid")
    if (
        not _exact_integer(document["schema_version"], 1)
        or document["artifact_type"] != "synthetic_ai_gateway_evidence_decision"
        or document["data_class"] != "synthetic_only"
        or document["execution"] != "offline_deterministic"
        or document["calls_models"] is not False
        or document["writes_records"] is not False
        or document["allowed_actions"] != []
        or document["decision_id"] != "carr-ai-gateway-decision-v1"
    ):
        raise GatewayDecisionError("gateway decision is not the fixed D1 evidence-only contract")
    bindings = _validate_bindings(document["evidence_bindings"])
    provider_evidence = _validate_provider_evidence(document["provider_evidence"], bindings)
    _validate_bound_d1_evidence(bindings)
    comparison = _exact_object(document["comparison"], COMPARISON_FIELDS, "gateway comparison fields are invalid")
    if (
        not _exact_integer(comparison["provider_evidence_count"], 1)
        or comparison != {
            "status": "unavailable",
            "reason": "insufficient_pinned_provider_evidence",
            "provider_evidence_count": 1,
        }
    ):
        raise GatewayDecisionError("gateway comparison evidence is unavailable or insufficient")
    if document["decision"] != "declined":
        raise GatewayDecisionError("gateway adoption prerequisites are not met")
    absence = _validate_runtime_absence(document["runtime_absence"])
    if document["future_gate"] != "new_pinned_provider_evidence_and_new_gateway_shape_decision_required":
        raise GatewayDecisionError("gateway future gate is invalid")
    return {
        "decision_id": document["decision_id"],
        "evidence_bindings": [{"path": path, "sha256": bindings[path]} for path in REQUIRED_BINDING_PATHS],
        "provider_evidence": [dict(row) for row in provider_evidence],
        "comparison": dict(comparison),
        "decision": document["decision"],
        "runtime_absence": dict(absence),
        "future_gate": document["future_gate"],
    }


def load_gateway_decision() -> dict[str, Any]:
    """Load only the fixed repository decision after checking its complete digest."""
    try:
        raw = DECISION_PATH.read_bytes()
    except OSError as exc:
        raise GatewayDecisionError("gateway decision fixture cannot be loaded") from exc
    if _sha256_bytes(raw) != DECISION_FIXTURE_SHA256:
        raise GatewayDecisionError("gateway decision fixture does not match its fixed binding")
    try:
        artifact = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise GatewayDecisionError("gateway decision fixture cannot be parsed") from exc
    return validate_gateway_decision(artifact)


def decide_gateway() -> dict[str, Any]:
    """Return a deterministic, redacted declined decision; never a gateway request."""
    validated = load_gateway_decision()
    return {
        "schema_version": 1,
        "state": "declined",
        "decision": "declined",
        "decision_id": validated["decision_id"],
        "comparison": validated["comparison"],
        "provider_inventory": [
            {field: row[field] for field in ("provider_id", "model_id", "route_id", "run_id")}
            for row in validated["provider_evidence"]
        ],
        "evidence_bindings": validated["evidence_bindings"],
        "runtime_absence": validated["runtime_absence"],
        "future_gate": validated["future_gate"],
        "calls_models": False,
        "writes_records": False,
        "allowed_actions": [],
    }


if __name__ == "__main__":
    print(json.dumps(decide_gateway(), sort_keys=True))
