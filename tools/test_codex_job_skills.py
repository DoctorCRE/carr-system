"""Send-time skill selection against temporary catalogs; nothing leaves the machine."""
import sys
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
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
    """Selection is local: the brief's Skills: line names them; nothing leaves the machine."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.repo = Path(self.tmp.name) / 'home', Path(self.tmp.name) / 'repo'
        self.texts = {}
        for name in ('poteto-mode', 'principle-prove-it-works', 'tdd', 'grill-with-docs', 'pr', 'research'):
            path = self.repo / '.claude/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            self.texts[name] = f'---\nname: {name}\ndescription: Procedure {name}\n---\nComplete instructions {name}\n'
            path.write_text(self.texts[name])

    def prepare(self, brief):
        return skill_picker.prepare(brief, self.repo, home=self.home)

    def test_skills_line_selects_named_skills_with_full_text(self):
        brief, picks = self.prepare('Card: X\nSkills: poteto-mode, TDD, Grill With Docs, missing-skill\nOpen a PR after research.')
        self.assertEqual([p['name'] for p in picks],
                         ['poteto-mode', 'principle-prove-it-works', 'grill-with-docs', 'tdd'])
        for pick in picks:
            self.assertIn(self.texts[pick['name']], brief)
        self.assertNotIn(self.texts['pr'], brief)
        self.assertNotIn(self.texts['research'], brief)
        self.assertIn('Named skill not found: missing-skill.', brief)
        self.assertTrue(brief.startswith('Skills: poteto-mode, principle-prove-it-works, grill-with-docs, tdd\n\n'))

    def test_no_skills_line_sends_baseline_and_makes_no_network_call(self):
        with patch('socket.socket', side_effect=AssertionError('network used')):
            brief, picks = self.prepare('Repair the dispatcher. Use tdd.')
        self.assertEqual([p['name'] for p in picks], ['poteto-mode', 'principle-prove-it-works'])
        self.assertIn('## Required skills', brief)


if __name__ == '__main__':
    unittest.main()
