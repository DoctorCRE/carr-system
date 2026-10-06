#!/usr/bin/env python3
"""Contract tests for session_directory.py — the bridge's view of live sessions.

Sessions announce themselves into the Model Room (session_presence.py). The
bridge is the one writer of the directory built from those announcements: it
reads them off the wire, probes each local session every cycle, expires the
ones that stopped answering, and routes an @-addressed room turn into the
session it names. The desk registry (hermes-desks.json) is untouched by all of
this; a pid socket is still refused as a Hermes dispatch target there.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import session_directory as sd  # noqa: E402
import state as state_mod  # noqa: E402

NOW = "2026-09-27T13:00:00+00:00"
LATER = "2026-09-27T13:20:00+00:00"
HOST = "studio"


def presence(handle, *, runtime="codex", event="announce", host=HOST, name=None,
             title=None, address=None, seq=10, beat_at=NOW, surface=None):
    rec = {"schema": "session-presence/v1", "event": event, "runtime": runtime,
           "session_id": "sid-" + handle, "handle": handle, "name": name, "title": title,
           "cwd": "/w", "host": host, "pid": 123, "surface": surface,
           "address": address or {"kind": "codex-thread", "value": "sid-" + handle},
           "announced_at": NOW, "beat_at": beat_at}
    return {"kind": "receipt", "seat": runtime, "seq": seq, "msg_id": f"m-{seq}",
            "body": json.dumps({"session_presence": rec})}


def turn(body, seq=20, seat="claude", kind="turn"):
    return {"kind": kind, "seat": seat, "seq": seq, "msg_id": f"t-{seq}", "body": body}


def test_announce_is_ingested():
    data = sd.empty()
    changed = sd.ingest(data, [presence("codex-6394537b", name="sol", title="Orchestrator")],
                        host=HOST, now=NOW)
    assert changed == ["codex-6394537b"]
    e = data["sessions"]["codex-6394537b"]
    assert e["name"] == "sol" and e["title"] == "Orchestrator" and e["local"] is True
    assert e["last_live_at"] is None


def test_ordinary_turns_and_bad_bodies_are_ignored():
    data = sd.empty()
    junk = [turn("hello"), {"kind": "receipt", "seq": 3, "body": "{not json"},
            {"kind": "receipt", "seq": 4, "body": json.dumps({"session_presence": {"handle": "BAD HANDLE"}})},
            {"kind": "receipt", "seq": 5, "body": json.dumps({"heartbeat": {}})}]
    assert sd.ingest(data, junk, host=HOST, now=NOW) == []
    assert data["sessions"] == {}


def test_reannounce_updates_and_depart_removes():
    data = sd.empty()
    sd.ingest(data, [presence("claude-51fa95bb", runtime="claude")], host=HOST, now=NOW)
    sd.ingest(data, [presence("claude-51fa95bb", runtime="claude", title="Now titled", seq=11)],
              host=HOST, now=NOW)
    assert data["sessions"]["claude-51fa95bb"]["title"] == "Now titled"
    sd.ingest(data, [presence("claude-51fa95bb", runtime="claude", event="depart", seq=12)],
              host=HOST, now=NOW)
    assert "claude-51fa95bb" not in data["sessions"]


def test_alias_colliding_with_a_desk_seat_is_dropped():
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="codex")], host=HOST, now=NOW,
              reserved={"codex", "claude", "codex-desk"})
    assert data["sessions"]["codex-6394537b"]["name"] is None


def test_live_probe_stamps_and_dead_local_session_expires():
    data = sd.empty()
    sd.ingest(data, [presence("codex-aaaaaaaa"), presence("codex-bbbbbbbb", seq=11)],
              host=HOST, now=NOW)
    live = {"codex-aaaaaaaa": True, "codex-bbbbbbbb": False}
    expired = sd.refresh(data, now=NOW, host=HOST, probe=lambda e: live[e["handle"]])
    assert expired == []
    assert data["sessions"]["codex-aaaaaaaa"]["last_live_at"] == NOW
    assert data["sessions"]["codex-aaaaaaaa"]["live"] is True
    assert data["sessions"]["codex-bbbbbbbb"]["live"] is False
    expired = sd.refresh(data, now=LATER, host=HOST, probe=lambda e: live[e["handle"]])
    assert expired == ["codex-bbbbbbbb"]
    assert "codex-aaaaaaaa" not in expired and "codex-aaaaaaaa" in data["sessions"]


def test_remote_session_is_not_probed_and_expires_on_stale_beat():
    data = sd.empty()
    sd.ingest(data, [presence("claude-cccccccc", runtime="claude", host="macbook",
                              address={"kind": "claude-socket", "value": "/tmp/cc-socks/1.sock"})],
              host=HOST, now=NOW)
    probed = []
    sd.refresh(data, now=NOW, host=HOST, probe=lambda e: probed.append(e) or True)
    assert probed == [] and data["sessions"]["claude-cccccccc"]["live"] is None
    far = "2026-09-27T14:00:00+00:00"
    assert sd.refresh(data, now=far, host=HOST, probe=lambda e: True) == ["claude-cccccccc"]


def test_mentions_match_handle_and_alias_on_word_boundaries():
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="sol")], host=HOST, now=NOW)
    assert sd.mentioned(data, turn("@sol please merge")) == ["codex-6394537b"]
    assert sd.mentioned(data, turn("ping @codex-6394537b now")) == ["codex-6394537b"]
    assert sd.mentioned(data, turn("@sol-orchestrator hi")) == []
    assert sd.mentioned(data, turn("mail user@sol")) == []
    assert sd.mentioned(data, turn("@sol x", kind="receipt")) == []


class Sink:
    def __init__(self):
        self.posts = []

    def __call__(self, body, seat, **kw):
        self.posts.append({"body": body, "seat": seat, **kw})
        return {"ok": True}


def test_route_delivers_once_per_turn_across_cycles():
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="sol")], host=HOST, now=NOW)
    sd.refresh(data, now=NOW, host=HOST, probe=lambda e: True)
    state = state_mod.default_state()
    delivered = []

    def deliver(entry, text, msg_id):
        delivered.append((entry["handle"], text, msg_id))
        return {"status": "delivered"}

    sink = Sink()
    t = turn("@sol please merge #1341")
    out = sd.route(data, [t], state, deliver=deliver, add_room_turn=sink)
    assert [o["status"] for o in out] == ["delivered"]
    assert delivered[0][0] == "codex-6394537b"
    assert delivered[0][1] == "[model-room · claude] @sol please merge #1341"
    assert sd.route(data, [t], state, deliver=deliver, add_room_turn=sink) == []
    assert len(delivered) == 1
    receipt = json.loads(sink.posts[0]["body"])["session_delivery"]
    assert receipt["handle"] == "codex-6394537b" and receipt["status"] == "delivered"
    assert sink.posts[0]["seat"] == "hermes" and sink.posts[0]["kind"] == "receipt"


def test_session_message_receipt_reaches_only_its_target_and_no_desk():
    """PR #1345 review, finding 2: `session-presence send` posts a receipt so no
    seated desk spends a dispatch on it; the directory router delivers it to the
    one handle it names (by handle or alias), once."""
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="sol"), presence("codex-aaaaaaaa", seq=11)],
              host=HOST, now=NOW)
    sd.refresh(data, now=NOW, host=HOST, probe=lambda e: True)
    msg = {"kind": "receipt", "seat": "claude", "seq": 30, "msg_id": "t-30",
           "body": json.dumps({"session_message": {"to": "codex-6394537b", "text": "please confirm"}})}
    # the desk fan-out filter: a receipt is queued onto no desk
    assert state_mod.route_turn(state_mod.default_state(), msg, {"codex-desk": "codex"}) == []
    delivered = []
    state = state_mod.default_state()
    out = sd.route(data, [msg], state,
                   deliver=lambda e, text, mid: delivered.append((e["handle"], text)) or {"status": "delivered"},
                   add_room_turn=Sink())
    assert delivered == [("codex-6394537b", "[model-room · claude] please confirm")]
    assert [o["handle"] for o in out] == ["codex-6394537b"]
    assert sd.route(data, [msg], state, deliver=lambda *a: {"status": "delivered"}, add_room_turn=Sink()) == []
    # a message to a handle that is not listed, or a malformed one, reaches nobody
    for body in ({"session_message": {"to": "codex-ffffffff", "text": "x"}},
                 {"session_message": {"to": "codex-6394537b"}}, {"session_message": "x"}):
        stray = {**msg, "msg_id": f"t-{len(str(body))}", "body": json.dumps(body)}
        assert sd.addressed(data, stray) == []


def test_route_to_a_dead_session_is_reported_not_attempted():
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="sol")], host=HOST, now=NOW)
    sd.refresh(data, now=NOW, host=HOST, probe=lambda e: False)
    sink = Sink()
    out = sd.route(data, [turn("@sol hi")], state_mod.default_state(),
                   deliver=lambda *a: (_ for _ in ()).throw(AssertionError("must not deliver")),
                   add_room_turn=sink)
    assert out[0]["status"] == "not_live"


def test_deliverer_errors_are_contained_as_failed_receipts():
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="sol")], host=HOST, now=NOW)
    sd.refresh(data, now=NOW, host=HOST, probe=lambda e: True)
    sink = Sink()

    def boom(*a):
        raise OSError("socket refused")

    out = sd.route(data, [turn("@sol hi")], state_mod.default_state(), deliver=boom, add_room_turn=sink)
    assert out[0]["status"] == "failed" and "socket refused" in out[0]["detail"]


def test_default_deliverer_by_runtime():
    calls = []
    claude = {"handle": "claude-51fa95bb", "runtime": "claude", "local": True,
              "address": {"kind": "claude-socket", "value": "/tmp/cc-socks/1.sock"}}
    codex = {"handle": "codex-6394537b", "runtime": "codex", "local": True,
             "address": {"kind": "codex-thread", "value": "thread-1"}}
    grok = {"handle": "grok-12345678", "runtime": "grok", "local": True,
            "address": {"kind": "none", "value": None}}
    deliver = sd.make_deliverer(
        inject_claude=lambda sock, payload: calls.append(("claude", sock, payload)),
        codex_start_turn=lambda thread, text: calls.append(("codex", thread, text)) or {"status": "delivered"})
    assert deliver(claude, "hi", "t-1")["status"] == "delivered"
    assert calls[0][0] == "claude" and calls[0][1] == "/tmp/cc-socks/1.sock"
    assert calls[0][2]["type"] == "user" and calls[0][2]["message"]["content"] == "hi"
    assert calls[0][2]["origin"]["msg_id"] == "t-1"
    assert deliver(codex, "hi", "t-2")["status"] == "delivered"
    assert calls[1] == ("codex", "thread-1", "hi")
    assert deliver(grok, "hi", "t-3")["status"] == "no_inbound_address"
    # a Desktop-hosted Claude socket requires that session's own token, which
    # nobody else holds: the bridge must not pretend a silent drop delivered
    guarded = {**claude, "host_session_id": "local_210ea3de",
               "address": {"kind": "claude-socket", "value": "/tmp/cc-socks/2.sock", "auth": "token"}}
    out = deliver(guarded, "hi", "t-4")
    assert out["status"] == "needs_sendmessage" and "local_210ea3de" in out["detail"]
    assert len(calls) == 2  # nothing was injected into the guarded socket


def test_default_probe_by_runtime():
    probe = sd.make_probe(socket_live=lambda p: p == "/tmp/cc-socks/1.sock",
                          codex_owner=lambda t: "owner" if t == "thread-1" else None,
                          pid_alive=lambda pid: pid == 42)
    assert probe({"runtime": "claude", "address": {"kind": "claude-socket", "value": "/tmp/cc-socks/1.sock"}}) is True
    assert probe({"runtime": "claude", "address": {"kind": "none", "value": None}, "pid": 42}) is True
    assert probe({"runtime": "codex", "surface": "codex-desktop",
                  "address": {"kind": "codex-thread", "value": "thread-1"}}) is True
    assert probe({"runtime": "codex", "surface": "codex-desktop",
                  "address": {"kind": "codex-thread", "value": "thread-2"}}) is False
    assert probe({"runtime": "codex", "surface": "codex-cli", "pid": 42,
                  "address": {"kind": "codex-thread", "value": "thread-2"}}) is True
    assert probe({"runtime": "grok", "pid": 7, "address": {"kind": "none", "value": None}}) is False


def test_roster_rows_for_the_heartbeat():
    data = sd.empty()
    sd.ingest(data, [presence("codex-6394537b", name="sol", title="Orchestrator",
                              surface="codex-desktop")], host=HOST, now=NOW)
    sd.refresh(data, now=NOW, host=HOST, probe=lambda e: True)
    rows = sd.roster(data)
    assert rows == [{"handle": "codex-6394537b", "name": "sol", "runtime": "codex",
                     "surface": "codex-desktop", "title": "Orchestrator", "cwd": "/w",
                     "host": HOST, "live": True, "last_live_at": NOW,
                     "address": {"kind": "codex-thread", "value": "sid-codex-6394537b"},
                     "host_session_id": None}]


def test_directory_file_round_trip():
    path = Path(tempfile.mkdtemp()) / "session-directory.json"
    data = sd.load(path)
    assert data == sd.empty()
    sd.ingest(data, [presence("codex-6394537b")], host=HOST, now=NOW)
    sd.save(path, data)
    assert sd.load(path)["sessions"]["codex-6394537b"]["handle"] == "codex-6394537b"


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
        print(f"{failures} session_directory test(s) failed", file=sys.stderr)
        return 1
    print("all session_directory unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
