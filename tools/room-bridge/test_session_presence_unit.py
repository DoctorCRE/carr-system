#!/usr/bin/env python3
"""Contract tests for session_presence.py — every session announcing itself.

Requirement, 2026-09-27: "sessions need to post theirself to the model room at session
start so that every other session can reach them easily", widened the same day
to "not just claude or codex, every session no matter what model".

What these pin:
  * one runtime-neutral record shape, whichever runtime's hook fed it
    (Claude Code, Codex Desktop/CLI, Grok Build — Grok reads Claude's hooks);
  * the hook NEVER blocks a session start: no network inside the hook, and an
    unreachable room costs a logged notice, never a nonzero exit or stdout;
  * nothing secret rides along (the Claude messaging token lives in the same
    environment as the socket path);
  * a heartbeat that stays local unless there is something new to say.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import session_presence as sp  # noqa: E402

CLAUDE_ID = "f4d5b78a-e7d8-4bd1-9006-652a51fa95bb"
CODEX_ID = "01a0e2da-dabe-7642-a06a-1a686394537b"


def claude_env(**extra):
    env = {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/28283.sock",
           "CLAUDE_CODE_MESSAGING_TOKEN": "SECRET-TOKEN-VALUE",
           "CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "CLAUDE_PID": "28283"}
    env.update(extra)
    return env


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="presence-"))


def test_this_host_survives_a_scheduler_path_without_sbin():
    """launchd jobs run with a PATH that can lack /usr/sbin. A bare `scutil`
    then fails, the DHCP name "Mac" wins, and the bridge marks every session
    on this machine other_host (2026-09-27: every Codex delivery refused)."""
    import os
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); import session_presence as sp; "
            "print(sp.this_host())") % str(HERE)
    with_sbin = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}).stdout.strip()
    without = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin"}).stdout.strip()
    assert without == with_sbin, f"host differs by PATH: {without!r} vs {with_sbin!r}"


def test_this_host_uses_system_scutil_and_preserves_fallback():
    """A PATH-shadowed scutil must not choose the bridge's routing host."""
    with patch.object(sp.subprocess, "run", return_value=type("Result", (), {"stdout": "Studio-A\n"})()) as run:
        assert sp.this_host() == "studio-a"
    run.assert_called_once_with(["/usr/sbin/scutil", "--get", "LocalHostName"],
                                capture_output=True, text=True, timeout=1)

    with patch.object(sp.subprocess, "run", side_effect=OSError("unavailable")):
        with patch.object(sp.socket, "gethostname", return_value="Fallback.local"):
            assert sp.this_host() == "fallback"


def test_claude_record_shape():
    rec = sp.build_record({"hook_event_name": "SessionStart", "session_id": CLAUDE_ID,
                           "cwd": "/Users/x/carr-system", "source": "startup"},
                          runtime_hint="claude", env=claude_env(), host="studio",
                          now="2026-09-27T13:00:00+00:00")
    assert rec["runtime"] == "claude"
    assert rec["session_id"] == CLAUDE_ID
    assert rec["handle"] == "claude-51fa95bb"  # last 8 hex of the id
    assert rec["address"] == {"kind": "claude-socket", "value": "/tmp/cc-socks/28283.sock",
                              "auth": "token"}
    assert rec["surface"] == "claude-desktop"
    assert rec["cwd"] == "/Users/x/carr-system"
    assert rec["host"] == "studio"
    assert rec["pid"] == 28283
    assert rec["schema"] == "session-presence/v1"


def test_token_socket_is_flagged_without_the_token():
    rec = sp.build_record({"session_id": CLAUDE_ID}, runtime_hint="claude",
                          env=claude_env(CLAUDE_CODE_HOST_SESSION_ID="local_210ea3de"), host="h")
    assert rec["address"]["auth"] == "token"
    assert rec["host_session_id"] == "local_210ea3de"
    plain = sp.build_record({"session_id": CLAUDE_ID}, runtime_hint="claude",
                            env={"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/flash.sock"}, host="h")
    assert plain["address"]["auth"] == "none"


def test_no_secret_in_record():
    rec = sp.build_record({"hook_event_name": "SessionStart", "session_id": CLAUDE_ID},
                          runtime_hint="claude", env=claude_env(), host="h")
    assert "SECRET-TOKEN-VALUE" not in json.dumps(rec)


def test_codex_record_uses_thread_address_and_desktop_surface():
    d = tmpdir()
    rollout = d / "rollout.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": CODEX_ID, "originator": "Codex Desktop"}}) + "\n")
    rec = sp.build_record({"hook_event_name": "SessionStart", "session_id": CODEX_ID,
                           "cwd": "/Users/x/carr-system", "source": "startup",
                           "transcript_path": str(rollout), "model": "gpt-5.6-sol"},
                          runtime_hint="codex", env={}, host="h")
    assert rec["runtime"] == "codex"
    assert rec["address"] == {"kind": "codex-thread", "value": CODEX_ID}
    assert rec["surface"] == "codex-desktop"
    assert rec["model"] == "gpt-5.6-sol"
    assert rec["handle"] == "codex-6394537b"


def test_codex_cli_surface():
    d = tmpdir()
    rollout = d / "rollout.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {
        "originator": "codex_cli_rs"}}) + "\n")
    rec = sp.build_record({"hook_event_name": "SessionStart", "session_id": CODEX_ID,
                           "transcript_path": str(rollout)}, runtime_hint="codex", env={}, host="h")
    assert rec["surface"] == "codex-cli"


def test_grok_detected_even_through_claude_hook_entry():
    # Grok Build scans ~/.claude/settings.json, so the Claude-configured entry fires
    rec = sp.build_record({"hook_event_name": "SessionStart", "hookEventName": "session_start",
                           "sessionId": "g-123456789abc", "cwd": "/w"},
                          runtime_hint="claude", env={}, host="h")
    assert rec["runtime"] == "grok"
    assert rec["session_id"] == "g-123456789abc"
    assert rec["address"]["kind"] == "none"


def test_explicit_name_alias_is_validated():
    rec = sp.build_record({"session_id": CODEX_ID}, runtime_hint="codex",
                          env={"CARR_SESSION_NAME": "Sol"}, host="h")
    assert rec["name"] == "sol"
    bad = sp.build_record({"session_id": CODEX_ID}, runtime_hint="codex",
                          env={"CARR_SESSION_NAME": "no spaces; rm"}, host="h")
    assert bad["name"] is None


def test_title_from_prompt_is_one_short_line():
    rec = sp.build_record({"hook_event_name": "UserPromptSubmit", "session_id": CLAUDE_ID,
                           "prompt": "Fix the calendar prebrief\nsecond line " + "x" * 300},
                          runtime_hint="claude", env={}, host="h")
    assert rec["title"] == "Fix the calendar prebrief"
    long = sp.build_record({"session_id": CLAUDE_ID, "prompt": "y" * 300},
                           runtime_hint="claude", env={}, host="h")
    assert len(long["title"]) <= sp.TITLE_MAX


def test_missing_session_id_is_refused():
    try:
        sp.build_record({"hook_event_name": "SessionStart"}, runtime_hint="claude", env={}, host="h")
    except sp.PresenceError:
        return
    raise AssertionError("a record without a session id must be refused")


def test_hook_session_start_writes_outbox_and_spawns_no_inline_post():
    d = tmpdir()
    posted, spawned = [], []
    out, err = io.StringIO(), io.StringIO()
    rc = sp.run_hook("claude", stdin=io.StringIO(json.dumps({
        "hook_event_name": "SessionStart", "session_id": CLAUDE_ID, "cwd": "/w", "source": "startup"})),
        env=claude_env(), presence_dir=d, host="h",
        post=lambda *a, **k: posted.append(a), spawn_flush=lambda path: spawned.append(path),
        stdout=out, stderr=err)
    assert rc == 0
    assert out.getvalue() == ""          # SessionStart stdout becomes model context
    assert posted == []                  # never on the session's critical path
    assert len(spawned) == 1
    box = json.loads((d / "outbox" / "claude-51fa95bb.json").read_text())
    assert box["record"]["event"] == "announce" and box["posted_at"] is None


def test_gate_replay_never_reaches_the_room():
    # ops/gate-replay.py runs every wired hook in a sandbox; a replay must
    # never spawn the child that posts to the live Model Room
    d = tmpdir()
    spawned = []
    rc = sp.run_hook("claude", stdin=io.StringIO(json.dumps({
        "hook_event_name": "SessionStart", "session_id": CLAUDE_ID})),
        env={**claude_env(), "CARR_GATE_REPLAY_ROOT": "/tmp/replay"}, presence_dir=d, host="h",
        post=None, spawn_flush=lambda p: spawned.append(p), stdout=io.StringIO(), stderr=io.StringIO())
    assert rc == 0 and spawned == []


def test_hook_fails_open_on_garbage_and_logs():
    d = tmpdir()
    out = io.StringIO()
    rc = sp.run_hook("claude", stdin=io.StringIO("{not json"), env={}, presence_dir=d, host="h",
                     post=None, spawn_flush=lambda p: None, stdout=out, stderr=io.StringIO())
    assert rc == 0 and out.getvalue() == ""
    assert "presence notice" in (d / "presence.log").read_text()


def test_flush_posts_receipt_to_model_room_and_marks_posted():
    d = tmpdir()
    calls = []

    def post(body, seat, **kw):
        calls.append((body, seat, kw))
        return {"ok": True, "seq": 7}

    sp.run_hook("claude", stdin=io.StringIO(json.dumps({"hook_event_name": "SessionStart",
                "session_id": CLAUDE_ID})), env=claude_env(), presence_dir=d, host="h",
                post=None, spawn_flush=lambda p: None, stdout=io.StringIO(), stderr=io.StringIO())
    box_path = d / "outbox" / "claude-51fa95bb.json"
    assert sp.flush(box_path, post=post, room="model-room") is True
    body, seat, kw = calls[0]
    assert seat == "claude" and kw["kind"] == "receipt" and kw["room"] == "model-room"
    assert json.loads(body)["session_presence"]["handle"] == "claude-51fa95bb"
    assert json.loads(box_path.read_text())["posted_at"]


def test_flush_unreachable_room_keeps_outbox_pending_and_logs():
    d = tmpdir()

    def post(*a, **k):
        raise RuntimeError("add-room-turn failed (rc=1): network down")

    sp.run_hook("codex", stdin=io.StringIO(json.dumps({"hook_event_name": "SessionStart",
                "session_id": CODEX_ID})), env={}, presence_dir=d, host="h",
                post=None, spawn_flush=lambda p: None, stdout=io.StringIO(), stderr=io.StringIO())
    box_path = d / "outbox" / "codex-6394537b.json"
    assert sp.flush(box_path, post=post, room="model-room") is False
    assert json.loads(box_path.read_text())["posted_at"] is None
    assert "network down" in (d / "presence.log").read_text()


def test_beat_is_local_when_nothing_new():
    d = tmpdir()
    spawned = []
    common = dict(env=claude_env(), presence_dir=d, host="h", post=None,
                  spawn_flush=lambda p: spawned.append(p), stdout=io.StringIO(), stderr=io.StringIO())
    sp.run_hook("claude", stdin=io.StringIO(json.dumps({"hook_event_name": "SessionStart",
                "session_id": CLAUDE_ID})), **common)
    box_path = d / "outbox" / "claude-51fa95bb.json"
    box = json.loads(box_path.read_text())
    box["posted_at"] = box["record"]["announced_at"]
    box["record"]["title"] = "already titled"
    box_path.write_text(json.dumps(box))
    spawned.clear()
    sp.run_hook("claude", stdin=io.StringIO(json.dumps({"hook_event_name": "Stop",
                "session_id": CLAUDE_ID})), **common)
    assert spawned == []                 # nothing new, recently posted -> no network
    assert json.loads(box_path.read_text())["record"]["beat_at"]


def test_first_prompt_titles_the_session_and_reposts():
    d = tmpdir()
    spawned = []
    common = dict(env=claude_env(), presence_dir=d, host="h", post=None,
                  spawn_flush=lambda p: spawned.append(p), stdout=io.StringIO(), stderr=io.StringIO())
    sp.run_hook("claude", stdin=io.StringIO(json.dumps({"hook_event_name": "SessionStart",
                "session_id": CLAUDE_ID})), **common)
    box_path = d / "outbox" / "claude-51fa95bb.json"
    box = json.loads(box_path.read_text())
    box["posted_at"] = box["record"]["announced_at"]
    box_path.write_text(json.dumps(box))
    spawned.clear()
    sp.run_hook("claude", stdin=io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit",
                "session_id": CLAUDE_ID, "prompt": "Build the presence registry"})), **common)
    rec = json.loads(box_path.read_text())["record"]
    assert rec["title"] == "Build the presence registry"
    assert rec["event"] == "announce"
    assert len(spawned) == 1


def test_session_end_posts_depart():
    d = tmpdir()
    spawned = []
    common = dict(env=claude_env(), presence_dir=d, host="h", post=None,
                  spawn_flush=lambda p: spawned.append(p), stdout=io.StringIO(), stderr=io.StringIO())
    sp.run_hook("claude", stdin=io.StringIO(json.dumps({"hook_event_name": "SessionEnd",
                "session_id": CLAUDE_ID})), **common)
    box = json.loads((d / "outbox" / "claude-51fa95bb.json").read_text())
    assert box["record"]["event"] == "depart" and len(spawned) == 1


def test_lookup_by_handle_name_and_title():
    directory = {"sessions": {
        "codex-6394537b": {"handle": "codex-6394537b", "name": "sol", "title": "Orchestrator - take over"},
        "claude-51fa95bb": {"handle": "claude-51fa95bb", "name": None, "title": "Presence build"},
    }}
    assert sp.lookup(directory, "codex-6394537b")["handle"] == "codex-6394537b"
    assert sp.lookup(directory, "@sol")["handle"] == "codex-6394537b"
    assert sp.lookup(directory, "presence")["handle"] == "claude-51fa95bb"
    try:
        sp.lookup(directory, "r")  # matches both titles
    except sp.PresenceError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("an ambiguous name must be refused")
    try:
        sp.lookup(directory, "nobody")
    except sp.PresenceError as exc:
        assert "no live session" in str(exc)
    else:
        raise AssertionError("an unknown name must be refused")


def test_send_posts_a_session_message_receipt_that_no_desk_answers():
    """PR #1345 review, finding 2: a plain turn fans out to every seated desk,
    each spending a dispatch on a message meant for one session. A receipt never
    reaches a desk (state.route_turn's kind filter); the bridge's session router
    carries it to the one handle it names."""
    calls = []
    directory = {"sessions": {"codex-6394537b": {"handle": "codex-6394537b", "name": "sol"}}}
    sp.send(directory, "sol", "please confirm", seat="claude",
            post=lambda body, seat, **kw: calls.append((body, seat, kw)) or {"ok": True},
            room="model-room")
    body, seat, kw = calls[0]
    assert json.loads(body) == {"session_message": {"to": "codex-6394537b", "text": "please confirm"}}
    assert seat == "claude" and kw["kind"] == "receipt" and kw["room"] == "model-room"


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
        print(f"{failures} session_presence test(s) failed", file=sys.stderr)
        return 1
    print("all session_presence unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
