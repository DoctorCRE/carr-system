#!/usr/bin/env python3
"""Integration proof for tools/room-bridge/bridge.py's run_once() — the full
poll-cycle orchestration (routing, delivery, async reply capture, sync reply
capture) wired together with fakes for read-room, add-room-turn and
dispatch.dispatch. No socket, no subprocess, no network, no live desk;
covers the claude-session async pending/reply-capture path across two poll
cycles and the codex-session synchronous reply path in one.

Run:  python3 tools/test-room-bridge-cycle.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "room-bridge"))

import desks  # noqa: E402
import bridge  # noqa: E402

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


def make_registry(path: Path) -> desks.Registry:
    path.write_text(json.dumps({"desks": {
        "joe-desk": {"kind": "claude-session", "socket": "/tmp/does-not-exist-room-bridge-test.sock",
                     "room_seat": "claude"},
        "codex-desk": {"kind": "codex-session", "model": "gpt-test", "cwd": "/tmp",
                       "thread_id": None, "room_seat": "codex"},
    }}))
    return desks.Registry(path)


class RecordingRoom:
    def __init__(self):
        self.calls = []

    def add_room_turn(self, *, body, seat, kind="turn", room="partner-line", msg_id=None):
        self.calls.append({"body": body, "seat": seat, "kind": kind, "msg_id": msg_id})
        return {"ok": True, "seq": 1000 + len(self.calls)}


class FakeReadRoom:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def __call__(self, after_seq, *, room="partner-line", limit=50):
        self.calls.append(after_seq)
        return {"turns": self.batches.pop(0) if self.batches else [], "more": False}


class OfflineQueueService:
    """A queue control plane with no targets and no external dependencies."""

    catalog: dict[str, dict] = {"targets": {}}

    @staticmethod
    def handle(turn, *, room):
        return {"handled": False}

    @staticmethod
    def reconcile_disabled_targets():
        return {"scanned": 0, "blocked": [], "diagnostics": []}


def run_offline_cycle(**kwargs):
    """Run one fully local cycle with deterministic desk and queue status."""
    with mock.patch.object(bridge, "probe_live", return_value=True):
        return bridge.run_once(
            queue_service=OfflineQueueService(),
            queue_projector=lambda **_kwargs: [],
            read_profiles=lambda: [],
            **kwargs,
        )


def fake_dispatch(name, task, *, registry, results_path, fresh=False, live_desktop=False):
    if name == "joe-desk":
        return {"msg_id": "dispatch-claude-1", "desk": name, "kind": "claude-session",
                "task": task, "dispatched_at": "2026-08-22T00:00:00+00:00",
                "status": "delivered", "detail": "the desk's socket accepted the turn"}
    if name == "codex-desk":
        return {"msg_id": "dispatch-codex-1", "desk": name, "kind": "codex-session",
                "task": task, "dispatched_at": "2026-08-22T00:00:00+00:00",
                "status": "completed", "result": "codex ack: got it"}
    raise AssertionError(f"unexpected desk in fake_dispatch: {name}")


def full_cycle_routes_delivers_and_captures_both_reply_shapes():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        reg = make_registry(tdp / "hermes-desks.json")
        state_path = tdp / "state.json"
        desk_state_dir = tdp / "desk-logs"
        desk_state_dir.mkdir()
        room = RecordingRoom()

        human_turn = {"seq": 1, "seat": "human", "sponsor": "joe", "body": "status check please",
                      "msg_id": "room-turn-1", "kind": "turn"}
        fake_read = FakeReadRoom([[human_turn], []])

        summary1 = run_offline_cycle(
            registry=reg, state_path=state_path, results_path=tdp / "results.jsonl",
            read_room=fake_read, add_room_turn=room.add_room_turn,
            dispatch_fn=fake_dispatch, desk_state_dir=desk_state_dir, log=lambda *a: None,
            probe_auth=lambda entry: None,
        )

        assert summary1["turns_read"] == 1, summary1
        assert summary1["last_seq"] == 1, summary1
        outcomes = {d["desk"]: d["outcome"] for d in summary1["delivered"]}
        assert outcomes.get("joe-desk") == "delivered_async", outcomes
        assert outcomes.get("codex-desk") == "replied_sync", outcomes

        # codex answered synchronously in the SAME cycle — one reply posted,
        # under the codex seat, kind turn.
        codex_replies = [c for c in room.calls if c["seat"] == "codex" and c["kind"] == "turn"]
        assert len(codex_replies) == 1, room.calls
        assert "codex ack" in codex_replies[0]["body"], codex_replies

        # claude has not answered yet — no claude-seat turn posted, and the
        # bridge's state remembers a turn is in flight.
        claude_replies = [c for c in room.calls if c["seat"] == "claude"]
        assert claude_replies == [], claude_replies

        import state as state_mod  # local import: path only set up above
        saved = state_mod.load_state(state_path)
        pending = state_mod.get_pending(saved, "joe-desk")
        assert pending is not None and pending["log_offset"] == 0, pending

        # the desk registry itself was stamped with a heartbeat this cycle
        reg_after = json.loads((tdp / "hermes-desks.json").read_text())
        assert "last_seen" in reg_after["desks"]["joe-desk"], reg_after
        assert "last_seen" in reg_after["desks"]["codex-desk"], reg_after
        assert reg_after["desks"]["joe-desk"]["last_live"] is True, reg_after
        assert reg_after["desks"]["codex-desk"]["last_live"] is True, reg_after

        # Now the claude desk "answers" — append a stream-json result line to
        # its log, exactly the shape --output-format stream-json produces.
        log_path = desk_state_dir / "joe-desk.log"
        log_path.write_text(
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "on it"}]}}) + "\n"
            + json.dumps({"type": "result", "result": "all green, nothing on fire"}) + "\n"
        )

        summary2 = run_offline_cycle(
            registry=reg, state_path=state_path, results_path=tdp / "results.jsonl",
            read_room=fake_read, add_room_turn=room.add_room_turn,
            dispatch_fn=fake_dispatch, desk_state_dir=desk_state_dir, log=lambda *a: None,
            probe_auth=lambda entry: None,
        )
        assert summary2["turns_read"] == 0, summary2
        outcomes2 = {d["desk"]: d["outcome"] for d in summary2["delivered"] if "desk" in d}
        assert outcomes2.get("joe-desk") == "replied", outcomes2

        claude_replies = [c for c in room.calls if c["seat"] == "claude" and c["kind"] == "turn"]
        assert len(claude_replies) == 1, room.calls
        assert "all green" in claude_replies[0]["body"], claude_replies

        saved2 = state_mod.load_state(state_path)
        assert state_mod.get_pending(saved2, "joe-desk") is None, saved2


check("one human turn: claude answers async two cycles later, codex answers "
      "sync in the first cycle, echo-free and heartbeat stamped",
      full_cycle_routes_delivers_and_captures_both_reply_shapes)


def a_pending_desk_is_never_offered_a_second_turn_before_it_answers():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        reg = make_registry(tdp / "hermes-desks.json")
        state_path = tdp / "state.json"
        desk_state_dir = tdp / "desk-logs"
        desk_state_dir.mkdir()
        room = RecordingRoom()

        turns = [
            {"seq": 1, "seat": "human", "sponsor": "joe", "body": "first",
             "msg_id": "t1", "kind": "turn"},
            {"seq": 2, "seat": "human", "sponsor": "joe", "body": "second",
             "msg_id": "t2", "kind": "turn"},
        ]
        fake_read = FakeReadRoom([turns, []])

        delivered_calls = {"n": 0}

        def counting_dispatch(name, task, *, registry, results_path, fresh=False, live_desktop=False):
            if name == "joe-desk":
                delivered_calls["n"] += 1
            return fake_dispatch(name, task, registry=registry, results_path=results_path)

        run_offline_cycle(
            registry=reg, state_path=state_path, results_path=tdp / "results.jsonl",
            read_room=fake_read, add_room_turn=room.add_room_turn,
            dispatch_fn=counting_dispatch, desk_state_dir=desk_state_dir, log=lambda *a: None,
            probe_auth=lambda entry: None,
        )
        # both turns were queued onto joe-desk, but only ONE was ever
        # actually dispatched this cycle — the second stays queued behind it.
        assert delivered_calls["n"] == 1, delivered_calls

        import state as state_mod
        saved = state_mod.load_state(state_path)
        assert len(saved["desks"]["joe-desk"]["queue"]) == 1, saved["desks"]["joe-desk"]


check("a desk with a turn already in flight is never handed a second one "
      "before it answers — ordering is preserved, not interleaved",
      a_pending_desk_is_never_offered_a_second_turn_before_it_answers)


print()
if FAILURES:
    print(f"room-bridge cycle integration: {len(FAILURES)} FAILED")
    sys.exit(1)
print("room-bridge cycle integration: DONE — every assertion held")
