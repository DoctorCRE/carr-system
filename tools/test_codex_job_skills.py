"""Send-time skill selection against temporary catalogs and a fake SDK."""
import asyncio
import os
import sys
from types import SimpleNamespace
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

    def test_env_file_fallback_reaches_sdk_without_changing_process_environment(self):
        env_file = self.home / '.config/carr/typesafe.env'
        env_file.parent.mkdir(parents=True)
        env_file.write_text('TYPESAFE_API_KEY=fixture-file-key\nUNRELATED=fixture-only\n')
        env_file.chmod(0o600)
        with patch.dict(os.environ, {}, clear=True), \
             patch('typesafe_sdk.AsyncTypeSafeClient', return_value=self.client({'skill-0': .95})()) as sdk:
            brief, picks = self.prepare()
            self.assertNotIn('TYPESAFE_API_KEY', os.environ)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(sdk.call_args.kwargs['api_key'], 'fixture-file-key')
        self.assertIn({'name': 'skill-0', 'probability': .95}, picks)
        self.assertNotIn('fixture-file-key', brief)
        self.assertNotIn('picker skipped:', brief)

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
