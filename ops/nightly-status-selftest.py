#!/usr/bin/env python3
"""Nightly exit status agrees with synthetic child outcomes in both modes."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NightlyStatusTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ("bin/nightly.sh", "bin/run-lock.sh", "bin/step-timeout.zsh", "ops/nightly-log.py"):
            target = self.root / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.root / "outcomes").mkdir()
        (self.root / "bin/routine-credential-env.sh").write_text(
            'carr_clear_routine_db_env() { return 0; }\n'
            'carr_load_routine_db_env() { return 0; }\n'
            'carr_routine_exec() { CARR_TEST_CLEAN=1 "$@"; }\n')
        (self.root / "bin/run-scheduled.sh").write_text(
            '#!/bin/zsh\n[ "${CARR_TEST_CLEAN:-}" = 1 ] || exit 91\n'
            'key="$2"; shift 2\nrc=0\n'
            '[ ! -f "$HOME/outcomes/$key" ] || rc="$(cat "$HOME/outcomes/$key")"\n'
            'print -r -- "$key $rc" >> "$HOME/receipts"\n'
            'print -r -- "child-output $key"\n'
            'print -ru2 -- "child-error $key"\n'
            'print -r -- "$*" >> "$HOME/commands"\n'
            'for arg in "$@"; do\n'
            '  case "$arg" in HC_CHAIN_RC=*|HC_EXPORTS_RC=*|HC_BACKUP_RC=*)\n'
            '    print -r -- "$arg" >> "$HOME/pings";; esac\n'
            'done\nexit "$rc"\n')

    def run_nightly(self, outcomes, *args):
        for key, rc in outcomes.items():
            (self.root / "outcomes" / f"nightly.{key}").write_text(str(rc))
        result = subprocess.run(
            ["/bin/zsh", str(self.root / "bin/nightly.sh"), *args],
            cwd=self.root,
            env={"HOME": str(self.root), "PATH": "/usr/bin:/bin",
                 "CARR_LOCK_DIR": str(self.root / "locks")},
            text=True, capture_output=True, timeout=20)
        receipts = (self.root / "receipts").read_text().splitlines()
        self.assertFalse(list((self.root / "locks").glob("*.lock")))
        return result, receipts

    def test_active_run_prevents_duplicate_children_and_logs(self):
        lock = self.root / "locks/carr-nightly-record-layer.lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(str(os.getpid()))
        result = subprocess.run(
            ["/bin/zsh", str(self.root / "bin/nightly.sh")], cwd=self.root,
            env={"HOME": str(self.root), "PATH": "/usr/bin:/bin",
                 "CARR_LOCK_DIR": str(self.root / "locks")},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOCKED", result.stdout)
        self.assertFalse((self.root / "receipts").exists())
        self.assertFalse((self.root / "out/nightly-runs").exists())
        self.assertTrue(lock.exists())

    def test_run_log_names_failed_step_preserves_output_and_later_steps(self):
        result, _ = self.run_nightly({"recovery-point": 7, "verb-probe": 78})
        self.assertEqual(result.returncode, 7, result.stderr)
        logs = list((self.root / "out/nightly-runs").glob("nightly-*.log"))
        self.assertEqual(len(logs), 1)
        content = logs[0].read_text()
        self.assertRegex(content, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z  START recovery point")
        self.assertIn("child-output nightly.recovery-point", content)
        self.assertIn("child-error nightly.recovery-point", content)
        self.assertIn("FAIL recovery point (exit 7)", content)
        self.assertIn("SKIP verb probe (exit 78)", content)
        self.assertIn("OK nightly ping (exit 0)", content)
        self.assertIn("nightly result: chain_failed (exit 7)", content)
        self.assertEqual(content, (self.root / "out/nightly.log").read_text())

    def test_each_invocation_keeps_a_distinct_log_in_both_modes(self):
        for args in ((), (), ("--exports",)):
            result, _ = self.run_nightly({}, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        logs = list((self.root / "out/nightly-runs").glob("*.log"))
        self.assertEqual(len(logs), 3)
        self.assertEqual(len([p for p in logs if p.name.startswith("nightly-exports-")]), 1)
        for path in logs:
            self.assertIn("nightly result: chain_ok (exit 0)", path.read_text())

    def test_early_configuration_failure_keeps_a_final_run_receipt(self):
        (self.root / "bin/routine-credential-env.sh").write_text(
            'carr_clear_routine_db_env() { return 0; }\n'
            'carr_load_routine_db_env() { return 78; }\n')
        result = subprocess.run(
            ["/bin/zsh", str(self.root / "bin/nightly.sh")], cwd=self.root,
            env={"HOME": str(self.root), "PATH": "/usr/bin:/bin",
                 "CARR_LOCK_DIR": str(self.root / "locks")},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 78, result.stderr)
        logs = list((self.root / "out/nightly-runs").glob("nightly-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("nightly result: chain_skipped (exit 78)", logs[0].read_text())
        self.assertFalse((self.root / "receipts").exists())

    def test_failed_nightly_remains_failed_after_reporter_failure(self):
        result, receipts = self.run_nightly({"recovery-point": 7, "nightly-ping": 9})
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("nightly.recovery-point 7", receipts)
        self.assertIn("nightly.verb-probe 0", receipts)
        self.assertIn("nightly.nightly-ping 9", receipts)
        self.assertIn("HC_CHAIN_RC=7", (self.root / "pings").read_text())


    def test_checklist_reminder_failure_is_reported_without_suppressing_later_jobs(self):
        result, receipts = self.run_nightly({'deal-checklist-reminders': 1})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('nightly.deal-checklist-reminders 1', receipts)
        self.assertIn('./.venv/bin/python tools/deal-checklist-reminders.py --apply',
                      (self.root / 'commands').read_text().splitlines())
        self.assertIn('nightly.verb-probe 0', receipts)
        self.assertIn('HC_CHAIN_RC=1', (self.root / 'pings').read_text())

    def test_export_failure_remains_visible_after_later_success(self):
        result, receipts = self.run_nightly({"exports": 7}, "--exports")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("nightly.exports 7", receipts)
        self.assertIn("nightly.system-graph 0", receipts)
        self.assertIn("HC_EXPORTS_RC=7", (self.root / "pings").read_text())

    def test_expected_skips_do_not_fail_either_chain(self):
        for args, skipped, last in (((), "encrypted-backup", "nightly-ping"),
                                    (("--exports",), "exports", "system-graph")):
            with self.subTest(args=args):
                result, receipts = self.run_nightly({skipped: 78}, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"nightly.{skipped} 78", receipts)
                self.assertIn(f"nightly.{last} 0", receipts)

    def test_timeout_is_not_overwritten_by_later_failure(self):
        for rc in (124, 137):
            with self.subTest(rc=rc):
                result, receipts = self.run_nightly(
                    {"recovery-point": rc, "cadence-engine": 7, "nightly-ping": 9})
                self.assertEqual(result.returncode, rc, result.stderr)
                self.assertIn("nightly.verb-probe 0", receipts)
                self.assertIn(f"HC_CHAIN_RC={rc}", (self.root / "pings").read_text())

    def test_skip_after_failure_does_not_hide_failure(self):
        result, receipts = self.run_nightly({"recovery-point": 7, "verb-probe": 78})
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("nightly.verb-probe 78", receipts)
        self.assertIn("HC_CHAIN_RC=7", (self.root / "pings").read_text())

    def test_reporter_failure_alone_fails_the_chain(self):
        result, receipts = self.run_nightly({"nightly-ping": 9})
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn("nightly.verb-probe 0", receipts)
        self.assertIn("HC_CHAIN_RC=0", (self.root / "pings").read_text())

    def test_successful_chain_stays_successful(self):
        result, receipts = self.run_nightly({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nightly.verb-probe 0", receipts)


class LocalBriefStatusTests(unittest.TestCase):
    def test_missing_configuration_is_skipped_without_running_children(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "carr-system/out").mkdir(parents=True)
            result = subprocess.run(
                ["/bin/zsh", str(ROOT / "bin/local-briefs.sh")],
                env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 78, result.stderr)
            self.assertIn("SKIP", (home / "carr-system/out/local-briefs.log").read_text())


if __name__ == "__main__":
    unittest.main()
