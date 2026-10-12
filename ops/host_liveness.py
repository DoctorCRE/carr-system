"""Host liveness: is each Mac a builder seat runs on reachable on the tailnet?

WHY (2026-10-12). Every seat row read FAIL layer=runner_wrapper for a week.
Nothing about the wrappers was wrong: the Mac Studio, where four of the six
seats live, was offline on the tailnet ("offline, last seen 2d ago") and the
daily probe that runs there had not run since 2026-10-05. Six red seat rows
named six wrong remedies; the one true fact, "the Studio is off", appeared in
none of them. ops/tailscale_health.py checks only this Mac's own backend, so a
peer could be gone for days with every Tailscale row green.

This module reads `Tailscale status --json` once and answers, for every host in
ops/config/seat-hosts.v1.json: online, offline since <LastSeen>, or unknown.
One health row per host; a seat whose host is offline reads SKIP instead of a
FAIL that blames its wrapper (ops/seat_health.health_rows), and dispatch refuses
to send work to it (tools/room-bridge/seat_gate.py).

UNKNOWN IS NOT OFFLINE. No Tailscale app, a stopped or logged-out backend, an
unreadable status, or a host absent from the peer list all yield "unknown":
no RED row and no SKIP. The local backend's own health already has a row
(ops/tailscale_health.py); this one only speaks when the backend is Running
and positively reports the peer Online=false. Daemon output never reaches a
row: only the configured host name, a boolean and a timestamp do.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "ops" / "config" / "seat-hosts.v1.json"
TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
LABEL = "host"

_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})")


def load_config(path: str | os.PathLike | None = None) -> dict:
    data = json.loads(Path(path or CONFIG).read_text())
    if data.get("schema") != "carr-seat-hosts/v1" or not isinstance(data.get("hosts"), dict) \
            or not isinstance(data.get("seats"), dict):
        raise ValueError("seat-hosts config has the wrong shape")
    return data


def _dns(name: object) -> str:
    return name.rstrip(".").lower() if isinstance(name, str) else ""


def _last_seen(value: object) -> str | None:
    """Tailscale writes 0001-01-01T00:00:00Z for never; keep minute precision."""
    if not isinstance(value, str):
        return None
    match = _STAMP.match(value)
    if not match or match.group(1).startswith("0001"):
        return None
    return f"{match.group(1)}T{match.group(2)}Z"


def read_status(binary: str = TAILSCALE_BIN, timeout: float = 15) -> dict | None:
    """Parsed `status --json`, or None when it cannot be read at all."""
    if not os.path.exists(binary):
        return None
    try:
        proc = subprocess.Popen([binary, "status", "--json"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def liveness(status: dict | None, config: dict) -> dict:
    """{'backend': state, 'self': host|None, 'hosts': {name: {...}}} for every configured host.

    Each host row is {'state': 'online'|'offline'|'unknown', 'last_seen': str|None,
    'self': bool}. Only a Running backend can say anything but unknown."""
    hosts = {name: {"state": "unknown", "last_seen": None, "self": False}
             for name in config["hosts"]}
    backend = status.get("BackendState") if isinstance(status, dict) else None
    result = {"backend": backend if isinstance(backend, str) else "unreadable",
              "self": None, "hosts": hosts}
    if backend != "Running":
        return result
    by_dns = {_dns(spec.get("dns_name")): name for name, spec in config["hosts"].items()
              if isinstance(spec, dict)}
    me = status.get("Self")
    if isinstance(me, dict) and _dns(me.get("DNSName")) in by_dns:
        name = by_dns[_dns(me.get("DNSName"))]
        hosts[name] = {"state": "online", "last_seen": None, "self": True}
        result["self"] = name
    peers = status.get("Peer")
    for peer in (peers.values() if isinstance(peers, dict) else ()):
        if not isinstance(peer, dict) or _dns(peer.get("DNSName")) not in by_dns:
            continue
        name = by_dns[_dns(peer.get("DNSName"))]
        if hosts[name]["self"] or type(peer.get("Online")) is not bool:
            continue
        hosts[name] = {"state": "online" if peer["Online"] else "offline",
                       "last_seen": _last_seen(peer.get("LastSeen")), "self": False}
    return result


def observe(binary: str | None = None, config: dict | None = None) -> dict:
    config = config or load_config()
    return liveness(read_status(binary or os.environ.get("TAILSCALE_BIN", TAILSCALE_BIN)), config)


def resolve_host(alias: object, config: dict) -> str | None:
    """Map an SSH destination (user@host, alias, FQDN) to a configured host name."""
    if not isinstance(alias, str) or not alias:
        return None
    wanted = alias.rsplit("@", 1)[-1].rstrip(".").lower()
    for name, spec in config["hosts"].items():
        names = {name.lower(), _dns(spec.get("dns_name"))}
        names.update(a.lower() for a in spec.get("aliases", ()) if isinstance(a, str))
        if wanted in names:
            return name
    return None


def seat_host(seat: str, config: dict) -> str | None:
    host = config["seats"].get(seat)
    return host if isinstance(host, str) and host in config["hosts"] else None


def offline_seats(live: dict, config: dict) -> dict[str, str]:
    """{seat: host} for every seat whose host the tailnet reports offline."""
    return {seat: host for seat in config["seats"]
            if (host := seat_host(seat, config))
            and live["hosts"].get(host, {}).get("state") == "offline"}


def action(name: str, config: dict) -> str:
    aliases = config["hosts"][name].get("aliases") or [name]
    return (f"on breach: open/update one dedup loop 'host {name} offline' · owner orchestrator "
            f"(power and network at the Mac are Joe's) · fix: wake or power on {name} and confirm "
            f"Tailscale runs there (launchctl kickstart gui/$(id -u)/com.carr.tailscale-up) · "
            f"verify: Tailscale status --json on this Mac lists {config['hosts'][name]['dns_name']} "
            f"Online and `ssh -o BatchMode=yes {aliases[0]} true` succeeds · auto-clear: the next "
            f"health run reads the peer online")


def rows(live: dict, config: dict) -> list[tuple[str, bool, str]]:
    """[(line, failed, host)] — one per configured host; only a known-offline host fails."""
    out = []
    for name in config["hosts"]:
        row = live["hosts"].get(name, {})
        state = row.get("state", "unknown")
        seats = sorted(seat for seat in config["seats"] if seat_host(seat, config) == name)
        if state == "online":
            where = "this Mac" if row.get("self") else "online on the tailnet"
            line = f"  OK  {LABEL + ' ' + name:<24} {where} · {action(name, config)}"
            out.append((line, False, name))
        elif state == "offline":
            since = row.get("last_seen") or "never seen"
            line = (f"  RED {LABEL + ' ' + name:<24} host offline since {since} — seats skipped: "
                    f"{', '.join(seats) or 'none'} · {action(name, config)}")
            out.append((line, True, name))
        else:
            line = (f"  ·   {LABEL + ' ' + name:<24} liveness unknown (tailnet backend "
                    f"{live.get('backend', 'unreadable')}, or host not in the peer list) — row skipped")
            out.append((line, False, name))
    return out


if __name__ == "__main__":
    import sys
    config = load_config()
    printed = rows(observe(config=config), config)
    for line, _, _ in printed:
        print(line)
    sys.exit(int(any(failed for _, failed, _ in printed)))
