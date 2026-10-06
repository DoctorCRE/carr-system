#!/usr/bin/env python3
"""Contract tests for codex_ipc.py — the wire into a thread Codex Desktop holds open.

WHY THIS EXISTS, found live 2026-09-27: the orchestrator seat moved to a Codex
Desktop thread and a room turn addressed to it failed with "thread ... already
has an active writer". The exec-resume path opens a second writer on the thread,
which Codex refuses while the Desktop app holds it. The Desktop app routes a new
turn on a thread it owns through its own IPC router (~/.codex/ipc/ipc.sock):
initialize, then thread-owner-discovery, then thread-follower-start-turn.

These tests run a fake router on a temporary unix socket that speaks the same
framing (4-byte little-endian length + UTF-8 JSON) and checks every frame the
client sends, so a wrong method name, version, or turn shape fails here instead
of in the live orchestrator thread.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import codex_ipc  # noqa: E402

THREAD = "01a0e2da-dabe-7642-a06a-1a686394537b"


def _frame(obj: dict) -> bytes:
    data = json.dumps(obj).encode()
    return struct.pack("<I", len(data)) + data


class FakeRouter:
    """One-connection fake of the Codex Desktop IPC router."""

    def __init__(self, *, owner: bool = True, start_error: str | None = None,
                 steer_ok: bool = True, chatter: bool = True):
        self.dir = tempfile.mkdtemp(prefix="ipc-")
        self.path = os.path.join(self.dir, "ipc.sock")
        self.owner = owner
        self.start_error = start_error
        self.steer_ok = steer_ok
        self.chatter = chatter
        self.seen: list[dict] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        conn, _ = self.srv.accept()
        buf = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while len(buf) >= 4:
                    n = struct.unpack("<I", buf[:4])[0]
                    if len(buf) < 4 + n:
                        break
                    msg = json.loads(buf[4:4 + n])
                    buf = buf[4 + n:]
                    self.seen.append(msg)
                    self._answer(conn, msg)
        finally:
            conn.close()

    def _answer(self, conn, msg: dict) -> None:
        if msg.get("type") == "client-discovery-response":
            return
        rid = msg.get("requestId")
        method = msg.get("method")
        if self.chatter:
            # the real router interleaves broadcasts and discovery requests
            conn.sendall(_frame({"type": "broadcast", "method": "thread-stream-state-changed",
                                 "params": {}}))
            conn.sendall(_frame({"type": "client-discovery-request", "requestId": "d-1",
                                 "request": {"type": "request", "method": "x", "params": {}}}))
        if method == "initialize":
            conn.sendall(_frame({"type": "response", "requestId": rid, "resultType": "success",
                                 "method": method, "result": {"clientId": "client-abc"}}))
        elif method == "thread-owner-discovery":
            if self.owner:
                conn.sendall(_frame({"type": "response", "requestId": rid, "resultType": "success",
                                     "method": method, "handledByClientId": "owner-1",
                                     "result": {"supportsUntrustedAppInput": True}}))
            else:
                conn.sendall(_frame({"type": "response", "requestId": rid, "resultType": "error",
                                     "method": method, "error": "no-client-found"}))
        elif method == "thread-follower-start-turn":
            if self.start_error:
                conn.sendall(_frame({"type": "response", "requestId": rid, "resultType": "error",
                                     "method": method, "error": self.start_error}))
            else:
                conn.sendall(_frame({"type": "response", "requestId": rid, "resultType": "success",
                                     "method": method, "handledByClientId": "owner-1",
                                     "result": {"result": {"turn": {"id": "turn-9"}}}}))
        elif method == "thread-follower-steer-turn":
            kind = "success" if self.steer_ok else "error"
            conn.sendall(_frame({"type": "response", "requestId": rid, "resultType": kind,
                                 "method": method, "error": None if self.steer_ok else "nope",
                                 "result": {"result": {"turnId": "turn-8"}}}))


def _requests(router: FakeRouter) -> list[dict]:
    return [m for m in router.seen if m.get("type") == "request"]


def test_owner_found_for_open_thread():
    r = FakeRouter(owner=True)
    assert codex_ipc.thread_owner(THREAD, socket_path=r.path) == "owner-1"
    reqs = _requests(r)
    assert [m["method"] for m in reqs] == ["initialize", "thread-owner-discovery"]
    assert reqs[0]["version"] == 0 and reqs[0]["sourceClientId"] == "initializing-client"
    assert reqs[0]["params"]["clientType"]
    assert reqs[1]["version"] == 1 and reqs[1]["sourceClientId"] == "client-abc"
    assert reqs[1]["params"] == {"hostId": "local", "conversationId": THREAD}


def test_no_owner_means_none():
    r = FakeRouter(owner=False)
    assert codex_ipc.thread_owner(THREAD, socket_path=r.path) is None


def test_missing_socket_means_none_not_raise():
    assert codex_ipc.thread_owner(THREAD, socket_path="/nonexistent/ipc.sock") is None


def test_discovery_requests_are_declined():
    r = FakeRouter(owner=True)
    codex_ipc.thread_owner(THREAD, socket_path=r.path)
    declines = [m for m in r.seen if m.get("type") == "client-discovery-response"]
    assert declines and all(m["response"] == {"canHandle": False} for m in declines)


def test_start_turn_sends_follower_request_with_turn_shape():
    r = FakeRouter(owner=True)
    out = codex_ipc.start_turn(THREAD, "hello sol", socket_path=r.path)
    assert out["status"] == "delivered", out
    assert out["thread_id"] == THREAD
    start = [m for m in _requests(r) if m["method"] == "thread-follower-start-turn"][0]
    assert start["version"] == 2
    assert start["targetClientId"] == "owner-1"
    turn = start["params"]["turnStart"]["request"]
    assert start["params"]["conversationId"] == THREAD
    assert turn["threadId"] == THREAD
    assert "approvalPolicy" not in turn
    assert turn["input"] == [{"type": "text", "text": "hello sol", "text_elements": []}]


def test_start_turn_without_owner_is_not_live():
    r = FakeRouter(owner=False)
    out = codex_ipc.start_turn(THREAD, "hello", socket_path=r.path)
    assert out["status"] == "not_live"
    assert not [m for m in _requests(r) if m["method"] == "thread-follower-start-turn"]


def test_busy_thread_falls_back_to_steer():
    r = FakeRouter(owner=True, start_error="turn already in progress")
    out = codex_ipc.start_turn(THREAD, "hello", socket_path=r.path)
    assert out["status"] == "delivered" and out["mode"] == "steer", out
    steer = [m for m in _requests(r) if m["method"] == "thread-follower-steer-turn"][0]
    assert steer["params"]["conversationId"] == THREAD
    assert steer["params"]["input"] == [{"type": "text", "text": "hello", "text_elements": []}]


def test_both_refused_is_failed_with_detail():
    r = FakeRouter(owner=True, start_error="bad", steer_ok=False)
    out = codex_ipc.start_turn(THREAD, "hello", socket_path=r.path)
    assert out["status"] == "failed" and "bad" in out["detail"]


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc!r}", file=sys.stderr)
    if failures:
        print(f"{failures} codex_ipc test(s) failed", file=sys.stderr)
        return 1
    print("all codex_ipc unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
