#!/usr/bin/env python3
"""Offline selection of a typed, synthetic read-only route.

The router returns a descriptor only.  It has no transport, database, or
tool-invocation path; its fixed policy and server attribution live in code.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import ai_eval


REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "evals" / "ai" / "function-router.v1.json"
BOUND_SUITE_PATH = REPO_ROOT / "evals" / "ai" / "model-boundary.v1.json"
POLICY_SHA256 = "4e3c0678f1afa755f5ccb8eb10f9388f397fb5e0d5970ba06bb9e1d154f310fd"
ACTION_RISK_REGISTRY_PATH = REPO_ROOT / "control-room" / "contracts" / "action-risk-registry.v1.json"
SERVER_CONTEXT = {
    "organization_tenant_id": "carr-internal",
    "runtime_principal": "synthetic-router-v1",
    "sponsoring_human_id": "synthetic-sponsor-001",
    "capability_profile": "read_only",
}
POLICY_FIELDS = {
    "schema_version", "artifact_type", "data_class", "execution", "calls_models",
    "writes_records", "allowed_actions", "routes", "denied_targets",
}
TOOL_SEMANTIC_FIELDS = {"tool_name", "write", "full_only", "input_schema"}
ROUTE_FIELDS = TOOL_SEMANTIC_FIELDS | {"protection"}
DENIED_TARGET_FIELDS = {"tool_name", "write", "full_only"}
PROPOSAL_FIELDS = {"schema_version", "tool_name", "arguments"}
FORBIDDEN_AUTHORITY_FIELDS = {
    "target", "organization_tenant_id", "tenant_id", "identity", "actor",
    "runtime_principal", "sponsoring_human_id", "profile", "capability", "capabilities",
    "write", "action", "actions", "allowed_actions",
}
ROUTER_VIOLATION_CODES = {
    "router_policy_invalid",
    "router_proposal_not_object",
    "router_proposal_missing_fields",
    "router_proposal_unknown_fields",
    "router_authority_field_forbidden",
    "router_schema_version_invalid",
    "router_unknown_tool",
    "router_write_target_forbidden",
    "router_sensitive_target_forbidden",
    "router_envelope_evidence_invalid",
    "router_arguments_missing",
    "router_arguments_unknown",
    "router_arguments_type_invalid",
}
SCHEMA_FIELDS = {
    "type", "properties", "required", "items", "enum", "description", "default",
    "additionalProperties", "minimum", "maximum", "minLength", "maxLength",
}
SUPPORTED_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "object", "array"}


class RouterError(ValueError):
    """A fixed policy or bound evaluation artifact is invalid."""


def _exact_object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise RouterError(f"{label} must contain its exact v1 fields")
    return value


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _detached(value: Any) -> Any:
    """Return a JSON-value copy with no caller or module-owned aliases."""
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _validate_schema(schema: Any) -> None:
    if not isinstance(schema, dict) or not set(schema).issubset(SCHEMA_FIELDS):
        raise RouterError("route input_schema is unsupported")
    schema_type = schema.get("type")
    if schema_type not in SUPPORTED_SCHEMA_TYPES:
        raise RouterError("route input_schema type is unsupported")
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or not schema["enum"]
        or len(schema["enum"]) != len(set(json.dumps(item, sort_keys=True) for item in schema["enum"]))
    ):
        raise RouterError("route input_schema enum is invalid")
    for field in ("minimum", "maximum"):
        if field in schema and (
            schema_type not in {"integer", "number"}
            or isinstance(schema[field], bool)
            or not isinstance(schema[field], (int, float))
            or not math.isfinite(schema[field])
        ):
            raise RouterError("route numeric schema bound is invalid")
    for field in ("minLength", "maxLength"):
        if field in schema and (
            schema_type != "string" or isinstance(schema[field], bool)
            or not isinstance(schema[field], int) or schema[field] < 0
        ):
            raise RouterError("route string schema bound is invalid")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise RouterError("route object schema is invalid")
        if not all(isinstance(key, str) and key for key in properties):
            raise RouterError("route object schema has invalid property names")
        if not all(isinstance(key, str) and key in properties for key in required):
            raise RouterError("route object schema has invalid required fields")
        if len(required) != len(set(required)):
            raise RouterError("route object schema repeats required fields")
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
            raise RouterError("route object schema has invalid additionalProperties")
        for child in properties.values():
            _validate_schema(child)
    elif schema_type == "array":
        if "items" not in schema:
            raise RouterError("route array schema requires items")
        _validate_schema(schema["items"])
    elif any(field in schema for field in ("properties", "required", "items", "additionalProperties")):
        raise RouterError("route scalar schema has object or array fields")


def _validate_policy(policy: Any) -> dict[str, Any]:
    artifact = _exact_object(policy, POLICY_FIELDS, "router policy")
    if artifact["schema_version"] != 1 or artifact["artifact_type"] != "synthetic_read_only_router_policy":
        raise RouterError("router policy schema is unsupported")
    if artifact["data_class"] != "synthetic_only" or artifact["execution"] != "offline_deterministic":
        raise RouterError("router policy is not D1 offline data")
    if artifact["calls_models"] is not False or artifact["writes_records"] is not False:
        raise RouterError("router policy cannot call models or write records")
    if artifact["allowed_actions"] != []:
        raise RouterError("router policy cannot authorize actions")
    try:
        risk_rows = json.loads(ACTION_RISK_REGISTRY_PATH.read_text())["verbs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RouterError("action risk registry cannot be read") from exc
    if not isinstance(artifact["routes"], list) or len(artifact["routes"]) < 2:
        raise RouterError("router policy needs two safe routes")
    names: set[str] = set()
    for row in artifact["routes"]:
        route = _exact_object(row, ROUTE_FIELDS, "router route")
        name = route["tool_name"]
        if not _nonempty_string(name) or name in names:
            raise RouterError("router routes must have unique names")
        names.add(name)
        if route["write"] is not False or route["full_only"] is not False or route["protection"] != "read_only":
            raise RouterError("router route is not safe for read-only selection")
        _validate_schema(route["input_schema"])
        risk = risk_rows.get(name)
        if (
            not isinstance(risk, dict)
            or risk.get("write") != route["write"]
            or risk.get("protection") != route["protection"]
        ):
            raise RouterError("router route does not match action risk evidence")
    if not isinstance(artifact["denied_targets"], list):
        raise RouterError("router denied targets are invalid")
    denied_names: set[str] = set()
    for row in artifact["denied_targets"]:
        denied = _exact_object(row, DENIED_TARGET_FIELDS, "router denied target")
        if (
            not _nonempty_string(denied["tool_name"])
            or denied["tool_name"] in names
            or denied["tool_name"] in denied_names
        ):
            raise RouterError("router denied target is invalid")
        denied_names.add(denied["tool_name"])
        if not isinstance(denied["write"], bool) or not isinstance(denied["full_only"], bool):
            raise RouterError("router denied target flags are invalid")
    return artifact


def _load_fixed_policy() -> dict[str, Any]:
    if _sha256(POLICY_PATH) != POLICY_SHA256:
        raise RouterError("router policy file digest does not match the fixed binding")
    try:
        policy = json.loads(POLICY_PATH.read_text())
    except (OSError, ValueError) as exc:
        raise RouterError("router policy cannot be loaded") from exc
    return _validate_policy(policy)


def _load_bound_suite() -> dict[str, Any]:
    try:
        return ai_eval.load_suite(BOUND_SUITE_PATH)
    except (ai_eval.SuiteError, OSError, ValueError) as exc:
        raise RouterError("bound response envelope suite cannot be loaded") from exc


def _accepted_envelope_binding(response_envelope: Any) -> dict[str, str] | None:
    try:
        suite = _load_bound_suite()
        validated = ai_eval.validate_response_envelope(suite, response_envelope)
        evaluated = ai_eval.evaluate_response_envelope(suite, response_envelope)
    except (ai_eval.SuiteError, RouterError, TypeError, ValueError):
        return None
    if (
        validated.get("state") != "accepted"
        or evaluated.get("state") != "accepted"
        or evaluated.get("evaluation", {}).get("passed") is not True
    ):
        return None
    return {
        "suite_digest": suite["_digest"],
        "case_id": validated["case_id"],
        "envelope_digest": _canonical_digest(response_envelope),
    }


def _schema_matches(value: Any, schema: dict[str, Any]) -> bool:
    schema_type = schema["type"]
    if schema_type == "string":
        matches = isinstance(value, str)
    elif schema_type == "boolean":
        matches = isinstance(value, bool)
    elif schema_type == "integer":
        matches = isinstance(value, int) and not isinstance(value, bool)
    elif schema_type == "number":
        matches = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    elif schema_type == "array":
        matches = isinstance(value, list) and all(_schema_matches(item, schema["items"]) for item in value)
    else:
        if not isinstance(value, dict):
            return False
        properties = schema["properties"]
        if set(value) - set(properties):
            return False
        if any(key not in value for key in schema.get("required", [])):
            return False
        matches = all(_schema_matches(item, properties[key]) for key, item in value.items())
    if not matches or ("enum" in schema and value not in schema["enum"]):
        return False
    if schema_type in {"integer", "number"}:
        return (("minimum" not in schema or value >= schema["minimum"])
                and ("maximum" not in schema or value <= schema["maximum"]))
    if schema_type == "string":
        return (("minLength" not in schema or len(value) >= schema["minLength"])
                and ("maxLength" not in schema or len(value) <= schema["maxLength"]))
    return True


def _refuse(code: str) -> dict[str, Any]:
    if code not in ROUTER_VIOLATION_CODES:
        raise RouterError("router emitted an undeclared violation code")
    return {"state": "refused", "violation_codes": [code]}


def route_read_only(proposal: Any, response_envelope: Any) -> dict[str, Any]:
    """Return a descriptor only for a typed read route with a passing bound envelope."""
    try:
        artifact = _load_fixed_policy()
    except RouterError:
        return _refuse("router_policy_invalid")
    envelope_binding = _accepted_envelope_binding(response_envelope)
    if envelope_binding is None:
        return _refuse("router_envelope_evidence_invalid")
    if not isinstance(proposal, dict):
        return _refuse("router_proposal_not_object")
    if set(proposal) & FORBIDDEN_AUTHORITY_FIELDS:
        return _refuse("router_authority_field_forbidden")
    if PROPOSAL_FIELDS - set(proposal):
        return _refuse("router_proposal_missing_fields")
    if set(proposal) - PROPOSAL_FIELDS:
        return _refuse("router_proposal_unknown_fields")
    if proposal["schema_version"] != 1:
        return _refuse("router_schema_version_invalid")
    name = proposal["tool_name"]
    if not isinstance(name, str):
        return _refuse("router_unknown_tool")
    allowed = {row["tool_name"]: row for row in artifact["routes"]}
    denied = {row["tool_name"]: row for row in artifact["denied_targets"]}
    if name not in allowed:
        target = denied.get(name)
        if target and target["write"]:
            return _refuse("router_write_target_forbidden")
        if target and target["full_only"]:
            return _refuse("router_sensitive_target_forbidden")
        return _refuse("router_unknown_tool")
    arguments = proposal["arguments"]
    if not isinstance(arguments, dict):
        return _refuse("router_arguments_type_invalid")
    schema = allowed[name]["input_schema"]
    properties = schema["properties"]
    if set(arguments) - set(properties):
        return _refuse("router_arguments_unknown")
    if any(field not in arguments for field in schema.get("required", [])):
        return _refuse("router_arguments_missing")
    if not _schema_matches(arguments, schema):
        return _refuse("router_arguments_type_invalid")
    return {
        "state": "accepted",
        "route": {"tool_name": name, "arguments": _detached(arguments)},
        "attribution": _detached(SERVER_CONTEXT),
        "envelope_binding": _detached(envelope_binding),
        "calls_models": False,
        "writes_records": False,
        "allowed_actions": [],
    }
