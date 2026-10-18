#!/usr/bin/env python3
"""Completed runs produce one duration report without a monitoring loop."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("durations", Path(__file__).with_name("build-duration-check.py"))
assert spec is not None and spec.loader is not None
durations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(durations)


class DurationTests(unittest.TestCase):
    def test_only_slow_completed_runs_are_reported(self):
        rows = [{"status": status, "workflowName": "CI", "startedAt": "2026-10-15T12:00:00Z",
                 "updatedAt": end, "url": url} for status, end, url in (
                     ("completed", "2026-10-15T12:20:00Z", "https://example.test/run/1"),
                     ("completed", "2026-10-15T12:02:00Z", "https://example.test/run/2"),
                     ("in_progress", "2026-10-15T12:30:00Z", "https://example.test/run/3"))]
        self.assertEqual(durations.slow_runs(rows, 15), ["CI took 20.0 min https://example.test/run/1"])


if __name__ == "__main__":
    unittest.main()
