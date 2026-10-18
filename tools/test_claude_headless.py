#!/usr/bin/env python3
"""Hermetic subprocess proof for the Model Room remote receiver."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/room-bridge/claude_remote_wire.py"
MODEL = "claude-sonnet-4-6"
TOKEN = "sk-ant-oat" + "x" * 98


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.trace = self.home / "calls.jsonl"
        fake = self.home / ".local/bin/claude"
        fake.parent.mkdir(parents=True)
        fake.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["CLI_TRACE"]).open("a") as trace:
    trace.write(json.dumps({{"args": args, "token_present": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))}}) + "\\n")
if args == ["auth", "status"]:
    print(json.dumps({{"authMethod": os.environ.get("TEST_AUTH_METHOD", "oauth_token")}}))
else:
    prompt = sys.stdin.read()
    print(json.dumps({{"type":"result", "subtype":"success", "is_error":False,
                      "result":prompt, "session_id":"00000000-0000-4000-8000-000000000002",
                      "modelUsage":{{os.environ.get("TEST_ACTUAL_MODEL", "{MODEL}"):{{"inputTokens":1}}}}}}))
    sys.exit(int(os.environ.get("TEST_CLI_EXIT", "0")))
''')
        fake.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "CLI_TRACE": str(self.trace)}
        self.request = {"desk":"dell-desk", "msg_id":"00000000-0000-4000-8000-000000000001",
                        "host":"dell", "model":MODEL, "effort":"high", "timeout_s":5,
                        "permission_mode":"dontAsk", "task":"task ' $(true)\nnext line"}
        self.tokens = self.home / ".config/carr/tokens.env"
        self.tokens.parent.mkdir(parents=True)

    def configure(self, value=TOKEN, mode=0o600):
        self.tokens.write_text("CLAUDE_CODE_OAUTH_TOKEN=" + value + "\n")
        self.tokens.chmod(mode)

    def run_receiver(self, **extra_env):
        return subprocess.run([sys.executable, str(SCRIPT), "--receive"], input=json.dumps(self.request),
                              capture_output=True, text=True, env={**self.env, **extra_env}, timeout=15)

    def test_subscription_model_and_result_readback(self):
        self.configure()
        proc = self.run_receiver()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        outcome = json.loads(proc.stdout)
        self.assertEqual(outcome["result"], self.request["task"])
        self.assertEqual(outcome["actual_model"], MODEL)
        self.assertEqual(outcome["msg_id"], self.request["msg_id"])
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(calls[0]["args"], ["auth", "status"])
        self.assertEqual(calls[1]["args"], ["-p", "--model", MODEL, "--effort", "high",
                                           "--permission-mode", "dontAsk", "--output-format", "json"])
        self.assertTrue(all(call["token_present"] for call in calls))
        self.assertNotIn(TOKEN, proc.stdout + proc.stderr + self.trace.read_text())

    def test_each_competing_auth_route_refused_before_cli(self):
        self.configure()
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                    "ANTHROPIC_PROFILE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "CLAUDE_CONFIG_DIR"):
            with self.subTest(key=key):
                proc = self.run_receiver(**{key:"synthetic-conflict"})
                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertFalse(self.trace.exists())
                self.assertNotIn("synthetic-conflict", proc.stdout + proc.stderr)

    def test_file_refusals_never_start_cli(self):
        for value, mode in ((None, 0o600), ("", 0o600), (TOKEN, 0o644), ("truncated", 0o600)):
            with self.subTest(mode=mode, present=bool(value)):
                if value is not None:
                    self.configure(value, mode)
                proc = self.run_receiver()
                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertFalse(self.trace.exists())

    def test_inherited_effort_override_refused_before_cli(self):
        self.configure()
        for effort in ("low", "medium", "high", "max", "invalid"):
            with self.subTest(effort=effort):
                proc = self.run_receiver(CLAUDE_CODE_EFFORT_LEVEL=effort)
                self.assertEqual(proc.returncode, 3, proc.stderr)
                outcome = json.loads(proc.stdout)
                self.assertEqual(outcome["status"], "failed")
                self.assertEqual(outcome["detail"], "remote_effort_override_refused")
                self.assertNotIn("effort", outcome)
                self.assertFalse(self.trace.exists(), "effort override reached the CLI")

    def test_empty_effort_override_uses_desk_effort(self):
        self.configure()
        proc = self.run_receiver(CLAUDE_CODE_EFFORT_LEVEL="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["effort"], "high")
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(calls[1]["args"], ["-p", "--model", MODEL, "--effort", "high",
                                           "--permission-mode", "dontAsk", "--output-format", "json"])

    def test_auth_status_refuses_other_credentials(self):
        self.configure()
        for method in ("api_key", "api_key_helper", "third_party", "claude.ai", "none"):
            with self.subTest(method=method):
                proc = self.run_receiver(TEST_AUTH_METHOD=method)
                self.assertEqual(proc.returncode, 3, proc.stderr)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertTrue(all(call["args"] == ["auth", "status"] for call in calls))

    def test_model_mismatch_and_cli_error_are_failures(self):
        self.configure()
        wrong = self.run_receiver(TEST_ACTUAL_MODEL="claude-opus-4-6")
        self.assertEqual(wrong.returncode, 1)
        self.assertEqual(json.loads(wrong.stdout)["detail"], "claude_model_mismatch")
        failed = self.run_receiver(TEST_CLI_EXIT="7")
        self.assertEqual(failed.returncode, 7)
        self.assertEqual(json.loads(failed.stdout)["status"], "failed")

    def test_missing_executable_has_value_free_diagnostic(self):
        self.configure()
        (self.home / ".local/bin/claude").unlink()
        proc = self.run_receiver()
        self.assertEqual(proc.returncode, 127)
        self.assertEqual(json.loads(proc.stdout)["detail"], "claude_cli_unavailable")
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
