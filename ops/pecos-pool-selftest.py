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
        (self.script.parent / "_data/pecos-source-2026Q3.json").unlink()
        self.write_source(headers=["NPI", "ENRLMT_ID", "STATE", "FIRST_NAME", "LAST_NAME", "PROVIDER_TYPE_DESC"])
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PECOS source schema drift", result.stderr)
        self.assertEqual(self.output("pecos.json").read_bytes(), before)

    def interrupt_import(self):
        rows = [
            [str(1234567890 + index), "N", "1", f"I2025010{index + 1}000100", "01",
             "PRACTITIONER - Dentist", state, name, "", "Example", ""]
            for index, (state, name) in enumerate((("FL", "Ava"), ("GA", "Bo"), ("AL", "Cal")))
        ]
        self.write_source(rows=rows)
        result = subprocess.run([sys.executable, "-c", """
import runpy, sys
scope = runpy.run_path(sys.argv[1])
scope = scope["main"].__globals__
scope["CHECKPOINT_INTERVAL"] = 1
save = scope["save_checkpoint"]
def interrupt(*args):
    save(*args)
    if args[2] == 2:
        raise SystemExit(75)
scope["save_checkpoint"] = interrupt
sys.argv = sys.argv[1:]
scope["main"]()
""", str(self.script), str(self.source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 75, result.stderr)
        checkpoint = self.script.parent / "_data/pecos-checkpoint-2026Q3.json"
        self.assertEqual(json.loads(checkpoint.read_text())["processed_rows"], 2)
        self.assertFalse(self.output("pecos.json").exists())
        return checkpoint

    def test_resume_skips_saved_rows_and_completes_both_outputs_once(self):
        checkpoint = self.interrupt_import()
        result = subprocess.run([sys.executable, "-c", """
import runpy, sys
scope = runpy.run_path(sys.argv[1])["main"].__globals__
decode = scope["enrl_date"]
def reject_saved_row(value):
    if value == "I20250101000100":
        raise AssertionError("saved row was processed again")
    return decode(value)
scope["enrl_date"] = reject_saved_row
sys.argv = sys.argv[1:]
scope["main"]()
""", str(self.script), str(self.source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3 rows scanned, 2 FL/AL", result.stdout)
        candidates = json.loads(self.output("pecos.json").read_text())
        self.assertEqual([row["name"] for row in candidates], ["Cal Example", "Ava Example"])
        data = self.script.parent / "_data"
        self.assertEqual(json.loads((data / "pecos-baseline-2026Q3.json").read_text()),
                         ["1234567890|I20250101000100", "1234567892|I20250103000100"])
        self.assertEqual(json.loads((data / "pecos-source-2026Q3.json").read_text())["rows_scanned"], 3)
        self.assertFalse(checkpoint.exists())

    def test_resume_refuses_changed_source_and_preserves_checkpoint(self):
        checkpoint = self.interrupt_import()
        before = checkpoint.read_bytes()
        self.write_source()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PECOS checkpoint belongs to different source content", result.stderr)
        self.assertEqual(checkpoint.read_bytes(), before)
        self.assertFalse(self.output("pecos.json").exists())
        self.assertFalse((self.script.parent / "_data/pecos-source-2026Q3.json").exists())

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
