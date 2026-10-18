#!/usr/bin/env python3
"""The desk registry: the only way Hermes is allowed to name a session.

A DESK IS A PLACE, NOT A PROCESS. Every ordinary Claude Code session binds
/tmp/cc-socks/<pid>.sock, and Joe's real windows sit in that directory. A pid
names a process that happened to start; it says nothing about what the session
is for, and it changes every time. A dispatcher that can address a pid can
walk into any window he has open, including one mid-way through a client
draft. So a pid socket is refused as a target — twice, at registration and
again at resolve, because the file between them is ordinary JSON a careless
script could edit.

A desk is a session started on purpose:

    claude --messaging-socket-path /tmp/cc-socks/claude-desk.sock

That flag is a statement of intent. A pid is not.

DESK KINDS:
  claude-session   a live labeled Claude Code session, addressed by socket
  claude-desktop   a queue-only Claude background session, handed to Desktop
  codex-session    a standing Codex thread, resumed per task through the CLI
  codex-live       a live Codex app-server, addressed by its unix socket
  flash-local      the local Flash model itself, answering direct questions (flash_wire.py)

BOTH KINDS KEEP THEIR CONTEXT, and that is the whole point. Joe, 2026-08-20:
The kinds differ only in HOW a turn reaches them: a Claude desk is a live
process listening on a socket, so a turn is delivered to something already
running. A Codex thread is durable rather than live — it is stored, resumed
per task, and needs no process sitting idle between turns.
"""

from __future__ import annotations

import json
import fcntl
from contextlib import contextmanager
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

import codex_models

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
PID_SOCKET = re.compile(r"^\d+\.sock$")

KINDS = ("claude-session", "claude-desktop", "claude-remote", "codex-session", "codex-live", "flash-local", "grok-cli")
KIND_ALIASES = {"codex-exec": "codex-session"}
EFFORT_CHOICES = ("minimal", "low", "medium", "high", "xhigh")

DESK_INSTRUCTION = (
    "Model Room desk instruction: If you need approvals, permissions, or decisions, "
    "send them to the orchestrator session via send_message in one message, then "
    "end the turn. Never ask Joe."
)


def desk_prompt(task: str) -> str:
    """Carry the fixed instruction on first turns and every resumed task."""
    if task.startswith("[Hermes queue "):
        headers, separator, body = task.partition("\n\n")
        if separator:
            return headers + separator + desk_prompt(body)
    if task == DESK_INSTRUCTION or task.startswith(DESK_INSTRUCTION + "\n\n"):
        return task
    return DESK_INSTRUCTION + ("\n\n" + task if task else "")

DEFAULT_REGISTRY = Path(
    os.environ.get(
        "CARR_HERMES_DESKS",
        Path.home() / ".config" / "carr" / "hermes-desks.json",
    )
)


class DeskError(Exception):
    """Refusal with a machine-readable reason, so Hermes can branch on it."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def dispatched_permission_mode(value: object = None) -> str:
    """Dispatched work denies permission needs instead of prompting a human."""
    mode = "dontAsk" if value is None else value
    if mode != "dontAsk":
        raise DeskError(
            "unsafe_permission_mode",
            f"dispatched desk permission mode {mode!r} is refused: use dontAsk "
            "(default). auto and acceptEdits can still prompt. Approvals and "
            "permission needs belong to the orchestrator; never ask Joe.",
        )
    return str(mode)


def is_live(sock_path: str, timeout: float = 0.25) -> bool:
    """Connect succeeds => a session is listening. The probe Claude Code uses."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def refuse_pid_socket(sock: str) -> None:
    if PID_SOCKET.match(os.path.basename(sock or "")):
        raise DeskError(
            "unlabeled_target",
            f"{sock} is a pid socket, not a desk. Start the session with "
            f"`claude --messaging-socket-path /tmp/cc-socks/<name>.sock` and "
            f"register that path instead — a pid is any window Joe happens to "
            f"have open.",
        )


def _normalize_effort(kind: str, effort: str | None) -> str | None:
    if effort is None:
        return None
    choices = ("low", "medium", "high", "xhigh", "max") if kind == "claude-remote" else EFFORT_CHOICES
    if effort not in choices:
        raise DeskError(
            "bad_effort",
            "reasoning effort must be one of minimal, low, medium, high, xhigh",
        )
    if kind == "claude-session":
        raise DeskError(
            "bad_effort",
            "a claude-session desk does not take effort; a live person's seat "
            "chooses its own model and reasoning profile",
        )
    return effort


def remote_posture(entry: dict) -> None:
    host = entry.get("host")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9][A-Za-z0-9_.-]*)?", host):
        raise DeskError("bad_remote_host", "remote desk needs an SSH host or user@host, never an option")
    model = entry.get("model")
    if not isinstance(model, str) or not re.fullmatch(r"claude-[a-z0-9]+(?:-[a-z0-9]+)+", model):
        raise DeskError("missing_model", "remote desk needs a full Claude model ID, never an alias")
    if not entry.get("effort"):
        raise DeskError("missing_effort", "remote desk needs an explicit reasoning effort")
    _normalize_effort("claude-remote", entry["effort"])
    dispatched_permission_mode(entry.get("permission_mode"))
    timeout = entry.get("timeout_s")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise DeskError("bad_remote_timeout", "remote desk deadline must be greater than 0 and at most 3600 seconds")


class Registry:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with Path(str(self.path) + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self) -> dict:
        with self._locked():
            return self._load_locked()

    def _load_locked(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            changed = False
            for entry in data.get("desks", {}).values():
                if entry.get("kind") in codex_models.CODEX_KINDS and "model" in entry:
                    entry["family"] = codex_models.family_default(entry.get("family"), entry.pop("model"))
                    changed = True
            if changed:
                self._save(data)
            return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {"desks": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(self.path)

    def entries(self) -> dict:
        return self._load().get("desks", {})

    def register(
        self,
        name: str,
        kind: str,
        socket: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        cwd: str | None = None,
        sandbox: str | None = None,
        add_dirs: list[str] | None = None,
        permission_mode: str | None = None,
        host: str | None = None,
        timeout_s: float = 900,
        family: str | None = None,
        network_access: bool = False,
    ) -> dict:
        if not NAME_OK.match(name or ""):
            raise DeskError(
                "bad_name",
                f"{name!r} is not a desk name — lowercase letters, digits and "
                f"hyphens, 2 to 41 characters",
            )
        kind = KIND_ALIASES.get(kind, kind)
        if kind not in KINDS:
            raise DeskError("bad_kind", f"{kind!r} is not one of {', '.join(KINDS)}")
        if type(network_access) is not bool:
            raise DeskError("bad_network_access", "network_access must be an explicit boolean")
        if network_access and kind != "codex-live":
            raise DeskError("bad_network_access", "network_access is supported only by codex-live")
        effort = _normalize_effort(kind, effort)
        if kind in codex_models.CODEX_KINDS:
            family = codex_models.family_default(family, model)
        elif family is not None:
            raise DeskError("bad_family", "family is only supported by Codex desks")

        entry: dict[str, object]
        if kind == "claude-session":
            if not socket:
                raise DeskError("missing_socket", "a claude-session desk needs --socket")
            refuse_pid_socket(socket)
            entry = {"kind": kind, "socket": str(socket),
                     "permission_mode": dispatched_permission_mode(permission_mode)}
        elif kind == "claude-desktop":
            if not model:
                raise DeskError("missing_model", "a claude-desktop desk needs --model")
            if not effort:
                raise DeskError("missing_effort", "a claude-desktop desk needs --effort")
            entry = {
                "kind": kind,
                "model": model,
                "effort": effort,
                "cwd": str(cwd or Path.cwd()),
                "permission_mode": dispatched_permission_mode(permission_mode),
            }
        elif kind == "claude-remote":
            entry = {"kind": kind, "host": host, "model": model, "effort": effort,
                     "timeout_s": timeout_s, "permission_mode": dispatched_permission_mode(permission_mode)}
            remote_posture(entry)
        elif kind == "grok-cli":
            if model != "grok-4.7" or effort != "high" or sandbox != "read-only":
                raise DeskError("bad_grok_posture", "Grok requires grok-4.7/high/read-only")
            entry = {"kind": kind, "model": model, "effort": effort,
                     "sandbox": sandbox, "cwd": str(cwd or Path.cwd())}
        elif kind == "flash-local":
            entry = {"kind": kind, "model": "flash", "effort": "minimal"}
        elif kind == "codex-live":
            if not socket:
                raise DeskError("missing_socket", "a codex-live desk needs --socket")
            refuse_pid_socket(socket)
            entry = {"kind": kind, "socket": str(socket), "thread_id": None,
                     "cwd": str(cwd or Path.cwd())}
            entry["effort"] = effort
            if family:
                entry["family"] = family
            if network_access:
                entry["network_access"] = True
        else:
            entry = {"kind": kind, "cwd": str(cwd or Path.cwd()),
                     "thread_id": None, "effort": effort}
            if family:
                entry["family"] = family
            if sandbox:
                entry["sandbox"] = sandbox
            if add_dirs:
                entry["add_dirs"] = [str(x) for x in add_dirs]

        entry["registered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._locked():
            data = self._load_locked()
            data.setdefault("desks", {})[name] = entry
            self._save(data)
        return entry

    def forget(self, name: str) -> None:
        with self._locked():
            data = self._load_locked()
            if data.get("desks", {}).pop(name, None) is not None:
                self._save(data)

    def remember_thread(self, name: str, thread_id: str) -> None:
        """Pin a Codex desk to the thread it just spoke in.

        Without this a Codex desk would start empty every task, which is the
        behaviour Joe rejected: it has its own context and is used as an equal
        seat, not as a shot.
        """
        with self._locked():
            data = self._load_locked()
            entry = data.get("desks", {}).get(name)
            if entry is None or entry.get("thread_id") == thread_id:
                return
            entry["thread_id"] = thread_id
            self._save(data)

    def resolve(self, name: str) -> dict:
        entry = self.entries().get(name or "")
        if entry is None:
            known = ", ".join(sorted(self.entries())) or "none registered"
            raise DeskError("unknown_desk", f"no desk named {name!r} (known: {known})")
        kind = KIND_ALIASES.get(entry.get("kind"), entry.get("kind"))
        entry = {**entry, "kind": kind}
        if kind not in KINDS:
            raise DeskError("bad_kind", f"desk {name!r} has kind {kind!r}")
        if kind == "claude-remote":
            remote_posture(entry)
        if kind in ("claude-session", "claude-desktop"):
            entry["permission_mode"] = dispatched_permission_mode(entry.get("permission_mode"))
        if kind == "grok-cli" and (entry.get("model") != "grok-4.7"
                or entry.get("effort") != "high" or entry.get("sandbox") != "read-only"):
            raise DeskError("bad_grok_posture", "Grok requires grok-4.7/high/read-only")
        network_access = entry.get("network_access", False)
        if type(network_access) is not bool or (network_access and kind != "codex-live"):
            raise DeskError("bad_network_access", "network_access must be boolean and is supported only by codex-live")
        if kind in ("claude-session", "codex-live"):
            sock = entry.get("socket", "")
            refuse_pid_socket(sock)
            if not is_live(sock):
                raise DeskError(
                    "desk_not_live",
                    f"nothing is listening on {sock} — the desk session has "
                    f"exited; start it again and re-register",
                )
        return {"name": name, **entry}
