#!/usr/bin/env python3
"""Exercise Codex-only hook installation and persisted trust through CLI dispatch."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "config_as_code_for_codex_continuity", REPO / "ops" / "config-as-code.py"
)
assert spec and spec.loader
mod: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _hook(command: str, matcher: str | None = None) -> dict:
    group: dict[str, Any] = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


class FakeAppServer:
    """Model the two app-server methods at the installer's narrow trust seam."""

    EVENT_NAMES = {
        "PreCompact": ("preCompact", "pre_compact"),
        "PostCompact": ("postCompact", "post_compact"),
        "SessionStart": ("sessionStart", "session_start"),
        "UserPromptSubmit": ("userPromptSubmit", "user_prompt_submit"),
        "Stop": ("stop", "stop"),
    }

    def __init__(self, hooks_path: Path, config_path: Path):
        self.hooks_path = hooks_path
        self.config_path = config_path
        self.batch_calls: list[dict[str, Any]] = []
        self.extra_hooks: list[dict[str, Any]] = []
        self.hook_overrides: dict[str, dict[str, Any]] = {}
        self.fail_after_batch = False
        self.fail_trusted_list = False

    def _version(self) -> str:
        return "sha256:" + hashlib.sha256(self.config_path.read_bytes()).hexdigest()

    def _config(self) -> dict[str, Any]:
        return tomllib.loads(self.config_path.read_text(encoding="utf-8"))

    def _hooks(self) -> list[dict[str, Any]]:
        document = json.loads(self.hooks_path.read_text(encoding="utf-8"))
        state = self._config().get("hooks", {}).get("state", {})
        out = []
        for source_event, groups in document.get("hooks", {}).items():
            if source_event not in self.EVENT_NAMES or not isinstance(groups, list):
                continue
            event_name, key_event = self.EVENT_NAMES[source_event]
            for group_index, group in enumerate(groups):
                for hook_index, hook in enumerate(group.get("hooks", [])):
                    command = hook.get("command")
                    key = f"{self.hooks_path}:{key_event}:{group_index}:{hook_index}"
                    material = json.dumps(
                        {"event": source_event, "group": group, "hook": hook},
                        sort_keys=True, separators=(",", ":"),
                    )
                    current_hash = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
                    trusted = state.get(key, {}).get("trusted_hash") == current_hash
                    out.append({
                        "key": key,
                        "eventName": event_name,
                        "command": command,
                        "source": "user",
                        "sourcePath": str(self.hooks_path),
                        "handlerType": hook.get("type"),
                        "matcher": group.get("matcher"),
                        "timeoutSec": hook.get("timeout"),
                        "enabled": True,
                        "currentHash": current_hash,
                        "trustStatus": "trusted" if trusted else "untrusted",
                    })
        for hook in out:
            hook.update(self.hook_overrides.get(hook["eventName"], {}))
        return out + self.extra_hooks

    @staticmethod
    def _quoted_key(key_path: str, suffix: str) -> str:
        match = re.fullmatch(rf'hooks\.state\.("(?:\\.|[^"])+"){suffix}', key_path)
        assert match, key_path
        return json.loads(match.group(1))

    def __call__(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "hooks/list":
            hooks = self._hooks()
            if self.fail_trusted_list and any(
                    hook["trustStatus"] == "trusted" for hook in hooks):
                self.fail_trusted_list = False
                raise RuntimeError("injected hooks/list verification failure")
            return {"data": [{"cwd": params["cwds"][0], "hooks": hooks,
                               "errors": [], "warnings": []}]}
        if method == "config/read":
            return {"config": self._config(), "origins": {}, "layers": [{
                "name": {"type": "user", "file": str(self.config_path), "profile": None},
                "version": self._version(), "config": self._config(),
            }]}
        if method != "config/batchWrite":
            raise AssertionError(f"unexpected app-server method: {method}")
        assert params["filePath"] == str(self.config_path)
        assert params["expectedVersion"] == self._version()
        assert params["reloadUserConfig"] is True
        self.batch_calls.append(params)
        raw = self.config_path.read_text(encoding="utf-8")
        for edit in params["edits"]:
            assert edit["mergeStrategy"] in {"upsert", "replace"}
            if edit["keyPath"].endswith(".trusted_hash"):
                key = self._quoted_key(edit["keyPath"], r"\.trusted_hash")
                value = edit["value"]
                assert re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                raw = (raw.rstrip() + f"\n\n[hooks.state.{json.dumps(key)}]\n"
                       f"trusted_hash = {json.dumps(value)}\n")
            else:
                key = self._quoted_key(edit["keyPath"], "")
                header = re.escape(f"[hooks.state.{json.dumps(key)}]")
                raw, count = re.subn(
                    rf"(?m)^{header}\ntrusted_hash = \"sha256:[0-9a-f]{{64}}\"\n?", "", raw,
                )
                assert count in {0, 1}, (key, count)
                value = edit["value"]
                if value is not None:
                    assert value.keys() == {"trusted_hash"}
                    raw = (raw.rstrip() + f"\n\n[hooks.state.{json.dumps(key)}]\n"
                           f"trusted_hash = {json.dumps(value['trusted_hash'])}\n")
        self.config_path.write_text(raw, encoding="utf-8")
        if self.fail_after_batch:
            self.fail_after_batch = False
            raise RuntimeError("injected config/batchWrite post-write failure")
        return {"status": "ok", "version": self._version(),
                "filePath": str(self.config_path), "overriddenMetadata": None}


def main() -> int:
    continuity = "/carr-system/ops/codex-continuity-hook.py"
    source = json.loads((REPO / "ops/config/codex-hooks.json").read_text().replace(
        "{{REPO}}", "/carr-system"))
    session_groups = source["hooks"]["SessionStart"]
    assert len(session_groups) == 3
    continuity_group = session_groups[1]
    unrelated_groups = [session_groups[0], session_groups[2]]
    # The live file still carries a continuity hook on an event the adapter
    # retired; install must clear it and keep the unrelated hook beside it.
    live: dict[str, Any] = {
        "hooks": {
            "SessionStart": unrelated_groups,
            "PreCompact": [_hook("/other/project/keep.py"), _hook(continuity)],
            "Stop": [_hook("/other/project/stop.py")],
        },
        "permissions": {"keep": True},
    }
    with tempfile.TemporaryDirectory(prefix="carr-codex-continuity-") as temp:
        root = Path(temp)
        repo_source = root / "tracked-codex-hooks.json"
        codex_live = root / ".codex" / "hooks.json"
        claude_settings = root / ".claude" / "settings.json"
        codex_config = root / ".codex" / "config.toml"
        repo_source.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
        codex_live.parent.mkdir()
        claude_settings.parent.mkdir()
        original_hooks = json.dumps(live, indent=2) + "\n"
        codex_live.write_text(original_hooks, encoding="utf-8")
        original_claude = b'{"never":"touch"}\n'
        claude_settings.write_bytes(original_claude)
        unrelated_hash = "sha256:" + "a" * 64
        original_config = (
            '# unrelated comment must survive\nmodel = "keep-me"\n'
            'released_at = 2026-09-05T14:30:00Z\n\n[custom]\nvalue = 7\n\n'
            '[hooks.state."/other/hooks.json:stop:0:0"]\n'
            f'trusted_hash = "{unrelated_hash}"\n'
        )
        codex_config.write_text(original_config, encoding="utf-8")

        mod.REPO = str(root)
        mod.CODEX_HOOKS_REPO = str(repo_source)
        mod.CODEX_HOOKS_SRC = str(codex_live)
        mod.SETTINGS = str(claude_settings)
        mod.CODEX_CONFIG = str(codex_config)
        app_server = FakeAppServer(codex_live, codex_config)
        mod.codex_app_server_request = app_server

        invalid_sources = []
        missing = copy.deepcopy(source)
        missing["hooks"]["SessionStart"] = unrelated_groups
        invalid_sources.append(missing)
        duplicate_group = copy.deepcopy(source)
        duplicate_group["hooks"]["SessionStart"].append(copy.deepcopy(continuity_group))
        invalid_sources.append(duplicate_group)
        duplicate_handler = copy.deepcopy(source)
        duplicate_handler["hooks"]["SessionStart"][1]["hooks"] *= 2
        invalid_sources.append(duplicate_handler)
        for field, value in (("type", "prompt"), ("timeout", True), ("timeout", "10")):
            malformed = copy.deepcopy(source)
            malformed["hooks"]["SessionStart"][1]["hooks"][0][field] = value
            invalid_sources.append(malformed)
        for invalid in invalid_sources:
            repo_source.write_text(json.dumps(invalid))
            mod.sys.argv = ["config-as-code.py", "install-codex-continuity"]
            assert mod.main() == 1
            assert codex_live.read_text() == original_hooks
            assert codex_config.read_text() == original_config
            assert not app_server.batch_calls
        repo_source.write_text(json.dumps(source))

        desired, contracts = mod.canonical_codex_continuity_hooks()
        assert desired == {"hooks": {"SessionStart": [continuity_group]}}
        assert len(contracts) == 1
        mixed = copy.deepcopy(source)
        mixed["hooks"]["SessionStart"][1]["hooks"].append(_hook("/other/mixed.py")["hooks"][0])
        repo_source.write_text(json.dumps(mixed))
        assert mod.canonical_codex_continuity_hooks() == (desired, contracts)
        repo_source.write_text(json.dumps(source))

        mod.sys.argv = ["config-as-code.py", "install-codex-continuity"]
        assert mod.main() == 0
        assert codex_live.read_text(encoding="utf-8") == original_hooks
        assert codex_config.read_text(encoding="utf-8") == original_config
        assert not app_server.batch_calls

        app_server.fail_trusted_list = True
        mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
        assert mod.main() == 1
        assert codex_live.read_text(encoding="utf-8") == original_hooks
        assert tomllib.loads(codex_config.read_text(encoding="utf-8")) == tomllib.loads(
            original_config)
        assert "# unrelated comment must survive" in codex_config.read_text(encoding="utf-8")
        assert len(app_server.batch_calls) == 2  # failed write plus selective rollback
        assert claude_settings.read_bytes() == original_claude
        app_server.batch_calls = []

        mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
        assert mod.main() == 0
        installed = json.loads(codex_live.read_text(encoding="utf-8"))
        assert installed["permissions"] == {"keep": True}
        assert installed["hooks"]["Stop"] == live["hooks"]["Stop"]
        assert installed["hooks"]["PreCompact"] == [_hook("/other/project/keep.py")]
        assert installed["hooks"]["SessionStart"] == unrelated_groups + [continuity_group]
        assert len(app_server.batch_calls) == 1
        assert len(app_server.batch_calls[0]["edits"]) == 1
        parsed_config = tomllib.loads(codex_config.read_text(encoding="utf-8"))
        assert parsed_config["model"] == "keep-me" and parsed_config["custom"] == {"value": 7}
        assert parsed_config["released_at"].isoformat() == "2026-09-05T14:30:00+00:00"
        assert parsed_config["hooks"]["state"]["/other/hooks.json:stop:0:0"] == {
            "trusted_hash": unrelated_hash}
        assert all(hook["trustStatus"] == "trusted" for hook in app_server._hooks()
                   if continuity in (hook.get("command") or ""))
        assert claude_settings.read_bytes() == original_claude

        mod.sys.argv = ["config-as-code.py", "verify-codex-continuity"]
        assert mod.main() == 0
        assert len(app_server.batch_calls) == 1

        after_first_hooks = codex_live.read_bytes()
        after_first_config = codex_config.read_bytes()
        mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
        assert mod.main() == 0
        assert codex_live.read_bytes() == after_first_hooks
        assert codex_config.read_bytes() == after_first_config
        assert len(app_server.batch_calls) == 1

        for field, value in (
            ("eventName", "preCompact"),
            ("matcher", "injected"),
            ("command", f"/bin/sh -c '{continuity}'"),
            ("handlerType", "prompt"),
            ("timeoutSec", 11),
        ):
            app_server.hook_overrides = {"sessionStart": {field: value}}
            mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
            assert mod.main() == 1, field
            assert codex_live.read_bytes() == after_first_hooks
            assert codex_config.read_bytes() == after_first_config
            assert len(app_server.batch_calls) == 1
        app_server.hook_overrides = {}

        duplicate = dict(next(hook for hook in app_server._hooks()
                              if hook["eventName"] == "sessionStart" and
                              continuity in (hook.get("command") or "")))
        duplicate["key"] += ":duplicate"
        app_server.extra_hooks = [duplicate]
        mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
        assert mod.main() == 1
        assert codex_live.read_bytes() == after_first_hooks
        assert codex_config.read_bytes() == after_first_config
        assert len(app_server.batch_calls) == 1
        app_server.extra_hooks = []

        injected = dict(duplicate)
        injected["key"] = f"{codex_live}:session_start:injected:0"
        injected["command"] = f"/bin/sh -c '{continuity}'"
        injected["currentHash"] = "sha256:" + "f" * 64
        injected["trustStatus"] = "untrusted"
        app_server.extra_hooks = [injected]
        mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
        assert mod.main() == 0
        assert injected["key"] not in tomllib.loads(
            codex_config.read_text(encoding="utf-8"))["hooks"]["state"]
        assert len(app_server.batch_calls) == 1
        app_server.extra_hooks = []

        # Rollback is selective; unrelated hooks and trust entries added before
        # removal remain represented after continuity is removed.
        updated = json.loads(codex_live.read_bytes())
        updated["hooks"].setdefault("PostCompact", []).append(_hook("/other/new-hook.py"))
        codex_live.write_text(json.dumps(updated, indent=2) + "\n")
        before_remove_hooks = codex_live.read_bytes()
        before_remove_config = codex_config.read_bytes()
        mod.sys.argv = ["config-as-code.py", "remove-codex-continuity"]
        assert mod.main() == 0
        assert codex_live.read_bytes() == before_remove_hooks
        assert codex_config.read_bytes() == before_remove_config
        mod.sys.argv.append("--apply")
        before_failed_remove_calls = len(app_server.batch_calls)
        real_hook_write = mod._write_codex_hooks_text
        hook_write_calls = 0

        def fail_hook_write_once(raw: str | None) -> None:
            nonlocal hook_write_calls
            hook_write_calls += 1
            if hook_write_calls == 1:
                raise OSError("injected hooks.json write failure")
            real_hook_write(raw)

        mod._write_codex_hooks_text = fail_hook_write_once
        assert mod.main() == 1
        mod._write_codex_hooks_text = real_hook_write
        assert codex_live.read_bytes() == before_remove_hooks
        assert tomllib.loads(codex_config.read_text(encoding="utf-8")) == tomllib.loads(
            before_remove_config.decode())
        assert "# unrelated comment must survive" in codex_config.read_text(encoding="utf-8")
        assert len(app_server.batch_calls) == before_failed_remove_calls + 2
        assert all(hook["trustStatus"] == "trusted" for hook in app_server._hooks()
                   if continuity in (hook.get("command") or ""))
        assert claude_settings.read_bytes() == original_claude

        assert mod.main() == 0
        removed = json.loads(codex_live.read_text())
        assert removed["hooks"]["PostCompact"] == [_hook("/other/new-hook.py")]
        assert removed["hooks"]["PreCompact"] == [_hook("/other/project/keep.py")]
        assert removed["hooks"]["Stop"] == live["hooks"]["Stop"]
        assert removed["hooks"]["SessionStart"] == unrelated_groups
        assert removed["permissions"] == {"keep": True}
        parsed_config = tomllib.loads(codex_config.read_text(encoding="utf-8"))
        assert parsed_config["model"] == "keep-me" and parsed_config["custom"] == {"value": 7}
        assert parsed_config["hooks"]["state"] == {
            "/other/hooks.json:stop:0:0": {"trusted_hash": unrelated_hash}}
        assert all(edit["value"] is None for edit in app_server.batch_calls[-1]["edits"])
        assert claude_settings.read_bytes() == original_claude

        mod.sys.argv = ["config-as-code.py", "verify-codex-continuity"]
        assert mod.main() == 1
        after_remove_hooks = codex_live.read_bytes()
        after_remove_config = codex_config.read_bytes()
        mod.sys.argv = ["config-as-code.py", "remove-codex-continuity", "--apply"]
        assert mod.main() == 0
        assert codex_live.read_bytes() == after_remove_hooks
        assert codex_config.read_bytes() == after_remove_config

    print("codex-continuity-installer-selftest: 1/1 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
