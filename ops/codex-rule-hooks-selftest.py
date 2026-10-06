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
from lib import rule_boot_gate as boot  # noqa: E402
from lib import rule_routes  # noqa: E402

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


def invoke(script, payload, state):
    env = dict(os.environ, CARR_RULE_BOOT_STATE_DIR=str(state),
               CARR_HOOK_GUARD_LOG=str(state / "guard.log"))
    result = subprocess.run([sys.executable, str(REPO / "hooks" / script)],
                            input=json.dumps(payload), capture_output=True, text=True,
                            env=env, cwd=REPO, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def main():
    events = json.loads((REPO / "ops/config/codex-hooks.json").read_text())["hooks"]
    boot_start = hook_command(events, "SessionStart", "gate-integrity.py")
    assert re.search(events["SessionStart"][0].get("matcher", ".*"), "clear")
    assert boot_start["timeout"] >= 15
    boot_pre = hook_command(events, "PreToolUse", "rule-boot-gate.py", "update_plan")
    assert boot_pre.get("async") is not True
    for tool in ("Bash", "apply_patch", "mcp__carr__standing_context", "update_plan"):
        hook_command(events, "PreToolUse", "rule-pack-preuse-reselection.py", tool)
    hook_command(events, "PostToolUse", "rule-boot-gate.py", "Bash")
    hook_command(events, "PostToolUse", "rule-boot-gate.py", "mcp__carr__standing_context")
    assert boot.classify("mcp__carr__standing_context", {"detail": "boot", "page": 1}) == ("fetch", 1)
    assert boot.classify("mcp__carr__read_doctrine", {}) == ("readonly", None)
    routes = json.loads((REPO / "ops/config/rule-routes.v1.json").read_text())
    edit_call = {"command": "*** Begin Patch\n*** Update File: hooks/example.py\n+new\n*** End Patch"}
    assert rule_routes.route_matches({"kind": "trigger", "tools": ["Write"],
                                      "verbs": [], "bash_patterns": []}, "apply_patch", edit_call)
    assert rule_routes.route_matches({"kind": "path_rule", "path_globs": ["hooks/*.py"]},
                                      "apply_patch", edit_call)
    assert rule_routes.matched_rule_ids(routes, "apply_patch", edit_call)
    move_call = {"command": "*** Begin Patch\n*** Update File: scratch.txt\n*** Move to: hooks/probe.py\n@@\n-before\n+after\n*** End Patch"}
    move_paths = rule_routes.call_paths(move_call)
    assert {"scratch.txt", "hooks/probe.py"} <= set(move_paths), move_paths
    destination_rules = {"43e2ef76", "86647daf", "a7784a18", "a9ecd5b4",
                         "c0b38d80", "e65efc68"}
    move_rules = set(rule_routes.matched_rule_ids(routes, "apply_patch", move_call))
    assert destination_rules <= move_rules, sorted(destination_rules - move_rules)
    add_call = {"command": "*** Begin Patch\n*** Add File: hooks/probe.py\n+after\n*** End Patch"}
    add_rules = set(rule_routes.matched_rule_ids(routes, "apply_patch", add_call))
    assert move_rules == add_rules, (move_rules, add_rules)
    assert {"4a53ff82", "58b44ccb", "99e951b9"} <= move_rules, move_rules
    move_out = {"command": "*** Begin Patch\n*** Update File: hooks/probe.py\n*** Move to: scratch.txt\n@@\n-before\n+after\n*** End Patch"}
    assert destination_rules <= set(rule_routes.matched_rule_ids(routes, "apply_patch", move_out))
    update_patch = {"command": "*** Begin Patch\n*** Update File: hooks/ledger-sweep.py\n@@\n-old\n+new\n*** End Patch"}
    patch_payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                     "tool_input": update_patch}
    edit_payload = dict(patch_payload, tool_name="Edit",
                        tool_input={"file_path": "hooks/ledger-sweep.py"})
    edit_rows = {row["trigger_id"] for row in rail.matched_triggers(edit_payload)}
    patch_rows = {row["trigger_id"] for row in rail.matched_triggers(patch_payload)}
    assert {"de72ad57b2c8", "5e186a09dcf2"} <= edit_rows
    assert edit_rows <= patch_rows, sorted(edit_rows - patch_rows)
    assert "bbffc139" not in rule_routes.matched_rule_ids(
        routes, "apply_patch", update_patch)
    update_rules = set(rule_routes.matched_rule_ids(routes, "apply_patch", update_patch))
    move_rows_expected = {row["trigger_id"] for row in rail.matched_triggers(
        dict(patch_payload, tool_input=move_call))}
    add_rows_expected = {row["trigger_id"] for row in rail.matched_triggers(
        dict(patch_payload, tool_input=add_call))}
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
        assert "hooks/ledger-sweep.py" in rule_routes.call_paths(update_variant)
        update_ids = set(rule_routes.matched_rule_ids(
            routes, "apply_patch", update_variant))
        assert update_ids == update_rules, (update_ids, update_rules)
        assert destination_rules <= update_ids, sorted(destination_rules - update_ids)
        update_rows = {row["trigger_id"] for row in rail.matched_triggers(
            dict(patch_payload, tool_input=update_variant))}
        assert update_rows == patch_rows, (update_rows, patch_rows)
        assert edit_rows <= update_rows, sorted(edit_rows - update_rows)
        move_variant = {"command": variant(move_call["command"])}
        assert {"scratch.txt", "hooks/probe.py"} <= set(rule_routes.call_paths(move_variant))
        move_ids = set(rule_routes.matched_rule_ids(
            routes, "apply_patch", move_variant))
        assert move_ids == move_rules, (move_ids, move_rules)
        assert destination_rules <= move_ids, sorted(destination_rules - move_ids)
        move_rows = {row["trigger_id"] for row in rail.matched_triggers(
            dict(patch_payload, tool_input=move_variant))}
        assert move_rows == move_rows_expected, (move_rows, move_rows_expected)
        assert "5e186a09dcf2" in move_rows
        add_variant = {"command": variant(add_call["command"])}
        assert "hooks/probe.py" in rule_routes.call_paths(add_variant)
        add_ids = set(rule_routes.matched_rule_ids(routes, "apply_patch", add_variant))
        assert add_ids == add_rules, (add_ids, add_rules)
        assert destination_rules <= add_ids, sorted(destination_rules - add_ids)
        add_rows = {row["trigger_id"] for row in rail.matched_triggers(
            dict(patch_payload, tool_input=add_variant))}
        assert add_rows == add_rows_expected, (add_rows, add_rows_expected)
        assert "5e186a09dcf2" in add_rows
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
    assert rail._row_matches("mcp__carr__new_deal", {},
                             {"kind": "verb", "pattern": "__new-deal$"})
    for event in ("PreToolUse", "PostToolUse"):
        for group in events[event]:
            for handler in group["hooks"]:
                if "rule-boot-gate.py" in handler["command"] or "rule-pack-preuse-reselection.py" in handler["command"]:
                    assert 2500 <= handler.get("additionalContextLimit", 0) <= 10000

    with tempfile.TemporaryDirectory() as temp:
        state = Path(temp)
        session = "codex-fixture"
        arm = {"schema": boot.SCHEMA, "status": "armed", "digest": "sha256:" + "a" * 64,
               "pages_total": 2, "epoch": "first"}
        base = {"session_id": session, "turn_id": "turn-1", "cwd": str(REPO), "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch"}}
        old = os.environ.get("CARR_RULE_BOOT_STATE_DIR")
        os.environ["CARR_RULE_BOOT_STATE_DIR"] = str(state)
        try:
            page_one = state / "page-one.json"
            page_one.write_text(json.dumps({"rule_boot": {
                "schema": boot.BOOT_SCHEMA, "digest": arm["digest"],
                "page": 1, "pages_total": 2, "total_chars": 12, "text": "page 1"}}))
            os.environ["CARR_RULE_BOOT_FETCH_STUB"] = str(page_one)
            try:
                for source in ("startup", "resume", "clear"):
                    notice = boot.arm_session(session, source)
                    assert "2 page(s)" in notice and boot.read_arm(session)["source"] == source
            finally:
                os.environ.pop("CARR_RULE_BOOT_FETCH_STUB", None)
            denied = invoke("rule-boot-gate.py", base, state)["hookSpecificOutput"]
            assert denied["permissionDecision"] == "deny"
            assert "continue" not in denied
            for page in (1, 2):
                command = f"./run.sh call standing-context '{{\"detail\":\"boot\",\"page\":{page}}}'"
                fetch = dict(base, tool_name="Bash", tool_input={"command": command})
                assert invoke("rule-boot-gate.py", fetch, state) == {}
                response = {"rule_boot": {"schema": boot.BOOT_SCHEMA, "digest": arm["digest"],
                                          "page": page, "pages_total": 2, "total_chars": 12, "text": f"page {page}"}}
                post = dict(fetch, hook_event_name="PostToolUse", tool_response=response)
                invoke("rule-boot-gate.py", post, state)
            assert invoke("rule-boot-gate.py", base, state) == {}
            os.environ["CARR_RULE_BOOT_FETCH_STUB"] = str(page_one)
            try:
                notice = boot.arm_session(session, "compact")
                assert "2 page(s)" in notice and boot.read_arm(session)["source"] == "compact"
            finally:
                os.environ.pop("CARR_RULE_BOOT_FETCH_STUB", None)
            again = invoke("rule-boot-gate.py", base, state)["hookSpecificOutput"]
            assert again["permissionDecision"] == "deny"
        finally:
            if old is None:
                os.environ.pop("CARR_RULE_BOOT_STATE_DIR", None)
            else:
                os.environ["CARR_RULE_BOOT_STATE_DIR"] = old
    print("codex-rule-hooks-selftest: source wiring and synthetic boot passed")


if __name__ == "__main__":
    main()
