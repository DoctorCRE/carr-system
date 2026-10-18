#!/usr/bin/env python3

from __future__ import annotations

import json
import os
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

DESK_SOCK = "/tmp/cc-socks/hermes-desk-test.sock"
BOOT_TIMEOUT_S = 90
ANSWER_TIMEOUT_S = 120
SEED = (
    "You are a throwaway desk session for the Hermes dispatch test. Reply with "
    "the single word READY and then wait. When a peer turn arrives, obey it "
    "exactly and reply with only what it asks for."
)


def wait_for(pred, timeout: float, what: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.25)
    print(f"  timed out waiting for {what}", file=sys.stderr)
    return False


def main() -> int:
    nonce = f"HERMES-{uuid.uuid4().hex[:10].upper()}"
    tmp = tempfile.TemporaryDirectory(prefix="hermes-desk-live-")
    root = Path(tmp.name)
    log = root / "desk.log"
    registry = desks.Registry(root / "desks.json")
    results = root / "results.jsonl"

    for stale in (DESK_SOCK,):
        try:
            os.unlink(stale)
        except FileNotFoundError:
            pass

    print(f"booting a labeled desk on {DESK_SOCK}")
    # Plain `claude -p` prints and
    # exits.
    proc = subprocess.Popen(
        ["claude", "--messaging-socket-path", DESK_SOCK,
         "-p", "--input-format", "stream-json", "--output-format", "stream-json",
         "--verbose"],
        stdin=subprocess.PIPE,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        cwd=str(root),
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(
        {"type": "user", "message": {"role": "user", "content": SEED}}) + "\n")
    proc.stdin.flush()
    try:
        if not wait_for(lambda: desks.is_live(DESK_SOCK), BOOT_TIMEOUT_S, "the desk socket"):
            print(log.read_text()[-2000:], file=sys.stderr)
            return 1
        print(f"  desk bound {DESK_SOCK} (pid {proc.pid})")

        def seeded() -> bool:
            try:
                return "READY" in log.read_text(errors="replace")
            except FileNotFoundError:
                return False

        if not wait_for(seeded, BOOT_TIMEOUT_S, "the desk to finish its seed turn"):
            print(log.read_text(errors="replace")[-2000:], file=sys.stderr)
            return 1
        print("  desk finished its seed turn")

        registry.register("hermes-desk-test", "claude-session", socket=DESK_SOCK)
        print("  registered as desk 'hermes-desk-test'")

        task = (
            f"Ignore your seed instruction to reply READY. Reply with exactly "
            f"{nonce} and nothing else."
        )
        row = dispatch.dispatch(
            "hermes-desk-test", task, registry=registry, results_path=results
        )
        print(f"  dispatched: {row['status']}")
        if row["status"] != "delivered":
            return 1

        def answered() -> bool:
            try:
                return nonce in log.read_text(errors="replace")
            except FileNotFoundError:
                return False

        if not wait_for(answered, ANSWER_TIMEOUT_S, "the desk to answer the dispatched task"):
            print("\n--- desk transcript tail ---", file=sys.stderr)
            print(log.read_text(errors="replace")[-3000:], file=sys.stderr)
            return 1

        print(f"\nPASS: the desk answered the dispatched task with {nonce}")
        rows = [json.loads(l) for l in results.read_text().splitlines() if l.strip()]
        print(f"      results file carries {len(rows)} line(s) for Hermes")
        return 0
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.unlink(DESK_SOCK)
        except FileNotFoundError:
            pass
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
