#!/usr/bin/env python3
"""Offline browser-verification host for the real Model Room bundle.

This is test-only plumbing: it serves the materialized, pinned DoctorCRE Model Room assets and a
single synthetic typed wire turn from the existing execution-fabric fixtures.
It neither writes a room turn nor stands in for the deployed Worker.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))



class PreviewHandler(SimpleHTTPRequestHandler):
    assets = ROOT / "out" / "doctorcre-artifacts" / "current"
    fixtures = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.assets), **kwargs)

    def _turns(self) -> dict:
        projection = json.loads((self.fixtures / "codex_desktop.observatory-projection.v1.json").read_text())
        turn = {"seq": "1", "msg_id": "preview-observatory", "at": projection["generated_at"],
                "sponsor": "joe", "seat": "hermes", "kind": "receipt",
                "body": json.dumps({"job_passport": {"schema_version": "job-passport-wire.v1",
                    "kind": "observatory_projection", "payload": projection}})}
        return {"actor": {"slug": "joe"}, "csrf_token": "preview-only", "latest_seq": "1", "turns": [turn]}

    def do_GET(self):  # noqa: N802 - stdlib callback
        path = urlparse(self.path).path
        if path == "/api/room/turns":
            body = json.dumps(self._turns()).encode("utf-8")
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store"); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/":
            self.path = "/room.html"
        return super().do_GET()

    def log_message(self, format, *args):  # noqa: A003 - keep browser verification quiet
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the real Model Room with an offline typed Job Passport wire fixture")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
