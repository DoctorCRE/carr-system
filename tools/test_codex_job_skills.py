"""Typed workflow stamping against temporary skill catalogs."""
import sys
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

import yaml

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

    def test_catalog_without_new_roots_matches_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            entries = [
                (home / '.agents/skills/synced/docs', 'docs'),
                (home / '.agents/skills/first', 'shared'),
                (home / '.agents/skills/second', 'shared'),
                (repo / '.claude/skills/shared', 'shared'),
                (repo / '.claude/skills/synced/pdf', 'pdf'),
                (home / '.agents/skills/.hidden/agent', 'hidden-agent'),
                (repo / '.claude/skills/.hidden/local', 'hidden-local'),
            ]
            for directory, name in entries:
                directory.mkdir(parents=True)
                (directory / 'SKILL.md').write_text(
                    f'---\nname: {name}\ndescription: Procedure {name}\n---\n')
            expected = sorted([
                skill_picker.Skill(name, f'Procedure {name}', (directory / 'SKILL.md').resolve())
                for directory, name in entries
            ], key=lambda skill: (skill.name, str(skill.path)))
            self.assertEqual(skill_picker.catalog(repo, home=home), expected)

    def test_catalog_reads_tstack_roots_and_deduplicates_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            for root, name in (('.claude/skills', 'file-pr'), ('.codex/skills', 'modernize')):
                path = home / root / name / 'SKILL.md'
                path.parent.mkdir(parents=True)
                path.write_text(f'---\nname: {name}\ndescription: Procedure {name}\n---\n')
            alias = home / '.codex/skills/file-pr'
            alias.symlink_to(home / '.claude/skills/file-pr', target_is_directory=True)
            self.assertEqual([s.name for s in skill_picker.catalog(repo, home=home)],
                             ['file-pr', 'modernize'])

    def test_catalog_prefers_agents_copy_over_claude_and_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            for root in ('.agents/skills', '.claude/skills', '.codex/skills'):
                path = home / root / 'file-pr/SKILL.md'
                path.parent.mkdir(parents=True)
                path.write_text('---\nname: file-pr\ndescription: Open a PR\n---\n')
            skills = skill_picker.catalog(repo, home=home)
            self.assertEqual([(s.name, s.path) for s in skills], [
                ('file-pr', (home / '.agents/skills/file-pr/SKILL.md').resolve())])
            for removed, expected in (('.agents/skills', '.claude/skills'),
                                      ('.claude/skills', '.codex/skills')):
                (home / removed / 'file-pr/SKILL.md').unlink()
                with self.subTest(expected_root=expected):
                    self.assertEqual([(s.name, s.path) for s in skill_picker.catalog(repo, home=home)], [
                        ('file-pr', (home / expected / 'file-pr/SKILL.md').resolve())])

    def test_catalog_prefers_repo_copy_over_new_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            local = repo / '.claude/skills/file-pr/SKILL.md'
            for root in (local.parent, home / '.claude/skills/file-pr',
                         home / '.codex/skills/file-pr'):
                root.mkdir(parents=True)
                (root / 'SKILL.md').write_text('---\nname: file-pr\ndescription: Open a PR\n---\n')
            self.assertEqual(skill_picker.catalog(repo, home=home), [
                skill_picker.Skill('file-pr', 'Open a PR', local.resolve())])

    def test_catalog_skips_dot_directories_in_new_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            for root in (home / '.claude/skills', home / '.codex/skills'):
                for relative in ('.system/hidden', 'nested/.hidden'):
                    path = root / relative / 'SKILL.md'
                    path.parent.mkdir(parents=True)
                    path.write_text('---\nname: hidden\ndescription: Hidden skill\n---\n')
                visible = root / 'visible/SKILL.md'
                visible.parent.mkdir(parents=True)
                visible.write_text('---\nname: visible\ndescription: Visible skill\n---\n')
            self.assertEqual(skill_picker.catalog(repo, home=home), [
                skill_picker.Skill('visible', 'Visible skill',
                                  (home / '.claude/skills/visible/SKILL.md').resolve())])

    def test_catalog_skips_synced_subfolders(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            for root in (home / '.claude/skills', home / '.codex/skills'):
                for relative in ('synced/handoff', 'nested/synced/handoff'):
                    path = root / relative / 'SKILL.md'
                    path.parent.mkdir(parents=True)
                    path.write_text('---\nname: handoff\ndescription: Synced copy\n---\n')
            self.assertEqual(skill_picker.catalog(repo, home=home), [])

    def test_workflow_validation_rejects_unknown_skill(self):
        table = yaml.safe_load((Path(__file__).resolve().parents[1] /
                                'ops/config/job-workflows.v1.yaml').read_text())
        table['job_types']['feature-backend']['stages']['build']['skills'].append('unknown-skill')
        with self.assertRaisesRegex(AssertionError, 'unknown-skill'):
            self.assert_workflow_skills_resolve(table, {
                'pstack:poteto-mode', 'pstack:principle-prove-it-works',
                'mattpocock-skills:grill-with-docs', 'mattpocock-skills:tdd',
                'mattpocock-skills:pr', 'mattpocock-skills:code-review',
                'mattpocock-skills:codebase-design', 'mattpocock-skills:diagnosing-bugs',
                'mattpocock-skills:domain-modeling', 'file-pr', 'babysit-pr',
                'modernize', 'zero-tech-debt'})

    def assert_workflow_skills_resolve(self, table, available):
        for job_type, job in table['job_types'].items():
            for stage in job['stages'].values():
                names = stage['skills'] + [item['skill'] for item in
                                           stage.get('conditional_skills', [])]
                self.assertFalse(set(names) - available,
                                 f'{job_type}: unknown skills {set(names) - available}')

    def test_workflow_skills_resolve_in_fixture_home(self):
        table = yaml.safe_load((Path(__file__).resolve().parents[1] /
                                'ops/config/job-workflows.v1.yaml').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / 'home', Path(tmp) / 'repo'
            inventories = {
                '.claude/plugins/cache/cursor-plugins/pstack/1.0/skills':
                    ('poteto-mode', 'principle-prove-it-works'),
                '.claude/plugins/cache/claude-plugins-official/mattpocock-skills/1.0/skills/engineering':
                    ('grill-with-docs', 'tdd', 'pr', 'code-review', 'codebase-design',
                     'diagnosing-bugs', 'domain-modeling'),
                '.claude/skills': ('file-pr', 'babysit-pr'),
                '.codex/skills': ('modernize', 'zero-tech-debt'),
            }
            for root, names in inventories.items():
                for name in names:
                    path = home / root / name / 'SKILL.md'
                    path.parent.mkdir(parents=True)
                    path.write_text(f'---\nname: {name}\ndescription: Procedure {name}\n---\n')
            available = {skill.name for skill in skill_picker.catalog(repo, home=home)}
            self.assert_workflow_skills_resolve(table, available)
            jobs = table['job_types']
            self.assertEqual(set(jobs), {'feature-ui', 'feature-backend', 'bug-fix',
                                        'removal-refactor', 'data-migration', 'review', 'research-audit'})
            for job_type, job in jobs.items():
                with self.subTest(job_type=job_type):
                    self.assertTrue(job['stages'])
                    if job['read_only']:
                        self.assertTrue({'pr', 'fix-rounds', 'ship'}.isdisjoint(job['stages']))
                        self.assertNotIn('mattpocock-skills:tdd', job['stages']['build']['skills'])
                        self.assertEqual(job['stages']['close']['artifact'], 'report_file')
                    else:
                        self.assertEqual(set(job['stages']),
                                         {'shape', 'build', 'pr', 'review', 'fix-rounds', 'ship', 'close'})
                        self.assertEqual(job['stages']['fix-rounds']['max_rounds'], 3)


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home, self.repo = Path(self.tmp.name) / 'home', Path(self.tmp.name) / 'repo'
        self.table = yaml.safe_load((Path(__file__).resolve().parents[1] /
                                    'ops/config/job-workflows.v1.yaml').read_text())
        table_path = self.repo / 'ops/config/job-workflows.v1.yaml'
        table_path.parent.mkdir(parents=True)
        table_path.write_text(yaml.safe_dump(self.table, sort_keys=False))
        self.texts = {}
        for name in ('pstack:poteto-mode', 'pstack:principle-prove-it-works',
                     'mattpocock-skills:grill-with-docs', 'mattpocock-skills:tdd',
                     'mattpocock-skills:pr', 'mattpocock-skills:code-review',
                     'mattpocock-skills:codebase-design', 'mattpocock-skills:diagnosing-bugs',
                     'mattpocock-skills:domain-modeling', 'file-pr', 'babysit-pr',
                     'modernize', 'zero-tech-debt', 'unrelated'):
            path = self.repo / '.claude/skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            self.texts[name] = f'---\nname: {name}\ndescription: Procedure {name}\n---\nInstructions for {name}\n'
            path.write_text(self.texts[name])

    def test_each_type_stamps_exact_table_skills_and_all_stage_requirements(self):
        for name, job in self.table['job_types'].items():
            with self.subTest(job_type=name):
                original = f'Type: {name}\nSkills: unrelated\nBuild the job.'
                brief, picks = skill_picker.prepare(original, self.repo, home=self.home)
                expected = list(dict.fromkeys(skill for stage in job['stages'].values()
                    for skill in stage['skills'] + [item['skill'] for item in
                                                   stage.get('conditional_skills', [])]))
                self.assertEqual(picks, [{'name': skill} for skill in expected])
                self.assertTrue(brief.startswith(original + '\n\n## Required skills\n'))
                stamped = brief.split('```yaml\n', 1)[1].split('\n```', 1)[0]
                self.assertEqual(yaml.safe_load(stamped), job)
                self.assertEqual(brief.count('### Workflow stages'), 1)
                self.assertNotIn('### unrelated', brief)
                for skill in expected:
                    self.assertEqual(brief.count('### ' + skill + '\n'), 1)
                    self.assertIn(self.texts[skill], brief)
                    self.assertIn(str((self.repo / '.claude/skills' / skill / 'SKILL.md').resolve()), brief)

    def test_missing_unknown_and_ambiguous_type_refuse_with_valid_types(self):
        for brief in ('Build it.', 'Type: nonexistent', 'Type:',
                      'Type: review\nType: bug-fix'):
            with self.subTest(brief=brief), self.assertRaises(ValueError) as error:
                skill_picker.prepare(brief, self.repo, home=self.home)
            for name in self.table['job_types']:
                self.assertIn(name, str(error.exception))

    def test_only_header_type_selects_the_workflow(self):
        for boundary in ('\n\n', '\n```text\n', '\n~~~text\n', '\n<work-request-text>\n', '\nWork Request: example\n'):
            with self.subTest(boundary=boundary):
                original = 'Card: WR594.S2.J3\nType: review' + boundary + 'Type: bug-fix'
                brief, picks = skill_picker.prepare(original, self.repo, home=self.home)
                self.assertNotIn({'name': 'mattpocock-skills:tdd'}, picks)
                with self.assertRaisesRegex(ValueError, 'brief requires one Type:'):
                    skill_picker.prepare('Card: WR594.S2.J3' + boundary + 'Type: review',
                                         self.repo, home=self.home)

    def test_worker_job_types_match_the_workflow_table(self):
        import json
        projection = Path(__file__).resolve().parents[1] / 'mcp-server/src/job-types.v1.json'
        self.assertEqual(json.loads(projection.read_text()), list(self.table['job_types']),
                         'Run tools/sync-job-types.py after changing the workflow table')

    def test_missing_required_skill_refuses_instead_of_partial_bundle(self):
        (self.repo / '.claude/skills/modernize/SKILL.md').unlink()
        with self.assertRaisesRegex(ValueError, 'required skill unavailable: modernize'):
            skill_picker.prepare('Type: feature-backend', self.repo, home=self.home)

    def test_prepare_makes_no_network_or_subprocess_calls(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')):
            brief, picks = skill_picker.prepare('Type: feature-ui', self.repo, home=self.home)
        self.assertEqual(len(picks), 10)
        self.assertIn('mattpocock-skills:codebase-design', brief)


if __name__ == '__main__':
    unittest.main()
