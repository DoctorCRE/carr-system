#!/usr/bin/env python3
"""Exercise the same registry import and retirement check CI and deploy use."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "ops/config/retired-verbs.v1.json"


class VerbLossGuardTests(unittest.TestCase):
    def setUp(self):
        self.retired = [name for entry in json.loads(LEDGER.read_text())["retirements"]
                        for name in entry["verbs"]]

    def run_guard(self, names, deployed):
        with tempfile.TemporaryDirectory() as raw:
            worker = Path(raw)
            (worker / "src").mkdir()
            (worker / "package.json").write_text('{"type":"module"}')
            (worker / "src/tools.js").write_text(
                "export const TOOLS = " + json.dumps(dict.fromkeys(names, {})) + ";\n")
            return subprocess.run(
                ["sh", str(REPO / "ops/verb-count.sh"), str(worker),
                 "--check-retirements", str(deployed)],
                text=True, capture_output=True)

    def test_intended_retirement_passes(self):
        result = self.run_guard(["keep-a", "keep-b"], 2 + len(self.retired))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2")

    def test_unlisted_missing_verb_fails(self):
        result = self.run_guard(["keep-a"], 2 + len(self.retired))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unlisted verb loss", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_listed_verb_still_present_fails_even_without_shrink(self):
        result = self.run_guard(["keep-a", self.retired[0]], 2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(self.retired[0], result.stderr)
        self.assertEqual(result.stdout, "")

    def test_partial_retirement_does_not_pass_count_fallback(self):
        result = self.run_guard(["keep-a", "keep-b"], 3)
        self.assertNotEqual(result.returncode, 0)

    def test_after_retirement_baseline_and_growth_pass(self):
        for deployed in (0, 1, 2):
            with self.subTest(deployed=deployed):
                result = self.run_guard(["keep-a", "keep-b"], deployed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "2")

    def test_ledger_has_exact_names_removed_by_pr_1806(self):
        patch = subprocess.check_output(
            ["git", "show", "6015711f5", "--", "mcp-server/src"], cwd=REPO, text=True)
        self.assertEqual(len(self.retired), 36)
        self.assertEqual(len(set(self.retired)), 36)
        for name in self.retired:
            self.assertIn('"' + name + '"', patch)


if __name__ == "__main__":
    unittest.main()
