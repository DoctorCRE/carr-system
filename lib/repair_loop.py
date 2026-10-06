"""Durable, replayable repair-loop transitions shared by health readers."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path


def read_state(path, valid=lambda state: isinstance(state, dict)):
    path = Path(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not valid(state):
            raise ValueError("invalid repair state")
        return state
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        shutil.copy2(path, path.with_name(path.name + f".corrupt-{uuid.uuid4()}"))
        return {}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def reconcile(row, desired, verb, save, *, outcome=None, refresh=False):
    """Open/update from a desired add payload; close only with recovery evidence.

    Persist each complete mutation before sending it. A lost response replays
    that same payload, including its key and optimistic version.
    """
    if not row.get("loop_id") and (desired is not None or row.get("open_payload")):
        row.setdefault("open_key", str(uuid.uuid4()))
        row.setdefault("open_payload", {**desired, "idempotency_key": row["open_key"]} if desired else {})
        save()
        response = verb("add-loop", row["open_payload"])
        if not response.get("ok") or not response.get("loop_id"):
            raise RuntimeError("repair loop open response failed")
        row.update(loop_id=response["loop_id"], body=row["open_payload"]["body"])
        del row["open_payload"]
        save()
        opened = True
    else:
        opened = False
    if not row.get("loop_id"):
        return "none"
    if desired is not None and row.get("body") == desired["body"] and (opened or not refresh):
        return "opened" if opened else "open"
    observed = verb("read-loop", {"loop_id": row["loop_id"]})
    if not observed.get("version") or observed.get("status") not in {"open", "done", "dropped", "closed"}:
        raise RuntimeError("repair loop read failed")
    if observed["status"] != "open":
        for key in ("loop_id", "body", "open_key", "close_payload", "update_payload"):
            row.pop(key, None)
        save()
        return reconcile(row, desired, verb, save, outcome=outcome) if desired else "cleared"
    if desired is not None:
        if row.get("body") == desired["body"] and not row.get("update_payload"):
            return "opened" if opened else "open"
        row.setdefault("update_payload", {"idempotency_key": str(uuid.uuid4()),
                       "loop_id": row["loop_id"], "base_version": int(observed["version"]),
                       "body": desired["body"]})
        save()
        response = verb("update-loop", row["update_payload"])
        if not response.get("ok"):
            raise RuntimeError("repair loop update response failed")
        row["body"] = row.pop("update_payload")["body"]
        save()
        return "updated"
    if outcome is None:
        return "open"
    row.setdefault("close_payload", {"idempotency_key": str(uuid.uuid4()),
                   "loop_id": row["loop_id"], "base_version": int(observed["version"]),
                   "resolution": "done", "outcome": outcome})
    save()
    response = verb("close-loop", row["close_payload"])
    if not response.get("ok"):
        raise RuntimeError("repair loop close response failed")
    for key in ("loop_id", "body", "open_key", "close_payload", "update_payload"):
        row.pop(key, None)
    save()
    return "cleared"
