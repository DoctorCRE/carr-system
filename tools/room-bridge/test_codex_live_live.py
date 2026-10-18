#!/usr/bin/env python3
"""the path must fit the limit macOS puts on unix sockets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import dispatch  # noqa: E402

RUN_DIR = Path("/private/tmp/hermes-codex-live-test")
BOOT_TIMEOUT_S = 90


def main() -> int:
    nonce = f"CXLIVE-{uuid.uuid4().hex[:10].upper()}"
    token = f"TOKEN-{uuid.uuid4().hex[:8].upper()}"
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR, ignore_errors=True)
    RUN_DIR.mkdir(parents=True)
    sock = RUN_DIR / "live.sock"
    log = RUN_DIR / "app-server.log"
    state = tempfile.TemporaryDirectory(prefix="hermes-cxlive-")
    reg = desks.Registry(RUN_DIR / "desks.json")
    results = RUN_DIR / "results.jsonl"

    print(f"booting a Codex app-server on {sock}")
    proc = subprocess.Popen(
        ["codex", "app-server", "--listen", f"unix://{sock}"],
        env=dict(os.environ, CODEX_SQLITE_HOME=state.name),
        stdin=subprocess.DEVNULL,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline and not desks.is_live(str(sock)):
            if proc.poll() is not None:
                print(log.read_text()[-1500:], file=sys.stderr)
                return 1
            time.sleep(0.3)
        if not desks.is_live(str(sock)):
            print(f"the app-server never bound {sock}", file=sys.stderr)
            return 1
        print(f"  bound (pid {proc.pid})")

        reg.register(
            "cx-live-test", "codex-live", socket=str(sock), cwd=str(RUN_DIR),
            model="gpt-5.1-codex-mini", effort="medium",
        )

        first = dispatch.dispatch(
            "cx-live-test",
            f"Remember this token: {token}. Reply with exactly {nonce} and nothing else.",
            registry=reg, results_path=results,
        )
        print(f"  first turn: {first['status']} -> {first.get('result')!r}")
        if first["status"] != "completed" or nonce not in (first.get("result") or ""):
            print("FAIL: the dispatched nonce did not come back", file=sys.stderr)
            return 1
        if first.get("resumed") is not False:
            print("FAIL: the first turn should not have resumed anything", file=sys.stderr)
            return 1

        second = dispatch.dispatch(
            "cx-live-test",
            "What token did I ask you to remember? Reply with only that token.",
            registry=reg, results_path=results,
        )
        print(f"  second turn: {second['status']} -> {second.get('result')!r}")
        if second.get("resumed") is not True:
            print("FAIL: the second turn did not resume the desk's thread", file=sys.stderr)
            return 1
        if token not in (second.get("result") or ""):
            print(f"FAIL: the desk did not recall {token}", file=sys.stderr)
            return 1
        if second.get("thread_id") != first.get("thread_id"):
            print("FAIL: the second turn landed in a different thread", file=sys.stderr)
            return 1

        print(f"\nPASS: the live Codex desk answered {nonce} and then recalled {token}")
        print(f"      both turns in thread {first['thread_id']}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        state.cleanup()
        shutil.rmtree(RUN_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
