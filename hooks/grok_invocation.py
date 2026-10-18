"""Identify Grok-owned hook invocations.

grok_session(): any Grok CLI process owns this hook through shell transports.
Joe 2026-10-06: "there is no reason to block any of my subscriptions from doing
real work. the model router is our protection from unqualified work." Grok
imports Claude's user-level hooks but has no CARR session lifecycle, so the
rule boot is never armed for it and every tool call was refused. Context
delivery hooks skip Grok sessions in every mode; effect guards stay on.

bounded_grok_read_only(): the runner's bounded read-only child, not an
interactive session (kept for the lifecycle hooks that only skip that case).

The marker alone is insufficient: inherited or forged markers cannot exempt
a writable/interactive Grok ancestor. Failed process readback keeps hooks on.
This is a context boundary; effect guards do not use it.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time

READ_ONLY_ENV = "CARR_GROK_RUN_READ_ONLY"
# The shortest enclosing hook has five seconds. Leave most of it for the
# retained gate when ancestry is slow, incomplete, or cannot be authenticated.
PROBE_BUDGET_S = 0.75
TRANSPARENT_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})


def _process_image(pid: int) -> str:
    if sys.platform == "darwin":
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libproc.proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        if libproc.proc_pidpath(pid, buffer, len(buffer)) <= 0:
            raise OSError(ctypes.get_errno(), "process image unavailable")
        return os.fsdecode(buffer.value)
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/{pid}/exe")
    raise OSError("process image readback unsupported")


def _grok_owner_command() -> str | None:
    pid = os.getppid()
    deadline = time.monotonic() + PROBE_BUDGET_S
    try:
        for _ in range(8):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            result = subprocess.run(["ps", "-ww", "-o", "ppid=,args=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=remaining)
            if result.returncode:
                return None
            parent, command = result.stdout.strip().split(None, 1)
            # argv[0] and process titles are writable; the OS image is separate.
            binary = os.path.basename(_process_image(pid))
            if time.monotonic() >= deadline:
                return None
            if re.fullmatch(r"grok(?:-\d+\.\d+\.\d+)?", binary):
                return command
            if binary not in TRANSPARENT_SHELLS:
                return None
            pid = int(parent)
            if pid <= 1:
                return None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return None


def grok_session() -> bool:
    """Any Grok image owns the hook through at most eight shell transports."""
    return _grok_owner_command() is not None


def bounded_grok_read_only() -> bool:
    if os.environ.get(READ_ONLY_ENV) != "1":
        return False
    command = _grok_owner_command()
    if command is None:
        return False
    # Inspect options before --print, never tokens from the prompt.
    print_flag = re.search(r" --print(?:\s|$)", command)
    if not print_flag:
        return False
    argv = command[:print_flag.start()].split()
    return (len(argv) == 12 and argv[1] == "--model"
            and argv[3] == "--reasoning-effort"
            and argv[5] == "--max-turns" and argv[6].isdigit()
            and argv[7:] == ["--always-approve", "--sandbox", "read-only",
                            "--output-format", "streaming-json"])
