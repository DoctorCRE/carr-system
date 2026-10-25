#!/usr/bin/env python3
"""Archive ciphertext with verified R2 readback and a durable local receipt.

Run with a ciphertext path to archive or retry it. Exit 1 while the copy is
owed. --dry-run changes no receipts. --prune-local DIRECTORY retains the newest
14 files and every older file without a matching verified archive receipt.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
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


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_receipt(path: Path, receipt: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix='.archive-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(receipt, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path) + '.archive.json')
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def archived(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        receipt = json.loads(Path(str(path) + '.archive.json').read_text())
        digest = sha256_of(str(path))
        return (receipt['archived'] is True and receipt['state'] == 'archived'
                and receipt['bytes'] == path.stat().st_size and receipt['sha256'] == digest
                and receipt['key'] == f'backups/{digest[:16]}/{path.name}'
                and receipt['bucket'] == r2.BUCKET)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_remote(key: str, digest: str, size: int) -> None:
    binary = r2.wrangler_bin()
    if not binary:
        raise ValueError('readback unavailable')
    with tempfile.TemporaryFile() as stream:
        result = subprocess.run(
            [binary, 'r2', 'object', 'get', f'{r2.BUCKET}/{key}', '--remote', '--pipe'],
            stdout=stream, stderr=subprocess.DEVNULL, timeout=120, env=r2._wrangler_env())
        stream.seek(0)
        observed = hashlib.sha256()
        total = 0
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            observed.update(chunk)
            total += len(chunk)
        if result.returncode or total != size or observed.hexdigest() != digest:
            raise ValueError('readback mismatch')


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prune-local', type=Path)
    modes.add_argument('--select-output', type=Path)
    args = parser.parse_args()
    if (args.prune_local or args.select_output) and (args.path or args.dry_run):
        parser.error('maintenance modes cannot archive a ciphertext path')
    if args.prune_local:
        copies = sorted((path for path in args.prune_local.glob('carr-*.sql.age')
                         if not path.is_symlink() and path.is_file()),
                        key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for copy in copies[14:]:
            try:
                with copy.open('rb') as source:
                    fcntl.flock(source, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if archived(copy):
                        copy.unlink()
                        Path(str(copy) + '.archive.json').unlink()
            except (FileNotFoundError, BlockingIOError):
                continue
        return 0
    if args.select_output:
        path = args.select_output
        path = path.with_name(path.name.removesuffix('.sql.age') + f'-{uuid.uuid4().hex}.sql.age')
        print(path)
        return 0
    if args.path is None:
        parser.error('a ciphertext path is required')
    path = args.path
    if path.is_symlink() or not path.is_file():
        parser.error('ciphertext must be a regular local file')
    arm_watchdog()
    try:
        with path.open('rb') as source:
            fcntl.flock(source, fcntl.LOCK_EX)
            if os.fstat(source.fileno()).st_ino != path.stat().st_ino:
                parser.error('ciphertext changed before archival')
            return archive(path, args.dry_run)
    except FileNotFoundError:
        parser.error('ciphertext disappeared before archival')


def archive(path: Path, dry_run: bool) -> int:
    sha = sha256_of(str(path))
    size = path.stat().st_size
    key = f'backups/{sha[:16]}/{path.name}'
    try:
        previous = json.loads(Path(str(path) + '.archive.json').read_text())
    except (OSError, ValueError):
        previous = {}
    receipt = {'archived': False, 'state': 'owed', 'reason': 'archive_pending',
               'key': key, 'bucket': r2.BUCKET, 'bytes': size, 'sha256': sha}
    if not dry_run:
        save_receipt(path, receipt)

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

        if (isinstance(previous, dict) and previous.get('archived') is False
                and previous.get('sha256') == sha and key in led['objects']):
            # The persisted reservation stands until the same key is replaced successfully.
            led['objects'][key] = {**led['objects'][key], 'sha256': None, 'bytes': 0}
        try:
            receipt['reason'] = 'upload_failed'
            r2.upload(str(path), key, sha, size, cap, provenance, led, dry_run=dry_run)
            if dry_run:
                receipt.update(state='dry_run', reason='dry_run')
            else:
                receipt['reason'] = 'readback_failed'
                verify_remote(key, sha, size)
                if path.stat().st_size != size or sha256_of(str(path)) != sha:
                    raise ValueError('source changed')
                receipt.update(archived=True, state='archived', reason='verified')
        except r2.QuotaExceeded:
            receipt['reason'] = 'quota_exceeded'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            if receipt['reason'] != 'readback_failed':
                receipt['reason'] = 'upload_failed'
        if not dry_run:
            save_receipt(path, receipt)
            print(r2.usage_summary(r2.load_ledger(), cap, provenance), file=sys.stderr)
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt['archived'] or dry_run else 1
    finally:
        # finally, not the tail of the success path: a quota refusal and an
        # upload error both returned before the old close() and leaked the
        # connection.
        signal.alarm(0)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
