#!/usr/bin/env python3
"""Verify normal nightly matching with synthetic rows and no database access."""
import contextlib
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class AvailabilityMatcherTests(unittest.TestCase):
    def setUp(self):
        self.psycopg = types.ModuleType("psycopg")
        self.psycopg.errors = types.SimpleNamespace(InsufficientPrivilege=PermissionError)
        with patch.dict(sys.modules, {"psycopg": self.psycopg}):
            self.matcher = runpy.run_path(str(ROOT / "pipelines/availability_matcher.py"))

    def test_normal_filters_and_uncomparable_rates(self):
        match = self.matcher["matches"]
        row = dict(status="available", area=1500, city="Austin", state="TX",
                   sub_type="office", rate_norm=25)
        spec = dict(min_sf=1000, max_sf=2000, cities=["austin"], states="tx",
                    sub_types="Office", max_rate_sf_yr=30)
        self.assertTrue(match(spec, row)[0])
        for key, value in (("area", 500), ("status", "leased"), ("city", "Dallas"),
                           ("rate_norm", None), ("rate_norm", 35)):
            with self.subTest(key=key, value=value):
                self.assertFalse(match(spec, {**row, key: value})[0])

    def test_default_status_and_explicit_status(self):
        row = {"status": "leased"}
        self.assertFalse(self.matcher["matches"]({}, row)[0])
        self.assertTrue(self.matcher["matches"]({"statuses": ["leased"]}, row)[0])

    def run_matcher(self, args=(), *, empty=False):
        statements = []
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def execute(self, sql):
                statements.append(sql)
                if "count(*)" in sql:
                    return types.SimpleNamespace(fetchone=lambda: (0 if empty else 1,))
                if "from space_search s" in sql:
                    rows = [("s1", {"min_sf": 1000, "unknown_filter": True}, "C1", "Test Client")]
                else:
                    rows = [("a1", "available", 25, False, None, "2026-01-01", "fixture",
                             1500, "100", "Austin", "TX", "office", "Test Address", "Test Building")]
                return types.SimpleNamespace(fetchall=lambda: rows)
        self.psycopg.connect = lambda _: Connection()
        with tempfile.TemporaryDirectory() as tmp:
            main = self.matcher["main"]
            with (
                patch.dict(main.__globals__, {"OUT": Path(tmp), "REPORT": Path(tmp) / "report.md"}),
                patch.dict("os.environ", {"DATABASE_URL": "synthetic-fixture"}, clear=True),
                patch.object(sys, "argv", ["availability_matcher.py", *args]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), 0)
                return (Path(tmp) / "report.md").read_text(), statements

    def test_normal_query_joins_and_report(self):
        report, sql = self.run_matcher()
        self.assertIn("**1** · matches: **1**", report)
        self.assertIn("C1 Test Client", report)
        self.assertIn("`unknown_filter`", report)
        self.assertTrue(any("distinct on (av.space_id)" in query and "av.id desc" in query for query in sql))
        self.assertTrue(any("join client" in query and "join party" in query for query in sql))
        self.assertTrue(any("join space" in query and "join building" in query for query in sql))

    def test_all_statuses_keeps_history_query(self):
        _, sql = self.run_matcher(["--all-statuses"])
        self.assertFalse(any("distinct on" in query for query in sql))

    def test_empty_tables_still_produce_an_honest_report(self):
        report, _ = self.run_matcher(empty=True)
        self.assertIn("0 availabilities on record", report)

    def test_retired_canary_is_not_advertised_by_matcher(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["availability_matcher.py", "--help"]), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                self.matcher["main"]()
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("--canary", output.getvalue())

    def test_nightly_rejects_unknown_options_before_running_jobs(self):
        # Never invoke the old --canary branch: it enumerates the environment.
        result = subprocess.run(["/bin/zsh", str(ROOT / "bin/nightly.sh"), "--unknown"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 64)
        self.assertNotIn("--canary", result.stderr)

    def test_retired_canary_option_is_rejected_before_any_io(self):
        with patch.object(sys, "argv", ["availability_matcher.py", "--canary"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.matcher["main"]()
        self.assertEqual(raised.exception.code, 2)
        result = subprocess.run(["/bin/zsh", str(ROOT / "bin/nightly.sh"), "--canary"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 64)


if __name__ == "__main__":
    unittest.main()
