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

    def test_seeded_dependency_check_uses_a_small_disposable_tree(self):
        mod = load("ci_fixture", "ci-selftest.py")
        with mod.ci_fixture() as root:
            self.assertNotEqual(root, ROOT)
            self.assertLess(len(list(root.rglob("*.py"))), 32)
            rc, output = mod.run(["--only", "dependency"], root=root)
            self.assertEqual(rc, 0, output)
            (root / "requirements.txt").write_text("synthetic-package>=2\n")
            rc, output = mod.run(["--only", "dependency"], root=root)
            self.assertEqual(rc, 1, output)
            self.assertIn("STALE", output)
        self.assertFalse(root.exists())

    def test_missing_or_duplicated_hosted_class_is_red(self):
        original = (ROOT / '.github/workflows/ci.yml').read_text()
        canary = (ROOT / '.github/workflows/main-canary.yml').read_text()
        for replacement in ('', '          - "unit unit"\n'):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                (root / '.github/workflows').mkdir(parents=True)
                mutated = original.replace('          - "unit"\n', replacement, 1)
                self.assertNotEqual(mutated, original, 'the fixture must remove or duplicate the unit class')
                (root / '.github/workflows/ci.yml').write_text(mutated)
                (root / '.github/workflows/main-canary.yml').write_text(canary)
                mod = load('ci_membership', 'ci-selftest.py')
                with patch.object(mod, 'REPO', root), contextlib.redirect_stdout(io.StringIO()):
                    mod.test_each_class_runs_in_exactly_one_hosted_group()
                self.assertEqual(len(mod.RESULTS), 2)
                self.assertFalse(mod.RESULTS[0][1], mod.RESULTS)
                self.assertTrue(mod.RESULTS[1][1], mod.RESULTS)

if __name__ == "__main__":
    unittest.main()
