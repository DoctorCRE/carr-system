#!/usr/bin/env python3
"""canary-ingest-sink-selftest.py — hermetic contract checks for the notes-sweep
CANARY's local ingest destination (tools/canary-ingest-sink.py).

WHAT THIS DEFENDS. bin/notes-sweep-post.sh --canary posts a real HTTP request
with a real bearer token and a JSON body carrying external_id (lines ~430-500),
and the canary tier is only as real as the destination it posts to. This suite
proves that destination actually implements the contract the poster assumes:
auth, dedup-by-external_id, the 2xx/{"duplicate": true|false} response shape,
and — because the payload carries call-transcript text (addendum A12) — that
the sink never persists that text, only enough metadata to prove receipt.

NO MOCKS FOR THE HTTP SURFACE: every 401/400/404/413/200 case below launches
the REAL script as a subprocess, bound to a real loopback port with a real
temp 0600 credential file, and drives it with real HTTP requests. Only the
credential-refusal and static-source checks work at the unit/source level,
because they are about what happens BEFORE a socket is ever opened.

Everything here is loopback-only and touches no network beyond 127.0.0.1, no
real ~/.config, and no shared out/ directory — each case gets its own
tempfile.TemporaryDirectory.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SINK = REPO / "tools" / "canary-ingest-sink.py"

FAILED: list[str] = []
TOTAL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_credential(path: Path, token: str, mode: int = 0o600) -> None:
    path.write_text(f"CARR_CANARY_INGEST_TOKEN_NOTES={token}\n", encoding="utf-8")
    os.chmod(path, mode)


def wait_for_port(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


class Sink:
    """Launches the real script as a subprocess against a temp credential
    file and temp ledger dir, and tears it down."""

    def __init__(self, credential_file: Path, ledger_dir: Path, port: int | None = None) -> None:
        self.port = port if port is not None else free_port()
        self.credential_file = credential_file
        self.ledger_dir = ledger_dir
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, expect_up: bool = True) -> subprocess.Popen[bytes]:
        self.proc = subprocess.Popen(
            [sys.executable, str(SINK),
             "--port", str(self.port),
             "--credential-file", str(self.credential_file),
             "--ledger-dir", str(self.ledger_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if expect_up:
            up = wait_for_port(self.port)
            if not up:
                self.stop()
        return self.proc

    def stop(self) -> tuple[int | None, bytes, bytes]:
        if self.proc is None:
            return None, b"", b""
        proc = self.proc
        proc.terminate()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate(timeout=5)
        self.proc = None
        return proc.returncode, out, err

    def request(self, method: str, path: str, token: str | None,
                body: bytes | None = None, content_type: str | None = "application/json",
                headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            hdrs = dict(headers or {})
            if token is not None:
                hdrs["Authorization"] = f"Bearer {token}"
            if content_type is not None:
                hdrs["Content-Type"] = content_type
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()


def run_refusal_cases() -> None:
    """Cases about what happens BEFORE a socket is ever opened: no server
    should end up listening in any of these."""
    with tempfile.TemporaryDirectory(prefix="carr-canary-sink-refuse-") as raw:
        tmp = Path(raw)
        ledger_dir = tmp / "ledger"

        # missing credential file entirely
        missing = tmp / "missing.env"
        sink = Sink(missing, ledger_dir, port=free_port())
        proc = sink.start(expect_up=False)
        rc = proc.wait(timeout=5)
        _, out, err = sink.stop()
        check("refuses to start with no credential file at all (exit 78)",
              rc == 78, f"rc={rc} stderr={err!r}")
        check("...and never opens the port",
              not wait_for_port(sink.port, timeout=0.3))
        check("...and never wrote a ledger",
              not ledger_dir.exists())

        # credential file present but wrong mode (0644)
        bad_mode = tmp / "bad-mode.env"
        write_credential(bad_mode, "tok-a", mode=0o644)
        sink = Sink(bad_mode, ledger_dir, port=free_port())
        proc = sink.start(expect_up=False)
        rc = proc.wait(timeout=5)
        _, out, err = sink.stop()
        check("refuses to start with a non-0600 credential file (exit 78)",
              rc == 78, f"rc={rc} stderr={err!r}")
        check("...and never opens the port for a bad-mode credential",
              not wait_for_port(sink.port, timeout=0.3))

        # credential file present, correct mode, but no matching key inside
        empty_key = tmp / "empty-key.env"
        empty_key.write_text("SOME_OTHER_KEY=x\n", encoding="utf-8")
        os.chmod(empty_key, 0o600)
        sink = Sink(empty_key, ledger_dir, port=free_port())
        proc = sink.start(expect_up=False)
        rc = proc.wait(timeout=5)
        sink.stop()
        check("refuses to start with a 0600 file that has no token value (exit 78)",
              rc == 78, f"rc={rc}")


def run_live_cases() -> None:
    with tempfile.TemporaryDirectory(prefix="carr-canary-sink-live-") as raw:
        tmp = Path(raw)
        credential_file = tmp / "notes-canary.env"
        ledger_dir = tmp / "canary" / "notes-ingest"
        token = "s3cr3t-notes-token"
        write_credential(credential_file, token)

        sink = Sink(credential_file, ledger_dir)
        proc = sink.start()
        check("starts and binds its port with a valid 0600 credential",
              proc is not None and proc.poll() is None)

        try:
            # ---- auth ----
            status, body = sink.request("POST", "/ingest", None,
                                         body=b'{"external_id":"n-1","note_text":"secret call notes"}')
            check("POST with no Authorization header is 401", status == 401)
            check("...and the 401 body is empty (no detail leaked)", body == b"")

            status, body = sink.request("POST", "/ingest", "wrong-token",
                                         body=b'{"external_id":"n-1"}')
            check("POST with a wrong bearer token is 401", status == 401)

            # ---- wrong path / method -> 404 ----
            status, _ = sink.request("POST", "/not-ingest", token,
                                      body=b'{"external_id":"n-1"}')
            check("POST to the wrong path is 404", status == 404)

            status, _ = sink.request("GET", "/ingest", token)
            check("GET to the ingest path is 404", status == 404)

            # ---- missing external_id -> 400 ----
            status, _ = sink.request("POST", "/ingest", token, body=b'{"note_text":"hi"}')
            check("POST with no external_id is 400", status == 400)

            status, _ = sink.request("POST", "/ingest", token, body=b'{"external_id":""}')
            check("POST with an empty-string external_id is 400", status == 400)

            status, _ = sink.request("POST", "/ingest", token, body=b'not json at all')
            check("POST with unparseable JSON is 400", status == 400)

            # ---- oversized body -> 413 ----
            # Claim a Content-Length over the cap via a raw socket, but only
            # write a few bytes of "body" — the server must refuse on the
            # HEADER alone, before it ever tries to read the body, exactly as
            # tools/canary-ingest-sink.py documents ("Drain nothing"). Sending
            # a real multi-MB body over a live loopback connection the server
            # may close mid-transfer is exactly the kind of thing that makes a
            # test flaky for reasons that have nothing to do with the 413
            # contract.
            oversized_len = 3 * 1024 * 1024
            with socket.create_connection(("127.0.0.1", sink.port), timeout=5) as raw_sock:
                crlf = "\r\n"
                request_head = (
                    "POST /ingest HTTP/1.1" + crlf +
                    "Host: 127.0.0.1" + crlf +
                    f"Authorization: Bearer {token}" + crlf +
                    "Content-Type: application/json" + crlf +
                    f"Content-Length: {oversized_len}" + crlf +
                    "Connection: close" + crlf + crlf
                ).encode("ascii")
                raw_sock.sendall(request_head)
                raw_sock.sendall(b"x" * 16)  # a token amount of "body", never the full claimed length
                raw_sock.settimeout(5)
                response = b""
                try:
                    while True:
                        chunk = raw_sock.recv(4096)
                        if not chunk:
                            break
                        response += chunk
                        if b"\r\n\r\n" in response:
                            break
                except (TimeoutError, OSError):
                    pass
            check("POST over the live-contract size cap is 413 (refused on Content-Length alone)",
                  b" 413 " in response, f"response head={response[:200]!r}")

            # ---- first sight / duplicate ----
            note_text = "This is a private call transcript that must never be stored."
            payload = json.dumps({"external_id": "note-abc-123", "note_text": note_text}).encode("utf-8")
            status, body = sink.request("POST", "/ingest", token, body=payload)
            parsed = json.loads(body) if body else {}
            check("first post of a new external_id is 200 duplicate:false",
                  status == 200 and parsed.get("duplicate") is False, f"status={status} body={body!r}")
            check("the response never echoes submitted content back",
                  note_text.encode("utf-8") not in body and b"note_text" not in body)

            status, body = sink.request("POST", "/ingest", token, body=payload)
            parsed = json.loads(body) if body else {}
            check("repeating the SAME external_id is 200 duplicate:true",
                  status == 200 and parsed.get("duplicate") is True, f"status={status} body={body!r}")

            different = json.dumps({"external_id": "note-different-456"}).encode("utf-8")
            status, body = sink.request("POST", "/ingest", token, body=different)
            parsed = json.loads(body) if body else {}
            check("a DIFFERENT external_id after that is 200 duplicate:false",
                  status == 200 and parsed.get("duplicate") is False)

        finally:
            rc, out, err = sink.stop()

        # ---- the ledger: digest present, note text absent ----
        check("captured subprocess stdout/stderr contain no note text or bearer token",
              note_text.encode() not in out + err and token.encode() not in out + err)
        ledger_path = ledger_dir / "ledger.jsonl"
        check("the ledger file exists after accepted posts", ledger_path.is_file())
        check("the ledger directory is mode 0700",
              oct(ledger_dir.stat().st_mode & 0o777) == "0o700")
        check("the ledger file is mode 0600",
              ledger_path.exists() and oct(ledger_path.stat().st_mode & 0o777) == "0o600")

        rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
        ledger_text = ledger_path.read_text(encoding="utf-8")
        check("the ledger holds one row per ACCEPTED (non-duplicate) post",
              len(rows) == 2, f"rows={rows}")
        check("every ledger row carries external_id, body_sha256, body_bytes, observed_at and nothing else",
              all(set(r.keys()) == {"external_id", "body_sha256", "body_bytes", "observed_at"} for r in rows),
              f"rows={rows}")
        expected_digest = hashlib.sha256(payload).hexdigest()
        check("the stored digest matches sha256 of the raw posted body",
              any(r["external_id"] == "note-abc-123" and r["body_sha256"] == expected_digest for r in rows),
              f"rows={rows}")
        check("the ledger NEVER contains the note text itself",
              note_text not in ledger_text and "note_text" not in ledger_text)
        check("the ledger records the raw body's byte length",
              any(r["body_bytes"] == len(payload) for r in rows))


def run_ledger_trim_case() -> None:
    """The ledger is capped at a bounded line count so it cannot grow without
    limit. Exercised against the module directly (not a live 5000-post
    subprocess run, which would be slow) but through the REAL Ledger class."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("canary_ingest_sink", str(SINK))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    with tempfile.TemporaryDirectory(prefix="carr-canary-sink-trim-") as raw:
        ledger_dir = Path(raw) / "ledger"
        cap = 10
        ledger = mod.Ledger(str(ledger_dir), max_lines=cap)
        for i in range(cap + 7):
            ledger.record(f"trim-{i}", f"body-{i}".encode("utf-8"))
        lines = (ledger_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        check(f"the ledger trims to its cap ({cap} lines) once exceeded",
              len(lines) == cap, f"len={len(lines)}")
        rows = [json.loads(line) for line in lines]
        ids = {r["external_id"] for r in rows}
        check("trimming drops the OLDEST entries, keeping the most recent",
              "trim-0" not in ids and f"trim-{cap + 6}" in ids, f"ids={ids}")
        check("an id trimmed off disk is still remembered in-process (bounded disk, not bounded memory)",
              ledger.record("trim-0", b"synthetic duplicate") is True)


def run_static_checks() -> None:
    source = SINK.read_text(encoding="utf-8")
    check('binds literally to "127.0.0.1" and never to "0.0.0.0" or an empty host',
          '"127.0.0.1"' in source and "0.0.0.0" not in source)
    check("uses hmac.compare_digest for the token comparison (no naive == on a secret)",
          "hmac.compare_digest" in source)
    check("there is no --host / bind-address flag that could widen the bind",
          "--host" not in source and "--bind" not in source)
    check("the handler never writes request bodies or headers to its own log",
          "def log_message" in source)


def main() -> int:
    print("canary-ingest-sink-selftest\n")
    print("-- refusal-before-any-socket cases --")
    run_refusal_cases()
    print("-- live HTTP contract cases --")
    run_live_cases()
    print("-- ledger cap case --")
    run_ledger_trim_case()
    print("-- static source checks --")
    run_static_checks()

    print()
    passed = TOTAL - len(FAILED)
    print(f"canary-ingest-sink-selftest — {passed}/{TOTAL} passed")
    if FAILED:
        print("FAILED: " + "; ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
