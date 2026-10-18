#!/usr/bin/env python3
"""SHAPE, persisted as JSON:
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

DELIVERED_CAP = 4000
QUEUE_RETRY_CAP = 400
QUEUE_UNAVAILABLE_CAP = 128


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
    """The room cursor advances past every turn considered, delivered or not."""
    seqs = [int(t.get("seq", state["last_seq"])) for t in turns]
    state["last_seq"] = max([state["last_seq"], *seqs]) if seqs else state["last_seq"]
    return state["last_seq"]


def is_echo(turn: dict, desk_seat: str) -> bool:
    return str(turn.get("seat") or "") == str(desk_seat or "")


def is_unaddressed_desk_turn(turn: dict, desk_seat: str, desk_seats: dict[str, str]) -> bool:
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
    queued onto a desk when: its kind is "turn", the turn is not that desk's own echo
    (is_echo), and this bridge has not already delivered/queued this exact
    msg_id to that desk before (already_delivered). Returns the names
    of desks the turn was queued onto, for logging.

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
