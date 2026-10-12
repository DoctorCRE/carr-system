"""Dispatch refuses a seat that is offline or just failed (2026-10-12).

`dispatch.py send` asked one question before this: is the desk registered? It
never asked whether the Mac the work would run on was up, or whether that
seat's last exact-value probe had failed. With the Mac Studio offline for days,
a send to a Studio desk would have waited out its SSH timeout and recorded a
failure that blamed the desk.

Two refusals, both with the reason in the message:

  seat_host_offline  the desk runs on a host the tailnet reports offline
                     (ops/host_liveness.py; unknown liveness never refuses)
  seat_failed        the desk's seat ran its probe inside the 24-hour window
                     and failed (ops/seat_health.py; record layer first, this
                     Mac's report second). A missing or expired probe is STALE
                     and does not refuse: no evidence is not bad evidence.

Which seat a desk is: a claude-remote desk runs on its SSH host (opus-studio
when that host is the Studio); a Codex desk runs on THIS Mac, so it is the
`sol-*` seat whose host is this Mac; a Grok desk is the grok seat when this Mac
is the grok host. Every other kind is a live local session and has no seat.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "ops") not in sys.path:
    # Appended, never prepended: ops/ must not shadow a room-bridge module.
    sys.path.append(str(REPO / "ops"))

import codex_models  # noqa: E402
import host_liveness  # noqa: E402
import seat_health  # noqa: E402


def target(entry: dict, config: dict, live: dict) -> tuple[str | None, str | None]:
    """(seat, host) the desk's work runs on; either may be None."""
    kind, me = entry.get("kind"), live.get("self")
    if kind == "claude-remote":
        host = host_liveness.resolve_host(entry.get("host"), config)
        seat = "opus-studio" if host and host == host_liveness.seat_host("opus-studio", config) else None
        return seat, host
    if kind in codex_models.CODEX_KINDS:
        seats = [s for s in config["seats"] if s.startswith("sol-")
                 and me and host_liveness.seat_host(s, config) == me]
        return (seats[0] if len(seats) == 1 else None), me
    if kind == "grok-cli":
        return ("grok" if me and host_liveness.seat_host("grok", config) == me else None), me
    return None, me


def refusal(entry: dict, config: dict, live: dict, report_fn, *, now=None):
    """(code, message) when the desk must not be sent work, else None.

    `report_fn()` returns a carr-seat-health/v1 report and is called only when
    the desk maps to a seat, so a live local session costs no record read."""
    seat, host = target(entry, config, live)
    name = entry.get("name") or "this desk"
    state = live["hosts"].get(host, {}) if host else {}
    if state.get("state") == "offline":
        return ("seat_host_offline",
                f"dispatch refused: {name} runs on host {host}, which the tailnet reports offline "
                f"since {state.get('last_seen') or 'never seen'}. Wake {host} (see the host row in "
                f"./run.sh health) or send to a desk on a live Mac.")
    if not seat:
        return None
    now = now or datetime.now(timezone.utc)
    row = seat_health.local_seats(report_fn()).get(seat)
    row = row if isinstance(row, dict) else {}
    at, fresh = seat_health._observed(row, now)
    if not fresh or seat_health.dispatchable(
            {"schema": "carr-seat-health/v1", "seats": {seat: row}}, seat, now=now):
        return None
    layer = row.get("failing_layer") if row.get("failing_layer") in seat_health.FAILURE_LAYERS else "unknown"
    return ("seat_failed",
            f"dispatch refused: {name} is seat {seat}, whose probe at "
            f"{at.strftime('%Y-%m-%dT%H:%MZ')} failed (layer {layer}). "
            f"{seat_health.action(seat, layer if layer != 'unknown' else 'runner_wrapper')} "
            f"Then rerun ops/seat-health.py --seat {seat} --force; a passing probe readmits it.")


def report(repo: Path = REPO) -> dict:
    """Seat evidence: the record layer first, this Mac's report second."""
    try:
        local = json.loads((repo / "out/orch/budget/seat-health.json").read_text())
    except (OSError, ValueError):
        local = {}
    dashboard = seat_health.read_dashboard(
        lambda name, payload: seat_health.verb_call(repo, name, payload))
    return seat_health.combined_report(dashboard, local)


def check(entry: dict):
    """The send-time gate: live tailnet read, lazy seat evidence."""
    config = host_liveness.load_config()
    return refusal(entry, config, host_liveness.observe(config=config), report)
