#!/usr/bin/env python3
"""Behavioral regression coverage for bounded OneDrive recovery and reporting."""
import errno
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exporters import common, run_exports


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("vendors.xlsx")
        self.clock = Clock()
        self.launches = []
        self.probes = []

    def cold(self, paths, *, timeout):
        return [(p, OSError(errno.EDEADLK, "cold")) for p in paths]

    def running(self, *, timeout):
        self.probes.append(timeout)
        return False

    def launch(self, *, timeout):
        self.launches.append(timeout)
        return True

    def wait(self, **kwargs):
        defaults = dict(budget_seconds=1, poll_seconds=.25,
                        sleep=self.clock.sleep, monotonic=self.clock.monotonic,
                        probe=self.cold, running=self.running, launch=self.launch)
        defaults.update(kwargs)
        with redirect_stderr(io.StringIO()):
            return common.wait_for_provider([self.path], **defaults)

    def test_1_updater_is_not_a_provider_with_actual_pgrep(self):
        identity = "/Applications/OneDrive.app/Contents/MacOS/OneDriveUpdater"
        child = subprocess.Popen(["/bin/bash", "-c", 'exec -a "$1" sleep 20', "fixture", identity])
        try:
            time.sleep(.05)
            def census(command, **kwargs):
                done = subprocess.run(command, **kwargs)
                found = str(child.pid) in done.stdout.split()
                return subprocess.CompletedProcess(command, 0 if found else 1,
                                                   str(child.pid) if found else "")
            self.assertIs(common.provider_running(run=census), False)
            for pattern in common.PROVIDER_PROCESS_PATTERNS:
                self.assertIsNone(re.search(pattern, identity))
            self.assertTrue(any(re.search(p, identity.removesuffix("Updater"))
                                for p in common.PROVIDER_PROCESS_PATTERNS))
            for pattern in common.PROVIDER_PROCESS_PATTERNS:
                self.assertIsNone(re.search(pattern, identity + " --previous " + identity.removesuffix("Updater")))
        finally:
            child.terminate()
            child.wait(timeout=2)

    def test_2_probe_errors_and_timeout_are_unknown_and_bounded(self):
        for code in (2, 3):
            calls = []
            def run(command, **kwargs):
                calls.append(kwargs)
                return subprocess.CompletedProcess(command, code, "")
            self.assertIsNone(common.provider_running(run=run))
            self.assertTrue(all(0 < c["timeout"] <= common.PROVIDER_PROCESS_TIMEOUT_SECONDS
                                for c in calls))
        def stalled(*args, **kwargs):
            raise subprocess.TimeoutExpired("pgrep", kwargs.get("timeout", 1))
        self.assertIsNone(common.provider_running(run=stalled))
        result = self.wait(running=lambda **kw: common.provider_running(run=stalled, **kw))
        self.assertTrue(result.cold)
        self.assertIsNone(result.provider_state)
        self.assertEqual(self.launches, [])
        self.assertIn("unknown", result.diagnostic())

    def main_with(self, wait):
        ran = []
        log = io.StringIO()
        with patch.object(run_exports, "LIVE", True), \
             patch.object(run_exports, "TARGETS", {"first": ("a.json", None), "second": ("b.json", None)}), \
             patch.object(run_exports, "md_renders_retired", return_value=False), \
             patch.object(run_exports, "wait_for_provider", side_effect=wait), \
             patch.object(run_exports, "run_export", side_effect=lambda k, *a, **kw: ran.append(k) or True), \
             patch.object(sys, "argv", ["run_exports"]), redirect_stderr(log), redirect_stdout(log):
            with self.assertRaises(SystemExit) as exited:
                run_exports.main()
            self.assertEqual(exited.exception.code, 0)
        self.assertEqual(ran, ["first", "second"])
        return log.getvalue()

    def test_2_main_continues_after_unknown_probe(self):
        def timeout(**kw):
            raise subprocess.TimeoutExpired("pgrep", .01)
        def wait(paths):
            return self.wait(running=timeout)
        self.assertIn("unknown", self.main_with(wait))

    def test_3_wedged_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "wedged.xlsx"
            os.mkfifo(fifo)
            started = time.monotonic()
            cold = common.probe_provider_files([fifo], timeout=.15)
            # Process startup may consume the allowance on a loaded runner.
            # This integration check proves that a blocked read is reaped;
            # recovery's remaining-budget decisions use a controlled clock.
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(cold[0][0], fifo)
            self.assertEqual(cold[0][1].errno, errno.ETIMEDOUT)

    def test_3_timed_out_read_reserves_time_for_recovery(self):
        def wedged(paths, *, timeout):
            self.clock.now += timeout
            return [(p, OSError(errno.ETIMEDOUT, "wedged read")) for p in paths]

        result = self.wait(probe=wedged)
        self.assertEqual(result.cold[0][1].errno, errno.ETIMEDOUT)
        self.assertTrue(self.probes)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.clock.now, 1)
        self.main_with(lambda paths: result)

    def test_3_warm_and_missing_files_do_not_probe_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            warm = Path(directory) / "warm.xlsx"
            warm.write_bytes(b"warm")
            with redirect_stderr(io.StringIO()):
                result = common.wait_for_provider([warm, warm.with_name("missing")],
                                                 running=self.running, launch=self.launch)
            self.assertEqual(result.cold, [])
            self.assertEqual(self.probes, [])
            self.assertEqual(self.launches, [])

    def test_4_latency_is_charged_for_success_and_failed_launch(self):
        for success in (True, False):
            self.clock = Clock()
            def slow_launch(*, timeout):
                self.assertLessEqual(timeout, 1)
                self.clock.now += timeout
                return success
            result = self.wait(launch=slow_launch)
            self.assertEqual(self.clock.now, 1)
            self.assertEqual(self.clock.sleeps, [])
            self.assertIs(result.launch_succeeded, success)
        self.clock = Clock()
        def slow_probe(*, timeout):
            self.clock.now += timeout
            return False
        self.wait(running=slow_probe)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.launches, [])

    def test_4_expired_budget_never_launches(self):
        result = self.wait(budget_seconds=0)
        self.assertTrue(result.cold)
        self.assertEqual(self.probes, [])
        self.assertEqual(self.launches, [])

    def test_5_permission_error_has_no_invented_cause_or_launch(self):
        result = self.wait(probe=lambda paths, **kw: [(self.path, PermissionError(errno.EACCES, "denied"))])
        self.assertEqual(self.launches, [])
        self.assertEqual(self.probes, [])
        log = self.main_with(lambda paths: result)
        self.assertIn("EACCES", log)
        for invented in ("cloud-only", "free disk", "did not take", "pin the"):
            self.assertNotIn(invented, log)

    def test_5_transition_keeps_observed_launch_facts(self):
        states = iter([False, True, True, True])
        result = self.wait(running=lambda **kw: next(states))
        self.assertEqual(len(self.launches), 1)
        self.assertIs(result.provider_state, True)
        log = self.main_with(lambda paths: result)
        self.assertIn("launch attempt issued", log)
        self.assertIn("last observed up", log)
        self.assertNotIn("did not take", log)

    def test_recovery_can_make_a_cold_file_readable(self):
        result = self.wait(probe=lambda paths, **kw: [] if self.launches else self.cold(paths, **kw))
        self.assertEqual(result.cold, [])
        self.assertEqual(len(self.launches), 1)

    def test_launch_failure_never_raises_and_obeys_timeout(self):
        def failed(*args, **kw):
            self.assertEqual(kw["timeout"], .2)
            raise subprocess.TimeoutExpired("open", .2)
        self.assertIs(common.start_provider(run=failed, timeout=.2), False)

    def test_missing_pgrep_reports_unknown(self):
        with patch.object(common.shutil, "which", return_value=None):
            self.assertIsNone(common.provider_running())


if __name__ == "__main__":
    unittest.main(verbosity=2)
