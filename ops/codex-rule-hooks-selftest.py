#!/usr/bin/env python3
"""Synthetic Codex hook fixtures for the source-owned rule delivery wiring."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib import rule_operations as rule_routes  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "codex_rule_rail", REPO / "hooks/rule-pack-preuse-reselection.py")
assert spec and spec.loader
rail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rail)


def hook_command(events, event, script, tool="Bash"):
    matches = [h for g in events[event]
               if event == "SessionStart" or re.search(g.get("matcher", ".*"), tool)
               for h in g["hooks"] if script in h["command"]]
    assert len(matches) == 1, (event, script, tool, matches)
    return matches[0]


def main():
    events = json.loads((REPO / "ops/config/codex-hooks.json").read_text())["hooks"]
    boot_start = hook_command(events, "SessionStart", "gate-integrity.py")
    assert re.search(events["SessionStart"][0].get("matcher", ".*"), "clear")
    assert boot_start["timeout"] >= 15
    for tool in ("Bash", "apply_patch", "mcp__carr__standing_context", "update_plan"):
        hook_command(events, "PreToolUse", "rule-pack-preuse-reselection.py", tool)
    from lib.rule_scope_lookup import select_rules
    fixture = json.loads((REPO/'ops/fixtures/rule-scope-compatibility.v1.json').read_text())
    routes = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a'*64,
              'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local', 'sponsoring_human_id': 'joe'},
              'rules': [dict(id=rid, statement='Synthetic contract', personal_to=None, scope=scope)
                        for rid, scope in fixture['structural_scopes'].items()]}
    def scoped_ids(catalog, tool, value):
        return [r['id'] for r in select_rules(catalog, dict(tool_name=tool, tool_input=value))['rules']]

    edit_call = {"command": "*** Begin Patch\n*** Update File: hooks/example.py\n+new\n*** End Patch"}
    assert rule_routes.structural_matches({"kind": "trigger", "tools": ["Write"],
                                      "verbs": [], "bash_patterns": []}, "apply_patch", edit_call)
    assert rule_routes.structural_matches({"kind": "path_rule", "path_globs": ["hooks/*.py"]},
                                      "apply_patch", edit_call)
    assert scoped_ids(routes, "apply_patch", edit_call)
    move_call = {"command": "*** Begin Patch\n*** Update File: scratch.txt\n*** Move to: hooks/probe.py\n@@\n-before\n+after\n*** End Patch"}
    move_paths = rule_routes.call_paths(move_call)
    assert {"scratch.txt", "hooks/probe.py"} <= set(move_paths), move_paths
    destination_rules = {"43e2ef76", "86647daf", "a7784a18", "a9ecd5b4",
                         "c0b38d80", "e65efc68"}
    move_rules = set(scoped_ids(routes, "apply_patch", move_call))
    assert destination_rules <= move_rules, sorted(destination_rules - move_rules)
    add_call = {"command": "*** Begin Patch\n*** Add File: hooks/probe.py\n+after\n*** End Patch"}
    add_rules = set(scoped_ids(routes, "apply_patch", add_call))
    assert move_rules == add_rules, (move_rules, add_rules)
    assert {"4a53ff82", "58b44ccb", "99e951b9"} <= move_rules, move_rules
    move_out = {"command": "*** Begin Patch\n*** Update File: hooks/probe.py\n*** Move to: scratch.txt\n@@\n-before\n+after\n*** End Patch"}
    assert destination_rules <= set(scoped_ids(routes, "apply_patch", move_out))
    update_patch = {"command": "*** Begin Patch\n*** Update File: hooks/session-brief.py\n@@\n-old\n+new\n*** End Patch"}
    patch_payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                     "tool_input": update_patch}
    edit_payload = dict(patch_payload, tool_name="Edit",
                        tool_input={"file_path": "hooks/session-brief.py"})
    assert "bbffc139" not in scoped_ids(
        routes, "apply_patch", update_patch)
    update_rules = set(scoped_ids(routes, "apply_patch", update_patch))
    forms = (
        lambda text: text,
        lambda text: text.replace("\n", "\r\n"),
        lambda text: "\n" + text,
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch \n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\t\n", 1),
        lambda text: ("\n" + text.replace("*** Begin Patch\n", "*** Begin Patch \t\n", 1))
                     .replace("\n", "\r\n"),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u00a0\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u2003\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u202f\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u3000\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\v\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u0085\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u2028\n", 1),
        lambda text: text.replace("*** Begin Patch\n", "*** Begin Patch\u2029\n", 1),
    )
    for variant in forms:
        update_variant = {"command": variant(update_patch["command"])}
        assert "hooks/session-brief.py" in rule_routes.call_paths(update_variant)
        update_ids = set(scoped_ids(
            routes, "apply_patch", update_variant))
        assert update_ids == update_rules, (update_ids, update_rules)
        assert destination_rules <= update_ids, sorted(destination_rules - update_ids)
        move_variant = {"command": variant(move_call["command"])}
        assert {"scratch.txt", "hooks/probe.py"} <= set(rule_routes.call_paths(move_variant))
        move_ids = set(scoped_ids(
            routes, "apply_patch", move_variant))
        assert move_ids == move_rules, (move_ids, move_rules)
        assert destination_rules <= move_ids, sorted(destination_rules - move_ids)
        add_variant = {"command": variant(add_call["command"])}
        assert "hooks/probe.py" in rule_routes.call_paths(add_variant)
        add_ids = set(scoped_ids(routes, "apply_patch", add_variant))
        assert add_ids == add_rules, (add_ids, add_rules)
        assert destination_rules <= add_ids, sorted(destination_rules - add_ids)
    assert rule_routes.call_paths({"command": "prose before\n" + update_patch["command"]}) == []
    assert rule_routes.call_paths({"command": "cat <<'PATCH'\n" + update_patch["command"]
                                   + "\nPATCH"}) == []
    for suffix in ("\u200b", "\u2060", "\ufeff", "\x1c", "\x1d", "\x1e", "\x1f"):
        invalid = update_patch["command"].replace(
            "*** Begin Patch\n", f"*** Begin Patch{suffix}\n", 1)
        assert rule_routes.call_paths({"command": invalid}) == []
    assert "search-doctrine" in rule_routes.call_verbs("mcp__carr__search_doctrine", {})
    assert "confirm-merge" in rule_routes.call_verbs(
        "mcp__carr__call_verb", {"verb": "confirm-merge"})
    for event in ("PreToolUse", "PostToolUse"):
        for group in events[event]:
            for handler in group["hooks"]:
                if "rule-pack-preuse-reselection.py" in handler["command"]:
                    assert 2500 <= handler.get("additionalContextLimit", 0) <= 10000

    print("codex-rule-hooks-selftest: source wiring and rule lookup passed")


if __name__ == "__main__":
    main()
