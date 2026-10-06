#!/usr/bin/env python3
"""Contract tests for mention-only desks in state.route_turn.

Found live 2026-09-24: with two auto-answering desks seated (flash and codex),
every desk reply was routed to the other desk, whose reply was routed back, about
once a minute ("No further response." / "No action taken.") until one seat was
stopped. A desk registered room_listen="mention" hears people's turns, and hears
another desk only when that desk @-mentions its seat. Desks on the default keep
hearing other desks: desk-to-desk conversation is by design
(tools/test-room-bridge-state.py pins that).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import state as state_mod  # noqa: E402

SEATS = {"flash": "flash", "codex-desk": "codex"}
QUIET = frozenset({"flash"})


def turn(msg_id, seat, body="hello"):
    return {"kind": "turn", "msg_id": msg_id, "seat": seat, "body": body, "seq": 1}


def route(st, t):
    return sorted(state_mod.route_turn(st, t, SEATS, mention_only=QUIET))


def test_unmentioned_human_turn_keeps_flash_idle():
    assert route(state_mod.default_state(), turn("m1", "joe")) == ["codex-desk"]


def test_mention_only_desk_does_not_hear_other_desk():
    assert route(state_mod.default_state(), turn("m2", "codex", "No action taken.")) == []


def test_default_desk_still_hears_other_desks():
    assert route(state_mod.default_state(), turn("m3", "flash", "done")) == ["codex-desk"]


def test_ping_pong_dies_after_one_hop():
    """Simulate the live loop: each reply becomes the next room turn."""
    st = state_mod.default_state()
    frontier = route(st, turn("h1", "joe"))
    hops = 0
    while frontier and hops < 10:
        hops += 1
        frontier = [d for name in frontier
                    for d in route(st, turn(f"r{hops}-{name}", SEATS[name], "ack"))]
    assert hops <= 2, hops


def test_explicit_mention_reaches_mention_only_desk():
    assert route(state_mod.default_state(), turn("m4", "codex", "@flash please review")) == ["flash"]


def test_mention_must_be_a_whole_seat_name():
    assert route(state_mod.default_state(), turn("m5", "codex", "@flashy thing")) == []


def test_flash_requires_a_mention_even_without_registry_listen_setting():
    st = state_mod.default_state()
    assert sorted(state_mod.route_turn(st, turn("m6", "codex"), SEATS)) == []


def test_bridge_reads_room_listen_from_registry_entries():
    entries = {"flash": {"room_seat": "flash", "room_listen": "mention"},
               "codex-desk": {"room_seat": "codex"}}
    assert bridge.mention_only_desks(entries) == frozenset({"flash"})


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc!r}", file=sys.stderr)
    if failures:
        print(f"{failures} route_turn test(s) failed", file=sys.stderr)
        return 1
    print("all route_turn unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
