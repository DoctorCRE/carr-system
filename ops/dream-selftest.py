#!/usr/bin/env python3
import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dream', Path(__file__).with_name('dream.py'))
dream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dream)

class Store:
    def __init__(self):
        self.sections = {f'duplicate/{i}': {'section_id': str(i), 'section_key': f'duplicate/{i}', 'version': 1, 'content_hash': str(i), 'title': 'duplicate', 'body': {'text': 'same lesson'}} for i in range(5)}
        self.sections['stale'] = {'section_id': 'stale', 'section_key': 'stale', 'version': 1, 'content_hash': 'stale', 'title': 'stale', 'body': {'text': 'old script bin/fleet-sync.sh'}}
        self.sections['confirmed'] = {'section_id': 'confirmed', 'section_key': 'confirmed', 'version': 1, 'content_hash': 'confirmed', 'title': 'confirmed', 'body': {'text': 'confirmed lesson'}}
        self.applies = []; self.logs = []
    def read(self): return {'generation': 1, 'sections': list(self.sections.values())}
    def gate(self, item, attach): return True
    def apply(self, items, attach, key):
        self.applies.append(items)
        for item in items:
            if item['op'] == 'retire': self.sections.pop(item['section_key'])
            else:
                self.sections[item['section_key']]['body'] = {'text': item['body_text']}
                self.sections[item['section_key']]['version'] += 1
    def log(self, report, key): self.logs.append(report)

class DreamTest(unittest.TestCase):
    def run_fixture(self, *, moved=False, secret=False, missing_note=False):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            clone = dream.clone(store, Path(tmp) / 'mem')
            for i in range(1, 5): (clone / f'duplicate/{i}.md').unlink()
            (clone / 'stale.md').unlink()
            confirmed = clone / 'confirmed.md'
            confirmed.write_text(confirmed.read_text() + '\nVerified from session a\n')
            if secret: confirmed.write_text(confirmed.read_text() + '\n' + 'AKIA' + 'X' * 16)
            notes = [{'sections': [f'duplicate/{i}'], 'sessions': ['a'], 'note': 'duplicate'} for i in range(1, 5)] + [{'sections': ['stale'], 'sessions': ['b'], 'note': 'stale'}, {'sections': ['confirmed'], 'sessions': ['c'], 'note': 'confirmed'}]
            if missing_note: notes.pop()
            if moved: store.sections['duplicate/1']['version'] = 2
            report = dream.apply(store, clone, notes, ['a','b','c'], 'dream-attach', 'fixture')
            return store, report
    def test_full_dream_from_five_attaches_and_three_transcripts(self):
        store = Store()
        attach_ids = [f'synthetic-attach-{i}' for i in range(5)]
        for i, attach_id in enumerate(attach_ids):
            store.sections[f'duplicate/{i}']['body']['text'] = f'The same lesson from {attach_id}.'
        original = copy.deepcopy(store.sections)
        class Agent:
            def propose(self, session_id, transcript, memories):
                self_case.assertEqual(store.sections, original)
                self_case.assertEqual(store.applies, [])
                self_case.assertIn('synthetic transcript', transcript)
                if session_id == attach_ids[0]:
                    return [{'note': 'Merge duplicate lessons.', 'changes': [
                        {'section_key': 'duplicate/1', 'op': 'retire'},
                        {'section_key': 'duplicate/2', 'op': 'retire'}]}]
                if session_id == attach_ids[1]:
                    return [{'note': 'Merge remaining duplicate lessons.', 'changes': [
                        {'section_key': 'duplicate/3', 'op': 'retire'},
                        {'section_key': 'duplicate/4', 'op': 'retire'}]}]
                return [{'note': 'The old script is gone.', 'changes': [
                            {'section_key': 'stale', 'op': 'retire'}]},
                        {'note': 'Confirmed the lesson.', 'changes': [
                            {'section_key': 'confirmed', 'op': 'write', 'title': 'confirmed',
                             'body_text': 'confirmed lesson\nVerified 2026-01-01 from session synthetic-attach-2'}]}]
        self_case = self
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                path = Path(tmp) / f'session-{i}.jsonl'
                path.write_text('{"message":{"role":"user","content":"synthetic transcript ' + str(i) + '"}}\n')
                paths.append(path)
            result = dream.run(store, Path(tmp) / 'mem', list(zip(attach_ids[:3], paths)), Agent(), 'synthetic-dream-attach', 'synthetic-dream')
            self.assertEqual(len(store.applies), 1)
            self.assertEqual([k for k in store.sections if k.startswith('duplicate/')], ['duplicate/0'])
            self.assertNotIn('stale', store.sections)
            self.assertIn('Verified 2026-01-01 from session synthetic-attach-2', store.sections['confirmed']['body']['text'])
            self.assertEqual(result['changed'], 6)
            self.assertEqual(len(store.logs), 1)

    def test_fixture_applies_one_change_set(self):
        store, report = self.run_fixture()
        self.assertEqual(len(store.applies), 1)
        self.assertEqual([k for k in store.sections if k.startswith('duplicate/')], ['duplicate/0'])
        self.assertNotIn('stale', store.sections)
        self.assertIn('Verified', store.sections['confirmed']['body']['text'])
    def test_moved_unit_drops_while_others_apply(self):
        store, report = self.run_fixture(moved=True)
        self.assertIn('duplicate/1', store.sections)
        self.assertNotIn('duplicate/2', store.sections)
        self.assertEqual(report['dropped'], ['duplicate/1'])
        self.assertEqual(store.logs[0]['dropped'], ['duplicate/1'])
    def test_repeat_apply_is_a_no_op(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            notes = [{'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}]
            first = dream.apply(store, root, notes, ['a'], 'dream-attach', 'fixture')
            second = dream.apply(store, root, notes, ['a'], 'dream-attach', 'fixture')
            self.assertEqual(first, second)
            self.assertEqual(len(store.applies), 1)
            self.assertEqual(len(store.logs), 1)

    def test_moved_section_drops_entire_change_unit(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'duplicate/1.md').unlink()
            (root / 'duplicate/2.md').unlink()
            (root / 'stale.md').unlink()
            store.sections['duplicate/1']['version'] = 2
            notes = [{'sections': ['duplicate/1', 'duplicate/2'], 'sessions': ['a'], 'note': 'merge'},
                     {'sections': ['stale'], 'sessions': ['b'], 'note': 'obsolete'}]
            result = dream.apply(store, root, notes, ['a', 'b'], 'dream-attach', 'fixture')
            self.assertEqual(result['dropped'], ['duplicate/1', 'duplicate/2'])
            self.assertIn('duplicate/2', store.sections)
            self.assertNotIn('stale', store.sections)

    def test_proposals_use_condensed_transcripts_and_clone(self):
        store = Store()
        class Agent:
            def propose(self, session_id, transcript, memories):
                self.transcript = transcript
                return [{'note': 'confirmed', 'changes': [{'section_key': 'confirmed', 'op': 'write',
                         'title': memories['confirmed']['title'], 'body_text': 'Confirmed from session a.'}]}]
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            transcript = Path(tmp) / 'transcript.jsonl'
            transcript.write_text('{"message":{"role":"user","content":"synthetic confirmation"}}\n')
            agent = Agent()
            notes = dream.propose(root, [('a', transcript)], agent)
            self.assertIn('synthetic confirmation', agent.transcript)
            self.assertEqual(store.sections['confirmed']['body']['text'], 'confirmed lesson')
            dream.apply(store, root, notes, ['a'], 'dream-attach', 'fixture')
            self.assertEqual(store.sections['confirmed']['body']['text'], 'Confirmed from session a.')

    def test_overlapping_proposals_consolidate_provenance(self):
        class Agent:
            def propose(self, session_id, transcript, memories):
                return [{'note': transcript, 'changes': [{'section_key': 'confirmed', 'op': 'write',
                         'title': memories['confirmed']['title'], 'body_text': memories['confirmed']['body_text'] + transcript}]}]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store()
            root = dream.clone(store, Path(tmp) / 'mem')
            paths = [Path(tmp) / f'{i}.jsonl' for i in range(2)]
            for i, path in enumerate(paths):
                path.write_text('{"message":{"role":"user","content":"synthetic ' + str(i) + '"}}\n')
            notes = dream.propose(root, [('a', paths[0]), ('b', paths[1])], Agent())
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]['sessions'], ['a', 'b'])
            dream.apply(store, root, None, ['a', 'b'], 'attach', 'fixture')
            self.assertIn('synthetic 0', store.sections['confirmed']['body']['text'])
            self.assertIn('synthetic 1', store.sections['confirmed']['body']['text'])

    def test_reverted_proposal_leaves_no_change(self):
        class Agent:
            def __init__(self): self.calls = 0
            def propose(self, session_id, transcript, memories):
                self.calls += 1
                return [{'note': 'reconsidered', 'changes': [{'section_key': 'confirmed', 'op': 'write',
                    'title': memories['confirmed']['title'], 'body_text': 'temporary' if self.calls == 1 else 'confirmed lesson'}]}]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store()
            root = dream.clone(store, Path(tmp) / 'mem')
            path = Path(tmp) / 'session.jsonl'
            path.write_text('{"message":{"role":"user","content":"synthetic"}}\n')
            dream.propose(root, [('a', path), ('b', path)], Agent())
            self.assertEqual(dream.apply(store, root, None, ['a', 'b'], 'attach', 'fixture')['changed'], 0)
            self.assertEqual(store.applies, [])

    def test_unsafe_state_directory_is_refused(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            state = root.parent / '.mem-dream-state'
            state.chmod(0o755)
            with self.assertRaises(ValueError): dream.apply(store, root, [], [], 'attach', 'fixture')
            self.assertEqual(store.applies, [])

    def test_resume_after_log_failure_uses_the_same_change_set(self):
        class RetryStore(Store):
            def __init__(self):
                super().__init__(); self.saved = {}; self.fail_log = True
            def apply(self, items, attach, key):
                if key in self.saved:
                    self.assert_same = self.saved[key] == items
                    return
                self.saved[key] = items
                super().apply(items, attach, key)
            def log(self, report, key):
                if self.fail_log:
                    self.fail_log = False
                    raise RuntimeError('synthetic failure')
                super().log(report, key)
        store = RetryStore()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            notes = [{'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}]
            with self.assertRaises(RuntimeError): dream.apply(store, root, notes, ['a'], 'attach', 'fixture')
            dream.apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertTrue(store.assert_same)
            self.assertEqual(len(store.applies), 1)
            self.assertEqual(len(store.logs), 1)

    def test_write_race_rebases_only_the_conflicted_unit(self):
        class RacingStore(Store):
            def __init__(self): super().__init__(); self.raced = False
            def apply(self, items, attach, key):
                if not self.raced:
                    self.raced = True
                    self.sections['duplicate/1']['version'] += 1
                    raise dream.VersionConflict()
                super().apply(items, attach, key)
        store = RacingStore()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'duplicate/1.md').unlink()
            (root / 'stale.md').unlink()
            notes = [{'sections': ['duplicate/1'], 'sessions': ['a'], 'note': 'merge'},
                     {'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}]
            report = dream.apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(report['dropped'], ['duplicate/1'])
            self.assertIn('duplicate/1', store.sections)
            self.assertNotIn('stale', store.sections)
            self.assertEqual(len(store.applies), 1)

    def test_git_location_variables_cannot_redirect_clone(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'mem'
            poison = Path(tmp) / 'wrong-git-dir'
            with patch.dict(os.environ, {'GIT_DIR': str(poison), 'GIT_INDEX_FILE': str(Path(tmp) / 'wrong-index')}):
                dream.clone(store, root)
            self.assertTrue((root / '.git' / 'HEAD').exists())
            self.assertFalse(poison.exists())
            self.assertFalse((Path(tmp) / 'wrong-index').exists())

    def test_retired_key_conflict_drops_its_unit(self):
        class RetiredStore(Store):
            def apply(self, items, attach, key):
                if any(item['section_key'] == 'retired section' for item in items):
                    raise dream.VersionConflict('retired section')
                super().apply(items, attach, key)
        store = RetiredStore()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'retired section.md').write_text('---\n{"section_id": null, "section_key": "retired section", "title": "new", "version": 0, "content_hash": null}\n---\nnew')
            (root / 'stale.md').unlink()
            notes = [{'sections': ['retired section'], 'sessions': ['a'], 'note': 'new'},
                     {'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}]
            result = dream.apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(result['dropped'], ['retired section'])
            self.assertNotIn('stale', store.sections)

    def test_section_keys_with_spaces_and_periods(self):
        store = Store()
        store.sections['example/lesson v1.2'] = {'section_id': 'dot', 'section_key': 'example/lesson v1.2', 'title': 'dot', 'version': 1, 'content_hash': 'dot', 'body': {'text': 'lesson'}}
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'example/lesson v1.2.md').unlink()
            notes = [{'sections': ['example/lesson v1.2'], 'sessions': ['a'], 'note': 'obsolete'}]
            dream.apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertNotIn('example/lesson v1.2', store.sections)

    def test_untrusted_pending_file_cannot_bypass_verification(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            path = root / '_dream' / 'pending.json'
            path.parent.mkdir()
            path.write_text('{"key":"fixture","attach":"attach","changes":[{"section_key":"stale","op":"retire","base_version":1}],"report":{}}')
            self.assertEqual(dream.apply(store, root, [], ['a'], 'attach', 'fixture')['changed'], 0)
            self.assertIn('stale', store.sections)
            self.assertEqual(store.applies, [])

    def test_failing_gate_applies_nothing(self):
        store = Store()
        store.gate = lambda item, attach: False
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            with self.assertRaises(ValueError):
                dream.apply(store, root, [{'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}], ['a'], 'attach', 'fixture')
            self.assertEqual(store.applies, [])
            self.assertIn('stale', store.sections)

    def test_changed_front_matter_refuses(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            path = root / 'confirmed.md'
            path.write_text(path.read_text().replace('"version": 1', '"version": 2'))
            with self.assertRaises(ValueError): dream.apply(store, root, [], ['a'], 'attach', 'fixture')
            self.assertEqual(store.applies, [])

    def test_secret_and_missing_provenance_refuse_everything(self):
        for case in ('secret', 'missing_note'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                store = Store()
                root = dream.clone(store, Path(tmp) / 'mem')
                original = store.sections['confirmed']['body']['text']
                path = root / 'confirmed.md'
                suffix = ('AKIA' + 'X' * 16 + ' ci-secret-scan: allow') if case == 'secret' else 'changed'
                path.write_text(path.read_text() + '\n' + suffix)
                notes = [{'sections': ['confirmed'], 'sessions': ['a'], 'note': 'confirmed'}] if case == 'secret' else []
                with self.assertRaises(ValueError): dream.apply(store, root, notes, ['a'], 'attach', 'fixture')
                self.assertEqual(store.applies, [])
                self.assertEqual(store.sections['confirmed']['body']['text'], original)
                self.assertEqual(store.logs, [])

    def test_state_symlink_is_refused(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            state = root.parent / '.mem-dream-state'
            (state / 'pending.json').symlink_to(root / 'confirmed.md')
            with self.assertRaises(ValueError): dream.apply(store, root, [], [], 'attach', 'fixture')
            self.assertEqual(store.applies, [])


if __name__ == '__main__': unittest.main()
