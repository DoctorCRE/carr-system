#!/usr/bin/env python3
"""auth_control.py — desk sign-in state, and the one-click reconnect the Model
Room observatory drives (complete spec section 17, Joe's order: he must SEE
which models are signed in, and a button starts the sign-in without being
asked).

TWO HALVES, both deliberately small.

  DETECTION. Each poll cycle asks each desk's own vendor CLI whether it is
  signed in — `claude auth status` for a Claude desk, the Codex CLI's status
  read for a Codex desk — and the answer is stamped onto the desk registry so
  the heartbeat receipt can carry it onto the wire. THREE-VALUED ON PURPOSE:
  True, False, or None for "the probe could not answer". A missing binary, a
  timeout, or output this file does not recognise is NOT evidence that a desk
  is signed out, and reporting it as such would paint the panel red on every
  machine that happens not to have one of the CLIs installed (rule 88e9b5eb —
  "not authorized" and "not possible" are different findings). The panel
  renders None as unknown, never as urgent.

  EXECUTION. The panel's RECONNECT button posts a control turn onto the wire;
  this file decides whether the bridge may act on it, and then acts.

THE ALLOWLIST IS THE WHOLE SECURITY MODEL, so it is stated once, here, and
every clause is checked before anything is launched:

  * action must be exactly "login". Nothing else is executable, and an
    unrecognised action is refused with its name in the receipt rather than
    ignored — a control nobody answers looks identical to a bridge that is
    down.
  * the desk must already be REGISTERED. A control turn can only ever address
    a desk a human registered on this machine; it can never name a new one
    into existence, and it can never name a path or a command.
  * the sponsor must be a human partner, server-derived. The room stamps
    sponsor from the verified credential (partner-room.js's
    personalScopeForActor), so the panel cannot fake it — and the seat must be
    "human" as well, which is the same judgment boundary grammar.py already
    draws: a model seat echoing a control must never be treated as an
    instruction.
  * at most one login launch per desk per LOGIN_THROTTLE_S, persisted in the
    bridge's state file, so a stuck panel or a double click cannot open a
    browser window every thirty seconds.

THE BOUNDARY, ABSOLUTE. This file never reads, stores, relays, logs or types a
credential. It launches the vendor's own sign-in flow, which opens the
browser on this Mac, and the human approves there. There is no code path here
that could carry a secret even if one were handed to it.

AND THE RESTART, which is a measured fact from 2026-08-22 rather than a
precaution: a running desk holds its token in memory, so a desk that was
signed out when its process started stays broken after the human signs in.
The bridge therefore records that a desk is awaiting a login, notices the
probe flip to signed-in on a later cycle, and restarts that desk's process
through the registry's own start contract (dispatch.desk_stop then
dispatch.desk_start) — never by inventing a command line of its own.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone

CONTROL_ALLOWED_ACTIONS = frozenset({"login"})
# The two humans this system has. Deliberately a module constant and NOT
# anything a state file, a registry entry or a turn body could widen.
HUMAN_PARTNERS = frozenset({"joe", "dell"})
AUTHORIZED_CONTROL_SEATS = frozenset({"human"})
LOGIN_THROTTLE_S = 600.0
# TIGHT ON PURPOSE, and the number comes from the service's own cadence rather
# than from taste: com.carr.room-bridge wakes every 60 seconds, and every
# registered desk is probed each cycle. At eight seconds a machine with four
# desks spends at most ~32s in probes even when every vendor CLI hangs, which
# leaves the cycle's real work — routing, delivery and reply capture — inside
# the wake interval. A probe that needs longer than eight seconds to say whether
# a token exists locally has already told us it cannot answer.
PROBE_TIMEOUT_S = 8.0

# The vendor's OWN commands, per desk kind. A kind absent from these maps is
# refused by name rather than guessed at: launching the wrong binary at a
# sign-in prompt is exactly the class of mistake an allowlist exists to stop.
AUTH_STATUS_COMMANDS = {
    "grok-cli": ["grok", "models"],
    "claude-session": ["claude", "auth", "status"],
    "claude-desktop": ["claude", "auth", "status"],
    "codex-session": ["codex", "login", "status"],
    "codex-live": ["codex", "login", "status"],
}
AUTH_LOGIN_COMMANDS = {
    "claude-session": ["claude", "auth", "login", "--claudeai"],
    "claude-desktop": ["claude", "auth", "login", "--claudeai"],
    "codex-session": ["codex", "login"],
    "codex-live": ["codex", "login"],
}

_SIGNED_OUT = re.compile(r"not\s+logged\s+in|logged\s+out|not\s+signed\s+in|no\s+credentials|please\s+run\s+.*login",
                         re.IGNORECASE)
_SIGNED_IN = re.compile(r"logged\s+in|signed\s+in|authenticated\s+as", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _elapsed_seconds(iso_ts: str | None, *, now: str | None = None) -> float:
    if not iso_ts:
        return 0.0
    try:
        t = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    ref = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - t).total_seconds()


def parse_auth_output(text: str) -> bool | None:
    """True / False / None from a vendor status command's output.

    JSON first, because `claude auth status` speaks it and a parsed field beats
    a regex over prose every time. Otherwise the two phrase families above,
    signed-OUT checked first so "not logged in" can never be read as "logged
    in" by a substring match. Anything else is None — unrecognised output is a
    gap in this parser, not a finding about the desk."""
    blob = (text or "").strip()
    if not blob:
        return None
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        for key in ("loggedIn", "logged_in", "isLoggedIn", "authenticated"):
            if isinstance(parsed.get(key), bool):
                return parsed[key]
    if _SIGNED_OUT.search(blob):
        return False
    if _SIGNED_IN.search(blob):
        return True
    return None


def probe_auth(entry: dict, *, run=subprocess.run, timeout: float = PROBE_TIMEOUT_S) -> bool | None:
    """Ask one desk's vendor CLI whether it is signed in. Never raises: every
    failure mode collapses to None, which the panel renders as unknown."""
    command = AUTH_STATUS_COMMANDS.get(str(entry.get("kind") or ""))
    if not command:
        return None
    try:
        proc = run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    answer = parse_auth_output(f"{getattr(proc, 'stdout', '') or ''}\n{getattr(proc, 'stderr', '') or ''}")
    if answer is not None:
        return answer
    # A status command that exits clean with output this parser does not know
    # is still not proof of anything, so it stays None rather than becoming a
    # confident guess from the exit code alone.
    return None


def parse_control(turn: dict) -> dict | None:
    """The control object inside a control turn, or None when the turn is not
    one. A control turn is kind="receipt" whose whole body is JSON carrying a
    top-level "control" object — the same machine-readable receipt shape the
    assignment grammar already uses, so nothing new has to be taught to a
    reader of the wire."""
    if turn.get("kind") != "receipt":
        return None
    try:
        payload = json.loads(str(turn.get("body") or ""))
    except (json.JSONDecodeError, TypeError):
        return None
    control = payload.get("control") if isinstance(payload, dict) else None
    return control if isinstance(control, dict) else None


def classify_control(turn: dict, control: dict, *, registered: set[str], state,
                     now: str | None = None,
                     throttle_s: float = LOGIN_THROTTLE_S) -> tuple[str, str | None]:
    """("ok", None) or ("refused", reason). Every clause of the allowlist, in
    the order a reader would want to see it refused."""
    action = str(control.get("action") or "")
    if action not in CONTROL_ALLOWED_ACTIONS:
        return "refused", (f"action {action!r} is not executable "
                           f"(allowed: {sorted(CONTROL_ALLOWED_ACTIONS)})")

    seat = str(turn.get("seat") or "")
    sponsor = str(turn.get("sponsor") or "")
    if seat not in AUTHORIZED_CONTROL_SEATS:
        return "refused", f"seat {seat!r} may not issue a control; only a person may"
    if sponsor not in HUMAN_PARTNERS:
        return "refused", f"sponsor {sponsor!r} is not a human partner of this system"

    desk = str(control.get("desk") or "")
    if desk not in registered:
        return "refused", f"desk {desk!r} is not registered on this machine"

    last = (state.get("control_logins") or {}).get(desk)
    if last and _elapsed_seconds(last, now=now) < throttle_s:
        return "refused", (f"a login for {desk!r} was already launched at {last}; "
                           f"at most one per {int(throttle_s)}s")
    return "ok", None


def note_login_launched(state: dict, desk: str, when: str) -> None:
    state.setdefault("control_logins", {})[desk] = when
    state.setdefault("awaiting_login", {})[desk] = when


def clear_awaiting_login(state: dict, desk: str) -> None:
    state.setdefault("awaiting_login", {}).pop(desk, None)


def awaiting_login(state: dict) -> dict:
    return state.setdefault("awaiting_login", {})


def launch_login(name: str, entry: dict, *, run=subprocess.Popen) -> dict:
    """Start the vendor's own sign-in flow for one desk and return immediately.

    NOT captured, NOT piped, NOT waited on: the flow opens a browser and the
    human approves there. This function's entire job is to press the vendor's
    button; it has no channel through which a credential could reach it."""
    command = AUTH_LOGIN_COMMANDS.get(str(entry.get("kind") or ""))
    if not command:
        return {"launched": False, "reason": f"desk kind {entry.get('kind')!r} has no known sign-in command"}
    try:
        run(command)
    except (OSError, subprocess.SubprocessError) as e:
        return {"launched": False, "reason": f"could not start {command[0]!r}: {e}"}
    return {"launched": True, "command": command[0], "at": _now()}


def restart_desk(name: str, *, registry, stop, start) -> dict:
    """Kill and respawn one desk through the registry's OWN start contract.

    A desk that was signed out when it started holds that broken token in
    memory for its whole life, so signing in without a restart fixes nothing —
    measured 2026-08-22. stop/start are injected (dispatch.desk_stop /
    dispatch.desk_start) so this is provable without a live session."""
    try:
        stop(name)
        started = start(name, registry=registry)
    except Exception as e:  # noqa: BLE001 — a restart failure is a receipt, never a crashed cycle
        return {"restarted": False, "reason": f"{type(e).__name__}: {e}"}
    return {"restarted": True, "detail": started}
