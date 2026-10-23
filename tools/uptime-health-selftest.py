#!/usr/bin/env python3
import importlib.util
import contextlib
import io
import runpy
import json
import os
import subprocess
import tempfile
import sys
import time
import uuid
from http.client import IncompleteRead
from pathlib import Path
import unittest
from functools import partial
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest import mock
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("uptime_health", Path(__file__).with_name("uptime_health.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 5, 19, 0, 0, tzinfo=timezone.utc)


class UptimeHealthTests(unittest.TestCase):
    def run_health(self, read, destination):
        """Run the real CLI with fixture reads and an empty record-backed audit."""
        import health_breach
        path = Path(__file__).with_name("health-check.py")
        output = destination / "findings.json"
        with mock.patch.object(sys, "argv", [str(path), "--section", "uptime",
                                             "--findings-json", str(output)]), \
                mock.patch.dict(sys.modules, uptime_health=module), \
                mock.patch.dict(os.environ, {"CARR_UPTIME_RESPONSE_STATE": str(destination / "response.json")}), \
                mock.patch.object(module, "row", partial(module.row, read=read, now=NOW)), \
                mock.patch.object(module, "call_verb", return_value={"ok": True, "loop_id": "fixture-loop"}), \
                mock.patch.object(health_breach, "record_door", return_value=SimpleNamespace(ok=True, reply={"loops": []})), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("unexpected subprocess")) as process, \
                contextlib.redirect_stdout(io.StringIO()) as stdout, \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(path), run_name="__main__")
            process.assert_not_called()
        return subprocess.CompletedProcess(sys.argv, exited.exception.code, stdout.getvalue(), stderr.getvalue())

    def test_refused_or_malformed_loop_read_reports_error(self):
        replies = ({"ok": False, "refused": True}, {"loop": None}, {"loop": []},
                   {"loop_id": "fixture-loop", "version": 1, "status": "open"})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"key": "fixture-key", "loop_id": "fixture-loop"}))
            for reply in replies:
                calls = []
                def verb(name, payload):
                    calls.append((name, payload))
                    return reply
                with self.subTest(reply=reply):
                    self.assertEqual(module.reconcile_monitor(False, True, state, verb), "error")
                    self.assertEqual(calls, [("read-loop", {"loop_id": "fixture-loop"})])

    def test_dropped_monitor_loop_does_not_block_a_new_fault(self):
        state = Path(__file__).resolve().parents[1] / "out/_to_delete" / f"uptime-dropped-{uuid.uuid4()}.json"
        opens = []

        def verb(name, payload):
            if name == "add-loop":
                opens.append(payload)
                return {"ok": True, "loop_id": f"loop-{len(opens)}"}
            if name == "read-loop":
                return {"loop": {"loop_id": payload["loop_id"], "version": 2, "status": "dropped"},
                        "amended": False, "amendments": []}
            self.fail("must preserve the dropped disposition")

        self.assertEqual(module.reconcile_monitor(True, False, state, verb), "open")
        self.assertEqual(module.reconcile_monitor(False, True, state, verb), "clear")
        self.assertEqual(module.reconcile_monitor(True, False, state, verb), "open")
        self.assertEqual(len(opens), 2)

    def test_two_readers_share_record_identity_across_checkout_state_and_recovery(self):
        root = Path(__file__).resolve().parents[1] / "out/_to_delete" / f"uptime-readers-{uuid.uuid4()}"
        paths = [root / "worktree-a.json", root / "machine-b.json"]
        loops = {}
        replay = {}

        def reader(principal):
            def verb(name, payload):
                if name == "add-loop":
                    key = payload["idempotency_key"]
                    manifest = (principal, payload.copy())
                    if key in replay:
                        if manifest != replay[key][0]:
                            return {"ok": False, "error": "key_reuse"}
                        return {**replay[key][1], "replayed": True}
                    existing = next((value for value in loops.values()
                                     if value["status"] == "open" and value.get("source_note") == payload.get("source_note")), None)
                    if existing and payload.get("source_note"):
                        result = {"ok": True, "loop_id": existing["loop_id"], "deduplicated": True}
                    else:
                        loop_id = f"loop-{len(loops) + 1}"
                        loops[loop_id] = {"loop_id": loop_id,
                                          "status": "open", "version": 1, "source_note": payload.get("source_note")}
                        result = {"ok": True, "loop_id": loop_id}
                    replay[key] = (manifest, result)
                    return result
                if name == "read-loop":
                    return {"loop": loops[payload["loop_id"]].copy(),
                            "amended": False, "amendments": []}
                self.assertEqual(name, "close-loop")
                loops[payload["loop_id"]]["status"] = "done"
                return {"ok": True}
            return verb

        readers = [reader("machine-a"), reader("machine-b")]
        for path, verb in zip(paths, readers):
            self.assertEqual(module.reconcile_monitor(True, False, path, verb), "open")
        self.assertEqual(len(loops), 1)
        loops["loop-1"]["status"] = "dropped"
        for path, verb in zip(paths, readers):
            self.assertEqual(module.reconcile_monitor(False, True, path, verb), "clear")
        for path, verb in zip(paths, readers):
            self.assertEqual(module.reconcile_monitor(True, False, path, verb), "open")
        self.assertEqual(len(loops), 2)
        self.assertEqual(loops["loop-1"]["status"], "dropped")
        fresh = root / "new-machine.json"
        self.assertEqual(module.reconcile_monitor(True, False, fresh, reader("machine-c")), "open")
        self.assertEqual(module.reconcile_monitor(False, True, fresh, reader("machine-c")), "clear")
        self.assertEqual(loops["loop-2"]["status"], "done")
        self.assertEqual(len(loops), 2)

    def test_default_record_adapter_uses_supported_call_signature(self):
        root = Path(__file__).resolve().parents[1] / "out/_to_delete" / f"uptime-adapter-{uuid.uuid4()}.json"
        from unittest.mock import patch
        def call(name, payload):
            return {"ok": True, "loop_id": "fixture"}
        with patch.object(module, "call_verb", side_effect=call):
            self.assertEqual(module.reconcile_monitor(True, False, root), "open")

    def test_invalid_saved_retry_key_reports_error(self):
        state = Path(__file__).resolve().parents[1] / "out/_to_delete" / f"uptime-state-{uuid.uuid4()}.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        for key in (None, 4, [], {}):
            state.write_text(json.dumps({"key": key}))
            with self.subTest(key=key):
                self.assertEqual(module.reconcile_monitor(True, False, state, lambda *_: self.fail("invalid state must not call records")), "error")

    def test_partial_protocol_read_reports_bound_failure(self):
        def partial():
            raise IncompleteRead(b"fixture", 100)
        responses = []
        line, failed = module.row(read=partial, now=NOW, respond=lambda *args: responses.append(args))
        self.assertTrue(failed)
        self.assertIn("unreachable", line)
        self.assertEqual(responses, [(True, False)])

    def test_invalid_timestamps_and_truncated_body_preserve_health_cli_completion(self):
        good = {
            "schema": "carr-uptime.v1", "ok": True, "failures": 0,
            "pending_records": 0, "pending_alerts": 0,
            "checks": [{"name": name, "ok": True} for name in (
                "api-release", "app-release", "verb-round-trip")],
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            # Refuse local state before any record call, as in the original fixture.
            (destination / "response.json").write_text("not-json")
            for timestamp in (None, 4, [], {}, "2026-10-05T19:00:00Z"):
                body = json.dumps({**good, "checked_at": timestamp}).encode()
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.headers = {"Content-Length": str(len(body) + (100 if isinstance(timestamp, str) else 0))}
                response.read.return_value = body
                with self.subTest(timestamp=timestamp), mock.patch.object(module, "urlopen", return_value=response):
                    result = self.run_health(lambda: module._read_status("https://uptime.invalid/healthz"), destination)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(result.stdout.splitlines()[-1],
                                     "Projection freshness/tamper checks are recovery evidence; use --recovery --reason <why>.")
                    self.assertEqual(json.loads((destination / "findings.json").read_text())["findings"][0]["key"], "production_uptime")
                    response.read.assert_called_once_with(65537)

    def test_status_deadline_covers_streaming_headers_and_body(self):
        # The deadline belongs to the whole fetch subprocess, regardless of
        # whether it stalls in headers or the body. Simulate expiry immediately.
        for phase in ("headers", "body"):
            elapsed = [0.0]
            def expire(command, *, timeout, **_kwargs):
                elapsed[0] += timeout
                raise subprocess.TimeoutExpired(command, timeout)
            with self.subTest(phase=phase), \
                    mock.patch.object(time, "monotonic", side_effect=lambda: elapsed[0]), \
                    mock.patch.object(module.subprocess, "run", side_effect=expire) as process, \
                    mock.patch.dict(os.environ, {"CARR_UPTIME_STATUS_URL": "https://uptime.invalid/healthz"}):
                started = time.monotonic()
                with self.assertRaises((TimeoutError, subprocess.TimeoutExpired)):
                    module.read_status(timeout=0.25)
                self.assertLess(time.monotonic() - started, 0.7)
                process.assert_called_once_with(
                    [sys.executable, str(Path(module.__file__).resolve()), "https://uptime.invalid/healthz"],
                    capture_output=True, timeout=0.25, check=True)

    def test_monitor_failure_has_one_loop_and_verified_recovery_closes_it(self):
        root = Path(__file__).resolve().parents[1]
        state = root / "out/_to_delete" / f"uptime-response-{uuid.uuid4()}.json"
        calls = []

        def verb(name, payload):
            calls.append((name, payload))
            if name == "add-loop":
                return {"ok": True, "loop_id": "fixture-monitor-loop"}
            if name == "read-loop":
                return {"loop": {"loop_id": "fixture-monitor-loop", "version": 1, "status": "open"},
                        "amended": False, "amendments": []}
            return {"ok": True}

        for _ in range(2):
            self.assertEqual(module.reconcile_monitor(True, False, state, verb), "open")
        self.assertEqual(sum(name == "add-loop" for name, _ in calls), 1)
        self.assertEqual(module.reconcile_monitor(False, False, state, verb), "open")
        self.assertEqual(module.reconcile_monitor(False, True, state, verb), "clear")
        self.assertEqual(sum(name == "close-loop" for name, _ in calls), 1)
        self.assertIn("monitoring unavailable", calls[0][1]["body"])
        self.assertIn("recovered", calls[-1][1]["outcome"])
        self.assertEqual(calls[-1][1]["base_version"], 1)

    def test_monitor_loop_replays_after_lost_acknowledgement_and_refuses_corrupt_state(self):
        root = Path(__file__).resolve().parents[1]
        state = root / "out/_to_delete" / f"uptime-response-{uuid.uuid4()}.json"
        calls = []

        def lost_ack(name, payload):
            calls.append(payload)
            if len(calls) == 1:
                raise TimeoutError("fixture credential must never appear")
            self.assertEqual(payload, calls[0])
            return {"ok": True, "loop_id": "fixture-loop"}

        self.assertEqual(module.reconcile_monitor(True, False, state, lost_ack), "error")
        self.assertEqual(module.reconcile_monitor(True, False, state, lost_ack), "open")
        state.write_text("not-json")
        self.assertEqual(module.reconcile_monitor(True, False, state, lost_ack), "error")
        self.assertEqual(len(calls), 2)

    def test_health_cli_reads_fixture_status_and_records_a_bound_finding(self):
        payload = {
            "schema": "carr-uptime.v1", "ok": True,
            "checked_at": NOW.isoformat(), "failures": 0,
            "pending_records": 0, "pending_alerts": 0, "active_incident": None,
            "configuration_missing": [], "alert_error": None, "record_error": None,
            "checks": [{"name": name, "ok": True} for name in (
                "api-release", "app-release", "verb-round-trip")],
        }

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            for healthy in (True, False):
                payload["ok"] = healthy
                payload["failures"] = 0 if healthy else 3
                body = json.dumps(payload).encode()
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.headers = {"Content-Length": str(len(body))}
                response.read.return_value = body
                option = ({"return_value": response} if healthy else {"side_effect": HTTPError(
                    "https://uptime.invalid/healthz", 503, "fixture incident", response.headers, io.BytesIO(body))})
                with mock.patch.object(module, "urlopen", **option):
                    result = self.run_health(lambda: module._read_status("https://uptime.invalid/healthz"), destination)
                self.assertEqual(result.returncode, 0 if healthy else 1, result.stderr)
                self.assertIn("on breach:", result.stdout)
                findings = json.loads((destination / "findings.json").read_text())["findings"]
                self.assertEqual(len(findings), 0 if healthy else 1)
                if not healthy:
                    self.assertEqual(findings[0]["key"], "production_uptime")
                    self.assertTrue(findings[0]["hard_error"])

    def test_unprovisioned_monitor_warns_without_blocking_release(self):
        # Unreachable is WARN; a reachable monitor reporting failures stays hard.
        def unreachable():
            raise OSError("fixture monitor unavailable")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            result = self.run_health(unreachable, destination)
            findings = json.loads((destination / "findings.json").read_text())["findings"]
            self.assertEqual([f["key"] for f in findings], ["production_uptime"], result.stdout)
            self.assertFalse(findings[0]["hard_error"])
            self.assertIn("monitor unreachable", findings[0]["detail"])

    def test_output_has_bound_response_for_down_wrong_shape_slow_and_recovered(self):
        good = {
            "schema": "carr-uptime.v1", "ok": True, "checked_at": "2026-10-05T18:59:00Z",
            "failures": 0, "active_incident": None, "pending_records": 0, "pending_alerts": 0,
            "configuration_missing": [], "alert_error": None, "record_error": None,
            "checks": [{"name": name, "ok": True, "reason": "ok"} for name in (
                "api-release", "app-release", "verb-round-trip")],
        }
        cases = [
            (good, False, "OK"),
            ({**good, "ok": False, "failures": 3, "active_incident": "fixture-incident"}, True, "WARN"),
            ({**good, "pending_records": 1}, True, "WARN"),
            ({**good, "checked_at": "2026-10-05T18:55:00Z"}, True, "WARN"),
            ({"ok": True}, True, "WARN"),
            ({**good, "checks": []}, True, "WARN"),
        ]
        for payload, failed, prefix in cases:
            with self.subTest(payload=payload):
                line, observed = module.row(read=lambda: payload, now=NOW, respond=lambda *_: "none")
                self.assertEqual(observed, failed)
                self.assertTrue(line.startswith(prefix), line)
                for clause in ("on breach:", "loop", "owner orchestrator", "fix:", "verify:", "auto-clear:"):
                    self.assertIn(clause, line)

        def slow():
            raise TimeoutError("fixture-secret must never appear")
        line, failed = module.row(read=slow, now=NOW, respond=lambda *_: "none")
        self.assertTrue(failed)
        self.assertNotIn("fixture-secret", line)
        self.assertIn("unreachable", line)


if __name__ == "__main__":
    unittest.main()
