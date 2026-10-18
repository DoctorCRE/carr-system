#!/usr/bin/env python3
"""Tailscale up-or-down health row (WR-000178).

After the 2026-09-30 macOS reboot Tailscale on the Mac Studio stayed "stopped"
for about six hours. Nothing reported it; the first symptom was SSH to the
MacBook failing. This row reads `Tailscale status` directly — the artifact,
not a proxy — and FAILS the health run when the node is stopped or logged out,
because either one cuts the same SSH path.

Bound response (rule 590b11e1) is printed inline on every render. The fix for
"stopped" is the start-at-login agent com.carr.tailscale-up (bin/tailscale-up.sh),
which the ordinary config-as-code install places on the primary.
"""
from __future__ import annotations

import os
import json
import ipaddress
import subprocess
import time
import signal

TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
LABEL = "tailscale"

BOUND_ACTION = (
    "on breach: open/update one dedup loop 'Tailscale down on this Mac' · owner orchestrator · "
    "fix: launchctl kickstart gui/$(id -u)/com.carr.tailscale-up "
    "(or config-as-code.py install --apply if the agent is missing; a logged-out node needs "
    "Joe to sign in at the Tailscale menu) · verify: Tailscale status --json reports Running and "
    "`ssh macbook true` succeeds · auto-clear: the next health run reads a running node"
)


def classify(output: str, returncode: int) -> tuple[str, str]:
    """Validate the local backend, independent of the number of peers.

    Summaries are fixed vocabulary: daemon output may contain sign-in URLs,
    identifiers or credentials and must never reach a persistent health log.
    """
    try:
        data = json.loads(output)
    except (ValueError, TypeError):
        return "error", f"invalid status response (exit {returncode})"
    if not isinstance(data, dict):
        return "error", "invalid status object"
    state = data.get("BackendState")
    if state == "Stopped":
        return "stopped", "backend stopped"
    if state in ("NeedsLogin", "NeedsMachineAuth"):
        return "logged_out", "authentication required"
    if state == "Starting":
        return "starting", "backend starting"
    if state == "Running" and returncode == 0:
        node = data.get("Self")
        ips = node.get("TailscaleIPs") if isinstance(node, dict) else None
        if isinstance(ips, list) and ips:
            try:
                for address in ips:
                    if not isinstance(address, str):
                        raise ValueError("invalid address")
                    ipaddress.ip_address(address)
            except ValueError:
                return "error", "invalid local node addresses"
            return "running", "local backend ready"
    return "error", f"unready or invalid status (exit {returncode})"


def health_row(*, status: str, summary: str) -> tuple[str, bool]:
    """Render the row. Returns (line, failed)."""
    if status == "running":
        return f"  OK {LABEL:<18} running, {summary} · {BOUND_ACTION}", False
    if status == "absent":
        return (f"  ·  {LABEL:<18} app not installed on this Mac — row skipped · "
                f"{BOUND_ACTION}"), False
    word = {"stopped": "STOPPED", "logged_out": "LOGGED OUT", "starting": "STARTING"}.get(status, "UNREADABLE")
    return (f"  ✗✗ {LABEL:<18} {word} ({summary}) — SSH to the other Mac is cut off · "
            f"{BOUND_ACTION}"), True


def row(binary: str = TAILSCALE_BIN) -> tuple[str, bool]:
    """Run `Tailscale status` and render its row."""
    if not os.path.exists(binary):
        return health_row(status="absent", summary="")
    try:
        p = _run(binary, ["status", "--json"], 20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return health_row(status="error", summary=type(e).__name__)
    status, summary = classify(p.stdout, p.returncode)
    return health_row(status=status, summary=summary)


def _run(binary: str, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Bound the entire CLI process group, including daemon/API setup."""
    argv = [binary, *args]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", start_new_session=True)
    except OSError:
        return subprocess.CompletedProcess(argv, 127, "", "")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return subprocess.CompletedProcess(argv, 124, "", "")
    return subprocess.CompletedProcess(argv, proc.returncode, stdout or "", stderr or "")


def recover(binary: str = TAILSCALE_BIN, *, status_timeout: float = 20,
            up_timeout: float = 60, retry_delay: float = 5) -> int:
    """One-shot login recovery; Running is a no-op and sign-in is refused."""
    def report(message: str) -> None:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} tailscale-up: {message}", flush=True)

    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        report("app not installed; nothing to do")
        return 0
    for attempt in range(6):
        p = _run(binary, ["status", "--json"], status_timeout)
        if p.returncode == 124:
            report("status timed out (exit 124)")
            return 124
        state, summary = classify(p.stdout, p.returncode)
        if state != "error" or p.returncode == 0 or attempt == 5:
            break
        time.sleep(retry_delay)
    if state == "running":
        report("already running; nothing to do")
        return 0
    if state == "logged_out":
        report("authentication required; not running up")
        return 3
    if state == "stopped":
        report("stopped; running up")
        # Explicit CLI flags disable up's preserve-existing-settings shortcut.
        # The process watchdog supplies the bound without changing preferences.
        result = _run(binary, ["up"], up_timeout)
        if result.returncode == 124:
            report("up timed out (exit 124)")
        elif result.returncode and any(token in (result.stdout + result.stderr).lower()
                for token in ("log in", "authentication", "https://login.tailscale.com/")):
            report(f"authentication required; up exited {result.returncode}; output withheld")
        else:
            report(f"up exited {result.returncode}; output withheld")
        return result.returncode
    report(f"status unavailable: {summary}")
    return p.returncode or 1


if __name__ == "__main__":
    import sys
    binary = os.environ.get("TAILSCALE_BIN", TAILSCALE_BIN)
    if sys.argv[1:] == ["--recover"]:
        sys.exit(recover(binary))
    line, failed = row(binary)
    print(line)
    sys.exit(1 if failed else 0)
