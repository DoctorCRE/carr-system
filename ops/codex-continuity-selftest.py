#!/usr/bin/env python3
"""Contract tests for native Codex transcript history."""
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
TEST_TMP = pathlib.Path(os.environ.get("TMPDIR", ROOT / "out" / "test-tmp"))
ROLLOUT_NAME = "rollout-2026-09-05T12-00-00-01a0715a-6623-7220-82df-506062d5072f.jsonl"


def window_id(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"codex-selftest-window:{label}"))


def load_history_module():
    spec = importlib.util.spec_from_file_location("codex_history_selftest", HISTORY)
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




if __name__ == "__main__":
    unittest.main()
