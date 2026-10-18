#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "bin/remote-claude.sh"
MODEL = "claude-sonnet-4-6"


class RemoteDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.registry = self.home / "desks.json"
        self.results = self.home / "results.jsonl"
        self.trace = self.home / "ssh.json"
        self.registry.write_text(json.dumps({"desks": {"dell-desk": {
            "kind": "claude-remote", "host": "dell", "model": MODEL,
            "effort": "high", "timeout_s": 5, "permission_mode": "dontAsk"}}}))
        fake = self.home / "ssh"
        fake.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
payload = sys.stdin.read()
Path(os.environ["SSH_TRACE"]).write_text(json.dumps({{"args":args,"input":payload}}))
if "-V" in args:
    print("OpenSSH synthetic version")
    sys.exit(0)
if not payload.startswith("{{"):
    print("old unrecorded route")
    sys.exit(0)
request = json.loads(payload)
print(json.dumps({{"status":"completed", "result":"synthetic answer", "actual_model":"{MODEL}",
                  "requested_model":request["model"], "effort":request["effort"],
                  "msg_id":request["msg_id"], "desk":request["desk"],
                  "session_id":"00000000-0000-4000-8000-000000000001"}}))
''')
        fake.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": str(self.home) + os.pathsep + os.environ["PATH"],
                    "CARR_HERMES_DESKS": str(self.registry), "CARR_HERMES_RESULTS": str(self.results),
                    "SSH_TRACE": str(self.trace)}

    def run_wrapper(self, desk="dell-desk", *args):
        return subprocess.run(["bash", str(WRAPPER), desk, *args], input="synthetic task ' $(true)\nnext line",
                              text=True, capture_output=True, env=self.env, timeout=15)

    def test_wrapper_records_named_desk_model_and_result(self):
        proc = self.run_wrapper()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.results.exists(), "Model Room dispatch receipt missing")
        row = json.loads(self.results.read_text())
        self.assertEqual(row["desk"], "dell-desk")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["actual_model"], MODEL)
        self.assertEqual(row["result"], "synthetic answer")
        trace = json.loads(self.trace.read_text())
        self.assertEqual(trace["args"][-2], "dell")
        self.assertEqual(json.loads(trace["input"])["msg_id"], row["msg_id"])
        self.assertNotIn("synthetic task", " ".join(trace["args"]))

    def test_option_shaped_desk_never_reaches_ssh(self):
        proc = self.run_wrapper("-V")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.trace.exists())

    def test_unknown_desk_never_reaches_ssh(self):
        proc = self.run_wrapper("missing-desk")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.trace.exists())

    def test_registry_host_options_and_model_aliases_refused(self):
        for field, value in (("host", "-V"), ("host", "dell;true"), ("host", "dell\n"),
                             ("model", "sonnet"), ("effort", None), ("timeout_s", 0),
                             ("permission_mode", "auto")):
            with self.subTest(field=field, value=value):
                data = json.loads(self.registry.read_text())
                original = data["desks"]["dell-desk"][field]
                data["desks"]["dell-desk"][field] = value
                self.registry.write_text(json.dumps(data))
                proc = self.run_wrapper()
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse(self.trace.exists())
                data["desks"]["dell-desk"][field] = original
                self.registry.write_text(json.dumps(data))

    def test_transport_failures_never_report_completed(self):
        sys.path.insert(0, str(ROOT / "tools/room-bridge"))
        import claude_remote_wire as wire
        entry = {"name":"dell-desk", **json.loads(self.registry.read_text())["desks"]["dell-desk"]}
        request_id = "00000000-0000-4000-8000-000000000001"
        for status, stdout in ((255, ""), (0, "not JSON"),
                               (0, json.dumps({"status":"completed", "msg_id":"wrong", "desk":"dell-desk"})),
                               (0, json.dumps({"status":"completed", "msg_id":request_id, "desk":"dell-desk",
                                               "result":"answer", "actual_model":"wrong", "effort":"high"}))):
            with self.subTest(status=status, stdout=stdout):
                def fake(argv, **kwargs):
                    self.assertEqual(kwargs["timeout"], 20)
                    self.assertEqual(argv[-3], "--")
                    return subprocess.CompletedProcess(argv, status, stdout, "synthetic private diagnostic")
                outcome = wire.run_task(entry, "task", request_id, run=fake)
                self.assertEqual(outcome["status"], "failed")
                self.assertNotIn("private diagnostic", json.dumps(outcome))
        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        outcome = wire.run_task(entry, "task", request_id, run=timeout)
        self.assertEqual(outcome["status"], "timed_out")
        self.assertIn("do not replay", outcome["detail"])

    def test_remote_registration_round_trip(self):
        sys.path.insert(0, str(ROOT / "tools/room-bridge"))
        from desks import Registry
        reg = Registry(self.registry)
        reg.register("another-desk", "claude-remote", host="user@dell", model=MODEL,
                     effort="high", timeout_s=60)
        entry = reg.resolve("another-desk")
        self.assertEqual(entry["host"], "user@dell")
        self.assertEqual(entry["model"], MODEL)
        self.assertEqual(entry["timeout_s"], 60)

    def test_remote_receipt_requires_matching_requested_model(self):
        sys.path.insert(0, str(ROOT / "tools/room-bridge"))
        import claude_remote_wire as wire
        entry = {"name": "dell-desk", **json.loads(self.registry.read_text())["desks"]["dell-desk"]}
        request_id = "00000000-0000-4000-8000-000000000001"
        for requested in (None, "claude-opus-4-6"):
            with self.subTest(requested=requested):
                packet = {"desk": "dell-desk", "msg_id": request_id, "status": "completed",
                          "actual_model": MODEL, "effort": "high", "result": "answer",
                          "session_id": "00000000-0000-4000-8000-000000000002"}
                if requested is not None:
                    packet["requested_model"] = requested
                def fake(argv, **kwargs):
                    return subprocess.CompletedProcess(argv, 0, json.dumps(packet), "")
                self.assertEqual(wire.run_task(entry, "task", request_id, run=fake)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
