"""Claude-only, compaction-scoped prose dedupe for the existing JIT rule rail."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pathlib
import tempfile
import threading
from datetime import datetime, timezone

from lib import claude_continuity_config as continuity_config

REPO = pathlib.Path(__file__).resolve().parents[1]
HOOK_CONFIG = REPO / "ops/config/claude-continuity-hooks.json"
HOOK_ADAPTER = REPO / "ops/claude-continuity-hook.py"
MCP_PROXY = REPO / "mcp-server/continuity-stdio-proxy.mjs"
ACTIVE_MODES = {"checkpoint", "inject"}
_CONTRACT: continuity_config.Contract | None = None
_CONTRACT_LOCK = threading.Lock()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _contract() -> continuity_config.Contract:
    """Load one immutable contract for this short-lived hook process."""
    global _CONTRACT
    if _CONTRACT is None:
        with _CONTRACT_LOCK:
            if _CONTRACT is None:
                _CONTRACT = continuity_config.load(REPO)
    return _CONTRACT


def expected_config_digest() -> str:
    return _contract().config_digest


def _mode_file() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONTINUITY_MODE_FILE",
                                       pathlib.Path.home() / ".config/carr/claude-continuity-mode.json"))


def active() -> bool:
    try:
        mode = continuity_config.read_mode(_mode_file(), _contract())
        return mode in ACTIVE_MODES
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False


def _root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_RULE_DEDUPE_DIR",
                                       pathlib.Path.home() / ".config/carr/claude-rule-delivery"))


def transcript_path_digest(transcript_path: str) -> str:
    normalized = os.path.realpath(os.path.expanduser(transcript_path))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _paths(session_id: str, leaf_digest: str) -> tuple[pathlib.Path, pathlib.Path]:
    token = hashlib.sha256(f"{session_id}\0{leaf_digest}".encode()).hexdigest()
    root = _root()
    return root / f"{token}.json", root / f"{token}.lock"


def _atomic(path: pathlib.Path, value: dict) -> None:
    """Replace `path` atomically, owning the temp descriptor exactly once.

    DESCRIPTOR OWNERSHIP IS THE WHOLE POINT OF THE SHAPE BELOW. os.fdopen TAKES
    OWNERSHIP of the descriptor it is handed: the file object closes it when the
    context exits, on success and on exception alike. A second os.close(fd) in a
    finally is therefore not a harmless retry — by the time it runs, the kernel
    may already have handed that same integer back out, and in a process with
    more than one thread it hands it to somebody else. The "cleanup" then closes
    a stranger's file, and the stranger fails with EBADF somewhere unrelated.
    That is exactly how one dedupe write could break another thread's, make
    _locked raise, and send should_deliver down its fail-open path so the same
    rule set delivered twice.

    So the raw descriptor is closed on one path only: the one where fdopen never
    took it. After that, the file object is the sole owner and `with` is the
    sole close.
    """
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "wb")
    except BaseException:
        # Ownership never transferred, so this descriptor is still ours to close.
        os.close(fd)
        pathlib.Path(temp_name).unlink(missing_ok=True)
        raise
    try:
        with handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        # A successful replace leaves nothing here; a failure leaves the temp.
        pathlib.Path(temp_name).unlink(missing_ok=True)


def _audit(session_id: str, leaf_digest: str, digest: str, delivered: int, suppressed: int,
           generation: int) -> None:
    path = pathlib.Path(os.environ.get("CARR_CLAUDE_RULE_DEDUPE_AUDIT",
                                       pathlib.Path.home() / ".config/carr/claude-rule-delivery.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    row = {"schema_version": 1, "session_id": session_id, "leaf_digest": leaf_digest,
           "rule_set_digest": digest,
           "compaction_generation": generation, "delivered_bytes": delivered,
           "suppressed_bytes": suppressed, "observed_at": datetime.now(timezone.utc).isoformat()}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, _canonical(row) + b"\n")
    finally:
        os.close(fd)


def _locked(session_id: str, leaf_digest: str, operation):
    state_path, lock_path = _paths(session_id, leaf_digest)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_bytes())
        except FileNotFoundError:
            state = {"schema_version": 1, "compaction_generation": 0, "digests": []}
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or not isinstance(state.get("compaction_generation"), int)
                or not isinstance(state.get("digests"), list)):
            raise ValueError("dedupe state invalid")
        result = operation(state)
        _atomic(state_path, state)
        return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def reset(session_id: str, leaf_digest: str) -> bool:
    if not active():
        return False
    try:
        def operation(state):
            state["compaction_generation"] += 1
            state["digests"] = []
            return True
        return bool(_locked(session_id, leaf_digest, operation))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def should_deliver(session_id: str, leaf_digest: str, rule_set_digest: str,
                   payload_bytes: int) -> bool:
    """Fail open: any unavailable/unverified dedupe state delivers the rule body."""
    if not active():
        return True
    try:
        def operation(state):
            digests = state["digests"]
            delivered = rule_set_digest not in digests
            if delivered:
                digests.append(rule_set_digest)
                if len(digests) > 100:
                    del digests[:-100]
            return delivered, state["compaction_generation"]
        delivered, generation = _locked(session_id, leaf_digest, operation)
        _audit(session_id, leaf_digest, rule_set_digest, payload_bytes if delivered else 0,
               0 if delivered else payload_bytes, generation)
        return delivered
    except (OSError, ValueError, json.JSONDecodeError):
        return True
