#!/usr/bin/env python3
"""Exercise the scheduled restore report through its stdin/stdout CLI."""

from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/restore-rehearse-report.py"
PASS = """  ok    newest dump: carr-20261018.sql.age
  ok    dump is 4h old
  ok    created restore-rehearse-20261018T090000Z (br-fixture)
  restored total is 99.5% of production (floor 90%)
  RESTORE REHEARSAL: PASS
  teardown: decrypted dump removed
  teardown: branch br-fixture deleted
"""


class RestoreReportTests(unittest.TestCase):
    def run_report(self, transcript):
        return subprocess.run(
            [sys.executable, str(SCRIPT)], input=transcript,
            text=True, capture_output=True, timeout=5,
        )

    def test_complete_pass_reports_dump_percentage_and_deleted_branch(self):
        result = self.run_report(PASS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "RESTORE REHEARSAL: PASS | carr-20261018.sql.age | "
                         "restored 99.5% of production | branch br-fixture deleted\n")

    def test_only_one_exact_terminal_pass_can_report_success(self):
        for marker in ("", "VERIFY PASS", "prefix RESTORE REHEARSAL: PASS",
                       "RESTORE REHEARSAL: PASS extra",
                       "RESTORE REHEARSAL: PASS\nRESTORE REHEARSAL: PASS",
                       "RESTORE REHEARSAL: FAIL (2 assertion(s) failed)",
                       "RESTORE REHEARSAL: FAIL (1 assertion(s) failed)\n"
                       "RESTORE REHEARSAL: PASS"):
            with self.subTest(marker=marker):
                result = self.run_report(PASS.replace("RESTORE REHEARSAL: PASS", marker))
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stdout.startswith("RESTORE REHEARSAL: FAIL\n"))

    def test_pass_requires_complete_metadata_and_matching_deleted_branch(self):
        for missing in ("newest dump:", "restored total is", "created restore-rehearse-",
                        "teardown: branch"):
            with self.subTest(missing=missing):
                transcript = "\n".join(line for line in PASS.splitlines() if missing not in line)
                self.assertEqual(self.run_report(transcript).returncode, 1)
        result = self.run_report(PASS.replace("branch br-fixture deleted", "branch br-other deleted"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("branch deletion not confirmed", result.stdout)

    def test_failure_rows_are_quoted_verbatim_even_with_a_pass_marker(self):
        for row in ("  party  80  -  -   FAIL missing from the restore",
                    "  party  80  0  -80   FAIL restored EMPTY",
                    "  FAIL restored total is below the 90% floor — the dump looks truncated",
                    "  FAIL  restored database does not equal the artifact (see MISMATCH lines)",
                    "  MISMATCH party: artifact=80 restored=79",
                    "REHEARSAL FAILED: could not create the rehearsal branch"):
            with self.subTest(row=row):
                result = self.run_report(row + "\n" + PASS)
                self.assertEqual(result.returncode, 1)
                self.assertIn(row + "\n", result.stdout)

    def test_preflight_failure_is_a_reported_failure_without_terminal_marker(self):
        result = self.run_report("REHEARSAL FAILED: no fixture dump exists\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("REHEARSAL FAILED: no fixture dump exists\n", result.stdout)

    def test_cannot_run_preflight_diagnostic_survives(self):
        diagnostic = "REHEARSAL CANNOT RUN: no age private key at /synthetic/identity"
        result = self.run_report(diagnostic + "\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn(diagnostic + "\n", result.stdout)

    def test_unterminated_transcript_cannot_report_pass(self):
        for transcript in (PASS[:-1], PASS + "  evidence: recor"):
            with self.subTest(transcript=transcript):
                result = self.run_report(transcript)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertTrue(result.stdout.startswith("RESTORE REHEARSAL: FAIL\n"))
                self.assertIn("missing final newline", result.stdout)

    def test_assertion_verdict_is_reported_verbatim(self):
        marker = "RESTORE REHEARSAL: FAIL (3 assertion(s) failed)"
        result = self.run_report(PASS.replace("RESTORE REHEARSAL: PASS", marker))
        self.assertEqual(result.returncode, 1)
        self.assertIn(marker + "\n", result.stdout)

    def test_evidence_warning_retains_the_failed_recording_detail(self):
        warning = ("  EVIDENCE WARNING: the rehearsal result above is unaffected, but it\n"
                   "  was NOT recorded to ops.run — synthetic recording refusal\n")
        result = self.run_report(PASS + warning)
        self.assertEqual(result.returncode, 0)
        self.assertIn(warning, result.stdout)

    def test_expected_snapshot_drift_does_not_fail_but_warnings_survive(self):
        warning = "  WARN  newest dump is 30h old (>26h). The nightly chain is"
        transcript = ("  NOTE: 2 migration(s) were applied AFTER this dump was taken.\n"
                      "  party  80  -  -   expected: table postdates this dump\n"
                      "  lead  3  0  -3   expected: rows postdate this dump\n"
                      "  client  3  2  -1   (drift — the dump is a snapshot; production moved)\n"
                      + warning + "\n" + PASS)
        result = self.run_report(transcript)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(warning + "\n", result.stdout)

    def test_cleanup_failure_or_kept_branch_cannot_report_pass(self):
        for teardown in ("  teardown: KEEPING branch br-fixture (--keep-branch).",
                         "  teardown: COULD NOT DELETE branch br-fixture — delete it by hand:"):
            with self.subTest(teardown=teardown):
                result = self.run_report(PASS.replace("  teardown: branch br-fixture deleted", teardown))
                self.assertEqual(result.returncode, 1)
                self.assertIn(teardown + "\n", result.stdout)

    def test_selected_dump_variants_report_only_the_filename(self):
        for selected in ("selected dump: /tmp/fixture/carr-20261018.sql.age (--dump, an explicit path rather than backups/)",
                         "selected dump: carr-20261018.sql.age (--date 20261018)",
                         "selected dump: carr-20261018.sql.age (--backup-run-id 123, fetched from the artifact store)"):
            with self.subTest(selected=selected):
                result = self.run_report(PASS.replace("newest dump: carr-20261018.sql.age", selected))
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn(" | carr-20261018.sql.age | ", result.stdout)

    def test_ambiguous_metadata_cannot_report_success(self):
        for duplicate in ("  ok    newest dump: carr-other.sql.age\n",
                          "  restored total is 95.0% of production (floor 90%)\n",
                          "  ok    created restore-rehearse-other (br-other)\n"):
            with self.subTest(duplicate=duplicate):
                self.assertEqual(self.run_report(duplicate + PASS).returncode, 1)

    def test_prompt_pipeline_includes_stderr_and_rejects_nonzero_producer(self):
        prompt = (REPO / "ops/scheduled-tasks/restore-rehearse-weekly.SKILL.md").read_text()
        command = re.search(r"```(?:bash|sh)\n(.+)\n```", prompt)
        self.assertIsNotNone(command, "the scheduled prompt must carry one executable pipeline")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "tools/restore-rehearse-report.py").symlink_to(SCRIPT)
            producer = root / "run.sh"
            pipeline = command[1].replace("~/carr-system", shlex.quote(directory))
            for transcript, status, expected in ((PASS, 0, 0), (PASS, 73, 1),
                                                 (PASS, 137, 1), (PASS[:-1], 0, 1), ("", 0, 1),
                                                 ("REHEARSAL FAILED: synthetic preflight refusal\n", 1, 1)):
                with self.subTest(transcript=transcript, status=status):
                    producer.write_text(f'#!/bin/sh\ncat transcript >&2\nexit {status}\n')
                    producer.chmod(0o755)
                    (root / "transcript").write_text(transcript)
                    result = subprocess.run(["/bin/sh", "-c", pipeline], text=True,
                                            capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    verdict = "PASS |" if expected == 0 else "FAIL\n"
                    self.assertTrue(result.stdout.startswith("RESTORE REHEARSAL: " + verdict), result.stdout)
                    if status:
                        self.assertIn(f"producer exited {status}", result.stdout)


if __name__ == "__main__":
    unittest.main()
