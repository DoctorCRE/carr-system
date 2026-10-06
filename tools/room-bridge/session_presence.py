#!/usr/bin/env python3
"""session_presence.py — every session announces itself to the Model Room.

Requirement, 2026-09-27: "sessions need to post theirself to the model room at session
start so that every other session can reach them easily", widened the same day
to "not just claude or codex, every session no matter what model".

ONE RUNTIME-NEUTRAL ENTRY. Every runtime's hook adapter and every launcher calls
this file (directly, through hooks/session-presence-hook.py, or through
bin/session-presence). A new runtime needs one line in its hook config or its
launcher, never a copy of this logic.

WHAT A SESSION PUBLISHES, as one kind="receipt" room turn whose body is
{"session_presence": {...}}:
  runtime     claude | codex | grok | <launcher-supplied slug>
  surface     claude-desktop / cli / sdk-..., codex-desktop / codex-cli, grok
  session_id  the runtime's own id (Codex: the thread id)
  handle      <runtime>-<last 8 hex of the id>, the name other sessions use
  name        optional alias (CARR_SESSION_NAME or --name), e.g. "sol"
  title       a one-line summary of the first prompt, when one exists
  cwd, host, pid, model
  address     how to reach it: claude-socket (the uds path), codex-thread
              (the thread id), or none (Grok has no inbound channel)
  announced_at / beat_at

WHY A RECEIPT AND NOT A TURN: route_turn fans kind="turn" rows out to every
seated desk. A presence notice is bookkeeping, and as a turn it would wake every
desk in the room each time a session started.

THE HOOK NEVER BLOCKS A SESSION START. The hook writes the record to a local
outbox (one file per session, so concurrent sessions never race on one file)
and spawns a detached child that posts it. Nothing on the session's own path
touches the network. If the room is unreachable the child logs one notice line
to ~/.config/carr/session-presence/presence.log and leaves the outbox pending;
the next heartbeat retries it. The hook prints nothing: SessionStart stdout
becomes model context in both Claude Code and Codex.

HEARTBEAT. The bridge probes every local session each cycle (socket connect,
Desktop thread owner, or pid) and expires the ones that stopped answering, so a
session's liveness does not depend on it talking. The session's own heartbeat
is its UserPromptSubmit hook (SessionEnd posts a depart). It stays local unless
it has something new to say: a first title, a pending post, or a re-post once
BEAT_REPOST_S has passed. That re-post is the only liveness a session on
another Mac has, since the Studio bridge cannot probe it, so a remote session
left idle past the bridge's REMOTE_STALE_S drops off the list until its next
prompt.

NEVER PUBLISHED: the Claude messaging token that lives beside the socket path
in the same environment, and any prompt text beyond the one-line title.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCHEMA = "session-presence/v1"
PRESENCE_DIR = Path(os.environ.get("CARR_SESSION_PRESENCE_DIR",
                                   Path.home() / ".config" / "carr" / "session-presence"))
DIRECTORY_PATH = Path(os.environ.get("CARR_SESSION_DIRECTORY",
                                     Path.home() / ".config" / "carr" / "session-directory.json"))
ROOM = os.environ.get("CARR_SESSION_PRESENCE_ROOM",
                      os.environ.get("CARR_ROOM_BRIDGE_ROOM", "model-room"))
TITLE_MAX = 80
BEAT_REPOST_S = float(os.environ.get("CARR_SESSION_PRESENCE_REPOST", "900"))
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
RUNTIME_SLUG = re.compile(r"^[a-z][a-z0-9-]{1,20}$")
LOG_MAX_BYTES = 256 * 1024


class PresenceError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def this_host() -> str:
    """A stable per-machine name. The DHCP hostname ("Mac") is the same on
    every Mac on the network, so macOS's LocalHostName ("Mac-Studio") wins.
    scutil is called by absolute path: launchd PATHs can lack /usr/sbin, and
    the DHCP fallback then made the bridge treat local sessions as remote."""
    name = ""
    try:
        name = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"], capture_output=True,
                              text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    name = (name or socket.gethostname().split(".")[0]).lower()
    return re.sub(r"[^a-z0-9-]", "-", name)[:40] or "host"


def _handle(runtime: str, session_id: str) -> str:
    hexes = re.sub(r"[^0-9a-f]", "", session_id.lower())
    tail = (hexes or re.sub(r"[^a-z0-9]", "", session_id.lower()))[-8:]
    if len(tail) < 4:
        raise PresenceError("session id too short to name")
    return f"{runtime}-{tail}"


def _title(prompt: object) -> str | None:
    if not isinstance(prompt, str):
        return None
    for line in prompt.splitlines():
        line = " ".join(line.split())
        if line:
            return line[:TITLE_MAX]
    return None


def _codex_surface(transcript_path: object) -> str:
    """codex-desktop or codex-cli, from the rollout's own session_meta line."""
    try:
        with open(str(transcript_path), encoding="utf-8") as fh:
            first = json.loads(fh.readline())
        originator = str((first.get("payload") or {}).get("originator") or "")
    except (OSError, ValueError, TypeError, AttributeError):
        return "codex"
    return "codex-desktop" if "desktop" in originator.lower() else "codex-cli"


def _pid(value: object) -> int | None:
    try:
        pid = int(str(value))
    except (TypeError, ValueError):
        return None
    return pid if pid > 1 else None


def _runtime_pid(env: Mapping[str, str]) -> int | None:
    """The long-lived runtime process: the hook's parent, skipping a shell."""
    explicit = _pid(env.get("CLAUDE_PID") or env.get("CARR_SESSION_PID"))
    if explicit:
        return explicit
    ppid = os.getppid()
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(ppid)], capture_output=True,
                             text=True, timeout=1).stdout.split(None, 1)
        if len(out) == 2 and os.path.basename(out[1].strip()) in ("sh", "zsh", "bash", "dash"):
            return _pid(out[0]) or ppid
    except (OSError, subprocess.SubprocessError):
        pass
    return ppid


def build_record(payload: dict, *, runtime_hint: str, env: Mapping[str, str], host: str,
                 now: str | None = None, name: str | None = None,
                 title: str | None = None, address: dict | None = None) -> dict:
    """One normalized presence record from any runtime's hook payload."""
    if not isinstance(payload, dict):
        raise PresenceError("hook payload is not an object")
    now = now or _now()
    runtime = runtime_hint
    # Grok Build scans ~/.claude/settings.json, so the Claude-configured entry
    # fires inside Grok too. Grok's payload carries camelCase keys.
    if "sessionId" in payload or "hookEventName" in payload:
        runtime = "grok"
    if not RUNTIME_SLUG.match(runtime or ""):
        raise PresenceError(f"runtime {runtime!r} is not a slug")
    session_id = payload.get("session_id") or payload.get("sessionId") or env.get("CARR_SESSION_ID")
    if not isinstance(session_id, str) or not session_id.strip():
        raise PresenceError("no session id in the hook payload")
    session_id = session_id.strip()[:200]

    surface = None
    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    if address is None:
        address = {"kind": "none", "value": None}
        if runtime == "claude":
            sock = env.get("CLAUDE_CODE_MESSAGING_SOCKET")
            if isinstance(sock, str) and sock.startswith("/"):
                # A Desktop-hosted socket requires the session's own token as
                # its first line and silently drops anything else. Publish only
                # THAT a token is required, never the token.
                address = {"kind": "claude-socket", "value": sock,
                           "auth": "token" if env.get("CLAUDE_CODE_MESSAGING_TOKEN") else "none"}
        elif runtime == "codex":
            address = {"kind": "codex-thread", "value": session_id}
    if runtime == "claude":
        surface = env.get("CLAUDE_CODE_ENTRYPOINT") or "cli"
    elif runtime == "codex":
        surface = _codex_surface(payload.get("transcript_path"))
    elif runtime == "grok":
        surface = "grok"
    else:
        surface = env.get("CARR_SESSION_SURFACE") or runtime

    alias = (name or env.get("CARR_SESSION_NAME") or "").strip().lower()
    cwd = payload.get("cwd") or payload.get("workspaceRoot") or env.get("PWD")
    return {
        "schema": SCHEMA,
        "event": "announce",
        "runtime": runtime,
        "surface": str(surface)[:40],
        "session_id": session_id,
        "handle": _handle(runtime, session_id),
        "name": alias if SLUG.match(alias) else None,
        "title": title or _title(payload.get("prompt")) or _title(env.get("CARR_SESSION_TITLE")),
        "cwd": str(cwd)[:300] if cwd else None,
        "host": host,
        "pid": _runtime_pid(env) if env is os.environ else _pid(env.get("CLAUDE_PID") or env.get("CARR_SESSION_PID")),
        "model": model,
        "child": env.get("CLAUDE_CODE_CHILD_SESSION") == "1",
        # the Claude Desktop app's own session id: what a Claude sender passes
        # to SendMessage / the Desktop session tools to reach a token socket
        "host_session_id": (env.get("CLAUDE_CODE_HOST_SESSION_ID") or None) if runtime == "claude" else None,
        "address": address,
        "announced_at": now,
        "beat_at": now,
    }


# ---------------------------------------------------------------------------
# the local outbox: one file per session, written atomically
# ---------------------------------------------------------------------------


def _log(presence_dir: Path, message: str) -> None:
    try:
        presence_dir.mkdir(parents=True, exist_ok=True)
        path = presence_dir / "presence.log"
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(presence_dir / "presence.log.1")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now()} presence notice: {message[:500]}\n")
    except OSError:
        pass


def _outbox(presence_dir: Path) -> Path:
    return presence_dir / "outbox"


def _read_box(path: Path) -> dict | None:
    try:
        box = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return box if isinstance(box, dict) and isinstance(box.get("record"), dict) else None


def _write_box(path: Path, box: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(box, indent=2) + "\n")
    tmp.replace(path)


def _spawn_flush(path: Path) -> None:
    """Post the outbox entry from a detached child, off the session's path."""
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "flush", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )


def _default_post(body: str, seat: str, **kw) -> dict:
    import verb_io  # deferred: the hook path must not pay for it
    return verb_io.add_room_turn(body, seat, **kw)


def flush(path: Path, *, post=None, room: str = ROOM) -> bool:
    """Post one outbox record to the room. True when it landed."""
    path = Path(path)
    presence_dir = path.parent.parent
    box = _read_box(path)
    if box is None:
        return False
    rec = box["record"]
    stamp = rec.get("beat_at") or rec.get("announced_at") or _now()
    msg_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                            f"carr:session-presence:{rec.get('handle')}:{rec.get('event')}:{stamp}"))
    try:
        (post or _default_post)(
            json.dumps({"session_presence": rec}, separators=(",", ":")),
            rec.get("runtime") if SLUG.match(str(rec.get("runtime") or "")) else "hermes",
            kind="receipt", room=room, msg_id=msg_id, idempotency_key=f"presence:{msg_id}")
    except Exception as exc:  # noqa: BLE001 — any failure is the same fail-open notice
        _log(presence_dir, f"{rec.get('handle')}: room post failed, will retry on next beat: {exc}")
        return False
    fresh = _read_box(path) or box
    if fresh["record"].get("beat_at") == rec.get("beat_at"):
        fresh["posted_at"] = _now()
        fresh["posted_event"] = rec.get("event")
        _write_box(path, fresh)
    return True


def _event(payload: dict) -> str:
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    return re.sub(r"[_\s]", "", str(event)).lower()


def _due(box: dict, now: str) -> bool:
    posted = _parse(box.get("posted_at"))
    at = _parse(now)
    return posted is None or at is None or (at - posted).total_seconds() >= BEAT_REPOST_S


def handle_event(payload: dict, *, runtime_hint: str, env: Mapping[str, str], presence_dir: Path,
                 host: str, spawn_flush, now: str | None = None) -> str:
    now = now or _now()
    event = _event(payload)
    rec = build_record(payload, runtime_hint=runtime_hint, env=env, host=host, now=now)
    path = _outbox(presence_dir) / f"{rec['handle']}.json"
    box = _read_box(path)

    if event == "sessionend":
        prior = (box or {}).get("record") or rec
        rec = {**prior, "event": "depart", "beat_at": now}
        _write_box(path, {"record": rec, "posted_at": None})
        spawn_flush(path)
        return "depart"

    if event == "sessionstart" or box is None:
        if box is not None:
            rec["title"] = rec["title"] or box["record"].get("title")
            rec["name"] = rec["name"] or box["record"].get("name")
        _write_box(path, {"record": rec, "posted_at": None})
        spawn_flush(path)
        return "announce"

    prior = box["record"]
    new_title = rec["title"] and not prior.get("title")
    merged = {**prior, "event": "announce", "beat_at": now,
              "title": prior.get("title") or rec["title"]}
    box["record"] = merged
    repost = bool(new_title) or box.get("posted_at") is None or _due(box, now)
    if repost:
        box["posted_at"] = None if new_title else box.get("posted_at")
    _write_box(path, box)
    if repost:
        spawn_flush(path)
        return "beat-post"
    return "beat"


def run_hook(runtime_hint: str, *, stdin=None, env=None, presence_dir: Path | None = None,
             host: str | None = None, post=None, spawn_flush=None, stdout=None,
             stderr=None) -> int:
    """The hook entry. Always exits 0 and prints nothing."""
    del post, stdout, stderr  # the hook never posts inline and never prints
    presence_dir = Path(presence_dir or PRESENCE_DIR)
    env = os.environ if env is None else env
    spawn = spawn_flush or _spawn_flush
    if env.get("CARR_GATE_REPLAY_ROOT"):
        # ops/gate-replay.py runs every wired hook over fixtures in a sandbox.
        # The record is still built and written there; it never reaches the room.
        def spawn(path: Path) -> None:
            return None
    try:
        raw = (stdin or sys.stdin).read()
        payload = json.loads(raw) if raw.strip() else {}
        handle_event(payload, runtime_hint=runtime_hint, env=env,
                     presence_dir=presence_dir, host=host or this_host(),
                     spawn_flush=spawn)
    except Exception as exc:  # noqa: BLE001 — fail open, always
        _log(presence_dir, f"{runtime_hint} hook skipped: {exc.__class__.__name__}: {exc}")
    return 0


# ---------------------------------------------------------------------------
# lookup and send: what another session uses to reach one
# ---------------------------------------------------------------------------


def load_directory(path: Path = DIRECTORY_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"sessions": {}}
    return data if isinstance(data.get("sessions"), dict) else {"sessions": {}}


def lookup(directory: dict, query: str) -> dict:
    """Resolve a handle, alias, or title fragment to exactly one session."""
    q = (query or "").strip().lstrip("@").lower()
    if not q:
        raise PresenceError("name a session")
    sessions = list((directory.get("sessions") or {}).values())
    exact = [s for s in sessions if q in (str(s.get("handle") or "").lower(),
                                          str(s.get("name") or "").lower())]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [s for s in sessions if q in str(s.get("title") or "").lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise PresenceError(f"no live session matches {query!r}")
    names = ", ".join(sorted(str(s.get("handle")) for s in matches))
    raise PresenceError(f"{query!r} is ambiguous: {names}")


def send(directory: dict, query: str, text: str, *, seat: str, post=None,
         room: str = ROOM) -> dict:
    """Post a session_message receipt; the bridge carries it into the session.

    A receipt, not a turn: every seated desk answers a kind="turn" row, so a
    message for one session would cost each desk a dispatch. The bridge's
    session router (session_directory.addressed) delivers it to one handle."""
    target = lookup(directory, query)
    if not text.strip():
        raise PresenceError("empty message")
    body = json.dumps({"session_message": {"to": target["handle"], "text": text.strip()}},
                      separators=(",", ":"))
    return (post or _default_post)(body, seat, kind="receipt", room=room)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Announce, find and reach sessions via the Model Room.")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook", help="hook adapter entry: reads the hook payload on stdin")
    h.add_argument("runtime")
    f = sub.add_parser("flush", help="post one outbox record (spawned by the hook)")
    f.add_argument("path")
    a = sub.add_parser("announce", help="register from a launcher that has no SessionStart hook")
    a.add_argument("--runtime", required=True)
    a.add_argument("--session-id", required=True)
    a.add_argument("--name")
    a.add_argument("--title")
    a.add_argument("--cwd", default=os.getcwd())
    a.add_argument("--address-kind", choices=["claude-socket", "codex-thread", "none"])
    a.add_argument("--address")
    a.add_argument("--pid", type=int)
    sub.add_parser("list", help="sessions the bridge currently lists")
    lk = sub.add_parser("lookup")
    lk.add_argument("name")
    s = sub.add_parser("send", help="deliver a message to a session by name")
    s.add_argument("name")
    s.add_argument("text")
    s.add_argument("--seat", default=os.environ.get("CARR_SESSION_SEAT", "hermes"))
    args = p.parse_args(argv)

    if args.cmd == "hook":
        return run_hook(args.runtime)
    if args.cmd == "flush":
        flush(Path(args.path))
        return 0
    if args.cmd == "announce":
        env = dict(os.environ)
        if args.pid:
            env["CARR_SESSION_PID"] = str(args.pid)
        address = None
        if args.address_kind:
            address = {"kind": args.address_kind, "value": args.address}
        rec = build_record({"session_id": args.session_id, "cwd": args.cwd},
                           runtime_hint=args.runtime, env=env, host=this_host(),
                           name=args.name, title=args.title, address=address)
        path = _outbox(PRESENCE_DIR) / f"{rec['handle']}.json"
        _write_box(path, {"record": rec, "posted_at": None})
        ok = flush(path)
        print(json.dumps({"handle": rec["handle"], "name": rec["name"], "posted": ok}))
        return 0 if ok else 1
    directory = load_directory()
    try:
        if args.cmd == "list":
            for s in sorted(directory["sessions"].values(), key=lambda x: str(x.get("handle"))):
                print(f"{s.get('handle'):<18} {str(s.get('name') or '-'):<12} "
                      f"{'live' if s.get('live') else 'dead' if s.get('live') is False else '?':<5} "
                      f"{s.get('surface') or '-':<15} {s.get('title') or ''}")
            return 0
        if args.cmd == "lookup":
            print(json.dumps(lookup(directory, args.name), indent=2))
            return 0
        if args.cmd == "send":
            print(json.dumps(send(directory, args.name, args.text, seat=args.seat)))
            return 0
    except PresenceError as exc:
        print(f"session-presence: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    raise SystemExit(main())
