#!/usr/bin/env python3
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pipelines/radar/build-pecos-pool.py"


class PecosPoolCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "pipelines/radar/build-pecos-pool.py"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, self.script)
        (self.script.parent / "upstream").mkdir()
        (self.script.parent / "_data").mkdir()
        self.source = self.root / "PPEF_2026.10.01_Enrollment_Extract.csv"

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, headers=None, rows=None):
        headers = headers or ["NPI", "MULTIPLE_NPI_FLAG", "PECOS_ASCT_CNTL_ID", "ENRLMT_ID", "PROVIDER_TYPE_CD", "PROVIDER_TYPE_DESC", "STATE_CD", "FIRST_NAME", "MDL_NAME", "LAST_NAME", "ORG_NAME"]
        rows = rows or [["1234567890", "N", "1", "I20250102000100", "01", "PRACTITIONER - Dentist", "FL", "Ava", "", "Example", ""]]
        with self.source.open("w", newline="", encoding="latin1") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)

    def run_cli(self):
        return subprocess.run([sys.executable, str(self.script), str(self.source)], capture_output=True, text=True)

    def output(self, name):
        return self.script.parent / "upstream" / name

    def test_quarterly_source_creates_the_expected_synthetic_candidate(self):
        self.write_source()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(self.output("pecos.json").read_text())
        self.assertEqual([(row["name"], row["state"], row["profession"]) for row in rows], [("Ava Example", "FL", "Dentist")])
        self.assertEqual(rows[0]["detail"].count("1234567890"), 1)

    def test_schema_drift_does_not_replace_a_previous_candidate_pool(self):
        self.write_source()
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.output("pecos.json").read_bytes()
        self.write_source(headers=["NPI", "ENRLMT_ID", "STATE", "FIRST_NAME", "LAST_NAME", "PROVIDER_TYPE_DESC"])
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output("pecos.json").read_bytes(), before)

    def test_extract_dates_preserve_the_existing_data_quarter_labels(self):
        for extract, quarter in (("2026.04.01", "2026Q1"), ("2026.01.01", "2025Q4")):
            with self.subTest(extract=extract):
                self.source = self.root / f"PPEF_{extract}_Enrollment_Extract.csv"
                self.write_source()
                result = self.run_cli()
                self.assertEqual(result.returncode, 0, result.stderr)
                baseline = self.script.parent / "_data" / f"pecos-baseline-{quarter}.json"
                self.assertTrue(baseline.exists(), result.stdout)
                self.assertEqual(json.loads(baseline.read_text()), ["1234567890|I20250102000100"])

    def test_replaying_the_same_quarter_with_changed_content_is_refused(self):
        self.write_source()
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.output("pecos.json").read_bytes()
        self.write_source(rows=[["1234567890", "N", "1", "I20250102000100", "01", "PRACTITIONER - Dentist", "AL", "Ava", "", "Example", ""]])
        replay = self.run_cli()
        self.assertNotEqual(replay.returncode, 0)
        self.assertEqual(self.output("pecos.json").read_bytes(), before)

    def test_replaying_an_identical_quarter_is_idempotent(self):
        self.write_source()
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.output("pecos.json").read_bytes()
        replay = self.run_cli()
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertIn("already processed", replay.stdout)
        self.assertEqual(self.output("pecos.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
