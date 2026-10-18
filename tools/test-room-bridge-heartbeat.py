#!/usr/bin/env python3
"""Unit proof for the room bridge's observatory heartbeat — the receipt turn
the Model Room panel derives its desk roster, liveness, cursor lag and cycle
age from (Joe's ruling 0892c539: the panel reads ONLY the wire).

WHAT MUST HOLD, and why each one is worth a test rather than a comment:

  THE THROTTLE IS REAL AND PERSISTED. launchd fires bridge.py far more often
  than five minutes. An unthrottled heartbeat would post a receipt every cycle
  and bury the conversation the panel exists to show, so "at most once per
  five minutes" is a correctness property of the wire, not a preference. And
  the throttle has to survive between invocations — the bridge is a
  poll-and-exit process, so an in-memory timer would never fire twice.

  A NEVER-POSTED BRIDGE POSTS IMMEDIATELY. Including one upgraded from a state
  file written before this key existed: the panel would otherwise show no
  desks at all for the first five minutes after a deploy.

  THE BODY IS MACHINE-READABLE AND COMPLETE. The panel parses it; a desk with
  no room_seat must still appear (that is exactly the panel's "no wire
  registered" dormant case, and omitting it makes an unwired desk look like a
  desk that was never registered).

No socket, no subprocess, no network, no live desk — every collaborator is a
plain parameter, the same acceptance bar tools/test-room-bridge-cycle.py sets.

Run:  python3 tools/test-room-bridge-heartbeat.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "room-bridge"))

import bridge  # noqa: E402
import state as state_mod  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {e!r}")
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


class RecordingRoom:
    def __init__(self):
        self.calls = []

    def add_room_turn(self, *, body, seat, kind="turn", room="partner-line", msg_id=None):
        self.calls.append({"body": body, "seat": seat, "kind": kind, "msg_id": msg_id})
        return {"ok": True, "seq": 900 + len(self.calls)}


DESKS = {
    "joe-desk": {"kind": "claude-session", "room_seat": "claude",
                 "last_seen": "2026-08-22T14:00:00+00:00", "last_live": True,
                 "last_auth": True},
    "codex-desk": {"kind": "codex-session", "room_seat": "codex",
                   "last_seen": "2026-08-22T13:58:00+00:00", "last_live": False,
                   "last_auth": False},
    # never probed successfully — the panel must render this as UNKNOWN
    "unwired-desk": {"kind": "claude-session"},
}


def a_bridge_that_has_never_spoken_posts_its_first_heartbeat_at_once():
    state = state_mod.default_state()
    room = RecordingRoom()
    posted = bridge.post_heartbeat(state, DESKS, add_room_turn=room.add_room_turn,
                                    cursor=68, now="2026-08-22T14:00:00+00:00")
    assert posted is not None, "a bridge with no recorded heartbeat must post one"
    assert len(room.calls) == 1, room.calls
    assert room.calls[0]["seat"] == "hermes", room.calls[0]
    assert room.calls[0]["kind"] == "receipt", room.calls[0]
    assert state_mod.get_heartbeat_at(state) == "2026-08-22T14:00:00+00:00", state


def a_state_file_written_before_this_key_existed_still_posts():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "room-bridge-state.json"
        path.write_text(json.dumps({"last_seq": 41, "desks": {}}))
        state = state_mod.load_state(path)
        assert "last_heartbeat_at" in state, state
        assert bridge.heartbeat_due(state, now="2026-08-22T14:00:00+00:00") is True


def a_second_cycle_inside_five_minutes_is_throttled_and_posts_nothing():
    state = state_mod.default_state()
    room = RecordingRoom()
    bridge.post_heartbeat(state, DESKS, add_room_turn=room.add_room_turn,
                          cursor=68, now="2026-08-22T14:00:00+00:00")
    # launchd fires again a minute later, and again four minutes after that —
    # the second is inside the window, the third lands exactly on it.
    again = bridge.post_heartbeat(state, DESKS, add_room_turn=room.add_room_turn,
                                   cursor=69, now="2026-08-22T14:01:00+00:00")
    assert again is None, "a heartbeat inside the interval must be throttled"
    assert len(room.calls) == 1, room.calls
    assert state_mod.get_heartbeat_at(state) == "2026-08-22T14:00:00+00:00", state

    at_the_boundary = bridge.post_heartbeat(state, DESKS, add_room_turn=room.add_room_turn,
                                             cursor=70, now="2026-08-22T14:05:00+00:00")
    assert at_the_boundary is not None, "five minutes elapsed is due, not throttled"
    assert len(room.calls) == 2, room.calls
    assert state_mod.get_heartbeat_at(state) == "2026-08-22T14:05:00+00:00", state


def the_throttle_survives_the_process_exiting_between_cycles():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "room-bridge-state.json"
        room = RecordingRoom()
        first = state_mod.load_state(path)
        bridge.post_heartbeat(first, DESKS, add_room_turn=room.add_room_turn,
                              cursor=68, now="2026-08-22T14:00:00+00:00")
        state_mod.save_state(path, first)

        # A whole new invocation, exactly as launchd runs it.
        second = state_mod.load_state(path)
        assert bridge.heartbeat_due(second, now="2026-08-22T14:02:00+00:00") is False, second
        assert bridge.heartbeat_due(second, now="2026-08-22T14:09:00+00:00") is True, second


def a_broken_or_backwards_timestamp_fails_closed_rather_than_flooding():
    state = state_mod.default_state()
    state["last_heartbeat_at"] = "not-a-timestamp"
    assert bridge.heartbeat_due(state, now="2026-08-22T14:00:00+00:00") is False


def the_body_carries_every_registered_desk_including_the_unwired_one():
    payload = json.loads(bridge.heartbeat_body(DESKS, 68, "2026-08-22T14:00:00+00:00"))
    hb = payload["heartbeat"]
    assert hb["cursor"] == 68, hb
    assert hb["cycle_at"] == "2026-08-22T14:00:00+00:00", hb
    by_name = {d["name"]: d for d in hb["desks"]}
    assert set(by_name) == {"joe-desk", "codex-desk", "unwired-desk"}, by_name
    # EXACT shape, not inclusion: an extra field slipping into the wire contract
    # is exactly the class of defect an inclusion-only assertion lets through.
    assert by_name["joe-desk"] == {"name": "joe-desk", "seat": "claude", "live": True,
                                   "last_seen": "2026-08-22T14:00:00+00:00",
                                   "auth": True, "profile": None}, by_name
    assert by_name["codex-desk"]["live"] is False, by_name
    assert by_name["codex-desk"]["auth"] is False, by_name
    # the dormant case the panel renders as "no wire registered"
    assert by_name["unwired-desk"]["seat"] is None, by_name
    assert by_name["unwired-desk"]["live"] is False, by_name
    # NULL, NOT FALSE. A desk whose vendor CLI could not be asked has not been
    # shown to be signed out, and publishing false here would paint the panel
    # red on any machine missing a CLI.
    assert by_name["unwired-desk"]["auth"] is None, by_name

    # a non-boolean that somehow reached the registry is normalised to null
    odd = {"x": {"room_seat": "claude", "last_live": True, "last_auth": "yes"}}
    assert json.loads(bridge.heartbeat_body(odd, 1, "t"))["heartbeat"]["desks"][0]["auth"] is None
    # compact JSON: the wire carries turns, not pretty-printed documents
    assert ", " not in bridge.heartbeat_body(DESKS, 68, "2026-08-22T14:00:00+00:00")


PROFILES = [
    {"key": "builder", "name": "Builder", "model": "opus", "desk": "joe-desk", "status": "active"},
    {"key": "doc", "name": "Doc", "model": None, "desk": None, "status": "parked"},
]


def the_body_republishes_the_profile_roster_when_one_is_known():
    # Named agent profiles (loop 520): the NAME persists, the model is staffing
    # detail, and presence is REPUBLISHED, not assumed — any feed window must
    # contain current profile truth, so the roster rides the throttled
    # heartbeat beside the desks.
    payload = json.loads(bridge.heartbeat_body(
        DESKS, 68, "2026-08-22T14:00:00+00:00", profiles=PROFILES))
    hb = payload["heartbeat"]
    assert hb["profiles"] == PROFILES, hb
    # desks and cursor are untouched by the roster riding along
    assert {d["name"] for d in hb["desks"]} == {"joe-desk", "codex-desk", "unwired-desk"}, hb
    assert hb["cursor"] == 68, hb


def a_roster_that_cannot_be_fetched_degrades_to_an_absent_key():
    # The heartbeat must never die because the roster read failed: desk truth
    # still ships, and the ABSENT key says honestly that profile truth is
    # unknown this window — never an empty list, which would read as "no
    # profiles exist".
    payload = json.loads(bridge.heartbeat_body(
        DESKS, 68, "2026-08-22T14:00:00+00:00", profiles=None))
    assert "profiles" not in payload["heartbeat"], payload


def a_desk_bound_to_a_profile_carries_the_binding_on_the_wire():
    bound = {"joe-desk": {"kind": "claude-session", "room_seat": "claude",
                          "last_live": True, "profile": "builder"}}
    row = json.loads(bridge.heartbeat_body(bound, 1, "t"))["heartbeat"]["desks"][0]
    assert row["profile"] == "builder", row


def post_heartbeat_carries_the_roster_through():
    state = state_mod.default_state()
    room = RecordingRoom()
    bridge.post_heartbeat(state, DESKS, add_room_turn=room.add_room_turn,
                          cursor=68, now="2026-08-22T14:00:00+00:00",
                          profiles=PROFILES)
    body = json.loads(room.calls[0]["body"])
    assert body["heartbeat"]["profiles"] == PROFILES, body


def stale_pid_shared_sessions_do_not_grow_heartbeat_forever():
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "room-bridge"))
    import session_directory
    now = "2026-09-29T12:00:00+00:00"
    old = "2026-09-29T11:00:00+00:00"
    data = session_directory.empty()
    data["sessions"] = {f"codex-{n:08x}": {
        "runtime": "codex", "surface": "codex-cli", "host": "mac-studio", "pid": 49715,
        "beat_at": old, "seen_at": old, "last_live_at": old, "title": "x" * 100,
        "address": {"kind": "codex-thread", "value": str(n)},
    } for n in range(100)}
    expired = session_directory.refresh(data, now=now, host="mac-studio", probe=lambda _: True)
    assert len(expired) == 100, len(expired)
    assert session_directory.roster(data) == []


def heartbeat_stays_within_the_room_contract_for_a_large_live_roster():
    sessions = [{"handle": f"codex-{n:08x}", "title": "Review " + "x" * 140,
                 "runtime": "codex", "surface": "codex-cli", "model": "gpt-6-sol",
                 "live": True, "last_live_at": "2026-09-29T12:00:00Z"} for n in range(150)]
    body = bridge.heartbeat_body(DESKS, 68, "2026-09-29T12:00:00Z", sessions=sessions)
    assert len(body) <= 20000, len(body)
    payload = json.loads(body)["heartbeat"]
    assert payload["sessions_total"] == 150
    assert payload["sessions_truncated"] > 0


def main() -> int:
    check("a bridge that has never spoken posts its first heartbeat at once",
          a_bridge_that_has_never_spoken_posts_its_first_heartbeat_at_once)
    check("a state file written before the heartbeat existed still posts one",
          a_state_file_written_before_this_key_existed_still_posts)
    check("a second cycle inside five minutes is throttled; the boundary is due",
          a_second_cycle_inside_five_minutes_is_throttled_and_posts_nothing)
    check("the throttle survives the process exiting between launchd cycles",
          the_throttle_survives_the_process_exiting_between_cycles)
    check("a broken or backwards timestamp fails closed rather than flooding",
          a_broken_or_backwards_timestamp_fails_closed_rather_than_flooding)
    check("the body republishes the profile roster when one is known",
          the_body_republishes_the_profile_roster_when_one_is_known)
    check("a roster that cannot be fetched degrades to an absent key",
          a_roster_that_cannot_be_fetched_degrades_to_an_absent_key)
    check("a desk bound to a profile carries the binding on the wire",
          a_desk_bound_to_a_profile_carries_the_binding_on_the_wire)
    check("post_heartbeat carries the roster through",
          post_heartbeat_carries_the_roster_through)
    check("stale shared pid sessions expire from heartbeat",
          stale_pid_shared_sessions_do_not_grow_heartbeat_forever)
    check("large live roster stays within room body contract",
          heartbeat_stays_within_the_room_contract_for_a_large_live_roster)
    check("the receipt body carries every registered desk, unwired ones included",
          the_body_carries_every_registered_desk_including_the_unwired_one)

    print()
    if FAILURES:
        print(f"room-bridge heartbeat: {len(FAILURES)} FAILED")
        return 1
    print("room-bridge heartbeat: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
