#!/usr/bin/env python3
from __future__ import annotations

import os
import json
import ipaddress
import subprocess
import time
import signal
import re

TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
ROUTE_BIN = "/sbin/route"
LABEL = "tailscale"

BOUND_ACTION = (
    "on breach: open/update one dedup loop 'Tailscale down on this Mac' · owner orchestrator · "
    "fix: launchctl kickstart gui/$(id -u)/com.carr.tailscale-up "
    "(or config-as-code.py install --apply if the agent is missing; a logged-out node needs "
    "Joe to sign in at the Tailscale menu) · verify: Tailscale status --json reports Running, "
    "route -n get 100.64.0.1 resolves through utun, and `ssh macbook true` succeeds · "
    "auto-clear: the next health run reads Running with the utun route restored"
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
    word = {"stopped": "STOPPED", "logged_out": "LOGGED OUT", "starting": "STARTING",
            "route_missing": "ROUTE MISSING"}.get(status, "UNREADABLE")
    return (f"  ✗✗ {LABEL:<18} {word} ({summary}) — SSH to the other Mac is cut off · "
            f"{BOUND_ACTION}"), True


def row(binary: str = TAILSCALE_BIN) -> tuple[str, bool]:
    """Read backend readiness and the route ordinary TCP traffic uses."""
    if not os.path.exists(binary):
        return health_row(status="absent", summary="")
    try:
        p = _run(binary, ["status", "--json"], 20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return health_row(status="error", summary=type(e).__name__)
    status, summary = classify(p.stdout, p.returncode)
    if status == "running":
        status, summary = route_state()
    return health_row(status=status, summary=summary)


def route_state(timeout: float = 10) -> tuple[str, str]:
    result = _run(os.environ.get("TAILSCALE_ROUTE_BIN", ROUTE_BIN),
                  ["-n", "get", "100.64.0.1"], timeout)
    interfaces = re.findall(r"^\s*interface:\s*(\S+)\s*$", result.stdout, re.MULTILINE)
    if result.returncode == 0 and len(interfaces) == 1 and re.fullmatch(r"utun[0-9]+", interfaces[0]):
        return "running", "tailnet route through utun"
    return "route_missing", f"no verified utun route (exit {result.returncode})"


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
            up_timeout: float = 60, retry_delay: float = 5,
            route_timeout: float = 10) -> int:
    """Start a stopped node or reconnect once for a lost route; never prompt."""
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
        state, summary = route_state(route_timeout)
        if state == "running":
            report("already running with utun route; nothing to do")
            return 0
    if state == "logged_out":
        report("authentication required; not running up")
        return 3
    if state in ("stopped", "route_missing"):
        if state == "route_missing":
            report("route missing; reconnecting once with down then up")
            down = _run(binary, ["down"], up_timeout)
            report(f"down exited {down.returncode}; output withheld")
        else:
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
        verified = _run(binary, ["status", "--json"], status_timeout)
        state, summary = classify(verified.stdout, verified.returncode)
        if state == "running":
            state, summary = route_state(route_timeout)
        if state != "running":
            report(f"repair failed: {summary}")
            print(health_row(status=state, summary=summary)[0], flush=True)
            return result.returncode or 1
        report("repair verified: Running with utun route restored")
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
