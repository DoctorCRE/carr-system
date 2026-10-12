#!/usr/bin/env python3
"""Fail-open native Claude continuity adapter.

The adapter never sends transcript text.  It binds a Claude session leaf to a
normalized transcript-path digest, records bounded lifecycle receipts, and
injects only a Worker-built recovery capsule on compact/resume SessionStart.
Stop is always advisory and this process always exits zero.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import claude_continuity_config as continuity_config  # noqa: E402
from lib import claude_continuity_spool as spool  # noqa: E402
from lib.claude_rule_delivery_dedupe import reset as reset_rule_delivery  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
HOOK_CONFIG = REPO / "ops/config/claude-continuity-hooks.json"
MCP_PROXY = REPO / "mcp-server/continuity-stdio-proxy.mjs"
MODES = continuity_config.MODES
EVENTS = {"UserPromptSubmit", "PostToolUse", "PreCompact", "SessionStart", "Stop"}
EVENT_MAP = {
    "UserPromptSubmit": "user_prompt_submit", "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact", "Stop": "stop",
}
MAX_INPUT_BYTES = 1_000_000
MAX_TAIL_BYTES = 262_144
MAX_CAPSULE_BYTES = 4_800
MAX_NUDGE_BYTES = 2_400
MAX_SESSION_FILES = 500
# Observed on this machine 2026-09-11: the earliest native compaction in 1821
# transcripts happened at 5.46 MB, the deepest at 21 MB.  Asking at 2 MB and
# again every 2 MB puts at least two checkpoint prompts before the earliest
# boundary ever seen here, without nagging an ordinary session (median 572 KB).
NUDGE_FIRST_BYTES = int(os.environ.get("CARR_CLAUDE_CONTINUITY_NUDGE_FIRST", 2_000_000))
NUDGE_INTERVAL_BYTES = int(os.environ.get("CARR_CLAUDE_CONTINUITY_NUDGE_INTERVAL", 2_000_000))
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


def expected_config_digest() -> str:
    return continuity_config.load(REPO).config_digest


def _prune(directory: pathlib.Path, keep: int, suffix: str) -> None:
    files = sorted(directory.glob(f"*{suffix}"), key=lambda path: path.stat().st_mtime_ns)
    for victim in files[:max(0, len(files) - keep)]:
        victim.unlink(missing_ok=True)


def pinned_affinity(transcript_path_digest: str, live: str) -> str:
    """Decide a session's project once.

    The Worker treats project affinity as part of an immutable leaf binding, so
    a session that reports a different value later is refused for the rest of
    its life.  cwd is not stable — EnterWorktree, a directory change, or a
    task-assigned clone all move it — so the first observation wins and every
    later cwd travels as telemetry instead.
    """
    directory = session_dir()
    path = directory / f"{transcript_path_digest}.affinity.json"

    def read() -> str | None:
        try:
            document = json.loads(path.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        pinned = document.get("project_affinity")
        return pinned if isinstance(pinned, str) and pinned else None

    existing = read()
    if existing is not None:
        return existing
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _prune(directory, MAX_SESSION_FILES, ".affinity.json")
        fd, temp_name = tempfile.mkstemp(prefix=".affinity-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(_canonical({"schema_version": 1, "project_affinity": live}))
                handle.flush()
                os.fsync(handle.fileno())
            # link() refuses to clobber, so a concurrent hook cannot repin.
            os.link(temp_name, path)
        except FileExistsError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            pathlib.Path(temp_name).unlink(missing_ok=True)
    except OSError as exc:
        _warn(f"affinity pin unavailable ({exc.__class__.__name__})")
        return live
    return read() or live


def _nudge_path(transcript_path_digest: str) -> pathlib.Path:
    return session_dir() / f"{transcript_path_digest}.nudge.json"


def due_for_nudge(transcript_path_digest: str, byte_offset: int) -> bool:
    """Ask again only once per interval of real transcript growth."""
    if byte_offset < NUDGE_FIRST_BYTES:
        return False
    try:
        document = json.loads(_nudge_path(transcript_path_digest).read_bytes())
        last = document.get("byte_offset")
    except (OSError, ValueError, json.JSONDecodeError):
        last = None
    if isinstance(last, int) and byte_offset - last < NUDGE_INTERVAL_BYTES:
        return False
    return True


def mark_nudged(transcript_path_digest: str, byte_offset: int) -> None:
    directory = session_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _prune(directory, MAX_SESSION_FILES, ".nudge.json")
        fd, temp_name = tempfile.mkstemp(prefix=".nudge-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(_canonical({"schema_version": 1, "byte_offset": byte_offset}))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, _nudge_path(transcript_path_digest))
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            pathlib.Path(temp_name).unlink(missing_ok=True)
    except OSError as exc:
        _warn(f"nudge state unavailable ({exc.__class__.__name__})")


def unsent_receipts() -> int:
    return len(spool.spooled())


def _read_mode() -> str:
    try:
        return continuity_config.read_mode(
            mode_path(), continuity_config.load(REPO)
        ) or "disabled"
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        _warn(f"invalid mode; disabled ({exc.__class__.__name__})")
        return "disabled"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _normalized_path(path: str) -> pathlib.Path:
    normalized = unicodedata.normalize("NFC", os.path.realpath(os.path.expanduser(path)))
    return pathlib.Path(normalized)


def _path_digest(path: pathlib.Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


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
    roots = tuple(_normalized_path(value) for value in raw if value)
    if not roots or len(roots) > 8:
        raise ValueError("invalid transcript roots")
    return roots


def _validate_transcript_location(transcript: pathlib.Path, session_id: str,
                                  agent_id: str | None) -> None:
    if not any(transcript.is_relative_to(root) for root in _transcript_roots()):
        raise ValueError("transcript is outside configured Claude roots")
    if agent_id is None:
        if transcript.stem != session_id:
            raise ValueError("transcript filename does not bind the Claude session")
        return
    if transcript.parent.name != "subagents" or transcript.stem not in {agent_id, f"agent-{agent_id}"}:
        raise ValueError("subagent transcript does not bind the native agent")


def source_cursor(transcript: pathlib.Path) -> tuple[dict, str]:
    fd = os.open(transcript, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        named_before = os.stat(transcript, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(named_before.st_mode)
                or (before.st_dev, before.st_ino) != (named_before.st_dev, named_before.st_ino)):
            raise ValueError("transcript path identity is unverified")
        size = before.st_size
        start = max(0, size - MAX_TAIL_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        tail = os.read(fd, MAX_TAIL_BYTES + 1)
        after = os.fstat(fd)
        named_after = os.stat(transcript, follow_symlinks=False)
    finally:
        os.close(fd)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)):
        raise ValueError("transcript changed during bounded read")
    if len(tail) > MAX_TAIL_BYTES:
        raise ValueError("bounded transcript tail changed while reading")
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    digest.update(b"\0")
    digest.update(tail)
    source_digest = digest.hexdigest()
    return ({"byte_offset": size, "device": before.st_dev, "inode": before.st_ino,
             "mtime_ns": before.st_mtime_ns, "tail_start": start,
             "source_digest": source_digest}, source_digest)


def startup_source_cursor(transcript: pathlib.Path, identity: dict) -> tuple[dict, str]:
    """Bind a main-session startup before Claude creates its transcript inode."""
    if identity.get("native_agent_id") is not None or transcript.suffix != ".jsonl":
        raise ValueError("pending startup transcript is not a main-session JSONL path")
    roots = _transcript_roots()
    if not any(transcript.parent.parent == root for root in roots):
        raise ValueError("pending startup transcript lacks a direct Claude project parent")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(transcript.parent, flags)
    try:
        before = os.fstat(parent_fd)
        named_before = os.stat(transcript.parent, follow_symlinks=False)
        if (not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(named_before.st_mode)
                or (before.st_dev, before.st_ino) != (named_before.st_dev, named_before.st_ino)):
            raise ValueError("pending startup transcript parent is unverified")
        try:
            os.stat(transcript.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("pending startup transcript unexpectedly exists")
        after = os.fstat(parent_fd)
        named_after = os.stat(transcript.parent, follow_symlinks=False)
        if ((before.st_dev, before.st_ino, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_mtime_ns)
                or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)):
            raise ValueError("pending startup transcript parent changed during verification")
        try:
            os.stat(transcript.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("pending startup transcript appeared during verification")
    finally:
        os.close(parent_fd)
    source_digest = hashlib.sha256(_canonical({
        "kind": "claude_startup_pending_v1",
        "transcript_path_digest": identity["transcript_path_digest"],
    })).hexdigest()
    return ({"byte_offset": 0, "mtime_ns": 0, "source_digest": source_digest,
             "startup_pending": True}, source_digest)


def _identity(payload: dict) -> tuple[dict, pathlib.Path]:
    session_id = payload.get("session_id")
    transcript_raw = payload.get("transcript_path")
    cwd_raw = payload.get("cwd")
    if not isinstance(session_id, str) or NATIVE_ID.fullmatch(session_id) is None:
        raise ValueError("invalid session id")
    if not isinstance(transcript_raw, str) or not transcript_raw or len(transcript_raw) > 4096:
        raise ValueError("invalid transcript path")
    if not isinstance(cwd_raw, str) or not cwd_raw or len(cwd_raw) > 4096:
        raise ValueError("invalid cwd")
    transcript = _normalized_path(transcript_raw)
    cwd = _normalized_path(cwd_raw)
    agent_id = payload.get("agent_id")
    if agent_id is not None and (not isinstance(agent_id, str)
                                 or NATIVE_ID.fullmatch(agent_id) is None):
        raise ValueError("invalid agent_id")
    _validate_transcript_location(transcript, session_id, agent_id)
    transcript_path_digest = _path_digest(transcript)
    base = {"runtime": "claude", "session_id": session_id,
            "transcript_path_digest": transcript_path_digest,
            "project_affinity": pinned_affinity(transcript_path_digest, project_affinity(cwd)),
            "cwd": str(cwd)}
    if agent_id is not None:
        base["native_agent_id"] = agent_id
        base["parent_session_id"] = session_id
    for source, target in (("model", "model_id"),):
        value = payload.get(source)
        if value is not None:
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError(f"invalid {source}")
            base[target] = value
    return base, transcript


def _call(name: str, args: dict) -> dict | None:
    return spool.invoke(name, args)[0]


def _shadow_audit(event: str, identity: dict, cursor: dict) -> None:
    path = _path("CARR_CLAUDE_CONTINUITY_AUDIT",
                 pathlib.Path.home() / ".config/carr/claude-continuity-audit.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and path.stat().st_size > 1_000_000:
        path.replace(path.with_suffix(".jsonl.1"))
    row = {"schema_version": 1, "mode": "shadow", "event": event,
           "session_id": identity["session_id"],
           "transcript_path_digest": identity["transcript_path_digest"],
           "project_affinity": identity["project_affinity"],
           "source_highwater": cursor["byte_offset"],
           "observed_at": datetime.now(timezone.utc).isoformat()}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, _canonical(row) + b"\n")
    finally:
        os.close(fd)


def receipt_key(args: dict) -> str:
    """One key per request body, which is the store envelope's own contract.

    Keying on the transcript offset alone collided: a prompt that has not yet
    reached the transcript (queued or blocked) fires UserPromptSubmit again at
    the same offset, and every repeat carried a new observed_at under the first
    one's key, so the store refused it as key_reuse and the hook spooled a
    receipt that could never send.  A byte-identical resend (the drain) still
    replays under the same key.
    """
    return hashlib.sha256(_canonical(args)).hexdigest()


def _record(event: str, identity: dict, cursor: dict, source_digest: str,
            telemetry: dict | None = None) -> bool:
    args = {**identity, "event_type": event, "cursor": cursor, "transcript_digest": source_digest,
            "observed_at": datetime.now(timezone.utc).isoformat()}
    if telemetry:
        args["telemetry"] = telemetry
    args["idempotency_key"] = receipt_key(args)
    response, failure = spool.invoke("claude-record-event", args)
    if response is not None:
        return True
    try:
        if failure and failure["kind"] == "refused":
            spool.archive({"verb": "claude-record-event", "args": args}, "refused", failure["error"])
        else:
            spool.spool_receipt("claude-record-event", args, failure or {"kind": "transport"})
    except (OSError, ValueError) as exc:
        _warn(f"receipt unavailable ({exc.__class__.__name__})")
    return False


def _sample_post_tool(payload: dict, identity: dict) -> bool:
    tool_id = payload.get("tool_use_id")
    if not isinstance(tool_id, str) or not tool_id:
        return False
    digest = hashlib.sha256(f"{identity['session_id']}:{tool_id}".encode()).digest()
    return digest[0] < 26  # Stable ~10% sample.


def _emit_context(event: str, context: str, limit: int = MAX_CAPSULE_BYTES) -> None:
    if len(context.encode("utf-8")) > limit:
        raise ValueError("Worker capsule exceeded native byte bound")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                              "additionalContext": context}},
                     separators=(",", ":"), ensure_ascii=False))


def _checkpoint_version(response: dict | None) -> int | None:
    if not response:
        return None
    if response.get("found") is False or response.get("checkpoint") is None:
        return 0
    checkpoint = response.get("checkpoint")
    raw = checkpoint.get("checkpoint_version") if isinstance(checkpoint, dict) else None
    if isinstance(raw, str) and raw.isdigit() and not raw.startswith("0"):
        raw = int(raw)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= 2 ** 53 - 1 else None


def _activation_envelope(identity: dict, cursor: dict, response: dict | None) -> str:
    lines = [
        _checkpoint_arguments(identity, cursor, response),
        "Call mcp__carr-continuity__claude-checkpoint at milestones: fill state; refresh cursor/time; "
        "nondecreasing generation; new key, reuse on retry. Null version: read recovery first.",
        "Never infer completion from telemetry or replay pending effects.",
    ]
    unsent = unsent_receipts()
    if unsent:
        lines.append(f"Spool: {unsent} receipts await nightly drain.")
    return "\n".join(lines)


def _binding(identity: dict) -> dict:
    return {key: identity.get(key) for key in (
        "runtime", "session_id", "transcript_path_digest", "project_affinity",
        "parent_session_id", "native_agent_id") if identity.get(key) is not None}


def _source(cursor: dict) -> dict:
    return {key: cursor[key] for key in (
        "byte_offset", "mtime_ns", "source_digest", "startup_pending") if key in cursor}


def _checkpoint_arguments(identity: dict, cursor: dict, response: dict | None) -> str:
    checkpoint = (response or {}).get("checkpoint") or {}
    args = {**_binding(identity), "idempotency_key": secrets.token_hex(16),
            "expected_version": _checkpoint_version(response),
            "compaction_generation": int(checkpoint.get("compaction_generation", 0)),
            "state": {"objective": "<objective>", "next_action": "<next action>",
                      "source_cursor": _source(cursor),
                      "source_observed_at": datetime.now(timezone.utc).isoformat()}}
    return "arguments=" + json.dumps(args, sort_keys=True, separators=(",", ":"))


def checkpoint_request(identity: dict, cursor: dict, response: dict | None) -> str:
    """The ask that actually fires, carrying a cursor fresh as of this prompt.

    The startup envelope can only speak once, at the top of a session, and its
    cursor is stale by the time anything worth saving has happened.  This is
    re-issued on transcript growth so the binding, the expected version, and the
    source cursor a checkpoint must carry are all current when the ask arrives.
    """
    current_version = _checkpoint_version(response)
    version_text = str(current_version) if current_version is not None else "unavailable"
    return "\n".join([
        "CARR Claude continuity: this session is now deep enough that losing it would cost real work.",
        "Save a checkpoint with mcp__carr-continuity__claude-checkpoint before continuing. "
        "Use these flat top-level arguments; fill objective and next_action and add the needed state fields. "
        "Reuse the idempotency_key only for retries. If the version is unavailable, read claude-read-recovery first.",
        _checkpoint_arguments(identity, cursor, response),
        f"expected_version={version_text}; compaction_generation nondecreasing",
        "Write what a session with no memory of this one would need to carry on: the objective, the "
        "decisions with their reasons and refs, unresolved defects, pending external effects (to be "
        "verified, never replayed), and the single next action. Do not infer completion from tool telemetry.",
    ])


def main() -> int:
    sys.path.insert(0, str(REPO))
    if os.environ.get("CARR_GROK_RUN_READ_ONLY") == "1":
        try:
            from hooks.grok_invocation import bounded_grok_read_only
            if bounded_grok_read_only():
                return 0
        except ImportError:
            pass  # an unavailable optional probe retains ordinary processing
    mode = _read_mode()
    if mode == "disabled":
        return 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("hook input too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
            raise ValueError("unsupported event")
        event_name = payload["hook_event_name"]
        identity, transcript = _identity(payload)
        # Decide the sample before the bounded tail read, not after: the read
        # hashes up to 256 KB and nine out of ten PostToolUse events discard it.
        if (event_name == "PostToolUse" and mode != "shadow"
                and not _sample_post_tool(payload, identity)):
            return 0
        try:
            cursor, source_digest = source_cursor(transcript)
        except FileNotFoundError:
            if event_name != "SessionStart" or payload.get("source") != "startup":
                raise
            cursor, source_digest = startup_source_cursor(transcript, identity)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _warn(f"unverified native input ignored ({exc.__class__.__name__})")
        return 0

    if ((event_name == "PreCompact") or
            (event_name == "SessionStart" and payload.get("source") in {"compact", "resume"})):
        reset_rule_delivery(identity["session_id"], identity["transcript_path_digest"])
    if mode == "shadow":
        try:
            _shadow_audit(payload["hook_event_name"], identity, cursor)
        except OSError as exc:
            _warn(f"shadow audit unavailable ({exc.__class__.__name__})")
        return 0
    if event_name == "SessionStart":
        response = _call("claude-read-recovery", identity)
        context = _activation_envelope(identity, cursor, response)
        capsule = response.get("capsule") if response else None
        if (mode == "inject" and payload.get("source") in {"compact", "resume"}
                and isinstance(capsule, str) and capsule):
            context += "\n\n" + capsule
        try:
            _emit_context("SessionStart", context)
        except ValueError as exc:
            _warn(str(exc))
        return 0
    event = EVENT_MAP[event_name]
    if (event_name == "UserPromptSubmit" and mode == "inject"
            and due_for_nudge(identity["transcript_path_digest"], cursor["byte_offset"])):
        response = _call("claude-read-recovery", identity)
        try:
            _emit_context("UserPromptSubmit", checkpoint_request(identity, cursor, response),
                          MAX_NUDGE_BYTES)
            mark_nudged(identity["transcript_path_digest"], cursor["byte_offset"])
        except ValueError as exc:
            _warn(str(exc))
    telemetry = None
    if event == "post_tool_use":
        telemetry = {"tool_name": str(payload.get("tool_name", ""))[:200],
                     "sample_rate_basis_points": 1000}
    _record(event, identity, cursor, source_digest, telemetry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
