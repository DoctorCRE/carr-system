#!/usr/bin/env python3
"""Selftest for fill-engine/audit_template_colors.apply_sendable_note.

A map that already carries a SENDABLE FORMAT note with the retired send sentence
must be rewritten to the current sentence and saved, with every other byte
(including a later amendment paragraph and the hand-formatted JSON) unchanged.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
spec = importlib.util.spec_from_file_location(
    "audit_template_colors", os.path.join(REPO, "fill-engine", "audit_template_colors.py"))
assert spec is not None and spec.loader is not None
atc: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atc)

LEGACY = "Naming a sendable format is not permission to send: Joe sends, and no verb can."
CURRENT = "Naming a sendable format is not itself an ask to send: it goes out when Joe asks (decision 41906a30)."
AMENDMENT = "LATER AMENDMENT: nothing else moved."


def fixture(note_tail: str) -> str:
    note = ("REVIEWED. SENDABLE FORMAT (old doctrine): this is a LETTER template. " + note_tail
            + "\n\n" + AMENDMENT)
    return '{\n "slug": "loi-x",\n "review_note": ' + json.dumps(note) + ',\n "slots": [1, 2]\n}\n'


class ApplySendableNote(unittest.TestCase):
    def run_on(self, raw: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "loi-purchase.json")
            with open(path, "w") as fh:
                fh.write(raw)
            old = atc.MAPS
            atc.MAPS = d
            try:
                atc.apply_sendable_note("loi-purchase")
            finally:
                atc.MAPS = old
            with open(path) as fh:
                return fh.read()

    def test_existing_note_gets_the_current_send_sentence_and_is_saved(self):
        for legacy in (LEGACY, LEGACY.replace("no verb can", "no verb in this system can")):
            with self.subTest(legacy=legacy):
                raw = fixture(legacy)
                out = self.run_on(raw)
                self.assertNotIn("Joe sends", out)
                self.assertIn(CURRENT, out)
                self.assertEqual(out, raw.replace(legacy, CURRENT))
                self.assertEqual(self.run_on(out), out)

    def test_later_amendment_paragraph_survives(self):
        self.assertIn(AMENDMENT, json.loads(self.run_on(fixture(LEGACY)))["review_note"])

    def test_current_note_is_left_untouched(self):
        raw = fixture(CURRENT)
        self.assertEqual(self.run_on(raw), raw)

    def test_map_without_a_note_gets_the_full_note(self):
        raw = '{\n "slug": "loi-x",\n "review_note": "REVIEWED."\n}\n'
        out = json.loads(self.run_on(raw))["review_note"]
        self.assertTrue(out.startswith("REVIEWED."))
        self.assertIn("SENDABLE FORMAT", out)
        self.assertIn(CURRENT, out)


if __name__ == "__main__":
    unittest.main()
