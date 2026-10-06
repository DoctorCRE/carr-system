#!/usr/bin/env python3
"""Cross-surface context lifecycle gate.

Claude invokes this file at PostToolUse, PreCompact, and Stop. PostToolUse
records high-water/density evidence and may announce once; PreCompact records
but always allows; Stop is the only Claude refusal seam. Codex has no claimed
native Stop hook: its rollout and dispatcher adapters are explicit bare CLI
commands exposed by this same file.

Lifecycle mutations are expected-version CAS operations over one task file.
State, immutable side objects, and native-identity bindings commit through one
recoverable filesystem transaction. Readers take the same root lock and finish
or discard an interrupted transaction before trusting any public bytes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import secrets
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stop_latch import announce  # noqa: E402

MANIFEST_DEFAULT = REPO / "ops/config/session-context-lifecycle.v2.json"
STATE_DIR_DEFAULT = REPO / "out/session-context-lifecycle"
ACTIONS = {"NOOP", "ANNOUNCE", "RECOVER_SAME_TASK", "REFUSE"}
REASONS = {
    "CONTEXT_HANDOFF_REQUIRED", "CONTEXT_SIGNAL_UNAVAILABLE",
    "CONTEXT_SIGNAL_AMBIGUOUS", "CONTEXT_SIGNAL_INVALID",
    "WINDOW_CONFIG_INVALID", "HANDOFF_RECEIPT_MISSING",
    "HANDOFF_RECEIPT_INVALID", "SUCCESSOR_SURFACE_INVALID",
    "SUCCESSOR_NOT_ACTIVE", "SUCCESSOR_NOT_PINNED",
    "TAKEOVER_NOT_VERIFIED", "OWNERSHIP_MISMATCH", "OWNERSHIP_MISSING",
    "OWNERSHIP_DUPLICATE", "LIFECYCLE_INVALID", "RECOVERY_CAPACITY",
    "RECOVERY_ERROR", "RECOVERY_IDLE", "RECOVERY_ACTIVE",
    "RECOVERY_TERMINAL", "RECOVERY_SNAPSHOT_STALE",
    "RECOVERY_SNAPSHOT_INVALID", "RECOVERY_WAITING",
}
WORKER_STATES = {
    "RUNNING", "IDLE", "WAITING_ATTENTION", "CAPACITY_EXHAUSTED",
    "ERROR", "TERMINAL",
}
SURFACES = {"claude", "codex"}
OWNER_STATES = {"ACTIVE", "DRAINING", "SUCCESSOR_DECLARED", "TERMINAL"}
HANDOFF_STATES = {
    "DRAINING", "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED",
    "PREDECESSOR_TERMINAL",
}
RECOVERY_STATES = {"PENDING", "COMPLETED", "ABORTED"}
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WORKER_TOOLS = {"Agent", "Task", "spawn_agent", "create_thread", "fork_thread"}
WINDOW_KEYS = {"model_context_window", "context_window", "contextWindow"}
TOTAL_KEYS = {"total_tokens", "totalTokens"}
USAGE_PARTS = {
    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
    "output_tokens", "inputTokens", "cacheCreationInputTokens",
    "cacheReadInputTokens", "outputTokens",
}


class LifecycleError(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason if reason in REASONS else "LIFECYCLE_INVALID"
        self.detail = detail


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def positive(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "numeric signal is non-finite")
    try:
        number = int(value)
    except OverflowError as exc:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "numeric signal is outside integer range") from exc
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def manifest_path() -> Path:
    return Path(os.environ.get("CARR_SESSION_CONTEXT_MANIFEST", MANIFEST_DEFAULT))


def load_manifest() -> dict[str, Any]:
    try:
        data = json.loads(manifest_path().read_text(encoding="utf-8"))
    except Exception as exc:
        raise LifecycleError("WINDOW_CONFIG_INVALID", f"manifest unreadable: {exc}") from exc
    try:
        if data.get("schema_version") != 2:
            raise ValueError("schema_version must be 2")
        if set(data.get("surface_policies") or {}) != SURFACES:
            raise ValueError("surface policies must be exact")
        for surface in sorted(SURFACES):
            policy = data["surface_policies"][surface]
            thresholds = policy["thresholds_percent"]
            dense = positive(thresholds["dense_soft"])
            normal = positive(thresholds["normal_soft"])
            hard = positive(thresholds["hard"])
            if not dense or not normal or not hard or not dense < normal < hard <= 100:
                raise ValueError(f"{surface} thresholds must satisfy 0 < dense < normal < hard <= 100")
            if not isinstance(policy["model_windows"], dict):
                raise ValueError(f"{surface} model_windows must be an object")
        claude = data["surface_policies"]["claude"]
        if any(claude.get(key) != "announce_only" for key in (
                "stop_outcome", "signal_failures", "fallback_caps_outcome",
                "control_errors")):
            raise ValueError("Claude context outcomes must be announce-only")
        if "claude-fable-5-1" not in claude["model_windows"]:
            raise ValueError("Claude Fable 5.1 model alias is required")
        if set(data["actions"]) != ACTIONS or set(data["reasons"]) != REASONS:
            raise ValueError("action/reason vocabulary drift")
    except Exception as exc:
        raise LifecycleError("WINDOW_CONFIG_INVALID", f"manifest invalid: {exc}") from exc
    return data


def surface_policy(manifest: dict[str, Any], surface: str) -> dict[str, Any]:
    try:
        policy = manifest["surface_policies"][surface]
    except Exception as exc:
        raise LifecycleError("WINDOW_CONFIG_INVALID",
                             f"{surface} surface policy unavailable") from exc
    if not isinstance(policy, dict):
        raise LifecycleError("WINDOW_CONFIG_INVALID",
                             f"{surface} surface policy invalid")
    return policy


def state_root(manifest: dict[str, Any] | None = None) -> Path:
    explicit = os.environ.get("CARR_SESSION_CONTEXT_STATE_DIR")
    if explicit:
        return Path(explicit)
    legacy = os.environ.get("CARR_CONTEXT_STATE")
    if legacy:
        path = Path(legacy).expanduser().resolve()
        return path.parent / f".{path.name}.lifecycle"
    return REPO / (manifest or {}).get("state_directory", "out/session-context-lifecycle")


def state_file(task_key: str, manifest: dict[str, Any] | None = None) -> Path:
    legacy = os.environ.get("CARR_CONTEXT_STATE")
    if legacy:
        return Path(legacy)
    return state_root(manifest) / f"{hashlib.sha256(task_key.encode()).hexdigest()}.json"


def lock_file(task_key: str, manifest: dict[str, Any] | None = None) -> Path:
    return state_file(task_key, manifest).with_suffix(".lock")


SIGNAL_KEYS = {
    "highwater", "invocations", "active_minutes", "cycles",
    "generation_tool_calls", "mutated_paths", "worker_starts",
    "last_observed_at", "notices",
}


def validate_signal_state(signal: Any) -> None:
    if not isinstance(signal, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "signal is not an object")
    if frozenset(signal) not in {frozenset(SIGNAL_KEYS),
                                 frozenset(SIGNAL_KEYS | {"risk"})}:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "signal fields are not exact")
    for field in ("highwater", "invocations", "cycles",
                  "generation_tool_calls", "worker_starts"):
        value = signal.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LifecycleError(
                "LIFECYCLE_INVALID", f"signal {field} is invalid")
    active_minutes = signal.get("active_minutes")
    if (isinstance(active_minutes, bool)
            or not isinstance(active_minutes, (int, float))
            or not math.isfinite(float(active_minutes))
            or float(active_minutes) < 0):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "signal active_minutes is invalid")
    for field in ("mutated_paths", "notices"):
        values = signal.get(field)
        if (not isinstance(values, list)
                or any(not isinstance(item, str) or not item for item in values)
                or values != sorted(set(values))):
            raise LifecycleError(
                "LIFECYCLE_INVALID", f"signal {field} is invalid")
    observed = signal.get("last_observed_at")
    if observed is not None:
        try:
            parse_time(observed)
        except Exception as exc:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                f"signal last_observed_at is invalid: {exc}") from exc
    if "risk" in signal and signal.get("risk") not in {
            "R0", "R1", "R2", "R3", "R4", "R5", "R6"}:
        raise LifecycleError("LIFECYCLE_INVALID", "signal risk is invalid")


def is_digest(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def validate_task_state(value: dict[str, Any], task_key: str,
                        manifest: dict[str, Any] | None = None) -> None:
    """Reject structurally inconsistent persisted lifecycle state.

    State files are mutable input outside the hook process's trust boundary.
    Every reader must establish the small ownership/shape invariants it relies
    on before a Stop decision or state-machine transition can use them.
    """
    if value.get("schema_version") != 1:
        raise LifecycleError("LIFECYCLE_INVALID", "state schema version is invalid")
    if value.get("task_key") != task_key:
        raise LifecycleError("LIFECYCLE_INVALID", "state task_key mismatch")
    version = value.get("version")
    if (isinstance(version, bool) or not isinstance(version, int)
            or version < -1):
        raise LifecycleError("LIFECYCLE_INVALID", "state version is not an integer")
    generation = value.get("generation")
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or generation < 0):
        raise LifecycleError("LIFECYCLE_INVALID", "state generation is invalid")
    status = value.get("task_status")
    if status not in {"ACTIVE", "TERMINAL"}:
        raise LifecycleError("LIFECYCLE_INVALID", "task status is invalid")
    owners = value.get("owners")
    if not isinstance(owners, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "owners is not an object")
    for owner_id, owner in owners.items():
        if (not isinstance(owner_id, str) or not owner_id
                or not isinstance(owner, dict)
                or owner.get("id") != owner_id):
            raise LifecycleError("LIFECYCLE_INVALID", "owner record is invalid")
        owner_generation = owner.get("generation")
        activation_init = owner.get("activation_init_digest")
        activation_final = owner.get("activation_final_digest")
        if (owner.get("surface") not in SURFACES
                or owner.get("state") not in OWNER_STATES
                or isinstance(owner_generation, bool)
                or not isinstance(owner_generation, int)
                or owner_generation < 0
                or owner_generation > generation + 1
                or (owner.get("evidence_digest") is not None
                    and not is_digest(owner.get("evidence_digest")))
                or (activation_final is not None
                    and not is_digest(activation_final))
                or (activation_init is not None
                    and not is_digest(activation_init))
                or owner.get("ownership_digest") != owner_digest(owner)):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "owner record semantics are invalid")
        if owner_generation == 0:
            if (not is_digest(activation_init)
                    or activation_final is not None):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "initial owner lacks immutable activation provenance")
            verify_owner_initialization(task_key, owner, manifest)
        elif activation_init is not None:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "successor owner carries initialization provenance")
        if (owner_generation > 0
                and owner.get("state") != "SUCCESSOR_DECLARED"
                and not is_digest(activation_final)):
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "verified owner lacks immutable activation provenance")
        if activation_final is not None:
            verify_owner_activation(task_key, owner, manifest)
        terminal_evidence = owner.get("terminal_evidence_digest")
        terminal_provenance = owner.get("terminal_provenance_digest")
        if owner.get("state") == "TERMINAL":
            if (not is_digest(terminal_evidence)
                    or not is_digest(terminal_provenance)):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "terminal owner lacks immutable terminal provenance")
        elif terminal_evidence is not None or terminal_provenance is not None:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "nonterminal owner retains terminal provenance")
        verify_identity_binding(task_key, owner, manifest)
    active_owner = value.get("active_owner")
    if status == "ACTIVE":
        if not isinstance(active_owner, str) or active_owner not in owners:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "active_owner has no owner record")
    elif active_owner is not None:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "terminal task retains an active owner")
    for field in ("handoff", "recovery_intent"):
        if value.get(field) is not None and not isinstance(value.get(field), dict):
            raise LifecycleError("LIFECYCLE_INVALID", f"{field} is not an object")
    recovery_history = value.get("recovery_history")
    if (not isinstance(recovery_history, list)
            or any(not is_digest(item) for item in recovery_history)
            or len(recovery_history) != len(set(recovery_history))):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "recovery history is invalid")
    validate_recovery_history(task_key, recovery_history, manifest)
    validate_signal_state(value.get("signal"))

    handoff = value.get("handoff") or {}
    handoff_state = handoff.get("state")
    if handoff and handoff_state not in HANDOFF_STATES:
        raise LifecycleError("LIFECYCLE_INVALID", "handoff state is invalid")
    by_generation: dict[int, dict[str, Any]] = {}
    for owner in owners.values():
        owner_generation = int(owner["generation"])
        if owner_generation in by_generation:
            raise LifecycleError(
                "LIFECYCLE_INVALID", "multiple owners share one generation")
        by_generation[owner_generation] = owner
    expected_generations = set(range(generation + 1))
    if handoff_state == "SUCCESSOR_DECLARED":
        expected_generations.add(generation + 1)
    if set(by_generation) != expected_generations:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "immutable owner lineage is incomplete")
    for owner_generation in range(1, generation + 1):
        owner = by_generation[owner_generation]
        lineage_predecessor = by_generation[owner_generation - 1]
        verified = read_verified_final_packet(
            task_key, owner.get("activation_final_digest"), manifest)
        offer = verified["offer"]
        if (offer.get("generation") != owner_generation
                or offer.get("predecessor") != lineage_predecessor.get("id")
                or offer.get("predecessor_surface")
                != lineage_predecessor.get("surface")
                or offer.get("successor") != owner.get("id")
                or offer.get("successor_surface") != owner.get("surface")):
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                "owner lineage is not linked to immutable handoff history")
    if handoff:
        predecessor = handoff.get("predecessor")
        successor = handoff.get("successor")
        handoff_generation = handoff.get("generation")
        if (not isinstance(predecessor, str) or not predecessor
                or not isinstance(successor, str) or not successor
                or predecessor == successor
                or predecessor not in owners
                or isinstance(handoff_generation, bool)
                or not isinstance(handoff_generation, int)
                or not is_digest(handoff.get("offer_digest"))):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "handoff identity is invalid")
        if handoff_state in {
                "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED",
                "PREDECESSOR_TERMINAL"}:
            if (successor not in owners
                    or not is_digest(handoff.get("declaration_digest"))):
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "handoff declaration is invalid")
        if handoff_state in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
            if (not is_digest(handoff.get("receipt_digest"))
                    or not is_digest(handoff.get("final_digest"))):
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "verified handoff receipts are invalid")

        predecessor_state = owners[predecessor].get("state")
        successor_state = (owners.get(successor) or {}).get("state")
        if handoff_state == "DRAINING":
            valid = (handoff_generation == generation + 1
                     and active_owner == predecessor
                     and predecessor_state == "DRAINING")
        elif handoff_state == "SUCCESSOR_DECLARED":
            valid = (handoff_generation == generation + 1
                     and active_owner == predecessor
                     and predecessor_state == "DRAINING"
                     and successor_state == "SUCCESSOR_DECLARED"
                     and owners[successor].get("generation") == handoff_generation)
        elif handoff_state == "TAKEOVER_VERIFIED":
            valid = (handoff_generation == generation
                     and active_owner == successor
                     and predecessor_state == "DRAINING"
                     and successor_state == "ACTIVE"
                     and owners[successor].get("generation") == generation)
        else:
            valid = (handoff_generation == generation
                     and predecessor_state == "TERMINAL"
                     and ((status == "ACTIVE" and active_owner == successor
                           and successor_state == "ACTIVE")
                          or (status == "TERMINAL" and active_owner is None
                              and successor_state == "TERMINAL")))
        if not valid:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "handoff ownership relationship is invalid")

    recovery = value.get("recovery_intent") or {}
    recovery_state = recovery.get("state")
    if recovery and recovery_state not in RECOVERY_STATES:
        raise LifecycleError("LIFECYCLE_INVALID", "recovery state is invalid")
    if recovery:
        failed_owner = recovery.get("failed_owner")
        recovery_generation = recovery.get("generation")
        if (recovery.get("task_key") != task_key
                or failed_owner not in owners
                or isinstance(recovery_generation, bool)
                or not isinstance(recovery_generation, int)
                or recovery_generation < 0
                or not is_digest(recovery.get("nonce"))
                or not is_digest(recovery.get("snapshot_digest"))
                or not is_digest(recovery.get("history_digest"))
                or not isinstance(recovery.get("source_event_id"), str)
                or not recovery.get("source_event_id")):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "recovery identity is invalid")
        if (not recovery_history
                or recovery.get("history_digest") != recovery_history[-1]):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "recovery intent is not history-linked")
        failed_state = owners[failed_owner].get("state")
        if recovery_state == "PENDING" and failed_state != "DRAINING":
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "pending recovery owner is not draining")
        if (recovery_state == "ABORTED" and status == "ACTIVE"
                and failed_state != "ACTIVE"):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "aborted recovery owner is not active")

    active_ids = [owner_id for owner_id, owner in owners.items()
                  if owner.get("state") == "ACTIVE"]
    transitional = (handoff_state in {"DRAINING", "SUCCESSOR_DECLARED"}
                    or recovery_state == "PENDING")
    if status == "ACTIVE":
        expected_state = "DRAINING" if transitional else "ACTIVE"
        if (owners[active_owner].get("state") != expected_state
                or active_ids != ([] if transitional else [active_owner])):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "active ownership cardinality is invalid")
    elif active_ids or any(owner.get("state") != "TERMINAL"
                           for owner in owners.values()):
        raise LifecycleError("LIFECYCLE_INVALID",
                             "terminal task retains nonterminal owners")
    for owner in owners.values():
        if owner.get("state") == "TERMINAL":
            verify_terminal_provenance(task_key, value, owner, manifest)
    if handoff_state in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
        verify_handoff_state(task_key, value, manifest)


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(canonical(value) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


_GUARD_LOCAL = threading.local()
_TRANSACTION_LOCAL = threading.local()


def _absolute(path: Path) -> Path:
    """Return stable absolute bytes without following a mutable symlink."""
    return Path(os.path.abspath(os.fspath(path)))


def _fsync_directory(path: Path) -> None:
    """Durably publish directory-entry changes for a trusted directory path."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _relative_parts(value: str, label: str) -> tuple[str, ...]:
    path = Path(value)
    parts = path.parts
    if (path.is_absolute() or not parts
            or any(part in {"", ".", ".."} for part in parts)):
        raise LifecycleError(
            "LIFECYCLE_INVALID", f"lifecycle transaction {label} is invalid")
    return parts


def _open_directory_at(root_fd: int, parts: tuple[str, ...], *,
                       create: bool = False) -> int:
    """Open descendants beneath a pinned fd without following symlinks."""
    current = os.dup(root_fd)
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        for part in parts:
            try:
                child = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=current)
                os.fsync(current)
                child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def _load_json_at(parent_fd: int, name: str) -> tuple[bool, Any | None]:
    """Read one regular file relative to a pinned directory, without links."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return False, None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return True, None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            raw = fh.read()
        try:
            return True, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return True, None
    finally:
        os.close(fd)


def _load_json_fd(fd: int) -> Any | None:
    """Decode one already-open regular file without releasing its identity."""
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        return None
    try:
        raw = os.pread(fd, os.fstat(fd).st_size + 1, 0)
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None


def _entry_matches_fd(parent_fd: int, name: str, fd: int) -> bool:
    """Prove a pinned directory entry still names one open regular file."""
    try:
        opened = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return (stat.S_ISREG(opened.st_mode)
            and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino))


def _open_matching_json_at(
        parent_fd: int, name: str, matches: Callable[[Any], bool]
        ) -> int | None:
    """Open and retain the exact named inode when its JSON matches."""
    try:
        fd = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd)
    except (FileNotFoundError, OSError):
        return None
    value = _load_json_fd(fd)
    if (value is None or not matches(value)
            or not _entry_matches_fd(parent_fd, name, fd)):
        os.close(fd)
        return None
    return fd


def _atomic_write_at(parent_fd: int, name: str, value: Any) -> None:
    """Durably replace one regular file beneath a pinned directory."""
    if name in {"", ".", ".."} or "/" in name:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "lifecycle publication name is invalid")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
             | getattr(os, "O_NOFOLLOW", 0))
    temp_name = f".{name}.{secrets.token_hex(16)}"
    fd = None
    try:
        fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        with os.fdopen(fd, "wb") as fh:
            fd = None
            fh.write(canonical(value) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(
            temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _publish_immutable_at(
        parent_fd: int, name: str, value: Any, *, reason: str,
        invalid_detail: str, collision_detail: str,
        matches: Callable[[Any], bool]) -> None:
    """Publish one immutable value beneath a pinned parent descriptor."""
    lock_name = f"{Path(name).stem}.lock"
    lock_fd = os.open(
        lock_name,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600, dir_fd=parent_fd)
    if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
        os.close(lock_fd)
        raise LifecycleError(reason, invalid_detail)
    with os.fdopen(lock_fd, "a+b") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        temp_name = None
        temp_fd = final_fd = matching_fd = None
        created = False
        try:
            exists, existing = _load_json_at(parent_fd, name)
            if exists:
                if existing is None or not matches(existing):
                    raise LifecycleError(reason, collision_detail)
                matching_fd = _open_matching_json_at(
                    parent_fd, name, matches)
                if matching_fd is None:
                    raise LifecycleError(reason, collision_detail)
                os.fsync(parent_fd)
                if not _entry_matches_fd(parent_fd, name, matching_fd):
                    raise LifecycleError(reason, collision_detail)
                return

            temp_name = f".{name}.{secrets.token_hex(16)}"
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600, dir_fd=parent_fd)
            remaining = memoryview(canonical(value) + b"\n")
            while remaining:
                written = os.write(temp_fd, remaining)
                if written <= 0:
                    raise OSError("immutable publication write made no progress")
                remaining = remaining[written:]
            os.fsync(temp_fd)
            temp_identity = os.fstat(temp_fd)
            try:
                os.link(
                    temp_name, name,
                    src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False)
                created = True
            except FileExistsError:
                exists, existing = _load_json_at(parent_fd, name)
                if not exists or existing is None or not matches(existing):
                    raise LifecycleError(reason, collision_detail)
                matching_fd = _open_matching_json_at(
                    parent_fd, name, matches)
                if matching_fd is None:
                    raise LifecycleError(reason, collision_detail)
            if created:
                linked_identity = None
                try:
                    linked_identity = os.stat(
                        name, dir_fd=parent_fd, follow_symlinks=False)
                    final_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd)
                    final_identity = os.fstat(final_fd)
                    final_value = _load_json_fd(final_fd)
                    if (not stat.S_ISREG(final_identity.st_mode)
                            or (final_identity.st_dev, final_identity.st_ino)
                            != (temp_identity.st_dev, temp_identity.st_ino)
                            or (linked_identity.st_dev, linked_identity.st_ino)
                            != (temp_identity.st_dev, temp_identity.st_ino)
                            or final_value is None
                            or not matches(final_value)):
                        raise LifecycleError(reason, invalid_detail)
                except Exception:
                    try:
                        current = os.stat(
                            name, dir_fd=parent_fd, follow_symlinks=False)
                        if (linked_identity is not None
                                and (current.st_dev, current.st_ino)
                                == (linked_identity.st_dev,
                                    linked_identity.st_ino)):
                            os.unlink(name, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
                    os.fsync(parent_fd)
                    raise
            os.fsync(parent_fd)
            held_fd = final_fd if created else matching_fd
            if (held_fd is None
                    or not _entry_matches_fd(parent_fd, name, held_fd)):
                raise LifecycleError(reason, collision_detail)
        finally:
            if matching_fd is not None:
                os.close(matching_fd)
            if final_fd is not None:
                os.close(final_fd)
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_name is not None:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass


def _parts_beneath(root: Path, path: Path, label: str) -> tuple[str, ...]:
    """Return a validated relative path beneath the configured root."""
    try:
        parts = tuple(_absolute(path).relative_to(root).parts)
    except ValueError as exc:
        raise LifecycleError(
            "LIFECYCLE_INVALID", f"{label} escapes lifecycle root") from exc
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise LifecycleError("LIFECYCLE_INVALID", f"{label} is invalid")
    return parts


def _cleanup_transaction_at(
        parent_fd: int, name: str, directory_fd: int | None = None) -> None:
    """Remove one transaction beneath a pinned parent descriptor."""
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    owns_directory_fd = directory_fd is None
    try:
        if directory_fd is None:
            try:
                directory_fd = os.open(name, flags, dir_fd=parent_fd)
            except (FileNotFoundError, NotADirectoryError, OSError):
                return
        assert directory_fd is not None

        def remove_contents(fd: int) -> None:
            for name in os.listdir(fd):
                mode = os.stat(
                    name, dir_fd=fd, follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    child_fd = os.open(name, flags, dir_fd=fd)
                    try:
                        remove_contents(child_fd)
                    finally:
                        os.close(child_fd)
                    os.rmdir(name, dir_fd=fd)
                else:
                    os.unlink(name, dir_fd=fd)

        try:
            try:
                os.unlink("journal.json", dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            else:
                # Once this entry removal is durable, any cleanup interruption
                # leaves a journal-less directory recovery may discard safely.
                os.fsync(directory_fd)
            remove_contents(directory_fd)
            os.fsync(directory_fd)
        finally:
            if owns_directory_fd:
                os.close(directory_fd)
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileNotFoundError:
        return


def _cleanup_transaction(directory: Path) -> None:
    """Remove only one controller-created transaction directory."""
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    parent_fd = os.open(directory.parent, flags)
    try:
        _cleanup_transaction_at(parent_fd, directory.name)
    finally:
        os.close(parent_fd)


def _publish_transaction_artifact(
        root: Path, directory: Path, artifact: dict[str, Any], *,
        pinned_root_fd: int | None = None,
        pinned_transaction_fd: int | None = None) -> None:
    final_rel = artifact.get("final")
    staged_rel = artifact.get("staged")
    expected_digest = artifact.get("value_digest")
    if (not isinstance(final_rel, str) or not final_rel
            or not isinstance(staged_rel, str) or not staged_rel
            or not is_digest(expected_digest)):
        raise LifecycleError("LIFECYCLE_INVALID",
                             "lifecycle transaction artifact is invalid")
    final_parts = _relative_parts(final_rel, "final path")
    staged_parts = _relative_parts(staged_rel, "staged path")
    final = _absolute(root / final_rel)
    staged = _absolute(directory / staged_rel)
    try:
        final.relative_to(root)
        staged.relative_to(directory)
    except ValueError as exc:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "lifecycle transaction path escapes its root") from exc
    root_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                  | getattr(os, "O_NOFOLLOW", 0))
    root_fd = (os.open(root, root_flags) if pinned_root_fd is None
               else os.dup(pinned_root_fd))
    transaction_fd = final_parent_fd = staged_parent_fd = staged_fd = None
    final_fd = matching_fd = None
    try:
        if pinned_transaction_fd is None:
            directory_parts = tuple(directory.relative_to(root).parts)
            transaction_fd = _open_directory_at(root_fd, directory_parts)
        else:
            transaction_fd = os.dup(pinned_transaction_fd)
        final_parent_fd = _open_directory_at(
            root_fd, final_parts[:-1], create=True)

        exists, existing = _load_json_at(final_parent_fd, final_parts[-1])
        if exists:
            if existing is None or digest(existing) != expected_digest:
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "lifecycle transaction artifact collided")
            matching_fd = _open_matching_json_at(
                final_parent_fd, final_parts[-1],
                lambda value: digest(value) == expected_digest)
            if matching_fd is None:
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "lifecycle transaction artifact identity changed")
            # Recovery may find the exact link created immediately before a
            # crash.  It is not safe to delete the journal until the containing
            # directory has crossed a durable sync boundary in this process.
            os.fsync(final_parent_fd)
            if not _entry_matches_fd(
                    final_parent_fd, final_parts[-1], matching_fd):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "lifecycle transaction artifact identity changed")
            return

        staged_parent_fd = _open_directory_at(
            transaction_fd, staged_parts[:-1])
        staged_fd = os.open(
            staged_parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=staged_parent_fd)
        staged_identity = os.fstat(staged_fd)
        value = _load_json_fd(staged_fd)
        if value is None:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "lifecycle transaction artifact is unreadable")
        if digest(value) != expected_digest:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "lifecycle transaction artifact digest changed")
        created = False
        try:
            os.link(
                staged_parts[-1], final_parts[-1],
                src_dir_fd=staged_parent_fd, dst_dir_fd=final_parent_fd,
                follow_symlinks=False)
            created = True
        except FileExistsError:
            exists, existing = _load_json_at(
                final_parent_fd, final_parts[-1])
            if (not exists or existing is None
                    or digest(existing) != expected_digest):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "lifecycle transaction artifact collided")
            matching_fd = _open_matching_json_at(
                final_parent_fd, final_parts[-1],
                lambda existing: digest(existing) == expected_digest)
            if matching_fd is None:
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "lifecycle transaction artifact identity changed")
        if created:
            linked_identity = None
            try:
                linked_identity = os.stat(
                    final_parts[-1], dir_fd=final_parent_fd,
                    follow_symlinks=False)
                final_fd = os.open(
                    final_parts[-1],
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=final_parent_fd)
                final_identity = os.fstat(final_fd)
                final_value = _load_json_fd(final_fd)
                if (not stat.S_ISREG(final_identity.st_mode)
                        or (final_identity.st_dev, final_identity.st_ino)
                        != (staged_identity.st_dev, staged_identity.st_ino)
                        or (linked_identity.st_dev, linked_identity.st_ino)
                        != (staged_identity.st_dev, staged_identity.st_ino)
                        or final_value is None
                        or digest(final_value) != expected_digest):
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        "lifecycle transaction artifact identity changed")
            except Exception:
                # This process created the entry, so a failed identity proof
                # must retract it durably before leaving the journal for retry.
                try:
                    current = os.stat(
                        final_parts[-1], dir_fd=final_parent_fd,
                        follow_symlinks=False)
                    if (linked_identity is not None
                            and (current.st_dev, current.st_ino)
                            == (linked_identity.st_dev,
                                linked_identity.st_ino)):
                        os.unlink(final_parts[-1], dir_fd=final_parent_fd)
                except FileNotFoundError:
                    pass
                os.fsync(final_parent_fd)
                raise
        os.fsync(final_parent_fd)
        held_fd = final_fd if created else matching_fd
        if (held_fd is None or not _entry_matches_fd(
                final_parent_fd, final_parts[-1], held_fd)):
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "lifecycle transaction artifact identity changed")
        # Keep a named sync boundary for durability instrumentation while the
        # descriptor above remains the race-safe authority. Recovery passes a
        # pinned root and must not reopen a replaceable ancestor by pathname.
        if pinned_root_fd is None:
            _fsync_directory(final.parent)
    except LifecycleError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise LifecycleError(
            "LIFECYCLE_INVALID",
            f"lifecycle transaction path is unsafe: {exc}") from exc
    finally:
        for fd in (matching_fd, final_fd, staged_fd,
                   staged_parent_fd, final_parent_fd,
                   transaction_fd, root_fd):
            if fd is not None:
                os.close(fd)


def _recover_transactions_unlocked(
        root: Path, manifest: dict[str, Any] | None = None, *,
        pinned_root_fd: int | None = None) -> None:
    transactions = root / "transactions"
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    root_fd = (os.open(root, flags) if pinned_root_fd is None
               else os.dup(pinned_root_fd))
    transactions_fd = None
    try:
        try:
            transactions_fd = _open_directory_at(root_fd, ("transactions",))
        except FileNotFoundError:
            return
        except OSError as exc:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                f"lifecycle transaction root is unsafe: {exc}") from exc
        transaction_names = sorted(os.listdir(transactions_fd))
        for name in transaction_names:
            directory = transactions / name
            directory_fd = None
            try:
                mode = os.stat(
                    name, dir_fd=transactions_fd,
                    follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        "lifecycle transaction directory is a symlink")
                if not stat.S_ISDIR(mode):
                    continue
                directory_fd = os.open(
                    name, flags, dir_fd=transactions_fd)
                journal_exists, journal = _load_json_at(
                    directory_fd, "journal.json")
                if not journal_exists:
                    _cleanup_transaction_at(
                        transactions_fd, name, directory_fd)
                    continue
                if journal is None:
                    raise LifecycleError("LIFECYCLE_INVALID",
                                         "lifecycle transaction journal is invalid")
                if (not isinstance(journal, dict)
                        or set(journal) != {
                            "schema_version", "task_key", "state_path",
                            "target_state_digest", "artifacts"}
                        or journal.get("schema_version") != 1
                        or not isinstance(journal.get("task_key"), str)
                        or not journal.get("task_key")
                        or not isinstance(journal.get("state_path"), str)
                        or not journal.get("state_path")
                        or not is_digest(journal.get("target_state_digest"))
                        or not isinstance(journal.get("artifacts"), list)):
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        "lifecycle transaction journal is invalid")
                state_path = _absolute(Path(journal["state_path"]))
                expected_state_path = _absolute(
                    state_file(journal["task_key"], manifest))
                if state_path != expected_state_path:
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        "lifecycle transaction state path is not the configured task state")
                try:
                    state_parts = tuple(state_path.relative_to(root).parts)
                except ValueError as exc:
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        "lifecycle transaction state path escapes its root") from exc
                if not state_parts:
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        "lifecycle transaction state path is invalid")
                state_parent_fd = _open_directory_at(
                    root_fd, state_parts[:-1])
                try:
                    state_exists, current = _load_json_at(
                        state_parent_fd, state_parts[-1])
                finally:
                    os.close(state_parent_fd)
                committed = (
                    state_exists and isinstance(current, dict)
                    and current.get("task_key") == journal["task_key"]
                    and digest(current) == journal["target_state_digest"])
                if committed:
                    for artifact in journal["artifacts"]:
                        if not isinstance(artifact, dict):
                            raise LifecycleError(
                                "LIFECYCLE_INVALID",
                                "lifecycle transaction artifact list is invalid")
                        _publish_transaction_artifact(
                            root, directory, artifact,
                            pinned_root_fd=root_fd,
                            pinned_transaction_fd=directory_fd)
                _cleanup_transaction_at(
                    transactions_fd, name, directory_fd)
            except LifecycleError:
                raise
            except OSError as exc:
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    f"lifecycle transaction directory is unsafe: {exc}") from exc
            finally:
                if directory_fd is not None:
                    os.close(directory_fd)
    finally:
        if transactions_fd is not None:
            os.close(transactions_fd)
        os.close(root_fd)


@contextmanager
def lifecycle_guard(manifest: dict[str, Any] | None = None):
    """Serialize lifecycle visibility and recover any interrupted commit."""
    root = _absolute(state_root(manifest))
    root.mkdir(parents=True, exist_ok=True)
    held = getattr(_GUARD_LOCAL, "held", None)
    if held is None:
        held = {}
        _GUARD_LOCAL.held = held
    key = os.fspath(root)
    if key in held:
        held[key][1] += 1
        try:
            yield root
        finally:
            held[key][1] -= 1
        return
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    root_fd = os.open(root, flags)
    lock_fd = None
    try:
        lock_fd = os.open(
            ".lifecycle.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=root_fd)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "lifecycle lock is not a regular file")
        lock = os.fdopen(lock_fd, "a+b")
        lock_fd = None
    except Exception:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(root_fd)
        raise
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    except Exception:
        lock.close()
        os.close(root_fd)
        raise
    held[key] = [lock, 1, root_fd]
    try:
        _recover_transactions_unlocked(
            root, manifest, pinned_root_fd=root_fd)
        yield root
    finally:
        held.pop(key, None)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        os.close(root_fd)


def _guard_root_fd(root: Path) -> int:
    """Borrow the descriptor pinned by the active lifecycle guard."""
    held = getattr(_GUARD_LOCAL, "held", None) or {}
    entry = held.get(os.fspath(_absolute(root)))
    if entry is None or len(entry) < 3:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "lifecycle root descriptor is unavailable")
    return int(entry[2])


def _guarded_parent_fd(
        root: Path, path: Path, label: str, *, create: bool = False
        ) -> tuple[int, str]:
    """Open one path's parent beneath the active guard's pinned root."""
    parts = _parts_beneath(root, path, label)
    parent_fd = _open_directory_at(
        _guard_root_fd(root), parts[:-1], create=create)
    return parent_fd, parts[-1]


def _guarded_load_json(root: Path, path: Path, label: str
                       ) -> tuple[bool, Any | None]:
    """Read one guarded public value without reopening the root pathname."""
    try:
        parent_fd, name = _guarded_parent_fd(root, path, label)
    except FileNotFoundError:
        return False, None
    try:
        return _load_json_at(parent_fd, name)
    finally:
        os.close(parent_fd)


def _state_parent_fd(
        root: Path, path: Path, lock: Path) -> tuple[int, str, str]:
    """Pin the configured state and lock parent for one guarded operation."""
    root_fd = _guard_root_fd(root)
    try:
        state_parts = _parts_beneath(root, path, "lifecycle state path")
        lock_parts = _parts_beneath(root, lock, "lifecycle task lock")
        if state_parts[:-1] != lock_parts[:-1]:
            raise LifecycleError(
                "LIFECYCLE_INVALID", "lifecycle task lock parent is invalid")
        return (_open_directory_at(root_fd, state_parts[:-1], create=True),
                state_parts[-1], lock_parts[-1])
    except LifecycleError:
        # Preserve the explicit legacy single-state-file interface while still
        # pinning its external parent for the duration of this operation.
        legacy = os.environ.get("CARR_CONTEXT_STATE")
        if not legacy or _absolute(Path(legacy)) != _absolute(path):
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        parent_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0))
        return parent_fd, path.name, lock.name


def _create_transaction_directory(root_fd: int) -> tuple[int, str, int]:
    """Create and pin one durable transaction directory."""
    transactions_fd = _open_directory_at(
        root_fd, ("transactions",), create=True)
    directory_name = secrets.token_hex(16)
    directory_fd = None
    try:
        os.mkdir(directory_name, 0o700, dir_fd=transactions_fd)
        # A journal cannot protect published state if its own transaction
        # directory entry disappears after power loss.
        os.fsync(transactions_fd)
        directory_fd = os.open(
            directory_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=transactions_fd)
        return transactions_fd, directory_name, directory_fd
    except Exception:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(transactions_fd)
        raise


def _active_transaction(task_key: str | None = None) -> dict[str, Any] | None:
    transaction = getattr(_TRANSACTION_LOCAL, "current", None)
    if (transaction is not None and task_key is not None
            and transaction.get("task_key") != task_key):
        raise LifecycleError("LIFECYCLE_INVALID",
                             "cross-task lifecycle transaction is invalid")
    return transaction


def _stage_transaction_value(final: Path, value: Any) -> None:
    transaction = _active_transaction()
    if transaction is None:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "no lifecycle transaction is active")
    root = transaction["root"]
    directory = transaction["directory"]
    final = _absolute(final)
    try:
        final_rel = os.fspath(final.relative_to(root))
    except ValueError as exc:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "transaction artifact escapes lifecycle root") from exc
    value_digest = digest(value)
    prior = transaction["artifacts"].get(final_rel)
    if prior is not None:
        existing = _transaction_value(final)
        if existing is None or digest(existing) != value_digest:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "transaction staged conflicting artifact bytes")
        return
    staged_rel = f"staged/{digest({'final': final_rel})}.json"
    staged_parts = _relative_parts(staged_rel, "staged path")
    staged_parent_fd = _open_directory_at(
        transaction["directory_fd"], staged_parts[:-1], create=True)
    try:
        _atomic_write_at(staged_parent_fd, staged_parts[-1], value)
    finally:
        os.close(staged_parent_fd)
    transaction["artifacts"][final_rel] = {
        "final": final_rel, "staged": staged_rel,
        "value_digest": value_digest,
    }


def _transaction_value(final: Path) -> Any | None:
    transaction = _active_transaction()
    if transaction is None:
        return None
    final = _absolute(final)
    try:
        final_rel = os.fspath(final.relative_to(transaction["root"]))
    except ValueError:
        return None
    artifact = transaction["artifacts"].get(final_rel)
    if artifact is None:
        return None
    staged_parts = _relative_parts(artifact["staged"], "staged path")
    try:
        staged_parent_fd = _open_directory_at(
            transaction["directory_fd"], staged_parts[:-1])
    except FileNotFoundError:
        return None
    try:
        exists, value = _load_json_at(staged_parent_fd, staged_parts[-1])
        return value if exists else None
    finally:
        os.close(staged_parent_fd)


def _transaction_public_value(final: Path) -> tuple[bool, Any | None]:
    """Read public bytes beneath the active transaction's pinned root."""
    transaction = _active_transaction()
    if transaction is None:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "no lifecycle transaction is active")
    parts = _parts_beneath(
        transaction["root"], final, "transaction public path")
    try:
        parent_fd = _open_directory_at(
            transaction["root_fd"], parts[:-1])
    except FileNotFoundError:
        return False, None
    try:
        return _load_json_at(parent_fd, parts[-1])
    finally:
        os.close(parent_fd)


def _transaction_paths_under(parent: Path) -> set[Path]:
    transaction = _active_transaction()
    if transaction is None:
        return set()
    parent = _absolute(parent)
    paths: set[Path] = set()
    for artifact in transaction["artifacts"].values():
        final = _absolute(transaction["root"] / artifact["final"])
        if final.parent == parent:
            paths.add(final)
    return paths


def _commit_transaction(transaction: dict[str, Any], state_path: Path,
                        updated: dict[str, Any]) -> None:
    root = transaction["root"]
    directory = transaction["directory"]
    journal = {
        "schema_version": 1,
        "task_key": transaction["task_key"],
        "state_path": os.fspath(_absolute(state_path)),
        "target_state_digest": digest(updated),
        "artifacts": list(transaction["artifacts"].values()),
    }
    _atomic_write_at(transaction["directory_fd"], "journal.json", journal)
    transaction["state_published"] = True
    _atomic_write_at(
        transaction["state_parent_fd"], transaction["state_name"], updated)
    for artifact in journal["artifacts"]:
        _publish_transaction_artifact(
            root, directory, artifact,
            pinned_root_fd=transaction["root_fd"],
            pinned_transaction_fd=transaction["directory_fd"])
    _cleanup_transaction_at(
        transaction["transactions_fd"], transaction["directory_name"],
        transaction["directory_fd"])
    transaction["cleaned"] = True


def mutate_state(task_key: str, expected_version: int | None,
                 change: Callable[[dict[str, Any] | None], dict[str, Any]],
                 manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    with lifecycle_guard(manifest) as root:
        path = state_file(task_key, manifest)
        lock = lock_file(task_key, manifest)
        root_fd = _guard_root_fd(root)
        state_parent_fd, state_name, lock_name = _state_parent_fd(
            root, path, lock)
        try:
            lock_fd = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600, dir_fd=state_parent_fd)
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                os.close(lock_fd)
                raise LifecycleError(
                    "LIFECYCLE_INVALID", "lifecycle task lock is not a regular file")
            with os.fdopen(lock_fd, "a+b") as guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                exists, current = _load_json_at(state_parent_fd, state_name)
                if exists and not isinstance(current, dict):
                    raise LifecycleError(
                        "LIFECYCLE_INVALID", "state is not an object")
                if current is not None:
                    validate_task_state(current, task_key, manifest)
                actual = int(current.get("version", -1)) if current else -1
                if expected_version is not None and actual != expected_version:
                    raise LifecycleError(
                        "LIFECYCLE_INVALID",
                        f"expected version {expected_version}, found {actual}")
                before = canonical(current) if current is not None else None
                transactions = root / "transactions"
                transactions_fd, directory_name, directory_fd = (
                    _create_transaction_directory(root_fd))
                directory = transactions / directory_name
                transaction = {
                    "root": root, "directory": directory,
                    "task_key": task_key, "root_fd": root_fd,
                    "transactions_fd": transactions_fd,
                    "directory_fd": directory_fd,
                    "directory_name": directory_name,
                    "state_parent_fd": state_parent_fd,
                    "state_name": state_name,
                    "artifacts": {}, "state_published": False,
                    "cleaned": False,
                }
                _TRANSACTION_LOCAL.current = transaction
                try:
                    updated = change(current)
                    if (not isinstance(updated, dict)
                            or updated.get("task_key") != task_key):
                        raise LifecycleError(
                            "LIFECYCLE_INVALID",
                            "mutation returned invalid task state")
                    validate_task_state(updated, task_key, manifest)
                    # Terminal observations, already-drained predecessors, and
                    # exact idempotent replays are reads, not new versions.
                    if before is not None and canonical(updated) == before:
                        _cleanup_transaction_at(
                            transactions_fd, directory_name, directory_fd)
                        transaction["cleaned"] = True
                        return updated
                    updated["version"] = actual + 1
                    updated["updated_at"] = utc_now()
                    validate_task_state(updated, task_key, manifest)
                    _commit_transaction(transaction, path, updated)
                    return updated
                finally:
                    _TRANSACTION_LOCAL.current = None
                    if (not transaction["state_published"]
                            and not transaction["cleaned"]):
                        _cleanup_transaction_at(
                            transactions_fd, directory_name, directory_fd)
                    os.close(directory_fd)
                    os.close(transactions_fd)
        finally:
            os.close(state_parent_fd)


def read_state(task_key: str, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with lifecycle_guard(manifest) as root:
        path = state_file(task_key, manifest)
        parent_fd, state_name, lock_name = _state_parent_fd(
            root, path, lock_file(task_key, manifest))
        try:
            lock_fd = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600, dir_fd=parent_fd)
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                os.close(lock_fd)
                raise LifecycleError(
                    "LIFECYCLE_INVALID", "lifecycle task lock is not a regular file")
            with os.fdopen(lock_fd, "a+b") as guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                exists, state = _load_json_at(parent_fd, state_name)
                if exists and not isinstance(state, dict):
                    raise LifecycleError(
                        "LIFECYCLE_INVALID", "state is not an object")
                if state is not None:
                    validate_task_state(state, task_key, manifest)
                return state
        finally:
            os.close(parent_fd)


def object_path(task_key: str, kind: str, object_digest: str,
                manifest: dict[str, Any] | None = None) -> Path:
    task_hash = hashlib.sha256(task_key.encode()).hexdigest()
    return state_root(manifest) / "objects" / task_hash / kind / f"{object_digest}.json"


def write_immutable(task_key: str, kind: str, value: Any,
                    manifest: dict[str, Any] | None = None) -> str:
    object_digest = digest(value)
    path = object_path(task_key, kind, object_digest, manifest)
    transaction = _active_transaction(task_key)
    if transaction is not None:
        exists, existing = _transaction_public_value(path)
        if exists:
            if existing is None:
                raise LifecycleError(
                    "HANDOFF_RECEIPT_INVALID", f"invalid published {kind}")
            if digest(existing) != object_digest:
                raise LifecycleError(
                    "HANDOFF_RECEIPT_INVALID", "immutable object collision")
            return object_digest
        _stage_transaction_value(path, value)
        return object_digest
    with lifecycle_guard(manifest) as root:
        return _write_immutable_public(
            root, path, kind, value, object_digest)


def _write_immutable_public(root: Path, path: Path, kind: str, value: Any,
                            object_digest: str) -> str:
    parent_fd, name = _guarded_parent_fd(
        root, path, f"{kind} object path", create=True)
    try:
        _publish_immutable_at(
            parent_fd, name, value,
            reason="HANDOFF_RECEIPT_INVALID",
            invalid_detail=f"invalid published {kind}",
            collision_detail="immutable object collision",
            matches=lambda existing: digest(existing) == object_digest)
    finally:
        os.close(parent_fd)
    return object_digest


def identity_binding_path(surface: str, identity: str,
                          manifest: dict[str, Any] | None = None) -> Path:
    key = digest({"surface": surface, "identity": identity})
    return state_root(manifest) / "identity-bindings" / f"{key}.json"


def claim_identity(surface: str, identity: str, task_key: str,
                   activation_provenance_digest: str,
                   manifest: dict[str, Any] | None = None) -> None:
    """Permanently bind one native identity to one lifecycle task.

    The binding is never removed at terminal state. Publication is atomic, so
    simultaneous task admissions cannot both claim the same controller-native
    identity. An exact retry for the same task and provenance is idempotent.
    """
    if (surface not in SURFACES or not isinstance(identity, str) or not identity
            or not is_digest(activation_provenance_digest)):
        raise LifecycleError(
            "OWNERSHIP_MISMATCH", "native identity claim is invalid")
    value = {
        "schema_version": 1, "surface": surface, "identity": identity,
        "task_key": task_key,
        "activation_provenance_digest": activation_provenance_digest,
    }
    path = identity_binding_path(surface, identity, manifest)
    transaction = _active_transaction(task_key)
    if transaction is not None:
        exists, existing = _transaction_public_value(path)
        if exists:
            if existing != value:
                raise LifecycleError(
                    "OWNERSHIP_MISMATCH",
                    "native identity is permanently bound to another lifecycle")
            return
        _stage_transaction_value(path, value)
        return
    with lifecycle_guard(manifest) as root:
        _claim_identity_public(root, path, value)


def _claim_identity_public(
        root: Path, path: Path, value: dict[str, Any]) -> None:
    parent_fd, name = _guarded_parent_fd(
        root, path, "identity binding path", create=True)
    try:
        _publish_immutable_at(
            parent_fd, name, value,
            reason="OWNERSHIP_MISMATCH",
            invalid_detail="native identity binding is invalid",
            collision_detail=(
                "native identity is permanently bound to another lifecycle"),
            matches=lambda existing: existing == value)
    finally:
        os.close(parent_fd)


def verify_identity_binding(task_key: str, owner: dict[str, Any],
                            manifest: dict[str, Any] | None = None) -> None:
    with lifecycle_guard(manifest) as root:
        path = identity_binding_path(
            str(owner.get("surface")), str(owner.get("id")), manifest)
        binding = _transaction_value(path)
        if binding is None:
            transaction = _active_transaction(task_key)
            if transaction is not None:
                _exists, binding = _transaction_public_value(path)
            else:
                _exists, binding = _guarded_load_json(
                    root, path, "identity binding path")
        expected = {
            "schema_version": 1, "surface": owner.get("surface"),
            "identity": owner.get("id"), "task_key": task_key,
            "activation_provenance_digest": owner.get("evidence_digest"),
        }
        if binding != expected:
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "native identity binding is missing or inconsistent")


def read_identity_binding(surface: str, identity: str,
                          manifest: dict[str, Any] | None = None
                          ) -> dict[str, Any] | None:
    """Read one authoritative native-identity index entry in O(1)."""
    with lifecycle_guard(manifest) as root:
        path = identity_binding_path(surface, identity, manifest)
        binding = _transaction_value(path)
        if binding is None:
            transaction = _active_transaction()
            if transaction is not None:
                _exists, binding = _transaction_public_value(path)
            else:
                _exists, binding = _guarded_load_json(
                    root, path, "identity binding path")
        if binding is None:
            return None
        if (set(binding) != {
                "schema_version", "surface", "identity", "task_key",
                "activation_provenance_digest"}
                or binding.get("schema_version") != 1
                or binding.get("surface") != surface
                or binding.get("identity") != identity
                or not isinstance(binding.get("task_key"), str)
                or not binding.get("task_key")
                or not is_digest(binding.get("activation_provenance_digest"))):
            raise LifecycleError(
                "OWNERSHIP_MISMATCH", "native identity binding is invalid")
        return binding


RECOVERY_CAUSES = {"RECOVERY_CAPACITY", "RECOVERY_ERROR", "RECOVERY_IDLE"}
RECOVERY_EVENT_KEYS = {
    "schema_version", "task_key", "source_event_id", "snapshot_digest",
    "nonce", "generation", "failed_owner", "cause", "previous_digest",
}


def validate_recovery_history(task_key: str, history: list[str],
                              manifest: dict[str, Any] | None = None) -> None:
    previous: str | None = None
    event_ids: set[str] = set()
    for event_digest in history:
        event = read_object(
            task_key, "recovery-event", event_digest, manifest)
        generation = event.get("generation")
        source = event.get("source_event_id")
        if (set(event) != RECOVERY_EVENT_KEYS
                or event.get("schema_version") != 1
                or event.get("task_key") != task_key
                or not isinstance(source, str) or not source
                or source in event_ids
                or not is_digest(event.get("snapshot_digest"))
                or not is_digest(event.get("nonce"))
                or isinstance(generation, bool)
                or not isinstance(generation, int) or generation < 0
                or not isinstance(event.get("failed_owner"), str)
                or not event.get("failed_owner")
                or event.get("cause") not in RECOVERY_CAUSES
                or event.get("previous_digest") != previous):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "recovery history linkage is invalid")
        event_ids.add(source)
        previous = event_digest
    directory = object_path(
        task_key, "recovery-event", "unused", manifest).parent
    try:
        with lifecycle_guard(manifest) as root:
            parts = _parts_beneath(
                root, directory, "recovery history directory")
            public_names: set[str] = set()
            try:
                directory_fd = _open_directory_at(
                    _guard_root_fd(root), parts)
            except FileNotFoundError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    for name in os.listdir(directory_fd):
                        path = Path(name)
                        if (path.suffix == ".json" and is_digest(path.stem)
                                and stat.S_ISREG(os.stat(
                                    name, dir_fd=directory_fd,
                                    follow_symlinks=False).st_mode)):
                            public_names.add(path.stem)
                finally:
                    os.close(directory_fd)
            published = public_names | {
                path.stem for path in _transaction_paths_under(directory)
                if is_digest(path.stem)
            }
    except OSError as exc:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "recovery history cannot be listed") from exc
    if published != set(history):
        raise LifecycleError(
            "LIFECYCLE_INVALID",
            "recovery history omits or invents an immutable event")


def recovery_history_event_ids(task_key: str, state: dict[str, Any],
                               manifest: dict[str, Any]) -> set[str]:
    return {
        str(read_object(task_key, "recovery-event", item, manifest)
            .get("source_event_id"))
        for item in state.get("recovery_history", [])
    }


def read_object(task_key: str, kind: str, object_digest: str,
                manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    with lifecycle_guard(manifest) as root:
        path = object_path(task_key, kind, object_digest, manifest)
        value = _transaction_value(path)
        if value is None:
            transaction = _active_transaction(task_key)
            if transaction is not None:
                exists, value = _transaction_public_value(path)
                if not exists:
                    raise LifecycleError(
                        "HANDOFF_RECEIPT_MISSING",
                        f"missing {kind} {object_digest}")
            else:
                try:
                    exists, value = _guarded_load_json(
                        root, path, f"{kind} object path")
                except OSError as exc:
                    raise LifecycleError(
                        "HANDOFF_RECEIPT_INVALID", f"invalid {kind}: {exc}") from exc
                if not exists:
                    raise LifecycleError(
                        "HANDOFF_RECEIPT_MISSING",
                        f"missing {kind} {object_digest}")
        if not isinstance(value, dict) or digest(value) != object_digest:
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID", f"tampered {kind} {object_digest}")
        return value


def verify_owner_initialization(task_key: str, owner: dict[str, Any],
                                manifest: dict[str, Any] | None = None) -> None:
    """Bind generation-zero ownership to an immutable admission packet."""
    packet = read_object(
        task_key, "initialization", str(owner.get("activation_init_digest")),
        manifest)
    evidence = packet.get("native_evidence")
    evidence_digest = packet.get("native_evidence_digest")
    source = packet.get("source")
    if (packet.get("schema_version") != 1
            or packet.get("task_key") != task_key
            or packet.get("owner") != owner.get("id")
            or packet.get("surface") != owner.get("surface")
            or packet.get("generation") != 0
            or source not in {"task_init", "claude_hook"}
            or not isinstance(packet.get("created_at"), str)
            or not packet.get("created_at")
            or evidence_digest != owner.get("evidence_digest")):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "initialization provenance linkage is invalid")
    if source == "claude_hook":
        if (not isinstance(evidence, dict)
                or not is_digest(evidence_digest)
                or digest(evidence) != evidence_digest
                or evidence.get("session_id") != owner.get("id")
                or not isinstance(evidence.get("controller_callback_id"), str)
                or not evidence.get("controller_callback_id")
                or (evidence.get("transcript_path") is not None
                    and (not isinstance(evidence.get("transcript_path"), str)
                         or not evidence.get("transcript_path")))
                or evidence.get("status") != "active"):
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                "hook initialization native evidence is invalid")
        return
    if (not isinstance(evidence, dict)
            or not is_digest(evidence_digest)
            or digest(evidence) != evidence_digest):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "initialization native evidence linkage is invalid")
    try:
        validated = validate_native_evidence(
            str(owner.get("surface")), evidence,
            expected_identity=str(owner.get("id")), accept=True,
            require_live=False)
    except LifecycleError as exc:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            f"initialization native evidence is invalid: {exc.detail}") from exc
    if validated != evidence_digest:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "initialization native evidence digest changed")


def verify_terminal_provenance(task_key: str, state: dict[str, Any],
                               owner: dict[str, Any],
                               manifest: dict[str, Any] | None = None) -> None:
    provenance_digest = owner.get("terminal_provenance_digest")
    packet = read_object(
        task_key, "terminal", str(provenance_digest), manifest)
    evidence = packet.get("native_evidence")
    evidence_digest = packet.get("native_evidence_digest")
    kind = packet.get("terminal_kind")
    if (packet.get("schema_version") != 1
            or packet.get("task_key") != task_key
            or packet.get("owner") != owner.get("id")
            or packet.get("surface") != owner.get("surface")
            or packet.get("generation") != owner.get("generation")
            or not isinstance(packet.get("created_at"), str)
            or not packet.get("created_at")
            or not isinstance(evidence, dict)
            or not is_digest(evidence_digest)
            or evidence_digest != owner.get("terminal_evidence_digest")
            or digest(evidence) != evidence_digest):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "terminal provenance linkage is invalid")
    if kind == "predecessor_terminal":
        final_digest = packet.get("handoff_final_digest")
        try:
            verified = read_verified_final_packet(
                task_key, final_digest, manifest)
        except LifecycleError as exc:
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                f"terminal handoff packet is invalid: {exc.detail}") from exc
        offer = verified["offer"]
        owner_generation = owner.get("generation")
        linked = (offer.get("predecessor") == owner.get("id")
                  and offer.get("predecessor_surface") == owner.get("surface")
                  and isinstance(owner_generation, int)
                  and not isinstance(owner_generation, bool)
                  and offer.get("generation") == owner_generation + 1)
    elif kind == "task_terminal":
        final_digest = packet.get("handoff_final_digest")
        linked = state.get("task_status") == "TERMINAL"
        if linked and final_digest is not None:
            try:
                verified = read_verified_final_packet(
                    task_key, final_digest, manifest)
            except LifecycleError as exc:
                raise LifecycleError(
                    "HANDOFF_RECEIPT_INVALID",
                    f"terminal handoff packet is invalid: {exc.detail}") from exc
            offer = verified["offer"]
            linked = (offer.get("successor") == owner.get("id")
                      and offer.get("successor_surface") == owner.get("surface")
                      and offer.get("generation") == owner.get("generation"))
    else:
        linked = False
    if not linked:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "terminal provenance is not linked to lifecycle state")
    try:
        validated = validate_native_evidence(
            str(owner.get("surface")), evidence,
            expected_identity=str(owner.get("id")), terminal=True,
            require_live=False)
    except LifecycleError as exc:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            f"terminal native evidence is invalid: {exc.detail}") from exc
    if validated != evidence_digest:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "terminal native evidence digest changed")
    validate_owner_native_binding(
        task_key, owner, evidence, manifest,
        error_reason="HANDOFF_RECEIPT_INVALID",
        require_live_checkout=False)


def audit(record: dict[str, Any], manifest: dict[str, Any] | None = None) -> None:
    if record.get("session") == "selftest" or os.environ.get("CARR_CONTEXT_AUDIT") == "off":
        return
    default = REPO / (manifest or {}).get("audit_log", "out/context-handoff.jsonl")
    path = Path(os.environ.get("CARR_CONTEXT_AUDIT", default))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": utc_now(), "hook": "context-handoff-gate", **record}
        with open(path, "ab") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(canonical(row) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def context_envelopes(row: dict[str, Any]):
    """Containers whose model/window fields describe the current session.

    Claude publishes those fields on the row or its message envelope. Codex
    publishes its model on a turn_context payload. Tool inputs and assistant
    content may also contain fields named model or context_window, but those
    describe a child invocation and must never select this session's threshold.
    """
    yield row
    message = row.get("message")
    if isinstance(message, dict):
        yield message
    payload = row.get("payload")
    if row.get("type") == "turn_context" and isinstance(payload, dict):
        yield payload


def transcript_rows(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    if not isinstance(path, str):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "transcript_path must be a string")
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def compact_pre_tokens(row: dict[str, Any]) -> int | None:
    raw_payload = row.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else row
    kind = payload.get("type") or row.get("type")
    if kind != "compact_boundary":
        return None
    for key in ("preTokens", "pre_tokens"):
        number = positive(payload.get(key))
        if number:
            return number
    return None


def usage_total(row: dict[str, Any]) -> int | None:
    # Codex token_count events carry two different domains: last_token_usage
    # is the live turn/context occupancy, while total_token_usage is cumulative
    # billing usage across the session.  Combining them turns a modest current
    # context into millions of apparent tokens and forces spurious handoffs.
    raw_payload = row.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    if row.get("type") == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        current = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(current, dict):
            return None
        direct: list[int] = []
        for key in TOTAL_KEYS:
            number = positive(current.get(key)) if key in current else None
            if number:
                direct.append(number)
        if direct:
            return max(direct)
        parts = [positive(current.get(key)) for key in USAGE_PARTS if key in current]
        return sum(number or 0 for number in parts) if any(parts) else None

    candidates: list[int] = []
    containers = [row]
    for key in ("message", "payload"):
        if isinstance(row.get(key), dict):
            containers.append(row[key])
    for container in list(containers):
        if isinstance(container.get("usage"), dict):
            containers.append(container["usage"])
        info = container.get("info")
        if isinstance(info, dict):
            for key in ("total_token_usage", "last_token_usage"):
                if isinstance(info.get(key), dict):
                    containers.append(info[key])
    for container in containers:
        candidates.extend(number for number in
                          (positive(container.get(key)) for key in TOTAL_KEYS if key in container)
                          if number)
        parts = [positive(container.get(key)) for key in USAGE_PARTS if key in container]
        if any(parts):
            candidates.append(sum(number or 0 for number in parts))
    compact = compact_pre_tokens(row)
    if compact:
        candidates.append(compact)
    return max(candidates) if candidates else None


def latest_models(rows: list[dict[str, Any]]) -> set[str]:
    """Return only the newest authoritative model declaration.

    A transcript is session history, not one simultaneous context.  Model
    switches therefore leave older, legitimately different windows behind.
    Combining every historical model makes the live window ambiguous and can
    suppress a hard-threshold Stop.  Preserve ambiguity within the newest row,
    but never let an older row override or conflict with the current one.
    """
    for row in reversed(rows):
        models: set[str] = set()
        for envelope in context_envelopes(row):
            for key in ("model", "model_name", "modelName"):
                value = envelope.get(key)
                if isinstance(value, str) and value.strip():
                    models.add(value.strip())
        if models:
            return models
    return set()


def latest_explicit_windows(
        rows: list[dict[str, Any]]) -> tuple[bool, set[int]]:
    """Return the newest authoritative explicit-window declaration only."""
    for row in reversed(rows):
        seen = False
        values: set[int] = set()
        for envelope in context_envelopes(row):
            for key in WINDOW_KEYS:
                if key not in envelope:
                    continue
                seen = True
                number = positive(envelope.get(key))
                if number:
                    values.add(number)
        if seen:
            return True, values
    return False, set()


def resolve_window(rows: list[dict[str, Any]], manifest: dict[str, Any],
                   surface: str = "claude") -> dict[str, Any]:
    if "CARR_CONTEXT_WINDOW" in os.environ:
        number = positive(os.environ.get("CARR_CONTEXT_WINDOW"))
        if not number:
            return {"ok": False, "reason": "WINDOW_CONFIG_INVALID", "tier": "override"}
        return {"ok": True, "window": number, "tier": "override"}

    bindings = surface_policy(manifest, surface).get("model_windows", {})
    models = latest_models(rows)
    # Distinct declarations in the newest authoritative row are contradictory
    # even when they map to the same numeric window, or one is unknown. Never
    # reduce identity ambiguity into apparent numeric agreement.
    if len(models) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "tier": "model"}
    bound: set[int] = set()
    malformed = False
    for model in models:
        if model in bindings:
            number = positive(bindings.get(model))
            if number:
                bound.add(number)
            else:
                malformed = True
    if malformed:
        return {"ok": False, "reason": "WINDOW_CONFIG_INVALID", "tier": "model"}
    if len(bound) == 1:
        return {"ok": True, "window": next(iter(bound)), "tier": "model",
                "models": sorted(models)}
    if len(bound) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "tier": "model"}

    present, windows = latest_explicit_windows(rows)
    if len(windows) == 1:
        return {"ok": True, "window": next(iter(windows)), "tier": "transcript"}
    if len(windows) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "tier": "transcript"}
    if present:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_INVALID", "tier": "transcript"}
    compact = [number for number in (compact_pre_tokens(row) for row in rows) if number]
    if compact:
        return {"ok": True, "window": min(compact), "tier": "compact_boundary"}
    return {"ok": False, "reason": "CONTEXT_SIGNAL_UNAVAILABLE", "tier": "none",
            "models": sorted(models)}


def payload_mutated_paths(payload: dict[str, Any]) -> set[str]:
    tool = payload.get("tool_name") or payload.get("toolName")
    if tool not in MUTATING_TOOLS:
        return set()
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    paths: set[str] = set()
    if isinstance(value, dict):
        for key in ("file_path", "path", "notebook_path"):
            item = value.get(key)
            if isinstance(item, str) and item:
                paths.add(os.path.abspath(os.path.expanduser(item)))
    return paths


def owner_digest(owner: dict[str, Any]) -> str:
    return digest({key: owner.get(key) for key in
                   ("id", "surface", "generation", "state", "evidence_digest",
                    "activation_init_digest", "activation_final_digest",
                    "terminal_evidence_digest", "terminal_provenance_digest")})


def fresh_signal() -> dict[str, Any]:
    return {"highwater": 0, "invocations": 0, "active_minutes": 0.0,
            "cycles": 0, "generation_tool_calls": 0,
            "mutated_paths": [], "worker_starts": 0,
            "last_observed_at": None, "notices": []}


def initial_state(task_key: str, owner_id: str, surface: str,
                  evidence_digest: str | None,
                  activation_init_digest: str) -> dict[str, Any]:
    owner = {"id": owner_id, "surface": surface, "generation": 0,
             "state": "ACTIVE", "evidence_digest": evidence_digest,
             "activation_init_digest": activation_init_digest}
    owner["ownership_digest"] = owner_digest(owner)
    return {
        "schema_version": 1, "task_key": task_key, "version": -1,
        "task_status": "ACTIVE", "generation": 0, "active_owner": owner_id,
        "owners": {owner_id: owner}, "handoff": None, "recovery_intent": None,
        "recovery_history": [],
        "signal": fresh_signal(),
    }


def claude_owner_task_key(owner_id: str, manifest: dict[str, Any]) -> str | None:
    """Resolve a native Claude session back to its accepted lifecycle task.

    The controller callback does not carry an application-defined task key.
    The permanent native-identity index is the authoritative binding. Only the
    one bound task is validated; unrelated state can neither add latency nor
    block this callback.
    """
    binding = read_identity_binding("claude", owner_id, manifest)
    if binding is None:
        return None
    task_key = str(binding["task_key"])
    value = read_state(task_key, manifest)
    owner = ((value or {}).get("owners") or {}).get(owner_id)
    if (not isinstance(owner, dict)
            or owner.get("surface") != "claude"
            or owner.get("evidence_digest")
            != binding.get("activation_provenance_digest")):
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Claude identity binding does not match its lifecycle owner")
    return task_key


def hook_task_key(payload: dict[str, Any],
                  manifest: dict[str, Any] | None = None) -> tuple[str, str]:
    explicit = (payload.get("task_key")
                if "task_key" in payload
                else os.environ.get("CARR_CONTEXT_TASK_KEY"))
    sessions = [payload[key] for key in ("session_id", "sessionId")
                if key in payload]
    if (not sessions
            or any(not isinstance(value, str) or not value.strip()
                   for value in sessions)
            or len(set(sessions)) != 1):
        raise LifecycleError(
            "LIFECYCLE_INVALID",
            "live Claude callback requires one nonempty native session identity")
    session = sessions[0]
    if explicit is not None and (
            not isinstance(explicit, str) or not explicit.strip()):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "callback task key is invalid")
    if manifest is not None:
        bound = claude_owner_task_key(session, manifest)
        if bound:
            if explicit and explicit != bound:
                raise LifecycleError(
                    "OWNERSHIP_MISMATCH",
                    "callback task key conflicts with authoritative Claude binding")
            return bound, session
    if explicit:
        return explicit, session
    return f"claude:{session}", session


def update_observation(current: dict[str, Any] | None, task_key: str, owner_id: str,
                       payload: dict[str, Any], rows: list[dict[str, Any]],
                       event: str, now: dt.datetime,
                       manifest: dict[str, Any]) -> dict[str, Any]:
    transcript = payload.get("transcript_path") or payload.get("transcriptPath")
    callback_id = (payload.get("prompt_id") or payload.get("promptId")
                   or payload.get("tool_use_id") or payload.get("toolUseId"))
    recorded = ((current or {}).get("owners") or {}).get(owner_id)
    if recorded and recorded.get("surface") != "claude":
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Claude callback cannot observe a non-Claude owner")
    # A provenance-verified historical terminal callback is intentionally a
    # no-op even after its native transcript has been archived. Every live or
    # new callback must instead prove the controller identity and a readable
    # transcript before it can observe or initialize lifecycle state.
    if recorded and recorded.get("state") == "TERMINAL":
        if current is None:  # narrowed by recorded; explicit for type checking
            raise LifecycleError(
                "LIFECYCLE_INVALID", "terminal owner lacks task state")
        return current
    if (not isinstance(owner_id, str) or not owner_id
            or not isinstance(callback_id, str) or not callback_id
            or not isinstance(transcript, str) or not transcript):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "live Claude callback evidence is incomplete")
    transcript_path = Path(transcript).expanduser()
    try:
        with open(transcript_path, "rb") as live_transcript:
            live_transcript.read(1)
    except (OSError, ValueError) as exc:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "hook transcript_path is not a readable file") from exc
    if not transcript_path.is_file():
        raise LifecycleError(
            "LIFECYCLE_INVALID", "hook transcript_path is not a live file")
    transcript = str(transcript_path.resolve())
    if current is None:
        native_evidence = {
            "session_id": owner_id,
            "transcript_path": transcript,
            "controller_callback_id": callback_id,
            "status": "active",
        }
        evidence_digest = digest(native_evidence)
        packet = {
            "schema_version": 1, "task_key": task_key,
            "owner": owner_id, "surface": "claude", "generation": 0,
            "source": "claude_hook", "native_evidence": native_evidence,
            "native_evidence_digest": evidence_digest, "created_at": utc_now(),
        }
        init_digest = write_immutable(
            task_key, "initialization", packet, manifest)
        claim_identity(
            "claude", owner_id, task_key, evidence_digest, manifest)
        state = initial_state(
            task_key, owner_id, "claude", evidence_digest, init_digest)
    else:
        state = current
    if state.get("task_status") == "TERMINAL":
        return state
    # One task may still receive the draining predecessor's final Stop after a
    # successor is active.  That terminal callback must neither contaminate the
    # successor generation's counters nor advance task state.
    if state.get("active_owner") != owner_id:
        return state
    signal = state.setdefault("signal", {})
    totals = [usage_total(row) for row in rows]
    signal["highwater"] = max([positive(signal.get("highwater")) or 0]
                              + [number for number in totals if number])
    last = signal.get("last_observed_at")
    if last:
        try:
            elapsed = max(0.0, (now - parse_time(last)).total_seconds())
            cap = int(surface_policy(manifest, "claude")["fallback_caps"]["adjacent_activity_cap_seconds"])
            signal["active_minutes"] = round(float(signal.get("active_minutes", 0.0))
                                                    + min(elapsed, cap) / 60.0, 3)
        except Exception:
            pass
    signal["last_observed_at"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tool = payload.get("tool_name") or payload.get("toolName")
    if event == "PostToolUse":
        signal["invocations"] = int(signal.get("invocations", 0)) + 1
        signal["generation_tool_calls"] = int(signal.get("generation_tool_calls", 0)) + 1
        signal["mutated_paths"] = sorted(set(signal.get("mutated_paths") or [])
                                          | payload_mutated_paths(payload))
        if tool in WORKER_TOOLS:
            signal["worker_starts"] = int(signal.get("worker_starts", 0)) + 1
    if event == "Stop":
        signal["cycles"] = int(signal.get("cycles", 0)) + 1
    risk = payload.get("risk") or payload.get("risk_level")
    if isinstance(risk, str) and risk:
        signal["risk"] = risk.upper()
    return state


def density(signal: dict[str, Any], manifest: dict[str, Any],
            surface: str = "claude") -> bool:
    cfg = surface_policy(manifest, surface)["density"]
    return (
        signal.get("risk") in set(cfg["risk_levels"])
        or int(signal.get("generation_tool_calls", 0)) >= int(cfg["generation_tool_calls"])
        or len(set(signal.get("mutated_paths") or [])) >= int(cfg["distinct_mutated_paths"])
        or int(signal.get("worker_starts", 0)) >= int(cfg["worker_starts"])
    )


def fallback_level(signal: dict[str, Any], manifest: dict[str, Any], dense: bool,
                   surface: str = "claude") -> str | None:
    caps = surface_policy(manifest, surface)["fallback_caps"]
    values = (int(signal.get("invocations", 0)), float(signal.get("active_minutes", 0.0)),
              int(signal.get("cycles", 0)))
    for level in ("hard", "dense_soft" if dense else "normal_soft"):
        cap = caps[level]
        if (values[0] >= int(cap["invocations"]) or values[1] >= float(cap["active_minutes"])
                or values[2] >= int(cap["cycles"])):
            return level
    return None


def context_decision(rows: list[dict[str, Any]], state: dict[str, Any],
                     manifest: dict[str, Any]) -> dict[str, Any]:
    signal_state = state.get("signal") or {}
    policy = surface_policy(manifest, "claude")
    window = resolve_window(rows, manifest, "claude")
    used = max([positive(signal_state.get("highwater")) or 0]
               + [number for number in (usage_total(row) for row in rows) if number])
    is_dense = density(signal_state, manifest)
    fallback = fallback_level(signal_state, manifest, is_dense)
    threshold_name = "dense_soft" if is_dense else "normal_soft"
    threshold = int(policy["thresholds_percent"][threshold_name])
    if window.get("ok") and used:
        ratio = 100.0 * used / int(window["window"])
        hard = int(policy["thresholds_percent"]["hard"])
        crossed = ratio >= hard or ratio >= threshold
        return {"available": True, "used": used, "window": window["window"],
                "ratio": round(ratio, 3), "ratio_label": "claude_transcript",
                "window_tier": window["tier"], "dense": is_dense,
                "threshold": hard if ratio >= hard else threshold,
                "crossed": crossed, "fallback_level": fallback,
                "reason": "CONTEXT_HANDOFF_REQUIRED" if crossed else None}
    reason = window.get("reason") or "CONTEXT_SIGNAL_UNAVAILABLE"
    control_error = reason in {
        "WINDOW_CONFIG_INVALID", "CONTEXT_SIGNAL_AMBIGUOUS"}
    return {"available": False, "used": used or None, "dense": is_dense,
            "crossed": bool(fallback) or control_error,
            "fallback_level": fallback,
            "reason": (reason if control_error else
                       "CONTEXT_HANDOFF_REQUIRED" if fallback else reason),
            "signal_reason": reason, "window_tier": window.get("tier")}


def block_payload(task_key: str, version: int, signal: dict[str, Any],
                  reason_code: str = "CONTEXT_HANDOFF_REQUIRED") -> dict[str, Any]:
    reason = {"action": "REFUSE", "reason": reason_code,
              "signal": signal, "task": task_key, "version": version}
    return {"decision": "block", "reason": canonical(reason).decode("utf-8")}


def _add_notice(state: dict[str, Any] | None, notice: str) -> dict[str, Any]:
    if not state:
        raise LifecycleError("LIFECYCLE_INVALID", "state disappeared")
    signal = state.setdefault("signal", {})
    signal["notices"] = sorted(set(signal.get("notices") or []) | {notice})
    return state


def refuse_stop_on_control_error(event: str, task_key: str, reason: str,
                                 version: int = -1) -> None:
    if event != "Stop":
        return
    announce(f"Claude context lifecycle control warning ({reason}); native Claude behavior continues.")


def hook_main() -> int:
    wired_event = os.environ.get("CARR_CONTEXT_HOOK_EVENT")
    event = wired_event or "Stop"
    task_key = os.environ.get("CARR_CONTEXT_TASK_KEY", "claude:unknown")
    owner_id = "unknown"
    manifest: dict[str, Any] | None = None
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "hook payload is not an object")
        payload_event = payload.get("hook_event_name") or payload.get("hookEventName")
        if wired_event is not None:
            if wired_event not in {"PostToolUse", "PreCompact", "Stop"}:
                # The controller intended to select a trusted seam but supplied
                # unusable wiring. Treat that control failure as Stop so it can
                # never become the only event spelling that silently allows.
                event = "Stop"
                raise LifecycleError(
                    "LIFECYCLE_INVALID", "wired hook event is invalid")
            event = wired_event
        else:
            event = payload_event or "Stop"
            if event not in {"PostToolUse", "PreCompact", "Stop"}:
                raise LifecycleError(
                    "LIFECYCLE_INVALID", "payload hook event is invalid")
        task_key, owner_id = hook_task_key(payload)
        rows = transcript_rows(
            payload.get("transcript_path") or payload.get("transcriptPath"))
        manifest = load_manifest()
        task_key, owner_id = hook_task_key(payload, manifest)
    except LifecycleError as exc:
        audit({"session": owner_id, "event": event,
               "action": "ANNOUNCE" if event == "Stop" else "NOOP",
               "reason": exc.reason, "detail": exc.detail[:500]}, manifest)
        refuse_stop_on_control_error(event, task_key, exc.reason)
        return 0
    except Exception as exc:
        audit({"session": owner_id, "event": event,
               "action": "ANNOUNCE" if event == "Stop" else "NOOP",
               "reason": "LIFECYCLE_INVALID", "detail": str(exc)[:500]}, manifest)
        refuse_stop_on_control_error(event, task_key, "LIFECYCLE_INVALID")
        return 0
    try:
        state = mutate_state(
            task_key, None,
            lambda current: update_observation(current, task_key, owner_id, payload, rows,
                                               event, dt.datetime.now(dt.timezone.utc), manifest),
            manifest,
        )
    except LifecycleError as exc:
        audit({"session": owner_id, "event": event,
               "action": "ANNOUNCE" if event == "Stop" else "NOOP",
               "reason": exc.reason}, manifest)
        refuse_stop_on_control_error(event, task_key, exc.reason)
        return 0
    except Exception as exc:
        audit({"session": owner_id, "event": event,
               "action": "ANNOUNCE" if event == "Stop" else "NOOP",
               "reason": "LIFECYCLE_INVALID", "detail": str(exc)[:500]}, manifest)
        refuse_stop_on_control_error(event, task_key, "LIFECYCLE_INVALID")
        return 0
    try:
        signal = context_decision(rows, state, manifest)
    except Exception as exc:
        # Persisted state is outside the hook process's trust boundary.  A
        # semantically malformed scalar must refuse Stop just like unreadable
        # JSON does; letting ValueError escape disables the only blocking seam.
        audit({"session": owner_id, "event": event,
               "action": "ANNOUNCE" if event == "Stop" else "NOOP",
               "reason": "LIFECYCLE_INVALID", "detail": str(exc)[:500]}, manifest)
        version = state.get("version", -1)
        if isinstance(version, bool) or not isinstance(version, int):
            version = -1
        refuse_stop_on_control_error(event, task_key, "LIFECYCLE_INVALID",
                                     version)
        return 0
    audit({"session": owner_id, "event": event, "task_key": task_key,
           "action": "ANNOUNCE" if event == "Stop" and signal.get("crossed") else "NOOP",
           "version": state["version"], "caught": bool(signal.get("crossed")),
           "signal": signal}, manifest)
    if event == "PreCompact":
        return 0
    if state.get("task_status") == "TERMINAL":
        return 0
    pending = state.get("handoff") or {}
    if pending.get("state") in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
        try:
            verify_handoff_state(task_key, state, manifest)
        except LifecycleError as exc:
            if event == "Stop":
                announce(f"Claude continuity could not verify prior handoff ({exc.reason}); native Claude behavior continues.")
            return 0
    caller = (state.get("owners") or {}).get(owner_id) or {}
    if (caller.get("surface") == "claude"
            and caller.get("state") == "TERMINAL"):
        return 0
    if (pending.get("state") in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}
            and pending.get("predecessor") == owner_id
            and caller.get("state") in {"DRAINING", "TERMINAL"}):
        return 0
    if state.get("active_owner") != owner_id:
        if event == "Stop":
            announce("Claude continuity ownership evidence does not match; native Claude behavior continues.")
        return 0
    if event == "Stop" and signal.get("crossed"):
        # Once per task, like the PostToolUse notice below. Announcing on every
        # Stop re-woke the session after each reply, so an idle session kept
        # replying to its own notice (2026-09-23, Mac Studio setup session).
        notice_key = "STOP_HEADROOM_NOTICE"
        if notice_key in set((state.get("signal") or {}).get("notices") or []):
            return 0
        try:
            mutate_state(task_key, state["version"],
                         lambda current: _add_notice(current, notice_key), manifest)
        except LifecycleError:
            return 0
        announce("Claude context headroom notice: checkpoint durable semantic progress when appropriate; native auto-compaction and Stop remain available.")
        return 0
    if event == "PostToolUse" and (signal.get("crossed") or not signal.get("available")):
        notice_key = str(signal.get("reason") or signal.get("signal_reason")
                         or "CONTEXT_SIGNAL_UNAVAILABLE")
        notices = set((state.get("signal") or {}).get("notices") or [])
        if notice_key not in notices:
            try:
                mutate_state(task_key, state["version"],
                             lambda current: _add_notice(current, notice_key), manifest)
            except LifecycleError:
                return 0
            if signal.get("crossed"):
                announce("Context lifecycle threshold reached. Finish the current unit of work and complete a verified same-task handoff before the next Stop boundary.")
            else:
                announce(f"Context lifecycle signal is {signal.get('signal_reason', 'unavailable')}; fallback activity counters are active.")
    return 0


def parse_evidence(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception as exc:
        raise LifecycleError("LIFECYCLE_INVALID", f"evidence JSON invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "evidence must be an object")
    return value


def validate_native_evidence(surface: str, evidence: dict[str, Any], *,
                             expected_identity: str | None = None,
                             accept: bool = False,
                             terminal: bool = False,
                             require_live: bool = True) -> str:
    if surface == "codex":
        required = {"thread_id", "project_id", "cwd", "status", "event_id"}
        if (not required.issubset(evidence)
                or any(not isinstance(evidence.get(key), str)
                       or not evidence[key].strip() for key in required)):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID", "Codex evidence fields missing")
        pinned = evidence.get("pinnedIndex")
        if ((accept and "pinnedIndex" not in evidence)
                or ("pinnedIndex" in evidence
                    and (isinstance(pinned, bool)
                         or not isinstance(pinned, int) or pinned <= 0))):
            raise LifecycleError(
                "SUCCESSOR_NOT_PINNED", "Codex pinnedIndex must be a positive integer")
        if require_live:
            try:
                evidence["cwd"] = str(
                    Path(evidence["cwd"]).expanduser().resolve())
            except (OSError, RuntimeError, ValueError) as exc:
                raise LifecycleError(
                    "SUCCESSOR_SURFACE_INVALID", "Codex cwd is invalid") from exc
        else:
            cwd = evidence["cwd"]
            if (not Path(cwd).is_absolute()
                    or os.path.normpath(cwd) != cwd):
                raise LifecycleError(
                    "SUCCESSOR_SURFACE_INVALID",
                    "historical Codex cwd is not stored canonical absolute bytes")
        if expected_identity is not None and evidence.get("thread_id") != expected_identity:
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                                 "Codex thread_id does not match lifecycle owner")
        if (require_live
                and not Path(evidence["cwd"]).is_dir()):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID", "Codex cwd is not a live directory")
        if accept and evidence["status"].lower() not in {"active", "idle", "running"}:
            raise LifecycleError("SUCCESSOR_NOT_ACTIVE", "Codex successor is not active")
        if terminal and evidence["status"].lower() not in {"archived", "terminated", "terminal"}:
            raise LifecycleError("LIFECYCLE_INVALID", "Codex predecessor lacks terminal evidence")
    elif surface == "claude":
        required = {"session_id", "transcript_path", "controller_callback_id", "status"}
        if (not required.issubset(evidence)
                or any(not isinstance(evidence.get(key), str)
                       or not evidence[key].strip() for key in required)):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID", "Claude evidence fields missing")
        if expected_identity is not None and evidence.get("session_id") != expected_identity:
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                                 "Claude session_id does not match lifecycle owner")
        if (require_live
                and not Path(evidence["transcript_path"]).expanduser().is_file()):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                                 "Claude transcript_path is not a live file")
        if accept and evidence["status"].lower() not in {"active", "idle", "running"}:
            raise LifecycleError("SUCCESSOR_NOT_ACTIVE", "Claude successor is not active")
        if terminal and evidence["status"].lower() not in {"archived", "terminated", "terminal"}:
            raise LifecycleError("LIFECYCLE_INVALID", "Claude predecessor lacks terminal evidence")
    else:
        raise LifecycleError("SUCCESSOR_SURFACE_INVALID", f"unsupported surface {surface}")
    return digest(evidence)


def validate_codex_checkout(evidence: dict[str, Any]) -> None:
    """Require a Codex owner to belong to this repository checkout."""
    try:
        Path(evidence["cwd"]).relative_to(REPO.resolve())
    except (KeyError, TypeError, OSError, RuntimeError, ValueError) as exc:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Codex owner is outside the CARR checkout") from exc


def owner_activation_evidence(task_key: str, owner: dict[str, Any],
                              manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Return the immutable evidence that admitted an owner generation."""
    if owner.get("generation") == 0:
        packet = read_object(
            task_key, "initialization",
            str(owner.get("activation_init_digest")), manifest)
        evidence = packet.get("native_evidence")
    else:
        verified = read_verified_final_packet(
            task_key, owner.get("activation_final_digest"), manifest)
        evidence = verified["acceptance"].get("native_evidence")
    if not isinstance(evidence, dict):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "owner activation evidence is missing")
    return evidence


def validate_owner_native_binding(task_key: str, owner: dict[str, Any],
                                  evidence: dict[str, Any],
                                  manifest: dict[str, Any] | None,
                                  *, error_reason: str = "OWNERSHIP_MISMATCH",
                                  require_live_checkout: bool = True) -> None:
    """Compare current/terminal Codex evidence to immutable admission bytes."""
    if owner.get("surface") != "codex":
        return
    try:
        if require_live_checkout:
            validate_codex_checkout(evidence)
        admitted = owner_activation_evidence(task_key, owner, manifest)
        if (evidence.get("project_id") != admitted.get("project_id")
                or evidence.get("cwd") != admitted.get("cwd")):
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex project or cwd changed after activation")
    except LifecycleError as exc:
        raise LifecycleError(error_reason, exc.detail) from exc


def validate_predecessor_binding(task_key: str, state: dict[str, Any],
                                 owner_id: str, surface: str,
                                 evidence: dict[str, Any],
                                 manifest: dict[str, Any]) -> None:
    """Bind a new offer to the predecessor's immutable admission identity."""
    owner = (state.get("owners") or {}).get(owner_id)
    if not isinstance(owner, dict) or owner.get("surface") != surface:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "predecessor surface does not match recorded owner")
    if surface != "codex":
        return
    validate_owner_native_binding(task_key, owner, evidence, manifest)


def validate_successor_evidence(offer: dict[str, Any], successor: str,
                                evidence: dict[str, Any], *,
                                accept: bool = False,
                                require_live: bool = True) -> str:
    surface = offer["successor_surface"]
    evidence_digest = validate_native_evidence(
        surface, evidence, expected_identity=successor, accept=accept,
        require_live=require_live)
    if surface == "codex":
        if require_live:
            validate_codex_checkout(evidence)
        trusted_project = offer.get("successor_project_id")
        if (not isinstance(trusted_project, str)
                or not trusted_project.strip()
                or str(evidence.get("project_id", "")).strip()
                != trusted_project):
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex successor project is not predecessor-authorized")
    # A same-surface Codex handoff must stay in the project and checkout that
    # the predecessor offered. The thread id changes by design; changing the
    # project or cwd would be a different slice wearing this task_key.
    if offer.get("predecessor_surface") == surface == "codex":
        predecessor = offer.get("native_evidence") or {}
        if evidence.get("cwd") != predecessor.get("cwd"):
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex successor is not in the offered project and cwd")
    return evidence_digest


def lifecycle_init(args, manifest):
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_native_evidence(
        args.surface, evidence, expected_identity=args.owner, accept=True)
    if args.surface == "codex":
        validate_codex_checkout(evidence)
    packet = {
        "schema_version": 1, "task_key": args.task_key,
        "owner": args.owner, "surface": args.surface, "generation": 0,
        "source": "task_init", "native_evidence": evidence,
        "native_evidence_digest": evidence_digest, "created_at": utc_now(),
    }
    result: dict[str, str] = {}

    def create(current):
        if current is not None:
            raise LifecycleError("LIFECYCLE_INVALID", "task already exists")
        result["init_digest"] = write_immutable(
            args.task_key, "initialization", packet, manifest)
        claim_identity(
            args.surface, args.owner, args.task_key,
            evidence_digest, manifest)
        return initial_state(
            args.task_key, args.owner, args.surface,
            evidence_digest, result["init_digest"])

    return mutate_state(args.task_key, args.expected_version, create, manifest)


def offer_create(args, manifest):
    if args.predecessor_surface not in SURFACES:
        raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                             f"unsupported predecessor surface {args.predecessor_surface}")
    if args.successor_surface not in SURFACES:
        raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                             f"unsupported successor surface {args.successor_surface}")
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_native_evidence(
        args.predecessor_surface, evidence, expected_identity=args.predecessor)
    current = read_state(args.task_key, manifest)
    if not current or current.get("task_status") != "ACTIVE":
        raise LifecycleError("LIFECYCLE_INVALID", "task is not active")
    if args.predecessor == args.successor or args.successor in current["owners"]:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "successor identity was already used by this task")
    validate_predecessor_binding(
        args.task_key, current, args.predecessor,
        args.predecessor_surface, evidence, manifest)
    successor_project_id = None
    if args.successor_surface == "codex":
        project_field = ("project_id" if args.predecessor_surface == "codex"
                         else "successor_project_id")
        project_value = evidence.get(project_field)
        if not isinstance(project_value, str) or not project_value.strip():
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex successor lacks predecessor-authorized project binding")
        successor_project_id = project_value.strip()
    offer = {"schema_version": 1, "task_key": args.task_key,
             "generation": args.generation, "predecessor": args.predecessor,
             "predecessor_surface": args.predecessor_surface,
             "successor": args.successor, "successor_surface": args.successor_surface,
             "native_evidence": evidence, "native_evidence_digest": evidence_digest,
             "created_at": utc_now()}
    if successor_project_id is not None:
        offer["successor_project_id"] = successor_project_id
    result: dict[str, str] = {}

    def change(state):
        if not state or state.get("task_status") != "ACTIVE":
            raise LifecycleError("LIFECYCLE_INVALID", "task is not active")
        if state.get("active_owner") != args.predecessor:
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor is not sole active owner")
        if args.predecessor == args.successor or args.successor in state["owners"]:
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "successor identity was already used by this task")
        validate_predecessor_binding(
            args.task_key, state, args.predecessor,
            args.predecessor_surface, evidence, manifest)
        if args.generation != int(state.get("generation", 0)) + 1:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "offer generation must be current generation plus one")
        if state.get("handoff") and state["handoff"].get("state") not in {None, "PREDECESSOR_TERMINAL"}:
            raise LifecycleError("LIFECYCLE_INVALID", "handoff already pending")
        owner = state["owners"].get(args.predecessor)
        recovery = state.get("recovery_intent") or {}
        if recovery.get("state") == "ABORTED":
            if (recovery.get("failed_owner") != args.predecessor
                    or recovery.get("generation") != state.get("generation")):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "aborted recovery does not belong to the predecessor")
            state["recovery_intent"] = None
            recovery = {}
        recovering = (recovery.get("state") == "PENDING"
                      and recovery.get("failed_owner") == args.predecessor
                      and recovery.get("task_key") == args.task_key
                      and recovery.get("generation") == state.get("generation"))
        if not owner or owner.get("surface") != args.predecessor_surface:
            raise LifecycleError("OWNERSHIP_MISMATCH",
                                 "predecessor surface does not match recorded owner")
        if owner.get("state") != "ACTIVE" and not (
                recovering and owner.get("state") == "DRAINING"):
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor owner state is not ACTIVE")
        result["offer_digest"] = write_immutable(
            args.task_key, "offer", offer, manifest)
        owner["state"] = "DRAINING"
        owner["ownership_digest"] = owner_digest(owner)
        state["handoff"] = {"state": "DRAINING",
                            "offer_digest": result["offer_digest"],
                            "predecessor": args.predecessor, "successor": args.successor,
                            "generation": args.generation}
        return state

    state = mutate_state(args.task_key, args.expected_version, change, manifest)
    return {"offer_digest": result["offer_digest"], "state": state}


def successor_declare(args, manifest):
    offer = read_object(args.task_key, "offer", args.offer_digest, manifest)
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_successor_evidence(
        offer, args.successor, evidence, accept=True)
    declaration = {"schema_version": 1, "task_key": args.task_key,
                   "offer_digest": args.offer_digest, "successor": args.successor,
                   "native_evidence": evidence, "native_evidence_digest": evidence_digest,
                   "declared_at": utc_now()}
    result: dict[str, str] = {}

    def change(state):
        pending = (state or {}).get("handoff") or {}
        if pending.get("state") != "DRAINING" or pending.get("offer_digest") != args.offer_digest:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "offer is not the pending handoff")
        if offer.get("successor") != args.successor:
            raise LifecycleError("OWNERSHIP_MISMATCH", "successor does not match offer")
        if args.successor in state["owners"]:
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "successor identity was already used by this task")
        result["declaration_digest"] = write_immutable(
            args.task_key, "declaration", declaration, manifest)
        claim_identity(
            offer["successor_surface"], args.successor, args.task_key,
            evidence_digest, manifest)
        successor = {"id": args.successor, "surface": offer["successor_surface"],
                     "generation": offer["generation"], "state": "SUCCESSOR_DECLARED",
                     "evidence_digest": evidence_digest}
        successor["ownership_digest"] = owner_digest(successor)
        state["owners"][args.successor] = successor
        pending.update({"state": "SUCCESSOR_DECLARED",
                        "declaration_digest": result["declaration_digest"]})
        return state

    state = mutate_state(args.task_key, args.expected_version, change, manifest)
    return {"declaration_digest": result["declaration_digest"], "state": state}


def successor_accept(args, manifest):
    offer = read_object(args.task_key, "offer", args.offer_digest, manifest)
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_successor_evidence(
        offer, args.successor, evidence, accept=True)
    current = read_state(args.task_key, manifest)
    pending_now = (current or {}).get("handoff") or {}
    declaration_digest = pending_now.get("declaration_digest")
    if not declaration_digest:
        raise LifecycleError("TAKEOVER_NOT_VERIFIED", "successor declaration is absent")
    declaration = read_object(
        args.task_key, "declaration", declaration_digest, manifest)
    if (declaration.get("successor") != args.successor
            or declaration.get("native_evidence_digest") != evidence_digest
            or declaration.get("native_evidence") != evidence):
        raise LifecycleError("TAKEOVER_NOT_VERIFIED",
                             "acceptance evidence does not match declaration")
    acceptance = {"successor": args.successor, "surface": offer["successor_surface"],
                  "native_evidence": evidence, "native_evidence_digest": evidence_digest,
                  "accepted_at": utc_now()}
    receipt = {"schema_version": 1, "task_key": args.task_key,
               "offer_digest": args.offer_digest,
               "declaration_digest": declaration_digest,
               "ownership_acceptance": acceptance}
    result: dict[str, str] = {}

    def change(state):
        pending = (state or {}).get("handoff") or {}
        if pending.get("state") != "SUCCESSOR_DECLARED" or pending.get("offer_digest") != args.offer_digest:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "successor was not declared for this offer")
        if pending.get("declaration_digest") != declaration_digest:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "successor declaration changed")
        if offer.get("successor") != args.successor:
            raise LifecycleError("OWNERSHIP_MISMATCH", "successor does not match offer")
        predecessor = state["owners"].get(offer["predecessor"])
        successor = state["owners"].get(args.successor)
        if not predecessor or predecessor.get("state") != "DRAINING":
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor is not draining")
        if not successor or successor.get("state") != "SUCCESSOR_DECLARED":
            raise LifecycleError("SUCCESSOR_NOT_ACTIVE", "successor declaration is absent")
        result["receipt_digest"] = write_immutable(
            args.task_key, "receipt", receipt, manifest)
        final_packet = {"schema_version": 1, "offer": offer,
                        "declaration": declaration,
                        "ownership_acceptance": acceptance,
                        "offer_digest": args.offer_digest,
                        "declaration_digest": declaration_digest,
                        "receipt_digest": result["receipt_digest"]}
        result["final_digest"] = write_immutable(
            args.task_key, "final", final_packet, manifest)
        successor.update({"state": "ACTIVE", "evidence_digest": evidence_digest,
                          "activation_final_digest": result["final_digest"]})
        successor["ownership_digest"] = owner_digest(successor)
        state["active_owner"] = args.successor
        state["generation"] = int(offer["generation"])
        state["signal"] = fresh_signal()
        recovery = state.get("recovery_intent") or {}
        if (recovery.get("state") == "PENDING"
                and recovery.get("failed_owner") == offer.get("predecessor")
                and recovery.get("task_key") == args.task_key
                and recovery.get("generation") == int(offer["generation"]) - 1):
            recovery.update({"state": "COMPLETED", "successor": args.successor,
                             "completed_at": utc_now()})
        pending.update({"state": "TAKEOVER_VERIFIED",
                        "receipt_digest": result["receipt_digest"],
                        "final_digest": result["final_digest"]})
        return state

    state = mutate_state(args.task_key, args.expected_version, change, manifest)
    return {"receipt_digest": result["receipt_digest"],
            "final_digest": result["final_digest"], "state": state}


def read_verified_final_packet(task_key: str, final_digest: Any,
                               manifest: dict[str, Any] | None = None
                               ) -> dict[str, Any]:
    """Validate a self-contained immutable handoff independently of live state."""
    if not is_digest(final_digest):
        raise LifecycleError("HANDOFF_RECEIPT_MISSING", "final_digest missing")
    final = read_object(task_key, "final", str(final_digest), manifest)
    digests = {
        key: final.get(key)
        for key in ("offer_digest", "declaration_digest", "receipt_digest")
    }
    for key, value in digests.items():
        if not is_digest(value):
            raise LifecycleError("HANDOFF_RECEIPT_MISSING", f"{key} missing")
    offer_digest = str(digests["offer_digest"])
    declaration_digest = str(digests["declaration_digest"])
    receipt_digest = str(digests["receipt_digest"])
    offer = read_object(task_key, "offer", offer_digest, manifest)
    declaration = read_object(
        task_key, "declaration", declaration_digest, manifest)
    receipt = read_object(
        task_key, "receipt", receipt_digest, manifest)
    acceptance = receipt.get("ownership_acceptance") or {}
    acceptance_evidence = acceptance.get("native_evidence") or {}
    offer_evidence = offer.get("native_evidence") or {}
    successor_project_id = offer.get("successor_project_id")
    offer_generation = offer.get("generation")
    if (final.get("schema_version") != 1
            or offer.get("schema_version") != 1
            or offer.get("task_key") != task_key
            or offer.get("predecessor_surface") not in SURFACES
            or offer.get("successor_surface") not in SURFACES
            or not isinstance(offer.get("predecessor"), str)
            or not offer.get("predecessor")
            or not isinstance(offer.get("successor"), str)
            or not offer.get("successor")
            or offer.get("predecessor") == offer.get("successor")
            or isinstance(offer_generation, bool)
            or not isinstance(offer_generation, int)
            or offer_generation < 1
            or not isinstance(offer_evidence, dict)
            or digest(offer_evidence) != offer.get("native_evidence_digest")
            or declaration.get("schema_version") != 1
            or declaration.get("task_key") != task_key
            or declaration.get("offer_digest") != digests["offer_digest"]
            or declaration.get("successor") != offer.get("successor")
            or receipt.get("schema_version") != 1
            or receipt.get("task_key") != task_key
            or receipt.get("offer_digest") != digests["offer_digest"]
            or receipt.get("declaration_digest")
            != digests["declaration_digest"]
            or acceptance.get("successor") != offer.get("successor")
            or acceptance.get("surface") != offer.get("successor_surface")
            or final.get("offer") != offer
            or final.get("declaration") != declaration
            or final.get("ownership_acceptance") != acceptance
            or declaration.get("native_evidence_digest")
            != acceptance.get("native_evidence_digest")
            or declaration.get("native_evidence")
            != acceptance.get("native_evidence")
            or not isinstance(acceptance_evidence, dict)
            or digest(acceptance_evidence)
            != acceptance.get("native_evidence_digest")
            or (offer.get("successor_surface") == "codex"
                and (not isinstance(successor_project_id, str)
                     or not successor_project_id.strip()
                     or str(acceptance_evidence.get("project_id", "")).strip()
                     != successor_project_id))):
        raise LifecycleError("HANDOFF_RECEIPT_INVALID",
                             "offer/receipt/final linkage is invalid")
    try:
        predecessor_evidence_digest = validate_native_evidence(
            str(offer.get("predecessor_surface")), offer_evidence,
            expected_identity=str(offer.get("predecessor")),
            require_live=False)
        successor_evidence_digest = validate_successor_evidence(
            offer, str(offer.get("successor")), acceptance_evidence,
            accept=True, require_live=False)
    except LifecycleError as exc:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            f"handoff native evidence is invalid: {exc.detail}") from exc
    if (predecessor_evidence_digest != offer.get("native_evidence_digest")
            or successor_evidence_digest
            != acceptance.get("native_evidence_digest")):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "handoff native evidence digest changed")
    return {"offer": offer, "declaration": declaration,
            "receipt": receipt, "final": final, "acceptance": acceptance,
            **digests, "final_digest": final_digest}


def verify_owner_activation(task_key: str, owner: dict[str, Any],
                            manifest: dict[str, Any] | None = None) -> None:
    verified = read_verified_final_packet(
        task_key, owner.get("activation_final_digest"), manifest)
    offer = verified["offer"]
    acceptance = verified["acceptance"]
    if (offer.get("successor") != owner.get("id")
            or offer.get("successor_surface") != owner.get("surface")
            or offer.get("generation") != owner.get("generation")
            or acceptance.get("native_evidence_digest")
            != owner.get("evidence_digest")):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "owner activation is not linked to immutable acceptance")


def verify_handoff_state(task_key: str, state: dict[str, Any],
                         manifest: dict[str, Any] | None = None
                         ) -> dict[str, Any]:
    pending = state.get("handoff") or {}
    if pending.get("state") not in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
        raise LifecycleError("TAKEOVER_NOT_VERIFIED", "takeover is not verified")
    for key in ("offer_digest", "declaration_digest", "receipt_digest", "final_digest"):
        if not pending.get(key):
            raise LifecycleError("HANDOFF_RECEIPT_MISSING", f"{key} missing")
    verified = read_verified_final_packet(
        task_key, pending["final_digest"], manifest)
    offer = verified["offer"]
    acceptance = verified["acceptance"]
    owners = state.get("owners") or {}
    predecessor_owner = owners.get(pending.get("predecessor")) or {}
    successor_owner = owners.get(pending.get("successor")) or {}
    if (verified["offer_digest"] != pending.get("offer_digest")
            or verified["declaration_digest"]
            != pending.get("declaration_digest")
            or verified["receipt_digest"] != pending.get("receipt_digest")
            or offer.get("generation") != pending.get("generation")
            or offer.get("predecessor") != pending.get("predecessor")
            or offer.get("successor") != pending.get("successor")
            or offer.get("predecessor_surface")
            != predecessor_owner.get("surface")
            or offer.get("successor_surface") != successor_owner.get("surface")
            or successor_owner.get("id") != offer.get("successor")
            or successor_owner.get("generation") != offer.get("generation")
            or successor_owner.get("evidence_digest")
            != acceptance.get("native_evidence_digest")):
        raise LifecycleError("HANDOFF_RECEIPT_INVALID",
                             "offer/receipt/final linkage is invalid")
    return {key: verified[key] for key in
            ("offer_digest", "declaration_digest", "receipt_digest", "final_digest")}


def predecessor_terminal(args, manifest):
    evidence = parse_evidence(args.evidence_json)

    def change(state):
        pending = (state or {}).get("handoff") or {}
        if pending.get("state") != "TAKEOVER_VERIFIED" or pending.get("predecessor") != args.predecessor:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "takeover is not verified")
        owner = state["owners"].get(args.predecessor)
        if not owner or owner.get("state") != "DRAINING":
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor is not draining")
        verify_handoff_state(args.task_key, state, manifest)
        evidence_digest = validate_native_evidence(
            owner["surface"], evidence,
            expected_identity=args.predecessor, terminal=True)
        validate_owner_native_binding(
            args.task_key, owner, evidence, manifest)
        terminal_packet = {
            "schema_version": 1, "task_key": args.task_key,
            "terminal_kind": "predecessor_terminal",
            "owner": args.predecessor, "surface": owner["surface"],
            "generation": owner["generation"],
            "native_evidence": evidence,
            "native_evidence_digest": evidence_digest,
            "handoff_final_digest": pending.get("final_digest"),
            "created_at": utc_now(),
        }
        provenance_digest = write_immutable(
            args.task_key, "terminal", terminal_packet, manifest)
        owner.update({"state": "TERMINAL",
                      "terminal_evidence_digest": evidence_digest,
                      "terminal_provenance_digest": provenance_digest})
        owner["ownership_digest"] = owner_digest(owner)
        pending["state"] = "PREDECESSOR_TERMINAL"
        return state

    return mutate_state(args.task_key, args.expected_version, change, manifest)


def task_terminal(args, manifest):
    evidence = parse_evidence(args.evidence_json)

    def change(state):
        if not state or state.get("task_status") == "TERMINAL":
            raise LifecycleError("RECOVERY_TERMINAL", "task is already terminal")
        if state.get("active_owner") != args.owner:
            raise LifecycleError("OWNERSHIP_MISMATCH", "only sole active owner may terminate task")
        active = [owner for owner in state.get("owners", {}).values()
                  if owner.get("state") == "ACTIVE"]
        if len(active) != 1:
            raise LifecycleError("OWNERSHIP_DUPLICATE", "task lacks exactly one active owner")
        handoff = state.get("handoff") or {}
        if handoff.get("state") in {"DRAINING", "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED"}:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "handoff is incomplete")
        evidence_digest = validate_native_evidence(
            active[0]["surface"], evidence,
            expected_identity=args.owner, terminal=True)
        validate_owner_native_binding(
            args.task_key, active[0], evidence, manifest)
        terminal_packet = {
            "schema_version": 1, "task_key": args.task_key,
            "terminal_kind": "task_terminal",
            "owner": args.owner, "surface": active[0]["surface"],
            "generation": active[0]["generation"],
            "native_evidence": evidence,
            "native_evidence_digest": evidence_digest,
            "handoff_final_digest": (handoff.get("final_digest")
                                     if handoff.get("state")
                                     == "PREDECESSOR_TERMINAL" else None),
            "created_at": utc_now(),
        }
        provenance_digest = write_immutable(
            args.task_key, "terminal", terminal_packet, manifest)
        active[0].update({"state": "TERMINAL",
                          "terminal_evidence_digest": evidence_digest,
                          "terminal_provenance_digest": provenance_digest})
        active[0]["ownership_digest"] = owner_digest(active[0])
        state["task_status"] = "TERMINAL"
        state["active_owner"] = None
        return state

    return mutate_state(args.task_key, args.expected_version, change, manifest)


def resolve_codex_rollout(path: str) -> dict[str, Any]:
    rows = transcript_rows(path)
    authoritative: set[str] = set()
    legacy: set[str] = set()
    turns: set[str] = set()
    invalid = {"payload.id": False, "session_id": False,
               "task_started.turn_id": False}

    def capture(payload: dict[str, Any], field: str, target: set[str],
                tier: str) -> None:
        if field not in payload:
            return
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            invalid[tier] = True
            return
        target.add(value)

    for row in rows:
        raw_payload = row.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        if row.get("type") == "session_meta":
            capture(payload, "id", authoritative, "payload.id")
            capture(payload, "session_id", legacy, "session_id")
        if (row.get("type") == "event_msg"
                and payload.get("type") == "task_started"):
            capture(payload, "turn_id", turns, "task_started.turn_id")
    invalid_tier = next((tier for tier in (
        "payload.id", "session_id", "task_started.turn_id")
        if invalid[tier]), None)
    if invalid_tier is not None:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_INVALID",
                "resolver": invalid_tier}
    if len(authoritative) == 1:
        value = next(iter(authoritative))
        return {"ok": True, "task_key": f"codex:{value}", "resolver": "payload.id",
                "ignored_lineage": sorted(item for item in legacy if item != value)}
    if len(authoritative) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "resolver": "payload.id"}
    if len(legacy) == 1:
        return {"ok": True, "task_key": f"codex:{next(iter(legacy))}", "resolver": "session_id"}
    if len(legacy) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "resolver": "session_id"}
    if len(turns) == 1:
        return {"ok": True, "task_key": f"codex-turn:{next(iter(turns))}",
                "resolver": "task_started.turn_id"}
    return {"ok": False,
            "reason": "CONTEXT_SIGNAL_AMBIGUOUS" if len(turns) > 1
            else "CONTEXT_SIGNAL_UNAVAILABLE",
            "resolver": "task_started.turn_id"}


def codex_rollout_metrics(path: str, manifest: dict[str, Any]) -> dict[str, Any]:
    rows = transcript_rows(path)
    timestamps: list[dt.datetime] = []
    invocations = cycles = worker_starts = 0
    mutated: set[str] = set()
    for row in rows:
        try:
            timestamps.append(parse_time(row.get("timestamp")))
        except Exception:
            pass
        raw_payload = row.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        kind = payload.get("type")
        if (row.get("type") == "response_item"
                and kind in {"custom_tool_call", "function_call", "mcp_tool_call"}):
            invocations += 1
            name = payload.get("name") or ""
            if name in WORKER_TOOLS:
                worker_starts += 1
            args = payload.get("arguments") or payload.get("input")
            if name in MUTATING_TOOLS and isinstance(args, dict):
                for key in ("path", "file_path"):
                    if isinstance(args.get(key), str):
                        mutated.add(os.path.abspath(os.path.expanduser(args[key])))
        if row.get("type") == "event_msg" and kind == "task_started":
            cycles += 1
    timestamps.sort()
    policy = surface_policy(manifest, "codex")
    cap = int(policy["fallback_caps"]["adjacent_activity_cap_seconds"])
    active_seconds = sum(min(max(0.0, (b - a).total_seconds()), cap)
                         for a, b in zip(timestamps, timestamps[1:]))
    highwater = max([number for number in
                     (usage_total(row) for row in rows) if number] or [0])
    signal = {"highwater": highwater, "invocations": invocations,
              "active_minutes": round(active_seconds / 60.0, 3), "cycles": cycles,
              "generation_tool_calls": invocations, "mutated_paths": sorted(mutated),
              "worker_starts": worker_starts}
    is_dense = density(signal, manifest, "codex")
    fallback = fallback_level(signal, manifest, is_dense, "codex")
    window = resolve_window(rows, manifest, "codex")
    result = {"ratio_label": "internal_rollout", "signal": signal, "dense": is_dense,
              "fallback_level": fallback, "window": window}
    if window.get("ok") and highwater:
        result["ratio"] = round(100.0 * highwater / int(window["window"]), 3)
    return result


SNAPSHOT_KEYS = {"task_key", "observed_at", "source_event_id", "observer_id",
                 "source_surface", "evidence_ref", "evidence_digest", "workers"}
WORKER_KEYS = {"id", "surface", "generation", "normalized_state", "last_progress_at",
               "task_terminal", "attention_kind", "error_code", "recoverable",
               "capacity_code", "ownership_digest"}


def validate_snapshot(snapshot: Any, task_key: str,
                      manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_KEYS:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             "dispatcher snapshot keys are not exact")
    if snapshot.get("task_key") != task_key:
        raise LifecycleError("OWNERSHIP_MISMATCH", "snapshot task_key mismatch")
    for field in ("source_event_id", "observer_id", "source_surface",
                  "evidence_ref"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 f"{field} must be a non-empty string")
    if snapshot.get("source_surface") not in SURFACES:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             "source_surface is invalid")
    evidence = {key: snapshot.get(key) for key in SNAPSHOT_KEYS
                if key != "evidence_digest"}
    if (not is_digest(snapshot.get("evidence_digest"))
            or digest(evidence) != snapshot.get("evidence_digest")):
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             "snapshot evidence digest mismatch")
    now = dt.datetime.now(dt.timezone.utc)
    try:
        observed = parse_time(snapshot["observed_at"])
    except Exception as exc:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             f"observed_at invalid: {exc}") from exc
    age = (now - observed).total_seconds()
    if age < 0:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "future snapshot")
    if age > int(manifest["dispatcher"]["freshness_seconds"]):
        raise LifecycleError("RECOVERY_SNAPSHOT_STALE", "snapshot is stale")
    workers = snapshot.get("workers")
    if not isinstance(workers, list):
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "workers must be an array")
    for worker in workers:
        if not isinstance(worker, dict) or set(worker) != WORKER_KEYS:
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "worker keys are not exact")
        worker_generation = worker.get("generation")
        if (not isinstance(worker.get("id"), str) or not worker["id"].strip()
                or worker.get("surface") not in SURFACES
                or isinstance(worker_generation, bool)
                or not isinstance(worker_generation, int)
                or worker_generation < 0
                or not isinstance(worker.get("task_terminal"), bool)
                or not isinstance(worker.get("recoverable"), bool)
                or not is_digest(worker.get("ownership_digest"))):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker identity fields are invalid")
        if worker.get("normalized_state") not in WORKER_STATES:
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "worker state invalid")
        attention = worker.get("attention_kind")
        if (not isinstance(attention, str) or not attention
                or attention not in ({"NONE"} | set(
                    manifest["dispatcher"]["waiting_attention_kinds"]))):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker attention kind is invalid")
        for field in ("error_code", "capacity_code"):
            if (worker.get(field) is not None
                    and (not isinstance(worker.get(field), str)
                         or not worker[field])):
                raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                     f"worker {field} is invalid")
        if ((worker.get("normalized_state") == "TERMINAL")
                != worker.get("task_terminal")):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker terminal evidence is inconsistent")
        try:
            last_progress = parse_time(worker.get("last_progress_at"))
        except Exception as exc:
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 f"last_progress_at invalid: {exc}") from exc
        if (last_progress - now).total_seconds() > int(
                manifest["dispatcher"]["future_skew_seconds"]):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker last_progress_at is in the future")
    return workers


def dispatcher_decision(task_key: str, snapshot: dict[str, Any],
                        state: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    workers = validate_snapshot(snapshot, task_key, manifest)
    if state.get("task_status") == "TERMINAL":
        return {"action": "NOOP", "reason": "RECOVERY_TERMINAL"}
    active_id = state.get("active_owner")
    if not active_id:
        raise LifecycleError("OWNERSHIP_MISSING", "task has no active owner")
    expected = (state.get("owners") or {}).get(active_id, {}).get("ownership_digest")
    matches = [worker for worker in workers
               if worker.get("ownership_digest") == expected]
    if not matches:
        raise LifecycleError("OWNERSHIP_MISSING", "snapshot has no current owner")
    if len(matches) > 1:
        raise LifecycleError("OWNERSHIP_DUPLICATE",
                             "snapshot has duplicate current owners")
    worker = matches[0]
    if worker.get("id") != active_id:
        raise LifecycleError("OWNERSHIP_MISMATCH", "snapshot owner id mismatch")
    recorded = (state.get("owners") or {}).get(active_id) or {}
    if (worker.get("surface") != recorded.get("surface")
            or worker.get("generation") != recorded.get("generation")):
        raise LifecycleError("OWNERSHIP_MISMATCH",
                             "snapshot owner surface or generation mismatch")
    state_name = worker["normalized_state"]
    waiting = set(manifest["dispatcher"]["waiting_attention_kinds"])
    capacity = set(manifest["dispatcher"]["recoverable_capacity_codes"])
    if state_name == "TERMINAL" or worker.get("task_terminal"):
        return {"action": "NOOP", "reason": "RECOVERY_TERMINAL",
                "failed_owner": active_id}
    if (state_name == "CAPACITY_EXHAUSTED"
            and worker.get("capacity_code") in capacity):
        return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_CAPACITY",
                "failed_owner": active_id}
    if state_name == "ERROR" and worker.get("recoverable") is True:
        return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_ERROR",
                "failed_owner": active_id}
    if state_name == "IDLE" and worker.get("attention_kind") in {None, "NONE"}:
        return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_IDLE",
                "failed_owner": active_id}
    if (state_name == "WAITING_ATTENTION"
            and worker.get("attention_kind") in waiting):
        return {"action": "NOOP", "reason": "RECOVERY_WAITING",
                "failed_owner": active_id}
    if state_name == "RUNNING":
        heartbeat_age = (dt.datetime.now(dt.timezone.utc)
                         - parse_time(worker["last_progress_at"])).total_seconds()
        if heartbeat_age > int(manifest["dispatcher"]["heartbeat_max_seconds"]):
            return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_IDLE",
                    "failed_owner": active_id}
        return {"action": "NOOP", "reason": "RECOVERY_ACTIVE",
                "failed_owner": active_id}
    raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                         "worker condition has no valid predicate")


def dispatcher_evaluate(args, manifest):
    state = read_state(args.task_key, manifest)
    if not state:
        raise LifecycleError("OWNERSHIP_MISSING", "task state missing")
    snapshot = parse_evidence(args.snapshot_json)
    validate_snapshot(snapshot, args.task_key, manifest)
    pending = state.get("recovery_intent")
    consumed = recovery_history_event_ids(args.task_key, state, manifest)
    pending_replay = (pending and pending.get("state") == "PENDING"
                      and pending.get("source_event_id")
                      == snapshot.get("source_event_id"))
    if snapshot.get("source_event_id") in consumed and not pending_replay:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "recovery source event was already consumed")
    if pending and pending.get("state") == "PENDING":
        same = (
            pending.get("task_key") == args.task_key
            and pending.get("generation") == state.get("generation")
            and pending.get("source_event_id") == snapshot.get("source_event_id")
            and pending.get("snapshot_digest") == snapshot.get("evidence_digest")
        )
        if not same:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "recovery replay evidence changed")
        replay_state = state
        if args.apply:
            if args.expected_version is None:
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "--apply requires --expected-version")

            def verify_replay(current):
                current_pending = (current or {}).get("recovery_intent") or {}
                if (current_pending.get("state") != "PENDING"
                        or current_pending.get("nonce") != pending.get("nonce")
                        or current_pending.get("snapshot_digest")
                        != snapshot.get("evidence_digest")):
                    raise LifecycleError("LIFECYCLE_INVALID",
                                         "recovery replay state changed")
                return current

            replay_state = mutate_state(
                args.task_key, args.expected_version, verify_replay, manifest)
        return {
            "action": "RECOVER_SAME_TASK",
            "reason": pending["cause"],
            "failed_owner": pending["failed_owner"],
            "nonce": pending["nonce"],
            "applied": bool(args.apply),
            "replay": True,
            **({"state": replay_state} if args.apply else {}),
        }
    decision = dispatcher_decision(args.task_key, snapshot, state, manifest)
    if decision["action"] != "RECOVER_SAME_TASK":
        return {**decision, "applied": False}
    handoff = state.get("handoff") or {}
    if handoff.get("state") in {
            "DRAINING", "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED"}:
        raise LifecycleError(
            "LIFECYCLE_INVALID",
            "recovery cannot start while a prior handoff is nonterminal")
    nonce_input = {"task_key": args.task_key, "generation": state.get("generation"),
                   "failed_owner": decision["failed_owner"],
                   "cause": decision["reason"],
                   "source_event_id": snapshot["source_event_id"],
                   "snapshot_digest": snapshot["evidence_digest"]}
    nonce = digest(nonce_input)
    result = {**decision, "nonce": nonce, "applied": False, "replay": False}
    if not args.apply:
        return result
    if args.expected_version is None:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "--apply requires --expected-version")

    replay = False

    def change(current):
        nonlocal replay
        if not current or current.get("task_status") == "TERMINAL":
            raise LifecycleError("RECOVERY_TERMINAL",
                                 "terminal task cannot recover")
        pending = current.get("recovery_intent") or {}
        if pending.get("state") == "PENDING":
            if pending.get("nonce") == nonce:
                replay = True
                return current
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "another recovery intent is pending")
        owner = current["owners"].get(decision["failed_owner"])
        if not owner or owner.get("state") != "ACTIVE":
            raise LifecycleError("OWNERSHIP_MISMATCH",
                                 "old owner is not ACTIVE")
        prior_handoff = current.get("handoff") or {}
        if prior_handoff.get("state") == "PREDECESSOR_TERMINAL":
            if prior_handoff.get("successor") != decision["failed_owner"]:
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "completed handoff successor does not match recovery owner")
            current["handoff"] = None
        history = current.get("recovery_history")
        if not isinstance(history, list):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "recovery history is missing")
        event_record = {
            "schema_version": 1, **nonce_input, "nonce": nonce,
            "previous_digest": history[-1] if history else None,
        }
        history_digest = write_immutable(
            args.task_key, "recovery-event", event_record, manifest)
        history.append(history_digest)
        owner["state"] = "DRAINING"
        owner["ownership_digest"] = owner_digest(owner)
        current["recovery_intent"] = {**nonce_input, "nonce": nonce,
                                      "history_digest": history_digest,
                                      "state": "PENDING",
                                      "created_at": utc_now()}
        return current

    updated = mutate_state(args.task_key, args.expected_version, change, manifest)
    result.update({"applied": True, "replay": replay, "state": updated})
    return result


def recovery_abort(args, manifest):
    def change(state):
        if not state or state.get("task_status") == "TERMINAL":
            raise LifecycleError("RECOVERY_TERMINAL", "terminal task cannot recover")
        pending = state.get("recovery_intent") or {}
        if (pending.get("state") != "PENDING"
                or pending.get("nonce") != args.nonce
                or pending.get("failed_owner") != args.owner):
            raise LifecycleError("LIFECYCLE_INVALID", "pending recovery does not match abort")
        handoff = state.get("handoff") or {}
        if handoff.get("state") not in {None, "PREDECESSOR_TERMINAL"}:
            raise LifecycleError("LIFECYCLE_INVALID", "recovery handoff already started")
        owner = (state.get("owners") or {}).get(args.owner)
        if not owner or owner.get("state") != "DRAINING":
            raise LifecycleError("OWNERSHIP_MISMATCH", "recovering owner is not DRAINING")
        owner["state"] = "ACTIVE"
        owner["ownership_digest"] = owner_digest(owner)
        state["active_owner"] = args.owner
        pending.update({"state": "ABORTED", "aborted_at": utc_now()})
        return state

    return mutate_state(args.task_key, args.expected_version, change, manifest)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command")
    status = sub.add_parser("status")
    status.add_argument("--task-key", required=True)
    key = sub.add_parser("codex-task-key")
    key.add_argument("--rollout", required=True)
    generic_key = sub.add_parser("task-key")
    generic_key.add_argument("--surface", choices=("codex", "claude"),
                             required=True)
    generic_key.add_argument("--rollout")
    generic_key.add_argument("--session-id")
    generic_key.add_argument("--transcript")
    observe = sub.add_parser("codex-observe")
    observe.add_argument("--rollout", required=True)

    init = sub.add_parser("task-init")
    init.add_argument("--task-key", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--surface", choices=("codex", "claude"), required=True)
    init.add_argument("--evidence-json", required=True)
    init.add_argument("--expected-version", type=int, required=True)

    offer = sub.add_parser("handoff-offer-create")
    for name in ("task-key", "predecessor", "predecessor-surface",
                 "successor", "successor-surface", "evidence-json"):
        offer.add_argument(f"--{name}", required=True)
    offer.add_argument("--generation", type=int, required=True)
    offer.add_argument("--expected-version", type=int, required=True)

    declare = sub.add_parser("successor-declare")
    for name in ("task-key", "offer-digest", "successor", "evidence-json"):
        declare.add_argument(f"--{name}", required=True)
    declare.add_argument("--expected-version", type=int, required=True)

    accept = sub.add_parser("successor-accept")
    for name in ("task-key", "offer-digest", "successor", "evidence-json"):
        accept.add_argument(f"--{name}", required=True)
    accept.add_argument("--expected-version", type=int, required=True)

    pred = sub.add_parser("predecessor-terminal")
    pred.add_argument("--task-key", required=True)
    pred.add_argument("--predecessor", required=True)
    pred.add_argument("--evidence-json", required=True)
    pred.add_argument("--expected-version", type=int, required=True)
    verify = sub.add_parser("verify-handoff")
    verify.add_argument("--task-key", required=True)

    terminal = sub.add_parser("task-terminal")
    terminal.add_argument("--task-key", required=True)
    terminal.add_argument("--owner", required=True)
    terminal.add_argument("--evidence-json", required=True)
    terminal.add_argument("--expected-version", type=int, required=True)

    dispatch = sub.add_parser("dispatcher-evaluate")
    dispatch.add_argument("--task-key", required=True)
    dispatch.add_argument("--snapshot-json", required=True)
    dispatch.add_argument("--apply", action="store_true")
    dispatch.add_argument("--expected-version", type=int)
    abort = sub.add_parser("recovery-abort")
    abort.add_argument("--task-key", required=True)
    abort.add_argument("--owner", required=True)
    abort.add_argument("--nonce", required=True)
    abort.add_argument("--expected-version", type=int, required=True)
    return p


def cli_main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = load_manifest()
        if args.command == "status":
            result = read_state(args.task_key, manifest)
        elif args.command == "codex-task-key":
            result = resolve_codex_rollout(args.rollout)
        elif args.command == "task-key":
            if args.surface == "codex":
                if not args.rollout:
                    raise LifecycleError("CONTEXT_SIGNAL_UNAVAILABLE",
                                         "Codex task-key needs --rollout")
                result = resolve_codex_rollout(args.rollout)
            elif args.session_id:
                result = {"ok": True,
                          "task_key": f"claude:{args.session_id}",
                          "resolver": "session_id"}
            elif args.transcript:
                result = {
                    "ok": True,
                    "task_key": "claude-transcript:"
                    + digest(os.path.abspath(args.transcript)),
                    "resolver": "transcript_path",
                }
            else:
                raise LifecycleError("CONTEXT_SIGNAL_UNAVAILABLE",
                                     "Claude task-key needs session or transcript")
        elif args.command == "codex-observe":
            result = {"task": resolve_codex_rollout(args.rollout),
                      "context": codex_rollout_metrics(args.rollout, manifest)}
        elif args.command == "task-init":
            result = lifecycle_init(args, manifest)
        elif args.command == "handoff-offer-create":
            result = offer_create(args, manifest)
        elif args.command == "successor-declare":
            result = successor_declare(args, manifest)
        elif args.command == "successor-accept":
            result = successor_accept(args, manifest)
        elif args.command == "predecessor-terminal":
            result = predecessor_terminal(args, manifest)
        elif args.command == "verify-handoff":
            state = read_state(args.task_key, manifest)
            if not state:
                raise LifecycleError("OWNERSHIP_MISSING", "task state missing")
            result = verify_handoff_state(args.task_key, state, manifest)
        elif args.command == "task-terminal":
            result = task_terminal(args, manifest)
        elif args.command == "dispatcher-evaluate":
            result = dispatcher_evaluate(args, manifest)
        elif args.command == "recovery-abort":
            result = recovery_abort(args, manifest)
        else:
            parser().print_help(sys.stderr)
            return 2
        print(canonical(result).decode("utf-8"))
        return 0
    except LifecycleError as exc:
        print(canonical({"action": "REFUSE", "reason": exc.reason,
                         "detail": exc.detail}).decode("utf-8"))
        return 2


def main() -> int:
    return cli_main(sys.argv[1:]) if len(sys.argv) > 1 else hook_main()


if __name__ == "__main__":
    raise SystemExit(main())
