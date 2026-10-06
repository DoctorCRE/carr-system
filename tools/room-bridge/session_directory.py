#!/usr/bin/env python3
"""session_directory.py — the bridge's list of live sessions, and delivery into them.

Sessions announce themselves into the Model Room (session_presence.py). This
file is the other half: the room bridge reads those announcements off the wire,
keeps one directory of them, probes every local session each cycle, expires the
ones that stopped answering, and carries an @-addressed room turn into the
session it names.

WHY A SEPARATE FILE FROM hermes-desks.json. A desk is a place Hermes may
dispatch work to, and desks.py refuses a pid socket as a desk on purpose (a pid
is any window that happens to be open). A self-announced session is different:
it published its own name, title and address, so it can be reached by an
explicit @-mention from someone who read that name. Keeping the two in separate
files keeps the queue, seat fan-out and the pid-socket refusal exactly as they
were: nothing here is a dispatch target, nothing here hears unaddressed room
chatter, and the bridge remains the only writer of both files.

DELIVERY, by address:
  claude-socket  one NDJSON user turn over the session's own socket, the same
                 wire a claude-session desk uses (claude_wire.py)
  codex-thread   thread-follower-start-turn through the Codex Desktop IPC
                 router (codex_ipc.py) to the window that owns the thread
  none           reported as no_inbound_address (Grok Build has no inbound
                 channel; its sessions are listed so others can see them)
The session answers in its own window; a receipt in the room records whether
the turn landed.

LIVENESS, by runtime: a Claude session with a socket is live when the socket
accepts a connection; a Codex Desktop session when the Desktop router names an
owner for its thread; everything else when its pid is alive. A session on
another host cannot be probed from here and expires when its own heartbeat
re-posts stop.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "session-presence/v1"
HANDLE = re.compile(r"^[a-z][a-z0-9-]{1,20}-[a-z0-9]{4,8}$")
ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MENTION = re.compile(r"(?<![\w@.])@([a-z0-9][a-z0-9-]{1,40})(?![\w@.-])")
LOCAL_STALE_S = float(os.environ.get("CARR_SESSION_LOCAL_STALE", "600"))
REMOTE_STALE_S = float(os.environ.get("CARR_SESSION_REMOTE_STALE", "2700"))
DELIVERED_CAP = 2000
ADDRESS_KINDS = ("claude-socket", "codex-thread", "none")
FIELDS = ("runtime", "surface", "session_id", "name", "title", "cwd", "host", "pid",
          "model", "child", "host_session_id", "address", "announced_at", "beat_at")


def empty() -> dict:
    return {"schema": "session-directory/v1", "sessions": {}}


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return empty()
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return empty()
    return data


def save(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def _parse(ts) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _age(ts, now: str) -> float:
    a, b = _parse(ts), _parse(now)
    if a is None or b is None:
        return float("inf")
    return (b - a).total_seconds()


def _presence(turn: dict) -> dict | None:
    if turn.get("kind") != "receipt":
        return None
    try:
        body = json.loads(str(turn.get("body") or ""))
    except ValueError:
        return None
    rec = body.get("session_presence") if isinstance(body, dict) else None
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return None
    if not HANDLE.match(str(rec.get("handle") or "")):
        return None
    if rec.get("event") not in ("announce", "depart"):
        return None
    address = rec.get("address")
    if not isinstance(address, dict) or address.get("kind") not in ADDRESS_KINDS:
        return None
    return rec


def ingest(data: dict, turns: list[dict], *, host: str, now: str,
           reserved: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Apply every presence receipt in `turns`. Returns the handles touched."""
    touched: list[str] = []
    sessions = data.setdefault("sessions", {})
    for turn in turns:
        rec = _presence(turn)
        if rec is None:
            continue
        handle = rec["handle"]
        if rec["event"] == "depart":
            sessions.pop(handle, None)
            touched.append(handle)
            continue
        entry = {k: rec.get(k) for k in FIELDS}
        alias = str(entry.get("name") or "")
        entry["name"] = alias if ALIAS.match(alias) and alias not in reserved else None
        entry["handle"] = handle
        entry["local"] = rec.get("host") == host
        prior = sessions.get(handle) or {}
        entry["title"] = entry.get("title") or prior.get("title")
        entry["live"] = prior.get("live")
        entry["last_live_at"] = prior.get("last_live_at")
        entry["seen_at"] = now
        entry["source_seq"] = turn.get("seq")
        sessions[handle] = entry
        touched.append(handle)
    return touched


def refresh(data: dict, *, now: str, host: str, probe) -> list[str]:
    """Probe every local session; expire the ones that stopped answering."""
    expired: list[str] = []
    sessions = data.setdefault("sessions", {})
    for handle, entry in list(sessions.items()):
        entry["local"] = entry.get("host") == host
        if entry["local"]:
            try:
                live = bool(probe(entry))
            except Exception:  # noqa: BLE001 — a failed probe is a dead probe
                live = False
            # Codex CLI sessions share one long-lived Desktop process PID. A
            # live process therefore says nothing about an individual thread.
            # Its own check-in is the only bounded liveness evidence.
            if entry.get("runtime") == "codex" and entry.get("surface") == "codex-cli":
                live = live and _age(entry.get("beat_at"), now) <= LOCAL_STALE_S
            entry["live"] = live
            if live:
                entry["last_live_at"] = now
            elif _age(entry.get("last_live_at") or entry.get("seen_at"), now) > LOCAL_STALE_S:
                expired.append(handle)
        else:
            entry["live"] = None
            if _age(entry.get("beat_at") or entry.get("seen_at"), now) > REMOTE_STALE_S:
                expired.append(handle)
    for handle in expired:
        sessions.pop(handle, None)
    return expired


def mentioned(data: dict, turn: dict) -> list[str]:
    """Handles a kind="turn" row addresses by @handle or @alias."""
    if turn.get("kind") != "turn":
        return []
    names = {m.lower() for m in MENTION.findall(str(turn.get("body") or "").lower())}
    if not names:
        return []
    out = []
    for handle, entry in sorted((data.get("sessions") or {}).items()):
        if handle in names or (entry.get("name") and entry["name"] in names):
            out.append(handle)
    return out


def _session_message(turn: dict) -> dict | None:
    """The {"to", "text"} of a `session-presence send` receipt, or None.

    `send` posts a receipt rather than a turn because state.route_turn queues
    every kind="turn" row onto every seated desk: a message meant for one
    session would cost each desk a dispatch (PR #1345 review). Receipts reach
    no desk; this router alone carries them."""
    if turn.get("kind") != "receipt":
        return None
    try:
        body = json.loads(str(turn.get("body") or ""))
    except (ValueError, TypeError):
        return None
    msg = body.get("session_message") if isinstance(body, dict) else None
    if not isinstance(msg, dict):
        return None
    to, text = msg.get("to"), msg.get("text")
    if not (isinstance(to, str) and isinstance(text, str) and text.strip()):
        return None
    return {"to": to.lower(), "text": text}


def addressed(data: dict, turn: dict) -> list[str]:
    """Handles a room row is addressed to: @-mentions in a turn, or the one
    target of a session_message receipt (by handle or alias)."""
    msg = _session_message(turn)
    if msg is None:
        return mentioned(data, turn)
    return [h for h, e in sorted((data.get("sessions") or {}).items())
            if h == msg["to"] or (e.get("name") and e["name"] == msg["to"])][:1]


def format_turn(turn: dict) -> str:
    msg = _session_message(turn)
    text = msg["text"] if msg is not None else (turn.get("body") or "")
    return f"[model-room · {turn.get('seat') or '?'}] {text}"


def _delivered(state: dict) -> list:
    got = state.setdefault("session_delivered", [])
    if not isinstance(got, list):
        state["session_delivered"] = got = []
    return got


def route(data: dict, turns: list[dict], state: dict, *, deliver, add_room_turn) -> list[dict]:
    """Carry each @-addressed turn or session_message receipt into the
    session(s) it names, once each."""
    outcomes: list[dict] = []
    seen = _delivered(state)
    for turn in turns:
        msg_id = str(turn.get("msg_id") or "")
        if not msg_id:
            continue
        for handle in addressed(data, turn):
            key = f"{handle}|{msg_id}"
            if key in seen:
                continue
            seen.append(key)
            entry = data["sessions"][handle]
            if entry.get("local") and entry.get("live") is False:
                result = {"status": "not_live",
                          "detail": "the session stopped answering its liveness probe"}
            elif not entry.get("local"):
                result = {"status": "other_host",
                          "detail": f"the session runs on {entry.get('host')}; this bridge cannot reach it"}
            else:
                try:
                    result = deliver(entry, format_turn(turn), msg_id) or {}
                except Exception as exc:  # noqa: BLE001 — contained to this delivery
                    result = {"status": "failed", "detail": f"{exc.__class__.__name__}: {exc}"}
            outcome = {"handle": handle, "source_msg_id": msg_id, "source_seq": turn.get("seq"),
                       "status": str(result.get("status") or "failed"),
                       "detail": str(result.get("detail") or "")[:300]}
            outcomes.append(outcome)
            try:
                add_room_turn(body=json.dumps({"session_delivery": outcome}, separators=(",", ":")),
                              seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()))
            except RuntimeError:
                pass  # the delivery already happened; a lost receipt is not a lost turn
    del seen[:-DELIVERED_CAP]
    return outcomes


def make_deliverer(*, inject_claude=None, codex_start_turn=None):
    """The default deliverer, dispatching on the session's published address."""
    if inject_claude is None:
        import claude_wire

        def inject_claude(sock: str, payload: dict) -> None:
            conn = claude_wire.inject_keepalive(sock, payload)
            conn.close()
    if codex_start_turn is None:
        import codex_ipc
        codex_start_turn = codex_ipc.start_turn

    def deliver(entry: dict, text: str, msg_id: str) -> dict:
        address = entry.get("address") or {}
        kind, value = address.get("kind"), address.get("value")
        if kind == "claude-socket" and address.get("auth") == "token":
            # The socket drops any turn not preceded by the session's own
            # token, silently. Only Claude's native peer messaging holds a
            # route in; report that rather than claim a delivery that vanished.
            return {"status": "needs_sendmessage",
                    "detail": ("this Claude session's socket requires its own token; reach it "
                               "from a Claude session with SendMessage (Desktop session "
                               f"{entry.get('host_session_id') or 'id unknown'})")}
        if kind == "claude-socket" and isinstance(value, str):
            inject_claude(value, {
                "type": "user",
                "message": {"role": "user", "content": text},
                "origin": {"kind": "peer", "from": "model-room", "msg_id": msg_id},
            })
            return {"status": "delivered", "detail": "the session's socket accepted the turn"}
        if kind == "codex-thread" and isinstance(value, str):
            return codex_start_turn(value, text)
        return {"status": "no_inbound_address",
                "detail": f"a {entry.get('runtime')} session publishes no inbound channel"}

    return deliver


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
        if pid <= 1:
            return False
        os.kill(pid, 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def make_probe(*, socket_live=None, codex_owner=None, pid_alive=None):
    if socket_live is None:
        import desks
        socket_live = desks.is_live
    if codex_owner is None:
        import codex_ipc
        codex_owner = codex_ipc.thread_owner
    pid_alive = pid_alive or _pid_alive

    def probe(entry: dict) -> bool:
        address = entry.get("address") or {}
        if address.get("kind") == "claude-socket" and address.get("value"):
            return bool(socket_live(address["value"]))
        if entry.get("runtime") == "codex" and entry.get("surface") == "codex-desktop":
            return codex_owner(str(address.get("value") or "")) is not None
        return bool(pid_alive(entry.get("pid")))

    return probe


def roster(data: dict) -> list[dict]:
    """The session rows the bridge heartbeat republishes to the room."""
    return [
        {"handle": handle, "name": e.get("name"), "runtime": e.get("runtime"),
         "surface": e.get("surface"), "title": e.get("title"), "cwd": e.get("cwd"),
         "host": e.get("host"), "live": e.get("live"), "last_live_at": e.get("last_live_at"),
         "address": e.get("address"), "host_session_id": e.get("host_session_id")}
        for handle, e in sorted((data.get("sessions") or {}).items())
    ]
