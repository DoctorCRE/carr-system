"""Canonical local configuration contract for native Claude continuity.

The dedicated installer owns the continuity overlay, mode receipt, and narrow
MCP binding.  Other local configuration tools consume this module so they can
recognize that overlay without copying it into their own source of truth.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
from dataclasses import dataclass

MODES = frozenset({"disabled", "shadow", "checkpoint", "inject"})
ACTIVE_MODES = frozenset({"shadow", "checkpoint", "inject"})
EVENTS = frozenset({"UserPromptSubmit", "PostToolUse", "PreCompact", "SessionStart", "Stop"})
HOOK_BASENAME = "/ops/claude-continuity-hook.py"
MCP_SERVER_NAME = "carr-continuity"
MAX_CONFIG_BYTES = 2_000_000
MAX_MODE_BYTES = 4096


@dataclass(frozen=True)
class Contract:
    repo: pathlib.Path
    hooks: dict[str, list[dict]]
    config_digest: str
    mcp_server: dict


def _read_json(path: pathlib.Path, *, limit: int, missing: dict | None = None) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if missing is not None:
            return copy.deepcopy(missing)
        raise RuntimeError(f"missing required file: {path}")
    if len(raw) > limit:
        raise RuntimeError(f"configuration too large: {path}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"configuration root must be an object: {path}")
    return value


def load(repo: pathlib.Path | str) -> Contract:
    root = pathlib.Path(repo)
    source = root / "ops/config/claude-continuity-hooks.json"
    adapter = root / "ops/claude-continuity-hook.py"
    proxy = root / "mcp-server/continuity-stdio-proxy.mjs"
    try:
        rendered = source.read_text(encoding="utf-8").replace("{{REPO}}", str(root))
        document = json.loads(rendered)
        adapter_bytes = adapter.read_bytes()
        proxy_bytes = proxy.read_bytes()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("canonical Claude continuity configuration is unreadable") from exc
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(hooks, dict) or set(hooks) != EVENTS:
        raise RuntimeError("canonical Claude continuity hook event set is invalid")
    digest = hashlib.sha256(rendered.encode("utf-8") + b"\0" + adapter_bytes
                            + b"\0" + proxy_bytes).hexdigest()
    mcp_server = {
        "type": "stdio",
        "command": "/usr/bin/env",
        "args": ["node", str(proxy)],
        "env": {"CARR_MCP_CLIENT_PROFILE": "claude-continuity"},
    }
    return Contract(root, hooks, "sha256:" + digest, mcp_server)


def mode_document(mode: str, contract: Contract) -> dict:
    if mode not in MODES:
        raise RuntimeError(f"unknown Claude continuity mode: {mode}")
    return {"schema_version": 1, "mode": mode,
            "config_digest": contract.config_digest}


def read_mode(path: pathlib.Path | str, contract: Contract) -> str | None:
    target = pathlib.Path(path)
    if not target.exists():
        return None
    document = _read_json(target, limit=MAX_MODE_BYTES)
    mode = document.get("mode")
    if mode not in MODES or document != mode_document(mode, contract):
        raise RuntimeError("Claude continuity mode document is stale or noncanonical")
    return mode


def _commands(entry: object) -> list[str]:
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        return []
    return [hook["command"] for hook in hooks
            if isinstance(hook, dict) and isinstance(hook.get("command"), str)]


def mentions_continuity(entry: object) -> bool:
    return any(HOOK_BASENAME in command for command in _commands(entry))


def observed_entries(entries: object) -> list:
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if mentions_continuity(entry)]


def validate_hooks(hooks: object, contract: Contract, *, require_complete: bool) -> None:
    if not isinstance(hooks, dict):
        raise RuntimeError("Claude settings hooks must be an object")
    for event, entries in hooks.items():
        observed = observed_entries(entries)
        wanted = contract.hooks.get(event)
        if observed and (wanted is None or observed != wanted):
            raise RuntimeError(f"noncanonical Claude continuity hook present: {event}")
    if require_complete:
        for event, wanted in contract.hooks.items():
            entries = hooks.get(event)
            if not isinstance(entries, list) or observed_entries(entries) != wanted:
                raise RuntimeError(f"installed Claude continuity hook is missing: {event}")


def has_overlay(hooks: object) -> bool:
    return (isinstance(hooks, dict)
            and any(observed_entries(entries) for entries in hooks.values()))


def add_overlay(document: dict, contract: Contract) -> dict:
    result = copy.deepcopy(document)
    hooks = result.setdefault("hooks", {})
    validate_hooks(hooks, contract, require_complete=False)
    for event, wanted in contract.hooks.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise RuntimeError(f"settings hook event must be a list: {event}")
        if not observed_entries(entries):
            entries.extend(copy.deepcopy(wanted))
    return result


def remove_overlay(document: dict, contract: Contract) -> dict:
    result = copy.deepcopy(document)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    validate_hooks(hooks, contract, require_complete=False)
    for event, wanted in contract.hooks.items():
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        hooks[event] = [entry for entry in entries if entry not in wanted]
    return result


def render_effective_hooks(base: dict, live: dict, contract: Contract,
                           mode: str | None) -> dict:
    """Render base plus an installed overlay while refusing ambiguous live state."""
    validate_hooks(live, contract, require_complete=False)
    if mode not in MODES:
        if has_overlay(live):
            raise RuntimeError(
                "Claude continuity hooks exist without a valid installed mode; "
                "run the dedicated continuity remover"
            )
        return copy.deepcopy(base)
    validate_hooks(base, contract, require_complete=False)
    if has_overlay(base):
        raise RuntimeError("tracked base hooks must not contain Claude continuity hooks")
    return add_overlay({"hooks": base}, contract)["hooks"]


def strip_installed_overlay(live: dict, contract: Contract, mode: str | None) -> dict:
    """Return the base projection for drift comparison and repository capture."""
    if mode not in MODES:
        if has_overlay(live):
            raise RuntimeError("Claude continuity hooks exist without a valid installed mode")
        return copy.deepcopy(live)
    validate_hooks(live, contract, require_complete=True)
    result = remove_overlay({"hooks": live}, contract)["hooks"]
    return {event: entries for event, entries in result.items() if entries}


def validate_mcp(document: dict, contract: Contract, *, required: bool) -> None:
    servers = document.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise RuntimeError("Claude mcpServers must be an object")
    observed = servers.get(MCP_SERVER_NAME)
    if observed is not None and observed != contract.mcp_server:
        raise RuntimeError("noncanonical carr-continuity MCP server present")
    if required and observed != contract.mcp_server:
        raise RuntimeError("active Claude continuity mode requires the canonical MCP server")


def install_mcp(document: dict, contract: Contract) -> dict:
    validate_mcp(document, contract, required=False)
    result = copy.deepcopy(document)
    result.setdefault("mcpServers", {})[MCP_SERVER_NAME] = copy.deepcopy(contract.mcp_server)
    return result


def remove_mcp(document: dict, contract: Contract) -> dict:
    validate_mcp(document, contract, required=False)
    result = copy.deepcopy(document)
    servers = result.get("mcpServers")
    if isinstance(servers, dict):
        servers.pop(MCP_SERVER_NAME, None)
        if not servers:
            result.pop("mcpServers", None)
    return result
