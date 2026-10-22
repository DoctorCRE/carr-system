"""Send-time skill selection against temporary catalogs and a fake SDK."""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_picker


class CatalogTests(unittest.TestCase):
    def test_catalog_reads_frontmatter_and_rebuilds_for_each_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            path = home / '.claude/plugins/cache/cursor-plugins/pstack/1.0/skills/testing/SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('---\nname: testing\ndescription: >-\n  Test observable behavior,\n  including failures.\n---\n# Full instructions\n')
            older = home / '.claude/plugins/cache/cursor-plugins/pstack/0.9/skills/obsolete/SKILL.md'
            older.parent.mkdir(parents=True)
            older.write_text('---\nname: obsolete\ndescription: Old version\n---\n')
            local = repo / '.claude/skills/local/SKILL.md'
            local.parent.mkdir(parents=True)
            local.write_text('---\nname: local\ndescription: "Local: specific"\n---\n')
            agent = home / '.agents/skills/agent/SKILL.md'
            agent.parent.mkdir(parents=True)
            agent.write_text('---\nname: agent\ndescription: Shared skill\n---\n')
            skills = skill_picker.catalog(repo, home=home)
            self.assertEqual({s.name: s.description for s in skills}, {
                'pstack:testing': 'Test observable behavior, including failures.',
                'local': 'Local: specific', 'agent': 'Shared skill'})
            self.assertTrue(all(s.path.is_absolute() for s in skills))
            local.write_text('---\nname: local\ndescription: Updated immediately\n---\n')
            self.assertIn('Updated immediately', [s.description for s in skill_picker.catalog(repo, home=home)])


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.repo = Path(self.tmp.name) / 'home', Path(self.tmp.name) / 'repo'
        self.texts = {}
        for name in ('poteto-mode', 'principle-prove-it-works', *(f'skill-{n}' for n in range(8))):
            path = self.repo / '.claude/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            self.texts[name] = f'---\nname: {name}\ndescription: Procedure {name}\n---\nComplete instructions {name}\n'
            path.write_text(self.texts[name])
        self.calls = []

    def client(self, probabilities=None, error=None, delay=0):
        owner = self
        class FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def system_one(self, *, state, questions):
                owner.calls.append((state, questions))
                await asyncio.sleep(delay)
                if error:
                    raise error
                return SimpleNamespace(nouls={key: SimpleNamespace(noul=next(
                    (value for name, value in (probabilities or {}).items()
                     if f'the {name} skill:' in question.instructions), 0.01))
                    for key, question in questions.items()})
        return FakeClient

    def prepare(self, **kwargs):
        return skill_picker.prepare('Repair the dispatcher.', self.repo, home=self.home, **kwargs)

    def test_single_sdk_call_threshold_cap_baselines_and_full_text(self):
        probabilities = {f'skill-{n}': value for n, value in enumerate((.79, .80, .81, .82, .83, .84, .85, .99))}
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-only'}):
            brief, picks = self.prepare(client_factory=self.client(probabilities))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], 'Repair the dispatcher.')
        self.assertEqual(len(self.calls[0][1]), 10)
        self.assertEqual([p['name'] for p in picks], ['poteto-mode', 'principle-prove-it-works',
                         'skill-7', 'skill-6', 'skill-5', 'skill-4', 'skill-3', 'skill-2'])
        self.assertEqual(picks[2]['probability'], .99)
        self.assertNotIn('skill-0', brief)
        self.assertNotIn('skill-1', brief)
        for pick in picks:
            self.assertIn(self.texts[pick['name']], brief)
            self.assertIn(str((self.repo / '.claude/skills' / pick['name'] / 'SKILL.md').resolve()), brief)
        self.assertIn('## Required skills', brief)

    def test_missing_key_sends_baseline_without_call(self):
        with patch.dict(os.environ, {}, clear=True):
            brief, picks = self.prepare(client_factory=self.client())
        self.assertEqual(self.calls, [])
        self.assertEqual(len(picks), 2)
        self.assertTrue(all(p['probability'] is None for p in picks))
        self.assertIn('picker skipped: TYPESAFE_API_KEY is missing', brief)
        self.assertIn(self.texts['poteto-mode'], brief)

    def test_failure_does_not_expose_response_or_block_baseline(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-only'}):
            brief, picks = self.prepare(client_factory=self.client(error=RuntimeError('sensitive response')))
        self.assertEqual(len(picks), 2)
        self.assertIn('picker skipped: RuntimeError', brief)
        self.assertNotIn('sensitive response', brief)

    def test_total_deadline_cancels_request_and_returns_baseline(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-only'}), \
             patch.object(skill_picker, 'TIMEOUT_S', .02):
            brief, picks = self.prepare(client_factory=self.client(delay=1))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(picks), 2)
        self.assertIn('picker skipped: timeout', brief)

    def test_large_catalog_shards_only_when_context_requires_it(self):
        for n in range(45):
            path = self.repo / '.claude/skills' / f'large-{n}' / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text(f'---\nname: large-{n}\ndescription: "' + 'x' * 1500 + '"\n---\nComplete body\n')
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-only'}):
            brief, picks = self.prepare(client_factory=self.client())
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(state == 'Repair the dispatcher.' for state, _ in self.calls))
        self.assertEqual(sum(len(questions) for _, questions in self.calls), 55)
        self.assertEqual(len(picks), 2)
        self.assertNotIn('picker skipped:', brief)

    def test_malformed_answer_falls_back_without_partial_selection(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-only'}):
            brief, picks = self.prepare(client_factory=self.client({'skill-0': .99, 'skill-1': float('nan')}))
        self.assertEqual(len(picks), 2)
        self.assertIn('picker skipped: ValueError', brief)

    def test_exact_threshold_is_included(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fixture-only'}):
            _, picks = self.prepare(client_factory=self.client({'skill-0': .80, 'skill-1': .7999}))
        self.assertEqual([p['name'] for p in picks][2:], ['skill-0'])


if __name__ == '__main__':
    unittest.main()
