#!/usr/bin/env python3
"""the ORDER (initialize, then the
initialized notification, then thread/start or thread/resume, then turn/start),
and that a resumed desk sends thread/resume with the id it was given instead of
starting a fresh thread.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import dispatch  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


class FakeAppServer:

    def __init__(self, path: str, answer: str = "the live seat answered",
                 silent_turn: bool = False, finish_on_interrupt: bool = False,
                 message_only: bool = False, terminal_before_response: bool = False):
        self.path = path
        self.answer = answer
        self.silent_turn = silent_turn
        self.finish_on_interrupt = finish_on_interrupt
        self.message_only = message_only
        self.terminal_before_response = terminal_before_response
        self.seen: list[dict] = []
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        self.srv.settimeout(10)
        threading.Thread(target=self._serve, daemon=True).start()

    @staticmethod
    def _accept_key(key: str) -> str:
        return base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()

    def _send(self, conn, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", n))
        conn.sendall(bytes(header) + payload)   # server frames are unmasked

    def _recv(self, conn) -> dict | None:
        def exact(n):
            buf = b""
            while len(buf) < n:
                c = conn.recv(n - len(buf))
                if not c:
                    return None
                buf += c
            return buf
        head = exact(2)
        if not head:
            return None
        size = head[1] & 0x7F
        masked = bool(head[1] & 0x80)
        if size == 126:
            size = struct.unpack("!H", exact(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", exact(8))[0]
        mask = exact(4) if masked else b""
        payload = exact(size) or b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn) -> None:
        with conn:
            buf = b""
            while b"\r\n\r\n" not in buf:
                c = conn.recv(4096)
                if not c:
                    return
                buf += c
            key = ""
            for line in buf.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {self._accept_key(key)}\r\n\r\n"
            ).encode())

            while True:
                msg = self._recv(conn)
                if msg is None:
                    return
                self.seen.append(msg)
                method = msg.get("method")
                if method == "initialize":
                    self._send(conn, {"id": msg["id"], "result": {"userAgent": "fake"}})
                elif method in ("thread/start", "thread/resume"):
                    tid = (msg.get("params") or {}).get("threadId") or "thread-live-0001"
                    self._send(conn, {"id": msg["id"], "result": {"thread": {"id": tid}}})
                elif method == "turn/start":
                    if self.terminal_before_response:
                        self._send(conn, {'method': 'turn/completed', 'params': {
                            'threadId': msg['params']['threadId'],
                            'turn': {'id': 'turn-1', 'status': 'completed'}}})
                    self._send(conn, {"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
                    if self.silent_turn:
                        continue
                    self._send(conn, {"method": "item/completed", "params": {
                        "threadId": msg["params"]["threadId"], "turnId": "turn-1",
                        "item": {"type": "agentMessage", "text": self.answer}}})
                    if self.message_only:
                        continue
                    self._send(conn, {'method': 'turn/completed', 'params': {
                        'threadId': 'thread-live-0001', 'turn': {'id': 'turn-1', 'status': 'completed'}}})
                elif method == 'turn/interrupt':
                    self._send(conn, {'id': msg['id'], 'result': {}})
                    if self.finish_on_interrupt:
                        self.silent_turn = False
                        self._send(conn, {'method': 'turn/completed', 'params': {
                            'threadId': msg['params']['threadId'],
                            'turn': {'id': msg['params']['turnId'], 'status': 'interrupted'}}})
                elif method == 'thread/read':
                    self._send(conn, {'id': msg['id'], 'result': {'thread': {
                        'id': msg['params']['threadId'], 'turns': [
                            {'id': 'turn-1', 'status': 'inProgress' if self.silent_turn else 'completed'}]}}})

    def methods(self) -> list[str]:
        return [str(m.get("method")) for m in self.seen if m.get("method")]

    def close(self) -> None:
        try:
            self.srv.close()
        except OSError:
            pass


from test_codex_models_unit import catalog_fixture


@catalog_fixture()
def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="codex-live-test-")
    root = Path(tmp.name)
    reg = desks.Registry(root / "desks.json")
    results = root / "results.jsonl"
    sock = str(root / "live.sock")

    def a_turn_reaches_a_live_codex_seat():
        srv = FakeAppServer(sock)
        try:
            reg.register(
                "cx", "codex-live", socket=sock, cwd=str(root), model="gpt-5.1-codex-mini",
                effort="medium",
            )
            row = dispatch.dispatch("cx", "do the thing", registry=reg, results_path=results)
            assert row["status"] == "completed", row
            assert row["result"] == "the live seat answered", row
            assert row["resumed"] is False, row
            assert row["thread_id"] == "thread-live-0001", row
            turn = next(m for m in srv.seen if m.get("method") == "turn/start")
            assert turn["params"]["model"] == "gpt-6.1-sol", turn
            assert turn["params"]["effort"] == "medium", turn
            assert "sandboxPolicy" not in turn["params"], turn
        finally:
            srv.close()

    check("a task reaches a live Codex seat and its answer comes straight back",
          a_turn_reaches_a_live_codex_seat)

    def read_only_network_access_is_opt_in_fresh_and_ephemeral():
        srv = FakeAppServer(sock)
        try:
            reg.register("cx-network", "codex-live", socket=sock, cwd=str(root),
                         model="gpt-5.1-codex-mini", effort="medium", network_access=True)
            try:
                dispatch.dispatch("cx-network", "read the authorized source", registry=reg,
                                  results_path=results)
            except desks.DeskError as exc:
                assert exc.code == "network_access_requires_fresh_thread", exc.code
            else:
                raise AssertionError("network-enabled dispatch without --fresh was accepted")
            assert srv.methods() == [], srv.methods()

            row = dispatch.dispatch("cx-network", "read the authorized source", registry=reg,
                                   results_path=results, fresh=True)
            assert row["status"] == "completed", row
            turn = next(m for m in srv.seen if m.get("method") == "turn/start")
            assert turn["params"]["sandboxPolicy"] == {"type": "readOnly", "networkAccess": True}, turn
            assert srv.methods()[:4] == ["initialize", "initialized", "thread/start", "turn/start"], srv.methods()
            assert reg.entries()["cx-network"].get("thread_id") is None, reg.entries()["cx-network"]
        finally:
            srv.close()

    check("read-only network access is explicit, fresh-only, and never pinned for resume",
          read_only_network_access_is_opt_in_fresh_and_ephemeral)

    def network_access_registry_values_are_strict_and_codex_live_only():
        for value in (1, "true", None):
            try:
                reg.register("bad-network", "codex-live", socket=sock,
                             model="gpt-5.1-codex-mini", effort="medium", network_access=value)
            except desks.DeskError as exc:
                assert exc.code == "bad_network_access", exc.code
            else:
                raise AssertionError(f"non-boolean network_access accepted: {value!r}")
        try:
            reg.register("wrong-kind", "codex-session", model="gpt-5.6-sol", effort="high",
                         network_access=True)
        except desks.DeskError as exc:
            assert exc.code == "bad_network_access", exc.code
        else:
            raise AssertionError("network_access accepted on codex-session")

    check("network access registry opt-in rejects non-booleans and other desk kinds",
          network_access_registry_values_are_strict_and_codex_live_only)

    def wire_refuses_network_on_resume_before_connecting():
        for value, thread_id, expected in (
            (True, "existing-thread", "network_access_requires_fresh_thread"),
            (1, None, "bad_network_access"),
        ):
            try:
                dispatch.codex_wire.run_turn(sock, "read", thread_id=thread_id,
                                             network_access=value)
            except desks.DeskError as exc:
                assert exc.code == expected, exc.code
            else:
                raise AssertionError(f"unsafe wire request accepted: {value!r}, {thread_id!r}")

    check("the wire rejects resume and non-boolean network requests before socket use",
          wire_refuses_network_on_resume_before_connecting)

    def the_call_order_is_the_one_codex_found():
        srv = FakeAppServer(sock)
        try:
            reg.forget("cx2")
            reg.register(
                "cx2", "codex-live", socket=sock, cwd=str(root), model="gpt-5.1-codex-mini",
                effort="medium",
            )
            dispatch.dispatch("cx2", "again", registry=reg, results_path=results)
            got = srv.methods()
            assert got[:4] == ["initialize", "initialized", "thread/start", "turn/start"], got
        finally:
            srv.close()

    check("the wire follows initialize, initialized, thread/start, turn/start",
          the_call_order_is_the_one_codex_found)

    def a_second_task_resumes_the_same_thread():
        srv = FakeAppServer(sock, answer="still here")
        try:
            row = dispatch.dispatch("cx", "and again", registry=reg, results_path=results)
            assert row["resumed"] is True, row
            assert row["thread_id"] == "thread-live-0001", row
            assert "thread/resume" in srv.methods(), srv.methods()
            assert "thread/start" not in srv.methods(), srv.methods()
        finally:
            srv.close()

    check("a second task resumes the same live thread instead of starting one",
          a_second_task_resumes_the_same_thread)

    def a_pid_socket_is_still_refused():
        try:
            reg.register(
                "sneaky", "codex-live", socket="/tmp/cc-socks/4242.sock",
                model="gpt-5.1-codex-mini", effort="medium",
            )
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a pid socket was accepted as a codex-live desk")

    check("the pid-socket guard covers the new kind too", a_pid_socket_is_still_refused)

    def a_dead_app_server_is_refused():
        reg.register(
            "gone", "codex-live", socket=str(root / "nothing.sock"), cwd=str(root),
            model="gpt-5.1-codex-mini", effort="medium",
        )
        try:
            reg.resolve("gone")
        except desks.DeskError as e:
            assert e.code == "desk_not_live", e.code
        else:
            raise AssertionError("a codex-live desk with no server resolved")

    check("a codex-live desk whose app-server is gone is refused",
          a_dead_app_server_is_refused)

    def a_silent_seat_stops_at_the_callers_limit():
        import time as _time
        quiet = str(root / "quiet.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(quiet)
        server.listen(8)
        held: list = []
        stop = threading.Event()

        def accept_and_hold():
            server.settimeout(0.2)
            while not stop.is_set():
                try:
                    conn, _ = server.accept()
                    held.append(conn)
                except OSError:
                    continue

        t = threading.Thread(target=accept_and_hold, daemon=True)
        t.start()
        try:
            reg.register("cx-quiet", "codex-live", socket=quiet, cwd=str(root),
                         model="gpt-5.1-codex-mini", effort="medium")
            began = _time.monotonic()
            row = dispatch.dispatch("cx-quiet", "do the thing", registry=reg,
                                    results_path=results, codex_timeout_s=1)
            took = _time.monotonic() - began
            assert row["status"] == "timed_out", row
            assert took < 10, took
        finally:
            stop.set()
            t.join(timeout=2)
            for conn in held:
                conn.close()
            server.close()

    check("a silent live Codex seat stops at the caller's whole-call limit",
          a_silent_seat_stops_at_the_callers_limit)

    def a_turn_that_times_out_keeps_its_new_thread():
        stalled = str(root / "stalled.sock")
        srv = FakeAppServer(stalled, silent_turn=True)
        try:
            reg.register("cx-stall", "codex-live", socket=stalled, cwd=str(root),
                         model="gpt-5.1-codex-mini", effort="medium")
            row = dispatch.dispatch("cx-stall", "do the thing", registry=reg,
                                    results_path=results, codex_timeout_s=1)
            assert row["status"] == "timed_out", row
            assert row["thread_id"] == "thread-live-0001", row
            assert reg.entries()["cx-stall"].get("thread_id") == "thread-live-0001", reg.entries()["cx-stall"]
        finally:
            srv.close()

    check("a live Codex turn that times out keeps the thread it started",
          a_turn_that_times_out_keeps_its_new_thread)

    def timeout_requires_terminal_confirmation():
        for confirms in (False, True):
            path = str(root / f'cancel-{confirms}.sock')
            srv = FakeAppServer(path, silent_turn=True, finish_on_interrupt=confirms)
            try:
                with patch.object(dispatch.codex_wire, 'CANCEL_TIMEOUT_S', 0.1):
                    row = dispatch.codex_wire.run_turn(path, 'build', deadline_s=0.1)
                assert row['status'] == 'timed_out', row
                assert row['termination_confirmed'] is confirms, row
                request = next(m for m in srv.seen if m.get('method') == 'turn/interrupt')
                assert request['params'] == {'threadId': 'thread-live-0001', 'turnId': 'turn-1'}, request
            finally:
                srv.close()

    check('timeout interrupts the exact turn; acknowledgement alone is not termination',
          timeout_requires_terminal_confirmation)

    def agent_message_does_not_release_a_running_turn():
        path = str(root / 'message.sock')
        srv = FakeAppServer(path, message_only=True)
        try:
            with patch.object(dispatch.codex_wire, 'CANCEL_TIMEOUT_S', 0.1):
                row = dispatch.codex_wire.run_turn(path, 'build', deadline_s=0.1)
            assert row['status'] == 'timed_out', row
            assert not row['termination_confirmed'], row
        finally:
            srv.close()

    check('an agent message is not proof that its turn has stopped',
          agent_message_does_not_release_a_running_turn)

    def terminal_read_requires_the_exact_turn():
        path = str(root / 'read.sock')
        srv = FakeAppServer(path)
        try:
            assert dispatch.codex_wire.turn_terminated(path, 'thread-live-0001', 'turn-1')
            assert not dispatch.codex_wire.turn_terminated(path, 'thread-live-0001', 'old-turn')
            srv.silent_turn = True
            assert not dispatch.codex_wire.turn_terminated(path, 'thread-live-0001', 'turn-1')
        finally:
            srv.close()

    check('thread read reconciles a terminal turn and refuses active or absent turns',
          terminal_read_requires_the_exact_turn)

    def terminal_notification_can_precede_start_response():
        path = str(root / 'early-terminal.sock')
        srv = FakeAppServer(path, silent_turn=True, terminal_before_response=True)
        try:
            with patch.object(dispatch.codex_wire, 'CANCEL_TIMEOUT_S', 0.1):
                row = dispatch.codex_wire.run_turn(path, 'build', deadline_s=0.1)
            assert row['status'] == 'completed', row
            assert row['termination_confirmed'], row
        finally:
            srv.close()

    check('terminal notification queued before the start response still confirms termination',
          terminal_notification_can_precede_start_response)

    def desktop_recovery_reads_only_the_marked_terminal_turn():
        binary = root / 'bin'
        binary.mkdir()
        codex = binary / 'codex'
        trace = root / 'stdio-requests.json'
        marker = 'Room write owner: unique-job'
        user_item = {'type': 'userMessage', 'content': [{'type': 'text', 'text': marker}]}
        cases = [
            ('desktop-thread', 'completed', user_item, True),
            ('desktop-thread', 'inProgress', user_item, False),
            ('desktop-thread', 'completed', {'type': 'userMessage', 'content': [
                {'type': 'text', 'text': 'Room write owner: different-job'}]}, False),
            ('desktop-thread', 'completed', {'type': 'agentMessage', 'content': [
                {'type': 'text', 'text': marker}]}, False),
            ('other-thread', 'completed', user_item, False),
        ]
        for tid, status, item, expected in cases:
            reply = {'thread': {'id': tid, 'turns': [
                {'id': 'desktop-turn', 'status': status, 'items': [item]}]}}
            codex.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
methods = []
for line in sys.stdin:
 msg = json.loads(line)
 methods.append(msg['method'])
 if msg['method'] == 'initialize':
  print(json.dumps({{'id':msg['id'], 'result':{{}}}}), flush=True)
 elif msg['method'] == 'thread/read':
  print(json.dumps({{'id':msg['id'], 'result':{reply!r}}}), flush=True)
Path({str(trace)!r}).write_text(json.dumps(methods))
''')
            codex.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(binary)}):
                got = dispatch.codex_wire.desktop_turn_terminated('desktop-thread', marker)
            assert got is expected, (tid, status, item, got)
            assert json.loads(trace.read_text()) == ['initialize', 'initialized', 'thread/read']

    check('Desktop reconciliation reads marked terminal history without starting or resuming a turn',
          desktop_recovery_reads_only_the_marked_terminal_turn)

    tmp.cleanup()
    print()
    if FAILURES:
        print(f"codex-live unit: {len(FAILURES)} FAILED")
        return 1
    print("codex-live unit: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
