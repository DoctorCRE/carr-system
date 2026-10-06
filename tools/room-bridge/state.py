#!/usr/bin/env python3
"""state.py — the room bridge's own memory: how far it has read the room, and
what each desk has already seen or is waiting on. Pure logic, no socket, no
subprocess, no network — the acceptance bar carried over from
tools/partner-line/watch.py's poll_once() design (fetch/injector/notifier as
plain parameters), extended here to state itself: every function below takes
a state dict and returns a new fact about it, so bridge.py's run_once() is the
only place that touches a file.

WHY A STATE FILE AND NOT JUST THE ROOM'S OWN CURSOR. read-room already gives a
monotonic seq and a `latest_seq` cursor — that is enough to never re-READ a
turn. It is not enough to know whether a turn has already been DELIVERED to a
given desk: a claude-session desk answers asynchronously (dispatch.py's own
docstring: "the desk answers in its own window, and this file does not carry
that back"), so a turn can be delivered in one poll cycle and its reply not
land until several cycles later. The bridge is invoked periodically by
launchd (a short poll-and-exit cycle, matching every other launchd service in
this repo — see ops/config/services.json), so this state has to survive
between invocations or a restart mid-wait would silently drop the desk's
answer.

SHAPE, persisted as JSON:
{
  "last_seq": 0,                # the room cursor — every turn up to and
                                 # including this seq has been considered
  "desks": {
    "<name>": {
      "delivered": ["<msg_id>", ...],   # bounded — turns already queued or
                                         # delivered to THIS desk; the echo/
                                         # duplicate guard
      "queue": [ {"msg_id","seat","body","seq"}, ... ],  # not yet injected
      "pending": null | {
        "dispatch_msg_id": "...",   # dispatch.py's own msg_id for the
                                     # delivered turn — the correlation key
        "log_offset": 0,            # byte offset into the desk's log BEFORE
                                     # injection; where reply-scanning starts
        "injected_at": "iso",
        "source_msg_id": "...",     # the room turn's msg_id this answers
        "source_seq": 0
      }
    }
  },
  "last_heartbeat_at": null,     # when this bridge last posted its own
                                  # heartbeat receipt to the room (the
                                  # observatory's only source of desk state);
                                  # null until the first one goes out
  "control_logins": {"<desk>": "iso"},   # last sign-in flow launched per desk;
                                          # THE THROTTLE for the observatory's
                                          # RECONNECT button, persisted here
                                          # because the bridge exits between
                                          # cycles and an in-memory timer would
                                          # never fire twice
  "awaiting_login": {"<desk>": "iso"}    # a login was launched and this desk is
                                          # waiting for the probe to read signed
                                          # in, at which point its PROCESS is
                                          # restarted (a running desk holds its
                                          # token in memory)
}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DELIVERED_CAP = 4000  # per desk; oldest dropped first — a dedup memory, not an audit log
QUEUE_RETRY_CAP = 400  # timing hints only; canonical attempt evidence remains in Hermes
QUEUE_UNAVAILABLE_CAP = 128  # bounded migration-safe task -> first-known-dead time


def default_state() -> dict:
    return {"last_seq": 0, "desks": {}, "last_heartbeat_at": None,
            "control_logins": {}, "awaiting_login": {},
            "queue_event_cursor": 0, "queue_projection_digest": None,
            "queue_projection_checked_at": None,
            "queue_projection_last_success_at": None,
            "queue_projection_health_last_posted_at": None,
            "queue_projection_error": None,
            "queue_retry_at": {}, "queue_unavailable_since": {}}


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state()
    data.setdefault("last_seq", 0)
    data.setdefault("desks", {})
    # A state file written before the observatory heartbeat existed has no such
    # key; absent means "never posted", which correctly makes the first cycle
    # after an upgrade publish one immediately.
    data.setdefault("last_heartbeat_at", None)
    data.setdefault("control_logins", {})
    data.setdefault("awaiting_login", {})
    data.setdefault("queue_event_cursor", 0)
    data.setdefault("queue_projection_digest", None)
    data.setdefault("queue_projection_checked_at", None)
    data.setdefault("queue_projection_last_success_at", None)
    data.setdefault("queue_projection_health_last_posted_at", None)
    data.setdefault("queue_projection_error", None)
    unavailable = data.get("queue_unavailable_since")
    if not isinstance(unavailable, dict):
        unavailable = {}
    # Keep only the intentionally tiny migration field.  Timestamps are
    # validated at the dispatch boundary as well; malformed values must never
    # become an immediate deadline.
    data["queue_unavailable_since"] = {
        desk: value for desk, value in unavailable.items()
        if isinstance(desk, str) and len(desk) <= 80
        and isinstance(value, str) and len(value) <= 64
    }
    while len(data["queue_unavailable_since"]) > QUEUE_UNAVAILABLE_CAP:
        data["queue_unavailable_since"].pop(next(iter(data["queue_unavailable_since"])))
    retry_at = data.get("queue_retry_at")
    if not isinstance(retry_at, dict):
        retry_at = {}
    data["queue_retry_at"] = {
        task_id: due for task_id, due in retry_at.items()
        if isinstance(task_id, str) and task_id.startswith("t_")
        and isinstance(due, str) and len(due) <= 64
    }
    while len(data["queue_retry_at"]) > QUEUE_RETRY_CAP:
        data["queue_retry_at"].pop(next(iter(data["queue_retry_at"])))
    return data


def set_queue_retry_at(state: dict, task_id: str, retry_at: str) -> None:
    scheduled = state.setdefault("queue_retry_at", {})
    if not isinstance(scheduled, dict):
        state["queue_retry_at"] = scheduled = {}
    scheduled[task_id] = retry_at
    while len(scheduled) > QUEUE_RETRY_CAP:
        scheduled.pop(next(iter(scheduled)))


def clear_queue_retry_at(state: dict, task_id: str | None) -> None:
    if isinstance(task_id, str):
        state["queue_retry_at"].pop(task_id, None)


def set_queue_unavailable_since(state: dict, desk_name: str, when: str) -> None:
    values = state.setdefault("queue_unavailable_since", {})
    if not isinstance(values, dict):
        state["queue_unavailable_since"] = values = {}
    values[desk_name] = when
    while len(values) > QUEUE_UNAVAILABLE_CAP:
        values.pop(next(iter(values)))


def clear_queue_unavailable_since(state: dict, task_id: str | None) -> None:
    if isinstance(task_id, str):
        state.setdefault("queue_unavailable_since", {}).pop(task_id, None)


def prune_queue_unavailable_since(state: dict, ready_task_ids: set[str]) -> None:
    values = state.setdefault("queue_unavailable_since", {})
    if isinstance(values, dict):
        for task_id in list(values):
            if task_id not in ready_task_ids:
                values.pop(task_id, None)


def get_heartbeat_at(state: dict) -> str | None:
    return state.get("last_heartbeat_at")


def set_heartbeat_at(state: dict, when: str) -> None:
    state["last_heartbeat_at"] = when


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


def _desk_slot(state: dict, name: str) -> dict:
    return state["desks"].setdefault(
        name, {"delivered": [], "queue": [], "pending": None}
    )


def advance_seq(state: dict, turns: list[dict]) -> int:
    """The room cursor advances past every turn considered, delivered or not —
    same stance as partner-line/watch.py's next_watermark(): a skipped row is
    still successfully accounted for, or it is re-fetched and re-classified
    forever."""
    seqs = [int(t.get("seq", state["last_seq"])) for t in turns]
    state["last_seq"] = max([state["last_seq"], *seqs]) if seqs else state["last_seq"]
    return state["last_seq"]


def is_echo(turn: dict, desk_seat: str) -> bool:
    """A turn IS this desk's own echo when the seat that spoke it is the same
    seat this desk speaks for — never re-inject a desk's own words back at it.
    """
    return str(turn.get("seat") or "") == str(desk_seat or "")


def is_unaddressed_desk_turn(turn: dict, desk_seat: str, desk_seats: dict[str, str]) -> bool:
    """For a desk registered room_listen="mention": a turn spoken by ANOTHER
    desk is unaddressed unless it names this desk's seat as a whole-word
    @-mention. Turns from people are never unaddressed. Desks on the default
    ("all") keep hearing other desks — desk-to-desk conversation is by design.

    WHY, found live 2026-09-24: with flash and codex both seated and both
    auto-answering, each desk's reply was routed to the other and answered,
    about once a minute, until a seat was stopped. is_echo only stops a desk
    hearing itself; a mention-only desk cannot be one half of such a loop.
    """
    speaker = str(turn.get("seat") or "")
    if speaker not in set(desk_seats.values()):
        return False
    mention = re.compile(r"(?<![\w@])@" + re.escape(str(desk_seat)) + r"(?![\w-])")
    return not mention.search(str(turn.get("body") or ""))


def already_delivered(state: dict, desk_name: str, msg_id: str) -> bool:
    return msg_id in _desk_slot(state, desk_name)["delivered"]


def mark_delivered(state: dict, desk_name: str, msg_id: str) -> None:
    slot = _desk_slot(state, desk_name)
    if msg_id in slot["delivered"]:
        return
    slot["delivered"].append(msg_id)
    if len(slot["delivered"]) > DELIVERED_CAP:
        slot["delivered"] = slot["delivered"][-DELIVERED_CAP:]


def route_turn(state: dict, turn: dict, desk_seats: dict[str, str], *,
               mention_only: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Queue one new room turn onto every desk it is eligible for.

    desk_seats maps desk name -> the room seat that desk speaks for. A turn is
    queued onto a desk when: its kind is "turn" (system/receipt rows are the
    bridge's own bookkeeping — visible in the room's transcript, but never a
    task for a desk to answer; see the module docstring below for why this
    matters more than it looks), the turn is not that desk's own echo
    (is_echo), and this bridge has not already delivered/queued this exact
    msg_id to that desk before (already_delivered) — the two-part guard the
    brief calls for: "echo suppression by msg_id + seat". Returns the names
    of desks the turn was queued onto, for logging.

    WHY THE KIND FILTER, found live: before it existed, a kind=receipt turn
    posted under seat="hermes" (e.g. "codex-desk failed: quota_exhausted")
    routed to EVERY desk exactly like an ordinary turn, since "hermes" is
    nobody's room_seat — including the desk the receipt was ABOUT, which then
    burned a real dispatch trying to "answer" a report of its own prior
    failure. Not an infinite loop (the desk's own reply carries its own seat,
    which the next cycle's is_echo correctly excludes from re-delivery to
    itself) but genuine wasted cost and noise for a message nobody was asking
    a desk to act on.
    """
    if turn.get("kind") != "turn":
        return []
    msg_id = str(turn.get("msg_id") or "")
    if not msg_id:
        return []
    queued_onto: list[str] = []
    for name, seat in desk_seats.items():
        if is_echo(turn, seat):
            continue
        if seat == "flash" and not re.search(r"(?<![\w@])@flash(?![\w-])", str(turn.get("body") or ""), re.I):
            continue
        if name in mention_only and is_unaddressed_desk_turn(turn, seat, desk_seats):
            continue
        if already_delivered(state, name, msg_id):
            continue
        slot = _desk_slot(state, name)
        slot["queue"].append({
            "msg_id": msg_id,
            "seat": turn.get("seat"),
            "body": turn.get("body"),
            "seq": turn.get("seq"),
        })
        mark_delivered(state, name, msg_id)
        queued_onto.append(name)
    return queued_onto


def has_pending(state: dict, desk_name: str) -> bool:
    return _desk_slot(state, desk_name)["pending"] is not None


def pop_next_queued(state: dict, desk_name: str) -> dict | None:
    slot = _desk_slot(state, desk_name)
    if not slot["queue"]:
        return None
    return slot["queue"].pop(0)


def has_queued(state: dict, desk_name: str) -> bool:
    return bool(_desk_slot(state, desk_name)["queue"])


def set_pending(state: dict, desk_name: str, *, dispatch_msg_id: str,
                 log_offset: int, injected_at: str, source_msg_id: str,
                 source_seq, origin_kind: str = "room",
                 kanban_task_id: str | None = None, target: str | None = None,
                 finish: str | None = None, cap: str | None = None,
                 transport: str | None = None,
                 session_id: str | None = None) -> None:
    slot = _desk_slot(state, desk_name)
    slot["pending"] = {
        "dispatch_msg_id": dispatch_msg_id,
        "log_offset": log_offset,
        "injected_at": injected_at,
        "source_msg_id": source_msg_id,
        "source_seq": source_seq,
        "origin_kind": origin_kind,
    }
    if origin_kind == "queue":
        slot["pending"].update({
            "kanban_task_id": kanban_task_id,
            "target": target,
            "finish": finish,
            "cap": cap,
        })
    if transport:
        slot["pending"]["transport"] = transport
    if session_id:
        slot["pending"]["session_id"] = session_id


def clear_pending(state: dict, desk_name: str) -> None:
    _desk_slot(state, desk_name)["pending"] = None


def get_pending(state: dict, desk_name: str) -> dict | None:
    return _desk_slot(state, desk_name)["pending"]
