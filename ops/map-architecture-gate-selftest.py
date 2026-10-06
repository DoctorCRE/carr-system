#!/usr/bin/env python3
"""Regression fixtures for the mandatory map-architecture Stop gate."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "map-architecture-gate.py")
spec = importlib.util.spec_from_file_location("map_architecture_gate", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def user(value):
    return {"type": "user", "message": {"role": "user", "content": value}}


def assistant_tool(name, tool_id="map-call", value=None):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": value or {}},
    ]}}


def tool_result(value, tool_id="map-call", is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error,
         "content": value},
    ]}}


METHOD_IDS = [
    "recursive_source_intake", "typed_domain_queries", "spatial_authoring_workbench",
    "deterministic_component_registry", "portable_geospatial_interchange",
    "entrance_level_coordinate_verification", "route_label_identity_separation",
    "search_and_tour_modes", "map_event_contract", "provider_rights_receipt",
    "human_promotion_receipt",
]


def architecture_payload(**overrides):
    value = {
        "ok": True,
        "architecture": "carr-map-tour-v1",
        "contract": {
            "id": "carr-workspace-market-map-route-planning",
            "version": "1.2.0",
            "path": "workspace/contracts/market-map-route-planning.v1.json",
        },
        "method_ids": METHOD_IDS,
        "sources": [
            {"document": "maps-and-demographics", "section_key": "ai-built-interactive-tour-maps-source-rendering-routing-and-promotion-gate", "version": 3, "body_text": "governed map method"},
            {"document": "carr-workspace-bduf", "section_key": "s13-ipad-application-and-tour-mode", "version": 2, "body_text": "governed Tour method"},
        ],
    }
    value.update(overrides)
    return value


def success(payload=None):
    return tool_result(json.dumps(payload or architecture_payload()))


def codex_user(value):
    return {"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": value}]}}


def codex_call(source):
    return {"type": "response_item", "payload": {"type": "custom_tool_call",
            "name": "exec", "input": source}}


def codex_output(value):
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output",
            "output": value}}


CASES = [
    ("build request is caught without architecture",
     [user("Build an interactive property tour map for tomorrow.")], True),
    ("recommendation request is caught without architecture",
     [user("Which GIS and mapping stack should we use for a broker tour?")], True),
    ("direct live verb after request satisfies gate",
     [user("Create a Google Maps tour."), assistant_tool("mcp__carr__map-architecture"), success()], False),
    ("Codex nested live verb after request satisfies gate",
     [codex_user("Build a MapLibre property map."),
      codex_call("await tools.mcp__carr__map_architecture({});"),
      codex_output(json.dumps(architecture_payload()))], False),
    ("local call-verb path after request satisfies gate",
     [user("Design an interactive market map."),
      assistant_tool("Bash", value={"command": "./run.sh call map-architecture '{}'"}),
      tool_result(json.dumps(architecture_payload()))], False),
    ("failed live verb does not satisfy gate",
     [user("Create a Google Maps tour."), assistant_tool("mcp__carr__map-architecture"),
      tool_result('{"error":"map_architecture_unavailable"}', is_error=True)], True),
    ("minimal success-looking stub does not satisfy gate",
     [user("Create a Google Maps tour."), assistant_tool("mcp__carr__map-architecture"),
      success({"ok": True, "architecture": "carr-map-tour-v1"})], True),
    ("stale contract version does not satisfy gate",
     [user("Create a Google Maps tour."), assistant_tool("mcp__carr__map-architecture"),
      success(architecture_payload(contract={"id": "carr-workspace-market-map-route-planning", "version": "1.1.0", "path": "workspace/contracts/market-map-route-planning.v1.json"}))], True),
    ("blank doctrine body does not satisfy gate",
     [user("Create a Google Maps tour."), assistant_tool("mcp__carr__map-architecture"),
      success(architecture_payload(sources=[
          {"document": "maps-and-demographics", "section_key": "ai-built-interactive-tour-maps-source-rendering-routing-and-promotion-gate", "version": 1, "body_text": ""},
          {"document": "carr-workspace-bduf", "section_key": "s13-ipad-application-and-tour-mode", "version": 2, "body_text": "Tour"},
      ]))], True),
    ("old architecture read does not satisfy a new task",
     [assistant_tool("mcp__carr__map-architecture"), success(),
      user("Create a MapLibre tour map.")], True),
    ("ordinary task is out of scope", [user("Draft a lease renewal email.")], False),
    ("figurative map phrase is out of scope", [user("Map out the next three project steps.")], False),
    ("roadmap is out of scope", [user("Update the product roadmap.")], False),
    ("mind map is out of scope", [user("Make a mind map of these ideas.")], False),
    ("historical mention is not a task", [user("Dell used a property tour map yesterday.")], False),
]


def real_hook(records, cwd=REPO):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for row in records:
            fh.write(json.dumps(row) + "\n")
        transcript = fh.name
    try:
        payload = {
            "transcript_path": transcript,
            "session_id": "selftest",
            "stop_hook_active": False,
            "cwd": cwd,
        }
        result = subprocess.run(
            [os.sys.executable, HOOK], input=json.dumps(payload), text=True,
            capture_output=True, timeout=20,
        )
        body = json.loads(result.stdout or "{}")
        # DEMOTED 2026-08-23 (the gates-audit council's Stop-gate rationing,
        # Joe's order): this gate emitted {"decision": "block"}, reopening the
        # turn. It now announces the same instruction into context. "Spoke"
        # therefore means an announcement, a surviving `decision` key means the
        # demotion regressed, and exit 2 would mean the same.
        if "decision" in body or result.returncode == 2:
            return False
        return bool((body.get("hookSpecificOutput") or {}).get("additionalContext"))
    finally:
        os.unlink(transcript)


def main():
    outcomes = []
    for name, records, expected in CASES:
        got, reason = mod.evaluate(records)
        ok = got == expected
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} ({reason})")

    structured = real_hook([user("Build an interactive tour map.")])
    outcomes.append(structured)
    print(f"{'PASS' if structured else 'FAIL'}  structured Stop announcement, no reopen")

    non_carr = not real_hook([user("Build an interactive tour map.")], "/private/tmp/other-project")
    outcomes.append(non_carr)
    print(f"{'PASS' if non_carr else 'FAIL'}  non-CARR cwd is out of scope")

    hook_configs = [
        os.path.join(REPO, "ops", "config", "hooks.json"),
        os.path.join(REPO, "ops", "config", "codex-hooks.json"),
    ]
    for path in hook_configs:
        wired = "map-architecture-gate.py" in open(path).read()
        outcomes.append(wired)
        print(f"{'PASS' if wired else 'FAIL'}  wired in {os.path.basename(path)}")

    hardening = open(os.path.join(REPO, "ops", "harden-gates.sh")).read()
    hardened = "map-architecture-gate.py" in hardening
    outcomes.append(hardened)
    print(f"{'PASS' if hardened else 'FAIL'}  gate is in OS hardening set")

    skill_path = os.path.join(REPO, "claude-tree", "skills", "build-interactive-map", "SKILL.md")
    skill = open(skill_path).read()
    skill_valid = ("TODO" not in skill and "map-architecture" in skill
                   and "Google Maps" in skill and "map out the steps" in skill
                   and "PMTiles" in skill and "property identity" in skill
                   and "entrance" in skill and "Grok Build" in skill)
    outcomes.append(skill_valid)
    print(f"{'PASS' if skill_valid else 'FAIL'}  map skill has trigger and mandatory live read")

    print(f"map-architecture-gate-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
