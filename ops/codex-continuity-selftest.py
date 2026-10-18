#!/usr/bin/env python3
"""Contract tests for the native Codex continuity adapters."""
import json
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY = ROOT / "ops" / "codex-history.py"
HOOK = ROOT / "ops" / "codex-continuity-hook.py"
TEST_TMP = pathlib.Path(os.environ.get("TMPDIR", ROOT / "out" / "test-tmp"))
ROLLOUT_NAME = "rollout-2026-09-05T12-00-00-01a0715a-6623-7220-82df-506062d5072f.jsonl"


def window_id(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"codex-selftest-window:{label}"))


def load_history_module():
    spec = importlib.util.spec_from_file_location("codex_history_selftest", HISTORY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hook_module():
    spec = importlib.util.spec_from_file_location("codex_hook_selftest", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_row(session_id, cwd):
    return {
        "timestamp": "2026-09-05T12:00:00Z", "type": "session_meta",
        "payload": {"id": session_id, "session_id": session_id,
                    "cwd": str(pathlib.Path(cwd).resolve()), "originator": "codex_cli_rs",
                    "context_window": {"window_id": window_id("window-initial")}},
    }


class AdapterCase(unittest.TestCase):
    def setUp(self):
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.codex_home = self.base / "codex-home"
        self.project = self.base / "project"
        self.project.mkdir()
        self.rollout_dir = self.codex_home / "sessions" / "2026" / "09" / "05"
        self.rollout_dir.mkdir(parents=True)
        self.session_id = "01a0715a-6623-7220-82df-506062d5072f"
        self.rollout = self.rollout_dir / ROLLOUT_NAME
        self.env = {**os.environ, "CODEX_HOME": str(self.codex_home)}

    def write_rollout(self, rows, final_newline=True):
        body = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False)
                         for row in rows)
        self.rollout.write_text(body + ("\n" if final_newline else ""), encoding="utf-8")

    def native_rollout(self, *rows, final_newline=True):
        self.write_rollout([native_row(self.session_id, self.project), *rows], final_newline)

    def append_rollout(self, row):
        with self.rollout.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")

    def hook_payload(self, event="SessionStart", **extra):
        payload = {"session_id": self.session_id, "transcript_path": str(self.rollout),
                   "cwd": str(self.project), "hook_event_name": event,
                   "model": "gpt-5.6-sol"}
        payload.update(extra)
        return payload

    def run_history(self, command, payload):
        return subprocess.run([sys.executable, str(HISTORY), command],
                              input=json.dumps(payload), text=True, capture_output=True,
                              check=False, env=self.env)

    def install_fake_record_call(self, response=None, outage=False, recovery_tool_error=False):
        fake = self.base / "fake-call.py"
        log = self.base / "calls.jsonl"
        if log.exists():
            log.unlink()
        response_path = self.base / "response.json"
        if response is not None:
            response_path.write_text(json.dumps(response), encoding="utf-8")
        fake.write_text(
            """#!/usr/bin/env python3
import json, os, pathlib, sys
log = pathlib.Path(os.environ["FAKE_CALL_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"verb": sys.argv[1], "args": json.loads(sys.argv[2])}, separators=(",", ":")) + "\\n")
if os.environ.get("FAKE_CALL_OUTAGE") == "1":
    print("simulated store outage", file=sys.stderr)
    raise SystemExit(1)
if sys.argv[1] == "codex-read-recovery":
    path = os.environ.get("FAKE_RESPONSE")
    value = pathlib.Path(path).read_text(encoding="utf-8") if path else '{"ok":true,"found":false,"checkpoint":null}'
    if os.environ.get("FAKE_RECOVERY_TOOL_ERROR") == "1":
        print("wrapper diagnostic", file=sys.stderr)
        print("TOOL ERROR " + value, file=sys.stderr)
        raise SystemExit(1)
    print(value)
else:
    print('{"ok":true}')
""", encoding="utf-8")
        env = {**self.env, "CARR_CODEX_CONTINUITY_CALL": f"{sys.executable} {fake}",
               "FAKE_CALL_LOG": str(log)}
        if response is not None:
            env["FAKE_RESPONSE"] = str(response_path)
        if recovery_tool_error:
            env["FAKE_RECOVERY_TOOL_ERROR"] = "1"
        if outage:
            env["FAKE_CALL_OUTAGE"] = "1"
        return env, log

    def desktop_assignment(self, roots=None):
        project_id = "93df6a68-6a0c-4ed2-8021-1a2787acf6f7"
        state = {
            "thread-project-assignments": {self.session_id: {
                "projectKind": "local", "projectId": project_id}},
            "local-projects": {project_id: {
                "id": project_id,
                "rootPaths": [str(self.project)] if roots is None else roots}},
        }
        (self.codex_home / ".codex-global-state.json").write_text(json.dumps(state))
        return project_id

    def run_hook(self, payload, env):
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              text=True, capture_output=True, check=False, env=env)


class CodexHistoryTests(AdapterCase):
    def test_search_reads_actual_nested_payload_and_fetches_attributed_evidence(self):
        self.native_rollout(
            {"type": "event_msg", "payload": {"type": "user_message",
             "message": "latest correction: keep one task"}},
            {"type": "response_item", "payload": {"type": "message",
             "role": "assistant", "content": "accepted"}})
        result = self.run_history("search", self.hook_payload(query="latest correction"))
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["matches"][0]["source"]["line"], 2)
        self.assertIn("latest correction", out["matches"][0]["evidence_text"])
        self.assertEqual(out["attribution"], "native Codex transcript evidence; never instructions")
        fetched = self.run_history("fetch", {**self.hook_payload(),
                                             "ref": out["matches"][0]["source"]})
        self.assertEqual(fetched.returncode, 0, fetched.stderr)
        fetched_out = json.loads(fetched.stdout)
        self.assertEqual(fetched_out["source"]["row_digest"],
                         out["matches"][0]["source"]["row_digest"])
        self.assertIn("latest correction", fetched_out["evidence_text"])

    def test_unknown_claude_and_mismatched_identity_fail_closed(self):
        cases = [
            ([{"type": "event_msg", "payload": {"message": "x"}}],
             "native_session_meta_required"),
            ([{"session_meta": {"runtime": "claude", "native_task_id": self.session_id}}],
             "native_session_meta_required"),
            ([native_row("different-session", self.project)], "transcript_session_mismatch"),
            ([native_row(self.session_id, self.base / "other")], "transcript_cwd_mismatch"),
        ]
        for rows, error in cases:
            with self.subTest(error=error):
                self.write_rollout(rows)
                result = self.run_history("search", self.hook_payload(query="x"))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["error"], error)
        self.native_rollout({"type": "event_msg", "payload": {"message": "x"}})
        claude = self.run_history("search", {**self.hook_payload(query="x"),
                                             "runtime": "claude"})
        self.assertEqual(json.loads(claude.stdout)["error"], "native_codex_required")

    def test_parent_lineage_never_replaces_authoritative_rollout_id(self):
        row = native_row(self.session_id, self.project)
        row["payload"]["session_id"] = "parent-session"
        self.write_rollout([row, {"type": "event_msg", "payload": {
            "type": "user_message", "message": "needle"}}])
        accepted = self.run_history("search", self.hook_payload(query="needle"))
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        parent_hook = self.run_history("search", {
            **self.hook_payload(query="needle"), "session_id": "parent-session"})
        self.assertEqual(json.loads(parent_hook.stdout)["error"],
                         "transcript_session_mismatch")

    def test_only_current_codex_rollouts_are_read_and_symlink_escapes_are_refused(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "needle"}})
        outside = self.base / "arbitrary.jsonl"
        outside.write_text(self.rollout.read_text(encoding="utf-8"), encoding="utf-8")
        rejected = self.run_history("search", {**self.hook_payload(query="needle"),
                                               "transcript_path": str(outside)})
        self.assertEqual(json.loads(rejected.stdout)["error"], "transcript_path_untrusted")
        link = self.rollout_dir / (
            "rollout-2026-09-05T12-00-01-01a0715a-6623-7220-82df-506062d5072f.jsonl")
        link.symlink_to(outside)
        rejected = self.run_history("search", {**self.hook_payload(query="needle"),
                                               "transcript_path": str(link)})
        self.assertEqual(json.loads(rejected.stdout)["error"], "transcript_path_symlink")

    def test_result_line_and_byte_limits_have_continuation_and_coverage(self):
        self.native_rollout(*[
            {"type": "event_msg", "payload": {"type": "user_message",
             "message": f"needle {i} " + "x" * 80}} for i in range(8)])
        first = json.loads(self.run_history("search", {**self.hook_payload(query="needle"),
                                                       "limit": 2}).stdout)
        self.assertEqual(first["count"], 2)
        self.assertIn("result_limit_reached", first["coverage"]["warnings"])
        self.assertFalse(first["coverage"]["complete"])
        second = json.loads(self.run_history("search", {
            **self.hook_payload(query="needle"), "cursor": first["next_cursor"],
            "limit": 2}).stdout)
        self.assertEqual(second["matches"][0]["source"]["line"], 4)
        bounded = json.loads(self.run_history("search", {
            **self.hook_payload(query="absent"), "max_lines": 2, "max_bytes": 512}).stdout)
        self.assertLessEqual(bounded["coverage"]["lines_scanned"], 2)
        self.assertTrue(set(bounded["coverage"]["warnings"]) &
                        {"line_limit_reached", "byte_limit_reached"})

    def test_partial_oversize_rotation_and_truncation_are_explicit(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "unterminated"}},
                            final_newline=False)
        partial = json.loads(self.run_history("search", self.hook_payload(query="never")).stdout)
        self.assertEqual(partial["gaps"][0]["kind"], "partial_final_line")
        self.native_rollout({"type": "event_msg", "payload": {
            "type": "user_message", "message": "valid"}})
        with self.rollout.open("ab") as handle:
            handle.write(b"{malformed}\n")
        malformed = json.loads(self.run_history("search", self.hook_payload(query="never")).stdout)
        self.assertEqual(malformed["gaps"][0]["kind"], "malformed_json")
        self.native_rollout({"type": "event_msg",
                             "payload": {"message": "x" * (256 * 1024 + 1)}})
        oversized = json.loads(self.run_history("search", self.hook_payload(query="never")).stdout)
        self.assertEqual(oversized["gaps"][0]["kind"], "oversize_line")
        self.native_rollout({"type": "event_msg", "payload": {"message": "one"}},
                            {"type": "event_msg", "payload": {"message": "two"}})
        cursor = json.loads(self.run_history("search",
                            self.hook_payload(query="absent")).stdout)["next_cursor"]
        self.write_rollout([native_row(self.session_id, self.project)])
        truncated = self.run_history("search", {**self.hook_payload(query="x"),
                                                "cursor": cursor})
        self.assertEqual(json.loads(truncated.stdout)["error"], "transcript_truncated")
        old_identity = (self.rollout.stat().st_dev, self.rollout.stat().st_ino)
        replacement = self.rollout.with_suffix(".replacement")
        replacement.write_text(
            "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False)
                      for row in [native_row(self.session_id, self.project),
                                  {"type": "event_msg", "payload": {
                                      "message": "replacement"}}]) + "\n",
            encoding="utf-8",
        )
        replacement_identity = (replacement.stat().st_dev, replacement.stat().st_ino)
        self.assertNotEqual(replacement_identity, old_identity)
        os.replace(replacement, self.rollout)
        rotated = self.run_history("search", {**self.hook_payload(query="x"),
                                              "cursor": cursor})
        self.assertEqual(json.loads(rotated.stdout)["error"], "transcript_rotated")

    def test_allowlisted_shapes_redact_credentials_and_unknown_rows_are_gaps(self):
        raw_secret = "sk-abcdefghijklmnopqrstuvwx"
        self.native_rollout(
            {"type": "response_item", "payload": {
                "type": "function_call", "name": "deploy_tool",
                "arguments": json.dumps({"password": "open-sesame",
                                         "api_key": raw_secret})}},
            {"type": "future_native_row", "payload": {
                "type": "future_payload", "credentials": raw_secret,
                "nested": {"password": "must-never-escape"}}})
        result = self.run_history("search", self.hook_payload(query="deploy_tool"))
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["count"], 1)
        evidence = out["matches"][0]["evidence_text"]
        self.assertIn("<REDACTED>", evidence)
        self.assertNotIn(raw_secret, result.stdout)
        self.assertNotIn("open-sesame", result.stdout)
        self.assertNotIn("must-never-escape", result.stdout)
        self.assertEqual(out["coverage"]["redactions"], 2)
        self.assertEqual(out["coverage"]["unsupported_rows"], 1)
        self.assertIn("redacted_evidence", out["coverage"]["warnings"])
        self.assertIn("unsupported_format", out["coverage"]["warnings"])
        self.assertEqual(out["gaps"][0]["kind"], "unsupported_format")
        secret_search = json.loads(self.run_history(
            "search", self.hook_payload(query=raw_secret)).stdout)
        self.assertEqual(secret_search["count"], 0)
        self.assertNotIn(raw_secret, json.dumps(secret_search))

    def test_more_than_fifty_gaps_report_exact_omitted_count(self):
        self.native_rollout(*[
            {"type": f"future_type_{index}", "payload": {"type": "opaque"}}
            for index in range(63)])
        result = self.run_history("search", self.hook_payload(query="absent"))
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(len(out["gaps"]), 50)
        self.assertEqual(out["coverage"]["gaps_total"], 63)
        self.assertEqual(out["coverage"]["gaps_omitted"], 13)
        self.assertIn("gaps_omitted", out["coverage"]["warnings"])

    def test_validated_rollout_rejects_rotation_and_truncation_on_reopen(self):
        self.native_rollout({"type": "event_msg", "payload": {
            "type": "user_message", "message": "stable evidence"}})
        history = load_history_module()
        original_codex_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.addCleanup(lambda: (os.environ.__setitem__("CODEX_HOME", original_codex_home)
                                 if original_codex_home is not None
                                 else os.environ.pop("CODEX_HOME", None)))
        payload = self.hook_payload(query="stable")
        meta = history.validate_native_rollout(payload)
        first = history.command_search(payload)
        ref = first["matches"][0]["source"]
        old = self.rollout.with_suffix(".old")
        self.rollout.rename(old)
        self.native_rollout({"type": "event_msg", "payload": {
            "type": "user_message", "message": "replacement"}})
        with self.assertRaises(history.HistoryFailure) as rotated:
            history.source_highwater(meta)
        self.assertEqual(rotated.exception.payload["error"], "transcript_rotated")
        original_validate = history.validate_native_rollout
        history.validate_native_rollout = lambda ignored: meta
        self.addCleanup(setattr, history, "validate_native_rollout", original_validate)
        with self.assertRaises(history.HistoryFailure) as search_rotated:
            history.command_search(payload)
        self.assertEqual(search_rotated.exception.payload["error"],
                         "transcript_rotated")
        with self.assertRaises(history.HistoryFailure) as fetch_rotated:
            history.command_fetch({**payload, "ref": ref})
        self.assertEqual(fetch_rotated.exception.payload["error"],
                         "transcript_rotated")

        history.validate_native_rollout = original_validate
        meta = history.validate_native_rollout(payload)
        with self.rollout.open("r+b") as handle:
            handle.truncate(meta["header_end"])
        with self.assertRaises(history.HistoryFailure) as truncated:
            history.source_highwater(meta)
        self.assertEqual(truncated.exception.payload["error"], "transcript_truncated")


class CodexHookTests(AdapterCase):
    def checkpoint(self, state=None, cursor=None, version=7):
        return {"ok": True, "found": True, "checkpoint": {
            "checkpoint_version": version,
            "state": state or {
                "objective": "keep one task effective",
                "latest_corrections": [{"text": "use native payloads", "refs": ["rollout:2"]}],
                "decisions": [{"text": "rejected approach", "why": "fabricated schema",
                               "refs": ["rollout:3"]}],
                "next_action": "verify the actual hook contract"},
            "cursor": cursor or {"byte_offset": 1, "source_digest": "0" * 64}}}

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        context = output["additionalContext"]
        self.assertLessEqual(len(context.encode("utf-8")), 12000)
        return context

    def binding(self, context):
        line = next(line for line in context.splitlines() if "arguments=" in line)
        return json.loads(line.split("arguments=", 1)[1])

    def test_session_start_delivers_saved_state_and_next_binding(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        env, log = self.install_fake_record_call(self.checkpoint())
        for source in ("startup", "resume", "compact"):
            context = self.context(self.run_hook(self.hook_payload(source=source), env))
            for expected in ("keep one task effective", "use native payloads", "rejected approach",
                             "fabricated schema", "verify the actual hook contract",
                             "checkpoint version 7", "never auto-reexecute", "codex-history.py search"):
                self.assertIn(expected, context)
            binding = self.binding(context)
            self.assertEqual(binding["expected_version"], 7)
            self.assertEqual(binding["native_task_id"], self.session_id)
            self.assertEqual(binding["cursor"]["byte_offset"], self.rollout.stat().st_size)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual({call["verb"] for call in calls}, {"codex-read-recovery"})
        self.assertTrue(all("project_id" in call["args"] for call in calls))

    def test_missing_outage_and_rejection_are_distinct(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        env, _ = self.install_fake_record_call({"ok": True, "found": False, "checkpoint": None})
        missing = self.context(self.run_hook(self.hook_payload(source="startup"), env))
        self.assertIn("no checkpoint is saved", missing)
        self.assertEqual(self.binding(missing)["expected_version"], 0)
        env, _ = self.install_fake_record_call(outage=True)
        outage = self.context(self.run_hook(self.hook_payload(source="startup"), env))
        self.assertIn("checkpoint recovery unavailable", outage)
        self.assertNotIn("arguments=", outage)
        env, _ = self.install_fake_record_call({"error": "codex_native_principal_required"},
                                               recovery_tool_error=True)
        rejected = self.context(self.run_hook(self.hook_payload(source="resume"), env))
        self.assertIn("checkpoint recovery codex_native_principal_required", rejected)

    def test_large_recovery_omits_whole_items_with_warning(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        state = {"objective": "objective",
                 "latest_corrections": [{"text": "latest correction", "refs": ["rollout:2"]}],
                 "constraints": [{"text": f"constraint-{i}-" + "z" * 3000, "refs": ["rollout:3"]}
                                 for i in range(6)],
                 "next_action": "next action"}
        env, _ = self.install_fake_record_call(self.checkpoint(state=state))
        context = self.context(self.run_hook(self.hook_payload(source="compact"), env))
        for expected in ("latest correction", "next action", "coverage warning", "arguments="):
            self.assertIn(expected, context)
        self.assertNotIn('"text":"constraint-5-', context)

    def test_legacy_desktop_project_recovers_only_verified_existing_checkpoint(self):
        hook = load_hook_module()
        project_id = self.desktop_assignment()
        meta = {"runtime": "codex", "native_task_id": self.session_id,
                "project_id": "git:canonical", "cwd": str(self.project)}
        calls = []

        def found(_verb, args):
            calls.append(args["project_id"])
            if args["project_id"] == "git:canonical":
                return {"status": "rejected", "error": "codex_recovery_binding_conflict"}
            return {"status": "ok", "response": {"ok": True, "found": True,
                                                 "checkpoint": {"checkpoint_version": 2}}}

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), \
             mock.patch.object(hook, "call_verb", side_effect=found):
            self.assertEqual(hook.read_recovery(meta)["status"], "ok")
        self.assertEqual((meta["project_id"], calls), (project_id, ["git:canonical", project_id]))

        meta["project_id"] = "git:canonical"

        def missing(_verb, args):
            if args["project_id"] == "git:canonical":
                return {"status": "rejected", "error": "codex_recovery_binding_conflict"}
            return {"status": "ok", "response": {"ok": True, "found": False}}

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), \
             mock.patch.object(hook, "call_verb", side_effect=missing):
            self.assertEqual(hook.read_recovery(meta)["status"], "rejected")
        self.assertEqual(meta["project_id"], "git:canonical")

    def test_other_events_and_untrusted_payloads_have_no_side_effect(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        env, log = self.install_fake_record_call(self.checkpoint())
        for payload in (self.hook_payload("UserPromptSubmit", turn_id="t1"),
                        self.hook_payload("PreCompact", turn_id="t1", trigger="auto"),
                        self.hook_payload(source="clear"),
                        {**self.hook_payload(source="startup"), "session_id": "other-session"},
                        {**self.hook_payload(source="startup"), "runtime": "claude"}):
            result = self.run_hook(payload, env)
            self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
