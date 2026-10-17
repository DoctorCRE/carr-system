#!/usr/bin/env python3
"""Offline lifecycle fixtures. No launchd command or model request can escape these tests."""
import importlib.util
import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "room-bridge"))
import flashlib
import state
import flash_wire


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        env = patch.dict(os.environ, {"CARR_FLASH_STATE_DIR": str(self.directory),
                                     "CARR_FLASH_LOG": str(self.directory / "server.log")})
        env.start()
        self.addCleanup(env.stop)
        no_process = patch.object(flashlib.subprocess, "run", side_effect=AssertionError("unmocked process"))
        no_process.start()
        self.addCleanup(no_process.stop)
        no_network = patch.object(flashlib.urllib.request, "urlopen", side_effect=AssertionError("unmocked network"))
        no_network.start()
        self.addCleanup(no_network.stop)

    def test_warm_ensure_does_not_launch(self):
        launch = Mock(side_effect=AssertionError("warm server must not be launched"))
        self.assertTrue(flashlib.ensure(ready=lambda **_: True, launch=launch))

    def test_off_switch_path_defaults_to_config_and_uses_state_dir_override(self):
        self.assertEqual(flashlib.off_switch_path(), self.directory / "flash.off")
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(flashlib.Path, "home", return_value=Path("/Users/tester")):
            self.assertEqual(flashlib.off_switch_path(), Path("/Users/tester/.config/carr/flash.off"))

    def test_off_switch_refuses_lifecycle_entry_points_with_a_clear_reason(self):
        (self.directory / "flash.off").touch()
        launch = Mock(side_effect=AssertionError("switched-off Flash must not launch"))
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertFalse(flashlib.ensure(ready=lambda **_: True, launch=launch))
        self.assertIn("flash is switched off", error.getvalue())
        with self.assertRaisesRegex(flashlib.FlashSwitchedOff, "^flash is switched off$"):
            flashlib.ensure_server()
        with self.assertRaisesRegex(flashlib.FlashSwitchedOff, "^flash is switched off$"):
            flashlib.ensure_desk(lambda: True, launch=launch)
        for scope in (flashlib.activity_scope, flashlib.request_scope):
            with self.assertRaisesRegex(flashlib.FlashSwitchedOff, "^flash is switched off$"):
                with scope():
                    pass
        launch.assert_not_called()

    def test_cold_ensure_bootstraps_once(self):
        ready = Mock(side_effect=[False, False, False, True])
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.assertTrue(flashlib.ensure(ready=ready, launch=launch, sleep=lambda _: None))
        self.assertEqual([c.args[0] for c in launch.call_args_list], ["enable", "bootstrap", "kickstart"])

    def test_loaded_job_uses_kickstart(self):
        ready = Mock(side_effect=[False, False, True])
        launch = Mock(side_effect=[subprocess.CompletedProcess([], code, "", "") for code in (0, 5, 0)])
        self.assertTrue(flashlib.ensure(ready=ready, launch=launch))
        self.assertEqual([c.args[0] for c in launch.call_args_list], ["enable", "bootstrap", "kickstart"])

    def test_timeout_fixture_waits_180_seconds_and_disables_late_load(self):
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with patch.object(flashlib.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertFalse(flashlib.ensure(ready=lambda **_: False, launch=launch,
                                           clock=lambda: elapsed[0], sleep=sleep))
        self.assertEqual(elapsed[0], 180)
        self.assertEqual([c.args[0] for c in launch.call_args_list],
                         ["enable", "bootstrap", "kickstart", "disable", "bootout", "disable", "bootout"])

    def test_idle_fixtures(self):
        fixtures = json.loads((HERE / "fixtures/flash-idle.v1.json").read_text())
        for case in fixtures:
            with self.subTest(case=case["name"]):
                activity = self.directory / "last-request"
                if case["request"] is None:
                    activity.unlink(missing_ok=True)
                else:
                    activity.touch()
                    os.utime(activity, (case["request"], case["request"]))
                stop = Mock()
                self.assertEqual(flashlib.idle_stop(now=case["now"], started=lambda: case["started"], stop=stop),
                                 case["stop"])
                self.assertEqual(stop.called, case["stop"] or case["started"] is None)

    def test_request_lock_prevents_idle_stop_even_after_15_minutes(self):
        stop = Mock()
        with flashlib.activity_scope():
            self.assertFalse(flashlib.idle_stop(now=10000, started=lambda: 1, stop=stop))
        stop.assert_not_called()
        self.assertTrue((self.directory / "last-request").exists())

    def test_health_threshold_and_bound_action(self):
        self.assertTrue(flashlib.health_row(now=1801, started=lambda: 1).startswith("OK"))
        row = flashlib.health_row(now=1802, started=lambda: 1)
        self.assertTrue(row.startswith("WARN"), row)
        for text in ("owner Platform Engineer", "bin/flash-idle-stop", "verify launchctl", "auto-clear", "flash_residency"):
            self.assertIn(text, row)
        self.assertTrue(flashlib.health_row(now=1802, started=lambda: None).startswith("OK"))

    def test_health_accepts_the_named_off_switch_without_reading_process_state(self):
        (self.directory / "flash.off").touch()
        started = Mock(side_effect=AssertionError("switched-off health must not inspect launchd"))
        row = flashlib.health_row(started=started)
        self.assertTrue(row.startswith("OK"), row)
        self.assertIn("switched off (by choice)", row)
        self.assertIn("flash.off", row)
        started.assert_not_called()

    def test_flash_script_logs_the_switched_off_handoff_without_reading_inputs(self):
        (self.directory / "flash.off").touch()
        missing = self.directory / "must-not-be-read"
        script = load(HERE / "flash-script.py", "flash_script_off_test")
        script.RUNS_LOG = str(self.directory / "script-runs.jsonl")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = script.main(["fixture", str(missing), "--json"])
        row = json.loads(Path(script.RUNS_LOG).read_text())
        self.assertEqual(result, 4)
        self.assertEqual(row["handoff"], "flash is switched off")
        self.assertEqual(row["turns"], 0)
        self.assertEqual(json.loads(output.getvalue())["detail"], "flash is switched off")

    def test_log_tracks_detached_desk_inference_without_counting_shutdown_or_probes(self):
        now = datetime(2026, 10, 5, 23, 0, 0).timestamp()
        log = self.directory / "server.log"
        log.write_text("1005 22:55:00 ds4-server: chat ctx=1..2:1 TOOLS prompt start\n"
                       "1005 22:59:00 ds4-server: GET /v1/models\n"
                       "1005 23:00:00 ds4-server: kv cache stored\n")
        self.assertEqual(flashlib._activity(now), now - 300)
        stop = Mock()
        self.assertFalse(flashlib.idle_stop(now=now, started=lambda: now - 3600, stop=stop))
        stop.assert_not_called()
        self.assertTrue(flashlib.idle_stop(now=now + 600, started=lambda: now - 3600, stop=stop))

    def test_request_readiness_race_fails_once_instead_of_restarting_deadline(self):
        with patch.object(flashlib, "ensure_server") as ensure, patch.object(flashlib, "is_ready", return_value=False):
            with self.assertRaises(TimeoutError), flashlib.request_scope():
                self.fail("a request ran without readiness")
        ensure.assert_called_once()

    def test_shared_lock_contention_has_a_deadline(self):
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        with flashlib.lifecycle_lock():
            with self.assertRaises(TimeoutError), flashlib.lifecycle_lock(
                    shared=True, deadline=180, clock=lambda: elapsed[0], sleep=sleep):
                self.fail("exclusive lock was bypassed")
        self.assertEqual(elapsed[0], 180)

    def test_desk_timeout_stops_detached_desk_and_server(self):
        elapsed = [0]
        def sleep(seconds):
            elapsed[0] += seconds
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with patch.object(flashlib, "ensure_server"), patch.object(flashlib, "_stop") as stop:
            with self.assertRaises(TimeoutError):
                flashlib.ensure_desk(lambda: False, launch=launch, clock=lambda: elapsed[0], sleep=sleep)
        self.assertEqual(elapsed[0], 120)
        self.assertEqual([c.args[0] for c in launch.call_args_list], ["enable", "bootstrap", "kickstart"])
        stop.assert_called_once_with(launch=launch)

    def test_live_desk_joins_an_active_request_without_waiting_for_exclusive_lock(self):
        with flashlib.activity_scope(), patch.object(flashlib, "ensure_server"), patch.object(flashlib, "_start") as start:
            flashlib.ensure_desk(lambda: True, sleep=Mock(side_effect=AssertionError("warm desk waited")))
        start.assert_not_called()

    def test_readiness_wrapper_reserves_the_complete_cleanup_budget(self):
        with patch.object(flashlib.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaises(TimeoutError):
                flashlib.ensure_server()
        self.assertGreaterEqual(run.call_args.kwargs["timeout"], 180 + 4 * 15 + 30)

    def test_stop_disables_both_agents_and_stops_the_detached_process(self):
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with patch.object(flashlib.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as process:
            flashlib._stop(launch=launch)
        self.assertEqual([c.args[0] for c in launch.call_args_list], ["disable", "bootout", "disable", "bootout"])
        self.assertEqual(process.call_args.args[0][-2:], ["stop", "flash"])

    def test_ensure_timeout_preserves_collector_offline_observation(self):
        collector = load(HERE / "resource-collector.py", "fixture_collector")
        with patch.object(flashlib, "ensure_server", side_effect=TimeoutError("cold timeout")):
            self.assertEqual(collector.flash_server_available(), (False, None))

    def test_ensure_timeout_preserves_scorecard_error(self):
        scorecard = load(HERE.parent / "ops/jev_scorecard.py", "fixture_scorecard")
        with patch.object(flashlib, "ensure_server", side_effect=TimeoutError("cold timeout")):
            result = scorecard._chat([], endpoint=flashlib.LOCAL_URL, model="fixture", temperature=0,
                                    reasoning_effort="low", max_tokens=1, timeout=1)
        self.assertIn("TimeoutError", result["error"])

    def test_room_only_queues_flash_when_mentioned(self):
        for body, expected in (("hello", []), ("@flash-extra hi", []), ("@flash hi", ["flash"]), ("@FLASH hi", ["flash"])):
            snapshot = {"desks": {}, "last_seq": 0}
            turn = {"kind": "turn", "msg_id": "m1", "seat": "joe", "seq": 1, "body": body}
            self.assertEqual(state.route_turn(snapshot, turn, {"flash": "flash"}), expected)

    def test_ensure_timeout_preserves_direct_room_timeout(self):
        with patch.object(flashlib, "ensure_server", side_effect=TimeoutError("cold timeout")):
            # The captured production transport would be called only after readiness.
            result = flash_wire.run_turn("fixture", opener=flashlib.urllib.request.urlopen)
        self.assertEqual(result["status"], "timed_out")

    def test_installer_never_enables_model_or_desk(self):
        installer = load(HERE / "flash_install.py", "fixture_installer")
        home = self.directory / "home"
        agents = home / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        (home / ".local/bin").mkdir(parents=True)
        for label in (flashlib.SERVER_LABEL, flashlib.DESK_LABEL):
            (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}}))
        launch = Mock()
        installer.install(HERE.parent, home, launch=launch)
        self.assertEqual([c.args[0][1] for c in launch.call_args_list], ["disable", "disable", "bootout", "bootstrap", "print"])
        for call in launch.call_args_list[2:]:
            self.assertIn("com.carr.flash-idle-stop", " ".join(call.args[0]))
        for label in (flashlib.SERVER_LABEL, flashlib.DESK_LABEL):
            self.assertFalse(plistlib.loads((agents / f"{label}.plist").read_bytes())["RunAtLoad"])
            self.assertNotIn("KeepAlive", plistlib.loads((agents / f"{label}.plist").read_bytes()))
        job = plistlib.loads((agents / "com.carr.flash-idle-stop.plist").read_bytes())
        self.assertEqual(job["StartCalendarInterval"], [{"Minute": m} for m in range(0, 60, 5)])


if __name__ == "__main__":
    unittest.main()
