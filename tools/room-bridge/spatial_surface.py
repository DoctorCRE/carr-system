"""Shared, deterministic spatial projection and telemetry truth helpers.

These helpers own no work, authority, native session, or user layout.  They
validate optional read projections for any CARR surface and keep stale or
malformed observations from becoming a newer visual truth.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import execution_contract as contract


SPATIAL_FIELDS = {"schema_version", "surface", "projection_digest", "generated_at", "canonical_binding", "nodes", "edges", "semantic_zoom", "home_zone", "list_order"}
TELEMETRY_FIELDS = {"schema_version", "measurement_id", "metric_kind", "unit", "scope", "attribution", "source", "observed_at", "source_window", "freshness", "value"}
EXTENSION_FIELDS = {"schema_version", "extension_id", "version", "api_version", "contributions", "permissions", "package", "provenance", "enablement"}


def _id(value: Any, name: str) -> None:
    contract._string(value, name, identifier=True)


def validate_spatial_surface(value: Any, source_projection: Any | None = None) -> dict:
    """Fail closed and bind a spatial read view to exact canonical evidence."""
    view = contract._expect_exact(value, SPATIAL_FIELDS, "spatial surface")
    if view["schema_version"] != "spatial-surface-projection.v1":
        raise contract.ContractError("unsupported spatial surface schema_version")
    contract._digest(view["projection_digest"], "spatial surface projection_digest")
    if view["projection_digest"] != contract.canonical_digest({k: v for k, v in view.items() if k != "projection_digest"}):
        raise contract.ContractError("spatial surface digest does not bind exact content")
    surface = contract._expect_exact(view["surface"], {"surface_id", "workspace_id", "view_id", "version"}, "spatial surface identity")
    for key, item in surface.items(): contract._string(item, f"spatial surface {key}", identifier=key != "version")
    binding = contract._expect_exact(view["canonical_binding"], {"work_request_id", "state_version", "canonical_record_digest", "source_projection_digest"}, "spatial canonical binding")
    _id(binding["work_request_id"], "spatial work_request_id"); contract._digest(binding["canonical_record_digest"], "spatial canonical digest"); contract._digest(binding["source_projection_digest"], "spatial source digest")
    if not isinstance(binding["state_version"], int) or binding["state_version"] < 1: raise contract.ContractError("spatial state_version must be positive")
    if source_projection is not None:
        source = contract.validate_observatory_projection(source_projection)
        if (binding["work_request_id"], binding["state_version"], binding["canonical_record_digest"], binding["source_projection_digest"]) != (source["work_request_id"], source["source_state"]["state_version"], source["source_state"]["canonical_record_digest"], source["projection_digest"]):
            raise contract.ContractError("spatial surface does not bind exact source projection")
    ids, orders = set(), []
    for node in view["nodes"]:
        row = contract._expect_exact(node, {"node_id", "node_type", "entity_ref", "parent_node_id", "group_ref", "geometry", "presentation", "status", "accessibility", "resource_refs"}, "spatial node")
        _id(row["node_id"], "spatial node_id")
        if row["node_id"] in ids: raise contract.ContractError("spatial node ids must be unique")
        ids.add(row["node_id"])
        if row["node_type"] not in {"work_request", "attempt_lane", "component", "phase", "evidence", "resource"}: raise contract.ContractError("spatial node type is invalid")
        contract._string(row["entity_ref"], "spatial entity_ref")
        geometry = contract._expect_exact(row["geometry"], {"declared_bounds", "derived_bounds", "user_layout_preference"}, "spatial geometry")
        for bounds in (geometry["declared_bounds"], geometry["derived_bounds"]):
            bound = contract._expect_exact(bounds, {"x", "y", "width", "height"}, "spatial bounds")
            if not all(isinstance(bound[key], (int, float)) and not isinstance(bound[key], bool) for key in bound) or bound["width"] <= 0 or bound["height"] <= 0: raise contract.ContractError("spatial bounds are invalid")
        if geometry["user_layout_preference"] is not None: contract._expect_exact(geometry["user_layout_preference"], {"x", "y", "width", "height"}, "user layout preference")
        presentation = contract._expect_exact(row["presentation"], {"focusable", "selected", "home_zone_member", "visual_priority", "needs_attention_reason"}, "spatial presentation")
        if presentation["visual_priority"] not in {"normal", "high", "critical"}: raise contract.ContractError("spatial visual priority is invalid")
        if not all(isinstance(presentation[k], bool) for k in ("focusable", "selected", "home_zone_member")): raise contract.ContractError("spatial presentation booleans invalid")
        status = contract._expect_exact(row["status"], {"state", "freshness", "evidence_refs"}, "spatial status")
        if status["state"] not in {"current", "verified", "stale", "unknown", "blocked", "failed", "partial"} or status["freshness"] not in {"fresh", "stale", "unknown"}: raise contract.ContractError("spatial status invalid")
        contract._list_of_strings(status["evidence_refs"], "spatial status evidence")
        access = contract._expect_exact(row["accessibility"], {"label", "non_color_status_token", "list_order"}, "spatial accessibility")
        contract._string(access["label"], "spatial accessibility label"); contract._string(access["non_color_status_token"], "spatial noncolor token")
        if not isinstance(access["list_order"], int) or access["list_order"] < 0: raise contract.ContractError("spatial list order invalid")
        orders.append(access["list_order"]); contract._list_of_strings(row["resource_refs"], "spatial resource refs")
    if len(orders) != len(set(orders)): raise contract.ContractError("spatial list order must be unique")
    if set(view["list_order"]) != ids: raise contract.ContractError("spatial list parity must name every node exactly once")
    for edge in view["edges"]:
        row = contract._expect_exact(edge, {"edge_id", "from_node_id", "to_node_id", "relationship", "evidence_refs"}, "spatial edge")
        _id(row["edge_id"], "spatial edge_id")
        if row["from_node_id"] not in ids or row["to_node_id"] not in ids: raise contract.ContractError("spatial edge has dangling node")
        if row["relationship"] not in {"contains", "depends_on", "staffed_by", "observed_at", "evidenced_by", "handoff_from"}: raise contract.ContractError("spatial relationship invalid")
    zoom = contract._expect_exact(view["semantic_zoom"], {"overview", "detail"}, "spatial semantic zoom")
    for level in zoom.values():
        row = contract._expect_exact(level, {"max_scale" if level is zoom["overview"] else "min_scale", "summary"}, "spatial zoom level")
        contract._string(row["summary"], "spatial zoom summary")
    home = contract._expect_exact(view["home_zone"], {"home_node_id", "attention_node_ids", "return_label"}, "spatial home zone")
    if home["home_node_id"] not in ids or not any(row["node_id"] == home["home_node_id"] and row["presentation"]["home_zone_member"] for row in view["nodes"]): raise contract.ContractError("spatial home node must be a home-zone node")
    if not set(home["attention_node_ids"]).issubset(ids): raise contract.ContractError("spatial attention node is dangling")
    return view


def select_newer_surface(current: Any | None, incoming: Any) -> Any:
    """CAS-like visual selection: conflicts and stale views never silently win."""
    newer = validate_spatial_surface(incoming)
    if current is None: return newer
    older = validate_spatial_surface(current)
    a, b = older["canonical_binding"], newer["canonical_binding"]
    if a["work_request_id"] != b["work_request_id"]: raise contract.ContractError("spatial views are for different work requests")
    if b["state_version"] < a["state_version"]: raise contract.ContractError("stale spatial surface")
    if b["state_version"] == a["state_version"] and b["canonical_record_digest"] != a["canonical_record_digest"]: raise contract.ContractError("same-version spatial conflict")
    if b["state_version"] == a["state_version"] and newer["projection_digest"] == older["projection_digest"]: raise contract.ContractError("stale spatial surface")
    return newer


def project_job_passport_surface(source_projection: Any) -> dict:
    """Build the first reusable surface from an Observatory projection.

    The positions are deterministic derived geometry.  A consumer may retain a
    user layout preference separately, but cannot send it back to work state.
    """
    source = contract.validate_observatory_projection(source_projection)
    binding = {"work_request_id": source["work_request_id"], "state_version": source["source_state"]["state_version"], "canonical_record_digest": source["source_state"]["canonical_record_digest"], "source_projection_digest": source["projection_digest"]}
    current = next((row["component_ref"] for row in source["component_map"] if row["current"]), None)
    state = "verified" if source["state"]["verification"] == "verified_success" else {"blocked": "blocked", "failed": "failed", "stale": "stale", "unknown": "unknown"}.get(source["state"]["progress"], "current")
    freshness = "stale" if source["state"]["progress"] == "stale" else "fresh"
    nodes = []
    def node(node_id, node_type, entity_ref, parent, index, label, resource_refs=(), home=False, attention=None):
        x, y = (0, 0) if index == 0 else (180 * index, 100)
        return {"node_id": node_id, "node_type": node_type, "entity_ref": entity_ref, "parent_node_id": parent, "group_ref": source["work_request_id"], "geometry": {"declared_bounds": {"x": x, "y": y, "width": 150, "height": 72}, "derived_bounds": {"x": x, "y": y, "width": 150, "height": 72}, "user_layout_preference": None}, "presentation": {"focusable": True, "selected": entity_ref == current, "home_zone_member": home, "visual_priority": "critical" if attention else "normal", "needs_attention_reason": attention}, "status": {"state": state, "freshness": freshness, "evidence_refs": source["evidence_refs"]}, "accessibility": {"label": label, "non_color_status_token": state.replace("_", " "), "list_order": index}, "resource_refs": list(resource_refs)}
    root_id = "node:work-request"
    nodes.append(node(root_id, "work_request", source["work_request_id"], None, 0, f"Work Request {source['work_request_id']}", home=True, attention="evidence stale" if freshness == "stale" else None))
    lane = source["attempt_lane"]
    nodes.append(node("node:attempt-lane", "attempt_lane", lane["attempt_id"], root_id, 1, f"Attempt {lane['attempt_id']} staffed by {lane['actual_staffing']['model_id']}", [lane["actual_staffing"]["native_session_ref"]], home=True))
    for index, component in enumerate(source["component_map"], start=2):
        nodes.append(node(f"node:component:{index}", "component", component["component_ref"], root_id, index, f"Component {component['component_ref']}"))
    edges = [{"edge_id": f"edge:contains:{row['node_id']}", "from_node_id": root_id, "to_node_id": row["node_id"], "relationship": "contains", "evidence_refs": source["evidence_refs"]} for row in nodes[1:]]
    view = {"schema_version": "spatial-surface-projection.v1", "surface": {"surface_id": "surface:model-room", "workspace_id": "workspace:control-room", "view_id": "view:job-passport", "version": "v1"}, "generated_at": source["generated_at"], "canonical_binding": binding, "nodes": nodes, "edges": edges, "semantic_zoom": {"overview": {"max_scale": 0.75, "summary": f"{len(nodes)} nodes · {len(edges)} relationships · {state}"}, "detail": {"min_scale": 0.75, "summary": f"Current component {current or 'unknown'} · persistent profile {lane['persistent_profile']['display_label']}"}}, "home_zone": {"home_node_id": root_id, "attention_node_ids": [root_id] if freshness == "stale" else [], "return_label": "Return to Job Passport Home"}, "list_order": [row["node_id"] for row in nodes]}
    view["projection_digest"] = contract.canonical_digest(view)
    return validate_spatial_surface(view, source)


def validate_telemetry_measurement(value: Any) -> dict:
    row = contract._expect_exact(value, TELEMETRY_FIELDS, "telemetry measurement")
    if row["schema_version"] != "telemetry-measurement.v1": raise contract.ContractError("unsupported telemetry schema_version")
    _id(row["measurement_id"], "telemetry measurement id")
    if row["metric_kind"] not in {"subscription_quota", "session_tokens", "billed_cost", "elapsed_time", "lifecycle_activity", "other"}: raise contract.ContractError("telemetry metric kind invalid")
    contract._string(row["unit"], "telemetry unit"); contract._string(row["scope"], "telemetry scope")
    attribution = contract._expect_exact(row["attribution"], {"provider_id", "model_id", "harness_id", "adapter_id", "attempt_id", "native_session_ref"}, "telemetry attribution")
    for key, item in attribution.items():
        if item is not None: _id(item, f"telemetry attribution {key}")
    source = contract._expect_exact(row["source"], {"type", "priority", "provenance_ref"}, "telemetry source")
    if source["type"] not in {"structured_provider_event", "official_provider_api", "documented_cli_json", "deterministic_local_clock", "unavailable"}: raise contract.ContractError("telemetry source type invalid")
    if not isinstance(source["priority"], int) or not 1 <= source["priority"] <= 5: raise contract.ContractError("telemetry source priority invalid")
    if source["type"] == "unavailable" and row["value"]["kind"] != "unavailable": raise contract.ContractError("unavailable telemetry source requires unavailable value")
    val = contract._expect_exact(row["value"], {"kind", "amount", "estimate_method", "uncertainty", "unavailable_reason"}, "telemetry value")
    if val["kind"] == "actual" and (not isinstance(val["amount"], (int, float)) or val["estimate_method"] is not None or val["unavailable_reason"] is not None): raise contract.ContractError("actual telemetry must be actual, not an estimate")
    if val["kind"] == "estimate" and (not isinstance(val["amount"], (int, float)) or not val["estimate_method"] or not val["uncertainty"]): raise contract.ContractError("estimate telemetry needs method and uncertainty")
    if val["kind"] == "unavailable" and (val["amount"] is not None or not val["unavailable_reason"]): raise contract.ContractError("unavailable telemetry is never zero")
    if row["metric_kind"] == "session_tokens" and "quota" in row["unit"].lower(): raise contract.ContractError("session tokens cannot masquerade as subscription quota")
    return row


def measurements_from_attempt_receipt(receipt: Any) -> list[dict]:
    """Project only measurements whose source is already deterministic.

    AttemptReceipt is a redacted CARR evidence record, not a provider billing
    response. Its lifecycle timestamps can prove elapsed time through the
    local controller clock. Its numeric ``cost_usd`` and token fields cannot
    prove billed cost or subscription quota without a separately approved
    provider source, so cost remains explicitly unavailable here.
    """
    completed = contract.validate_attempt_receipt(receipt)
    adapter = completed["adapter"]
    start = datetime.fromisoformat(completed["lifecycle"]["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(completed["lifecycle"]["ended_at"].replace("Z", "+00:00"))
    elapsed_ms = max(0, int((end - start).total_seconds() * 1000))
    attribution = {"provider_id": adapter["provider_id"], "model_id": adapter["model_id"], "harness_id": adapter["harness_id"], "adapter_id": adapter["adapter_id"], "attempt_id": completed["attempt_id"], "native_session_ref": adapter["native_session_ref"]}
    common = {"schema_version": "telemetry-measurement.v1", "scope": "attempt", "attribution": attribution,
              "observed_at": completed["lifecycle"]["ended_at"], "source_window": {"started_at": completed["lifecycle"]["started_at"], "ended_at": completed["lifecycle"]["ended_at"]}}
    evidence = f"evidence:attempt-receipt-{completed['attempt_id']}"
    values = [
        {**common, "measurement_id": f"measurement:{completed['attempt_id']}:elapsed", "metric_kind": "elapsed_time", "unit": "ms", "source": {"type": "deterministic_local_clock", "priority": 4, "provenance_ref": evidence}, "freshness": "fresh", "value": {"kind": "actual", "amount": elapsed_ms, "estimate_method": None, "uncertainty": None, "unavailable_reason": None}},
        {**common, "measurement_id": f"measurement:{completed['attempt_id']}:billed-cost", "metric_kind": "billed_cost", "unit": "USD", "source": {"type": "unavailable", "priority": 5, "provenance_ref": evidence}, "freshness": "unknown", "value": {"kind": "unavailable", "amount": None, "estimate_method": None, "uncertainty": None, "unavailable_reason": "no approved provider billing source"}},
    ]
    return [validate_telemetry_measurement(value) for value in values]


def validate_visual_extension_manifest(value: Any) -> dict:
    row = contract._expect_exact(value, EXTENSION_FIELDS, "visual extension manifest")
    if row["schema_version"] != "visual-extension-manifest.v1" or row["api_version"] != "carr-visual-projection-api.v1": raise contract.ContractError("unsupported visual extension api")
    _id(row["extension_id"], "visual extension id")
    if set(row["permissions"]) - {"sanitized_projection_data"}: raise contract.ContractError("visual extension permission denied")
    if row["enablement"] != {"installed": False, "enabled": False, "human_authorization_ref": None}: raise contract.ContractError("visual extension installation or enablement is outside this slice")
    provenance = contract._expect_exact(row["provenance"], {"publisher_id", "signature_status", "trust_status"}, "visual extension provenance")
    if provenance["signature_status"] != "verified" or provenance["trust_status"] != "trusted": raise contract.ContractError("visual extension publisher is not trusted")
    package = contract._expect_exact(row["package"], {"content_digest", "files"}, "visual extension package"); contract._digest(package["content_digest"], "visual extension package digest")
    paths = set()
    for file in package["files"]:
        entry = contract._expect_exact(file, {"path", "size_bytes", "digest"}, "visual extension package file")
        path = entry["path"]
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/") or "\\" in path or not path: raise contract.ContractError("visual extension path containment refused")
        if path in paths: raise contract.ContractError("visual extension duplicate package path")
        paths.add(path); contract._digest(entry["digest"], "visual extension file digest")
        if not isinstance(entry["size_bytes"], int) or not 0 < entry["size_bytes"] <= 1048576: raise contract.ContractError("visual extension file size invalid")
    for contribution in row["contributions"]:
        entry = contract._expect_exact(contribution, {"contribution_id", "kind", "entry_path"}, "visual extension contribution")
        if entry["kind"] not in {"visual_projection", "widget"} or entry["entry_path"] not in paths: raise contract.ContractError("visual extension contribution refused")
    return row
