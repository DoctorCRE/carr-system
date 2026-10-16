#!/usr/bin/env python3
"""The nightly launcher bounds jobs and continues after an independent failure."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NightlyTests(unittest.TestCase):
    def test_a_failed_job_does_not_prevent_the_other_jobs_from_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("bin/nightly.sh", "bin/run-lock.sh", "bin/step-timeout.zsh", "bin/with-timeout.py"):
                target = root / name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(ROOT / name, target)
            (root / "out").mkdir()
            (root / ".venv/bin").mkdir(parents=True)
            (root / ".venv/bin/python").symlink_to(sys.executable)
            (root / "bin/routine-credential-env.sh").write_text(
                'carr_clear_routine_db_env() { return 0; }\ncarr_load_routine_db_env() { return 0; }\ncarr_routine_exec() { "$@"; }\n')
            (root / "bin/run-scheduled.sh").write_text(
                '#!/bin/zsh\nprint -r -- "$*" >> "$PWD/out/attempts"\nexit 7\n')
            result = subprocess.run(["/bin/zsh", str(root / "bin/nightly.sh")], cwd=root,
                                    env={"HOME": str(root), "PATH": os.environ["PATH"],
                                         "CARR_LOCK_DIR": str(root / "locks")},
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = (root / "out/attempts").read_text().splitlines()
            self.assertTrue(any("nightly.encrypted-backup" in row for row in rows))
            self.assertTrue(any("nightly.credential-health" in row for row in rows))
            self.assertTrue(any("nightly.cadence-engine" in row for row in rows))
            self.assertTrue(all("bin/with-timeout.py" in row for row in rows))
            self.assertFalse((root / "out/nightly-runs").exists())


    def test_backup_failure_pings_only_the_backup_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".config/carr").mkdir(parents=True)
            (root / ".config/carr/healthchecks.env").write_text(
                "HC_PING_BACKUP=https://example.test/backup\n"
                "HC_PING_EXPORTS=https://example.test/exports\n"
                "HC_PING_MCP=https://example.test/worker\n")
            curl = root / "curl"
            curl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CARR_TEST_CALLS"\n')
            curl.chmod(0o755)
            calls = root / "calls"
            result = subprocess.run(["/bin/zsh", str(ROOT / "bin/hc-ping.sh")],
                                    env={"HOME": str(root), "PATH": f"{root}:/usr/bin:/bin",
                                         "HC_BACKUP_RC": "7", "CARR_TEST_CALLS": str(calls)},
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(calls.read_text().splitlines()), 1)
            self.assertIn("https://example.test/backup/fail", calls.read_text())


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("bin/nightly.sh", "bin/hc-ping.sh", "bin/run-lock.sh",
                     "bin/step-timeout.zsh", "bin/with-timeout.py"):
            target = self.root / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.root / "out").mkdir()
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").symlink_to(sys.executable)
        config = self.root / ".config/carr/healthchecks.env"
        config.parent.mkdir(parents=True)
        config.write_text("".join(f"HC_PING_{key}=https://example.test/{value}\n"
                                  for key, value in (("CHAIN", "chain"), ("MCP", "worker"),
                                                     ("BACKUP", "backup"), ("EXPORTS", "exports"))))
        stub = self.root / "test-bin/curl"
        stub.parent.mkdir()
        stub.write_text('#!/bin/sh\n'
                        'printf "curl %s\\n" "$*" >> "$CARR_TEST_CALLS"\n'
                        'case "$*" in *api.doctorcre.com/health*) '
                        'printf "%s" "$CARR_TEST_HEALTH"; exit "$CARR_TEST_HEALTH_RC";; esac\n'
                        'exit "$CARR_TEST_PING_RC"\n')
        stub.chmod(0o755)
        (self.root / "bin/routine-credential-env.sh").write_text(
            'carr_clear_routine_db_env() { return 0; }\n'
            'carr_load_routine_db_env() { return 0; }\n'
            'carr_routine_exec() { "$@"; }\n')
        (self.root / "bin/run-scheduled.sh").write_text(
            '#!/bin/zsh\n'
            'key="$2"; shift 2\n'
            'print -r -- "step $key" >> "$CARR_TEST_CALLS"\n'
            'export PATH="$HOME/test-bin:$PATH"\n'
            'case "$key" in *ping) "$@"; exit $?;; esac\n'
            '[ "$key" != "$CARR_TEST_FAIL_STEP" ] || exit 7\n'
            'exit 0\n')
        self.env = {"HOME": str(self.root), "PATH": f"{stub.parent}:/usr/bin:/bin",
                    "CARR_LOCK_DIR": str(self.root / "locks"),
                    "CARR_TEST_CALLS": str(self.root / "calls"),
                    "CARR_TEST_HEALTH": '{"ok":true}', "CARR_TEST_HEALTH_RC": "0",
                    "CARR_TEST_PING_RC": "0", "CARR_TEST_FAIL_STEP": ""}

    def run_script(self, name, *args, **env):
        return subprocess.run(["/bin/zsh", str(self.root / "bin" / name), *args],
                              cwd=self.root, env=self.env | env,
                              text=True, capture_output=True, timeout=20)

    def calls(self):
        path = self.root / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def ping_urls(self):
        return [line.split()[-1] for line in self.calls()
                if line.startswith("curl ") and "example.test" in line]

    def test_nightly_reports_chain_and_mcp_only_after_all_jobs(self):
        result = self.run_script("nightly.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ping_urls(), ["https://example.test/backup",
                                          "https://example.test/chain",
                                          "https://example.test/worker"])
        rows = self.calls()
        final_job = rows.index("step nightly.pr-board")
        self.assertTrue(all(index > final_job for index, row in enumerate(rows)
                            if "example.test/chain" in row or "example.test/worker" in row))

    def test_failed_job_alarms_chain_and_later_jobs_still_run(self):
        result = self.run_script("nightly.sh", CARR_TEST_FAIL_STEP="nightly.credential-health")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step nightly.pr-board", self.calls())
        self.assertEqual(self.ping_urls(), ["https://example.test/backup",
                                          "https://example.test/chain/fail",
                                          "https://example.test/worker"])

    def test_export_retry_does_not_report_chain_or_mcp(self):
        result = self.run_script("nightly.sh", "--exports")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ping_urls(), ["https://example.test/exports"])
        self.assertFalse(any("api.doctorcre.com/health" in row for row in self.calls()))

    def test_backup_failure_alarms_backup_and_chain(self):
        result = self.run_script("nightly.sh", CARR_TEST_FAIL_STEP="nightly.encrypted-backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step nightly.pr-board", self.calls())
        self.assertEqual(self.ping_urls(), ["https://example.test/backup/fail",
                                          "https://example.test/chain/fail",
                                          "https://example.test/worker"])

    def test_ping_delivery_failure_keeps_nightly_jobs_running(self):
        result = self.run_script("nightly.sh", CARR_TEST_PING_RC="7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("step nightly.pr-board", self.calls())
        self.assertEqual(self.ping_urls(), ["https://example.test/backup",
                                          "https://example.test/chain/fail",
                                          "https://example.test/worker"])

    def test_nightly_marks_unhealthy_or_unreachable_mcp_failed(self):
        for health, health_rc in (("{\"ok\":false}", "0"), ("not json", "0"),
                                  ('{"message":"ok", "ok":false}', "0"),
                                  ('{"nested":{"ok":true},"ok":false}', "0"),
                                  ('{"ok":"true"}', "0"), ('{"ok":1}', "0"),
                                  ('{"ok":true}', "22")):
            with self.subTest(health=health, health_rc=health_rc):
                (self.root / "calls").unlink(missing_ok=True)
                result = self.run_script("hc-ping.sh", "--nightly", HC_CHAIN_RC="0",
                                         CARR_TEST_HEALTH=health, CARR_TEST_HEALTH_RC=health_rc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.ping_urls(), ["https://example.test/chain",
                                                  "https://example.test/worker/fail"])

    def test_nightly_leaves_missing_chain_outcome_late(self):
        result = self.run_script("hc-ping.sh", "--nightly")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ping_urls(), ["https://example.test/worker"])

    def test_ping_delivery_failure_does_not_hide_other_outcomes(self):
        result = self.run_script("hc-ping.sh", "--nightly", HC_CHAIN_RC="0", CARR_TEST_PING_RC="7")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("OK -> pinged", result.stdout)
        self.assertEqual(self.ping_urls(), ["https://example.test/chain",
                                          "https://example.test/worker"])


if __name__ == "__main__":
    unittest.main()
