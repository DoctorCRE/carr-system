#!/usr/bin/env python3
"""Announce, at the end of a CARR map task, that the live map method was never
loaded.

IT ANNOUNCES, IT NO LONGER REOPENS (2026-08-23, Joe's Stop-gate rationing off
the gates-audit council). It emitted {"decision": "block"} — a reopened turn and
a whole extra assistant message — on both its finding path and its fail-closed
path. Eleven Stop hooks held that power; the council's rationing leaves it with
three (core conduct, completion-evidence, drift-assertion) and names this gate
among the demotions.

WHAT THAT CHANGES, said plainly rather than papered over. Before: a governed
session could not FINISH a map task without calling the verb. Now: it cannot
finish one QUIETLY. The instruction still arrives, in context, at the same
moment, and the fire is still recorded in out/map-architecture-gate.jsonl — but
a session that ignores it can end.

WHY THIS ONE IS WORTH RE-EXAMINING FIRST, alongside the context-handoff band.
This is a narrow matcher: it fires only on map work, which is rare, so it
contributes almost nothing to the reopen count the rationing exists to cut —
and its bind moment is genuinely the last cheap one, because the map artifact
reaches Joe or a client immediately after. The council's own advice was not to
spend the first telemetry week sentencing narrow, rarely-firing gates. Restoring
the reopen is one line in each of the two emit sites below. The better answer,
if it comes back, is probably not the reopen but an EARLIER bind: the harm is at
the map artifact's write, which is a PreToolUse moment, cheaper than Stop and
ahead of it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stop_latch import announce  # noqa: E402
sys.path.insert(0, REPO)
from lib.transcript_read import load_transcript  # noqa: E402
LOG = os.path.join(REPO, "out", "map-architecture-gate.jsonl")
CARR_PATH_MARKERS = ("/carr-system/", "/carr-system", "my drive/carr ai")
SYNTHETIC_PREFIXES = ("The following is the Codex agent history", "<environment_context>",
                      "<app-context>", "<skills_instructions>", "<permissions instructions>")
FIGURATIVE = re.compile(r"\b(?:roadmap|road map|mind map|concept map|relationship map|map out)\b", re.I)
MAP_CONTEXT = re.compile(
    r"\b(?:interactive\s+map|tour\s+map|property\s+map|market\s+map|route\s+map|"
    r"google\s+maps?|mapbox|maplibre|leaflet|geospatial|GIS|waypoints?|map\s+pins?|"
    r"day[ -]trip|mapping\s+(?:stack|system|workflow|architecture)|"
    r"map\s+(?:for|of|showing|with|that|system|architecture|stack|workflow))\b", re.I)
TASK_ACTION = re.compile(
    r"\b(?:build|create|make|design|redesign|revise|update|fix|generate|publish|deliver|"
    r"recommend|choose|which|best|should|integrate|review|audit|help|want|need|architecture)\b", re.I)
CALL_MARKER = re.compile(
    r"(?:mcp__(?:carr|carr_records)__(?:map[-_]architecture)|"
    r"run\.sh\s+call\s+map-architecture\b|"
    r"call[_-]verb[^\n]{0,240}map-architecture)", re.I)
CONTRACT_ID = "carr-workspace-market-map-route-planning"
CONTRACT_VERSION = "1.2.0"
CONTRACT_PATH = "workspace/contracts/market-map-route-planning.v1.json"
REQUIRED_METHOD_IDS = {
    "recursive_source_intake",
    "typed_domain_queries",
    "spatial_authoring_workbench",
    "deterministic_component_registry",
    "portable_geospatial_interchange",
    "entrance_level_coordinate_verification",
    "route_label_identity_separation",
    "search_and_tour_modes",
    "map_event_contract",
    "provider_rights_receipt",
    "human_promotion_receipt",
}
REQUIRED_SOURCES = {
    ("maps-and-demographics", "ai-built-interactive-tour-maps-source-rendering-routing-and-promotion-gate"),
    ("carr-workspace-bduf", "s13-ipad-application-and-tour-mode"),
}


def _content_text(content, kinds=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    values = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in kinds:
            values.append(str(block.get("text", "")))
    return "\n".join(values)


def role_and_text(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload.get("role"), _content_text(payload.get("content"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    return message.get("role") or record.get("type"), _content_text(message.get("content"))


def serialized(record):
    values = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                values.append(str(key))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            values.append(str(value))

    walk(record)
    return "\n".join(values)


def genuine_user_task(record):
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return ""
    if value.lstrip().startswith(SYNTHETIC_PREFIXES):
        return ""
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return ""
    return value


def is_map_task(value):
    if not value or FIGURATIVE.search(value):
        return False
    return bool(MAP_CONTEXT.search(value) and TASK_ACTION.search(value))


def latest_task(records):
    for index in range(len(records) - 1, -1, -1):
        value = genuine_user_task(records[index])
        if value:
            return index, value
    return -1, ""


def _parse_json_value(value):
    if isinstance(value, dict):
        for key in ("structuredContent", "structured_content", "result"):
            nested = value.get(key)
            if isinstance(nested, dict):
                return _parse_json_value(nested)
        content = value.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parsed = _parse_json_value(block["text"])
                    if parsed is not None:
                        return parsed
        return value
    if isinstance(value, list):
        for item in value:
            parsed = _parse_json_value(item)
            if parsed is not None:
                return parsed
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return _parse_json_value(parsed)


def _tool_result(record, expected_tool_id=None):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
        return _parse_json_value(payload.get("output"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("is_error"):
            continue
        if expected_tool_id and block.get("tool_use_id") != expected_tool_id:
            continue
        return _parse_json_value(block.get("content"))
    return None


def _map_call_id(record):
    raw = serialized(record)
    if not CALL_MARKER.search(raw):
        return None
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and CALL_MARKER.search(serialized(block)):
                return block.get("id") or "__unidentified__"
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
        return "__codex__"
    return "__unidentified__"


def _valid_architecture_payload(value):
    if not isinstance(value, dict):
        return False
    if value.get("ok") is not True or value.get("architecture") != "carr-map-tour-v1":
        return False
    contract = value.get("contract")
    if not isinstance(contract, dict) or contract.get("id") != CONTRACT_ID:
        return False
    if contract.get("version") != CONTRACT_VERSION or contract.get("path") != CONTRACT_PATH:
        return False
    methods = value.get("method_ids")
    if not isinstance(methods, list) or not REQUIRED_METHOD_IDS.issubset(set(methods)):
        return False
    sources = value.get("sources")
    if not isinstance(sources, list):
        return False
    seen = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("body_text"), str):
            return False
        if not source["body_text"].strip() or not isinstance(source.get("version"), int):
            return False
        seen.add((source.get("document"), source.get("section_key")))
    return REQUIRED_SOURCES.issubset(seen)


def successful_architecture_read(records):
    call_id = None
    for record in records:
        found_call = _map_call_id(record)
        if found_call:
            call_id = found_call
            continue
        expected = None if call_id in {None, "__codex__", "__unidentified__"} else call_id
        result = _tool_result(record, expected)
        if call_id and _valid_architecture_payload(result):
            return True
    return False


def evaluate(records):
    index, task = latest_task(records)
    if index < 0 or not is_map_task(task):
        return False, "current task is not governed map work"
    if successful_architecture_read(records[index + 1:]):
        return False, "current task loaded carr-map-tour-v1"
    return True, "current map task did not load carr-map-tour-v1 after the request"


def payload_is_carr(payload):
    cwd = payload.get("cwd") or payload.get("working_directory") or payload.get("workingDirectory")
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    normalized = cwd.replace("\\", "/").lower()
    repo = REPO.replace("\\", "/").lower().rstrip("/")
    return (normalized == repo or normalized.startswith(repo + "/")
            or any(marker in normalized for marker in CARR_PATH_MARKERS))


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(json.dumps(row) + "\n")
    except Exception:
        pass


def main():
    payload = {}
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active") or not payload_is_carr(payload):
            return 0
        path = payload.get("transcript_path") or payload.get("transcriptPath")
        if not path or not os.path.exists(path):
            return 0
        # One bad line in the session's own transcript must not switch the
        # gate off (bypass hunt, PR #1224): lib/transcript_read.py skips it
        # and records a transcript_tamper event instead of raising.
        records = load_transcript(path, hook="map-architecture-gate",
                                  session=payload.get("session_id") or payload.get("sessionId"),
                                  log_path=LOG)
        blocked, reason = evaluate(records)
        if not blocked:
            return 0
        audit({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "hook": "map-architecture-gate", "register": "announce",
               "session": payload.get("session_id") or payload.get("sessionId"),
               "reason": reason})
        return announce(
            "MAP ARCHITECTURE GATE — call the live `map-architecture` verb now. "
            "Read its two current doctrine sections and machine-contract pointer, then do the "
            "map work against carr-map-tour-v1. A prior task's read does not satisfy this one. "
            "This no longer holds your turn open, so make the call before the map "
            "work goes any further rather than after.")
    except Exception as exc:
        if payload_is_carr(payload):
            audit({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "hook": "map-architecture-gate",
                   "session": payload.get("session_id") or payload.get("sessionId"),
                   "reason": "gate internal failure", "error": type(exc).__name__})
            announce(
                "MAP ARCHITECTURE GATE COULD NOT RUN — the governed map-method check "
                "did not complete, so nothing here has confirmed the live method was "
                "loaded. It used to hold the turn open on this path; it now says so "
                "instead. Repair the gate or the transcript read, and treat the map "
                "method as unverified until you have.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
