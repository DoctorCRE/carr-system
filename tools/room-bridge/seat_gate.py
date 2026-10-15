"""Dispatch refuses a seat that is offline or just failed.

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

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "ops") not in sys.path:
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
    at, fresh = seat_health.observed(row, now)
    if not fresh or seat_health.dispatchable(
            {"schema": "carr-seat-health/v1", "seats": {seat: row}}, seat, now=now):
        return None
    layer = row.get("failing_layer") if row.get("failing_layer") in seat_health.FAILURE_LAYERS else "unknown"
    return ("seat_failed",
            f"dispatch refused: {name} is seat {seat}, whose probe at "
            f"{at.isoformat(timespec='minutes')} failed (layer {layer}). "
            f"{seat_health.action(seat, layer if layer != 'unknown' else 'runner_wrapper')} "
            f"Then rerun ops/seat-health.py --seat {seat} --force; a passing probe readmits it.")


class SendReads:

    def __init__(self, config: dict):
        self.config = config
        self._live: dict | None = None
        self._report: dict | None = None

    def live(self) -> dict:
        if self._live is None:
            self._live = host_liveness.observe(config=self.config)
        return self._live

    def report(self) -> dict:
        if self._report is None:
            self._report = seat_health.current_report(REPO)
        return self._report


def check(entry: dict):
    """The send-time gate. Any failure to read its inputs (a missing or
    malformed ops/config/seat-hosts.v1.json above all) is a refusal naming
    the file and the error class, never a traceback out of dispatch.main."""
    try:
        reads = SendReads(host_liveness.load_config())
        return refusal(entry, reads.config, reads.live(), reads.report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return ("seat_gate_unreadable",
                f"dispatch refused: the seat gate could not read {host_liveness.CONFIG} or the seat "
                f"evidence ({type(exc).__name__}). Nothing was launched. Restore that file "
                f"(schema carr-seat-hosts/v1) and rerun; python3 ops/host_liveness.py shows the error.")
