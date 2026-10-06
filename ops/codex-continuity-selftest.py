#!/usr/bin/env python3
"""Contract tests for the native Codex continuity adapters."""
import contextlib
import io
import json
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import time
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


def compacted_row(number, previous_window, window):
    return {
        "timestamp": f"2026-09-05T12:00:{number:02d}Z", "type": "compacted",
        "payload": {"message": "", "replacement_history": [],
                    "window_number": number,
                    "first_window_id": window_id("window-initial"),
                    "previous_window_id": window_id(previous_window),
                    "window_id": window_id(window)},
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

    def install_fake_record_call(self, response=None, outage=False, event_response=None,
                                 recovery_tool_error=False, event_tool_error=False,
                                 binding_responses=None):
        fake = self.base / "fake-call.py"
        log = self.base / "calls.jsonl"
        if log.exists():
            log.unlink()
        response_path = self.base / "response.json"
        if response is not None:
            response_path.write_text(json.dumps(response), encoding="utf-8")
        event_response_path = self.base / "event-response.json"
        if event_response is not None:
            event_response_path.write_text(json.dumps(event_response), encoding="utf-8")
        fake.write_text(
            """#!/usr/bin/env python3
import json, os, pathlib, sys, time
log = pathlib.Path(os.environ["FAKE_CALL_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"verb": sys.argv[1], "args": json.loads(sys.argv[2])}, separators=(",", ":")) + "\\n")
bindings = json.loads(os.environ.get("FAKE_BINDING_RESPONSES", "{}"))
bound = bindings.get(json.loads(sys.argv[2])["project_id"], {}).get(sys.argv[1])
if bound is not None:
    time.sleep(bound.get("delay", 0))
    value = json.dumps(bound["response"])
    if bound.get("tool_error"):
        print("TOOL ERROR " + value, file=sys.stderr)
        raise SystemExit(1)
    print(value)
    raise SystemExit(0)
time.sleep(float(os.environ.get("FAKE_CALL_DELAY", "0")))
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
elif os.environ.get("FAKE_EVENT_RESPONSE"):
    value = pathlib.Path(os.environ["FAKE_EVENT_RESPONSE"]).read_text(encoding="utf-8")
    if os.environ.get("FAKE_EVENT_TOOL_ERROR") == "1":
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
        if event_response is not None:
            env["FAKE_EVENT_RESPONSE"] = str(event_response_path)
        if recovery_tool_error:
            env["FAKE_RECOVERY_TOOL_ERROR"] = "1"
        if event_tool_error:
            env["FAKE_EVENT_TOOL_ERROR"] = "1"
        if outage:
            env["FAKE_CALL_OUTAGE"] = "1"
        if binding_responses is not None:
            env["FAKE_BINDING_RESPONSES"] = json.dumps(binding_responses)
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
                "latest_corrections": [{"text": "use native payloads",
                                         "refs": ["rollout:2"]}],
                "decisions": [{"text": "rejected approach", "why": "fabricated schema",
                               "refs": ["rollout:3"]}],
                "next_action": "verify the actual hook contract"},
            "cursor": cursor},
            "source_highwater": cursor,
            "unincorporated_user_turns": [],
            "unincorporated_user_turns_omitted": 0,
            "source_coverage": "known"}

    def test_checkpoint_version_requires_a_safe_json_integer(self):
        hook = load_hook_module()
        highwater = {"byte_offset": 12, "device": 1, "inode": 2,
                     "source_digest": "a" * 64}
        for version in (1, 7, (2 ** 53) - 1):
            recovery = {"status": "ok", "response": self.checkpoint(version=version)}
            self.assertEqual(hook.checkpoint_marker(recovery), {
                "checkpoint_version": version, "checkpoint_status": "available"})
            self.assertIn(f"checkpoint version: {version}",
                          hook.checkpoint_freshness(
                              recovery["response"]["checkpoint"], highwater))
        for malformed in ("1", "01", True, 0, 2 ** 53):
            recovery = {"status": "ok", "response": self.checkpoint(version=malformed)}
            self.assertEqual(hook.checkpoint_marker(recovery), {
                "checkpoint_version": None, "checkpoint_status": "unavailable"})
            self.assertIn("checkpoint version: unknown",
                          hook.checkpoint_freshness(
                              recovery["response"]["checkpoint"], highwater))

    def test_compact_session_requires_one_exact_window_checkpoint_refresh(self):
        compacted = compacted_row(1, "window-initial", "window-current")
        compacted["payload"]["replacement_history"] = ["never-store-opaque-compact-body"]
        self.native_rollout(compacted)
        payload = self.hook_payload(source="compact")
        history = load_history_module()
        with mock.patch.dict(os.environ, self.env):
            meta = history.validate_native_rollout(payload)
            highwater = history.source_highwater(meta)
        response = self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-initial"), "source_window_number": 0,
        })
        env, _ = self.install_fake_record_call(response)

        result = self.run_hook(payload, env)

        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        material = json.dumps({
            "operation": "codex-compaction-checkpoint-refresh-v3",
            "runtime": "codex", "native_task_id": self.session_id,
            "project_id": meta["project_id"], "cwd": meta["cwd"],
            "checkpoint_version": 7,
            "source_window_id": window_id("window-current"), "source_window_number": 1,
            "cursor": {**highwater, "source_window_id": window_id("window-current"),
                       "source_window_number": 1, "source": "compact"},
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected_key = str(uuid.uuid5(uuid.NAMESPACE_URL, material))
        for expected in (
            "HIGH PRIORITY CARR COMPACTION CHECKPOINT REPAIR",
            f'"native_task_id":"{self.session_id}"',
            f'"project_id":{json.dumps(meta["project_id"])}',
            f'"cwd":{json.dumps(meta["cwd"])}',
            '"expected_version":7', f'"idempotency_key":"{expected_key}"',
            f'"source_window_id":"{window_id("window-current")}"',
            '"source_window_number":1',
            f'"byte_offset":{highwater["byte_offset"]}',
            f'"source_digest":"{highwater["source_digest"]}"',
            '"source":"compact"',
            "before normal work", "full replacement state", "one fresh codex-read-recovery",
            "retry at most once", "read back and verify", "Use direct MCP for codex-checkpoint",
            "CARR_MCP_CLIENT_PROFILE=codex-continuity ./run.sh call <same verb>",
            "401", "Auth required", "codex_native_principal_required", "codex_continuity_owner_required",
            "codex-checkpoint and codex-read-recovery", "identical request", "same JSON",
            "server principal gate still applies",
            "never generic or unscoped authentication", "hard cap 24,000",
            "<=18,000-byte JSON budget", "2,000-byte reserve",
            "preflight stored JSON UTF-8 size including comma/colon separator spaces",
            "20,000 preferred", "write any complete valid <=24,000 state once",
            "not a gate",
            "Preserve the semantics", "not its wording or item count",
            "Collapse completed, duplicate, superseded, resolved, finalized work",
            "receipt identifier",
            "codex_continuity_payload_too_large", "same fixed binding and idempotency key",
            "do not issue further oversize retries",
            "Continuity verified: checkpoint v<version> · window <source_window_number>.",
            "Do not claim success before readback",
            "Continuity degraded: checkpoint repair failed (<specific reason>); native history remains available.",
            "Never hide a degraded repair",
            "perform one fresh codex-read-recovery",
            "complete checkpoint.state as the authoritative starting point",
            "orientation only and may omit whole items",
            "never treat an omitted item as absent",
            "If the complete fresh checkpoint.state is unavailable, do not write",
            "do not issue a knowingly stale write",
        ):
            self.assertIn(expected, context)
        self.assertNotIn("never-store-opaque-compact-body", context)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

        same_env, _ = self.install_fake_record_call(self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"), "source_window_number": 1,
        }))
        same = json.loads(self.run_hook(payload, same_env).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", same)
        self.assertIn("checkpoint covers current context window 1", same)
        self.assertIn("later native bytes or turns may remain unincorporated", same)

    def test_compact_session_repairs_from_bounded_tail_of_sparse_large_rollout(self):
        self.native_rollout()
        mib = 1024 * 1024
        with self.rollout.open("r+b") as handle:
            # Sparse one-MiB rows keep the physical fixture small while making
            # the logical transcript larger than the bounded 64-MiB tail.
            for offset in range(20 * mib, 71 * mib, mib):
                handle.seek(offset)
                handle.write(b"\n")
            handle.seek(0, os.SEEK_END)
            opaque = "never-surface-large-rollout-opaque-body"
            # A complete ordinary row can itself be larger than the former
            # per-row cap and can quote the candidate word inside its payload.
            # Only its authenticated depth-one type may classify the row.
            handle.write((json.dumps({
                "timestamp": "2026-09-05T12:00:06Z", "ordinal": 6,
                "type": "event_msg",
                "payload": {"message": f'payload says "compacted" {"x" * (3 * mib)}'},
            }, separators=(",", ":")) + "\n").encode())
            row_seven = compacted_row(7, "window-before-7", "window-seven")
            handle.write((json.dumps(row_seven, separators=(",", ":")) + "\n").encode())
            # The authenticated compacted row also exceeds the former cap.
            # Its opaque replacement body is streamed past while the bounded
            # outer metadata at the end remains available for verification.
            row_eight = compacted_row(8, "window-seven", "window-eight")
            row_eight["payload"]["replacement_history"] = [
                opaque + ("z" * (3 * mib))]
            for row in (row_eight,):
                handle.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
        self.assertGreater(self.rollout.stat().st_size, 64 * mib)
        self.assertGreater(20 * mib - (self.rollout.stat().st_size - 64 * mib),
                           2 * mib)
        response = self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-seven"),
            "source_window_number": 7,
        })
        env, _ = self.install_fake_record_call(response)

        result = self.run_hook(self.hook_payload(source="compact"), env)

        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertIn(f'"source_window_id":"{window_id("window-eight")}"', context)
        self.assertIn('"source_window_number":8', context)
        self.assertNotIn(opaque, context)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

    def test_checkpoint_refresh_bootstraps_resume_and_user_prompt_after_compaction(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        response = self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-initial"), "source_window_number": 0,
        })
        env, _ = self.install_fake_record_call(response)
        compact = json.loads(self.run_hook(
            self.hook_payload(source="compact"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", compact)
        for source in ("startup", "resume"):
            context = json.loads(self.run_hook(
                self.hook_payload(source=source), env).stdout)[
                    "hookSpecificOutput"]["additionalContext"]
            self.assertEqual("COMPACTION CHECKPOINT REPAIR" in context,
                             source == "resume", source)
            if source == "resume":
                self.assertIn('"source":"resume"', context)
        prompt = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-bootstrap"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", prompt)
        self.assertIn('"source":"user_prompt_submit"', prompt)
        self.assertIn('"turn_id":"turn-bootstrap"', prompt)
        self.assertIn("CARR Codex recovery checkpoint", prompt)

    def test_repair_keys_distinguish_newer_user_turns_in_same_window(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        env, _ = self.install_fake_record_call(self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-initial"), "source_window_number": 0,
        }))

        first = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-first"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        second = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-newer"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]

        def repair_key(context):
            directive = context.split("\n\nCARR Codex recovery checkpoint.", 1)[0]
            marker = '"idempotency_key":"'
            start = directive.index(marker) + len(marker)
            return directive[start:directive.index('"', start)]

        self.assertNotEqual(repair_key(first), repair_key(second),
                            "a newer user turn must never reuse an earlier repair key")

    def test_fresh_window_zero_never_emits_compaction_repair(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "fresh"}})
        env, _ = self.install_fake_record_call(
            {"ok": True, "found": False, "checkpoint": None})
        for payload in (
            self.hook_payload(source="resume"),
            self.hook_payload("UserPromptSubmit", turn_id="turn-fresh"),
        ):
            context = json.loads(self.run_hook(payload, env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("COMPACTION CHECKPOINT REPAIR", context)

    def test_user_prompt_repairs_legacy_checkpoint_but_exact_window_is_noop(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        legacy_env, _ = self.install_fake_record_call(
            self.checkpoint(cursor={"byte_offset": 1, "source_digest": "0" * 64}))
        legacy = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-legacy"), legacy_env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", legacy)
        exact_env, _ = self.install_fake_record_call(self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"), "source_window_number": 1,
        }))
        exact = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-exact"), exact_env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", exact)
        self.assertNotIn("Continuity verified:", exact)
        self.assertNotIn("Continuity degraded:", exact)
        self.assertIn("covers current context window 1", exact)
        self.assertIn("later native bytes or turns may remain unincorporated", exact)
        self.assertIn("no compaction repair is required until the next native compaction", exact)

    def test_exact_checkpoint_with_placeholder_refs_requests_repair(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        state = {
            "objective": "keep the active task",
            "latest_corrections": [{"text": "preserve correction",
                                     "refs": ["doctrine-section:{REF1}"]}],
            "decisions": [{"text": "preserve decision", "why": "still binding",
                           "refs": ["native-user-turn:{REF2}"]}],
            "next_action": "repair references before accepting the checkpoint",
        }
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["storage_contract"] = {
            "version": "codex-continuity-storage.v2", "semantic_state_max_bytes": 24000,
            "reference_manifest_max_bytes": 128000, "recovery_state": "full_logical_state",
        }
        env, _ = self.install_fake_record_call(response)

        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertIn("condensed checkpoint state contains unresolved reference placeholders",
                      context)
        self.assertIn("canonical source records remain authoritative and available", context)
        self.assertNotIn("{REF1}", context)
        self.assertNotIn("{REF2}", context)

    def test_user_prompt_placeholder_repair_has_no_healthy_noop_message(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        state = {
            "objective": "keep the active task through {REF3}",
            "latest_corrections": [{"text": "preserve correction",
                                     "refs": ["user:valid"]}],
            "next_action": "repair the unresolved placeholder",
        }
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["storage_contract"] = {
            "version": "codex-continuity-storage.v2", "semantic_state_max_bytes": 24000,
            "reference_manifest_max_bytes": 128000, "recovery_state": "full_logical_state",
        }
        env, _ = self.install_fake_record_call(response)

        context = json.loads(self.run_hook(self.hook_payload(
            "UserPromptSubmit", turn_id="turn-placeholder"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertIn("checkpoint state requires repair now", context)
        self.assertNotIn("no compaction repair is required", context)
        self.assertNotIn("{REF3}", context)

    def test_user_prompt_malformed_legacy_state_warns_without_crashing(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        response = self.checkpoint(cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["checkpoint"]["state"] = None
        env, _ = self.install_fake_record_call(response)

        result = self.run_hook(self.hook_payload(
            "UserPromptSubmit", turn_id="turn-malformed"), env)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("complete replacement state is unavailable", context)
        self.assertIn("checkpoint state requires repair now", context)
        self.assertNotIn("no compaction repair is required", context)

    def test_exact_checkpoint_with_valid_path_and_url_refs_stays_healthy(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        state = {
            "objective": "keep the active task",
            "latest_corrections": [{
                "text": "preserve path citation",
                "refs": ["/tmp/canonical-evidence.json"],
            }],
            "decisions": [{
                "text": "preserve URL citation",
                "why": "still binding",
                "refs": ["https://github.com/jbookout/carr-system/pull/897"],
            }],
            "next_action": "continue without repairing valid references",
        }
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["storage_contract"] = {
            "version": "codex-continuity-storage.v2", "semantic_state_max_bytes": 24000,
            "reference_manifest_max_bytes": 128000, "recovery_state": "full_logical_state",
        }
        env, _ = self.install_fake_record_call(response)

        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertIn("checkpoint covers current context window 1", context)

    def test_repair_directive_requires_one_pass_size_plan(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        refs = (
            [f"approval:current:{index}:live-contract" for index in range(32)]
            + ["decision:uncertain:requires-review"]
            + [f"receipt:completed:{index}:delivery-evidence" for index in range(68)]
            + ["receipt:completed:{REF1}:delivery-evidence"])
        state = {
            "objective": "keep the active task",
            "latest_corrections": [
                {"text": "current correction", "refs": ["approval:current:0:live-contract"]}
                for _ in range(4)
            ] + [{"text": "mixed reference manifest with readable words", "refs": refs}],
            "progress": [{"text": "completed delivery detail"}],
            "receipts": [{"text": "completed receipt detail"}],
            "artifacts": [{"text": "filler"} for _ in range(6)],
            "next_action": "normalize once before writing",
        }
        base_bytes = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        remaining = 23473 - base_bytes
        for artifact in state["artifacts"]:
            addition = min(3900 - len(artifact["text"]), remaining)
            artifact["text"] += "z" * addition
            remaining -= addition
        self.assertEqual(remaining, 0)
        state_bytes = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(state_bytes, 23473)
        self.assertEqual(len(refs), 102)
        self.assertLessEqual(state_bytes, 24000)
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["storage_contract"] = {
            "version": "codex-continuity-storage.v2", "semantic_state_max_bytes": 24000,
            "reference_manifest_max_bytes": 128000, "recovery_state": "full_logical_state",
        }
        env, _ = self.install_fake_record_call(response)
        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("one semantic compression pass", context)
        self.assertIn("<=18,000-byte JSON budget", context)
        self.assertIn("JSON budget for semantic state", context)
        self.assertIn("2,000-byte reserve", context)
        self.assertIn("at most one corrective pass", context)
        self.assertIn("Reference manifest: copy valid refs byte-for-byte", context)
        self.assertIn("Keep current/uncertain refs direct", context)
        self.assertIn("Before compression, classify refs/evidence", context)
        self.assertLess(context.index("Before compression, classify refs/evidence"),
                        context.index("After classification/archive, one semantic compression pass"))
        self.assertIn("not field, age, or size", context)
        self.assertIn("completed/superseded", context)
        self.assertIn("archive only completed/superseded progress/receipts/artifacts/refs", context)
        self.assertIn("Reuse pointer", context)
        self.assertIn("Keep readable word boundaries; never strip spaces/alter refs", context)
        self.assertIn("stored JSON UTF-8 size including comma/colon separator spaces", context)
        self.assertIn("excluding the separate 128,000-byte reference manifest", context)
        self.assertIn("report semantic bytes, manifest bytes, and ref count", context)
        self.assertIn("storage bytes/ref count/what cannot archive", context)
        self.assertIn("historical:true", context)
        self.assertIn("expected_digest", context)
        self.assertIn("archive_ref `codex-revision:<checkpoint UUID>:<version>:sha256:<64hex>`", context)
        self.assertIn("archive only completed/superseded", context)
        self.assertIn("Failure: retain and report", context)
        self.assertIn("The full replacement state requires nonempty objective and next_action", context)
        self.assertIn("CARR_MCP_CLIENT_PROFILE=codex-continuity", context)
        self.assertIn("codex_native_principal_required", context)
        self.assertIn("If its version differs from expected_version", context)
        self.assertIn("codex_checkpoint_version_conflict", context)
        self.assertIn("same fixed binding and idempotency key", context)
        directive = context.split("\n\nCARR Codex recovery checkpoint.", 1)[0]
        self.assertLessEqual(len(directive.encode("utf-8")), 6000)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

    def test_repair_directive_keeps_current_approval_refs_within_bound(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        state = {
            "objective": "keep the active task",
            "acceptance": [{"text": "approved rollout " + "z" * 3000,
                            "refs": ["approval:current"]} for _ in range(7)],
            "latest_corrections": [{"text": "repair this reference",
                                    "refs": ["legacy:{REF1}"]}],
            "next_action": "preserve approval while repairing",
        }
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["storage_contract"] = {
            "version": "codex-continuity-storage.v2", "semantic_state_max_bytes": 24000,
            "reference_manifest_max_bytes": 128000, "recovery_state": "full_logical_state",
        }
        env, _ = self.install_fake_record_call(response)
        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        directive = context.split("\n\nCARR Codex recovery checkpoint.", 1)[0]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", directive)
        self.assertIn("current approval text/refs stay directly in state",
                      directive)
        self.assertIn("Current/uncertain correction/decision refs stay direct", directive)
        self.assertIn("direct in the full logical state", directive)
        self.assertIn("Keep readable word boundaries", directive)
        self.assertIn("CARR_MCP_CLIENT_PROFILE=codex-continuity", directive)
        self.assertIn("same fixed binding and idempotency key", directive)
        self.assertLessEqual(len(directive.encode("utf-8")), 6000)

    def test_v2_reference_manifest_keeps_large_logical_refs_bounded(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        refs = [f"current:{index:04d}:" + "x" * 90 for index in range(1000)]
        state = {
            "objective": "preserve all current references",
            "latest_corrections": [{"text": "current evidence", "refs": refs}],
            "progress": [{"text": "x" * 3900}, {"text": "x" * 3900},
                         {"text": "x" * 2800}],
            "next_action": "read back exact current references",
        }
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        response["checkpoint"].update({
            "storage_contract": {
                "version": "codex-continuity-storage.v2",
                "semantic_state_max_bytes": 24000,
                "reference_manifest_max_bytes": 128000,
                "recovery_state": "full_logical_state",
            },
            "reference_manifest_summary": {
                "manifest_present": True,
                "reference_count": 1000,
                "stored_state_bytes": 11000,
                "stored_reference_manifest_bytes": 110000,
                "planned_semantic_state_bytes": 11000,
                "planned_reference_manifest_bytes": 110000,
            },
        })
        self.assertTrue(load_hook_module()._complete_checkpoint_state(response["checkpoint"]))
        env, _ = self.install_fake_record_call(response)
        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

    def test_storage_contract_gates_reference_manifest_repair_budget(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        state = {
            "objective": "repair the unresolved reference",
            "latest_corrections": [{"text": "repair", "refs": ["legacy:{REF1}"]}],
            "next_action": "write the repaired checkpoint",
        }
        cursor = {"byte_offset": 1, "source_digest": "0" * 64,
                  "source_window_id": window_id("window-current"),
                  "source_window_number": 1}
        legacy, _ = self.install_fake_record_call(self.checkpoint(state=state, cursor=cursor))
        legacy_context = json.loads(self.run_hook(self.hook_payload(source="resume"), legacy).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("excluding the separate 128,000-byte reference manifest", legacy_context)

        capable_response = self.checkpoint(state=state, cursor=cursor)
        capable_response["storage_contract"] = {
            "version": "codex-continuity-storage.v2", "semantic_state_max_bytes": 24000,
            "reference_manifest_max_bytes": 128000, "recovery_state": "full_logical_state",
        }
        capable, _ = self.install_fake_record_call(capable_response)
        capable_context = json.loads(self.run_hook(self.hook_payload(source="resume"), capable).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("excluding the separate 128,000-byte reference manifest", capable_context)

        missing = {"ok": True, "found": False, "checkpoint": None,
                   "storage_contract": capable_response["storage_contract"]}
        missing_env, _ = self.install_fake_record_call(missing)
        missing_context = json.loads(self.run_hook(self.hook_payload(source="resume"), missing_env).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("excluding the separate 128,000-byte reference manifest", missing_context)

    def test_exact_window_target_excess_is_healthy_until_next_compaction(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        # Every item stays inside the per-item text limit so the fixture is a
        # genuinely complete state: the no-op must come from the 24,000-byte
        # server limit, never from an incomplete-state warning.
        state = {
            "objective": "keep the active task",
            "constraints": [{"text": "z" * 3900} for _ in range(5)] + [{"text": ""}],
            "next_action": "continue without target-only repair",
        }
        base_bytes = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        state["constraints"][-1]["text"] = "z" * (20143 - base_bytes)
        state_bytes = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(state_bytes, 20143)
        self.assertGreater(state_bytes, 20000)
        self.assertLessEqual(state_bytes, 24000)
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        self.assertTrue(
            load_hook_module()._complete_checkpoint_state(response["checkpoint"]),
            "the healthy-path fixture must itself be a complete checkpoint state")
        env, _ = self.install_fake_record_call(response)

        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertNotIn("COMPACTION CHECKPOINT WARNING", context)
        self.assertIn("checkpoint covers current context window 1", context)
        self.assertNotIn("bounded repair directive exceeded its output limit", context)

    def test_placeholder_repair_above_target_never_cites_the_soft_target(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        # A complete 21,000-byte state whose only defect is an unresolved
        # reference.  Being above the 20,000-byte soft target is not itself a
        # repair reason, so the directive must not order a normalization pass.
        state = {
            "objective": "keep the active task",
            "latest_corrections": [{"text": "repair this reference",
                                    "refs": ["legacy:{REF1}"]}],
            "constraints": [{"text": "z" * 3900} for _ in range(5)] + [{"text": ""}],
            "next_action": "repair the unresolved reference before writing",
        }
        base_bytes = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        state["constraints"][-1]["text"] = "z" * (21000 - base_bytes)
        state_bytes = len(json.dumps(
            state, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(state_bytes, 21000)
        self.assertGreater(state_bytes, 20000)
        self.assertLessEqual(state_bytes, 24000)
        response = self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        })
        self.assertTrue(
            load_hook_module()._complete_checkpoint_state(response["checkpoint"]),
            "the fixture must itself be a complete checkpoint state")
        env, _ = self.install_fake_record_call(response)

        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertIn("unresolved reference placeholders", context)
        self.assertNotIn("normalization target", context)
        self.assertNotIn("normalize it now", context)
        self.assertNotIn("{REF1}", context)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

    def test_oversized_legacy_reference_list_still_emits_bounded_repair_directive(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        state = {
            "objective": "keep the active task",
            "latest_corrections": [{
                "text": "preserve correction",
                "refs": ["legacy:{REF1}"] * 10,
            }
                for _ in range(100)],
            "next_action": "repair unresolved references before writing",
        }
        env, _ = self.install_fake_record_call(self.checkpoint(state=state, cursor={
            "byte_offset": 1, "source_digest": "0" * 64,
            "source_window_id": window_id("window-current"),
            "source_window_number": 1,
        }))

        context = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", context)
        self.assertIn("additional unresolved reference locations omitted", context)
        self.assertNotIn("bounded repair directive exceeded its output limit", context)
        directive = context.split("\n\nCARR Codex recovery checkpoint.", 1)[0]
        self.assertLessEqual(len(directive.encode("utf-8")), 6000)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

    def test_user_prompt_reports_rejected_receipt_without_claiming_outage(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "receipt"}})
        env, _ = self.install_fake_record_call(
            self.checkpoint(cursor={"byte_offset": 1}),
            event_response={"error": "codex_event_key_conflict"},
            event_tool_error=True)
        context = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-rejected"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("was rejected by the record store (codex_event_key_conflict)", context)
        self.assertNotIn("receipt could not be stored because the record store is unavailable", context)

    def test_legacy_desktop_roots_require_an_array_of_absolute_strings(self):
        hook = load_hook_module()
        meta = {"runtime": "codex", "native_task_id": self.session_id,
                "project_id": "git:canonical", "cwd": str(self.project)}
        for roots in ("/unrelated/project", {"/": True}, [],
                      [str(self.base / "unrelated")], ["relative"],
                      [str(self.project), 42], [str(self.project), ""]):
            with self.subTest(roots=roots):
                self.desktop_assignment(roots)
                with mock.patch.dict(os.environ, self.env):
                    self.assertIsNone(hook._legacy_desktop_project(meta))
        for roots in ([str(self.project)], [str(self.base)],
                      [str(self.base / "unrelated"), str(self.project)]):
            with self.subTest(roots=roots):
                project_id = self.desktop_assignment(roots)
                with mock.patch.dict(os.environ, self.env):
                    self.assertEqual(hook._legacy_desktop_project(meta), project_id)

    def test_alternate_recovery_failures_survive_the_real_wrapper(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "recover"}})
        hook = load_hook_module()
        project_id = self.desktop_assignment()
        with mock.patch.dict(os.environ, self.env):
            meta = hook.HISTORY.validate_native_rollout(self.hook_payload())
        canonical = meta["project_id"]
        conflict = {"error": "codex_recovery_binding_conflict"}
        for alternate, status, error in (
            ({"response": {"ok": True}, "delay": 1}, "unavailable", None),
            ({"response": {"error": "codex_continuity_owner_required"},
              "tool_error": True}, "rejected", "codex_continuity_owner_required"),
        ):
            with self.subTest(status=status):
                env, log = self.install_fake_record_call(binding_responses={
                    canonical: {"codex-read-recovery": {
                        "response": conflict, "tool_error": True}},
                    project_id: {"codex-read-recovery": alternate},
                })
                with mock.patch.dict(os.environ, env):
                    result = hook.read_recovery(meta, deadline=time.monotonic() + 0.5)
                self.assertEqual(result["status"], status)
                self.assertEqual(result.get("error"), error)
                self.assertEqual(result["binding_conflict"],
                                 {"status": "rejected", "response": conflict,
                                  "error": conflict["error"]})
                self.assertEqual(meta["project_id"], canonical)
                self.assertEqual([json.loads(line)["args"]["project_id"]
                                  for line in log.read_text().splitlines()],
                                 [canonical, project_id])
                output = io.StringIO()
                with mock.patch.dict(os.environ, env), \
                     mock.patch.object(hook, "EVENT_DEADLINE_SECONDS", 0.5), \
                     mock.patch.object(sys, "stdin", io.StringIO(json.dumps(
                         self.hook_payload("UserPromptSubmit", turn_id="turn-alternate")))), \
                     contextlib.redirect_stdout(output), \
                     contextlib.redirect_stderr(io.StringIO()):
                    hook.main()
                context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
                self.assertIn("record store is unavailable" if status == "unavailable"
                              else f"checkpoint recovery ({error})", context)
                self.assertNotIn("checkpoint recovery (codex_recovery_binding_conflict)", context)

    def test_postcompact_slow_store_keeps_the_canonical_receipt_budget(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        env, log = self.install_fake_record_call()
        env["FAKE_CALL_DELAY"] = "5"
        result = self.run_hook(self.hook_payload(
            "PostCompact", turn_id="turn-slow-store", trigger="auto"), env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("TimeoutExpired", result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([call["verb"] for call in calls], ["codex-record-event"])
        self.assertEqual(calls[0]["args"]["cursor"]["source_window_number"], 1)

    def test_postcompact_large_rollout_is_scanned_once_before_the_receipt(self):
        self.native_rollout()
        row = json.dumps({"type": "event_msg", "payload": {"message": "x" * 1000}}) + "\n"
        with self.rollout.open("a") as handle:
            for _ in range(45000):
                handle.write(row)
        self.append_rollout(compacted_row(1, "window-initial", "window-current"))
        hook = load_hook_module()
        self.assertLess(self.rollout.stat().st_size, hook.HISTORY.MAX_COMPACTION_SCAN_BYTES)
        env, log = self.install_fake_record_call()
        env["FAKE_CALL_DELAY"] = "1.5"
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(sys, "stdin", io.StringIO(json.dumps(self.hook_payload(
                 "PostCompact", turn_id="turn-large-rollout", trigger="auto")))), \
             mock.patch.object(hook.HISTORY, "compaction_occurrence",
                               wraps=hook.HISTORY.compaction_occurrence) as scan, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            hook.main()
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("TimeoutExpired", errors.getvalue())
        self.assertEqual(scan.call_count, 1)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([call["verb"] for call in calls], ["codex-record-event"])
        self.assertEqual(calls[0]["args"]["cursor"]["source_window_number"], 1)

    def test_postcompact_retries_a_legacy_binding_only_with_checkpoint_proof(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        hook = load_hook_module()
        project_id = self.desktop_assignment()
        with mock.patch.dict(os.environ, self.env):
            canonical = hook.HISTORY.validate_native_rollout(self.hook_payload())["project_id"]
        for found in (True, False):
            with self.subTest(found=found):
                env, log = self.install_fake_record_call(binding_responses={
                    canonical: {
                        "codex-record-event": {"tool_error": True, "response": {
                            "error": "codex_event_binding_conflict"}},
                        "codex-read-recovery": {"tool_error": True, "response": {
                            "error": "codex_recovery_binding_conflict"}},
                    },
                    project_id: {"codex-read-recovery": {
                        "response": self.checkpoint() if found else {
                            "ok": True, "found": False, "checkpoint": None}}},
                })
                result = self.run_hook(self.hook_payload(
                    "PostCompact", turn_id="turn-legacy-post", trigger="auto"), env)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                expected = ["codex-record-event", "codex-read-recovery", "codex-read-recovery"]
                self.assertEqual([call["verb"] for call in calls],
                                 expected + (["codex-record-event"] if found else []))
                if found:
                    self.assertEqual(calls[-1]["args"]["project_id"], project_id)
                    self.assertEqual(calls[0]["args"]["cursor"], calls[-1]["args"]["cursor"])
                    self.assertNotEqual(calls[0]["args"]["idempotency_key"],
                                        calls[-1]["args"]["idempotency_key"])

    def test_legacy_desktop_project_recovers_only_verified_existing_checkpoint(self):
        hook = load_hook_module()
        project_id = self.desktop_assignment()
        meta = {"runtime": "codex", "native_task_id": self.session_id,
                "project_id": "git:canonical", "cwd": str(self.project)}
        calls = []

        def record_call(_verb, args, **_kwargs):
            calls.append(args["project_id"])
            if args["project_id"] == "git:canonical":
                return {"status": "rejected", "error": "codex_recovery_binding_conflict"}
            return {"status": "ok", "response": {"ok": True, "found": True,
                                               "checkpoint": {"checkpoint_version": 2}}}

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), \
             mock.patch.object(hook, "call_verb", side_effect=record_call):
            result = hook.read_recovery(meta)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(meta["project_id"], project_id)
        self.assertEqual(calls, ["git:canonical", project_id])

        meta["project_id"] = "git:canonical"
        def missing_call(_verb, args, **_kwargs):
            if args["project_id"] == "git:canonical":
                return {"status": "rejected", "error": "codex_recovery_binding_conflict"}
            return {"status": "ok", "response": {"ok": True, "found": False}}
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}), \
             mock.patch.object(hook, "call_verb", side_effect=missing_call):
            result = hook.read_recovery(meta)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(meta["project_id"], "git:canonical")

    def test_sanctioned_tool_error_rejection_is_not_reported_as_outage(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        response = {"error": "codex_recovery_binding_conflict"}
        env, _ = self.install_fake_record_call(
            response, recovery_tool_error=True)

        startup = json.loads(self.run_hook(
            self.hook_payload(source="resume"), env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
        self.assertIn("checkpoint recovery rejected (codex_recovery_binding_conflict)",
                      startup)
        self.assertIn("Continuity degraded: checkpoint recovery rejected (codex_recovery_binding_conflict)",
                      startup)
        self.assertNotIn("record store is unavailable", startup)

        prompt = json.loads(self.run_hook(
            self.hook_payload("UserPromptSubmit", turn_id="turn-recovery-rejected"),
            env).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("repair could not start because recovery was rejected (codex_recovery_binding_conflict)",
                      prompt)
        self.assertIn("checkpoint recovery (codex_recovery_binding_conflict)", prompt)
        self.assertNotIn("record store is unavailable", prompt)

    def test_compact_refresh_refuses_blind_write_paths(self):
        self.native_rollout(compacted_row(1, "window-initial", "window-current"))
        payload = self.hook_payload(source="compact")

        outage_env, _ = self.install_fake_record_call(outage=True)
        outage = json.loads(self.run_hook(payload, outage_env).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", outage)
        self.assertIn("record store is unavailable", outage)

        incomplete_env, _ = self.install_fake_record_call(
            self.checkpoint(state={"objective": "missing next action"}, cursor={
                "source_window_id": window_id("window-initial"),
                "source_window_number": 0}))
        incomplete = json.loads(self.run_hook(payload, incomplete_env).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", incomplete)
        self.assertIn("complete replacement state is unavailable", incomplete)

        for cursor in (
            {"source_window_id": "window-future", "source_window_number": 2},
            {"source_window_id": "window-conflict", "source_window_number": 1},
        ):
            unknown_env, _ = self.install_fake_record_call(
                self.checkpoint(cursor=cursor))
            unknown = json.loads(self.run_hook(payload, unknown_env).stdout)[
                "hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("COMPACTION CHECKPOINT REPAIR", unknown)
            self.assertIn("ahead, malformed, or conflicting", unknown)

        missing_env, _ = self.install_fake_record_call(
            {"ok": True, "found": False, "checkpoint": None})
        missing = json.loads(self.run_hook(payload, missing_env).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("COMPACTION CHECKPOINT REPAIR", missing)
        self.assertIn('"expected_version":0', missing)
        self.assertIn("If a complete replacement state cannot be assembled", missing)
        self.assertIn("warn and continue without writing", missing)

        self.native_rollout({"type": "event_msg", "payload": {"message": "no boundary"}})
        invalid_env, _ = self.install_fake_record_call(response=self.checkpoint())
        invalid = json.loads(self.run_hook(payload, invalid_env).stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("COMPACTION CHECKPOINT REPAIR", invalid)
        self.assertIn("trusted compact window marker is unavailable", invalid)

    def test_window_freshness_is_semantic_when_current_marker_is_trusted(self):
        hook = load_hook_module()
        highwater = {"byte_offset": 900, "device": 1, "inode": 2,
                     "source_digest": "a" * 64}
        current = {"source_window_id": "window-current", "source_window_number": 2}
        exact = self.checkpoint(cursor={"byte_offset": 1,
                                        "source_window_id": "window-current",
                                        "source_window_number": 2})["checkpoint"]
        behind = self.checkpoint(cursor={"byte_offset": 1,
                                         "source_window_id": "window-old",
                                         "source_window_number": 1})["checkpoint"]
        ahead = self.checkpoint(cursor={"byte_offset": 9999,
                                        "source_window_id": "window-future",
                                        "source_window_number": 3})["checkpoint"]
        malformed = self.checkpoint(cursor={"byte_offset": 1,
                                            "source_window_id": "window-other",
                                            "source_window_number": 2})["checkpoint"]
        self.assertIn("covers current context window 2",
                      hook.checkpoint_freshness(exact, highwater, current))
        self.assertIn("semantically stale",
                      hook.checkpoint_freshness(behind, highwater, current))
        self.assertIn("freshness is unknown",
                      hook.checkpoint_freshness(ahead, highwater, current))
        self.assertIn("freshness is unknown",
                      hook.checkpoint_freshness(malformed, highwater, current))
        self.assertIn("checkpoint is stale",
                      hook.checkpoint_freshness(behind, highwater))

    def test_session_start_native_payload_delivers_prioritized_bounded_recovery(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        env, log = self.install_fake_record_call(
            self.checkpoint(cursor={"byte_offset": 1, "source_digest": "old"}))
        for source in ("startup", "resume", "compact"):
            result = self.run_hook(self.hook_payload(source=source), env)
            self.assertEqual(result.returncode, 0, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            for expected in ("keep one task effective", "use native payloads",
                             "rejected approach", "fabricated schema",
                             "verify the actual hook contract", "checkpoint version: 7",
                             "checkpoint is stale"):
                self.assertIn(expected, context)
            self.assertLessEqual(len(context.encode("utf-8")), 12000)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(call["args"]["native_task_id"] == self.session_id
                            for call in calls))
        self.assertTrue(all("project_id" in call["args"] for call in calls))

    def test_ten_recovery_cycles_keep_corrections_reason_next_action_and_task_id(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "cycle"}})
        env, log = self.install_fake_record_call(self.checkpoint())
        contexts = [json.loads(self.run_hook(
            self.hook_payload(source="compact"), env).stdout)["hookSpecificOutput"]["additionalContext"]
                    for _ in range(10)]
        self.assertEqual(len(contexts), 10)
        for expected in ("use native payloads", "fabricated schema",
                         "verify the actual hook contract"):
            self.assertTrue(all(expected in context for context in contexts))
        self.assertTrue(all("never auto-reexecute" in context for context in contexts))
        self.assertTrue(all(len(context.encode("utf-8")) <= 12000 for context in contexts))
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual({call["args"]["native_task_id"] for call in calls},
                         {self.session_id})

    def test_large_recovery_omits_whole_items_with_warning_and_pointer(self):
        self.native_rollout(
            compacted_row(1, "window-initial", "window-current"),
            {"type": "event_msg", "payload": {"message": "latest"}})
        state = {"objective": "objective",
                 "latest_corrections": [{"text": "latest correction",
                                          "refs": ["rollout:2"]}],
                 "constraints": [{"text": f"constraint-{i}-" + "z" * 3000,
                                 "refs": ["rollout:3"]}
                                 for i in range(6)],
                 "next_action": "next action"}
        env, _ = self.install_fake_record_call(self.checkpoint(
            state=state, cursor={"byte_offset": 1, "source_digest": "0" * 64,
                                 "source_window_id": window_id("window-initial"),
                                 "source_window_number": 0}))
        context = json.loads(self.run_hook(
            self.hook_payload(source="compact"), env).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context.encode("utf-8")), 12000)
        for expected in ("latest correction", "next action", "coverage warning",
                         "codex-history.py search", "COMPACTION CHECKPOINT REPAIR",
                         "complete checkpoint.state as the authoritative starting point",
                         "orientation only and may omit whole items",
                         "never treat an omitted item as absent",
                         "If the complete fresh checkpoint.state is unavailable, do not write"):
            self.assertIn(expected, context)
        self.assertNotIn('"text":"constraint-3-', context)

    def test_missing_and_outage_are_distinct(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        missing_env, _ = self.install_fake_record_call(
            {"ok": True, "found": False, "checkpoint": None})
        missing = json.loads(self.run_hook(
            self.hook_payload(source="startup"), missing_env).stdout)
        self.assertIn("no durable checkpoint was found",
                      missing["hookSpecificOutput"]["additionalContext"].lower())
        outage_env, _ = self.install_fake_record_call(outage=True)
        outage = json.loads(self.run_hook(
            self.hook_payload(source="startup"), outage_env).stdout)
        self.assertIn("record store is unavailable",
                      outage["hookSpecificOutput"]["additionalContext"].lower())

    def test_session_start_exposes_bounded_backend_pending_turn_references(self):
        self.native_rollout({"type": "event_msg", "payload": {
            "type": "user_message", "message": "latest"}})
        response = self.checkpoint(cursor=None)
        response.update({
            "source_highwater": {"byte_offset": 123, "checkpoint_version": 7,
                                 "turn_id": "turn-25", "source_digest": "a" * 64,
                                 "password": "never-render-source-secret"},
            "source_coverage": "unknown",
            "unincorporated_user_turns": [
                {"event_type": "user_prompt_submit",
                 "cursor": {"byte_offset": index + 10,
                            "checkpoint_version": 7,
                            "turn_id": f"turn-{index}",
                            "source_digest": f"{index:064x}",
                            **({"access_token": "never-render-event-secret"}
                               if index == 0 else {})},
                 "transcript_ref": str(self.rollout.resolve()),
                 "created_at": "2026-09-05T12:00:00Z"}
                for index in range(25)],
            "unincorporated_user_turns_omitted": 4,
        })
        env, _ = self.install_fake_record_call(response)
        result = self.run_hook(self.hook_payload(source="compact"), env)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("recorded source highwater", context)
        self.assertIn('"turn_id":"turn-0"', context)
        self.assertIn(str(self.rollout.resolve()), context)
        self.assertIn("source coverage is unknown", context)
        self.assertIn("unsupported fields", context)
        self.assertIn("omitted 4 older unincorporated user turns", context)
        self.assertIn("all bounded pending user turns above remain unincorporated", context)
        self.assertNotIn("never-render", context)
        self.assertLessEqual(len(context.encode("utf-8")), 12000)

    def test_precompact_records_observed_checkpoint_version_before_compaction(self):
        self.native_rollout({"type": "event_msg", "payload": {
            "type": "user_message", "message": "compact me"}})
        cases = [
            (self.checkpoint(cursor={"byte_offset": 1}), False, 7, "available"),
            ({"ok": True, "found": False, "checkpoint": None,
              "source_highwater": None, "unincorporated_user_turns": [],
              "unincorporated_user_turns_omitted": 0,
              "source_coverage": "known"}, False, 0, "missing"),
            (None, True, None, "unavailable"),
        ]
        for index, (response, outage, version, status) in enumerate(cases):
            with self.subTest(status=status):
                env, log = self.install_fake_record_call(response, outage=outage)
                result = self.run_hook(self.hook_payload(
                    "PreCompact", turn_id=f"turn-{index}", trigger="auto"), env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                calls = [json.loads(line) for line in
                         log.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([call["verb"] for call in calls],
                                 ["codex-read-recovery", "codex-record-event"])
                cursor = calls[1]["args"]["cursor"]
                self.assertEqual(cursor["checkpoint_version"], version)
                self.assertEqual(cursor["checkpoint_status"], status)
                self.assertEqual(cursor["turn_id"], f"turn-{index}")
                self.assertEqual(cursor["trigger"], "auto")

    def test_slow_store_calls_share_one_deadline_and_still_attempt_event_write(self):
        self.native_rollout({"type": "event_msg", "payload": {
            "type": "user_message", "message": "deadline fixture"}})
        hook = load_hook_module()
        hook.EVENT_DEADLINE_SECONDS = 0.5
        self.assertLess(hook.EVENT_DEADLINE_SECONDS, 10)
        cases = [
            self.hook_payload("UserPromptSubmit", turn_id="turn-deadline"),
            self.hook_payload("PreCompact", turn_id="turn-deadline",
                              trigger="auto"),
        ]
        for payload in cases:
            with self.subTest(event=payload["hook_event_name"]):
                env, log = self.install_fake_record_call(
                    self.checkpoint(cursor={"byte_offset": 1}))
                env["FAKE_CALL_DELAY"] = "1"
                stdout, stderr = io.StringIO(), io.StringIO()
                prior_stdin = sys.stdin
                started = time.monotonic()
                try:
                    with mock.patch.dict(os.environ, env, clear=False), \
                            contextlib.redirect_stdout(stdout), \
                            contextlib.redirect_stderr(stderr):
                        sys.stdin = io.StringIO(json.dumps(payload))
                        self.assertEqual(hook.main(), 0)
                finally:
                    sys.stdin = prior_stdin
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 1.0)
                calls = [json.loads(line) for line in
                         log.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([call["verb"] for call in calls],
                                 ["codex-read-recovery", "codex-record-event"])
                self.assertIn("record store unavailable", stderr.getvalue())
                if payload["hook_event_name"] == "UserPromptSubmit":
                    context = json.loads(stdout.getvalue())[
                        "hookSpecificOutput"]["additionalContext"]
                    self.assertIn("checkpoint presence and version are unknown",
                                  context)
                    self.assertIn("receipt could not be stored; its status is unknown",
                                  context)
                    self.assertNotIn("checkpoint recovery remains available", context)
                else:
                    self.assertEqual(stdout.getvalue(), "")

    def test_turn_events_use_actual_turn_id_and_deterministic_source_key(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "new prompt"}})
        env, log = self.install_fake_record_call(
            self.checkpoint(cursor={"byte_offset": 1}))
        prompt = self.hook_payload("UserPromptSubmit", turn_id="turn-native-1",
                                   prompt="do the next thing")
        first = self.run_hook(prompt, env)
        self.append_rollout({"type": "event_msg", "payload": {
            "type": "agent_message", "message": "source advanced"}})
        pathlib.Path(env["FAKE_RESPONSE"]).write_text(json.dumps(
            self.checkpoint(cursor={"byte_offset": 1}, version=8)), encoding="utf-8")
        archived_day = self.codex_home / "sessions" / "2026" / "09" / "06"
        archived_day.mkdir()
        prior_rollout = self.rollout
        self.rollout = archived_day / ROLLOUT_NAME
        prior_rollout.rename(self.rollout)
        prompt["transcript_path"] = str(self.rollout)
        second = self.run_hook(prompt, env)
        third = self.run_hook(prompt, env)
        self.assertIn("checkpoint is stale",
                      json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("turn-native-1 is not incorporated",
                      json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual((first.returncode, second.returncode, third.returncode), (0, 0, 0))
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        events = [call for call in calls if call["verb"] == "codex-record-event"]
        self.assertNotEqual(events[0]["args"]["idempotency_key"],
                            events[1]["args"]["idempotency_key"])
        self.assertEqual(events[1]["args"]["idempotency_key"],
                         events[2]["args"]["idempotency_key"])
        self.assertNotEqual(events[0]["args"]["transcript_ref"],
                            events[1]["args"]["transcript_ref"])
        self.assertNotEqual(events[0]["args"]["cursor"]["source_digest"],
                            events[1]["args"]["cursor"]["source_digest"])
        self.assertEqual(events[0]["args"]["cursor"]["turn_id"], "turn-native-1")
        self.assertEqual(events[0]["args"]["cursor"]["checkpoint_version"], 7)
        self.assertEqual(events[1]["args"]["cursor"]["checkpoint_version"], 8)
        self.assertEqual(events[0]["args"]["cursor"]["checkpoint_status"],
                         "available")
        pre = self.hook_payload("PreCompact", turn_id="turn-native-1", trigger="auto")
        pre_first = self.run_hook(pre, env)
        self.append_rollout({"type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_tokens": 10}}})
        pre_second = self.run_hook(pre, env)
        self.assertEqual(pre_first.stdout, "")
        self.assertEqual(pre_second.stdout, "")
        pre_other_turn = self.hook_payload(
            "PreCompact", turn_id="turn-native-2", trigger="auto")
        self.assertEqual(self.run_hook(pre_other_turn, env).stdout, "")
        self.append_rollout(compacted_row(1, "window-initial", "window-after-1"))
        post = self.hook_payload("PostCompact", turn_id="turn-native-1", trigger="auto")
        self.assertEqual(self.run_hook(post, env).stdout, "")
        self.assertEqual(self.run_hook(pre, env).stdout, "")
        self.append_rollout(compacted_row(2, "window-after-1", "window-after-2"))
        self.assertEqual(self.run_hook(post, env).stdout, "")
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        pre_events = [call for call in calls
                      if call["args"].get("event_type") == "pre_compact"]
        self.assertEqual(pre_events[0]["args"]["idempotency_key"],
                         pre_events[1]["args"]["idempotency_key"])
        self.assertNotEqual(pre_events[0]["args"]["cursor"]["source_digest"],
                            pre_events[1]["args"]["cursor"]["source_digest"])
        self.assertEqual(pre_events[0]["args"]["cursor"]["source_window_id"],
                         window_id("window-initial"))
        self.assertEqual(pre_events[0]["args"]["cursor"]["source_window_number"], 0)
        self.assertNotEqual(pre_events[1]["args"]["idempotency_key"],
                            pre_events[2]["args"]["idempotency_key"])
        self.assertEqual(pre_events[2]["args"]["cursor"]["turn_id"],
                         "turn-native-2")
        self.assertNotEqual(pre_events[1]["args"]["idempotency_key"],
                            pre_events[3]["args"]["idempotency_key"])
        self.assertEqual(pre_events[3]["args"]["cursor"]["source_window_id"],
                         window_id("window-after-1"))
        post_events = [call for call in calls
                       if call["args"].get("event_type") == "post_compact"]
        self.assertNotEqual(post_events[0]["args"]["idempotency_key"],
                            post_events[1]["args"]["idempotency_key"])
        self.assertEqual([event["args"]["cursor"]["source_window_number"]
                          for event in post_events], [1, 2])

    def test_invalid_native_compaction_chain_has_no_record_side_effect(self):
        self.native_rollout(compacted_row(2, "wrong-window", "window-after-2"))
        env, log = self.install_fake_record_call(self.checkpoint())
        result = self.run_hook(self.hook_payload(
            "PostCompact", turn_id="turn-native-1", trigger="auto"), env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("native_compaction_chain_invalid", result.stderr)
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")

        history = load_history_module()
        bad_header = native_row(self.session_id, self.project)
        bad_header["payload"]["context_window"]["window_id"] = "not-a-uuid"
        self.write_rollout([bad_header])
        with mock.patch.dict(os.environ, self.env):
            meta = history.validate_native_rollout(self.hook_payload())
            with self.assertRaises(history.HistoryFailure) as bad_first:
                history.compaction_occurrence(meta, "pre")
        self.assertEqual(bad_first.exception.payload["error"],
                         "native_context_window_invalid")

        invalid_window = compacted_row(1, "window-initial", "window-after-1")
        invalid_window["payload"]["window_id"] = "not-a-uuid"
        self.native_rollout(invalid_window)
        with mock.patch.dict(os.environ, self.env):
            meta = history.validate_native_rollout(self.hook_payload())
            with self.assertRaises(history.HistoryFailure) as bad_window:
                history.compaction_occurrence(meta, "post")
        self.assertEqual(bad_window.exception.payload["error"],
                         "native_compaction_chain_invalid")

        self.native_rollout(
            {"type": "event_msg", "payload": {"message": "scan-one"}},
            {"type": "event_msg", "payload": {"message": "scan-two"}},
        )
        with mock.patch.dict(os.environ, self.env):
            meta = history.validate_native_rollout(self.hook_payload())
            with mock.patch.object(history, "MAX_COMPACTION_SCAN_LINES", 1):
                with self.assertRaises(history.HistoryFailure) as scan_limit:
                    history.compaction_occurrence(meta, "pre")
        self.assertEqual(scan_limit.exception.payload["error"],
                         "native_compaction_scan_limit")
        with self.assertRaises(history.HistoryFailure) as scan_timeout:
            history.compaction_occurrence(meta, "pre", deadline=0.0)
        self.assertEqual(scan_timeout.exception.payload["error"],
                         "native_compaction_scan_timeout")

    def test_invalid_unknown_and_claude_payloads_have_no_record_side_effect(self):
        self.native_rollout({"type": "event_msg", "payload": {"message": "latest"}})
        env, log = self.install_fake_record_call(self.checkpoint())
        bad_payloads = [
            {**self.hook_payload(source="startup"), "session_id": "different"},
            {**self.hook_payload(source="startup"), "runtime": "claude"},
            self.hook_payload(source="clear"),
            {**self.hook_payload(source="startup"), "hook_event_name": "UnknownEvent"},
        ]
        for payload in bad_payloads:
            result = self.run_hook(payload, env)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        self.assertFalse(log.exists(), log.read_text() if log.exists() else "")


if __name__ == "__main__":
    unittest.main()
