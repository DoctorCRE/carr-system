#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).with_name("writing-lint.py")
spec = importlib.util.spec_from_file_location("writing_lint", SCRIPT)
assert spec is not None and spec.loader is not None
writing_lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writing_lint)


class AttributionTests(unittest.TestCase):
    def attribution(self, text):
        return [hit for hit in writing_lint.lint(text, "social")
                if hit[1] == "dell-attribution"]

    def test_typography_and_wrapping_keep_the_hard_ban(self):
        for text in ("Dell's network", "Dell’s network", "Dell’s 15+ years",
                     "Dell brings a vendor network", "Dell\nbrings a vendor network",
                     "Dell\tbrings a vendor network"):
            with self.subTest(text=text):
                hits = self.attribution(text)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0][0], "HARD")

    def test_shared_network_and_unrelated_names_are_not_attribution(self):
        for text in ("Our network", "My network", "Dell attended the meeting.",
                     "O'Dell's network", "Arundell brings a network"):
            with self.subTest(text=text):
                self.assertEqual(self.attribution(text), [])

    def test_masked_examples_are_not_public_copy(self):
        for text in ("`Dell’s network`", "```Dell\nbrings a network```",
                     "[Reference](https://example.test/Dell's-network)",
                     "*Editorial note: Dell’s network*"):
            with self.subTest(text=text):
                self.assertEqual(self.attribution(text), [])

    def test_findings_keep_original_offset_and_line(self):
        text = "Our network.\nDell’s network."
        hit, = self.attribution(text)
        self.assertEqual(hit[2], text.index("Dell"))
        self.assertEqual(writing_lint.line_of(text, hit[2]), 2)

    def test_cli_refuses_wrapped_attribution(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "-", "--surface", "social"],
                                input="Dell\nbrings a vendor network.",
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[HARD] dell-attribution", result.stdout)


if __name__ == "__main__":
    unittest.main()
