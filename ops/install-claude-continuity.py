#!/usr/bin/env python3
"""Narrow transactional installer for native Claude continuity hooks."""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import claude_continuity_config as continuity_config  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
MODES = continuity_config.MODES


def settings_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_SETTINGS_FILE",
                                       pathlib.Path.home() / ".claude/settings.json"))


def mode_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONTINUITY_MODE_FILE",
                                       pathlib.Path.home() / ".config/carr/claude-continuity-mode.json"))


def claude_config_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONFIG_FILE",
                                       pathlib.Path.home() / ".claude.json"))


def _load_json(path: pathlib.Path, missing: dict | None = None) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if missing is not None:
            return copy.deepcopy(missing)
        raise RuntimeError(f"missing required file: {path}")
    if len(raw) > 2_000_000:
        raise RuntimeError(f"configuration too large: {path}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"configuration root must be an object: {path}")
    return value


def desired_hooks() -> dict[str, list[dict]]:
    return continuity_config.load(REPO).hooks


def expected_config_digest() -> str:
    return continuity_config.load(REPO).config_digest


def mode_document(mode: str) -> dict:
    return continuity_config.mode_document(mode, continuity_config.load(REPO))


def desired_mcp_server() -> dict:
    return continuity_config.load(REPO).mcp_server


def install_document(current: dict) -> dict:
    return continuity_config.add_overlay(current, continuity_config.load(REPO))


def remove_document(current: dict) -> dict:
    return continuity_config.remove_overlay(current, continuity_config.load(REPO))


def install_mcp_document(current: dict) -> dict:
    return continuity_config.install_mcp(current, continuity_config.load(REPO))


def remove_mcp_document(current: dict) -> dict:
    return continuity_config.remove_mcp(current, continuity_config.load(REPO))


def _encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_atomic(path: pathlib.Path, data: bytes, failure_name: str) -> None:
    if os.environ.get("CARR_CLAUDE_CONTINUITY_INJECT_FAILURE") == failure_name:
        raise OSError(f"injected {failure_name} failure")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        pathlib.Path(temp_name).unlink(missing_ok=True)


def _restore(path: pathlib.Path, before: bytes | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
    else:
        _write_atomic(path, before, "never")


def apply_transaction(settings: dict, mcp_config: dict, mode: str | None, remove: bool) -> None:
    target_settings, target_mode, target_mcp = settings_path(), mode_path(), claude_config_path()
    before_settings = target_settings.read_bytes() if target_settings.exists() else None
    before_mode = target_mode.read_bytes() if target_mode.exists() else None
    before_mcp = target_mcp.read_bytes() if target_mcp.exists() else None
    try:
        _write_atomic(target_settings, _encoded(settings), "settings")
        if remove:
            if os.environ.get("CARR_CLAUDE_CONTINUITY_INJECT_FAILURE") == "mode":
                raise OSError("injected mode failure")
            target_mode.unlink(missing_ok=True)
        else:
            assert mode is not None
            _write_atomic(target_mode, _encoded(mode_document(mode)), "mode")
        _write_atomic(target_mcp, _encoded(mcp_config), "mcp")
        # Parse both resources after the transaction before declaring success.
        _load_json(target_settings)
        if not remove:
            assert mode is not None
            parsed_mode = _load_json(target_mode)
            if parsed_mode != mode_document(mode):
                raise RuntimeError("mode verification failed")
        if _load_json(target_mcp) != mcp_config:
            raise RuntimeError("MCP configuration verification failed")
    except Exception:
        _restore(target_settings, before_settings)
        _restore(target_mode, before_mode)
        _restore(target_mcp, before_mcp)
        raise


def verify() -> tuple[bool, str]:
    current = _load_json(settings_path(), missing={})
    hooks = current.get("hooks", {})
    contract = continuity_config.load(REPO)
    try:
        continuity_config.validate_hooks(hooks, contract, require_complete=True)
        mode_name = continuity_config.read_mode(mode_path(), contract)
    except RuntimeError as exc:
        return False, str(exc)
    if mode_name is None:
        return False, "continuity mode is invalid"
    try:
        continuity_config.validate_mcp(_load_json(claude_config_path()), contract, required=True)
    except RuntimeError as exc:
        return False, str(exc)
    return True, f"all five Claude continuity hooks and dedicated MCP server match; mode={mode_name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove", "verify"))
    parser.add_argument("--mode", choices=sorted(MODES), default="shadow")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            ok, message = verify()
            print(message)
            return 0 if ok else 1
        # The mode receipt is the authority for the installed overlay, and it
        # goes stale by design whenever the adapter, hook config or proxy
        # changes.  Refusing to act on a stale receipt deadlocked the only tool
        # able to refresh one: an upgraded adapter left the hook reading a stale
        # receipt (so continuity silently disabled itself) while both install
        # and remove refused to run.  An explicit install or remove IS the
        # repair, so it proceeds and says what it replaced.  The overlay itself
        # is still protected — validate_hooks refuses a noncanonical overlay
        # independently of the receipt, and the hook keeps failing safe on a
        # stale receipt.  Absence remains valid for a first install.
        try:
            continuity_config.read_mode(mode_path(), continuity_config.load(REPO))
        except RuntimeError as exc:
            print(f"NOTE: replacing a stale continuity mode receipt ({exc})")
        current = _load_json(settings_path(), missing={})
        updated = remove_document(current) if args.action == "remove" else install_document(current)
        mcp_current = _load_json(claude_config_path(), missing={})
        mcp_updated = (remove_mcp_document(mcp_current) if args.action == "remove"
                       else install_mcp_document(mcp_current))
        if not args.apply:
            print(f"DRY RUN: would {args.action} Claude continuity hooks; settings and mode unchanged")
            return 0
        apply_transaction(updated, mcp_updated, None if args.action == "remove" else args.mode,
                          args.action == "remove")
        print(f"{args.action.upper()} OK: unrelated Claude settings and MCP servers preserved")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
