#!/usr/bin/env python3
"""Prove ordinary STORE calls boot without any npm installation.

`run.sh call` normally sends HTTPS to the deployed Worker. Database-driver,
WebSocket, and local tool-registry dependencies belong only to the explicit
direct-database break-glass path. This scratch copy intentionally has no
package.json and no node_modules; a module-scope package import therefore
reproduces the production plumbing failure immediately.
"""

import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mcp-server"


class Handler(http.server.BaseHTTPRequestHandler):
    received = None

    def do_POST(self):  # noqa: N802 — stdlib callback name
        length = int(self.headers.get("content-length", "0"))
        Handler.received = {
            "authorization": self.headers.get("authorization"),
            "body": json.loads(self.rfile.read(length)),
        }
        result_text = json.dumps({
            "ok": True,
            "recite": "Rules loaded: zero-install fixture",
        })
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": result_text}],
            },
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


node = shutil.which("node")
assert node, "node is required"

with tempfile.TemporaryDirectory() as td:
    scratch = Path(td) / "mcp-server"
    scratch.mkdir()
    for name in ("local-verb.mjs", "local-client-auth.mjs", "human-only-hint.mjs"):
        shutil.copy2(SOURCE / name, scratch / name)
    assert not (scratch / "node_modules").exists()
    assert not (scratch / "package.json").exists()

    token_file = Path(td) / "mcp-tokens.env"
    token_file.write_text("CARR_MCP_LOCAL_TOKEN=fixture-secret\n")
    token_file.chmod(0o600)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = dict(os.environ)
        for key in ("DATABASE_URL", "CARR_BREAK_GLASS", "CARR_BREAK_GLASS_REASON"):
            env.pop(key, None)
        env.update({
            "CARR_MCP_URL": f"http://127.0.0.1:{server.server_port}/mcp",
            "CARR_MCP_ENV": str(token_file),
        })
        result = subprocess.run(
            [node, str(scratch / "local-verb.mjs"), "standing-context", "{}"],
            cwd=scratch,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result.returncode == 0, result.stderr
    assert "local-verb identity -> local machine actor (via local-token)" in result.stderr
    output = json.loads(result.stdout)
    assert output == {"ok": True, "recite": "Rules loaded: zero-install fixture"}
    assert Handler.received is not None
    assert Handler.received["authorization"] == "Bearer fixture-secret"
    assert Handler.received["body"]["method"] == "tools/call"
    assert Handler.received["body"]["params"] == {
        "name": "standing-context",
        "arguments": {},
    }

print("local-verb-zero-install-selftest: ordinary STORE path needs no npm runtime")
