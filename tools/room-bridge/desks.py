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
"codex should be able to do the same thing as you. It has its own context. I
use codex equally as I do Claude code." An earlier draft of this file treated
Codex as a one-shot that started empty every time, which quietly made it the
lesser seat — fine for a mechanical job, useless for anything with a thread
running through it. It is not a lesser seat. A codex-session desk remembers
its thread id and resumes that thread on every later task, so a Codex desk
picks up where it left off exactly as a Claude desk does.

The kinds differ only in HOW a turn reaches them: a Claude desk is a live
process listening on a socket, so a turn is delivered to something already
running. A Codex thread is durable rather than live — it is stored, resumed
per task, and needs no process sitting idle between turns.
"""

from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

# a desk name a human can say out loud and a shell will not mangle
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
# /tmp/cc-socks/79534.sock — a process, not a desk
PID_SOCKET = re.compile(r"^\d+\.sock$")

KINDS = ("claude-session", "claude-desktop", "codex-session", "codex-live", "flash-local", "grok-cli")
# the old name for the Codex kind, before it carried a thread
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
        # Queue identity/provenance occupy fixed header positions. Decorate
        # the body, after the header separator, so Flash still reads them.
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
    if effort not in EFFORT_CHOICES:
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


class Registry:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY):
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
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
        effort = _normalize_effort(kind, effort)

        # dict[str, object]: a desk entry's values are a genuine mix (str,
        # None, list[str]) depending on kind — a bare literal makes mypy infer
        # the narrower type of whichever entry it sees first in each branch
        # and then flag every other shape as incompatible.
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
                # Background work cannot stop on a terminal approval dialog.
                # dontAsk denies unapproved actions instead of widening the
                # session's authority or leaving a hidden prompt waiting.
                "permission_mode": dispatched_permission_mode(permission_mode),
            }
        elif kind == "grok-cli":
            if model != "grok-4.7" or effort != "high" or sandbox != "read-only":
                raise DeskError("bad_grok_posture", "Grok requires grok-4.7/high/read-only")
            entry = {"kind": kind, "model": model, "effort": effort,
                     "sandbox": sandbox, "cwd": str(cwd or Path.cwd())}
        elif kind == "flash-local":
            # The model and effort are fixed by the direct protocol (ops/config/model-routes.v1.json): Flash,
            # thinking off. Recorded on the entry so a dispatch still names both, as every delegation must.
            entry = {"kind": kind, "model": "flash", "effort": "minimal"}
        elif kind == "codex-live":
            if not socket:
                raise DeskError("missing_socket", "a codex-live desk needs --socket")
            refuse_pid_socket(socket)
            entry = {"kind": kind, "socket": str(socket), "thread_id": None,
                     "cwd": str(cwd or Path.cwd())}
            entry["effort"] = effort
            if model:
                entry["model"] = model
        else:
            if not model:
                raise DeskError("missing_model", "a codex-session desk needs --model")
            # thread_id is filled in by the first dispatch and reused after
            entry = {"kind": kind, "model": model, "cwd": str(cwd or Path.cwd()),
                     "thread_id": None, "effort": effort}
            # A seat that cannot bind a socket or write where the work lives
            # reports its own cage as a fact about the machine. Carrying the
            # posture on the desk is how a task that genuinely needs more room
            # gets it WITHOUT every other desk being loosened to match.
            if sandbox:
                entry["sandbox"] = sandbox
            if add_dirs:
                entry["add_dirs"] = [str(x) for x in add_dirs]

        entry["registered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = self._load()
        data.setdefault("desks", {})[name] = entry
        self._save(data)
        return entry

    def forget(self, name: str) -> None:
        data = self._load()
        if data.get("desks", {}).pop(name, None) is not None:
            self._save(data)

    def remember_thread(self, name: str, thread_id: str) -> None:
        """Pin a Codex desk to the thread it just spoke in.

        Without this a Codex desk would start empty every task, which is the
        behaviour Joe rejected: it has its own context and is used as an equal
        seat, not as a shot.
        """
        data = self._load()
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
        if kind in ("claude-session", "claude-desktop"):
            # Recheck edited/legacy entries at the dispatch boundary.
            entry["permission_mode"] = dispatched_permission_mode(entry.get("permission_mode"))
        if kind == "grok-cli" and (entry.get("model") != "grok-4.7"
                or entry.get("effort") != "high" or entry.get("sandbox") != "read-only"):
            raise DeskError("bad_grok_posture", "Grok requires grok-4.7/high/read-only")
        if kind in ("claude-session", "codex-live"):
            sock = entry.get("socket", "")
            # second refusal: the file is editable, the guard is not
            refuse_pid_socket(sock)
            if not is_live(sock):
                raise DeskError(
                    "desk_not_live",
                    f"nothing is listening on {sock} — the desk session has "
                    f"exited; start it again and re-register",
                )
        return {"name": name, **entry}
