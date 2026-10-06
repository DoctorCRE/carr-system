#!/usr/bin/env python3
"""Backend-only Claude background-session and Desktop handoff wire.

The Model Room bridge does not drive the Claude Desktop UI.  It starts a
durable Claude Code background session with a caller-supplied UUID, observes
that session through ``claude agents --json --all``, reads the completed
assistant result from Claude Code's own persisted transcript, and finally
uses Claude Code's supported ``/desktop`` command from an attached PTY.  The
PTY is load-bearing: slash commands are interactive client commands, not
prompts that should be sent to a model with ``-p``.

No poll creates a session.  ``launch_background`` is called only after Hermes
has atomically claimed an explicitly addressed queue card; later poll cycles
only inspect the UUID already persisted in room-bridge state.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import time
import uuid
from pathlib import Path

from desks import DeskError, desk_prompt, dispatched_permission_mode


UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SHORT_ID = re.compile(r"\bbackgrounded\s*[·:]?\s*([0-9a-f]{8})\b", re.IGNORECASE)
ANSI_ESCAPE = re.compile(
    rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
)
ACTIVE_STATES = {"starting", "running", "working", "idle"}
# Current Claude Code reports a finished ``--bg`` agent as ``state: done``.
# Keep ``completed`` for compatibility with older supervisor payloads.
COMPLETED_STATES = {"completed", "done"}
FAILED_STATES = {"failed", "stopped", "killed"}
NEEDS_INPUT_STATES = {"needs_input", "needs-input", "blocked"}


class ClaudeDesktopError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _session_id(value: str | None = None) -> str:
    candidate = value or str(uuid.uuid4())
    if not UUID.fullmatch(candidate):
        raise ClaudeDesktopError("invalid_session_id", "Claude session id must be a UUID")
    return candidate.lower()


def launch_background(
    entry: dict,
    task: str,
    *,
    request_id: str | None = None,
    run=subprocess.run,
    claude_bin: str = "claude",
    timeout_s: float = 30.0,
) -> dict:
    """Start one supervisor-hosted Claude session and return its durable UUID."""
    request = _session_id(request_id)
    model = str(entry.get("model") or "").strip()
    effort = str(entry.get("effort") or "").strip()
    cwd = str(entry.get("cwd") or "").strip()
    try:
        permission_mode = dispatched_permission_mode(entry.get("permission_mode"))
    except DeskError as exc:
        raise ClaudeDesktopError(exc.code, str(exc)) from exc
    if not model or not effort or not cwd or not task.strip():
        raise ClaudeDesktopError(
            "invalid_background_contract",
            "Claude background dispatch requires model, effort, cwd, and a non-empty task",
        )
    argv = [
        claude_bin,
        "--bg",
        # Claude's background supervisor owns the session UUID and explicitly
        # ignores --session-id.  The request UUID names this dispatch; the
        # genuine session UUID is resolved from the supervisor immediately
        # after launch and is the only ref persisted by the bridge.
        "--name", f"model-room-{request[:8]}",
        "--model", model,
        "--effort", effort,
        "--permission-mode", permission_mode,
        desk_prompt(task),
    ]
    try:
        proc = run(
            argv,
            cwd=cwd,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ClaudeDesktopError("claude_unavailable", "claude is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeDesktopError("background_launch_timeout", "claude --bg did not return") from exc
    if proc.returncode != 0:
        # Provider output can contain task context.  Keep the bridge receipt a
        # bounded error class and leave diagnostics in Claude's own state.
        raise ClaudeDesktopError(
            "background_launch_failed", f"claude --bg exited {proc.returncode}"
        )
    match = SHORT_ID.search(f"{proc.stdout or ''}\n{proc.stderr or ''}")
    if match is None:
        raise ClaudeDesktopError(
            "background_session_unidentified", "claude --bg returned no background id"
        )
    short_id = match.group(1).lower()
    try:
        listed = run(
            [claude_bin, "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
        rows = json.loads(listed.stdout) if listed.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        rows = []
    row = next(
        (item for item in rows if isinstance(item, dict)
         and (item.get("id") == short_id
              or str(item.get("sessionId") or "").lower().startswith(short_id))),
        None,
    ) if isinstance(rows, list) else None
    sid = str((row or {}).get("sessionId") or "").lower()
    if not UUID.fullmatch(sid):
        # This exact short id came from the launch above. Stop it rather than
        # leave untracked model work running when the durable identity proof
        # cannot be established.
        try:
            run([claude_bin, "stop", short_id], capture_output=True, text=True,
                timeout=timeout_s, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        raise ClaudeDesktopError(
            "background_session_unidentified", "Claude supervisor did not resolve the session UUID"
        )
    return {
        "status": "delivered",
        "session_id": sid,
        "session_short_id": short_id,
        "session_name": f"model-room-{request[:8]}",
        "transport": "claude-desktop",
    }


def inspect_session(
    session_id: str,
    *,
    run=subprocess.run,
    claude_bin: str = "claude",
    timeout_s: float = 15.0,
) -> dict:
    """Return one redacted supervisor fact for a background session UUID."""
    sid = _session_id(session_id)
    try:
        proc = run(
            [claude_bin, "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeDesktopError("background_status_unavailable", "Claude agent status failed") from exc
    if proc.returncode != 0:
        raise ClaudeDesktopError("background_status_unavailable", "Claude agent status refused")
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeDesktopError("background_status_invalid", "Claude agent status was not JSON") from exc
    if not isinstance(rows, list):
        raise ClaudeDesktopError("background_status_invalid", "Claude agent status was not a list")
    matches = [item for item in rows if isinstance(item, dict) and item.get("sessionId") == sid]
    # Once Claude Desktop (or a terminal) opens the session, the supervisor
    # lists that client as a second ``kind: interactive`` row with the same
    # sessionId and no ``state``.  The task's lifecycle is the background row.
    row = next(
        (item for item in matches if str(item.get("kind") or "background") == "background"),
        matches[0] if matches else None,
    )
    if row is None:
        return {"session_id": sid, "state": "unknown", "found": False}
    state = str(row.get("state") or "running").strip().lower()
    return {
        "session_id": sid,
        "state": state,
        "found": True,
        "kind": str(row.get("kind") or "background"),
    }


def _transcript_candidates(session_id: str, root: Path) -> list[Path]:
    sid = _session_id(session_id)
    try:
        return sorted(root.glob(f"**/{sid}.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []


def read_final_text(
    session_id: str,
    *,
    transcript_root: Path | None = None,
    tail_bytes: int = 8 * 1024 * 1024,
) -> str | None:
    """Read the last final assistant text from Claude's own session transcript.

    Only a bounded tail is parsed.  Queue prompts require their typed terminal
    line at the end, so a result that is not in the final eight MiB is not a
    valid current completion signal anyway.
    """
    root = Path(transcript_root or (Path.home() / ".claude" / "projects"))
    candidates = _transcript_candidates(session_id, root)
    if not candidates:
        return None
    path = candidates[0]
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - tail_bytes)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return None
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start:
        lines = lines[1:]
    finals: list[str] = []
    typed: list[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("type") != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        chunks = message.get("content")
        if not isinstance(chunks, list):
            continue
        text = "\n".join(
            str(chunk.get("text"))
            for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("type") == "text"
            and isinstance(chunk.get("text"), str)
        ).strip()
        if not text:
            continue
        if "CARR_QUEUE_RESULT " in text:
            typed.append(text)
        if message.get("stop_reason") == "end_turn":
            finals.append(text)
    return typed[-1] if typed else (finals[-1] if finals else None)


# Cursor-positioning CSI sequences (CUU/CUD/CUF/CUB/CNL/CPL/CHA/CUP/HVP/VPA).
# Claude Code's renderer paints only the cells that changed, so a space it
# can skip is sent as a cursor move, not a space byte.  Captured from the real
# 2.1.281 CLI: ``Checking\x1b[16Gfor Claude Desktop…``.  Stripping that escape
# glues the words together; treating it as a gap keeps them readable.
CURSOR_MOVE = re.compile(rb"\x1b\[[0-9;]*[ABCDEFGHfd]")
# Charset designation (ESC ( B) and cursor save/restore (ESC 7 / ESC 8), which
# ANSI_ESCAPE leaves half-stripped.
TERMINAL_CONTROL = re.compile(rb"\x1b[()*+][0-9A-Za-z]|\x1b[78=>]|[\x00-\x08\x0e-\x1a\x1c-\x1f]")
DESKTOP_PROGRESS = (
    b"checkingforclaudedesktop",
    b"savingsession",
    b"openingclaudedesktop",
    b"openinginclaudedesktop",
    b"sessiontransferredtoclaudedesktop",
)
# The /desktop command's own failure copy (Claude Code 2.1.281).  An error
# state repaints a fresh dialog, so these arrive whole rather than as diffs.
DESKTOP_ERRORS = (
    b"couldn'topenclaudedesktop",
    b"claudedesktopisnotinstalled",
    b"claudedesktopneedstobeupdated",
    b"istooold.updateto",
    b"thedesktopappisrequiredfor/desktop",
    b"downloadnow?(y/n)",
)
# ``claude attach`` refuses a session another client already holds, which is
# exactly the state /desktop produces: Claude Desktop is showing it.
ALREADY_OPEN = b"thissessionisrunninginanotherterminal"


def _screen_text(raw: bytes) -> bytes:
    """Collapse raw PTY bytes to lowercase text with no whitespace.

    Matching whitespace-free text makes detection independent of whether the
    renderer drew a space as a space byte or as a cursor move.
    """
    text = CURSOR_MOVE.sub(b" ", raw)
    text = TERMINAL_CONTROL.sub(b"", text)
    text = ANSI_ESCAPE.sub(b"", text)
    return re.sub(rb"\s+", b"", text.replace(b"\xc2\xa0", b" ")).lower()


def _list_sessions(claude_bin: str, timeout_s: float = 15.0) -> list | None:
    try:
        proc = subprocess.run(
            [claude_bin, "agents", "--json", "--all"],
            capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL,
        )
        rows = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return rows if isinstance(rows, list) else None


def _session_holders(rows: list | None, sid: str) -> dict | None:
    """Summarise who holds ``sid`` according to Claude's own supervisor.

    ``background_pid`` is the live supervisor-hosted process, if any.
    ``interactive`` is true when another client (Claude Desktop's embedded
    Claude Code, or a terminal) has the same session open with a live pid;
    the supervisor lists that client as a separate ``kind: interactive`` row.
    """
    if rows is None:
        return None
    background_pid = None
    interactive = False
    for row in rows:
        if not isinstance(row, dict) or str(row.get("sessionId") or "").lower() != sid:
            continue
        pid = row.get("pid")
        live = isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        if str(row.get("kind") or "background") == "background":
            if live:
                background_pid = pid
        elif live:
            interactive = True
    return {"background_pid": background_pid, "interactive": interactive}


def _stop_client(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def handoff_to_desktop(
    session_id: str,
    *,
    claude_bin: str = "claude",
    timeout_s: float = 30.0,
    ready_timeout_s: float = 8.0,
    list_sessions=None,
    status_poll_s: float = 1.0,
    settle_s: float = 2.0,
) -> dict:
    """Attach to a completed background session and invoke supported /desktop.

    Success is decided by Claude's supervisor, not by scraping the TUI: after
    a successful ``/desktop`` the background process that the attach woke
    exits (the CLI hands the session over and shuts itself down), and on a
    machine where Claude Desktop resumes it at once a live ``interactive``
    row for the same session appears.  Either is a status signal the CLI
    publishes through ``claude agents --json``.  The PTY text is used only to
    fail fast on /desktop's own error copy, and as a fallback progress signal
    when the supervisor listing is unavailable.

    Returns ``{"status": "opened"}`` after a handoff, or
    ``{"status": "already_open"}`` when the session is already held by
    another client such as Claude Desktop (``claude attach`` refuses it with
    "this session is running in another terminal").
    """
    sid = _session_id(session_id)
    lister = list_sessions or (lambda: _list_sessions(claude_bin))
    before = _session_holders(lister(), sid)
    if before is not None and before["interactive"]:
        return {"status": "already_open", "session_id": sid}
    # ``claude agents --json`` exposes both the durable UUID and the eight-char
    # agent id, but ``claude attach`` accepts only the latter.
    attach_id = sid[:8]
    master, slave = pty.openpty()
    try:
        try:
            proc = subprocess.Popen(
                [claude_bin, "attach", attach_id],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
        except OSError as exc:
            raise ClaudeDesktopError("desktop_handoff_unavailable", "could not attach to Claude") from exc
        finally:
            os.close(slave)

        def read_available(buffer: bytearray, wait_s: float) -> bool:
            readable, _, _ = select.select([master], [], [], wait_s)
            if not readable:
                return False
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            if chunk:
                buffer.extend(chunk)
                if len(buffer) > 262144:
                    del buffer[:-262144]
            return bool(chunk)

        # A completed agent is briefly woken before the attached prompt accepts
        # input. Wait for the initial transcript repaint to settle; sending the
        # slash command on the first byte loses it during that wake-up.
        attach_output = bytearray()
        ready_deadline = time.monotonic() + ready_timeout_s
        painted = False
        last_paint_at = 0.0
        while proc.poll() is None and time.monotonic() < ready_deadline:
            if read_available(attach_output, 0.25):
                painted = True
                last_paint_at = time.monotonic()
            elif painted and time.monotonic() - last_paint_at >= 0.75:
                break
        if proc.poll() is not None:
            while read_available(attach_output, 0.1):
                pass
            if ALREADY_OPEN in _screen_text(bytes(attach_output)):
                return {"status": "already_open", "session_id": sid}
            raise ClaudeDesktopError("desktop_attach_failed", "Claude attach exited before handoff")

        woken = _session_holders(lister(), sid)
        woken_pid = (woken or {}).get("background_pid")
        os.write(master, b"/desktop\r")

        deadline = time.monotonic() + timeout_s
        output = bytearray()
        next_status_at = time.monotonic() + status_poll_s
        outcome = None
        progress = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                while read_available(output, 0.1):
                    pass
            else:
                read_available(output, 0.25)
            screen = _screen_text(bytes(output))
            if any(marker in screen for marker in DESKTOP_ERRORS):
                outcome = "error"
                break
            progress = progress or any(marker in screen for marker in DESKTOP_PROGRESS)
            if time.monotonic() >= next_status_at or proc.poll() is not None:
                next_status_at = time.monotonic() + status_poll_s
                holders = _session_holders(lister(), sid)
                if holders is not None and (
                    holders["interactive"]
                    or (woken_pid is not None and holders["background_pid"] != woken_pid)
                ):
                    outcome = "opened"
                    break
                if progress and (holders is None or woken_pid is None):
                    # No supervisor pid to watch: fall back to the CLI's own
                    # progress copy, matched space-insensitively.
                    outcome = "opened"
                    break
            if proc.poll() is not None:
                outcome = "opened" if proc.returncode == 0 else "exited"
                break
        if outcome == "opened":
            # /desktop moves the attached client on (agent view, or a fresh
            # prompt in its cwd) instead of exiting. Give the app launch a
            # moment, then close only this attached client; the durable
            # conversation remains intact and is now held by Claude Desktop.
            time.sleep(settle_s)
            try:
                os.write(master, b"\x03")
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _stop_client(proc)
            return {"status": "opened", "session_id": sid}
        _stop_client(proc)
        if outcome == "error":
            raise ClaudeDesktopError("desktop_handoff_failed", "Claude /desktop reported an error")
        if outcome == "exited":
            raise ClaudeDesktopError(
                "desktop_handoff_failed", f"Claude /desktop exited {proc.returncode}"
            )
        raise ClaudeDesktopError("desktop_handoff_timeout", "Claude /desktop did not complete")
    finally:
        try:
            os.close(master)
        except OSError:
            pass
