#!/usr/bin/env python3
"""Offline tests for the narrow CI preflight entry points."""
# doctrine: engineering-workflow-sop
import contextlib
import importlib.util
import io
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ops" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PreflightTests(unittest.TestCase):
    def test_collection_only_does_not_run_the_full_selftest(self):
        mod = load("ci_preflight", "ci-selftest.py")
        def verdict():
            mod.RESULTS.append(("collection", True, ""))
        with patch.object(mod, "test_every_test_file_in_the_tree_is_collected", side_effect=verdict) as collect, patch.object(mod, "test_types_class_catches_a_seeded_type_error", side_effect=AssertionError("full suite ran")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(["--collection-only"]), 0)
            collect.assert_called_once()

    def test_missing_or_duplicated_hosted_class_is_red(self):
        original = (ROOT / '.github/workflows/ci.yml').read_text()
        for replacement in ('pushfloor types', 'pushfloor unit unit types'):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                (root / '.github/workflows').mkdir(parents=True)
                (root / '.github/workflows/ci.yml').write_text(
                    original.replace('pushfloor unit types', replacement))
                mod = load('ci_membership', 'ci-selftest.py')
                with patch.object(mod, 'REPO', root), contextlib.redirect_stdout(io.StringIO()):
                    mod.test_each_class_runs_in_exactly_one_hosted_group()
                self.assertEqual(len(mod.RESULTS), 1)
                self.assertFalse(mod.RESULTS[0][1], mod.RESULTS)

if __name__ == "__main__":
    unittest.main()
