#!/usr/bin/env python3
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch

OPENING = (
    "Use pstack's poteto-mode for this work. Step 1, size it: tiny -> build it now; "
    "one session -> run /grill-with-docs first; bigger -> run /wayfinder. "
    "Use /tdd for any behaviour change."
)


class BuilderSkillsTests(unittest.TestCase):
    def send(self, task, *flags):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(dispatch, 'dispatch', return_value={'status': 'completed'}) as send, \
             contextlib.redirect_stdout(io.StringIO()):
            result = dispatch.main(['--registry', str(Path(root) / 'desks.json'),
                                    'send', 'builder', task, *flags])
        self.assertEqual(result, 0)
        return send.call_args.args[1]

    def test_writing_job_gets_skill_opening_once(self):
        for flags in (('--checkout', 'new:build'), ('--writes', 'tools/*.py'),
                      ('--checkout', 'new:build', '--writes', 'tools/*.py')):
            with self.subTest(flags=flags):
                self.assertEqual(self.send('Fix dispatch.', *flags), OPENING + '\n\nFix dispatch.')
                task = OPENING + '\n\nFix dispatch.'
                self.assertEqual(self.send(task, *flags), task)

    def test_read_only_job_keeps_task_unchanged(self):
        self.assertEqual(self.send('Review dispatch.'), 'Review dispatch.')


if __name__ == '__main__':
    unittest.main()
