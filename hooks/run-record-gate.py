#!/usr/bin/env python3
"""Run record-backed hooks with the repository interpreter, never ambient Python.

Claude invokes hook commands through system Python, which intentionally has no
project dependencies on this Mac.  These gates need psycopg for the canonical
record read, so the interpreter is an explicit, baseline-protected part of the
hook contract.  PATH and environment never choose the interpreter.

FAILURE DIRECTION (reversed 2026-08-23, and why).  This wrapper used to return 2
on every bootstrap fault, writing nothing anywhere.  Exit 2 on a Stop hook
BLOCKS, so the session saw "No stderr output" and could neither comply nor
diagnose; it read as a wedged session for roughly ten consecutive turns.  Three
things make failing closed here wrong:

  * hooks/SETTINGS-BLOCK.md is the standing ruling on this machine — hooks fail
    open, and every allow-on-error lands in out/hook-guard.log so a degraded
    gate stays discoverable.  Silent-and-closed inverted that on both axes.
  * Both wrapped gates already fail OPEN on their own internal errors
    (`log("ALLOW(internal-error)"); sys.exit(0)`).  The launcher was stricter
    than the thing it launches.
  * This wrapper never reads stdin, so it never sees `stop_hook_active` — the
    de-escalation both gates rely on to stop after one block.  A bootstrap fault
    therefore blocked EVERY turn, unbounded, with no way for the session to
    satisfy it.  A fresh worktree with no .venv is a real, expected state here.

So a bootstrap fault is now a LOUD open gate: one line to stderr, one line to
the log, exit 1 (non-blocking) rather than 2 (blocking).  What must never happen
again is the silence, not the allow.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "out" / "hook-guard.log"
ALLOWED = frozenset({"drift-claim-gate.py", "drift-assertion-gate.py"})


def bail(reason: str) -> int:
    """Never leave without saying why — to the session AND to the log."""
    line = f"run-record-gate ALLOW(bootstrap-fault) {reason}"
    print(line, file=sys.stderr)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    return 1


def hand_stdin_to_exec() -> None:
    """Make fd 0 carry the hook payload again before execve.

    hooks/hook-meter-run.py runs this wrapper in-process: it reads the real
    stdin (fd 0) to EOF and hands this code a replacement sys.stdin holding the
    same bytes. execve keeps fd 0, not sys.stdin, so the exec'd gate used to
    read a drained pipe, fail json.load, log ALLOW(parse-error) and allow.
    drift-claim-gate logged nothing else in production on 2026-09-23 and -24;
    ops/gate-replay.py found it by reading that log line. The payload now goes
    into an unlinked temporary file that becomes fd 0. When this wrapper is
    run directly, sys.stdin is the real one and fd 0 is untouched."""
    if sys.stdin is sys.__stdin__:
        return
    try:
        data = sys.stdin.buffer.read()
        import tempfile
        handle = tempfile.TemporaryFile()
        handle.write(data)
        handle.flush()
        handle.seek(0)
        os.dup2(handle.fileno(), 0)
    except Exception as exc:  # noqa: BLE001 — the gate then fails open, loudly
        bail(f"could not hand the payload to the gate on stdin: {exc}")


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) == 2 else ""
    if gate not in ALLOWED:
        # Refusing to exec an unlisted path is the security property, and it is
        # kept.  The exit CODE is a separate question: a mis-wired hook command
        # is not something the session can fix by being blocked for it.
        return bail(f"argv does not name an allowed gate: {sys.argv[1:]!r}")
    python = REPO / ".venv" / "bin" / "python"
    # Never fall back to system Python: it cannot perform the canonical record
    # read.  A missing interpreter means the check did not run — say so aloud.
    if not os.access(python, os.X_OK):
        return bail(f"repo interpreter missing or not executable: {python}")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    # NO psycopg readiness probe.  It used to run `python -c "import psycopg"`
    # with timeout=3; measured 4.5-8.4s on this Mac at load average ~330, so
    # under load it timed out every time and silently disabled the gate.  It was
    # never the enforcement — both gates already catch ImportError and log
    # ALLOW(internal-error) — so all it did was convert their fail-open into
    # this wrapper's fail-closed, at the cost of a second Python cold start.
    # Raising the timeout would move the cliff; deleting the probe removes it.
    hand_stdin_to_exec()
    try:
        os.execve(str(python), [str(python), str(REPO / "hooks" / gate)], env)
    except OSError as exc:
        return bail(f"execve of {gate} failed: {exc}")
    return bail(f"execve of {gate} returned unexpectedly")


if __name__ == "__main__":
    raise SystemExit(main())
