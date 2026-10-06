#!/usr/bin/env python3
"""Hermetic proof of Model Room -> Claude background -> Desktop delivery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import claude_desktop_wire as wire  # noqa: E402
import desks  # noqa: E402
import dispatch  # noqa: E402
import state as state_mod  # noqa: E402


SID = "12345678-1234-4123-8123-123456789abc"
FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: {exc!r}")
        print(f"  FAIL  {label}\n          {exc!r}")
    else:
        print(f"  ok    {label}")


def test_launch_is_named_durable_and_backgrounded() -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:4] == ["agents", "--json", "--all"]:
            payload = [{"id": "12345678", "sessionId": SID,
                        "kind": "background", "state": "running"}]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="backgrounded · 12345678", stderr="")

    out = wire.launch_background(
        {"model": "opus", "effort": "high", "cwd": "/tmp", "permission_mode": "dontAsk"},
        "Read Model Room seq 6606", request_id=SID, run=run,
    )
    argv, kwargs = calls[0]
    assert argv[:2] == ["claude", "--bg"]
    assert "--session-id" not in argv
    assert argv[argv.index("--name") + 1] == "model-room-12345678"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert kwargs["cwd"] == "/tmp" and kwargs["stdin"] is subprocess.DEVNULL
    assert out == {"status": "delivered", "session_id": SID,
                   "session_short_id": "12345678",
                   "session_name": "model-room-12345678", "transport": "claude-desktop"}


def test_supervisor_status_is_uuid_bound() -> None:
    payload = [{"sessionId": SID, "id": "12345678", "kind": "background", "state": "completed"}]

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    assert wire.inspect_session(SID, run=run) == {
        "session_id": SID, "state": "completed", "found": True, "kind": "background"
    }


def test_final_typed_result_comes_from_persisted_transcript() -> None:
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "project" / f"{SID}.jsonl"
        path.parent.mkdir()
        rows = [
            {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [
                {"type": "text", "text": "working"}]}},
            {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": "Done\nCARR_QUEUE_RESULT {\"v\":1}"}]}},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        assert wire.read_final_text(SID, transcript_root=Path(root)) == (
            'Done\nCARR_QUEUE_RESULT {"v":1}'
        )


def test_desktop_handoff_uses_attached_pty_slash_command() -> None:
    with tempfile.TemporaryDirectory() as root:
        fake = Path(root) / "claude"
        capture = Path(root) / "input.txt"
        args_capture = Path(root) / "args.txt"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s' \"$*\" > {args_capture}\n"
            "printf 'ready\\n'\n"
            "IFS= read -r line\n"
            f"printf '%s' \"$line\" > {capture}\n"
            "[ \"$line\" = '/desktop' ]\n"
        )
        fake.chmod(0o755)
        assert wire.handoff_to_desktop(SID, claude_bin=str(fake), timeout_s=5,
                                       list_sessions=lambda: [], settle_s=0) == {
            "status": "opened", "session_id": SID
        }
        assert args_capture.read_text() == "attach 12345678"
        assert capture.read_text() == "/desktop"


FIXTURE = json.loads((HERE / "testdata" / "claude_desktop_real_capture.json").read_text())


def _fake_claude(root: str, *, attach_output: list[str] | None = None, attach_exit: int | None = None,
                 after_desktop: list[str] | None = None, exit_after: int | None = None) -> tuple[Path, Path]:
    """A stand-in ``claude`` that replays captured PTY bytes.

    It prints ``attach_output`` and exits ``attach_exit`` when that is set
    (the attach-refused path).  Otherwise it paints, reads the slash command,
    replays ``after_desktop`` and stays alive like the real attached client,
    unless ``exit_after`` is set.
    """
    fake = Path(root) / "claude"
    calls = Path(root) / "calls.jsonl"
    spec = {"attach_output": attach_output, "attach_exit": attach_exit,
            "after_desktop": after_desktop or [], "exit_after": exit_after}
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        f"spec = json.loads({json.dumps(json.dumps(spec))})\n"
        f"with open({str(calls)!r}, 'a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "out = sys.stdout.buffer\n"
        "if spec['attach_exit'] is not None:\n"
        "    for chunk in spec['attach_output'] or []:\n"
        "        out.write(chunk.encode()); out.flush()\n"
        "    sys.exit(spec['attach_exit'])\n"
        "out.write(b'transcript repaint\\r\\n'); out.flush()\n"
        "line = sys.stdin.readline().strip()\n"
        "if line != '/desktop':\n"
        "    sys.exit(9)\n"
        "for chunk in spec['after_desktop']:\n"
        "    out.write(chunk.encode()); out.flush(); time.sleep(0.02)\n"
        "if spec['exit_after'] is not None:\n"
        "    sys.exit(spec['exit_after'])\n"
        "time.sleep(30)\n"
    )
    fake.chmod(0o755)
    return fake, calls


def _calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_real_desktop_output_is_not_matched_by_the_old_substring_check() -> None:
    """Root cause of desktop_handoff_timeout (seq 43750/43776/43785/43796):
    Claude Code paints a skipped space as a cursor move, so the real 2.1.281
    bytes are ``Checking\\x1b[16Gfor Claude Desktop…``.  Stripping ANSI glued
    the words, and the old ``b"Checking for Claude Desktop"`` check never
    matched although Claude Desktop did open the session."""
    raw = "".join(FIXTURE["desktop_command_output"]).encode()
    old_rendered = wire.ANSI_ESCAPE.sub(b"", raw)
    assert b"Checking for Claude Desktop" not in old_rendered
    assert b"Opening Claude Desktop" not in old_rendered
    screen = wire._screen_text(raw)
    assert b"checkingforclaudedesktop" in screen
    assert not any(marker in screen for marker in wire.DESKTOP_ERRORS)


def test_handoff_detects_real_output_when_status_is_unavailable() -> None:
    with tempfile.TemporaryDirectory() as root:
        fake, calls = _fake_claude(root, after_desktop=FIXTURE["desktop_command_output"])
        out = wire.handoff_to_desktop(SID, claude_bin=str(fake), timeout_s=5, ready_timeout_s=3,
                                      list_sessions=lambda: None, status_poll_s=0.1, settle_s=0)
        assert out == {"status": "opened", "session_id": SID}
        assert _calls(calls) == [["attach", "12345678"]]


def test_handoff_success_is_the_supervisor_status_signal() -> None:
    """The woken background process exits once /desktop hands the session
    over; that supervisor fact decides success even with no progress text."""
    for after in (
        [],  # background pid gone, nothing else listed (seen on the Studio)
        [{"sessionId": SID, "kind": "interactive", "pid": 222, "status": "idle"}],  # Desktop resumed it
    ):
        listings = iter([
            [{"id": "12345678", "sessionId": SID, "kind": "background", "state": "done", "pid": 111}],
            [{"id": "12345678", "sessionId": SID, "kind": "background", "state": "done", "pid": 111}],
        ])

        def lister(after=after):
            try:
                return next(listings)
            except StopIteration:
                return [{"id": "12345678", "sessionId": SID, "kind": "background", "state": "done"},
                        *after]

        with tempfile.TemporaryDirectory() as root:
            fake, _calls_path = _fake_claude(root)
            out = wire.handoff_to_desktop(SID, claude_bin=str(fake), timeout_s=5, ready_timeout_s=3,
                                          list_sessions=lister, status_poll_s=0.1, settle_s=0)
            assert out == {"status": "opened", "session_id": SID}


def test_handoff_without_any_signal_times_out_honestly() -> None:
    rows = [{"id": "12345678", "sessionId": SID, "kind": "background", "state": "done", "pid": 111}]
    with tempfile.TemporaryDirectory() as root:
        fake, _calls_path = _fake_claude(root)
        try:
            wire.handoff_to_desktop(SID, claude_bin=str(fake), timeout_s=1, ready_timeout_s=3,
                                    list_sessions=lambda: rows, status_poll_s=0.1, settle_s=0)
        except wire.ClaudeDesktopError as exc:
            assert exc.code == "desktop_handoff_timeout"
        else:
            raise AssertionError("a handoff with no signal must not report opened")


def test_handoff_error_copy_fails_fast() -> None:
    error = ("\x1b[2GError:\x1b[9GCouldn't\x1b[18Gopen\x1b[23GClaude\x1b[30GDesktop"
             " (`open` exited 1). Open Claude Desktop and run /desktop again.")
    rows = [{"id": "12345678", "sessionId": SID, "kind": "background", "state": "done", "pid": 111}]
    with tempfile.TemporaryDirectory() as root:
        fake, _calls_path = _fake_claude(root, after_desktop=[error])
        try:
            wire.handoff_to_desktop(SID, claude_bin=str(fake), timeout_s=10, ready_timeout_s=3,
                                    list_sessions=lambda: rows, status_poll_s=0.1, settle_s=0)
        except wire.ClaudeDesktopError as exc:
            assert exc.code == "desktop_handoff_failed"
        else:
            raise AssertionError("/desktop error copy must fail the handoff")


def test_session_already_open_in_desktop_is_not_attached() -> None:
    """Root cause of desktop_attach_failed (seq 43742/43827/43859): by the
    time the bridge attached, Claude Desktop already held the session — the
    supervisor listed a live ``interactive`` row for it — and ``claude
    attach`` refuses such a session.  That is the goal state, not a failure."""
    rows = [{"id": "12345678", "sessionId": SID, "kind": "background", "state": "done"},
            {"sessionId": SID, "kind": "interactive", "pid": 62502, "status": "idle"}]
    with tempfile.TemporaryDirectory() as root:
        fake, calls = _fake_claude(root)
        out = wire.handoff_to_desktop(SID, claude_bin=str(fake), list_sessions=lambda: rows)
        assert out == {"status": "already_open", "session_id": SID}
        assert _calls(calls) == []


def test_real_attach_refusal_reads_as_already_open() -> None:
    with tempfile.TemporaryDirectory() as root:
        fake, _calls_path = _fake_claude(root, attach_output=FIXTURE["attach_after_transfer_output"],
                                         attach_exit=FIXTURE["attach_after_transfer_exit"])
        out = wire.handoff_to_desktop(SID, claude_bin=str(fake), ready_timeout_s=3,
                                      list_sessions=lambda: None)
        assert out == {"status": "already_open", "session_id": SID}


def test_other_early_attach_exit_still_fails() -> None:
    with tempfile.TemporaryDirectory() as root:
        fake, _calls_path = _fake_claude(root, attach_output=["No session 12345678\r\n"], attach_exit=1)
        try:
            wire.handoff_to_desktop(SID, claude_bin=str(fake), ready_timeout_s=3,
                                    list_sessions=lambda: None)
        except wire.ClaudeDesktopError as exc:
            assert exc.code == "desktop_attach_failed"
        else:
            raise AssertionError("an unexplained attach exit must stay a failure")


def test_status_reads_the_background_row_when_desktop_also_lists_it() -> None:
    payload = [{"sessionId": SID, "kind": "interactive", "pid": 62502, "status": "idle"},
               {"sessionId": SID, "id": "12345678", "kind": "background", "state": "done"}]

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    assert wire.inspect_session(SID, run=run)["state"] == "done"


def test_registry_dispatch_and_queue_only_fanout() -> None:
    with tempfile.TemporaryDirectory() as root:
        reg = desks.Registry(Path(root) / "desks.json")
        entry = reg.register("claude-desktop", "claude-desktop", model="opus",
                             effort="high", cwd=root)
        assert entry["permission_mode"] == "dontAsk"
        raw = reg._load()  # local fixture readback
        raw["desks"]["claude-desktop"]["room_seat"] = "claude"
        raw["desks"]["ordinary"] = {
            "kind": "claude-session", "room_seat": "reviewer", "socket": "/tmp/named.sock"
        }
        reg._save(raw)
        seats = bridge.conversational_desk_seats(reg.entries())
        assert seats == {"ordinary": "reviewer"}

        original = dispatch.claude_desktop_wire.launch_background
        setattr(dispatch.claude_desktop_wire, "launch_background", lambda _entry, _task: {
            "status": "delivered", "session_id": SID, "transport": "claude-desktop"
        })
        try:
            row = dispatch.dispatch("claude-desktop", "do review", registry=reg,
                                    results_path=Path(root) / "results.jsonl")
        finally:
            setattr(dispatch.claude_desktop_wire, "launch_background", original)
        assert row["session_id"] == SID and row["status"] == "delivered"


def test_done_pending_hands_off_then_finishes_queue() -> None:
    state = state_mod.default_state()
    state_mod.set_pending(
        state, "claude-desktop", dispatch_msg_id="dispatch-1", log_offset=0,
        injected_at="2026-08-31T12:00:00+00:00", source_msg_id="queue:t_queue0001",
        source_seq=1, origin_kind="queue", kanban_task_id="t_queue0001",
        target="claude-desktop", finish="review", cap="read",
        transport="claude-desktop", session_id=SID,
    )
    handed: list[str] = []
    finished: list[tuple[dict, str]] = []
    posted: list[dict] = []

    class Executor:
        def finish_pending(self, pending: dict, raw_result: str) -> dict:
            finished.append((pending, raw_result))
            return {
                "outcome": "review", "task_id": "t_queue0001",
                "completion": {"queue_completion": {
                    "v": 1, "task_id": "t_queue0001", "target": "claude-desktop",
                    "outcome": "success", "summary": "Reviewed",
                    "source_seq": 1, "source_msg_id": "queue:t_queue0001",
                }},
            }

    def post(**row) -> None:
        posted.append(row)

    def handoff(session_id: str) -> dict:
        handed.append(session_id)
        return {"status": "opened"}

    executor = Executor()
    result = 'Reviewed\nCARR_QUEUE_RESULT {"v":1}'
    outcome = bridge.handle_pending(
        "claude-desktop", "claude", state,
        add_room_turn=post, log_path=Path("unused"),
        pending_timeout_s=1800, queue_executor=cast(Any, executor),
        # Live Claude Code uses ``done`` for a completed --bg session.
        inspect_background=lambda _sid: {"state": "done", "found": True},
        read_background_result=lambda _sid: result,
        handoff_background=handoff,
    )
    assert outcome is not None
    assert outcome["outcome"] == "review"
    assert handed == [SID] and finished[0][1] == result
    assert json.loads(posted[0]["body"])["claude_desktop_handoff"]["status"] == "opened"
    assert json.loads(posted[1]["body"])["queue_completion"]["task_id"] == "t_queue0001"
    assert posted[1]["idempotency_key"] == "queue-completion:t_queue0001"
    assert state_mod.get_pending(state, "claude-desktop") is None


def test_handoff_timeout_after_completion_finishes_and_never_relaunches() -> None:
    """Defect a2e7dcb5: t_24f60cef completed and posted its answer (seq 43722),
    then /desktop timed out (seq 43724) and the bridge launched a SECOND
    session for the same task (seq 43725). The session already completed —
    read_background_result already returned the real answer — so a handoff
    failure afterward must mark only the handoff as failed and still finish
    the task with that result. It must never call fail_pending, which is the
    path that schedules a retry (a second launch) or blocks the task."""
    state = state_mod.default_state()
    state_mod.set_pending(
        state, "claude-desktop", dispatch_msg_id="dispatch-1", log_offset=0,
        injected_at="2026-09-24T12:00:00+00:00", source_msg_id="queue:t_24f60cef",
        source_seq=1, origin_kind="queue", kanban_task_id="t_24f60cef",
        target="claude-desktop", finish="done", cap="read",
        transport="claude-desktop", session_id=SID,
    )
    finished: list[tuple[dict, str]] = []
    posted: list[dict] = []

    class Executor:
        def finish_pending(self, pending: dict, raw_result: str) -> dict:
            finished.append((pending, raw_result))
            return {
                "outcome": "done", "task_id": "t_24f60cef",
                "completion": {"queue_completion": {
                    "v": 1, "task_id": "t_24f60cef", "target": "claude-desktop",
                    "outcome": "success", "summary": "Answered",
                    "source_seq": 1, "source_msg_id": "queue:t_24f60cef",
                }},
            }

        def fail_pending(self, pending: dict, reason: str, *, now=None) -> dict:
            # This is the retry/relaunch path (queue_dispatch._retry_or_block).
            # A handoff failure AFTER completion must never take it.
            raise AssertionError(
                f"fail_pending must not be called after completion (reason={reason!r})")

    def post(**row) -> None:
        posted.append(row)

    def handoff(session_id: str) -> dict:
        raise wire.ClaudeDesktopError("desktop_handoff_timeout", "Claude /desktop did not exit")

    executor = Executor()
    result = 'Here is the answer\nCARR_QUEUE_RESULT {"v":1}'
    outcome = bridge.handle_pending(
        "claude-desktop", "claude", state,
        add_room_turn=post, log_path=Path("unused"),
        pending_timeout_s=1800, queue_executor=cast(Any, executor),
        inspect_background=lambda _sid: {"state": "done", "found": True},
        read_background_result=lambda _sid: result,
        handoff_background=handoff,
    )
    assert outcome is not None
    assert outcome["outcome"] == "done"
    assert finished == [({
        "origin_kind": "queue", "kanban_task_id": "t_24f60cef", "target": "claude-desktop",
        "finish": "done", "cap": "read", "source_seq": 1, "source_msg_id": "queue:t_24f60cef",
        "dispatch_msg_id": "dispatch-1", "injected_at": "2026-09-24T12:00:00+00:00",
        "transport": "claude-desktop", "session_id": SID, "log_offset": 0,
    }, result)]
    handoff_receipt = json.loads(posted[0]["body"])["claude_desktop_handoff"]
    assert handoff_receipt["status"] == "failed"
    assert handoff_receipt["code"] == "desktop_handoff_timeout"
    assert json.loads(posted[1]["body"])["queue_completion"]["task_id"] == "t_24f60cef"
    assert state_mod.get_pending(state, "claude-desktop") is None


def test_already_open_handoff_receipt_is_honest() -> None:
    state = state_mod.default_state()
    state_mod.set_pending(
        state, "claude-desktop", dispatch_msg_id="dispatch-1", log_offset=0,
        injected_at="2026-09-24T12:00:00+00:00", source_msg_id="queue:t_24b0a0c6",
        source_seq=1, origin_kind="queue", kanban_task_id="t_24b0a0c6",
        target="claude-desktop", finish="done", cap="repo-write",
        transport="claude-desktop", session_id=SID,
    )
    posted: list[dict] = []

    class Executor:
        def finish_pending(self, pending: dict, raw_result: str) -> dict:
            return {"outcome": "done", "task_id": "t_24b0a0c6",
                    "completion": {"queue_completion": {"v": 1, "task_id": "t_24b0a0c6"}}}

    outcome = bridge.handle_pending(
        "claude-desktop", "claude", state,
        add_room_turn=lambda **row: posted.append(row), log_path=Path("unused"),
        pending_timeout_s=1800, queue_executor=cast(Any, Executor()),
        inspect_background=lambda _sid: {"state": "done", "found": True},
        read_background_result=lambda _sid: "done\nCARR_QUEUE_RESULT {}",
        handoff_background=lambda sid: {"status": "already_open", "session_id": sid},
    )
    assert outcome is not None and outcome["outcome"] == "done"
    assert json.loads(posted[0]["body"])["claude_desktop_handoff"]["status"] == "already_open"


def main() -> int:
    check("launch is named, durable, and backgrounded", test_launch_is_named_durable_and_backgrounded)
    check("supervisor status is UUID-bound", test_supervisor_status_is_uuid_bound)
    check("typed result is read from persisted transcript", test_final_typed_result_comes_from_persisted_transcript)
    check("Desktop handoff uses attached PTY slash command", test_desktop_handoff_uses_attached_pty_slash_command)
    check("desktop desk is queue-only and dispatchable", test_registry_dispatch_and_queue_only_fanout)
    check("done background hands off before queue completion", test_done_pending_hands_off_then_finishes_queue)
    check("handoff timeout after completion finishes and never relaunches",
          test_handoff_timeout_after_completion_finishes_and_never_relaunches)
    check("real /desktop bytes defeat the old substring check",
          test_real_desktop_output_is_not_matched_by_the_old_substring_check)
    check("real /desktop output detected when status is unavailable",
          test_handoff_detects_real_output_when_status_is_unavailable)
    check("handoff success is the supervisor status signal", test_handoff_success_is_the_supervisor_status_signal)
    check("handoff with no signal times out honestly", test_handoff_without_any_signal_times_out_honestly)
    check("/desktop error copy fails fast", test_handoff_error_copy_fails_fast)
    check("session already open in Desktop is not attached", test_session_already_open_in_desktop_is_not_attached)
    check("real attach refusal reads as already open", test_real_attach_refusal_reads_as_already_open)
    check("other early attach exit still fails", test_other_early_attach_exit_still_fails)
    check("status reads the background row", test_status_reads_the_background_row_when_desktop_also_lists_it)
    check("already-open handoff receipt is honest", test_already_open_handoff_receipt_is_honest)
    total = 17
    print(f"claude-desktop-wire unit: {total - len(FAILURES)}/{total} passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
