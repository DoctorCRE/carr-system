#!/usr/bin/env python3
"""Verify the launcher usage contract and independent command additions."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


class UsageTests(unittest.TestCase):
    def test_usage_preserves_output_and_exit_status(self):
        for args in ([], ["unknown-selftest-command"]):
            with self.subTest(args=args):
                result = subprocess.run([str(REPO / "run.sh"), *args], capture_output=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, b"")
                self.assertIn(b"review-pr <number>", result.stdout)
                self.assertEqual(hashlib.sha256(result.stdout).hexdigest(),
                                 "d92b8af32b13be7f1c90ff7a187bf33b73a551960c4b1b4b3c18289dbe24618b")

    def test_independent_usage_additions_merge(self):
        source = (REPO / "run.sh").read_text()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base, ours, theirs = [root / name for name in ("base", "ours", "theirs")]
            base.write_text(source)
            for target, anchor, addition in (
                (ours, "map [--missing]", '      "fixture-map|" \\\n'),
                (theirs, "worktree <name>", '      "fixture-worktree|" \\\n'),
            ):
                lines = source.splitlines(keepends=True)
                matches = [i for i, line in enumerate(lines) if anchor in line]
                self.assertEqual(len(matches), 1)
                lines.insert(matches[0], addition)
                target.write_text("".join(lines))
            result = subprocess.run(["git", "merge-file", "-p", str(ours), str(base), str(theirs)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, "independent additions conflict")
            merged = root / "run.sh"
            merged.write_text(result.stdout)
            rendered = subprocess.run(["zsh", str(merged)], capture_output=True, text=True)
            self.assertEqual(rendered.returncode, 2, rendered.stderr)
            self.assertIn("fixture-map|", rendered.stdout)
            self.assertIn("fixture-worktree|", rendered.stdout)


if __name__ == "__main__":
    unittest.main()
