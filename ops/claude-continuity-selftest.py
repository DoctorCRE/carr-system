#!/usr/bin/env python3
"""Deterministic lifecycle checks for the native Claude continuity adapter."""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "ops/claude-continuity-hook.py"
from git_env import fixture_env

GIT_ENV = fixture_env()


def load_hook():
    spec = importlib.util.spec_from_file_location("claude_continuity_hook", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClaudeContinuityHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="carr-claude-continuity-")
        self.root = pathlib.Path(self.temp.name)
        self.transcript = self.root / "session-1.jsonl"
        self.transcript.write_text('{"type":"user","message":"local only"}\n', encoding="utf-8")
        self.mode = self.root / "mode.json"
        self.spool = self.root / "spool"
        self.calls = self.root / "calls.jsonl"
        self.audit = self.root / "audit.jsonl"
        self.dedupe = self.root / "dedupe"
        self.caller = self.root / "call.py"
        self.caller.write_text("""#!/usr/bin/env python3
import json,os,sys
name,args=sys.argv[1],json.loads(sys.argv[2])
with open(os.environ['CALL_LOG'],'a',encoding='utf-8') as out:
 out.write(json.dumps({'name':name,'args':args,'profile':os.environ.get('CARR_MCP_CLIENT_PROFILE')})+'\\n')
if name=='claude-read-recovery':
 if os.environ.get('RECOVERY_EMPTY')=='1':
  print(json.dumps({'ok':True,'found':False,'checkpoint':None,'capsule':None}))
 else:
  print(json.dumps({'ok':True,'found':True,'checkpoint':{'checkpoint_version':'3'},'capsule':os.environ.get('RECOVERY_CAPSULE','bounded recovery capsule')}))
else:
 print(json.dumps({'ok':True}))
""", encoding="utf-8")
        self.caller.chmod(0o755)
        self.base_env = {**os.environ,
            "CARR_CLAUDE_CONTINUITY_MODE_FILE": str(self.mode),
            "CARR_CLAUDE_CONTINUITY_SPOOL_DIR": str(self.spool),
            "CARR_CLAUDE_CONTINUITY_SESSION_DIR": str(self.root / "session-state"),
            "CARR_CLAUDE_CONTINUITY_CALL": str(self.caller),
            "CARR_CLAUDE_TRANSCRIPT_ROOTS": str(self.root),
            "CARR_CLAUDE_CONTINUITY_AUDIT": str(self.audit),
            "CARR_CLAUDE_RULE_DEDUPE_DIR": str(self.dedupe),
            "CARR_CLAUDE_RULE_DEDUPE_AUDIT": str(self.root / "rule-dedupe-audit.jsonl"),
            "CALL_LOG": str(self.calls),
        }

    def tearDown(self):
        self.temp.cleanup()

    def set_mode(self, mode):
        digest = load_hook().expected_config_digest()
        self.mode.write_text(json.dumps({"schema_version": 1, "mode": mode,
                                         "config_digest": digest}), encoding="utf-8")

    def run_hook(self, event, **extra):
        payload = {"hook_event_name": event, "session_id": "session-1",
                   "transcript_path": str(self.transcript), "cwd": str(self.root), **extra}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, env=self.base_env, timeout=10, check=False)

    def call_rows(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_disabled_and_shadow_never_contact_the_store(self):
        self.assertEqual(self.run_hook("UserPromptSubmit").returncode, 0)
        self.set_mode("shadow")
        result = self.run_hook("PreCompact")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.call_rows(), [])
        audit = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual(audit[0]["event"], "PreCompact")
        self.assertEqual(audit[0]["mode"], "shadow")
        self.assertNotIn("transcript_path", audit[0])

    def test_stale_config_digest_disables_all_activity(self):
        self.mode.write_text(json.dumps({"schema_version": 1, "mode": "inject",
                                         "config_digest": "sha256:" + "0" * 64}))
        result = self.run_hook("SessionStart", source="resume")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.call_rows(), [])

    def test_checkpoint_mode_records_receipts_and_never_blocks_stop(self):
        self.set_mode("checkpoint")
        for event in ("UserPromptSubmit", "PreCompact", "Stop"):
            result = self.run_hook(event)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        rows = self.call_rows()
        self.assertEqual([row["args"]["event_type"] for row in rows],
                         ["user_prompt_submit", "pre_compact", "stop"])
        self.assertTrue(all(row["profile"] == "claude-continuity" for row in rows))
        self.assertTrue(all("transcript_path" not in row["args"] for row in rows))
        self.assertTrue(all(len(row["args"]["transcript_path_digest"]) == 64 for row in rows))

    def test_inject_reads_only_on_compact_or_resume_session_start(self):
        self.set_mode("inject")
        startup = self.run_hook("SessionStart", source="startup")
        compact = self.run_hook("SessionStart", source="compact")
        startup_output = json.loads(startup.stdout)
        self.assertIn("current_checkpoint_version=3", startup_output["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("bounded recovery capsule", startup_output["hookSpecificOutput"]["additionalContext"])
        output = json.loads(compact.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("bounded recovery capsule", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual([row["name"] for row in self.call_rows()],
                         ["claude-read-recovery", "claude-read-recovery"])

    def test_new_session_activation_exposes_exact_first_checkpoint_contract(self):
        self.set_mode("checkpoint")
        self.base_env["RECOVERY_EMPTY"] = "1"
        result = self.run_hook("SessionStart", source="startup")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn('"session_id":"session-1"', context)
        self.assertIn('"transcript_path_digest":', context)
        self.assertIn('"project_affinity":', context)
        self.assertIn("current_checkpoint_version=0", context)
        self.assertIn("call mcp__carr-continuity__claude-checkpoint", context)
        self.assertIn("expected_version=current_checkpoint_version", context)
        self.assertIn("must never be replayed automatically", context)
        self.assertLessEqual(len(context.encode()), 4800)

    def test_startup_before_transcript_creation_emits_verified_pending_cursor(self):
        self.set_mode("checkpoint")
        projects = self.root / "projects"
        project = projects / "-Users-booko-carr-system"
        project.mkdir(parents=True)
        self.base_env["CARR_CLAUDE_TRANSCRIPT_ROOTS"] = str(projects)
        self.transcript = project / "session-1.jsonl"

        result = self.run_hook("SessionStart", source="startup")

        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("current_checkpoint_version=3", context)
        cursor = json.loads(context.split("source_cursor=", 1)[1].split("\n", 1)[0])
        self.assertEqual(cursor["byte_offset"], 0)
        self.assertEqual(cursor["mtime_ns"], 0)
        self.assertTrue(cursor["startup_pending"])
        self.assertRegex(cursor["source_digest"], r"^[0-9a-f]{64}$")
        rows = self.call_rows()
        self.assertEqual([row["name"] for row in rows], ["claude-read-recovery"])
        self.assertEqual(rows[0]["args"]["session_id"], "session-1")

    def test_missing_transcript_fallback_is_startup_only_and_direct_project_only(self):
        self.set_mode("checkpoint")
        projects = self.root / "projects"
        project = projects / "-Users-booko-carr-system"
        nested = project / "nested"
        nested.mkdir(parents=True)
        self.base_env["CARR_CLAUDE_TRANSCRIPT_ROOTS"] = str(projects)
        self.transcript = project / "session-1.jsonl"
        resume = self.run_hook("SessionStart", source="resume")
        self.assertEqual(resume.stdout, "")
        self.assertEqual(self.call_rows(), [])

        self.transcript = nested / "session-1.jsonl"
        startup = self.run_hook("SessionStart", source="startup")
        self.assertEqual(startup.stdout, "")
        self.assertEqual(self.call_rows(), [])

    def test_maximum_subagent_binding_and_worker_capsule_fit_native_limit(self):
        self.set_mode("inject")
        session_id = "s" + "a" * 199
        agent_id = "g" + "b" * 199
        subagents = self.root / "subagents"
        subagents.mkdir()
        transcript = subagents / f"{agent_id}.jsonl"
        transcript.write_text('{"type":"user","message":"local only"}\n', encoding="utf-8")
        mandatory = ("Objective:\n- objective sentinel\nCurrent corrections:\n- correction sentinel\n"
                     "Current constraints:\n- constraint sentinel\n"
                     "Pending external effects (verify; never replay):\n- pending sentinel\n"
                     "Next action:\n- next sentinel\n")
        self.base_env["RECOVERY_CAPSULE"] = mandatory + "x" * (3200 - len(mandatory.encode()))

        result = self.run_hook("SessionStart", source="compact", session_id=session_id,
                               agent_id=agent_id, transcript_path=str(transcript))

        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context.encode()), 4800)
        for sentinel in ("objective sentinel", "correction sentinel", "constraint sentinel",
                         "pending sentinel", "next sentinel"):
            self.assertIn(sentinel, context)

    def test_multibyte_agent_identifier_is_refused_before_recovery(self):
        self.set_mode("inject")
        result = self.run_hook("SessionStart", source="compact", agent_id="🧭" * 200)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("unverified native input ignored", result.stderr)
        self.assertEqual(self.call_rows(), [])

    def test_precompact_and_compact_resume_reset_rule_delivery_generation(self):
        self.set_mode("checkpoint")
        leaf_digest = hashlib.sha256(str(self.transcript.resolve()).encode()).hexdigest()
        token = hashlib.sha256(f"session-1\0{leaf_digest}".encode()).hexdigest()
        self.dedupe.mkdir()
        state_path = self.dedupe / f"{token}.json"
        state_path.write_text(json.dumps({"schema_version": 1,
                                          "compaction_generation": 4,
                                          "digests": ["old-rule-set"]}))
        self.run_hook("PreCompact")
        first = json.loads(state_path.read_text())
        self.assertEqual(first, {"schema_version": 1,
                                 "compaction_generation": 5, "digests": []})
        self.run_hook("SessionStart", source="compact")
        second = json.loads(state_path.read_text())
        self.assertEqual(second["compaction_generation"], 6)
        self.assertEqual(second["digests"], [])

    def test_outage_spools_signed_receipt_but_never_replays_it(self):
        self.set_mode("checkpoint")
        self.base_env["CARR_CLAUDE_CONTINUITY_CALL"] = "/definitely/missing"
        first = self.run_hook("PreCompact")
        second = self.run_hook("Stop")
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        receipts = sorted(self.spool.glob("*.json"))
        self.assertEqual(len(receipts), 2)
        key = self.spool.with_suffix(".key").read_bytes()
        self.assertEqual(len(key), 32)
        for receipt in receipts:
            doc = json.loads(receipt.read_text())
            signature = doc.pop("hmac_sha256")
            canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            self.assertTrue(hmac.compare_digest(signature, hmac.new(key, canonical, hashlib.sha256).hexdigest()))
        self.base_env["CARR_CLAUDE_CONTINUITY_CALL"] = str(self.caller)
        self.run_hook("SessionStart", source="resume")
        self.assertEqual(len(list(self.spool.glob("*.json"))), 2,
                         "recovery never replays pending receipt files")

    def test_project_affinity_survives_worktree_path_change(self):
        repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, env=GIT_ENV)
        (repo / "a").write_text("a")
        subprocess.run(["git", "-C", str(repo), "add", "a"], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=GIT_ENV)
        other = self.root / "other-worktree"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "other", str(other)], check=True, env=GIT_ENV)
        hook = load_hook()
        with mock.patch.dict(os.environ, GIT_ENV, clear=True):
            self.assertEqual(hook.project_affinity(repo.resolve()), hook.project_affinity(other.resolve()))

    def test_post_tool_sampling_is_stable_and_requires_native_tool_id(self):
        hook = load_hook()
        identity = {"session_id": "session"}
        self.assertFalse(hook._sample_post_tool({}, identity))
        values = [hook._sample_post_tool({"tool_use_id": f"tool-{index}"}, identity)
                  for index in range(1000)]
        self.assertEqual(values, [hook._sample_post_tool({"tool_use_id": f"tool-{index}"}, identity)
                                  for index in range(1000)])
        self.assertGreater(sum(values), 70)
        self.assertLess(sum(values), 130)

    def test_transcript_must_be_rooted_and_session_or_subagent_bound(self):
        self.set_mode("checkpoint")
        wrong = self.root / "other-session.jsonl"
        wrong.write_text("{}\n")
        self.transcript = wrong
        self.assertEqual(self.run_hook("PreCompact").returncode, 0)
        self.assertEqual(self.call_rows(), [])
        outside = pathlib.Path(self.temp.name).parent / f"outside-{os.getpid()}.jsonl"
        outside.write_text("{}\n")
        try:
            self.transcript = outside
            self.assertEqual(self.run_hook("PreCompact").returncode, 0)
            self.assertEqual(self.call_rows(), [])
        finally:
            outside.unlink(missing_ok=True)
        subdir = self.root / "subagents"
        subdir.mkdir()
        self.transcript = subdir / "agent-leaf-7.jsonl"
        self.transcript.write_text("{}\n")
        result = self.run_hook("PreCompact", agent_id="leaf-7")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.call_rows()[0]["args"]["parent_session_id"], "session-1")


class ClaudeContinuityDeliversTest(ClaudeContinuityHookTest):
    """Checks on whether the protocol does its job, not only whether it refuses bad input."""

    def grow(self, size):
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write("x" * size + "\n")

    def test_a_directory_change_mid_session_does_not_break_the_binding(self):
        # The failure this replaces: cwd moved, project affinity moved with it,
        # and every later write was refused for the life of the session.
        self.set_mode("checkpoint")
        self.run_hook("UserPromptSubmit")
        moved = self.root / "elsewhere"
        moved.mkdir()
        self.run_hook("UserPromptSubmit", cwd=str(moved))
        rows = self.call_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["args"]["project_affinity"], rows[1]["args"]["project_affinity"],
                         "a cwd change must not repin the leaf binding")
        self.assertEqual(rows[1]["args"]["cwd"], str(moved.resolve()),
                         "the new cwd still travels as telemetry")

    def test_a_deep_session_is_asked_for_a_checkpoint_with_a_fresh_cursor(self):
        self.set_mode("inject")
        self.base_env["CARR_CLAUDE_CONTINUITY_NUDGE_FIRST"] = "4096"
        self.base_env["CARR_CLAUDE_CONTINUITY_NUDGE_INTERVAL"] = "4096"
        self.assertEqual(self.run_hook("UserPromptSubmit").stdout, "",
                         "a shallow session is never nudged")
        self.grow(8192)
        context = json.loads(self.run_hook("UserPromptSubmit").stdout)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "UserPromptSubmit")
        body = context["additionalContext"]
        self.assertIn("claude-checkpoint", body)
        self.assertIn("expected_version=3", body)
        self.assertIn('"session_id":"session-1"', body)
        offset = json.loads(body.split("state.source_cursor=")[1].split("\n")[0])["byte_offset"]
        self.assertEqual(offset, self.transcript.stat().st_size,
                         "the ask must carry the cursor as of this prompt, not session start")

    def test_the_checkpoint_ask_repeats_only_after_real_growth(self):
        self.set_mode("inject")
        self.base_env["CARR_CLAUDE_CONTINUITY_NUDGE_FIRST"] = "4096"
        self.base_env["CARR_CLAUDE_CONTINUITY_NUDGE_INTERVAL"] = "4096"
        self.grow(8192)
        self.assertNotEqual(self.run_hook("UserPromptSubmit").stdout, "")
        self.grow(16)
        self.assertEqual(self.run_hook("UserPromptSubmit").stdout, "",
                         "a nudge per prompt would be nagging, not a trigger")
        self.grow(8192)
        self.assertNotEqual(self.run_hook("UserPromptSubmit").stdout, "")

    def test_an_unread_spool_is_announced_instead_of_accumulating_silently(self):
        self.set_mode("inject")
        self.base_env["CARR_CLAUDE_CONTINUITY_CALL"] = str(self.root / "absent")
        self.run_hook("UserPromptSubmit")
        self.base_env["CARR_CLAUDE_CONTINUITY_CALL"] = str(self.caller)
        context = json.loads(self.run_hook("SessionStart", source="startup").stdout)
        self.assertIn("unsent continuity receipt", context["hookSpecificOutput"]["additionalContext"])

    def test_an_unsampled_tool_event_never_reads_the_transcript(self):
        self.set_mode("checkpoint")
        hook = load_hook()
        unsampled = next(f"tool-{index}" for index in range(1000)
                         if not hook._sample_post_tool({"tool_use_id": f"tool-{index}"},
                                                       {"session_id": "session-1"}))
        self.transcript.unlink()
        result = self.run_hook("PostToolUse", tool_use_id=unsampled, tool_name="Bash")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "", "the sample is decided before the bounded tail read")
        self.assertEqual(self.call_rows(), [])


if __name__ == "__main__":
    unittest.main()
