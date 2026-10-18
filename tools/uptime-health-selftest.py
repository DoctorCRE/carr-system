#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import tempfile
import sys
import threading
import time
import uuid
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import unittest
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("uptime_health", Path(__file__).with_name("uptime_health.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 5, 19, 0, 0, tzinfo=timezone.utc)


class UptimeHealthTests(unittest.TestCase):
    def test_dropped_monitor_loop_does_not_block_a_new_fault(self):
        state = Path(__file__).resolve().parents[1] / "out/_to_delete" / f"uptime-dropped-{uuid.uuid4()}.json"
        opens = []

        def verb(name, payload):
            if name == "add-loop":
                opens.append(payload)
                return {"ok": True, "loop_id": f"loop-{len(opens)}"}
            if name == "read-loop":
                return {"loop_id": payload["loop_id"], "version": 2, "status": "dropped"}
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
                    return loops[payload["loop_id"]].copy()
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
        mode = {"timestamp": None, "partial": False}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                body = json.dumps({**good, "checked_at": mode["timestamp"]}).encode()
                self.send_header("Content-Length", str(len(body) + (100 if mode["partial"] else 0)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = Path(__file__).resolve().parents[1]
        destination = root / "out/_to_delete" / f"uptime-invalid-{uuid.uuid4()}"
        destination.mkdir(parents=True)
        # Refuse local state before any record call; this fixture never contacts production.
        state = destination / "response.json"
        state.write_text("not-json")
        try:
            for timestamp in (None, 4, [], {}, "2026-10-05T19:00:00Z"):
                mode.update(timestamp=timestamp, partial=isinstance(timestamp, str))
                output = destination / "findings.json"
                result = subprocess.run([
                    sys.executable, str(root / "tools/health-check.py"), "--section", "uptime",
                    "--findings-json", str(output),
                ], env={**os.environ, "CARR_UPTIME_STATUS_URL": f"http://127.0.0.1:{server.server_port}/healthz",
                        "CARR_UPTIME_RESPONSE_STATE": str(state)}, capture_output=True, text=True, timeout=15)
                with self.subTest(timestamp=timestamp):
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(result.stdout.splitlines()[-1],
                                     "Projection freshness/tamper checks are recovery evidence; use --recovery --reason <why>.")
                    self.assertEqual(json.loads(output.read_text())["findings"][0]["key"], "production_uptime")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_status_deadline_covers_streaming_headers_and_body(self):
        mode = {"headers": False}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    if mode["headers"]:
                        time.sleep(0.8)
                    self.send_response(200)
                    self.send_header("Content-Length", "12")
                    self.end_headers()
                    for byte in b'{"ok":true} ':
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = os.environ.get("CARR_UPTIME_STATUS_URL")
        os.environ["CARR_UPTIME_STATUS_URL"] = f"http://127.0.0.1:{server.server_port}/healthz"
        try:
            for headers in (False, True):
                mode["headers"] = headers
                started = time.monotonic()
                with self.assertRaises((TimeoutError, subprocess.TimeoutExpired)):
                    module.read_status(timeout=0.25)
                self.assertLess(time.monotonic() - started, 0.7)
        finally:
            if original is None:
                os.environ.pop("CARR_UPTIME_STATUS_URL")
            else:
                os.environ["CARR_UPTIME_STATUS_URL"] = original
            server.shutdown()
            server.server_close()
            thread.join()

    def test_monitor_failure_has_one_loop_and_verified_recovery_closes_it(self):
        root = Path(__file__).resolve().parents[1]
        state = root / "out/_to_delete" / f"uptime-response-{uuid.uuid4()}.json"
        calls = []

        def verb(name, payload):
            calls.append((name, payload))
            if name == "add-loop":
                return {"ok": True, "loop_id": "fixture-monitor-loop"}
            if name == "read-loop":
                return {"loop_id": "fixture-monitor-loop", "version": 1, "status": "open"}
            return {"ok": True}

        for _ in range(2):
            self.assertEqual(module.reconcile_monitor(True, False, state, verb), "open")
        self.assertEqual(sum(name == "add-loop" for name, _ in calls), 1)
        self.assertEqual(module.reconcile_monitor(False, False, state, verb), "open")
        self.assertEqual(module.reconcile_monitor(False, True, state, verb), "clear")
        self.assertEqual(sum(name == "close-loop" for name, _ in calls), 1)
        self.assertIn("monitoring unavailable", calls[0][1]["body"])
        self.assertIn("recovered", calls[-1][1]["outcome"])

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
            "checked_at": datetime.now(timezone.utc).isoformat(), "failures": 0,
            "pending_records": 0, "pending_alerts": 0, "active_incident": None,
            "configuration_missing": [], "alert_error": None, "record_error": None,
            "checks": [{"name": name, "ok": True} for name in (
                "api-release", "app-release", "verb-round-trip")],
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if payload["ok"] else 503)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = Path(__file__).resolve().parents[1]
        destination = root / "out" / "_to_delete" / f"uptime-health-fixture-{uuid.uuid4()}"
        destination.mkdir(parents=True)
        try:
            for healthy in (True, False):
                payload["ok"] = healthy
                payload["failures"] = 0 if healthy else 3
                output = destination / "findings.json"
                result = subprocess.run([
                    sys.executable, str(root / "tools/health-check.py"), "--section", "uptime",
                    "--findings-json", str(output),
                ], env={**os.environ, "CARR_UPTIME_STATUS_URL": f"http://127.0.0.1:{server.server_port}/healthz",
                        "CARR_UPTIME_RESPONSE_STATE": str(destination / "response.json")},
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0 if healthy else 1, result.stderr)
                self.assertIn("on breach:", result.stdout)
                findings = json.loads(output.read_text())["findings"]
                self.assertEqual(len(findings), 0 if healthy else 1)
                if not healthy:
                    self.assertEqual(findings[0]["key"], "production_uptime")
                    self.assertTrue(findings[0]["hard_error"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_unprovisioned_monitor_warns_without_blocking_release(self):
        # Joe 2026-10-06: an unprovisioned monitor must not block releases. Unreachable is WARN;
        # a reachable monitor reporting production failures stays hard (test above).
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "findings.json"
            result = subprocess.run([
                sys.executable, str(root / "tools/health-check.py"), "--section", "uptime",
                "--findings-json", str(output),
            ], env={**os.environ, "CARR_UPTIME_STATUS_URL": "http://127.0.0.1:9/healthz",
                    "CARR_UPTIME_RESPONSE_STATE": str(Path(tmp) / "response.json")},
                capture_output=True, text=True, timeout=30)
            findings = json.loads(output.read_text())["findings"]
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
