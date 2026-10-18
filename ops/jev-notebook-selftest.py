#!/usr/bin/env python3
"""Offline suite for ops/jev_notebook.py: append-only recording and exact
error/artifact recall. The module asks no model, so nothing is faked."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_notebook.py"
SPEC = importlib.util.spec_from_file_location("jev_notebook", MODULE_PATH)
assert SPEC and SPEC.loader
nb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nb)


class RecordMistakeTests(unittest.TestCase):
    def test_appends_a_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            row = nb.record_mistake("off-by-one", "reverse a list", "used i instead of i-1",
                                     "used i-1", source="unit-test", notebook_path=str(path))
            self.assertEqual(row["id"], 0)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["kind"], "off-by-one")

    def test_ids_increment_and_the_file_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            first = nb.record_mistake("k", "t1", "w1", "f1", source="s", notebook_path=str(path))
            second = nb.record_mistake("k", "t2", "w2", "f2", source="s", notebook_path=str(path))
            self.assertEqual((first["id"], second["id"]), (0, 1))
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)

    def test_a_broken_path_never_raises(self):
        row = nb.record_mistake("k", "t", "w", "f", source="s",
                                 notebook_path="/proc/nonexistent/nb.jsonl")
        self.assertEqual(row["kind"], "k")


class RecallEmptyTests(unittest.TestCase):
    def test_empty_notebook_returns_no_lines_and_does_not_escalate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            out = nb.recall_mistakes("do a thing", notebook_path=str(path))
            self.assertEqual(out["detail"]["lines"], [])
            self.assertFalse(out["escalate"])

    def test_no_exact_signature_overlap_returns_no_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            nb.record_mistake("k", "parse json pointer paths", "forgot null check",
                               "added null check", source="s", notebook_path=str(path))
            out = nb.recall_mistakes("completely unrelated banana pancake recipe",
                                      notebook_path=str(path))
            self.assertEqual(out["detail"]["lines"], [])


class EntrypointTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
