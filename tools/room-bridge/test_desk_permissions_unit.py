#!/usr/bin/env python3
"""Synthetic desk contracts: no provider, credentials, or live sessions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_desktop_wire as wire
import desk_cli
import desks
import dispatch
import queue_dispatch
import bridge


INSTRUCTION = (
    "Model Room desk instruction: If you need approvals, permissions, or decisions, "
    "send them to the orchestrator session via send_message in one message, then "
    "end the turn. Never ask Joe."
)


class DeskPermissionTests(unittest.TestCase):
    def test_registration_help_does_not_offer_prompting_modes(self):
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as done:
            desk_cli.main(["register", "--help"])
        self.assertEqual(done.exception.code, 0)
        self.assertIn("dontAsk", output.getvalue())
        self.assertNotIn("opt-in", output.getvalue())

    def test_background_dispatch_refuses_original_blank_task_before_launch(self):
        sid = "12345678-1234-4123-8123-123456789abc"
        calls = []
        def supervisor(argv, **_kwargs):
            calls.append(argv)
            output = (json.dumps([{"id": sid[:8], "sessionId": sid}])
                      if argv[1] == "agents" else "backgrounded · 12345678")
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            entry = reg.register("fixture-desk", "claude-desktop", model="fixture-model", effort="high")
            launch_background = wire.launch_background
            for task in ("", " \t\n"):
                with self.subTest(task=task):
                    with self.assertRaises(wire.ClaudeDesktopError) as refused:
                        wire.launch_background(entry, task, run=supervisor)
                    self.assertEqual(refused.exception.code, "invalid_background_contract")
                    with patch.object(wire, "launch_background", side_effect=lambda entry, task:
                                      launch_background(entry, task, run=supervisor)):
                        row = dispatch.dispatch("fixture-desk", task, registry=reg,
                                                results_path=Path(root) / "results.jsonl")
                    self.assertEqual(row["status"], "failed", row)
                    self.assertEqual(row["detail"], "invalid_background_contract")
                    self.assertEqual(row["task"], task)
                    self.assertEqual(calls, [])

    def test_registration_refuses_prompting_and_unbounded_modes(self):
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            for kind in ("claude-desktop", "claude-session"):
                for mode in ("default", "manual", "plan", "auto", "acceptEdits", "bypassPermissions", "", "typo"):
                    with self.subTest(kind=kind, mode=mode):
                        with self.assertRaisesRegex(desks.DeskError, "orchestrator"):
                            reg.register("fixture-desk", kind, socket="/tmp/fixture-desk.sock",
                                         model="fixture-model", effort="high" if kind == "claude-desktop" else None,
                                         permission_mode=mode)
                        self.assertEqual(reg.entries(), {})

    def test_default_and_explicit_noninteractive_modes(self):
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            default = reg.register("fixture-desk", "claude-desktop", model="fixture-model", effort="high")
            self.assertEqual(default["permission_mode"], "dontAsk")
            for mode in ("dontAsk",):
                entry = reg.register("fixture-desk", "claude-desktop", model="fixture-model",
                                     effort="high", permission_mode=mode)
                self.assertEqual(entry["permission_mode"], mode)

    def test_edited_registry_refused_on_resolve(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "registry.json"
            reg = desks.Registry(path)
            for mode in ("default", "manual", "plan", "auto", "acceptEdits", "bypassPermissions"):
                path.write_text(json.dumps({"desks": {"fixture-desk": {
                    "kind": "claude-desktop", "permission_mode": mode}}}))
                with self.subTest(mode=mode), self.assertRaisesRegex(desks.DeskError, "orchestrator"):
                    reg.resolve("fixture-desk")

    def test_direct_background_launch_refuses_before_subprocess(self):
        for mode in ("default", "manual", "plan", "auto", "acceptEdits", "bypassPermissions", "", "typo"):
            with self.subTest(mode=mode):
                calls = []
                def run(argv, **_kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
                with self.assertRaisesRegex(wire.ClaudeDesktopError, "orchestrator"):
                    wire.launch_background({"model": "fixture-model", "effort": "high",
                                            "cwd": "/tmp", "permission_mode": mode}, "Synthetic task", run=run)
                self.assertEqual(calls, [])

    def test_cli_refuses_manual_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as root:
            from contextlib import redirect_stderr
            from io import StringIO
            stderr = StringIO()
            with redirect_stderr(stderr):
                result = desk_cli.main(["--registry", str(Path(root) / "registry.json"),
                    "register", "fixture-desk", "--kind", "claude-desktop",
                    "--model", "fixture-model", "--effort", "high", "--permission-mode", "manual"])
            self.assertEqual(result, 2)
            self.assertIn("orchestrator", stderr.getvalue())


class DeskFirstTurnTests(unittest.TestCase):
    def test_seeded_start_writes_complete_instruction_to_provider_fifo(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root) / "state"
            reg = desks.Registry(Path(root) / "registry.json")
            readers = []
            def launch(*_args, **_kwargs):
                readers.append(os.open(state_dir / "fixture-desk.stdin", os.O_RDONLY | os.O_NONBLOCK))
                return SimpleNamespace(pid=4242, poll=lambda: None)
            try:
                with patch.object(dispatch.credential_env, "claude_child_env", return_value=({}, None)), \
                     patch.object(dispatch, "_alive", return_value=True), \
                     patch.object(desks, "is_live", return_value=True), \
                     patch.object(dispatch.subprocess, "Popen", side_effect=launch):
                    row = dispatch.desk_start("fixture-desk", registry=reg, state_dir=state_dir,
                                              sock_dir=Path(root) / "socks", seed="Synthetic seed")
                self.assertFalse(row["already_running"])
                sent = os.read(readers[0], 65536)
                self.assertTrue(sent.endswith(b"\n"))
                self.assertEqual(json.loads(sent)["message"]["content"], INSTRUCTION + "\n\nSynthetic seed")
                self.assertIn("fixture-desk", reg.entries())
            finally:
                for fd in readers:
                    os.close(fd)

    def test_post_bind_exit_and_missing_fifo_reader_return_typed_failure(self):
        # Bound the reproduction in a separate process: the old blocking
        # open must never hang the test runner. The FIFO has no reader.
        probe = '''
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import desks, dispatch
root = Path(sys.argv[2])
reg = desks.Registry(root / "registry.json")
seed = None if sys.argv[3] == "none" else "Synthetic seed"
readers = []
def launch(*_args, **_kwargs):
    if sys.argv[3] == "full":
        readers.append(os.open(root / "state" / "fixture-desk.stdin", os.O_RDONLY | os.O_NONBLOCK))
    return SimpleNamespace(pid=4242, poll=lambda: 1 if sys.argv[4] == "exited" else None)
if sys.argv[3] == "full":
    seed = "x" * 1048576
with patch.object(dispatch.credential_env, "claude_child_env", return_value=({}, None)), \\
     patch.object(desks, "is_live", return_value=True), \\
     patch.object(dispatch, "_alive", return_value=sys.argv[4] != "dead"), \\
     patch.object(dispatch, "BIND_TIMEOUT_S", 0.1), \\
     patch.object(dispatch.subprocess, "Popen", side_effect=launch):
    try:
        dispatch.desk_start("fixture-desk", registry=reg, state_dir=root / "state", sock_dir=root / "socks", seed=seed)
        result = {"code": "unexpected_success"}
    except desks.DeskError as exc:
        result = {"code": exc.code}
print(json.dumps({**result, "entries": reg.entries()}))
'''
        for seed, life in (("none", "dead"), ("none", "exited"), ("seed", "dead"), ("seed", "alive"), ("full", "alive")):
            with self.subTest(seed=seed, life=life), tempfile.TemporaryDirectory() as root:
                try:
                    out = subprocess.run([sys.executable, "-c", probe, str(Path(__file__).resolve().parent), root,
                                          seed, life], capture_output=True, text=True, timeout=2)
                except subprocess.TimeoutExpired:
                    self.fail("desk_start hung opening a real FIFO after the bind probe")
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(json.loads(out.stdout), {"code": "desk_failed_to_start", "entries": {}})

    def test_unseeded_start_cannot_answer_first_bridge_task_with_bootstrap_ack(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root) / "state"
            reg = desks.Registry(Path(root) / "registry.json")
            readers = []
            def launch(*_args, **_kwargs):
                # A real FIFO with a synthetic provider reader; don't replace
                # the FIFO with a regular file (that hides startup races).
                readers.append(os.open(state_dir / "fixture-desk.stdin", os.O_RDONLY | os.O_NONBLOCK))
                (state_dir / "fixture-desk.log").touch()
                return SimpleNamespace(pid=4242, poll=lambda: None)
            try:
                with patch.object(dispatch.credential_env, "claude_child_env", return_value=({}, None)), \
                     patch.object(dispatch, "_alive", return_value=True), \
                     patch.object(desks, "is_live", return_value=True), \
                     patch.object(dispatch.subprocess, "Popen", side_effect=launch):
                    dispatch.desk_start("fixture-desk", registry=reg, state_dir=state_dir,
                                        sock_dir=Path(root) / "socks")
                state = {"desks": {}}
                with patch.object(desks, "is_live", return_value=True), \
                     patch.object(dispatch.inject_mod, "inject_keepalive",
                                  return_value=SimpleNamespace(close=lambda: None)):
                    bridge.deliver("fixture-desk", reg.resolve("fixture-desk"), "fixture-seat",
                                   {"body": "Synthetic first task", "seat": "fixture-orchestrator",
                                    "seq": 1, "msg_id": "fixture-msg"},
                                   state=state, registry=reg, results_path=Path(root) / "results.jsonl",
                                   add_room_turn=lambda **_kw: None, desk_state_dir=state_dir)
                pending = bridge.state_mod.get_pending(state, "fixture-desk")
                bootstrap = os.read(readers[0], 65536)
                results = ([{"type": "result", "result": "Bootstrap acknowledgement"}] if bootstrap else [])
                results.append({"type": "result", "result": "First task answer"})
                log = state_dir / "fixture-desk.log"
                log.write_text("".join(json.dumps(row) + "\n" for row in results))
                self.assertEqual(bridge.scan_for_result(log, pending["log_offset"]), "First task answer")
            finally:
                for fd in readers:
                    os.close(fd)

    def test_flash_queue_dispatch_keeps_receipt_and_stamped_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root) / "numbers.csv"
            data.write_text("value\n1\n2\n")
            task = queue_dispatch.QueueDeskExecutor._prompt({
                "task_id": "t_fixture_queue", "title": "Sum the values",
                "meta": {"source_seq": 1, "source_msg_id": "fixture-message",
                         "origin": "fixture:origin", "cap": "repo-write"},
                "instructions": f"data: {data}\nSum the values.",
            })
            reg = desks.Registry(Path(root) / "registry.json")
            reg.register("fixture-flash", "flash-local")
            with patch.object(dispatch.flash_wire, "_run_flash_script", return_value=(0, {"answer": "3"})):
                direct = dispatch.flash_wire.run_task(task, roots=[root])
                # Keep the real dispatch -> Flash parser/runner boundary.
                with patch.object(dispatch.flash_wire, "data_roots", return_value=[str(Path(root).resolve())]):
                    row = dispatch.dispatch("fixture-flash", task, registry=reg,
                                            results_path=Path(root) / "results.jsonl")
            self.assertEqual(row["status"], "completed", row)
            self.assertEqual(row["result"], direct["result"])
            terminal = queue_dispatch.parse_terminal_result(row["result"], "t_fixture_queue")
            self.assertEqual(terminal["outcome"], "success")
            prompt = desks.desk_prompt(task)
            self.assertEqual(dispatch.flash_wire.task_trust(prompt), ("fixture:origin", "repo-write"))
            self.assertIn(INSTRUCTION, prompt)
            self.assertEqual(desks.desk_prompt(prompt), prompt)

    def test_codex_live_resume_overrides_inherited_approval_policy(self):
        for thread in (None, "fixture-thread"):
            messages = []
            fake = SimpleNamespace(upgrade=lambda: None, send_json=messages.append,
                receive_json=lambda: {"method": "item/completed", "params": {
                    "item": {"type": "agentMessage", "text": "Synthetic result"}}})
            with self.subTest(thread=thread), patch.object(dispatch.codex_wire, "Wire", return_value=fake), \
                 patch.object(dispatch.codex_wire, "wait_response", return_value={"thread": {"id": "fixture-thread"}}):
                dispatch.codex_wire.run_turn("/tmp/fixture.sock", "Synthetic task", thread_id=thread)
                opened = next(m for m in messages if m.get("id") == "thread-open")
                turn = next(m for m in messages if m.get("method") == "turn/start")
                self.assertEqual(opened["params"]["approvalPolicy"], "never")
                self.assertEqual(turn["params"]["approvalPolicy"], "never")
                self.assertEqual(turn["params"]["input"][0]["text"], INSTRUCTION + "\n\nSynthetic task")

    def test_codex_desktop_turn_is_never_and_includes_instruction(self):
        from unittest.mock import Mock
        client = Mock()
        client.owner.return_value = "fixture-owner"
        client.request.return_value = {"resultType": "success"}
        with patch.object(dispatch.codex_ipc, "_open", return_value=client), \
             patch.object(dispatch.codex_ipc, "thread_owner", return_value="fixture-owner"):
            dispatch._to_codex({"thread_id": "fixture-thread"}, "Synthetic task", env={}, live_desktop=True)
        request = client.request.call_args.args[1]["turnStart"]["request"]
        self.assertEqual(request["approvalPolicy"], "never")
        self.assertEqual(request["input"][0]["text"], INSTRUCTION + "\n\nSynthetic task")

    def test_non_desk_desktop_messages_keep_their_original_posture(self):
        from unittest.mock import Mock
        client = Mock()
        client.owner.return_value = "fixture-owner"
        client.request.return_value = {"resultType": "success"}
        with patch.object(dispatch.codex_ipc, "_open", return_value=client):
            dispatch.codex_ipc.start_turn("fixture-thread", "Synthetic ordinary session message")
        request = client.request.call_args.args[1]["turnStart"]["request"]
        self.assertNotIn("approvalPolicy", request)
        self.assertEqual(request["input"][0]["text"], "Synthetic ordinary session message")

    def test_desk_never_steers_into_a_turn_with_unverified_permissions(self):
        from unittest.mock import Mock
        client = Mock()
        client.owner.return_value = "fixture-owner"
        client.request.side_effect = [{"resultType": "error", "error": "turn already in progress"},
                                      {"resultType": "success"}]
        with patch.object(dispatch.codex_ipc, "_open", return_value=client), \
             patch.object(dispatch.codex_ipc, "thread_owner", return_value="fixture-owner"):
            result = dispatch._to_codex({"thread_id": "fixture-thread"}, "Synthetic task", env={}, live_desktop=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("orchestrator", result["detail"])
        self.assertEqual(client.request.call_count, 1)

    def test_background_default_and_explicit_modes_include_instruction(self):
        sid = "12345678-1234-4123-8123-123456789abc"
        for mode in (None, "dontAsk"):
            calls = []
            def run(argv, **_kwargs):
                calls.append(argv)
                output = (json.dumps([{"id": sid[:8], "sessionId": sid}])
                          if argv[1] == "agents" else "backgrounded · 12345678")
                return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
            entry = {"model": "fixture-model", "effort": "high", "cwd": "/tmp"}
            if mode is not None:
                entry["permission_mode"] = mode
            with self.subTest(mode=mode):
                wire.launch_background(entry, "Synthetic task", request_id=sid, run=run)
                self.assertEqual(calls[0][calls[0].index("--permission-mode") + 1], mode or "dontAsk")
                self.assertEqual(calls[0][-1], INSTRUCTION + "\n\nSynthetic task")

    def test_every_desk_dispatch_includes_instruction_on_start_and_resume(self):
        # Observe the prompt at each provider boundary; no provider is called.
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            cases = {
                "claude-session": ("_to_claude", dispatch),
                "claude-desktop": ("_to_claude_desktop", dispatch),
                "codex-session": ("_to_codex", dispatch),
                "codex-live": ("run_turn", dispatch.codex_wire),
                "grok-cli": ("run_task", dispatch.grok_wire),
                "flash-local": ("run_task", dispatch.flash_wire),
            }
            for kind, (method, module) in cases.items():
                entry = {"kind": kind, "name": "fixture-desk", "model": "fixture-model",
                         "effort": "high", "socket": "/tmp/fixture.sock"}
                for thread in (None, "fixture-thread"):
                    with self.subTest(kind=kind, thread=thread):
                        entry["thread_id"] = thread
                        with patch.object(reg, "resolve", return_value=entry), patch.object(
                                module, method, return_value={"status": "delivered"}) as send:
                            dispatch.dispatch("fixture-desk", "Synthetic task", registry=reg,
                                              results_path=Path(root) / "results.jsonl")
                        prompt = send.call_args.args[0 if kind == "flash-local" else 1]
                        self.assertEqual(prompt, "Synthetic task" if kind == "claude-desktop"
                                         else INSTRUCTION + "\n\nSynthetic task")

    def test_persistent_claude_start_is_dontask_and_seeds_instruction(self):
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            for seed in (None, "Synthetic seed"):
                state = Path(root) / ("unseeded" if seed is None else "seeded")
                with patch.object(dispatch.credential_env, "claude_child_env", return_value=({}, None)), \
                     patch.object(dispatch, "_alive", return_value=True), \
                     patch.object(dispatch.os, "mkfifo", side_effect=lambda p, _m: Path(p).touch()), \
                     patch.object(desks, "is_live", return_value=True), \
                     patch.object(dispatch.subprocess, "Popen", return_value=SimpleNamespace(pid=4242, poll=lambda: None)) as launch:
                    dispatch.desk_start("fixture-desk", registry=reg, state_dir=state,
                                        sock_dir=Path(root) / "socks", seed=seed)
                self.assertIn("--permission-mode dontAsk", launch.call_args.args[0][2])
                sent = (state / "fixture-desk.stdin").read_text()
                if seed:
                    self.assertEqual(json.loads(sent)["message"]["content"], INSTRUCTION + "\n\nSynthetic seed")
                else:
                    self.assertEqual(sent, "")

    def test_codex_cli_start_and_resume_pin_never_and_include_instruction(self):
        entry = {"model": "fixture-model", "effort": "high", "cwd": "/tmp"}
        for thread in (None, "fixture-thread"):
            with self.subTest(thread=thread), patch.object(dispatch.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="")) as run:
                dispatch._to_codex({**entry, "thread_id": thread}, "Synthetic task", env={"PATH": "/tmp"})
                argv = run.call_args.args[0]
                self.assertIn('approval_policy="never"', argv)
                self.assertEqual(argv[-1], INSTRUCTION + "\n\nSynthetic task")


if __name__ == "__main__":
    unittest.main()
