#!/usr/bin/env python3
"""The wire into a live Codex session: WebSocket over a unix socket.

CODEX WORKED THIS OUT, NOT ME. Dispatched through this very bridge, it found
what three of my own attempts had missed: the app-server's unix transport is
not newline-delimited JSON-RPC and not LSP Content-Length framing. It is a
WebSocket — an HTTP Upgrade to /rpc, then masked text frames. That is why
every plain client got its connection closed on the spot, and why
`codex app-server proxy` looked silent: the proxy forwards WebSocket bytes, it
does not translate anything into them.

The Wire class below is Codex's, kept as it wrote it. Rewriting a working
protocol client to put it in my own words would add nothing and risk a bug in
the one part of this package I did not derive.

THE CALL ORDER MATTERS and is not guessable from the schema alone:

    initialize   -> wait for its response
    initialized  (a notification, no id, no reply)
    thread/start -> returns thread.id       (thread/resume to carry context)
    turn/start   -> returns turn.id
    then read until item/completed carries an item of type agentMessage

Codex proved the message shapes over stdio, where its sandbox let it reach the
protocol but not the network. The unix-socket half was proven here, from a
seat allowed to bind: a nonce that existed nowhere but inside the dispatched
turn came back verbatim in the completed agentMessage.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import uuid


class Wire:
    def __init__(self, path: str, timeout: float = 180.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.buf = bytearray()

    def _read_exact(self, size: int) -> bytes:
        while len(self.buf) < size:
            chunk = self.sock.recv(max(4096, size - len(self.buf)))
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
            chunk = self.sock.recv(4096)
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


def wait_response(wire: Wire, request_id: str, transcript: list[dict]) -> dict:
    while True:
        message = wire.receive_json()
        transcript.append(message)
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"request {request_id} failed: {message['error']}")
            return message["result"]


# ---------------------------------------------------------------------------
# one task, one live Codex thread
# ---------------------------------------------------------------------------


def run_turn(
    sock_path: str,
    task: str,
    thread_id: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    sandbox: str = "workspace-write",
    approval_policy: str = "never",
    timeout: float = 300.0,
) -> dict:
    """Deliver one turn to a live Codex session and wait for its answer.

    Unlike the Claude desk, which accepts a turn and answers in its own window,
    this returns the actual answer: the app-server hands back item/completed on
    the same connection, so there is nothing asynchronous to reconcile.
    """
    from desks import DeskError, desk_prompt
    if approval_policy != "never":
        raise DeskError("unsafe_approval_policy",
                        "dispatched Codex desks require never; permission needs go to the orchestrator")
    task = desk_prompt(task)
    wire = Wire(sock_path, timeout=timeout)
    transcript: list[dict] = []
    wire.upgrade()

    wire.send_json({
        "id": "initialize",
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "hermes-dispatch", "version": "0.1.0"},
            "capabilities": {"experimentalApi": False},
        },
    })
    wait_response(wire, "initialize", transcript)
    wire.send_json({"method": "initialized"})

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

    turn_params = {"threadId": tid, "input": [{"type": "text", "text": task}],
                   "approvalPolicy": "never"}
    if model:
        turn_params["model"] = model
    wire.send_json({"id": "turn-start", "method": "turn/start", "params": turn_params})
    wait_response(wire, "turn-start", transcript)

    deadline = time.monotonic() + timeout
    answer = None
    while time.monotonic() < deadline:
        msg = wire.receive_json()
        transcript.append(msg)
        if msg.get("method") == "item/completed":
            item = (msg.get("params") or {}).get("item") or {}
            if item.get("type") == "agentMessage":
                answer = item.get("text") or item.get("message")
                break
        if msg.get("method") in ("turn/failed", "turn/aborted"):
            return {"status": "failed", "thread_id": tid,
                    "detail": json.dumps(msg.get("params") or {})[:500]}

    if answer is None:
        return {"status": "timed_out", "thread_id": tid,
                "detail": f"no agentMessage within {timeout:.0f}s"}
    return {"status": "completed", "thread_id": tid, "result": answer.strip(),
            "resumed": bool(thread_id)}
