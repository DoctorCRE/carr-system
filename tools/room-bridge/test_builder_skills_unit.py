#!/usr/bin/env python3
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch


class BuilderSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('poteto-mode', 'principle-prove-it-works'):
            path = self.root / '.agents/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text(f'---\nname: {name}\ndescription: Fixture\n---\nFull {name}.\n')

    def send(self, task, *flags):
        with patch.dict(os.environ, {'HOME': str(self.root), 'TYPESAFE_API_KEY': ''}), \
             patch('work_cli.send', return_value={'status': 'queued'}) as send, \
             contextlib.redirect_stdout(io.StringIO()):
            result = dispatch.main(['--registry', str(self.root / 'desks.json'),
                                    'send', 'builder', task, *flags])
        self.assertEqual(result, 0)
        return send.call_args.args[1]

    def test_every_send_gets_standard_footer_once(self):
        for flags in ((), ('--checkout', 'new:build'), ('--writes', 'tools/*.py')):
            with self.subTest(flags=flags):
                sent = self.send('Fix dispatch.', *flags)
                self.assertTrue(sent.startswith('Fix dispatch.'))
                self.assertEqual(sent.count('## Required skills'), 1)
                self.assertIn('poteto-mode', sent)
                self.assertIn('principle-prove-it-works', sent)
                self.assertEqual(sent.count('Rules:'), 1)
                self.assertNotIn('never merge', sent)
                self.assertIn('GitHub auto-merge turned on (squash, pinned to your head commit)', sent)
                self.assertEqual(self.send(sent, *flags), sent)

    def test_builder_uses_result_questions_and_pinned_auto_merge(self):
        builder = (Path(__file__).parent / 'briefs/builder.md').read_text()
        self.assertIn('QUESTION:', builder)
        self.assertIn('Open the PR with GitHub auto-merge turned on (squash, pinned to your head commit); it merges itself when CI is green. App screen changes open as drafts until the independent taste score passes.', builder)
        for name in ('builder.md', 'staging-score.md'):
            self.assertNotIn('never merge', (Path(__file__).parent / 'briefs' / name).read_text())

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
