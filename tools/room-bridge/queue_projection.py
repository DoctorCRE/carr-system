#!/usr/bin/env python3
"""Read-only Hermes-to-room Queue projection.

Hermes remains authoritative.  This module opens its live SQLite database with
``mode=ro`` and ``PRAGMA query_only=ON``, turns durable task events into compact
state-complete room receipts, and advances its cursor only after the room
accepts the deterministic receipt id.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BOARD = "carr-build"
DB_PATH = Path(os.environ.get("CARR_HERMES_KANBAN_DB",
    Path.home() / ".hermes" / "kanban" / "boards" / BOARD / "kanban.db"))
EVENT_NAMESPACE = uuid.UUID("42f7b149-34a7-512d-bad1-b9ad6f35d4c4")
HEALTH_NAMESPACE = uuid.UUID("f67c8b09-9f57-5d3a-9b11-33b468cb7ad9")
EVENT_LIMIT = 200
HEALTH_INTERVAL_S = 60


def event_msg_id(board: str, event_id: int) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{board}:{event_id}"))


def _health_window(checked_at: str) -> str:
    stamp = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    return stamp.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def health_msg_id(board: str, cursor: int, checked_at: str) -> str:
    """Return the deterministic id for one successful projector check.

    The timestamp is the check's observed UTC time, not a task event time.  A
    replay of the same cycle therefore deduplicates through the room append
    door, while a later cycle gets a fresh freshness marker.
    """
    return str(uuid.uuid5(HEALTH_NAMESPACE,
                           f"{board}:projection-health:{cursor}:{_health_window(checked_at)}"))


def _health_due(last_posted_at: object, checked_at: str) -> bool:
    if last_posted_at is None:
        return True
    if not isinstance(last_posted_at, str):
        return False
    try:
        elapsed = (datetime.fromisoformat(checked_at.replace("Z", "+00:00")) -
                   datetime.fromisoformat(last_posted_at.replace("Z", "+00:00"))).total_seconds()
    except (TypeError, ValueError):
        return False
    return elapsed >= HEALTH_INTERVAL_S


def projection_health_receipt(*, checked_at: str, cursor: int,
                              projection_digest: str | None,
                              board: str = BOARD) -> dict:
    """Build the redacted, task-free receipt for a successful empty-or-full pass."""
    if not isinstance(checked_at, str) or not (checked_at.endswith("Z") or checked_at.endswith("+00:00")):
        raise ValueError("projection check time must be UTC")
    try:
        datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("projection check time must be ISO-8601") from exc
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise ValueError("projection cursor must be a non-negative integer")
    if projection_digest is not None and not isinstance(projection_digest, str):
        raise ValueError("projection digest must be a string or null")
    if (cursor == 0) != (projection_digest is None):
        raise ValueError("projection digest must be null iff cursor is zero")
    if projection_digest is not None:
        try:
            uuid.UUID(projection_digest)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("projection digest must be a UUID") from exc
    return {"queue_projection_health": {
        "v": 1, "board": board, "source": "hermes-queue-projector.v1",
        "status": "ok", "checked_at": checked_at, "event_cursor": cursor,
        "projection_digest": projection_digest,
    }}


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("bool is not an epoch")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(value)
    raise TypeError(type(value))


def _iso(epoch: object) -> str:
    try:
        return datetime.fromtimestamp(_as_int(epoch), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return datetime.fromtimestamp(0, timezone.utc).isoformat().replace("+00:00", "Z")


def _target_for(task: dict, target_catalog: dict) -> tuple[str, str | None]:
    for alias, entry in target_catalog.items():
        if isinstance(entry, dict) and entry.get("assignee") == task.get("assignee"):
            return alias, entry.get("effective_model")
    return "unassigned", None


def _source_seq(task: dict) -> int | None:
    body = task.get("body")
    if not isinstance(body, str) or not body.startswith("[CARR_QUEUE_META "):
        return None
    try:
        payload = json.loads(body.split("]", 1)[0][len("[CARR_QUEUE_META "):])
        value = payload.get("source_seq")
        return value if isinstance(value, int) and value >= 0 else None
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def _cap(task: dict) -> str:
    body = task.get("body")
    if not isinstance(body, str) or not body.startswith("[CARR_QUEUE_META "):
        return "read"
    try:
        value = json.loads(body.split("]", 1)[0][len("[CARR_QUEUE_META "):]).get("cap")
        return value if isinstance(value, str) and value in {"read", "repo-write", "record-write", "merge-approve", "production", "external-send", "destructive", "credential"} else "read"
    except (IndexError, ValueError, json.JSONDecodeError):
        return "read"


def _priority(value: object) -> str:
    try:
        return f"P{max(0, min(4, 4 - _as_int(value)))}"
    except (TypeError, ValueError):
        return "P2"


def card_for(task: dict, *, target_catalog: dict, updated_at: object) -> dict:
    target, model = _target_for(task, target_catalog)
    return {
        "title": str(task.get("title") or "Untitled")[:200],
        "target": target,
        "effective_model": model,
        "status": str(task.get("status") or "triage")[:32],
        "priority": _priority(task.get("priority")),
        "cap": _cap(task),
        "updated_at": _iso(updated_at),
        "source_seq": _source_seq(task),
    }


def _summary(event: dict, task: dict) -> str:
    title = str(task.get("title") or "this task")[:200]
    status = str(task.get("status") or "updated")
    kind = str(event.get("kind") or "updated")
    if status == "running": return f"{title} started."
    if status == "review": return f"{title} is ready for review."
    if status == "blocked": return f"{title} is blocked."
    if status == "done": return f"{title} finished."
    return f"{title} {kind.replace('_', ' ')}."


def receipt_for(event: dict, task: dict, *, target_catalog: dict, board: str = BOARD) -> dict:
    event_id = int(event["id"])
    card = card_for(task, target_catalog=target_catalog,
                    updated_at=event.get("created_at") or task.get("created_at"))
    return {"queue_event": {
        "v": 1, "board": board, "event_id": event_id,
        "event": str(event.get("kind") or "updated")[:48],
        "task_id": str(task["id"]), "card": card,
        "summary": _summary(event, task)[:300],
        "projected_at": _iso(event.get("created_at") or task.get("created_at")),
    }}


def missing_task_receipt(event: dict, *, board: str = BOARD) -> dict:
    """Build a redacted receipt for an event whose task row is gone.

    The event is still part of the durable stream, so silently skipping it
    would make the room projection permanently incomplete.  There is no task
    document to copy in this case: the card and summary are deliberately fixed
    safe values, while the event identity remains bound to its deterministic
    message id.
    """
    event_id = int(event["id"])
    projected_at = _iso(event.get("created_at"))
    return {"queue_event": {
        "v": 1, "board": board, "event_id": event_id,
        "event": str(event.get("kind") or "updated")[:48],
        "task_id": str(event.get("task_id") or "unknown"),
        "card": {
            "title": "Task unavailable", "target": "unassigned",
            "effective_model": None, "status": "missing", "priority": "P2",
            "cap": "read", "updated_at": projected_at, "source_seq": None,
        },
        "summary": "Task record unavailable.",
        "projected_at": projected_at,
    }}


def current_cards(tasks: list[dict], *, target_catalog: dict) -> list[dict]:
    return [
        {"task_id": str(task["id"]), **card_for(task, target_catalog=target_catalog,
          updated_at=task.get("completed_at") or task.get("started_at") or task.get("created_at"))}
        for task in tasks if str(task.get("status")) != "archived"
    ]


def open_reader(path: Path = DB_PATH) -> sqlite3.Connection:
    # immutable=1 is deliberately prohibited: Hermes writes through WAL and an
    # immutable reader can miss its live changes.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def project_once(*, state: dict, add_room_turn, target_catalog: dict,
                 db_path: Path = DB_PATH, board: str = BOARD, limit: int = EVENT_LIMIT,
                 checked_at: str | None = None) -> list[dict]:
    cursor = int(state.get("queue_event_cursor", 0) or 0)
    original_cursor = cursor
    original_digest = state.get("queue_projection_digest")
    original_health_posted_at = state.get("queue_projection_health_last_posted_at")
    projected: list[dict] = []
    try:
        with open_reader(db_path) as conn:
            source_head = int(conn.execute(
                "select coalesce(max(id), 0) as head from task_events").fetchone()["head"] or 0)
            events = conn.execute(
                "select id, task_id, kind, created_at from task_events where id > ? order by id asc limit ?",
                (cursor, limit + 1)).fetchall()
            complete = len(events) <= limit and cursor <= source_head
            for event_row in events[:limit]:
                event = dict(event_row)
                task_row = conn.execute("select * from tasks where id=?", (event["task_id"],)).fetchone()
                receipt = (missing_task_receipt(event, board=board) if task_row is None else
                           receipt_for(event, dict(task_row), target_catalog=target_catalog, board=board))
                add_room_turn(body=json.dumps(receipt, separators=(",", ":")), seat="hermes",
                              kind="receipt", msg_id=event_msg_id(board, event["id"]))
                state["queue_event_cursor"] = event["id"]
                state["queue_projection_digest"] = event_msg_id(board, event["id"])
                projected.append(receipt)
            end_head = int(conn.execute(
                "select coalesce(max(id), 0) as head from task_events").fetchone()["head"] or 0)
            complete = complete and source_head == end_head and int(state.get("queue_event_cursor", cursor) or 0) == end_head
            stamp = checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if complete and _health_due(state.get("queue_projection_health_last_posted_at"), stamp):
                health = projection_health_receipt(
                    checked_at=stamp, cursor=int(state.get("queue_event_cursor", cursor) or 0),
                    projection_digest=state.get("queue_projection_digest"), board=board)
                add_room_turn(body=json.dumps(health, separators=(",", ":")), seat="hermes",
                              kind="receipt", msg_id=health_msg_id(board, int(state.get("queue_event_cursor", cursor) or 0), stamp))
                state["queue_projection_health_last_posted_at"] = stamp
    except Exception:
        state["queue_event_cursor"] = original_cursor
        state["queue_projection_digest"] = original_digest
        state["queue_projection_health_last_posted_at"] = original_health_posted_at
        raise
    return projected
