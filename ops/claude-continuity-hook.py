#!/usr/bin/env python3
"""Fail-open native Claude checkpoint adapter.

SessionStart hands the session its checkpoint binding (the arguments
mcp__carr-continuity__claude-checkpoint needs) and, after a compact or resume
in inject mode, the Worker-built recovery capsule. PreCompact and a
compact/resume SessionStart reset the rule-delivery dedupe generation. Native
session history is the record of everything else. Never sends transcript text;
always exits zero.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import claude_continuity_config as continuity_config  # noqa: E402
from lib.claude_rule_delivery_dedupe import reset as reset_rule_delivery  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
EVENTS = continuity_config.EVENTS
MAX_INPUT_BYTES = 1_000_000
MAX_CONTEXT_BYTES = 4_800
MAX_SESSION_FILES = 500
CALL_TIMEOUT_SECONDS = 7.0
NATIVE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")


def _warn(message: str) -> None:
    print(f"claude continuity warning: {message}", file=sys.stderr)


def _path(env_name: str, default: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.environ.get(env_name, str(default))).expanduser()


def mode_path() -> pathlib.Path:
    return _path("CARR_CLAUDE_CONTINUITY_MODE_FILE",
                 pathlib.Path.home() / ".config/carr/claude-continuity-mode.json")


def session_dir() -> pathlib.Path:
    return _path("CARR_CLAUDE_CONTINUITY_SESSION_DIR",
                 pathlib.Path.home() / ".config/carr/claude-continuity-session")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _read_mode() -> str:
    try:
        return continuity_config.read_mode(mode_path(), continuity_config.load(REPO)) or "disabled"
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        _warn(f"invalid mode; disabled ({exc.__class__.__name__})")
        return "disabled"


def pinned_affinity(transcript_path_digest: str, live: str) -> str:
    """Decide a session's project once.

    The Worker treats project affinity as part of an immutable leaf binding, so
    a session that reports a different value later is refused for the rest of
    its life. cwd is not stable (EnterWorktree, a directory change, a
    task-assigned clone), so the first observation wins.
    """
    directory = session_dir()
    path = directory / f"{transcript_path_digest}.affinity.json"

    def read() -> str | None:
        try:
            pinned = json.loads(path.read_bytes()).get("project_affinity")
        except (OSError, ValueError, AttributeError):
            return None
        return pinned if isinstance(pinned, str) and pinned else None

    existing = read()
    if existing is not None:
        return existing
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        old = sorted(directory.glob("*.affinity.json"), key=lambda p: p.stat().st_mtime_ns)
        for victim in old[:max(0, len(old) - MAX_SESSION_FILES)]:
            victim.unlink(missing_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".affinity-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(_canonical({"schema_version": 1, "project_affinity": live}))
            os.link(temp_name, path)  # link() refuses to clobber a concurrent pin
        except FileExistsError:
            pass
        finally:
            pathlib.Path(temp_name).unlink(missing_ok=True)
    except OSError as exc:
        _warn(f"affinity pin unavailable ({exc.__class__.__name__})")
        return live
    return read() or live


def _normalized_path(path: str) -> pathlib.Path:
    return pathlib.Path(unicodedata.normalize("NFC", os.path.realpath(os.path.expanduser(path))))


def _git_value(cwd: pathlib.Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                              text=True, timeout=1.0, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def project_affinity(cwd: pathlib.Path) -> str:
    common = _git_value(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    remote = _git_value(cwd, "remote", "get-url", "origin")
    if common:
        material = {"kind": "git", "common": str(_normalized_path(common)), "origin": remote or ""}
    else:
        material = {"kind": "directory", "root": str(cwd)}
    return "sha256:" + hashlib.sha256(_canonical(material)).hexdigest()


def _transcript_roots() -> tuple[pathlib.Path, ...]:
    configured = os.environ.get("CARR_CLAUDE_TRANSCRIPT_ROOTS")
    raw = configured.split(os.pathsep) if configured else [str(pathlib.Path.home() / ".claude/projects")]
    return tuple(_normalized_path(value) for value in raw if value)


def identity(payload: dict) -> tuple[dict, pathlib.Path]:
    session_id = payload.get("session_id")
    transcript_raw = payload.get("transcript_path")
    cwd_raw = payload.get("cwd")
    if not isinstance(session_id, str) or NATIVE_ID.fullmatch(session_id) is None:
        raise ValueError("invalid session id")
    if not isinstance(transcript_raw, str) or not 0 < len(transcript_raw) <= 4096:
        raise ValueError("invalid transcript path")
    if not isinstance(cwd_raw, str) or not 0 < len(cwd_raw) <= 4096:
        raise ValueError("invalid cwd")
    transcript, cwd = _normalized_path(transcript_raw), _normalized_path(cwd_raw)
    agent_id = payload.get("agent_id")
    if agent_id is not None and (not isinstance(agent_id, str) or NATIVE_ID.fullmatch(agent_id) is None):
        raise ValueError("invalid agent_id")
    if not any(transcript.is_relative_to(root) for root in _transcript_roots()):
        raise ValueError("transcript is outside configured Claude roots")
    if agent_id is None and transcript.stem != session_id:
        raise ValueError("transcript filename does not bind the Claude session")
    if agent_id is not None and transcript.stem not in {agent_id, f"agent-{agent_id}"}:
        raise ValueError("subagent transcript does not bind the native agent")
    digest = hashlib.sha256(str(transcript).encode("utf-8")).hexdigest()
    base = {"runtime": "claude", "session_id": session_id, "transcript_path_digest": digest,
            "project_affinity": pinned_affinity(digest, project_affinity(cwd)), "cwd": str(cwd)}
    if agent_id is not None:
        base["native_agent_id"] = agent_id
        base["parent_session_id"] = session_id
    model = payload.get("model")
    if isinstance(model, str) and 0 < len(model) <= 200:
        base["model_id"] = model
    return base, transcript


def call(verb: str, args: dict) -> dict | None:
    """One record-layer call through the claude-continuity profile; None on any failure."""
    raw = os.environ.get("CARR_CLAUDE_CONTINUITY_CALL")
    try:
        argv = shlex.split(raw) if raw else [str(REPO / "run.sh"), "call"]
    except ValueError:
        return None
    argv += [verb, json.dumps(args, separators=(",", ":"), ensure_ascii=False)]
    env = {**os.environ, "CARR_MCP_CLIENT_PROFILE": "claude-continuity"}
    try:
        proc = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True,
                              timeout=CALL_TIMEOUT_SECONDS, check=False)
        response = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        response = None
    if isinstance(response, dict) and response.get("ok") is True:
        return response
    _warn(f"{verb} unavailable")
    return None


def checkpoint_version(response: dict | None) -> int | None:
    if not response:
        return None
    checkpoint = response.get("checkpoint")
    if response.get("found") is False or checkpoint is None:
        return 0
    raw = checkpoint.get("checkpoint_version") if isinstance(checkpoint, dict) else None
    if isinstance(raw, str) and raw.isdigit() and not raw.startswith("0"):
        raw = int(raw)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= 2 ** 53 - 1 else None


def activation(ident: dict, transcript: pathlib.Path, response: dict | None) -> str:
    try:
        offset = transcript.stat().st_size
    except OSError:
        offset = 0
    checkpoint = (response or {}).get("checkpoint") or {}
    binding = {k: ident[k] for k in ("runtime", "session_id", "transcript_path_digest", "project_affinity",
                                     "parent_session_id", "native_agent_id") if k in ident}
    args = {**binding, "idempotency_key": secrets.token_hex(16),
            "expected_version": checkpoint_version(response),
            "compaction_generation": int(checkpoint.get("compaction_generation", 0)),
            "state": {"objective": "<objective>", "next_action": "<next action>",
                      "source_cursor": {"byte_offset": offset},
                      "source_observed_at": datetime.now(timezone.utc).isoformat()}}
    return "\n".join([
        "arguments=" + json.dumps(args, sort_keys=True, separators=(",", ":")),
        "At a task boundary, save one checkpoint with mcp__carr-continuity__claude-checkpoint: "
        "fill state, refresh cursor/time, new key per save (reuse only on retry). "
        "Null version: read claude-read-recovery first.",
        "Never infer completion from telemetry or replay pending effects.",
    ])


def emit(event: str, context: str) -> None:
    if len(context.encode("utf-8")) > MAX_CONTEXT_BYTES:
        _warn("context exceeded native byte bound")
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
                     separators=(",", ":"), ensure_ascii=False))


def main() -> int:
    if os.environ.get("CARR_GROK_RUN_READ_ONLY") == "1":
        try:
            sys.path.insert(0, str(REPO))
            from hooks.grok_invocation import bounded_grok_read_only
            if bounded_grok_read_only():
                return 0
        except ImportError:
            pass
    mode = _read_mode()
    if mode in {"disabled", "shadow"}:  # shadow: installed but inert
        return 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("hook input too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
            raise ValueError("unsupported event")
        ident, transcript = identity(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _warn(f"unverified native input ignored ({exc.__class__.__name__})")
        return 0
    event, source = payload["hook_event_name"], payload.get("source")
    if event == "PreCompact" or source in {"compact", "resume"}:
        reset_rule_delivery(ident["session_id"], ident["transcript_path_digest"])
    if event != "SessionStart":
        return 0
    response = call("claude-read-recovery", ident)
    context = activation(ident, transcript, response)
    capsule = (response or {}).get("capsule")
    if mode == "inject" and source in {"compact", "resume"} and isinstance(capsule, str) and capsule:
        context += "\n\n" + capsule
    emit("SessionStart", context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
