#!/usr/bin/env python3
"""Stubbed CLI checks for production proof connection resolution."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "migrate_prod_support", REPO / "tools" / "migrate-prod-support.py"
)
assert SPEC and SPEC.loader
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


class NeonDsnTests(unittest.TestCase):
    def invoke(self, result):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["support", "neon-dsn", "--project-id", "fixture-project"]), \
             patch.object(support.subprocess, "run", side_effect=result) as provider, \
             redirect_stdout(output), redirect_stderr(error):
            code = support.main()
        return code, output.getvalue(), error.getvalue(), provider

    def test_connection_string_is_returned_only_on_success(self):
        with patch.dict(os.environ, {}, clear=True):
            code, output, error, provider = self.invoke([
                subprocess.CompletedProcess([], 0, "fixture-connection\n", "")])
        self.assertEqual((code, output, error), (0, "fixture-connection\n", ""))
        provider.assert_called_once_with(
            ["neonctl", "connection-string", "production", "--project-id", "fixture-project",
             "--role-name", "neondb_owner"], capture_output=True, text=True, timeout=90)

    def test_provider_failure_preserves_exit_code_and_redacts_diagnostics(self):
        canary = uuid.uuid4().hex
        result = subprocess.CompletedProcess(
            [], 7, "untrusted provider output", f"fixture failure {canary} postgres://fixture:{canary}@invalid/db")  # ci-secret-scan: allow
        with patch.dict(os.environ, {"NEON_API_KEY": canary}, clear=True):
            code, output, error, _ = self.invoke([result])
        self.assertEqual(code, 7)
        self.assertEqual(output, "")
        self.assertIn("fixture failure", error)
        self.assertNotIn(canary, error)
        self.assertLessEqual(len(error), support.DETAIL_MAX + 1)

    def test_unavailable_provider_returns_safe_error(self):
        with patch.dict(os.environ, {}, clear=True):
            code, output, error, _ = self.invoke(FileNotFoundError("fixture provider absent"))
        self.assertEqual((code, output), (1, ""))
        self.assertIn("neonctl unavailable (FileNotFoundError)", error)

    def test_retired_wrapper_commands_are_rejected(self):
        for command in ("write-receipt", "escalate"):
            with self.subTest(command=command), \
                 patch.object(sys, "argv", ["support", command, "--help"]), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                support.main()
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
