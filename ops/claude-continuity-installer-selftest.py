#!/usr/bin/env python3
"""Transactional preservation checks for the Claude continuity installer."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "ops/install-claude-continuity.py"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="carr-claude-installer-")
        self.home = pathlib.Path(self.temp.name)
        self.settings = self.home / ".claude/settings.json"
        self.mode = self.home / ".config/carr/claude-continuity-mode.json"
        self.codex = self.home / ".codex/hooks.json"
        self.claude_config = self.home / ".claude.json"
        self.settings.parent.mkdir(parents=True)
        self.codex.parent.mkdir(parents=True)
        self.unrelated = {
            "permissions": {"allow": ["Read", "Bash(git status)"]},
            "env": {"UNRELATED": "preserve-me"},
            "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{
                "type": "command", "command": "/usr/bin/true", "timeout": 2}]}],
                "CustomFutureEvent": [{"future": True}]},
        }
        self.settings.write_text(json.dumps(self.unrelated), encoding="utf-8")
        self.codex.write_text('{"unrelated":"codex-preserved"}\n', encoding="utf-8")
        self.claude_config.write_text(json.dumps({"theme": "dark", "mcpServers": {
            "existing": {"type": "http", "url": "https://example.invalid/mcp"}}}), encoding="utf-8")
        self.env = {**os.environ, "HOME": str(self.home),
                    "CARR_CLAUDE_SETTINGS_FILE": str(self.settings),
                    "CARR_CLAUDE_CONTINUITY_MODE_FILE": str(self.mode),
                    "CARR_CLAUDE_CONFIG_FILE": str(self.claude_config)}

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args, failure=None):
        env = dict(self.env)
        if failure:
            env["CARR_CLAUDE_CONTINUITY_INJECT_FAILURE"] = failure
        return subprocess.run([sys.executable, str(INSTALLER), *args], env=env,
                              capture_output=True, text=True, check=False)

    def assert_unrelated_preserved(self, actual):
        self.assertEqual(actual["permissions"], self.unrelated["permissions"])
        self.assertEqual(actual["env"], self.unrelated["env"])
        self.assertEqual(actual["hooks"]["CustomFutureEvent"], self.unrelated["hooks"]["CustomFutureEvent"])
        self.assertEqual(actual["hooks"]["SessionStart"][0], self.unrelated["hooks"]["SessionStart"][0])
        self.assertEqual(self.codex.read_text(), '{"unrelated":"codex-preserved"}\n')
        config = json.loads(self.claude_config.read_text())
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(config["mcpServers"]["existing"],
                         {"type": "http", "url": "https://example.invalid/mcp"})

    def test_install_is_preserving_idempotent_and_verifiable(self):
        before = self.settings.read_bytes()
        self.assertEqual(self.invoke("install", "--mode", "checkpoint").returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before, "dry run is read only")
        first = self.invoke("install", "--mode", "checkpoint", "--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        installed_bytes = self.settings.read_bytes()
        installed = json.loads(installed_bytes)
        self.assert_unrelated_preserved(installed)
        self.assertEqual(json.loads(self.mode.read_text())["mode"], "checkpoint")
        self.assertRegex(json.loads(self.mode.read_text())["config_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(self.invoke("verify").returncode, 0)
        server = json.loads(self.claude_config.read_text())["mcpServers"]["carr-continuity"]
        self.assertEqual(server["command"], "/usr/bin/env")
        self.assertEqual(server["env"], {"CARR_MCP_CLIENT_PROFILE": "claude-continuity"})
        self.assertNotIn("token", json.dumps(server).lower())
        self.assertEqual(self.invoke("install", "--mode", "checkpoint", "--apply").returncode, 0)
        self.assertEqual(self.settings.read_bytes(), installed_bytes)
        config = json.loads(self.claude_config.read_text())
        self.assertEqual(config["theme"], "dark")
        self.assertIn("existing", config["mcpServers"])
        continuity = config["mcpServers"]["carr-continuity"]
        self.assertNotIn("token", json.dumps(continuity).lower())
        self.assertIn("continuity-stdio-proxy.mjs", json.dumps(continuity))

    def test_pre_1817_canonical_commands_upgrade_and_refresh_receipt(self):
        command = ("CARR_MCP_CLIENT_PROFILE=claude-continuity /usr/bin/env python3 "
                   f"{ROOT}/ops/claude-continuity-hook.py")
        for retired in (False, True):
            with self.subTest(retired=retired):
                live = copy.deepcopy(self.unrelated)
                events = ["PreCompact", "SessionStart"]
                if retired:
                    events += ["UserPromptSubmit", "PostToolUse", "Stop"]
                for event in events:
                    entry = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
                    if event == "SessionStart":
                        entry["matcher"] = "^(startup|compact|resume)$"
                    live["hooks"].setdefault(event, []).append(entry)
                self.settings.write_text(json.dumps(live))
                self.mode.parent.mkdir(parents=True, exist_ok=True)
                self.mode.write_text(json.dumps({"schema_version": 1, "mode": "inject",
                                               "config_digest": "sha256:" + "0" * 64}))
                before = self.settings.read_bytes(), self.mode.read_bytes()
                dry = self.invoke("install", "--mode", "inject")
                self.assertEqual(dry.returncode, 0, dry.stderr)
                self.assertEqual((self.settings.read_bytes(), self.mode.read_bytes()), before)
                result = self.invoke("install", "--mode", "inject", "--apply")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.invoke("verify").returncode, 0)
                upgraded = json.loads(self.settings.read_text())
                self.assertNotIn(command, [hook["command"] for entries in upgraded["hooks"].values()
                    for entry in entries if isinstance(entry, dict)
                    for hook in entry.get("hooks", []) if "command" in hook])
                self.assert_unrelated_preserved(upgraded)
                self.assertFalse(set(events[2:]) & set(upgraded["hooks"]))
                first = self.settings.read_bytes(), self.mode.read_bytes()
                self.assertEqual(self.invoke("install", "--mode", "inject", "--apply").returncode, 0)
                self.assertEqual((self.settings.read_bytes(), self.mode.read_bytes()), first)

    def test_prior_command_tampering_refuses_with_exit_one_and_no_writes(self):
        command = ("CARR_MCP_CLIENT_PROFILE=claude-continuity /usr/bin/env python3 "
                   f"{ROOT}/ops/claude-continuity-hook.py")
        original_mcp = self.claude_config.read_bytes()
        self.mode.parent.mkdir(parents=True, exist_ok=True)
        self.mode.write_text('{"stale":"receipt"}')
        original_mode = self.mode.read_bytes()
        for event, mutation in [
            ("SessionStart", lambda entry: entry.update(matcher=".*")),
            ("PreCompact", lambda entry: entry["hooks"][0].update(timeout=999)),
            ("Stop", lambda entry: entry["hooks"][0].update(command=command + " --foreign")),
            ("UnknownEvent", lambda entry: None),
        ]:
            live = copy.deepcopy(self.unrelated)
            entry = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
            if event == "SessionStart":
                entry["matcher"] = "^(startup|compact|resume)$"
            mutation(entry)
            live["hooks"].setdefault(event, []).append(entry)
            self.settings.write_text(json.dumps(live))
            before = self.settings.read_bytes()
            for action in ("install", "remove"):
                with self.subTest(event=event, action=action):
                    result = self.invoke(action, "--apply")
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("noncanonical Claude continuity hook", result.stderr)
                    self.assertEqual(self.settings.read_bytes(), before)
                    self.assertEqual(self.mode.read_bytes(), original_mode)
                    self.assertEqual(self.claude_config.read_bytes(), original_mcp)

    def test_mode_digest_is_identical_in_installer_hook_and_dedupe(self):
        installer = load_module("claude_installer_digest", INSTALLER)
        dedupe = load_module("claude_dedupe_digest", ROOT / "lib/claude_rule_delivery_dedupe.py")
        self.assertEqual(installer.expected_config_digest(), dedupe.expected_config_digest())

    def test_remove_is_selective_and_idempotent(self):
        self.assertEqual(self.invoke("install", "--mode", "inject", "--apply").returncode, 0)
        self.assertEqual(self.invoke("remove", "--apply").returncode, 0)
        removed = json.loads(self.settings.read_text())
        self.assert_unrelated_preserved(removed)
        self.assertFalse(self.mode.exists())
        config = json.loads(self.claude_config.read_text())
        self.assertEqual(config, {"theme": "dark", "mcpServers": {
            "existing": {"type": "http", "url": "https://example.invalid/mcp"}}})
        self.assertNotIn("carr-continuity", json.loads(self.claude_config.read_text())["mcpServers"])
        for entries in removed["hooks"].values():
            if isinstance(entries, list):
                self.assertNotIn("claude-continuity-hook.py", json.dumps(entries))
        first = self.settings.read_bytes()
        self.assertEqual(self.invoke("remove", "--apply").returncode, 0)
        self.assertEqual(self.settings.read_bytes(), first)

    def test_a_stale_mode_receipt_can_be_upgraded_rather_than_deadlocking(self):
        # A receipt goes stale every time the adapter legitimately changes.  When
        # both install and remove refused one, an upgraded adapter left the hook
        # reading a stale receipt — silently disabling continuity — with no
        # supported way back. An explicit install or remove is the repair.
        self.assertEqual(self.invoke("install", "--mode", "inject", "--apply").returncode, 0)
        canonical = self.mode.read_bytes()
        stale = json.loads(self.mode.read_text())
        stale["config_digest"] = "sha256:" + "0" * 64
        self.mode.write_text(json.dumps(stale), encoding="utf-8")

        upgraded = self.invoke("install", "--mode", "inject", "--apply")
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        self.assertIn("stale continuity mode receipt", upgraded.stdout)
        self.assertEqual(self.mode.read_bytes(), canonical,
                         "the upgrade rewrites the receipt to the current contract")
        self.assertEqual(self.invoke("verify").returncode, 0)
        self.assert_unrelated_preserved(json.loads(self.settings.read_text()))

        self.mode.write_text(json.dumps(stale), encoding="utf-8")
        removed = self.invoke("remove", "--apply")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.mode.exists())

    def test_remove_refuses_substring_collision_without_deleting_either_hook(self):
        self.assertEqual(self.invoke("install", "--mode", "inject", "--apply").returncode, 0)
        configured = json.loads(self.settings.read_text())
        canonical = next(entry for entry in configured["hooks"]["PreCompact"]
                         if "claude-continuity-hook.py" in json.dumps(entry))
        wrapper = copy.deepcopy(canonical)
        wrapper["hooks"][0]["command"] = (
            "/usr/bin/env python3 /tmp/diagnose.py --mentions "
            + wrapper["hooks"][0]["command"]
        )
        configured["hooks"]["PreCompact"].append(wrapper)
        self.settings.write_text(json.dumps(configured), encoding="utf-8")
        before = self.settings.read_bytes()

        result = self.invoke("remove", "--apply")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("noncanonical Claude continuity hook", result.stderr)
        self.assertEqual(self.settings.read_bytes(), before)
        remaining = json.loads(self.settings.read_text())["hooks"]["PreCompact"]
        self.assertIn(canonical, remaining)
        self.assertIn(wrapper, remaining)

    def test_install_rolls_back_settings_if_mode_write_fails(self):
        before_settings = self.settings.read_bytes()
        before_codex = self.codex.read_bytes()
        result = self.invoke("install", "--mode", "inject", "--apply", failure="mode")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before_settings)
        self.assertFalse(self.mode.exists())
        self.assertEqual(self.codex.read_bytes(), before_codex)

    def test_install_rolls_back_all_resources_if_mcp_write_fails(self):
        before_settings = self.settings.read_bytes()
        before_mcp = self.claude_config.read_bytes()
        result = self.invoke("install", "--mode", "inject", "--apply", failure="mcp")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before_settings)
        self.assertEqual(self.claude_config.read_bytes(), before_mcp)
        self.assertFalse(self.mode.exists())

    def test_remove_rolls_back_settings_if_mode_delete_fails(self):
        self.assertEqual(self.invoke("install", "--mode", "inject", "--apply").returncode, 0)
        before_settings = self.settings.read_bytes()
        before_mode = self.mode.read_bytes()
        result = self.invoke("remove", "--apply", failure="mode")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before_settings)
        self.assertEqual(self.mode.read_bytes(), before_mode)

    def test_install_rolls_back_hooks_and_mode_if_mcp_write_fails(self):
        before_settings = self.settings.read_bytes()
        before_mcp = self.claude_config.read_bytes()
        result = self.invoke("install", "--mode", "inject", "--apply", failure="mcp")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before_settings)
        self.assertEqual(self.claude_config.read_bytes(), before_mcp)
        self.assertFalse(self.mode.exists())

    def test_remove_rolls_back_mcp_server_if_settings_write_fails(self):
        self.assertEqual(self.invoke("install", "--mode", "inject", "--apply").returncode, 0)
        before_mcp = self.claude_config.read_bytes()
        result = self.invoke("remove", "--apply", failure="settings")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.claude_config.read_bytes(), before_mcp)

    def test_install_upgrades_the_retired_five_event_overlay(self):
        self.assertEqual(self.invoke("install", "--apply").returncode, 0)
        live = json.loads(self.settings.read_text())
        command = live["hooks"]["PreCompact"][-1]["hooks"][0]["command"]
        old = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
        for event in ("UserPromptSubmit", "PostToolUse", "Stop"):
            live["hooks"][event] = [old]
        self.settings.write_text(json.dumps(live), encoding="utf-8")
        self.assertNotEqual(self.invoke("verify").returncode, 0)
        result = self.invoke("install", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        upgraded = json.loads(self.settings.read_text())
        self.assertFalse({"UserPromptSubmit", "PostToolUse", "Stop"} & set(upgraded["hooks"]))
        self.assertEqual(self.invoke("verify").returncode, 0)
        self.assert_unrelated_preserved(upgraded)

    def test_retired_cleanup_keeps_foreign_hooks_on_the_same_events(self):
        self.assertEqual(self.invoke("install", "--apply").returncode, 0)
        live = json.loads(self.settings.read_text())
        command = live["hooks"]["PreCompact"][-1]["hooks"][0]["command"]
        owned = {"type": "command", "command": command, "timeout": 10}
        prompt = {"type": "prompt", "prompt": "Preserve this unrelated prompt hook"}
        foreign_command = {"type": "command", "command": "/usr/local/bin/keep-me.sh", "timeout": 3}
        live["hooks"]["Stop"] = [{"hooks": [owned, prompt]}]
        live["hooks"]["UserPromptSubmit"] = [{"hooks": [foreign_command]}, {"hooks": [owned]}]
        live["hooks"]["PostToolUse"] = [{"matcher": "Bash", "hooks": [foreign_command, owned]}]
        self.settings.write_text(json.dumps(live), encoding="utf-8")
        result = self.invoke("install", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(self.settings.read_text())["hooks"]
        self.assertEqual(after["Stop"], [{"hooks": [prompt]}])
        self.assertEqual(after["UserPromptSubmit"], [{"hooks": [foreign_command]}])
        self.assertEqual(after["PostToolUse"], [{"matcher": "Bash", "hooks": [foreign_command]}])
        self.assertEqual(self.invoke("verify").returncode, 0)
        live = json.loads(self.settings.read_text())
        live["hooks"]["Stop"] = [{"hooks": [owned, prompt]}]
        self.settings.write_text(json.dumps(live), encoding="utf-8")
        self.assertEqual(self.invoke("remove", "--apply").returncode, 0)
        self.assertEqual(json.loads(self.settings.read_text())["hooks"]["Stop"], [{"hooks": [prompt]}])

    def test_tampered_retired_entry_is_refused_without_writing(self):
        self.assertEqual(self.invoke("install", "--apply").returncode, 0)
        live = json.loads(self.settings.read_text())
        command = live["hooks"]["PreCompact"][-1]["hooks"][0]["command"]
        live["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": command, "timeout": 999}]}]
        self.settings.write_text(json.dumps(live), encoding="utf-8")
        before = self.settings.read_bytes()
        self.assertNotEqual(self.invoke("install", "--apply").returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_tampered_matcher_command_or_timeout_fails_closed(self):
        self.assertEqual(self.invoke("install", "--apply").returncode, 0)
        canonical = json.loads(self.settings.read_text())
        for event, mutation in [
            ("SessionStart", lambda entry: entry.update(matcher=".*")),
            ("PreCompact", lambda entry: entry["hooks"][0].update(command="/bin/sh -c injected")),
            ("SessionStart", lambda entry: entry["hooks"][0].update(timeout=999)),
        ]:
            tampered = copy.deepcopy(canonical)
            entry = next(item for item in tampered["hooks"][event]
                         if "claude-continuity-hook.py" in json.dumps(item))
            mutation(entry)
            # Preserve the recognizable continuity path in command tampering so
            # the installer must reject rather than append a second entry.
            if event == "PreCompact":
                entry["hooks"][0]["command"] += " /ops/claude-continuity-hook.py"
            self.settings.write_text(json.dumps(tampered), encoding="utf-8")
            before = self.settings.read_bytes()
            result = self.invoke("install", "--apply")
            self.assertNotEqual(result.returncode, 0, event)
            self.assertEqual(self.settings.read_bytes(), before, event)

        tampered_mcp = json.loads(self.claude_config.read_text())
        tampered_mcp["mcpServers"]["carr-continuity"]["args"].append("--injected")
        self.claude_config.write_text(json.dumps(tampered_mcp), encoding="utf-8")
        before = self.claude_config.read_bytes()
        result = self.invoke("install", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.claude_config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
