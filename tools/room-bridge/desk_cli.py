#!/usr/bin/env python3
"""desk_cli.py — the desk lifecycle: register / refresh / list / heartbeat.

A thin layer over desks.Registry (kind/socket/model/cwd — untouched, reused
as-is) and registry_ext (room_seat, last_seen — the two facts the room bridge
adds). No separate registry file: one hermes-desks.json, read by dispatch.py's
Hermes router and by tools/room-bridge/bridge.py alike, exactly the posture
registry_ext.py's own header states.

HEARTBEAT IS NOT presence-lease. Read live (list-verbs, 2026-08-22):
presence-lease is "acquire or refresh this actor's field-level Deal Room
presence for about three seconds" — a UI cursor lease keyed on (deal, field),
3-second expiry, that never enters event history. A desk is not a Deal Room
field and does not have a 3-second attention span; forcing this bridge's
heartbeat through it would be exactly the wrong-verb move rule 49c627cc warns
against. So heartbeat here is what the orchestrator's addendum specifies: a
last_seen timestamp this Mac writes locally into the desk's own registry
entry, on every successful poll/delivery cycle (bridge.py) or on demand (this
CLI's `heartbeat` subcommand) — the SAME write, registry_ext.stamp_heartbeat,
so the manual and automated paths never drift (rule a8c55a47).

Usage:
  desk_cli.py register <name> --kind claude-session --socket PATH --seat SEAT
  desk_cli.py register <name> --kind codex-session --model M --cwd DIR --seat SEAT
  desk_cli.py refresh <name> [--cwd DIR]     # re-probe, re-stamp; codex-session
                                              # desks may also repoint a stale cwd
  desk_cli.py list
  desk_cli.py heartbeat <name>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import registry_ext  # noqa: E402
from desks import DeskError  # noqa: E402


def cmd_register(reg: desks.Registry, args) -> int:
    entry = reg.register(
        args.name, args.kind, socket=args.socket, model=args.model, cwd=args.cwd,
        effort=getattr(args, "effort", None), sandbox=args.sandbox, add_dirs=args.add_dirs,
        permission_mode=getattr(args, "permission_mode", None),
    )
    if args.seat:
        entry = registry_ext.set_seat(args.name, args.seat, path=reg.path)
    if getattr(args, "room_listen", None):
        entry = registry_ext.set_room_listen(args.name, args.room_listen, path=reg.path)
    if args.profile:
        entry = registry_ext.set_profile(args.name, args.profile, path=reg.path)
    live = desks.is_live(entry.get("socket", "")) if entry.get("kind") in (
        "claude-session", "codex-live") else True
    registry_ext.stamp_heartbeat(args.name, live=live, path=reg.path)
    print(f"registered {args.name}: {entry}")
    return 0


def cmd_refresh(reg: desks.Registry, args) -> int:
    entries = reg.entries()
    if args.name not in entries:
        print(f"refresh: no desk named {args.name!r} — register it first", file=sys.stderr)
        return 2
    entry = entries[args.name]
    if args.cwd and entry.get("kind") in ("claude-desktop", "codex-session", "codex-live", "grok-cli"):
        # desks.Registry.register() always starts a codex-session entry with
        # thread_id=None (untouched, on purpose — see desks.py). Re-pointing a
        # stale cwd through it therefore also drops any old thread_id, which is
        # the right call here: a thread bound to a cwd that no longer exists
        # (e.g. a deleted worktree) cannot be resumed anyway, and the next
        # dispatch starts a fresh thread in the corrected directory.
        entry = reg.register(
            args.name, entry["kind"], socket=entry.get("socket"), model=entry.get("model"),
            effort=entry.get("effort"), cwd=args.cwd, sandbox=entry.get("sandbox"),
            add_dirs=entry.get("add_dirs"), permission_mode=entry.get("permission_mode"),
        )
        if entry.get("room_seat") is None:
            seat = entries[args.name].get("room_seat")
            if seat:
                registry_ext.set_seat(args.name, seat, path=reg.path)
        print(f"refresh: re-pointed {args.name} cwd -> {args.cwd}")
    if args.seat:
        registry_ext.set_seat(args.name, args.seat, path=reg.path)
    if args.profile:
        registry_ext.set_profile(args.name, args.profile, path=reg.path)
    try:
        live = True
        if entry.get("kind") in ("claude-session", "codex-live"):
            reg.resolve(args.name)  # raises DeskError if the socket is dead
            live = True
    except DeskError as e:
        registry_ext.stamp_heartbeat(args.name, live=False, path=reg.path)
        print(f"refresh: {args.name} is not live ({e.code}) — stamped last_seen anyway "
              f"so the gap is visible", file=sys.stderr)
        return 1
    registry_ext.stamp_heartbeat(args.name, live=live, path=reg.path)
    print(f"refresh: {args.name} is live, heartbeat stamped")
    return 0


def cmd_list(reg: desks.Registry, args) -> int:
    rows = reg.entries()
    if not rows:
        print("no desks registered")
        return 0
    for name, e in sorted(rows.items()):
        kind = e.get("kind", "?")
        seat = e.get("room_seat", "(no room seat — bridge will not route to it)")
        last_seen = e.get("last_seen", "never")
        last_live = e.get("last_live")
        if e.get("kind") in ("claude-desktop", "codex-session", "codex-live", "grok-cli") and e.get("model"):
            where = f"{e.get('model')} (effort={e.get('effort')})"
        else:
            where = e.get("socket") or "?"
        print(f"{name:20} {kind:15} seat={seat:10} live={last_live}  "
              f"last_seen={last_seen}  {where}")
    return 0


def cmd_heartbeat(reg: desks.Registry, args) -> int:
    entries = reg.entries()
    if args.name not in entries:
        print(f"heartbeat: no desk named {args.name!r}", file=sys.stderr)
        return 2
    entry = entries[args.name]
    live = True
    if entry.get("kind") in ("claude-session", "codex-live"):
        live = desks.is_live(entry.get("socket", ""))
    registry_ext.stamp_heartbeat(args.name, live=live, path=reg.path)
    print(f"heartbeat: {args.name} live={live}")
    return 0 if live else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registry", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register")
    r.add_argument("name")
    r.add_argument("--kind", required=True, choices=list(desks.KINDS))
    r.add_argument("--socket", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--effort", default=None, choices=list(desks.EFFORT_CHOICES))
    r.add_argument("--cwd", default=None)
    r.add_argument("--permission-mode", default=None,
                   help="dispatched desks use dontAsk only; other modes are refused and "
                        "permission needs go to the orchestrator")
    r.add_argument("--seat", default=None, help="the room seat this desk speaks for")
    r.add_argument("--room-listen", default=None, choices=list(registry_ext.ROOM_LISTEN_MODES),
                   help="'mention': hear people's turns, and other desks only when @seat-mentioned")
    r.add_argument("--profile", default=None,
                   help="named agent profile this desk carries (builder, designer, "
                        "reviewer, doc) — presentation and routing only, never authority")
    r.add_argument("--sandbox", default=None,
                   choices=["read-only", "workspace-write", "danger-full-access"])
    r.add_argument("--add-dir", dest="add_dirs", action="append", default=None)

    f = sub.add_parser("refresh")
    f.add_argument("name")
    f.add_argument("--cwd", default=None, help="repoint a stale codex-session cwd")
    f.add_argument("--seat", default=None)
    f.add_argument("--profile", default=None,
                   help="bind a named agent profile to this desk (presentation only)")

    sub.add_parser("list")

    h = sub.add_parser("heartbeat")
    h.add_argument("name")

    a = p.parse_args(argv)
    reg = desks.Registry(a.registry) if a.registry else desks.Registry()

    try:
        if a.cmd == "register":
            return cmd_register(reg, a)
        if a.cmd == "refresh":
            return cmd_refresh(reg, a)
        if a.cmd == "list":
            return cmd_list(reg, a)
        if a.cmd == "heartbeat":
            return cmd_heartbeat(reg, a)
    except DeskError as e:
        print(f"refused ({e.code}): {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
