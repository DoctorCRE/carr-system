"""Ed25519 operations with complete in-memory input on macOS and Linux."""
from __future__ import annotations

import base64
import json
import subprocess

_NODE = """
import {readFileSync} from 'node:fs';
import {sign, verify} from 'node:crypto';
const request = JSON.parse(readFileSync(0, 'utf8'));
const key = Buffer.from(request.key, 'base64');
const payload = Buffer.from(request.payload, 'base64');
if (process.argv[1] === 'sign') {
  process.stdout.write(sign(null, payload, key));
} else {
  process.exit(verify(null, payload, key, Buffer.from(request.signature, 'base64')) ? 0 : 1);
}
"""


def _run(operation: str, key: bytes, payload: bytes, signature: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    request = {"key": base64.b64encode(key).decode("ascii"),
               "payload": base64.b64encode(payload).decode("ascii"),
               "signature": base64.b64encode(signature).decode("ascii")}
    return subprocess.run(
        ["node", "--input-type=module", "-e", _NODE, operation],
        input=json.dumps(request, separators=(",", ":")).encode("ascii"),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False,
    )


def sign(key: bytes, payload: bytes) -> bytes:
    result = _run("sign", key, payload)
    if result.returncode or len(result.stdout) != 64:
        raise ValueError("Ed25519 signing failed")
    return result.stdout


def verify(key: bytes, payload: bytes, signature: bytes) -> bool:
    return _run("verify", key, payload, signature).returncode == 0
