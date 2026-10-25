#!/usr/bin/env python3
"""Record a scheduled session's first Stop without blocking its work.

Persist the first outcome before delivery. A per-session lock serializes Stops,
and an acknowledged delivery stamp suppresses later attempts. Failed delivery
leaves the immutable receipt for the next Stop, even after interactive work
changes the transcript. Existing stamps remain authoritative.

A remote commit followed by lost acknowledgement can still duplicate a run.
The recorder has no idempotent insert contract, so this hook cannot promise
exactly-once delivery across that ambiguity.
"""
import fcntl
import json
import os
import sys
import tempfile
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.scheduled_run import (log, quick_launched_task, prepare_from_transcript,
                               record_prepared_run)  # noqa: E402


def _repo_root():
    """The repo root, from CARR_REPO_ROOT env or script-relative.

    The selftest points CARR_REPO_ROOT at a throwaway tree
    so concurrent ci.sh runs never share one idempotency-cache directory.
    Production leaves the variable unset.
    """
    env = os.environ.get("CARR_REPO_ROOT")
    if env and os.path.isdir(env):
        return os.path.realpath(env)
    return REPO


CACHE_DIR = os.path.join(_repo_root(), "out", "scheduled-run-record-cache")


def cache_path(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "-_")
    return os.path.join(CACHE_DIR, safe or "unknown")


def already_recorded(session_id: str) -> bool:
    return bool(session_id) and os.path.exists(cache_path(session_id))


def save_cache(path: str, content: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=CACHE_DIR, prefix=".receipt-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        directory = os.open(CACHE_DIR, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


def _parse_error(exc):
    log(f"ALLOW(parse-error) {exc}")
    return 0


def _decision_error(exc):
    log(f"ALLOW(internal-error) {exc}")
    return 0


@decision(on_error=_decision_error)
def decide(payload):
    if (payload.get("hook_event_name") or "") != "Stop":
        return 0
    if payload.get("stop_hook_active"):
        return 0

    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    if not session_id or already_recorded(session_id):
        return 0

    pending = cache_path(session_id) + ".pending"
    if not os.path.exists(pending) and not quick_launched_task(transcript):
        return 0

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(session_id) + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if already_recorded(session_id):
            return 0
        if os.path.exists(pending):
            with open(pending) as fh:
                receipt = json.load(fh)
        else:
            receipt = prepare_from_transcript(
                transcript, correlation=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                       "carr:scheduled-session:" + session_id)))
            if receipt is None:
                return 0
            save_cache(pending, json.dumps(receipt))
        result = record_prepared_run(receipt)
        if result.get("recorded"):
            save_cache(cache_path(session_id), "recorded\n")
            os.unlink(pending)

    return 0


def main():
    return run(decide, parse_error=_parse_error)


if __name__ == "__main__":
    sys.exit(main())
