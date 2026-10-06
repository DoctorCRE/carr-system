#!/usr/bin/env python3
"""Synthetic timestamp-only tests; no real credential or provider call."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("grok_session", ROOT / "ops/grok_session.py")
assert spec is not None and spec.loader is not None
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


class GrokSessionTests(unittest.TestCase):
    def test_public_health_section_is_narrow_and_propagates_status(self):
        # Supply synthetic results through the helper seam. This entrypoint
        # must finish without running DB/other-provider health readers.
        harness = '''import runpy, sys, types
sys.path.insert(0, sys.argv.pop(1))
status = sys.argv.pop(1)
fake = types.ModuleType("grok_session")
fake.health_row = lambda: status + " Grok session — synthetic timestamps · run grok login"
sys.modules["grok_session"] = fake
path = sys.argv.pop(1)
runpy.run_path(path, run_name="__main__")
'''
        for status, code in [("OK", 0), ("WARN", 0), ("FAIL", 1)]:
            result = subprocess.run([sys.executable, "-c", harness, str(ROOT / "tools"), status,
                                     str(ROOT / "tools/health-check.py"), "--section", "grok-session"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertIn(status + " Grok session", result.stdout)
            self.assertTrue(result.stdout.rstrip().endswith(
                "Projection freshness/tamper checks are recovery evidence; use --recovery --reason <why>."))
            self.assertNotIn("jev paid cap", result.stdout)
            self.assertNotIn("credential health", result.stdout)
            self.assertNotIn("jev spend", result.stdout)
            self.assertEqual("CANONICAL_FINDING grok_session" in result.stdout, status == "FAIL")

    def test_nested_timestamp_storage_is_supported_without_retaining_scope_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 72)
            pair = json.loads(path.read_text())
            path.write_text(json.dumps({"synthetic-scope": pair}))
            self.assertEqual(session.read_timestamps(path), pair)

    def test_nightly_runs_narrow_health_and_preflights_reader(self):
        source = (ROOT / "bin/nightly.sh").read_text()
        self.assertTrue('step "Grok authentication health"' in source)
        self.assertTrue('tools/health-check.py --section grok-session' in source)
        self.assertTrue('tools/health-check.py ops/jev_spend_health.py ops/grok_session.py' in source)

    def test_transports_are_independent_and_never_echo_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def record(name, payload):
                if name == "add-loop":
                    return {"ok": True, "loop_id": "synthetic-loop"}
                if name == "read-loop":
                    return {"loop_id": "synthetic-loop", "version": 1, "status": "open"}
                self.fail(name)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'synthetic private output', 'synthetic private output'))
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", provider):
                session._deliver("Grok needs sign-in; run grok login.")
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(provider.call_args_list[0].args[0][0], "/usr/bin/osascript")
            self.assertIn("joe", provider.call_args_list[1].args[0])
            for call in provider.call_args_list:
                self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)

    def test_partial_delivery_does_not_repeat_successful_or_uncertain_send(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = mock.Mock(side_effect=[subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)])
            with mock.patch.object(session, "ROOT", Path(directory)), \
                    mock.patch.object(session, "_run_verb", return_value={"ok": True, "loop_id": "synthetic-loop", "version": 1, "status": "open"}), \
                    mock.patch.object(session.subprocess, "run", provider):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        session._deliver("Grok needs sign-in; run grok login.")
            self.assertEqual(provider.call_count, 2)

    def test_loop_failure_still_reaches_local_and_mail_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with mock.patch.object(session, "ROOT", Path(directory)), \
                    mock.patch.object(session, "_run_verb", side_effect=RuntimeError("record unavailable")), \
                    mock.patch.object(session.subprocess, "run", provider):
                with self.assertRaises(RuntimeError):
                    session._deliver("Grok needs sign-in; run grok login.")
            self.assertEqual(provider.call_count, 2)

    def test_healthy_readback_closes_existing_response_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            (root / "out/grok-session-loop.json").write_text('{"loop_id":"synthetic-loop"}')
            record = mock.Mock(side_effect=[{"loop_id":"synthetic-loop", "version":1, "status":"open"}, {"ok":True}])
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record):
                session._clear_loop()
            self.assertEqual(record.call_args.args[0], "close-loop")
            self.assertEqual(json.loads((root / "out/grok-session-loop.json").read_text()), {})

    def test_finding1_six_hour_and_refreshable_expired_access(self):
        with tempfile.TemporaryDirectory() as directory:
            for hours, age in ((6, 0), (-1, 7)):
                row = session.inspect_session(self.fixture(directory, hours, age=age), now=NOW,
                                              authenticate=lambda: "succeeded")
                self.assertEqual(row["status"], "OK")
            row = session.inspect_session(self.fixture(directory, 6), now=NOW,
                                          authenticate=lambda: "refused")
            self.assertEqual(row["status"], "FAIL")

    def test_finding2_complete_json_grammar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 72)
            pair = path.read_text()[:-1]
            for value in ('not_json', '01', '+1', '1.', '1e', 'NaN', 'truex',
                          '\"bad\\q\"', '\"bad\\u00xz\"'):
                with self.subTest(value=value):
                    path.write_text(pair + ', "unknown":' + value + '}')
                    self.assertEqual(session.inspect_session(path, now=NOW)["status"], "FAIL")

    def test_finding3_sibling_pairs_and_multiple_sessions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 72)
            pair = json.loads(path.read_text())
            for doc in ({"a": {"create_time": pair["create_time"]},
                         "b": {"expires_at": pair["expires_at"]}},
                        {"a": pair, "b": pair}):
                path.write_text(json.dumps(doc))
                self.assertEqual(session.inspect_session(path, now=NOW)["status"], "FAIL")

    def lifecycle(self, root):
        rows, keys, calls = {}, {}, []
        def record(name, payload):
            calls.append(name)
            if name == "add-loop":
                key = payload["idempotency_key"]
                if key not in keys:
                    ident = "loop-" + str(len(rows) + 1)
                    keys[key] = ident
                    rows[ident] = {"loop_id": ident, "version": 1, "status": "open"}
                return {"ok": True, "loop_id": keys[key]}
            row = rows[payload["loop_id"]]
            if name == "read-loop":
                return dict(row)
            if row["status"] != "open":
                raise RuntimeError("loop_not_open")
            row["version"] += 1
            if name == "close-loop":
                row["status"] = "done"
            return {"ok": True}
        return rows, calls, record

    def test_finding4_closed_pointer_is_reconciled_before_update_or_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                session._deliver("synthetic sign-in failure")
                rows["loop-1"]["status"] = "done"
                session._clear_loop()
                session._deliver("synthetic next failure")
            self.assertEqual(len(rows), 2)
            self.assertNotIn("close-loop", calls)

    def test_finding5_commit_then_timeout_reuses_identity_next_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            def uncertain(name, payload):
                result = record(name, payload)
                if name == "add-loop":
                    raise subprocess.TimeoutExpired("synthetic record", 1)
                return result
            with mock.patch.object(session, "ROOT", root), \
                    mock.patch.object(session.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                with mock.patch.object(session, "_run_verb", uncertain):
                    with self.assertRaises(RuntimeError):
                        session._deliver("synthetic failure", now=NOW)
                with mock.patch.object(session, "_run_verb", record):
                    session._deliver("synthetic failure", now=NOW + timedelta(days=1))
                    session._clear_loop()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows["loop-1"]["status"], "done")

    def test_finding6_stale_health_and_unchanged_timestamps_cannot_clear_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                state = root / "lock-state.json"
                session.sign_in_alert(state_path=state, now=NOW)
                fresh = mock.Mock(return_value={"status": "FAIL", "detail": "authentication refused", "authentication": "refused"})
                session.health_row(state_path=state, now=NOW, observe=fresh)
                self.assertTrue(fresh.called)
                self.assertEqual(rows["loop-1"]["status"], "open")

    def test_finding7_rebreach_creates_loop_without_repeat_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", provider):
                state = root / "lock-state.json"
                session.sign_in_alert(state_path=state, now=NOW)
                session.health_row(state_path=state, now=NOW,
                                   observe=lambda: {"status": "OK", "detail": "fresh", "authentication": "succeeded"})
                session.sign_in_alert(state_path=state, now=NOW)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows["loop-2"]["status"], "open")
            self.assertEqual(provider.call_count, 2)

    def test_crash_after_committed_create_recovers_from_persisted_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            def crashed(name, payload):
                result = record(name, payload)
                if name == "add-loop":
                    raise SystemExit("simulated process death before ID save")
                return result
            with mock.patch.object(session, "ROOT", root), \
                    mock.patch.object(session.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                with mock.patch.object(session, "_run_verb", crashed):
                    with self.assertRaises(SystemExit):
                        session.sign_in_alert(state_path=root / "state", now=NOW)
                saved = json.loads((root / "out/grok-session-loop.json").read_text())
                self.assertIn("incident", saved)
                self.assertNotIn("loop_id", saved)
                with mock.patch.object(session, "_run_verb", record):
                    session.health_row(state_path=root / "state", observe=lambda: {
                        "status": "OK", "detail": "fresh", "authentication": "succeeded"})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows["loop-1"]["status"], "done")

    def test_authentication_refusal_with_unchanged_timestamps_stays_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = self.fixture(directory, 6)
            rows, calls, record = self.lifecycle(root)
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                session.sign_in_alert(state_path=root / "state", now=NOW)
                for auth in ("refused", "unavailable", "unchecked"):
                    session.health_row(state_path=root / "state", now=NOW, observe=lambda: session.inspect_session(
                        credential, now=NOW, authenticate=lambda: auth))
                    self.assertEqual(rows["loop-1"]["status"], "open")
                session.health_row(state_path=root / "state", now=NOW, observe=lambda: session.inspect_session(
                    credential, now=NOW, authenticate=lambda: "succeeded"))
            self.assertEqual(rows["loop-1"]["status"], "done")

    def test_authentication_observation_holds_runner_incident_lock(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            entered, release, runner_started = threading.Event(), threading.Event(), threading.Event()
            def observe():
                entered.set()
                self.assertTrue(release.wait(3))
                return {"status": "OK", "detail": "fresh", "authentication": "succeeded"}
            def runner():
                runner_started.set()
                session.sign_in_alert(state_path=root / "state", now=NOW)
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                session.sign_in_alert(state_path=root / "state", now=NOW)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    health = pool.submit(session.health_row, state_path=root / "state", observe=observe)
                    self.assertTrue(entered.wait(3))
                    running = pool.submit(runner)
                    self.assertTrue(runner_started.wait(3))
                    self.assertFalse(running.done())
                    release.set()
                    health.result(timeout=3)
                    running.result(timeout=3)
            self.assertEqual(rows["loop-1"]["status"], "done")
            self.assertEqual(rows["loop-2"]["status"], "open")

    def test_probe_discards_diagnostics_and_distinguishes_refusal_from_outage(self):
        for result, expected in ((subprocess.CompletedProcess([], 0, "models", ""), "succeeded"),
                                 (subprocess.CompletedProcess([], 1, "", "grok login"), "refused"),
                                 (subprocess.CompletedProcess([], 1, "", "network unavailable"), "unavailable")):
            with mock.patch.object(session.subprocess, "run", return_value=result) as provider:
                self.assertEqual(session.authenticate(), expected)
                self.assertEqual(provider.call_args.args[0], ["grok", "models"])
                self.assertEqual(provider.call_args.kwargs["stdin"], subprocess.DEVNULL)
        with mock.patch.object(session.subprocess, "run", side_effect=subprocess.TimeoutExpired("private", 60)):
            self.assertEqual(session.authenticate(), "unavailable")

    def test_installed_scoped_credential_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 6)
            pair = json.loads(path.read_text())
            path.write_text(json.dumps({"https://accounts.x.ai/sign-in": pair}))
            self.assertEqual(session.read_timestamps(path), pair)

    def test_shared_authentication_result(self):
        for result, expected in ((subprocess.CompletedProcess([], 0, "models", ""), "succeeded"),
                                 (subprocess.CompletedProcess([], 1, "", "grok login"), "refused"),
                                 (subprocess.CompletedProcess([], 1, "", "network unavailable"), "unavailable")):
            self.assertEqual(session.authentication_result(result), expected)

    def test_unresolved_incident_keeps_one_record_across_days(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", provider):
                session.sign_in_alert(state_path=root / "state", now=NOW)
                session.sign_in_alert(state_path=root / "state", now=NOW + timedelta(days=1))
            self.assertEqual(len(rows), 1)
            self.assertNotIn("update-loop", calls)
            self.assertEqual(provider.call_count, 4)

    def fixture(self, directory, hours, age=24):
        path = Path(directory) / "auth.json"
        path.write_text(json.dumps({"create_time": (NOW - timedelta(hours=age)).isoformat(),
                                    "expires_at": (NOW + timedelta(hours=hours)).isoformat()}))
        return path

    def test_days_left_and_age_use_timestamps_not_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            row = session.inspect_session(self.fixture(directory, 72, age=48), now=NOW)
            self.assertEqual((row["age_days"], row["days_left"], row["status"]), (2, 3, "OK"))

    def test_access_expiry_is_informational_without_authentication_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            for hours in (72, 48, 24, 6, 0, -12):
                row = session.inspect_session(self.fixture(directory, hours), now=NOW)
                self.assertEqual(row["status"], "OK" if hours > 0 else "WARN")
                self.assertAlmostEqual(row["days_left"], hours / 24)
                self.assertEqual(row["authentication"], "unchecked")

    def test_missing_malformed_and_future_timestamps_fail_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            for raw in [None, "{", '{}', '{"create_time":"bad","expires_at":null}',
                        json.dumps({"create_time": (NOW + timedelta(days=1)).isoformat(),
                                    "expires_at": (NOW + timedelta(days=2)).isoformat()})]:
                if raw is not None:
                    path.write_text(raw)
                row = session.inspect_session(path, now=NOW)
                self.assertEqual(row["status"], "FAIL")
                self.assertNotIn("bad", row["detail"])

    def test_concurrent_breaches_share_the_daily_lock(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, calls, record = self.lifecycle(root)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", provider):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda _: session.sign_in_alert(state_path=root / "state.json", now=NOW), range(8)))
            self.assertEqual(len(rows), 1)
            self.assertEqual(provider.call_count, 2)

    def test_health_row_binds_owner_remediation_verification_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            row = session.inspect_session(self.fixture(directory, 72), now=NOW)
            line = session.health_row(state_path=Path(directory)/"state.json", now=NOW, observe=lambda: row)
            for text in ["age 1.00d", "3.00d left", "owner Joe", "run grok login", "verify", "auto-clear"]:
                self.assertIn(text, line)

    def test_reader_returns_only_allowed_fields_and_rejects_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 72)
            self.assertEqual(set(session.read_timestamps(path)), {"create_time", "expires_at"})
            path.write_text('{"create_time":"2026-10-01T12:00:00Z", "create_time":"2026-10-01T12:00:00Z", "expires_at":"2026-10-05T12:00:00Z"}')
            self.assertEqual(session.inspect_session(path, now=NOW)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
