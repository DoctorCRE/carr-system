#!/usr/bin/env python3
"""Regression proof for the independently managed Claude continuity overlay."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("config_as_code_continuity_test",
                                              ROOT / "ops/config-as-code.py")
assert SPEC and SPEC.loader
config: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


def continuity_source() -> dict:
    command = ("CARR_MCP_CLIENT_PROFILE=claude-continuity /usr/bin/env python3 "
               "{{REPO}}/ops/claude-continuity-hook.py")
    plain = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    return {"hooks": {
        "UserPromptSubmit": [plain],
        "PostToolUse": [plain],
        "PreCompact": [plain],
        "SessionStart": [{"matcher": "^(startup|compact|resume)$", **plain}],
        "Stop": [plain],
    }}


class ContinuityOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="config-continuity-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        (self.repo / "ops/config").mkdir(parents=True)
        (self.repo / "mcp-server").mkdir()
        (self.repo / "ops/launchd").mkdir()
        (self.repo / "ops/scheduled-tasks").mkdir()
        (self.repo / "ops/claude-continuity-hook.py").write_text("# adapter\n")
        (self.repo / "mcp-server/continuity-stdio-proxy.mjs").write_text("// proxy\n")
        (self.repo / "ops/config/claude-continuity-hooks.json").write_text(
            json.dumps(continuity_source(), indent=2) + "\n")
        self.base = {"PreToolUse": [{"matcher": "Bash", "hooks": [{
            "type": "command", "command": "/bin/true", "timeout": 5,
        }]}]}
        (self.repo / "ops/config/hooks.json").write_text(
            json.dumps(self.base, indent=2) + "\n")
        self.settings = self.home / ".claude/settings.json"
        self.settings.parent.mkdir(parents=True)
        self.mode = self.home / ".config/carr/claude-continuity-mode.json"
        self.mode.parent.mkdir(parents=True)
        self.mcp = self.home / ".claude.json"

        config.REPO = str(self.repo)
        config.SETTINGS = str(self.settings)
        config.HOOKS_REPO = str(self.repo / "ops/config/hooks.json")
        config.TASKS_SRC = str(self.home / ".claude/scheduled-tasks")
        config.TASKS_REPO = str(self.repo / "ops/scheduled-tasks")
        config.TASKS_QUARANTINE = str(self.home / ".claude/scheduled-tasks-quarantine")
        config.LAUNCHD_SRC = str(self.home / "Library/LaunchAgents")
        config.LAUNCHD_REPO = str(self.repo / "ops/launchd")
        config.LAUNCHD_ALT_REPO = {}
        config.CODEX_HOOKS_SRC = str(self.home / ".codex/hooks.json")
        config.CODEX_CONFIG = str(self.home / ".codex/config.toml")
        config.CLAUDE_CONTINUITY_MODE_FILE = str(self.mode)
        config.CLAUDE_MCP_CONFIG = str(self.mcp)
        config.IS_PRIMARY = False
        config.PREREQUISITE_CHECK = lambda _repo: []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def rendered_continuity(self) -> dict:
        return json.loads(json.dumps(continuity_source()).replace("{{REPO}}", str(self.repo)))["hooks"]

    def live_document(self) -> dict:
        hooks = json.loads(json.dumps(self.base))
        for event, entries in self.rendered_continuity().items():
            hooks.setdefault(event, []).extend(entries)
        return {"hooks": hooks, "theme": "preserve-me"}

    def continuity_entry_count(self) -> int:
        document = json.loads(self.settings.read_text())
        return sum("claude-continuity-hook.py" in json.dumps(entry)
                   for entries in document["hooks"].values() for entry in entries)

    def activate(self, *, include_hooks: bool = True, mode: str = "inject") -> None:
        contract = config.continuity_config.load(self.repo)
        document = self.live_document() if include_hooks else {
            "hooks": json.loads(json.dumps(self.base)), "theme": "preserve-me"}
        self.settings.write_text(json.dumps(document) + "\n")
        self.mode.write_text(json.dumps(
            config.continuity_config.mode_document(mode, contract)) + "\n")
        self.mcp.write_text(json.dumps({"theme": "mcp-preserved", "mcpServers": {
            "other": {"type": "http", "url": "https://example.invalid"},
            "carr-continuity": contract.mcp_server,
        }}) + "\n")

    def test_active_overlay_survives_base_install(self) -> None:
        self.activate()
        mode_before = self.mode.read_bytes()
        mcp_before = self.mcp.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc = config.cmd_install(True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.continuity_entry_count(), 5)
        self.assertEqual(json.loads(self.settings.read_text())["theme"], "preserve-me")
        self.assertEqual(self.mode.read_bytes(), mode_before)
        self.assertEqual(self.mcp.read_bytes(), mcp_before)
        self.assertIn("dedicated MCP binding verified", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as checked:
            self.assertEqual(config.cmd_check(), 0)
        self.assertIn("continuity overlay and dedicated MCP binding verified", checked.getvalue())

    def test_absent_mode_keeps_ordinary_base_install_unchanged(self) -> None:
        before = {"hooks": json.loads(json.dumps(self.base)), "theme": "preserve-me"}
        self.settings.write_text(json.dumps(before) + "\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_install(True), 0)
            self.assertEqual(config.cmd_check(), 0)
        self.assertEqual(json.loads(self.settings.read_text()), before)
        self.assertFalse(self.mode.exists())
        self.assertFalse(self.mcp.exists())

    def test_disabled_installed_mode_preserves_inert_overlay(self) -> None:
        self.activate(mode="disabled")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_install(True), 0)
            self.assertEqual(config.cmd_check(), 0)
        self.assertEqual(self.continuity_entry_count(), 5)
        self.assertEqual(json.loads(self.mode.read_text())["mode"], "disabled")

    def test_valid_mode_restores_missing_hooks(self) -> None:
        self.activate(include_hooks=False)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(config.cmd_install(True), 0)
        self.assertEqual(self.continuity_entry_count(), 5)
        self.assertIn("WILL RESTORE five canonical entries", output.getvalue())

    def test_stale_mode_tampered_hook_or_mcp_fails_without_overwrite(self) -> None:
        cases = []

        self.activate()
        stale = json.loads(self.mode.read_text())
        stale["config_digest"] = "sha256:" + "0" * 64
        self.mode.write_text(json.dumps(stale) + "\n")
        cases.append("stale mode")
        before = self.settings.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_install(True), 1)
        self.assertEqual(self.settings.read_bytes(), before)

        self.activate()
        tampered = json.loads(self.settings.read_text())
        entry = next(item for item in tampered["hooks"]["SessionStart"]
                     if "claude-continuity-hook.py" in json.dumps(item))
        entry["matcher"] = ".*"
        self.settings.write_text(json.dumps(tampered) + "\n")
        cases.append("tampered hook")
        before = self.settings.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_install(True), 1)
        self.assertEqual(self.settings.read_bytes(), before)

        self.activate()
        mcp = json.loads(self.mcp.read_text())
        mcp["mcpServers"]["carr-continuity"]["args"].append("--injected")
        self.mcp.write_text(json.dumps(mcp) + "\n")
        cases.append("tampered MCP")
        before = self.settings.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_install(True), 1)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(cases, ["stale mode", "tampered hook", "tampered MCP"])

    def test_dedicated_remove_remains_clean_and_idempotent(self) -> None:
        self.activate()
        installer_spec = importlib.util.spec_from_file_location(
            "continuity_installer_for_config_test", ROOT / "ops/install-claude-continuity.py")
        assert installer_spec and installer_spec.loader
        installer: Any = importlib.util.module_from_spec(installer_spec)
        installer_spec.loader.exec_module(installer)
        installer.REPO = self.repo
        installed = json.loads(self.settings.read_text())
        mcp = json.loads(self.mcp.read_text())
        removed = installer.remove_document(installed)
        removed_mcp = installer.remove_mcp_document(mcp)
        self.assertEqual(installer.remove_document(removed), removed)
        self.assertEqual(installer.remove_mcp_document(removed_mcp), removed_mcp)
        self.settings.write_text(json.dumps(removed) + "\n")
        self.mcp.write_text(json.dumps(removed_mcp) + "\n")
        self.mode.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_install(True), 0)
            self.assertEqual(config.cmd_check(), 0)
        self.assertEqual(self.continuity_entry_count(), 0)

    def test_pull_never_captures_continuity_overlay_into_base_source(self) -> None:
        self.activate()
        live = json.loads(self.settings.read_text())
        live["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 6
        self.settings.write_text(json.dumps(live) + "\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(config.cmd_pull(True), 0)
        captured = (self.repo / "ops/config/hooks.json").read_text()
        self.assertNotIn("claude-continuity-hook.py", captured)
        self.assertEqual(json.loads(captured)["PreToolUse"][0]["hooks"][0]["timeout"], 6)


if __name__ == "__main__":
    unittest.main()
