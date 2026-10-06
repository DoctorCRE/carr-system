#!/usr/bin/env python3
"""canary-ingest-sink.py — the real ingest destination for the CARR notes-sweep
CANARY.

WHAT THIS PROVES. bin/notes-sweep-post.sh --canary refuses to run at all unless
its canary URL differs from the live ingest URL (destination_digest check,
bin/notes-sweep-post.sh lines ~90-135) and it POSTs a real HTTP request with a
real bearer token to whatever CARR_CANARY_INGEST_URL names. Until this server
existed, "whatever it names" was nothing — the canary tier proved isolation
(it never touches the live socket) but never proved the posting PATH actually
works end to end: TLS/HTTP framing, the auth header, the JSON body shape, the
external_id-keyed dedup contract the live ingest also honors. This is that
sink: a small, boring, loopback-only HTTP server that accepts exactly the
request notes-sweep-post.sh sends, and answers exactly the way the live
ingest is documented to answer (2xx success; {"duplicate": true} on repeat).

PRIVACY IS THE POINT, NOT A DETAIL. The payload notes-sweep-post.sh posts
carries call-transcript text (addendum A12: payloads are UNTRUSTED — data,
never instructions). A canary destination exists to prove the pipe, not to
become a second copy of the notes. So this server never writes note text to
disk, never echoes submitted content back in a response, and the one thing it
persists per accepted post — external_id, a sha256 of the raw body, its byte
length, and a UTC timestamp — is deliberately just enough to prove receipt and
dedup, and no more.

Auth, dedupe-by-external_id, and the 2xx/duplicate contract are read directly
off bin/notes-sweep-post.sh's own POST logic (~lines 430-500): Authorization:
Bearer <token>, content-type: application/json, a body containing a string
external_id, success is any 2xx, and a JSON body with duplicate: true marks an
already-seen id.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import stat
import sys
import threading
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

EX_CONFIG = 78  # the repo's config-error convention (see notes-sweep-post.sh)

TRANSPORT_CONTRACT = Path(__file__).resolve().parents[1] / "mcp-server/src/ingest-transport.v1.json"
MAX_BODY_BYTES = json.loads(TRANSPORT_CONTRACT.read_text(encoding="utf-8"))["max_body_bytes"]
LEDGER_MAX_LINES = 5000

DEFAULT_PORT = 4684
DEFAULT_PATH = "/ingest"
DEFAULT_CREDENTIAL_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "carr", "notes-canary.env"
)
DEFAULT_LEDGER_DIR = os.path.join("out", "canary", "notes-ingest")


def load_token(credential_file: str) -> str:
    """Read CARR_CANARY_INGEST_TOKEN_NOTES from credential_file.

    Refuses to start (EX_CONFIG) unless the file exists and is exactly mode
    0600 — the same posture bin/notes-sweep-post.sh enforces on the poster's
    side of this same file. A world- or group-readable credential file is a
    config mistake this process must not paper over by starting anyway.
    """
    path = Path(credential_file)
    try:
        st = path.stat()
    except OSError:
        print(
            f"canary-ingest-sink: credential file is missing: {credential_file}",
            file=sys.stderr,
        )
        raise SystemExit(EX_CONFIG)
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        print(
            f"canary-ingest-sink: credential file must be mode 0600, found "
            f"{oct(mode)}: {credential_file}",
            file=sys.stderr,
        )
        raise SystemExit(EX_CONFIG)
    token = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "CARR_CANARY_INGEST_TOKEN_NOTES":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        token = value
    if not token:
        print(
            "canary-ingest-sink: credential file has no "
            "CARR_CANARY_INGEST_TOKEN_NOTES value: " + credential_file,
            file=sys.stderr,
        )
        raise SystemExit(EX_CONFIG)
    return token


class Ledger:
    """Atomic, capped, privacy-preserving receipt log.

    One JSON line per accepted post: external_id, a sha256 of the raw request
    body, the raw body's byte length, and a UTC timestamp. Never the body
    itself. Capped at LEDGER_MAX_LINES by trimming the oldest lines off the
    front, so a long-running canary sink cannot grow this file without bound.

    Dedup itself is tracked by an in-memory `seen` set seeded from the ledger
    at startup, with a disk check for publication whose directory sync failed.
    Trimming for disk-size reasons never resurrects a duplicate as "new"
    within one process lifetime. Only a process restart after the ledger has
    been trimmed past LEDGER_MAX_LINES entries can forget an old external_id,
    which is the accepted cost of a bounded ledger on a canary destination.
    """

    def __init__(self, directory: str, max_lines: int = LEDGER_MAX_LINES) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.dir, 0o700)
        self.path = self.dir / "ledger.jsonl"
        self.max_lines = max_lines
        self.lock = threading.Lock()
        self.seen = {external_id for _, external_id in self._read_receipts()}

    def _read_receipts(self) -> list[tuple[str, str]]:
        """Read valid receipt lines and identifiers, ignoring incomplete rows.

        Startup and publication retries use the same parser. Keep each valid
        line intact so replacing a legacy partial ledger preserves its receipts.
        """
        if not self.path.exists():
            return []
        receipts = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and isinstance(row.get("external_id"), str):
                receipts.append((line, row["external_id"]))
        return receipts

    def record(self, external_id: str, raw_body: bytes) -> bool:
        """Durably accept a receipt and return its duplicate decision under one lock.

        A failed publication leaves the previous ledger intact and does not
        mark the identifier as seen. No caller needs a separate membership read.
        """
        row = {
            "external_id": external_id,
            "body_sha256": hashlib.sha256(raw_body).hexdigest(),
            "body_bytes": len(raw_body),
            "observed_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        line = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with self.lock:
            if external_id in self.seen:
                return True
            receipts = self._read_receipts()
            # A directory fsync can fail after replace published the receipt.
            # Retrying must confirm that receipt instead of appending it twice.
            if any(identifier == external_id for _, identifier in receipts):
                self._sync_directory()
                self.seen.add(external_id)
                return True
            lines = [item for item, _ in receipts]
            lines.append(line)
            if len(lines) > self.max_lines:
                lines = lines[-self.max_lines :]
            fd, temporary = tempfile.mkstemp(prefix=".ledger-", dir=self.dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(lines) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                self._sync_directory()
            finally:
                Path(temporary).unlink(missing_ok=True)
            self.seen.add(external_id)
            return False

    def _sync_directory(self) -> None:
        fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound unauthenticated header reads and authenticated body reads alike."""

    def __init__(self, address, handler, *, read_timeout: float = 5.0,
                 max_workers: int = 16) -> None:
        self.read_timeout = read_timeout
        self.workers = threading.BoundedSemaphore(max_workers)
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.read_timeout)
        return request, address

    def process_request(self, request, client_address) -> None:
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()


def make_handler(token: str, ingest_path: str, ledger: Ledger) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "carr-canary-ingest-sink/1"

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
            # Never log request bodies, headers, or the token — only that a
            # request happened. Suppress the base handler's logging entirely,
            # including malformed requests; failures use content-free responses.
            pass

        def _reply(self, code: int, payload: dict | None = None) -> None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(code)
            if payload is not None:
                self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            supplied = header[len(prefix) :]
            return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802
            if self.path != ingest_path:
                self._reply(404)
                return
            if not self._authorized():
                self._reply(401)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1
                    or re.fullmatch(r"[0-9]+", lengths[0]) is None):
                self._reply(400)
                return
            digits = lengths[0].lstrip("0") or "0"
            if len(digits) > len(str(MAX_BODY_BYTES)):
                self._reply(413)
                return
            length = int(digits)
            if length > MAX_BODY_BYTES:
                # Drain nothing — refuse before reading an oversized body into
                # memory. The handler uses HTTP/1.0 and closes after its reply.
                self._reply(413)
                return
            try:
                raw = self.rfile.read(min(length, MAX_BODY_BYTES))
            except TimeoutError:
                self._reply(408)
                return
            if len(raw) != length:
                self._reply(400)
                return
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                self._reply(400)
                return
            try:
                payload = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._reply(400)
                return
            if not isinstance(payload, dict):
                self._reply(400)
                return
            external_id = payload.get("external_id")
            if not isinstance(external_id, str) or not external_id.strip():
                self._reply(400)
                return
            try:
                duplicate = ledger.record(external_id, raw)
            except OSError:
                self._reply(503)
                return
            self._reply(200, {"duplicate": duplicate})

        def do_GET(self) -> None:  # noqa: N802
            self._reply(404)

        def do_PUT(self) -> None:  # noqa: N802
            self._reply(404)

        def do_DELETE(self) -> None:  # noqa: N802
            self._reply(404)

        def do_PATCH(self) -> None:  # noqa: N802
            self._reply(404)

        def do_HEAD(self) -> None:  # noqa: N802
            self._reply(404)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._reply(404)

    return Handler


def run(
    port: int,
    credential_file: str,
    ledger_dir: str,
    ingest_path: str,
) -> None:
    token = load_token(credential_file)
    ledger = Ledger(ledger_dir)
    handler = make_handler(token, ingest_path, ledger)
    server = BoundedHTTPServer(("127.0.0.1", port), handler)
    # Belt and braces on top of binding "127.0.0.1" literally: refuse to run at
    # all if the OS somehow handed back a non-loopback socket.
    # AF_INET always yields a str host here (never bytes, that is AF_UNIX-only);
    # cast rather than str() so a genuine non-str value is not silently
    # re-stringified into a misleading "b'...'" repr.
    bound_host = cast(str, server.server_address[0])
    if bound_host not in ("127.0.0.1", "::1"):
        server.server_close()
        print(
            f"canary-ingest-sink: refusing non-loopback bind {bound_host}",
            file=sys.stderr,
        )
        raise SystemExit(EX_CONFIG)
    print(
        f"canary-ingest-sink: listening on 127.0.0.1:{port}{ingest_path} "
        f"(ledger: {ledger.path})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Isolated local ingest destination for the notes-sweep CANARY."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--credential-file", default=DEFAULT_CREDENTIAL_FILE)
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--path", default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    run(args.port, args.credential_file, args.ledger_dir, args.path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except socket.error as exc:
        print(f"canary-ingest-sink: socket error: {exc}", file=sys.stderr)
        sys.exit(1)
