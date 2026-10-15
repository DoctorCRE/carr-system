#!/usr/bin/env python3
"""The wire into a live Codex session: WebSocket over a unix socket.

THE CALL ORDER MATTERS and is not guessable from the schema alone:

    initialize   -> wait for its response
    initialized  (a notification, no id, no reply)
    thread/start -> returns thread.id       (thread/resume to carry context)
    turn/start   -> returns turn.id
    then read until turn/completed confirms that exact turn has terminated

"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import selectors
import subprocess
import time
import uuid

TERMINAL = {'completed', 'failed', 'interrupted'}
CANCEL_TIMEOUT_S = 2.0


class Wire:
    def __init__(self, path: str, timeout: float = 180.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.timeout = timeout
        self.deadline: float | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.turn_requested = False
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.buf = bytearray()

    def _recv(self, size: int) -> bytes:
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("whole-call deadline passed")
            self.sock.settimeout(min(self.timeout, remaining))
        return self.sock.recv(size)

    def _read_exact(self, size: int) -> bytes:
        while len(self.buf) < size:
            chunk = self._recv(max(4096, size - len(self.buf)))
            if not chunk:
                raise EOFError(f"socket closed with {size - len(self.buf)} bytes outstanding")
            self.buf.extend(chunk)
        out = bytes(self.buf[:size])
        del self.buf[:size]
        return out

    def upgrade(self) -> str:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /rpc HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        marker = b"\r\n\r\n"
        while marker not in self.buf:
            chunk = self._recv(4096)
            if not chunk:
                raise EOFError("socket closed during WebSocket upgrade")
            self.buf.extend(chunk)
        end = self.buf.index(marker) + len(marker)
        raw = bytes(self.buf[:end])
        del self.buf[:end]
        text = raw.decode("latin-1")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode("ascii")
        if not text.startswith("HTTP/1.1 101") or f"Sec-WebSocket-Accept: {expected}".lower() not in text.lower():
            raise RuntimeError(f"WebSocket upgrade rejected: {text!r}")
        return text

    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        size = len(payload)
        header = bytearray([0x80 | opcode])
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", size))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", size))
        header.extend(mask)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_json(self, value: dict) -> None:
        self.send_frame(0x1, json.dumps(value, separators=(",", ":")).encode())

    def receive_frame(self) -> tuple[int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", self._read_exact(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(size)
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        if not fin:
            raise RuntimeError("fragmented WebSocket frame is not supported by this proof client")
        if opcode == 0x9:
            self.send_frame(0xA, payload)
            return self.receive_frame()
        if opcode == 0x8:
            raise EOFError(f"server closed WebSocket: {payload!r}")
        return opcode, payload

    def receive_json(self) -> dict:
        while True:
            opcode, payload = self.receive_frame()
            if opcode == 0x1:
                return json.loads(payload)


class _StdioWire:
    def __init__(self, timeout: float):
        self.proc = subprocess.Popen(['codex', 'app-server'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.input = self.proc.stdin
        self.output = self.proc.stdout
        self.deadline = time.monotonic() + timeout
        self.buf = bytearray()
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.output, selectors.EVENT_READ)

    def upgrade(self) -> None:
        pass

    def send_json(self, value: dict) -> None:
        self.input.write((json.dumps(value) + '\n').encode())
        self.input.flush()

    def receive_json(self) -> dict:
        while b'\n' not in self.buf:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError('thread history read timed out')
            chunk = os.read(self.output.fileno(), 65536)
            if not chunk:
                raise EOFError('app-server exited during thread history read')
            self.buf.extend(chunk)
        line, _, remainder = self.buf.partition(b'\n')
        self.buf = remainder
        return json.loads(line)

    def close(self) -> None:
        self.selector.close()
        self.input.close()
        self.output.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)


class RequestRejected(RuntimeError):
    """A correlated JSON-RPC error proves that this request was rejected."""


def wait_response(wire: Wire | _StdioWire, request_id: str, transcript: list[dict]) -> dict:
    while True:
        message = wire.receive_json()
        transcript.append(message)
        if message.get("id") == request_id:
            if "error" in message:
                error = message['error']
                if (isinstance(error, dict) and type(error.get('code')) is int
                        and isinstance(error.get('message'), str) and 'result' not in message):
                    raise RequestRejected(f"request {request_id} failed: {error}")
                raise RuntimeError(f"request {request_id} has an invalid error response")
            return message["result"]


def _initialize(wire: Wire | _StdioWire) -> None:
    wire.upgrade()
    wire.send_json({'id': 'initialize', 'method': 'initialize', 'params': {
        'clientInfo': {'name': 'hermes-dispatch', 'version': '0.1.0'},
        'capabilities': {'experimentalApi': False}}})
    wait_response(wire, 'initialize', [])
    wire.send_json({'method': 'initialized'})


def desktop_turn_terminated(thread_id: str, marker: str, timeout: float = 5.0) -> bool:
    """A terminal turn containing this job's unique user-input marker is proof.

    A different completed turn, an absent marker or unreadable history retains
    the claim. This reader sends no thread/resume or turn/start request.
    """
    wire = None
    try:
        wire = _StdioWire(timeout)
        _initialize(wire)
        wire.send_json({'id': 'thread-read', 'method': 'thread/read',
                        'params': {'threadId': thread_id, 'includeTurns': True}})
        thread = wait_response(wire, 'thread-read', []).get('thread') or {}
        return bool(marker) and thread.get('id') == thread_id and any(
            turn.get('status') in TERMINAL and any(
                item.get('type') == 'userMessage' and any(
                    content.get('type') == 'text' and marker in content.get('text', '')
                    for content in item.get('content', []))
                for item in turn.get('items', []))
            for turn in thread.get('turns', []))
    except (OSError, EOFError, RuntimeError, ValueError, KeyError, TypeError):
        return False
    finally:
        if wire is not None:
            try:
                wire.close()
            except (OSError, subprocess.TimeoutExpired):
                pass


def _terminal(message: dict, thread_id: str, turn_id: str | None) -> bool:
    params = message.get('params') or {}
    turn = params.get('turn') or {}
    return (bool(turn_id) and message.get('method') == 'turn/completed' and params.get('threadId') == thread_id
            and turn.get('id') == turn_id and turn.get('status') in TERMINAL)


def turn_terminated(sock_path: str, thread_id: str, turn_id: str,
                    timeout: float = 2.0) -> bool:
    """Read the exact turn from its owning server; absence is never completion."""
    wire = None
    try:
        wire = Wire(sock_path, timeout=timeout)
        wire.deadline = time.monotonic() + timeout
        _initialize(wire)
        wire.send_json({'id': 'thread-read', 'method': 'thread/read',
                        'params': {'threadId': thread_id, 'includeTurns': True}})
        thread = wait_response(wire, 'thread-read', []).get('thread') or {}
        return thread.get('id') == thread_id and any(
            turn.get('id') == turn_id and turn.get('status') in TERMINAL
            for turn in thread.get('turns', []))
    except (OSError, EOFError, RuntimeError, ValueError, KeyError, TypeError):
        return False
    finally:
        if wire is not None:
            wire.sock.close()


def _interrupt(wire: Wire) -> bool:
    if not wire.turn_requested:
        return True
    if not wire.thread_id or not wire.turn_id:
        return False
    wire.deadline = time.monotonic() + CANCEL_TIMEOUT_S
    try:
        wire.send_json({'id': 'turn-interrupt', 'method': 'turn/interrupt',
                        'params': {'threadId': wire.thread_id, 'turnId': wire.turn_id}})
        while True:
            if _terminal(wire.receive_json(), wire.thread_id, wire.turn_id):
                return True
    except (OSError, EOFError, RuntimeError, ValueError):
        return False




def run_turn(
    sock_path: str,
    task: str,
    thread_id: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    sandbox: str = "workspace-write",
    approval_policy: str = "never",
    timeout: float = 300.0,
    deadline_s: float | None = None,
    effort: str | None = None,
    network_access: bool = False,
    on_executor=None,
) -> dict:
    """Deliver one turn to a live Codex session and wait for its answer.

    Unlike the Claude desk, which accepts a turn and answers in its own window,
    this waits for turn/completed as well as the answer. A deadline interrupts
    the turn; an unconfirmed interruption retains write ownership.
    """
    from desks import DeskError, desk_prompt
    if approval_policy != "never":
        raise DeskError("unsafe_approval_policy",
                        "dispatched Codex desks require never; permission needs go to the orchestrator")
    if type(network_access) is not bool:
        raise DeskError("bad_network_access", "network_access must be an explicit boolean")
    if network_access and thread_id is not None:
        raise DeskError("network_access_requires_fresh_thread",
                        "network access is permitted only on a fresh thread")
    task = desk_prompt(task)
    started = time.monotonic()
    if on_executor:
        on_executor({'kind': 'no_launch', 'reason': 'setup_pending'})
    wire = Wire(sock_path, timeout=timeout)
    if deadline_s is not None:
        wire.deadline = started + deadline_s
    try:
        return _run_turn(wire, task, thread_id=thread_id, cwd=cwd, model=model,
                         sandbox=sandbox, approval_policy=approval_policy, timeout=timeout, effort=effort,
                         network_access=network_access,
                         on_executor=on_executor)
    except TimeoutError:
        confirmed = _interrupt(wire)
        return {"status": "timed_out", "thread_id": getattr(wire, "thread_id", None) or thread_id,
                'turn_id': wire.turn_id, 'termination_confirmed': confirmed,
                "detail": f"no answer within {deadline_s if deadline_s is not None else timeout:.0f}s"
                          + ('; turn terminated' if confirmed else '; stuck: turn termination unconfirmed')}
    finally:
        wire.sock.close()


def _run_turn(wire: Wire, task: str, *, thread_id, cwd, model, sandbox,
              approval_policy, timeout, effort=None, network_access=False, on_executor=None) -> dict:
    transcript: list[dict] = []
    _initialize(wire)

    if thread_id:
        wire.send_json({
            "id": "thread-open",
            "method": "thread/resume",
            "params": {"threadId": thread_id, "approvalPolicy": "never"},
        })
    else:
        params = {"approvalPolicy": approval_policy, "sandbox": sandbox}
        if cwd:
            params["cwd"] = cwd
        if model:
            params["model"] = model
        wire.send_json({"id": "thread-open", "method": "thread/start", "params": params})
    opened = wait_response(wire, "thread-open", transcript)
    tid = (opened.get("thread") or {}).get("id") or thread_id
    wire.thread_id = tid
    if on_executor:
        on_executor({'kind': 'codex_turn', 'thread_id': tid, 'turn_id': None})

    turn_params = {"threadId": tid, "input": [{"type": "text", "text": task}],
                   "approvalPolicy": "never"}
    if model:
        turn_params["model"] = model
    if effort:
        turn_params["effort"] = effort
    if network_access:
        turn_params["sandboxPolicy"] = {"type": "readOnly", "networkAccess": True}
    wire.turn_requested = True
    wire.send_json({"id": "turn-start", "method": "turn/start", "params": turn_params})
    try:
        started_turn = wait_response(wire, "turn-start", transcript)
    except RequestRejected:
        if on_executor:
            on_executor({'kind': 'no_launch', 'thread_id': tid, 'turn_id': None,
                         'reason': 'start_rejected'})
        raise
    wire.turn_id = (started_turn.get('turn') or {}).get('id')
    if on_executor:
        on_executor({'kind': 'codex_turn', 'thread_id': tid, 'turn_id': wire.turn_id})

    deadline = time.monotonic() + timeout
    if getattr(wire, "deadline", None) is not None:
        deadline = min(deadline, wire.deadline)
    wire.deadline = deadline
    answer = None
    queued = iter(transcript)
    while time.monotonic() < deadline:
        msg = next(queued, None)
        if msg is None:
            msg = wire.receive_json()
        params = msg.get('params') or {}
        if (msg.get("method") == "item/completed" and params.get('threadId') == tid
                and params.get('turnId') == wire.turn_id):
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                answer = item.get("text") or item.get("message")
        if _terminal(msg, tid, wire.turn_id):
            turn = msg['params']['turn']
            return {'status': 'completed' if turn['status'] == 'completed' else 'failed',
                    'thread_id': tid, 'turn_id': wire.turn_id, 'termination_confirmed': True,
                    'result': (answer or '').strip(), 'resumed': bool(thread_id)}
    raise TimeoutError('turn did not terminate before deadline')
