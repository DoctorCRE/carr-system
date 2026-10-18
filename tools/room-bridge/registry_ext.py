#!/usr/bin/env python3
"""registry_ext.py — the room-bridge's additive fields on hermes-desks.json.

  room_seat      which partner-room seat this desk speaks for (claude, codex,
                 grok, sol, ...). This is what makes echo suppression and reply
                 attribution possible: a turn whose seat matches a desk's own
                 room_seat is that desk's own echo, never re-delivered to it,
                 and a captured reply is posted back under this seat.
  last_seen      ISO timestamp, stamped by the bridge on every successful poll
                 or delivery cycle that touched this desk (register/refresh/
                 heartbeat also stamp it by hand).

Both are stored as plain extra keys on the same desk entry in the same file.
dispatch.py's Registry ignores keys it did not write.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from desks import DEFAULT_REGISTRY, DeskError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"desks": {}}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def set_seat(name: str, seat: str, *, path: Path = DEFAULT_REGISTRY) -> dict:
    """Stamp a desk's room-bridge seat. Refuses a desk that isn't registered
    yet — set_seat is metadata ON an entry, never a way to create one; use
    desks.Registry.register() first."""
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    if entry is None:
        raise DeskError("unknown_desk", f"no desk named {name!r} — register it first")
    entry["room_seat"] = seat
    _save(path, data)
    return entry


ROOM_LISTEN_MODES = ("all", "mention")


def set_room_listen(name: str, mode: str, *, path: Path = DEFAULT_REGISTRY) -> dict:
    """How much of the room a desk hears: "all" (default) or "mention" — people's
    turns plus only those desk turns that @-mention its seat (see
    state.is_unaddressed_desk_turn). Metadata on an existing entry, like set_seat."""
    if mode not in ROOM_LISTEN_MODES:
        raise DeskError("bad_room_listen", f"room_listen must be one of {ROOM_LISTEN_MODES}")
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    if entry is None:
        raise DeskError("unknown_desk", f"no desk named {name!r} — register it first")
    entry["room_listen"] = mode
    _save(path, data)
    return entry


def get_seat(name: str, *, path: Path = DEFAULT_REGISTRY) -> str | None:
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    return (entry or {}).get("room_seat")


def set_profile(name: str, profile: str | None, *, path: Path = DEFAULT_REGISTRY) -> dict:
    """Bind (or with None, unbind) a desk to a named agent profile — the
    persistent identity (builder, designer, reviewer, doc) whose name the
    observatory shows as the desk's primary label. PRESENTATION AND ROUTING
    ONLY: this key changes what the heartbeat publishes and how the panel
    labels the node, and it grants nothing — the desk's credentials and the
    record layer's actor rules are untouched by any value written here. Same
    register-first stance as set_seat: metadata ON an entry, never a way to
    create one."""
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    if entry is None:
        raise DeskError("unknown_desk", f"no desk named {name!r} — register it first")
    if profile is None:
        entry.pop("profile", None)
    else:
        entry["profile"] = profile
    _save(path, data)
    return entry


def get_profile(name: str, *, path: Path = DEFAULT_REGISTRY) -> str | None:
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    return (entry or {}).get("profile")


def stamp_heartbeat(name: str, *, live: bool, path: Path = DEFAULT_REGISTRY) -> dict:
    """Record 'this desk was checked, and here is what we found'. Silently a
    no-op if the desk has been forgotten between the caller resolving it and
    this call landing — a heartbeat racing a `forget` should never resurrect
    the entry."""
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    if entry is None:
        return {}
    entry["last_seen"] = _now()
    entry["last_live"] = bool(live)
    _save(path, data)
    return entry


def stamp_auth(name: str, *, auth: bool | None, path: Path = DEFAULT_REGISTRY) -> dict:
    """Record this cycle's sign-in probe for one desk (spec section 17). Three
    valued — None means the vendor CLI could not answer, which is written as a
    null rather than collapsed to false (see auth_control's own header). Same
    no-op-on-forgotten-desk stance as stamp_heartbeat."""
    data = _load(path)
    entry = data.get("desks", {}).get(name)
    if entry is None:
        return {}
    entry["last_auth"] = auth if isinstance(auth, bool) else None
    entry["last_auth_at"] = _now()
    _save(path, data)
    return entry


def all_desks(path: Path = DEFAULT_REGISTRY) -> dict:
    return _load(path).get("desks", {})
