"""Disabled, fixture-only atomic runtime allocation and entrant-lock contract.

This module is deliberately not an entrypoint and has no live consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time
from typing import Callable, Mapping


class IsolationRefusal(RuntimeError):
    """Fail closed while preserving registry evidence."""


DENIED_CREDENTIAL_NAMES = frozenset({
    "DATABASE_URL", "PRODUCTION_DATABASE_URL", "BREAKGLASS_TOKEN",
    "BREAK_GLASS_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY",
    "CARR_DB_JOBS_URL", "CARR_DB_OWNER_URL", "CARR_DB_BACKUP_URL",
    "CARR_AUTHORITY_TOKEN", "CARR_DEVICE_TOKEN",
})
DENIED_CREDENTIAL_FRAGMENTS = ("PROD", "BREAK", "AUTHORITY", "OWNER", "BACKUP", "JOBS", "DEVICE")
ALLOWED_FAKE_CREDENTIAL_NAMES = frozenset({"R09_FAKE_TOKEN", "R09_FAKE_DATABASE_URL"})


def _decode_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"allocations": {}, "entrant": None, "receipts": []}
    try:
        with state_path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise IsolationRefusal("unknown registry state is preserved") from exc
    if not isinstance(state, dict) or set(state) != {"allocations", "entrant", "receipts"}:
        raise IsolationRefusal("unknown registry state is preserved")
    return state


def read_lock_posture(root: Path, owner_alive: Callable[[str], bool | None] | None = None) -> dict:
    """Read the R07 preservation posture without creating any registry artifact."""
    root = Path(root)
    state_path, lock_path = root / "r09-state.json", root / "r09-registry.lock"
    if not state_path.exists():
        return {"state": "none", "entrant": None}
    try:
        with lock_path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                state = _decode_state(state_path)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise IsolationRefusal("registry read lock missing; state preserved") from exc
    current = state["entrant"]
    state_name = "none" if current is None else "active"
    if current and owner_alive is not None and owner_alive(current["owner"]) is None:
        state_name = "stale_uncertain"
    return {"state": state_name, "entrant": current.copy() if current else None}


@dataclass(frozen=True)
class Owner:
    session: str
    worktree: str
    source_sha: str
    @property
    def key(self) -> str:
        return "|".join((self.session, self.worktree, self.source_sha))


@dataclass(frozen=True)
class Bundle:
    ports: tuple[int, ...]
    compose_name: str
    database_name: str


class RuntimeIsolation:
    """One flock-protected fixture registry under an already-authorized root."""
    def __init__(self, root: Path, owner: Owner, *, clock: Callable[[], float] = time.time):
        self.root, self.owner, self.clock = Path(root), owner, clock
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.state_path = self.root / "r09-state.json"
        self.lock_path = self.root / "r09-registry.lock"

    def _locked(self):
        handle = self.lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def _read(self) -> dict:
        return _decode_state(self.state_path)

    def _write(self, state: dict) -> None:
        fd, name = tempfile.mkstemp(dir=self.root, prefix=".r09-", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(name, 0o600); os.replace(name, self.state_path)
        finally:
            if os.path.exists(name): os.unlink(name)

    def _receipt(self, state: dict, event: str, *, run_id: str | None = None, generation: str | None = None) -> None:
        state["receipts"].append({"event": event, "at": self.clock(), "owner": self.owner.key,
                                  "run_id": run_id, "generation": generation})

    def _artifact(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    def allocate(self, bundle: Bundle, *, ttl_seconds: int = 300, idempotency_key: str | None = None) -> dict:
        if not bundle.ports or len(set(bundle.ports)) != len(bundle.ports) or any(not isinstance(p, int) for p in bundle.ports):
            raise IsolationRefusal("bundle must contain distinct explicit ports")
        if not bundle.compose_name or not bundle.database_name or ttl_seconds <= 0:
            raise IsolationRefusal("bundle must be complete")
        key = idempotency_key or secrets.token_hex(16)
        with self._locked():
            state = self._read(); now = self.clock()
            for run_id, entry in state["allocations"].items():
                if (not isinstance(run_id, str) or not isinstance(entry, dict)
                        or not isinstance(entry.get("owner"), str)
                        or not isinstance(entry.get("idempotency_key"), str)
                        or not isinstance(entry.get("expires_at"), (int, float))
                        or not isinstance(entry.get("resources"), list)
                        or not all(isinstance(item, str) for item in entry["resources"])):
                    raise IsolationRefusal("malformed allocation entry is preserved")
                if entry["owner"] == self.owner.key and entry["idempotency_key"] == key:
                    if entry["expires_at"] <= now: raise IsolationRefusal("expired allocation is preserved")
                    return {"run_id": run_id, "generation": entry["generation"], "artifact": self._artifact(run_id)}
            used = {item for entry in state["allocations"].values() for item in entry["resources"]}
            resources = {f"port:{p}" for p in bundle.ports} | {f"compose:{bundle.compose_name}", f"database:{bundle.database_name}"}
            if resources & used: raise IsolationRefusal("complete bundle collision; no partial claim")
            run_id, generation = secrets.token_hex(16), secrets.token_hex(16); artifact = self._artifact(run_id)
            try:
                artifact.mkdir(mode=0o700, parents=True, exist_ok=False)
                state["allocations"][run_id] = {"owner": self.owner.key, "generation": generation,
                    "resources": sorted(resources), "state": "claimed", "idempotency_key": key,
                    "expires_at": now + ttl_seconds, "claimed_at": now}
                self._receipt(state, "allocation-claimed", run_id=run_id, generation=generation); self._write(state)
            except Exception as exc:
                if artifact.exists(): shutil.rmtree(artifact, ignore_errors=True)
                if isinstance(exc, IsolationRefusal): raise
                raise IsolationRefusal("allocation was not committed") from exc
            return {"run_id": run_id, "generation": generation, "artifact": artifact}

    def _owned_entry(self, state: dict, allocation: Mapping[str, object]) -> tuple[str, dict]:
        run_id, generation = str(allocation.get("run_id", "")), str(allocation.get("generation", ""))
        entry = state["allocations"].get(run_id)
        if not entry or entry.get("owner") != self.owner.key or entry.get("generation") != generation:
            raise IsolationRefusal("allocation ownership or generation mismatch")
        return run_id, entry

    def write_fake_environment(self, allocation: Mapping[str, object], values: Mapping[str, str]) -> Path:
        names = tuple(values.keys())
        if any(name in DENIED_CREDENTIAL_NAMES or any(bit in name for bit in DENIED_CREDENTIAL_FRAGMENTS) for name in names):
            raise IsolationRefusal("credential name refused before value access")
        if set(names) - ALLOWED_FAKE_CREDENTIAL_NAMES: raise IsolationRefusal("credential name is not allowlisted")
        with self._locked():
            state = self._read(); run_id, _ = self._owned_entry(state, allocation); artifact = self._artifact(run_id)
            path = artifact / "env"; fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    for name in names: handle.write(f"{name}={values[name]}\n")
                (artifact / ".r09-exclude-from-archives").write_text("env\n", encoding="utf-8")
                os.chmod(artifact / ".r09-exclude-from-archives", 0o600)
            except Exception:
                path.unlink(missing_ok=True); raise
            return path

    def acquire_entrant(self, run_id: str, *, stale_after_seconds: int, owner_alive: Callable[[str], bool | None]) -> str:
        with self._locked():
            state = self._read(); entry = state["allocations"].get(run_id)
            if not entry or entry.get("owner") != self.owner.key: raise IsolationRefusal("entrant run is not an owned allocation")
            now, current = self.clock(), state["entrant"]
            if current:
                alive = owner_alive(current["owner"])
                if now - current["created_at"] <= stale_after_seconds or alive is not False:
                    self._receipt(state, "entrant-refused", run_id=run_id); self._write(state); raise IsolationRefusal("entrant active or liveness uncertain")
            generation = secrets.token_hex(16)
            state["entrant"] = {"owner": self.owner.key, "run_id": run_id, "generation": generation, "created_at": now}
            self._receipt(state, "entrant-takeover" if current else "entrant-acquired", run_id=run_id, generation=generation); self._write(state)
            return generation

    def lock_posture(self, owner_alive: Callable[[str], bool | None] | None = None) -> dict:
        return read_lock_posture(self.root, owner_alive)

    def teardown(self, allocation: Mapping[str, object], entrant_generation: str | None = None) -> None:
        with self._locked():
            state = self._read(); run_id, _ = self._owned_entry(state, allocation); current = state["entrant"]
            if current and current["run_id"] == run_id and entrant_generation is None: raise IsolationRefusal("entrant generation required")
            if entrant_generation is not None:
                if not current or current["owner"] != self.owner.key or current["generation"] != entrant_generation or current["run_id"] != run_id:
                    raise IsolationRefusal("entrant ownership or generation mismatch")
            artifact = self._artifact(run_id)
            try:
                if artifact.exists(): shutil.rmtree(artifact)
            except OSError as exc: raise IsolationRefusal("artifact teardown failed; allocation preserved") from exc
            if entrant_generation is not None:
                state["entrant"] = None; self._receipt(state, "entrant-released", run_id=run_id, generation=entrant_generation)
            generation = state["allocations"][run_id]["generation"]; del state["allocations"][run_id]
            self._receipt(state, "allocation-released", run_id=run_id, generation=generation); self._write(state)
