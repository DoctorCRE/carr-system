#!/usr/bin/env python3
"""Offline tests for the narrow CI preflight entry points."""
# doctrine: engineering-workflow-sop
import contextlib
import importlib.util
import io
import pathlib
import subprocess
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ops" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PreflightTests(unittest.TestCase):
    def test_required_registry_read_cannot_skip_loader_failure(self):
        mod = load("completion_preflight", "completion-evidence-gate-selftest.py")
        with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(mod.registry_prefix_coverage(required=True))

    def test_registry_refuses_unknown_writes_and_accepts_classified_writes(self):
        mod = load("completion_preflight", "completion-evidence-gate-selftest.py")
        for names, expected in [('["add-loop"]', True), ('["retro-unknown-write"]', False)]:
            with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, names, "")), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.registry_prefix_coverage(required=True), expected)

    def test_collection_only_does_not_run_the_full_selftest(self):
        mod = load("ci_preflight", "ci-selftest.py")
        def verdict():
            mod.RESULTS.append(("collection", True, ""))
        with patch.object(mod, "test_every_test_file_in_the_tree_is_collected", side_effect=verdict) as collect, patch.object(mod, "test_types_class_catches_a_seeded_type_error", side_effect=AssertionError("full suite ran")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["--collection-only"]), 0)
            collect.assert_called_once()

if __name__ == "__main__":
    unittest.main()
