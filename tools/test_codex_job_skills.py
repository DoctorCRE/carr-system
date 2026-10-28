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
        for name in ('poteto-mode', 'principle-prove-it-works', 'tdd', 'implement', 'grill-with-docs',
                     'codebase-design', 'zero-tech-debt', 'diagnosing-bugs'):
            path = self.repo / '.claude/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            self.texts[name] = f'---\nname: {name}\ndescription: Procedure {name}\n---\nComplete instructions {name}\n'
            path.write_text(self.texts[name])

    def prepare(self, brief):
        return skill_picker.prepare(brief, self.repo, home=self.home)

    def names(self, picks):
        return [p['name'] for p in picks]

    def test_prose_skills_line_keeps_every_named_skill_with_full_text(self):
        brief, picks = self.prepare('Card: X\nSkills: poteto-mode, grill-with-docs. Then codebase-design and '
                                    'zero-tech-debt (Grill With Docs first).\nFix the dispatcher.')
        self.assertEqual(self.names(picks), ['poteto-mode', 'principle-prove-it-works',
                                             'codebase-design', 'grill-with-docs', 'zero-tech-debt'])
        for pick in picks:
            self.assertIn(self.texts[pick['name']], brief)
        self.assertNotIn(self.texts['tdd'], brief)
        self.assertTrue(brief.startswith('Skills: poteto-mode, principle-prove-it-works, codebase-design, '
                                         'grill-with-docs, zero-tech-debt\n\n'))

    def test_names_match_whole_tokens_only(self):
        _, picks = self.prepare('Skills: TDD, codebase-design. Read tdd-notes and undiagnosing-bugs-x.')
        self.assertEqual(self.names(picks), ['poteto-mode', 'principle-prove-it-works', 'codebase-design', 'tdd'])

    def test_queue_brief_without_names_gets_tdd_and_implement_and_no_network(self):
        brief = ('Card: WR1.S1.J1\nRepair the dispatcher\nThe dispatcher drops jobs.\n\n'
                 'Work Request: WR-000001, slice 1.\nImplement this queued task and deliver through a ready pull request.')
        with patch('socket.socket', side_effect=AssertionError('network used')):
            sent, picks = self.prepare(brief)
        self.assertEqual(self.names(picks), ['poteto-mode', 'principle-prove-it-works', 'implement', 'tdd'])
        self.assertIn(self.texts['implement'], sent)


if __name__ == '__main__':
    unittest.main()
