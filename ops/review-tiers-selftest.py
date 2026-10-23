#!/usr/bin/env python3
"""Check generation of the retained JavaScript map without Python tier policy."""
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sync_review_tiers", REPO / "ops/sync-review-tiers.py")
assert spec and spec.loader
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class GenerationTests(unittest.TestCase):
    def test_retained_javascript_module_is_current(self):
        self.assertEqual(sync.check(), [])


if __name__ == "__main__":
    unittest.main()
