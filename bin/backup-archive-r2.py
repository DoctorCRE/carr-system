#!/usr/bin/env python3
"""Upload an encrypted DB dump and verify its remote object metadata.

Exit 1 when the off-Mac copy is unverified. Local ciphertext is retained by
backup-dump.sh. A dry run reports archived=false and sends no bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import signal
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import r2_archive as r2  # noqa: E402

CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 30_000
WATCHDOG_S = 1200          # comfortably above 90 + 600 + 120 of internal timeouts


def arm_watchdog(seconds: int = WATCHDOG_S) -> None:
    """Hard wall-clock ceiling on this process. Never silent, always an exit code."""
    def bail(_signum, _frame):
        print(f"WATCHDOG: backup-archive-r2.py exceeded {seconds}s and was aborted. "
              "The local dump stands; the R2 archive copy is OWED for this run.",
              file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)
    signal.signal(signal.SIGALRM, bail)
    signal.alarm(seconds)


def hashes_of(path: str) -> tuple[str, str]:
    h = hashlib.sha256()
    md5 = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            md5.update(chunk)
    return h.hexdigest(), md5.hexdigest()


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        sys.exit("usage: backup-archive-r2.py <path-to-.sql.age> [--dry-run]")
    path = paths[0]
    if not os.path.exists(path):
        sys.exit(f"not found: {path}")

    arm_watchdog()

    conn = None
    url = os.environ.get("DATABASE_URL")
    if url:
        c = None
        try:
            import psycopg
            c = psycopg.connect(
                url,
                connect_timeout=CONNECT_TIMEOUT_S,
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute(f"SET idle_in_transaction_session_timeout = {STATEMENT_TIMEOUT_MS}")
            conn = c
        except Exception as e:  # noqa: BLE001
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            print(f"note: DATABASE_URL set but connect failed ({type(e).__name__}); "
                  f"quota defaults to {r2.DEFAULT_QUOTA_GB} GB", file=sys.stderr)

    try:
        cap, provenance = r2.quota_bytes(conn)
        led = r2.load_ledger()
        if conn is not None:
            r2.reconcile(led, conn)

        sha, md5 = hashes_of(path)
        size = os.path.getsize(path)
        key = f"backups/{sha[:16]}/{os.path.basename(path)}"

        result = {"archived": False, "key": key, "bucket": r2.BUCKET,
                  "bytes": size, "sha256": sha, "reason": "verification_failed"}
        try:
            r2.upload(path, key, sha, size, cap, provenance, led, dry_run=dry_run, verify_md5=md5)
            if dry_run:
                result["reason"] = "dry_run"
            else:
                result.update(archived=True, reason="verified")
        except r2.QuotaExceeded:
            result["reason"] = "quota_exceeded"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            pass
        if not dry_run:
            print(r2.usage_summary(led, cap, provenance), file=sys.stderr)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["archived"] or dry_run else 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
