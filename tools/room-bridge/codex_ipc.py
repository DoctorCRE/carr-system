#!/usr/bin/env python3
"""The wire into a thread Codex Desktop holds open: its own IPC router.

WHY THIS EXISTS. Found live 2026-09-27, when the orchestrator seat moved to a
Codex Desktop thread and a room turn addressed to it never arrived. The desk
dispatched it through the exec-resume path (dispatch._to_codex), which opens a SECOND writer on
the thread; Codex refuses that while Desktop is the active writer ("thread ...
already has an active writer"). So a thread that is open in Desktop can only
take a new turn from inside the process that owns it.

HOW DESKTOP ITSELF DOES IT. Codex Desktop runs an IPC router on a unix socket
(~/.codex/ipc/ipc.sock) so its own windows can drive a thread another window
owns. A follower asks the router who owns a conversation
(`thread-owner-discovery`), then sends `thread-follower-start-turn` to that
owner, which starts the turn exactly as if it had been typed there. This file
is a follower that does only those two things. Read from the Desktop build
(app version 26.924) and proven against the live router the same day:
initialize answers with a client id, and owner discovery answered for the
orchestrator's thread.

FRAMING: each message is a 4-byte little-endian length, then UTF-8 JSON.
Every request carries a per-method `version`; the router refuses a mismatch,
so the numbers below are the ones the Desktop build uses. The router may also
ask any connected client whether it can handle someone else's request
(`client-discovery-request`); this client always declines.

WHAT IT DOES NOT DO. It never reads a transcript, never answers approvals,
never changes thread settings, and never waits for the reply: the session
answers in its own window, the same contract as a live Claude desk. Any
failure to reach the router reads as "no owner", so a caller falls back to the
durable route rather than failing the cycle.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
import uuid
from pathlib import Path

DEFAULT_SOCKET = os.environ.get(
    "CARR_CODEX_IPC_SOCKET", str(Path.home() / ".codex" / "ipc" / "ipc.sock"))
CLIENT_TYPE = "carr-room-bridge"
INITIALIZING = "initializing-client"
# Per-method versions from the Desktop build's IPC table. Local requests carry
# no hostId, so the plain table value applies.
VERSIONS = {
    "initialize": 0,
    "thread-owner-discovery": 1,
    "thread-follower-start-turn": 2,
    "thread-follower-steer-turn": 1,
}
MAX_FRAME = 16 * 1024 * 1024


class IpcError(Exception):
    pass


class _Client:
    def __init__(self, path: str, timeout: float):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.buf = b""
        self.client_id = INITIALIZING
        self.deadline = time.monotonic() + timeout

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode()
        self.sock.sendall(struct.pack("<I", len(data)) + data)

    def _recv(self) -> dict:
        while True:
            if len(self.buf) >= 4:
                size = struct.unpack("<I", self.buf[:4])[0]
                if size > MAX_FRAME:
                    raise IpcError("frame too large")
                if len(self.buf) >= 4 + size:
                    raw = self.buf[4:4 + size]
                    self.buf = self.buf[4 + size:]
                    return json.loads(raw)
            if time.monotonic() > self.deadline:
                raise IpcError("timed out")
            chunk = self.sock.recv(65536)
            if not chunk:
                raise IpcError("router closed the connection")
            self.buf += chunk

    def request(self, method: str, params: dict, *, target: str | None = None) -> dict:
        rid = str(uuid.uuid4())
        msg = {"type": "request", "requestId": rid, "sourceClientId": self.client_id,
               "version": VERSIONS[method], "method": method, "params": params}
        if target is not None:
            msg["targetClientId"] = target
        self._send(msg)
        while True:
            reply = self._recv()
            kind = reply.get("type")
            if kind == "response" and reply.get("requestId") == rid:
                return reply
            if kind == "client-discovery-request":
                self._send({"type": "client-discovery-response",
                            "requestId": reply.get("requestId"),
                            "response": {"canHandle": False}})
            # broadcasts and anything else addressed to other clients: ignore

    def initialize(self) -> None:
        reply = self.request("initialize", {"clientType": CLIENT_TYPE})
        cid = (reply.get("result") or {}).get("clientId")
        if reply.get("resultType") != "success" or not isinstance(cid, str):
            raise IpcError("initialize refused")
        self.client_id = cid

    def owner(self, thread_id: str) -> str | None:
        reply = self.request("thread-owner-discovery",
                             {"hostId": "local", "conversationId": thread_id})
        if reply.get("resultType") != "success":
            return None
        owner = reply.get("handledByClientId")
        return owner if isinstance(owner, str) and owner else None


def _open(socket_path: str | None, timeout: float) -> _Client | None:
    path = socket_path or DEFAULT_SOCKET
    try:
        client = _Client(path, timeout)
    except OSError:
        return None
    try:
        client.initialize()
    except (OSError, IpcError, ValueError):
        client.close()
        return None
    return client


def thread_owner(thread_id: str, *, socket_path: str | None = None,
                 timeout: float = 3.0) -> str | None:
    """The Desktop client id that holds `thread_id` open, or None.

    None covers every way of not knowing: Desktop not running, router
    unreachable, thread not open. That is the liveness answer a caller wants.
    """
    client = _open(socket_path, timeout)
    if client is None:
        return None
    try:
        return client.owner(thread_id)
    except (OSError, IpcError, ValueError):
        return None
    finally:
        client.close()


def _text_input(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "text_elements": []}]


def start_turn(thread_id: str, text: str, *, socket_path: str | None = None,
               timeout: float = 15.0, approval_policy: str | None = None) -> dict:
    """Start one turn carrying `text` in the Desktop window that owns the thread.

    Returns {"status": "delivered"|"not_live"|"failed", "thread_id", "mode",
    "detail"}. When the owner refuses a new turn because one is already running,
    the text is steered into the running turn instead, which is what a person
    typing into that window mid-turn gets. Desk calls requiring never refuse
    instead: steering cannot override the active turn's approval policy.
    """
    # Ordinary session messages preserve the Desktop owner's posture. Desk
    # dispatch explicitly supplies never and its already-instructed prompt.
    if approval_policy not in (None, "never"):
        raise ValueError("desk approval policy must be never; ask the orchestrator")
    base = {"thread_id": thread_id}
    client = _open(socket_path, timeout)
    if client is None:
        return {**base, "status": "not_live", "detail": "Codex Desktop IPC router unreachable"}
    try:
        owner = client.owner(thread_id)
        if owner is None:
            return {**base, "status": "not_live", "detail": "no Codex Desktop window holds this thread"}
        started = client.request("thread-follower-start-turn", {
            "conversationId": thread_id,
            "turnStart": {"request": {"threadId": thread_id, "input": _text_input(text),
                                      **({"approvalPolicy": "never"} if approval_policy else {})},
                          "context": {}},
        }, target=owner)
        if started.get("resultType") == "success":
            return {**base, "status": "delivered", "mode": "start",
                    "detail": "turn started in the Codex Desktop window that owns the thread"}
        start_error = str(started.get("error") or "start refused")
        if approval_policy == "never":
            return {**base, "status": "failed",
                    "detail": "desk turn refused; cannot bind never by steering an existing turn. "
                              "Permission needs go to the orchestrator. " + start_error[:200]}
        steered = client.request("thread-follower-steer-turn", {
            "conversationId": thread_id, "input": _text_input(text),
        }, target=owner)
        if steered.get("resultType") == "success":
            return {**base, "status": "delivered", "mode": "steer",
                    "detail": "steered into the turn already running in Codex Desktop"}
        return {**base, "status": "failed",
                "detail": f"start: {start_error}; steer: {steered.get('error') or 'refused'}"[:500]}
    except (OSError, IpcError, ValueError) as exc:
        return {**base, "status": "failed", "detail": f"IPC error: {exc}"[:500]}
    finally:
        client.close()
