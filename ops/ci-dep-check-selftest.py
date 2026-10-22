#!/usr/bin/env python3
"""Synthetic audit responses prove dependency CI fails closed without registry calls."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dependency_check', ROOT / 'ops/ci-dep-check.py')
assert spec is not None and spec.loader is not None
dependency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependency)


class DependencyTests(unittest.TestCase):
    def audit(self, response):
        output = StringIO()
        with patch.object(sys, 'argv', ['ci-dep-check.py', '--strict']), \
                patch.object(dependency.subprocess, 'run', side_effect=response) as process, \
                redirect_stdout(output), redirect_stderr(output):
            result = dependency.main()
        return result, output.getvalue(), process.call_args_list

    def test_audit_timeout_refuses_strict_ci(self):
        def timeout(*args, **kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 120)
            raise subprocess.TimeoutExpired(['npm', 'audit'], kwargs['timeout'])
        result, output, calls = self.audit(timeout)
        self.assertEqual(result, 1)
        self.assertIn('timed out', output)
        self.assertTrue(calls)

    def test_advisories_still_refuse_and_clean_reports_pass(self):
        for high, expected in ((1, 1), (0, 0)):
            with self.subTest(high=high):
                def report(*args, **kwargs):
                    return subprocess.CompletedProcess(args[0], int(bool(high)), stdout=json.dumps(
                        {'metadata': {'vulnerabilities': {'high': high, 'critical': 0}}}), stderr='')
                result, output, calls = self.audit(report)
                self.assertEqual(result, expected)
                self.assertTrue(calls)


if __name__ == '__main__':
    unittest.main()
