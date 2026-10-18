#!/usr/bin/env python3
"""Check that the referenced design skills exist."""
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[1]


class PRDesignPolicyTests(unittest.TestCase):
    def test_referenced_files_exist(self):
        for name in ("codebase-design", "zero-tech-debt"):
            path = REPO / ".agents/skills" / name / "SKILL.md"
            self.assertTrue(path.is_file(), str(path))


if __name__ == "__main__":
    unittest.main()
