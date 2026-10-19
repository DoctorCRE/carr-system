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


class BuilderSkillsTests(unittest.TestCase):
    def send(self, task, *flags):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(dispatch, 'dispatch', return_value={'status': 'completed'}) as send, \
             contextlib.redirect_stdout(io.StringIO()):
            result = dispatch.main(['--registry', str(Path(root) / 'desks.json'),
                                    'send', 'builder', task, *flags])
        self.assertEqual(result, 0)
        return send.call_args.args[1]

    def test_every_send_gets_standard_footer_once(self):
        for flags in ((), ('--checkout', 'new:build'), ('--writes', 'tools/*.py')):
            with self.subTest(flags=flags):
                sent = self.send('Fix dispatch.', *flags)
                self.assertTrue(sent.startswith('Fix dispatch.'))
                self.assertEqual(sent.count('Skills:'), 1)
                self.assertIn('Pocock TDD', sent)
                self.assertIn('poteto-mode', sent)
                self.assertIn('Prove It Works', sent)
                self.assertIn('grill-with-docs', sent)
                self.assertEqual(sent.count('Rules:'), 1)
                self.assertIn('never merge', sent)
                self.assertEqual(self.send(sent, *flags), sent)

    def test_custom_skills_are_preserved_and_standard_safety_is_added(self):
        task = 'Review dispatch.\n\nSkills: custom reviewer\n\nRules: inspect only.'
        sent = self.send(task)
        self.assertTrue(sent.startswith(task))
        self.assertEqual(sent.count('Skills:'), 1)
        self.assertIn('never force-push', sent)
        self.assertIn('no client PII', sent)
        self.assertEqual(self.send(sent), sent)


if __name__ == '__main__':
    unittest.main()
