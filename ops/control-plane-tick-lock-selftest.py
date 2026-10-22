#!/usr/bin/env python3
"""Exercise the tick wrapper with real processes and synthetic local files."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import job_watchdog as watchdog


class TickLockTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.wrapper = self.root / "bin/control-plane-tick.sh"
        self.wrapper.parent.mkdir()
        shutil.copy2(ROOT / "bin/control-plane-tick.sh", self.wrapper)
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text(f"#!{sys.executable}\n"
            "import pathlib, sys, time\n"
            f"root = pathlib.Path({self.directory!r})\n"
            "(root / 'started').touch()\n"
            "(root / 'arguments').write_text(' '.join(sys.argv[1:]))\n"
            "while not (root / 'finish').exists(): time.sleep(0.01)\n")
        python.chmod(0o700)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": self.directory,
                    "CARR_DB_JOBS_URL": "postgresql://synthetic.invalid/jobs"}

    def invoke(self, *args):
        return subprocess.run(["/bin/zsh", str(self.wrapper), *args], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def start(self):
        process = subprocess.Popen(["/bin/zsh", str(self.wrapper)], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not (self.root / "started").exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(process.communicate()[1])
            time.sleep(0.01)
        self.assertTrue((self.root / "started").exists())
        return process

    def test_crashed_worker_releases_lock_and_overlap_logs_once(self):
        process = self.start()
        skips = [self.invoke() for _ in range(100)]
        self.assertTrue(all(skip.returncode == 0 for skip in skips), [(s.returncode, s.stderr) for s in skips if s.returncode])
        self.assertEqual(sum("already active" in skip.stderr for skip in skips), 1)
        marker = self.root / "out/control-plane-tick.lock/overlap"
        self.assertTrue(marker.is_dir())
        process.kill()
        process.communicate(timeout=5)
        (self.root / "finish").touch()
        resumed = self.invoke()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn("overlap cleared", resumed.stderr)
        self.assertFalse(marker.exists())

    def test_concurrent_wakes_have_one_worker(self):
        processes = [subprocess.Popen(["/bin/zsh", str(self.wrapper)], env=self.env,
                     text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(12)]
        try:
            deadline = time.monotonic() + 5
            while (sum(p.poll() is None for p in processes) > 1 or not (self.root / "started").exists()) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(sum(p.poll() is None for p in processes), 1)
            self.assertTrue((self.root / "started").exists())
        finally:
            (self.root / "finish").touch()
            for process in processes:
                process.communicate(timeout=5)

    def test_wrapper_forwards_revival_preview_and_run_limit(self):
        (self.root / "finish").touch()
        result = self.invoke("--dry-run", "--max-jobs", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "arguments").read_text().endswith(
            "tick --mode auto --dry-run --max-jobs 1"))

    def test_abandoned_directory_does_not_skip_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "tick.lock"
            lock.mkdir()
            result = subprocess.run(["/bin/zsh", str(ROOT / "bin/control-plane-tick.sh")],
                env={"PATH": "/usr/bin:/bin", "HOME": directory,
                     "CARR_CONTROL_PLANE_LOCK_DIR": str(lock)},
                text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 78, result.stderr)
            self.assertIn("CARR_DB_JOBS_URL is required", result.stderr)


class TickWatchdogTests(unittest.TestCase):
    def test_bad_overlap_evidence_cannot_clear_a_finding(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["repositories"] = []
        config["paths"].update(merge_queue="queue", queue_logs=[], release_log="release")
        with tempfile.TemporaryDirectory() as directory, patch("job_watchdog.sys.platform", "linux"):
            root = Path(directory)
            marker = root / "out/control-plane-tick.lock/overlap"
            marker.parent.mkdir(parents=True)
            marker.write_text("invalid marker")
            found = watchdog.detect(watchdog.collect(root, config, 701), config, 701)
            self.assertEqual([f["kind"] for f in found], ["collection_error"])
            self.assertEqual(found[0]["blinds"], ["control_plane_tick_skipped"])
            effects = Mock()
            effects.report.return_value = {}
            prior = watchdog.detect({"control_plane_tick": 100}, config, 701)
            watchdog.reconcile(root, config, prior, effects, 701)
            watchdog.reconcile(root, config, found, effects, 702)
            latest = watchdog.read_latest(root / config["paths"]["findings"])
            self.assertIsNone(latest[prior[0]["key"]].get("cleared_at"))

    def test_persistent_skips_become_a_finding_and_clear_on_recovery(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["repositories"] = []
        config["paths"].update(merge_queue="queue", queue_logs=[], release_log="release")
        with tempfile.TemporaryDirectory() as directory, patch("job_watchdog.sys.platform", "linux"):
            root = Path(directory)
            marker = root / "out/control-plane-tick.lock/overlap"
            marker.mkdir(parents=True)
            os.utime(marker, (100, 100))
            self.assertEqual(watchdog.detect(watchdog.collect(root, config, 700), config, 700), [])
            found = watchdog.detect(watchdog.collect(root, config, 701), config, 701)
            self.assertEqual([f["kind"] for f in found], ["control_plane_tick_skipped"])
            self.assertIn("10 minutes", found[0]["reason"])
            effects = Mock()
            effects.report.return_value = {}
            watchdog.reconcile(root, config, found, effects, 701)
            marker.rmdir()
            watchdog.reconcile(root, config,
                watchdog.detect(watchdog.collect(root, config, 702), config, 702), effects, 702)
            latest = watchdog.read_latest(root / config["paths"]["findings"])
            self.assertIsNotNone(latest[found[0]["key"]]["cleared_at"])


if __name__ == "__main__":
    unittest.main()
