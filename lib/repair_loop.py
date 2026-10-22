"""Durable, replayable repair-loop transitions shared by health readers."""
from __future__ import annotations

import json
import shutil
import tempfile
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
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(json.dumps(state, sort_keys=True) + "\n")
            target.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def reconcile(row, desired, verb, save, *, outcome=None, refresh=False, identity=None):
    """Open/update from a desired add payload; close only with recovery evidence.

    Persist each complete mutation before sending it. A lost response replays
    that same payload, including its key and optimistic version.
    """
    if identity and desired is not None:
        desired = {**desired, "source_note": identity}
    if identity and not row.get("loop_id") and desired is None and not row.get("open_payload"):
        response = verb("read-loop", {"source_note": identity})
        if response.get("error") or "loop" not in response:
            raise RuntimeError("repair incident lookup failed")
        if response["loop"] is not None:
            incident = response["loop"]
            if not isinstance(incident, dict) or not incident.get("loop_id"):
                raise RuntimeError("repair incident lookup failed")
            row.update(loop_id=incident["loop_id"], body=incident.get("body"))
            save()
    if not row.get("loop_id") and (desired is not None or row.get("open_payload")):
        row.setdefault("open_key", str(uuid.uuid4()))
        row.setdefault("open_payload", {**desired, "idempotency_key": row["open_key"]} if desired else {})
        save()
        response = verb("add-loop", row["open_payload"])
        if not response.get("ok") or not response.get("loop_id"):
            if response.get("refused"):
                # The record layer answered no, so nothing was written under
                # this key, and replaying the identical payload can only be
                # refused again. Drop it; the next run sends the current one.
                # An unconfirmed reply (no `refused`) keeps its key for replay.
                row.pop("open_payload", None)
                row.pop("open_key", None)
                save()
            raise RuntimeError("repair loop open response failed")
        opening_identity = row["open_payload"].get("source_note")
        row.update(loop_id=response["loop_id"], body=row["open_payload"]["body"])
        del row["open_payload"]
        save()
        opened = not response.get("deduplicated", False)
        refresh = refresh or not opened
    else:
        opened = False
        opening_identity = None
    if not row.get("loop_id"):
        return "none"
    fresh_open = opened and (not identity or opening_identity == identity)
    if (desired is not None and row.get("body") == desired["body"] and
            (fresh_open or not refresh) and not row.get("update_payload")):
        return "opened" if opened else "open"
    observed = verb("read-loop", {"loop_id": row["loop_id"]}).get("loop")
    if (not isinstance(observed, dict) or not observed.get("version")
            or observed.get("status") not in {"open", "done", "dropped", "closed"}):
        raise RuntimeError("repair loop read failed")
    if observed["status"] != "open":
        for key in ("loop_id", "body", "open_key", "close_payload", "update_payload"):
            row.pop(key, None)
        save()
        return reconcile(row, desired, verb, save, outcome=outcome, refresh=refresh, identity=identity) if desired else "cleared"
    if identity and observed.get("source_note") not in (None, identity):
        raise RuntimeError("repair incident identity mismatch")
    if desired is not None:
        if observed.get("body", row.get("body")) == desired["body"] and not row.get("update_payload") and (
                not identity or observed.get("source_note") == identity):
            return "opened" if opened else "open"
        row.setdefault("update_payload", {"idempotency_key": str(uuid.uuid4()),
                       "loop_id": row["loop_id"], "base_version": int(observed["version"]),
                       "body": desired["body"],
                       **({"source_note": identity} if identity else {})})
        save()
        response = verb("update-loop", row["update_payload"])
        if not response.get("ok"):
            if response.get("refused"):
                row.pop("update_payload", None)
                if identity and response.get("error") == "incident_identity_taken":
                    if row.get("close_payload"):
                        settled = verb("close-loop", row["close_payload"])
                        if not settled.get("ok") and not settled.get("refused"):
                            raise RuntimeError("legacy repair close remains unconfirmed")
                        row.pop("close_payload")
                    for key in ("loop_id", "body", "open_key"):
                        row.pop(key, None)
                    save()
                    return reconcile(row, desired, verb, save, identity=identity, refresh=True)
                save()
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
        if response.get("refused"):
            row.pop("close_payload", None)
            save()
        raise RuntimeError("repair loop close response failed")
    for key in ("loop_id", "body", "open_key", "close_payload", "update_payload"):
        row.pop(key, None)
    save()
    return "cleared"
