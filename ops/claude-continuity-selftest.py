#!/usr/bin/env python3
"""Checks for the native Claude checkpoint adapter (SessionStart + PreCompact)."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "ops/claude-continuity-hook.py"
sys.path.insert(0, str(ROOT))
from lib import claude_continuity_config as continuity_config  # noqa: E402

CALLER = """#!/usr/bin/env python3
import json,os,sys
name,args=sys.argv[1],json.loads(sys.argv[2])
with open(os.environ['CALL_LOG'],'a',encoding='utf-8') as out:
 out.write(json.dumps({'name':name,'args':args,'profile':os.environ.get('CARR_MCP_CLIENT_PROFILE')})+'\\n')
if os.environ.get('RECOVERY_EMPTY')=='1':
 print(json.dumps({'ok':True,'found':False,'checkpoint':None,'capsule':None}))
else:
 print(json.dumps({'ok':True,'found':True,'checkpoint':{'checkpoint_version':'3','compaction_generation':2},'capsule':'bounded recovery capsule'}))
"""


class ClaudeContinuityHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="carr-claude-continuity-")
        self.root = pathlib.Path(self.temp.name).resolve()
        self.transcript = self.root / "session-1.jsonl"
        self.transcript.write_text('{"type":"user","message":"local only"}\n', encoding="utf-8")
        self.mode = self.root / "mode.json"
        self.calls = self.root / "calls.jsonl"
        self.dedupe = self.root / "dedupe"
        caller = self.root / "call.py"
        caller.write_text(CALLER, encoding="utf-8")
        caller.chmod(0o755)
        self.env = {**os.environ,
                    "CARR_CLAUDE_CONTINUITY_MODE_FILE": str(self.mode),
                    "CARR_CLAUDE_CONTINUITY_SESSION_DIR": str(self.root / "session-state"),
                    "CARR_CLAUDE_CONTINUITY_CALL": str(caller),
                    "CARR_CLAUDE_TRANSCRIPT_ROOTS": str(self.root),
                    "CARR_CLAUDE_RULE_DEDUPE_DIR": str(self.dedupe),
                    "CARR_CLAUDE_RULE_DEDUPE_AUDIT": str(self.root / "rule-dedupe-audit.jsonl"),
                    "CALL_LOG": str(self.calls)}

    def tearDown(self):
        self.temp.cleanup()

    def set_mode(self, mode, digest=None):
        digest = digest or continuity_config.load(ROOT).config_digest
        self.mode.write_text(json.dumps({"schema_version": 1, "mode": mode, "config_digest": digest}))

    def run_hook(self, event, cwd=None, **extra):
        payload = {"hook_event_name": event, "session_id": "session-1",
                   "transcript_path": str(self.transcript), "cwd": str(cwd or self.root), **extra}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=self.env, timeout=20, check=False)

    def calls_made(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        return output["additionalContext"]

    def arguments(self, context):
        line = next(line for line in context.splitlines() if line.startswith("arguments="))
        return json.loads(line[len("arguments="):])

    def test_contract_has_exactly_two_events(self):
        self.assertEqual(set(continuity_config.load(ROOT).hooks), {"SessionStart", "PreCompact"})

    def test_disabled_or_stale_mode_does_nothing(self):
        self.assertEqual(self.run_hook("SessionStart", source="compact").stdout, "")
        self.set_mode("shadow")
        self.assertEqual(self.run_hook("SessionStart", source="compact").stdout, "")
        self.set_mode("inject", digest="sha256:" + "0" * 64)
        result = self.run_hook("SessionStart", source="compact")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(self.calls_made(), [])

    def test_startup_hands_over_the_checkpoint_binding(self):
        self.set_mode("inject")
        context = self.context(self.run_hook("SessionStart", source="startup", model="claude-x"))
        args = self.arguments(context)
        digest = hashlib.sha256(str(self.transcript).encode()).hexdigest()
        self.assertEqual(args["session_id"], "session-1")
        self.assertEqual(args["transcript_path_digest"], digest)
        self.assertEqual(args["expected_version"], 3)
        self.assertEqual(args["compaction_generation"], 2)
        self.assertEqual(args["state"]["source_cursor"]["byte_offset"], self.transcript.stat().st_size)
        self.assertIn("claude-checkpoint", context)
        self.assertNotIn("bounded recovery capsule", context)
        [call] = self.calls_made()
        self.assertEqual((call["name"], call["profile"]), ("claude-read-recovery", "claude-continuity"))
        self.assertEqual(call["args"]["model_id"], "claude-x")

    def test_compact_injects_capsule_only_in_inject_mode(self):
        self.set_mode("inject")
        self.assertIn("bounded recovery capsule", self.context(self.run_hook("SessionStart", source="compact")))
        self.set_mode("checkpoint")
        self.assertNotIn("bounded recovery capsule", self.context(self.run_hook("SessionStart", source="resume")))

    def test_missing_checkpoint_and_outage(self):
        self.set_mode("inject")
        self.env["RECOVERY_EMPTY"] = "1"
        self.assertEqual(self.arguments(self.context(self.run_hook("SessionStart", source="startup")))
                         ["expected_version"], 0)
        self.env["CARR_CLAUDE_CONTINUITY_CALL"] = "/definitely/missing"
        result = self.run_hook("SessionStart", source="compact")
        self.assertIsNone(self.arguments(self.context(result))["expected_version"])
        self.assertIn("claude-read-recovery unavailable", result.stderr)

    def test_precompact_and_compact_reset_rule_delivery_generation(self):
        self.set_mode("checkpoint")
        leaf = hashlib.sha256(str(self.transcript).encode()).hexdigest()
        state = self.dedupe / (hashlib.sha256(f"session-1\0{leaf}".encode()).hexdigest() + ".json")
        self.dedupe.mkdir()
        state.write_text(json.dumps({"schema_version": 1, "compaction_generation": 4, "digests": ["old"]}))
        result = self.run_hook("PreCompact")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(json.loads(state.read_text()),
                         {"schema_version": 1, "compaction_generation": 5, "digests": []})
        self.run_hook("SessionStart", source="compact")
        self.assertEqual(json.loads(state.read_text())["compaction_generation"], 6)
        self.assertEqual([c["name"] for c in self.calls_made()], ["claude-read-recovery"])

    def test_untrusted_input_is_ignored(self):
        self.set_mode("inject")
        outside = pathlib.Path(tempfile.mkdtemp()) / "session-1.jsonl"
        for payload in ({"agent_id": "🧭" * 200}, {"transcript_path": str(outside)},
                        {"session_id": "other-session"}):
            result = self.run_hook("SessionStart", source="compact", **payload)
            self.assertEqual((result.returncode, result.stdout), (0, ""))
            self.assertIn("unverified native input ignored", result.stderr)
        self.assertEqual(self.calls_made(), [])

    def test_project_affinity_is_pinned_for_the_session(self):
        self.set_mode("inject")
        first = self.arguments(self.context(self.run_hook("SessionStart", source="startup")))
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        second = self.arguments(self.context(self.run_hook("SessionStart", source="compact", cwd=elsewhere)))
        self.assertEqual(first["project_affinity"], second["project_affinity"])


if __name__ == "__main__":
    unittest.main()
