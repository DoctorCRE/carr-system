"""Durable local incident state shared by canonical health rows."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from contextlib import contextmanager


def confirmed_reply(name: str, result) -> dict:
    answer = result.reply
    if not result.ok or not isinstance(answer, dict) or answer.get("error") or (name != "read-loop" and answer.get("ok") is not True):
        raise RuntimeError(f"{name} did not confirm the write ({result.kind})")
    return answer


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def loop_record(run_verb, loop_id) -> dict:
    current = run_verb("read-loop", {"loop_id": loop_id})
    current = current.get("loop", current)
    if not isinstance(current, dict) or current.get("loop_id") != loop_id or type(current.get("version")) is not int:
        raise RuntimeError("read-loop returned no matching version")
    return current


def read_state(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except FileNotFoundError:
        return {}


@contextmanager
def incident_lock(path):
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
