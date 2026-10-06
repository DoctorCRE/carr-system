#!/usr/bin/env python3
"""The council's Grok chair goes through the sanctioned runner and its verdicts.

A fake `grok` on PATH returns a cancelled run naming grok-4.6 as raw JSON, which
is what a direct `grok --print` call would happily save as the council answer.
Runner fixtures replay the streams offline; nothing here touches the network.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "ops/fixtures/grok-run"
ZSH = shutil.which("zsh")
FAKE_GROK = '#!/bin/sh\necho \'{"type":"end","stopReason":"cancelled","modelUsage":{"grok-4.6":{}}}\'\n'


@unittest.skipUnless(ZSH, "council-lib.sh is zsh")
class CouncilGrokTests(unittest.TestCase):
    def chair(self, fixture, brief="brief"):
        with tempfile.TemporaryDirectory(prefix="council-grok-") as tmp:
            tmp = Path(tmp)
            (tmp / "bin").mkdir()
            fake = tmp / "bin/grok"
            fake.write_text(FAKE_GROK)
            fake.chmod(0o755)
            (tmp / "brief.md").write_text(brief)
            env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}",
                       GROK_RUN_FAKE_NDJSON=str(FIXTURES / fixture))
            run = subprocess.run(
                [ZSH, "-c", f'REPO="{ROOT}"; source "$REPO/bin/council-lib.sh"; '
                            f'run_grok "{tmp}/brief.md" "{tmp}/grok.md"'],
                env=env, capture_output=True, text=True, timeout=30)
            out = tmp / "grok.md"
            return (run.returncode, out.read_text() if out.exists() else None,
                    (tmp / "grok.rejected").exists())

    def test_completed_run_delivers_the_final_text(self):
        code, text, rejected = self.chair("good.ndjson")
        self.assertEqual(code, 0)
        self.assertTrue(text and not text.lstrip().startswith("{"), text)
        self.assertFalse(rejected)

    def test_linked_council_brief_keeps_the_prose_chair_answer(self):
        code, text, rejected = self.chair("good.ndjson",
            "Weigh https://github.com/example/repo/pull/1 and answer in prose.")
        self.assertEqual(code, 0)
        self.assertEqual(text, "Hello world\n")
        self.assertFalse(rejected)

    def test_cancelled_run_is_rejected_and_never_left_as_the_answer(self):
        code, text, rejected = self.chair("cancelled.ndjson")
        self.assertNotEqual(code, 0)
        self.assertIsNone(text)
        self.assertTrue(rejected)

    def test_wrong_model_run_is_rejected_and_never_left_as_the_answer(self):
        code, text, rejected = self.chair("wrong-model.ndjson")
        self.assertNotEqual(code, 0)
        self.assertIsNone(text)
        self.assertTrue(rejected)


if __name__ == "__main__":
    unittest.main()
