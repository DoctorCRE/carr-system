#!/usr/bin/env python3
"""Exercise dream cloning, verification, application, and recovery."""
import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dream', Path(__file__).with_name('dream.py'))
assert spec and spec.loader
dream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dream)

class Store:
    def __init__(self):
        self.sections = {f'duplicate/{i}': {'section_id': str(i), 'section_key': f'duplicate/{i}', 'version': 1, 'content_hash': str(i), 'title': 'duplicate', 'body': {'text': 'same lesson'}} for i in range(5)}
        self.sections['stale'] = {'section_id': 'stale', 'section_key': 'stale', 'version': 1, 'content_hash': 'stale', 'title': 'stale', 'body': {'text': 'old script bin/fleet-sync.sh'}}
        self.sections['confirmed'] = {'section_id': 'confirmed', 'section_key': 'confirmed', 'version': 1, 'content_hash': 'confirmed', 'title': 'confirmed', 'body': {'text': 'confirmed lesson'}}
        self.applies = []; self.logs = []
    def read(self): return {'generation': 1, 'sections': list(self.sections.values())}
    def gate(self, item, attach, section_id=None): return True
    def apply(self, items, attach, key):
        self.applies.append(items)
        for item in items:
            if item['op'] == 'retire': self.sections.pop(item['section_key'])
            else:
                self.sections[item['section_key']]['body'] = {'text': item['body_text']}
                self.sections[item['section_key']]['version'] += 1
    def log(self, report, key): self.logs.append(report)

def fixture_apply(store, root, notes, sessions, attach, key):
    """Legacy safety fixtures exercise inference edits, not factual support."""
    if notes and all(isinstance(unit, dict) and unit.get('sections') for unit in notes):
        for unit in notes:
            claims = []
            for section in unit['sections']:
                path = root / (section + '.md')
                text = dream._parse(path.read_text())[1] if path.exists() else unit['note']
                if path.exists():
                    title = dream._parse(path.read_text())[0].get('title')
                    if title: text += '\n' + title
                claims.append({'section_key': section, 'text': text, 'label': 'inference',
                               'anchors': [{'transcript_ref': 'synthetic-fixture', 'entry_index': 0, 'item_index': 0}],
                               'supersedes': {'section_key': section, 'version': 1} if section not in ('new lesson', 'retired section') else None})
            unit['claims'] = claims
    observation = {'anchor': {'transcript_ref': 'synthetic-fixture', 'entry_index': 0, 'item_index': 0},
                   'kind': 'user', 'text': 'Synthetic fixture change requested'}
    return dream.apply(store, root, notes, {session: [observation] for session in sessions}, attach, key)


class DreamTest(unittest.TestCase):
    def test_condense_preserves_bounded_redacted_tool_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.jsonl'
            records = [
                {'message': {'role': 'assistant', 'content': [
                    {'type': 'text', 'text': 'tests passed'},
                    {'type': 'tool_use', 'id': 'test', 'name': 'Bash',
                     'input': {'command': 'python3 suite.py'}}]}},
                {'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 'test', 'content':
                     'boring log\n' * 1000 + 'Exit code: 1\nFAILED test_memory\nTests: 1 failed, 4 passed\nError: ' + 'AKIA' + 'X' * 16}]}},
                {'type': 'response_item', 'payload': {'type': 'function_call',
                 'name': 'exec_command', 'arguments': '{"cmd":"pytest"}'}},
                {'type': 'response_item', 'payload': {'type': 'function_call_output',
                 'output': 'Process exited with code 1\nFAILED test_other'}},
            ]
            path.write_text('\n'.join(json.dumps(r) for r in records))
            evidence = dream.condense(path, transcript_ref='fixture-session', max_tokens=1000)
            text = json.dumps(evidence)
            for expected in ('python3 suite.py', 'Exit code: 1', 'FAILED test_memory',
                             '1 failed, 4 passed', 'pytest', 'FAILED test_other'):
                self.assertIn(expected, text)
            self.assertNotIn('AKIA' + 'X' * 16, text)
            self.assertNotIn('boring log', text)
            self.assertLessEqual(len(text), 4000)
            self.assertTrue(all(e['anchor']['transcript_ref'] == 'fixture-session' for e in evidence))
            self.assertEqual(evidence, dream.condense(path, transcript_ref='fixture-session', max_tokens=1000))

    def test_condense_keeps_structured_exit_status_and_hard_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.jsonl'
            path.write_text(json.dumps({'message': {'role': 'tool', 'content': {
                'exit_code': 2, 'stdout': 'noise\n' * 1000,
                'stderr': 'AssertionError: expected 3 tests\nValueError: bad shape'}}}))
            evidence = dream.condense(path, max_tokens=1000)
            self.assertIn('exit_code: 2', json.dumps(evidence))
            self.assertIn('AssertionError', json.dumps(evidence))
            self.assertIn('ValueError: bad shape', json.dumps(evidence))
            self.assertNotIn('noise', json.dumps(evidence))
            self.assertEqual(dream.condense(path, max_tokens=1), [])

    def test_unsupported_claim_with_allowed_session_is_rejected(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            path = root / 'confirmed.md'
            path.write_text(path.read_text() + '\nUnsupported assertion is verified')
            notes = [{'sections': ['confirmed'], 'sessions': ['a'], 'note': 'verified'}]
            with self.assertRaisesRegex(ValueError, 'claim'):
                dream.apply(store, root, notes, {'a': []}, 'attach', 'fixture')
            self.assertEqual(store.applies, [])

    def test_claim_support_and_labels(self):
        for case in ('supported', 'no_anchor', 'unknown_anchor', 'wrong_session',
                     'passed', 'uncovered', 'supersedes', 'assistant', 'mixed', 'inference', 'quoted'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                store = Store()
                root = dream.clone(store, Path(tmp) / 'mem')
                transcript = Path(tmp) / 'session.jsonl'
                transcript.write_text(json.dumps({'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'content': 'Exit code: 1\nTests failed'}]}}))
                evidence = dream.condense(transcript)
                if case == 'assistant': evidence[0]['kind'] = 'assistant'
                if case == 'mixed': evidence[0]['text'] = 'Tests: 4 passed, 1 failed'
                text = 'Tests passed' if case in ('passed', 'mixed') else 'Tests failed'
                label = case if case in ('inference', 'quoted') else 'observed'
                if case == 'inference': text = 'The suite needs repair'
                path = root / 'confirmed.md'
                path.write_text(path.read_text() + '\n' + text + ('\nHidden claim' if case == 'uncovered' else ''))
                claim = {'section_key': 'confirmed', 'text': text, 'label': label,
                         'anchors': [copy.deepcopy(evidence[0]['anchor'])],
                         'supersedes': {'section_key': 'confirmed', 'version': 1}}
                if case == 'no_anchor': claim['anchors'] = []
                if case == 'unknown_anchor': claim['anchors'][0]['entry_index'] = 99
                if case == 'supersedes': claim['supersedes']['version'] = 99
                notes = [{'sections': ['confirmed'], 'sessions': ['b' if case == 'wrong_session' else 'a'],
                          'note': 'test result', 'claims': [claim]}]
                inputs = {'a': evidence, 'b': []}
                if case in ('supported', 'inference', 'quoted'):
                    report = dream.apply(store, root, notes, inputs, 'attach', 'fixture')
                    self.assertEqual(report['changed'], 1)
                    saved = root.parent / '.mem-dream-state' / 'evidence.json'
                    self.assertEqual(json.loads(saved.read_text()), inputs)
                    body = store.sections['confirmed']['body']['text']
                    self.assertIn(label, body)
                    self.assertIn(evidence[0]['anchor']['transcript_ref'], body)
                else:
                    with self.assertRaises(ValueError):
                        dream.apply(store, root, notes, inputs, 'attach', 'fixture')
                    self.assertEqual(store.applies, [])

    def test_success_claims_follow_totals_markers_and_exit_status(self):
        outcomes = [
            ('Tests: 4 passed, 1 FAIL\nProcess exited with code 1', False),
            ('Tests: 4 passed, 0 failed\nExit code: 0', True),
            ('Tests: 4 passed, 0 failed\nexit_code: 2', False),
            ('Tests: 4 passed, 0 failed\nreturncode: -1', False),
            ('Tests: 4 passed, 0 failed\nreturn code: 3', False),
            ('Tests: 4 passed, 0 failed\nExit status: 7', False),
            ('Tests: 4 passed, 2 failures\nExit code: 0', False),
            ('Tests: 4 passed, failures=1, errors=0', False),
            ('Tests: 4 passed, 0 failed\nFAIL test_memory', False),
            ('Tests: 4 passed, 0 failed\n✖ test_memory', False),
            ('Tests: 4 passed, 0 failed\nnot ok 1 - memory', False),
            ('Tests: 4 passed, failed=0, errors=0\nexit_code: 0', True),
            ('ℹ tests 4\nℹ pass 4\nℹ fail 0\nExit code: 0', True),
            ('ℹ tests 4\nℹ pass 3\nℹ fail 1\nExit code: 0', False),
            ({'stdout': 'Tests: 4 passed, 0 failed', 'exit_code': 1}, False),
            ({'stdout': 'Tests: 4 passed, 0 failed', 'exit_code': 0}, True),
            ({'stdout': 'Tests: 4 passed, 0 failed', 'returncode': -9}, False),
        ]
        for label in ('observed', 'quoted', 'inference'):
            for success in ('Tests passed', 'Tests succeeded', 'Tests green', 'Tests all pass'):
                for output, passes in outcomes:
                    with self.subTest(label=label, success=success, output=output), tempfile.TemporaryDirectory() as tmp:
                        store = Store()
                        root = dream.clone(store, Path(tmp) / 'mem')
                        transcript = Path(tmp) / 'session.jsonl'
                        content = dict(output) if isinstance(output, dict) else {'stdout': output}
                        content['stdout'] = success + '\n' + content['stdout']
                        transcript.write_text(json.dumps({'message': {'role': 'tool', 'content': content}}))
                        evidence = dream.condense(transcript)
                        claim = {'section_key': 'confirmed', 'text': success, 'label': label,
                                 'anchors': [evidence[0]['anchor']],
                                 'supersedes': {'section_key': 'confirmed', 'version': 1}}
                        notes = [{'sections': ['confirmed'], 'sessions': ['a'], 'note': 'result', 'claims': [claim]}]
                        path = root / 'confirmed.md'
                        path.write_text(path.read_text() + '\n' + success)
                        if passes:
                            self.assertEqual(dream.apply(store, root, notes, {'a': evidence}, 'attach', 'fixture')['changed'], 1)
                        else:
                            with self.assertRaises(ValueError):
                                dream.apply(store, root, notes, {'a': evidence}, 'attach', 'fixture')
                            self.assertEqual(store.applies, [])

    def test_private_key_payloads_never_reach_evidence_or_memory(self):
        for kind in ('PRIVATE KEY', 'RSA PRIVATE KEY', 'EC PRIVATE KEY', 'DSA PRIVATE KEY',
                     'ENCRYPTED PRIVATE KEY', 'OPENSSH PRIVATE KEY', 'PGP PRIVATE KEY BLOCK'):
            for form in ('complete', 'truncated', 'split'):
                with self.subTest(kind=kind, form=form), tempfile.TemporaryDirectory() as tmp:
                    begin = '-----' + 'BEGIN ' + kind + '-----'
                    end = '-----' + 'END ' + kind + '-----'
                    first, second = 'SYNTHETIC_KEY_PAYLOAD_ONE', 'SYNTHETIC_KEY_PAYLOAD_TWO'
                    start = 'Tests passed\nError: ' + begin + '\nError: ' + first
                    finish = '\nError: ' + second + '\nError: ' + end + '\nTests passed'
                    outputs = [start, finish] if form == 'split' else [start + (finish if form == 'complete' else '\nError: ' + second * 5000)]
                    transcript = Path(tmp) / 'session.jsonl'
                    transcript.write_text('\n'.join(json.dumps({'message': {'role': 'tool', 'content': output}}) for output in outputs))
                    evidence = dream.condense(transcript, max_tokens=1000)
                    serialized = json.dumps(evidence)
                    for payload in (first, second, begin, end): self.assertNotIn(payload, serialized)
                    self.assertLessEqual(len(serialized), 4000)
                    store = Store()
                    root = dream.clone(store, Path(tmp) / 'mem')
                    path = root / 'confirmed.md'
                    original = path.read_text()
                    claim = {'section_key': 'confirmed', 'text': first, 'label': 'observed',
                             'anchors': [evidence[0]['anchor']],
                             'supersedes': {'section_key': 'confirmed', 'version': 1}}
                    notes = [{'sections': ['confirmed'], 'sessions': ['a'], 'note': 'result', 'claims': [claim]}]
                    path.write_text(original + '\n' + first)
                    with self.assertRaises(ValueError):
                        dream.apply(store, root, notes, {'a': evidence}, 'attach', 'fixture')
                    claim['label'] = 'inference'
                    claim['text'] = 'Tests require review'
                    path.write_text(original + '\nTests require review')
                    dream.apply(store, root, notes, {'a': evidence}, 'attach', 'fixture')
                    saved = (root.parent / '.mem-dream-state' / 'evidence.json').read_text()
                    for payload in (first, second, begin, end):
                        self.assertNotIn(payload, saved + store.sections['confirmed']['body']['text'])

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
            report = fixture_apply(store, clone, notes, ['a','b','c'], 'dream-attach', 'fixture')
            return store, report
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
        self.assertEqual([{k: v for k, v in unit.items() if k != 'claims'} for unit in report['dropped']],
                         [{'sections': ['duplicate/1'], 'sessions': ['a'], 'note': 'duplicate'}])
        self.assertEqual(store.logs[0]['dropped'], report['dropped'])
    def test_case_colliding_keys_are_refused_and_logged(self):
        store = Store()
        section = copy.deepcopy(store.sections['stale'])
        section['section_key'] = 'Stale'
        store.sections['Stale'] = section
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'case_colliding_section_keys'):
                dream.clone(store, Path(tmp) / 'mem', 'fixture')
            self.assertEqual(store.logs[0]['error'], 'case_colliding_section_keys')
            self.assertEqual(store.applies, [])

    def test_clone_retries_same_path_after_case_collision(self):
        store = Store()
        section = copy.deepcopy(store.sections['stale'])
        section['section_key'] = 'Stale'
        store.sections['Stale'] = section
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'mem'
            with self.assertRaisesRegex(ValueError, 'case_colliding_section_keys'):
                dream.clone(store, root, 'fixture')
            del store.sections['Stale']
            self.assertEqual(dream.clone(store, root, 'fixture'), root)
            self.assertEqual(fixture_apply(store, root, [], ['a'], 'attach', 'fixture')['changed'], 0)

    def test_error_and_success_logs_use_distinct_idempotency_keys(self):
        calls = []
        class Response:
            returncode = 0
            stdout = '{"ok": true}'
        def call(verb, args):
            calls.append(args)
            return Response()
        store = dream.VerbStore(call)
        store.log({'error': 'case_colliding_section_keys'}, 'night')
        store.log({'changed': 1}, 'night')
        store.log({'error': 'version_conflict', 'attempts': 3}, 'night')
        store.log({'attempts': 3, 'error': 'version_conflict'}, 'night')
        self.assertNotEqual(calls[0]['idempotency_key'], calls[1]['idempotency_key'])
        self.assertNotEqual(calls[0]['section_key'], calls[1]['section_key'])
        self.assertNotEqual(calls[0]['idempotency_key'], calls[2]['idempotency_key'])
        self.assertNotEqual(calls[0]['section_key'], calls[2]['section_key'])
        self.assertEqual(calls[2], calls[3])

    def test_malformed_notes_refused_before_apply(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            with self.assertRaisesRegex(ValueError, 'invalid change unit'):
                fixture_apply(store, root, [{}], ['a'], 'attach', 'fixture')
            self.assertEqual(store.applies, [])

    def test_apply_gates_use_one_current_snapshot(self):
        source = Store()
        calls = []
        def call(verb, args):
            calls.append((verb, args))
            result = source.read() if verb == 'read-doctrine' else {'result': 'pass'}
            return subprocess.CompletedProcess([], 0, json.dumps({'ok': True, **result}), '')
        store = dream.VerbStore(call)
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            path = root / 'confirmed.md'
            path.write_text(path.read_text() + '\nconfirmed again')
            (root / 'new lesson.md').write_text('---\n{"section_key": "new lesson", "title": "new", "version": 0}\n---\nnew lesson')
            notes = [{'sections': ['confirmed', 'new lesson', 'stale'], 'sessions': ['a'], 'note': 'update lessons'}]
            report = fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(report['changed'], 3)
        self.assertEqual(sum(verb == 'read-doctrine' for verb, _ in calls), 2)
        gates = [args for verb, args in calls if verb == 'dry-run-doctrine-gates']
        self.assertTrue(all('Dream evidence' in g['body_text'] for g in gates if g['op'] == 'write'))
        gates = [{**g, 'body_text': g['body_text'].split('\n\nDream evidence')[0]} if 'body_text' in g else g for g in gates]
        self.assertEqual(gates, [
            {'document': 'team-memory', 'op': 'write', 'attach_id': 'attach',
             'section_id': 'confirmed', 'body_text': 'confirmed lesson\nconfirmed again'},
            {'document': 'team-memory', 'op': 'write', 'attach_id': 'attach', 'body_text': 'new lesson'},
            {'document': 'team-memory', 'op': 'retire', 'attach_id': 'attach', 'section_id': 'stale'},
        ])

    def test_repeat_apply_is_a_no_op(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            notes = [{'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}]
            first = fixture_apply(store, root, notes, ['a'], 'dream-attach', 'fixture')
            second = fixture_apply(store, root, notes, ['a'], 'dream-attach', 'fixture')
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
            result = fixture_apply(store, root, notes, ['a', 'b'], 'dream-attach', 'fixture')
            self.assertEqual(result['dropped'], [notes[0]])
            self.assertEqual(result['notes'], [notes[1]])
            self.assertEqual(store.logs[0], result)
            self.assertIn('duplicate/2', store.sections)
            self.assertNotIn('stale', store.sections)

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
            with self.assertRaises(RuntimeError): fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
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
            report = fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(report['dropped'], [notes[0]])
            self.assertIn('duplicate/1', store.sections)
            self.assertNotIn('stale', store.sections)
            self.assertEqual(len(store.applies), 1)

    def test_exhausted_conflicts_are_logged_and_can_resume(self):
        class RacingStore(Store):
            def __init__(self):
                super().__init__()
                self.conflicts = iter(['duplicate/1', 'duplicate/2', 'duplicate/3'])
            def apply(self, items, attach, key):
                conflict = next(self.conflicts, None)
                if conflict:
                    raise dream.VersionConflict(conflict)
                super().apply(items, attach, key)
        store = RacingStore()
        before = copy.deepcopy(store.sections)
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            keys = ['duplicate/1', 'duplicate/2', 'duplicate/3', 'stale']
            for key in keys:
                (root / (key + '.md')).unlink()
            notes = [{'sections': [key], 'sessions': ['a'], 'note': 'obsolete'} for key in keys]
            with self.assertRaises(dream.VersionConflict) as raised:
                fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(raised.exception.section_key, 'duplicate/3')
            self.assertEqual(store.sections, before)
            self.assertEqual(store.logs, [{'error': 'version_conflict', 'attempts': 3,
                                         'section_key': 'duplicate/3', 'notes': [],
                                         'dropped': notes[:2], 'changed': 0}])
            report = fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(report, {'notes': notes[2:], 'dropped': notes[:2], 'changed': 2})
            self.assertEqual(store.logs[-1], report)
            self.assertEqual(len(store.applies), 1)
            self.assertNotIn('stale', store.sections)

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
            result = fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertEqual(result['dropped'], [notes[0]])
            self.assertNotIn('stale', store.sections)

    def test_section_keys_with_spaces_and_periods(self):
        store = Store()
        store.sections['example/lesson v1.2'] = {'section_id': 'dot', 'section_key': 'example/lesson v1.2', 'title': 'dot', 'version': 1, 'content_hash': 'dot', 'body': {'text': 'lesson'}}
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'example/lesson v1.2.md').unlink()
            notes = [{'sections': ['example/lesson v1.2'], 'sessions': ['a'], 'note': 'obsolete'}]
            fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
            self.assertNotIn('example/lesson v1.2', store.sections)

    def test_untrusted_pending_file_cannot_bypass_verification(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            path = root / '_dream' / 'pending.json'
            path.parent.mkdir()
            path.write_text('{"key":"fixture","attach":"attach","changes":[{"section_key":"stale","op":"retire","base_version":1}],"report":{}}')
            self.assertEqual(fixture_apply(store, root, [], ['a'], 'attach', 'fixture')['changed'], 0)
            self.assertIn('stale', store.sections)
            self.assertEqual(store.applies, [])

    def test_failing_gate_applies_nothing(self):
        store = Store()
        store.gate = lambda item, attach, section_id=None: False
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            with self.assertRaises(ValueError):
                fixture_apply(store, root, [{'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}], ['a'], 'attach', 'fixture')
            self.assertEqual(store.applies, [])
            self.assertIn('stale', store.sections)

    def test_changed_front_matter_refuses(self):
        store = Store()
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            path = root / 'confirmed.md'
            path.write_text(path.read_text().replace('"version": 1', '"version": 2'))
            with self.assertRaises(ValueError): fixture_apply(store, root, [], ['a'], 'attach', 'fixture')
            self.assertEqual(store.applies, [])

    def test_existing_secret_does_not_block_unrelated_change(self):
        store = Store()
        store.sections['confirmed']['body']['text'] = 'AKIA' + 'X' * 16
        with tempfile.TemporaryDirectory() as tmp:
            root = dream.clone(store, Path(tmp) / 'mem')
            (root / 'stale.md').unlink()
            report = fixture_apply(store, root, [{'sections': ['stale'], 'sessions': ['a'], 'note': 'obsolete'}], ['a'], 'attach', 'fixture')
            self.assertEqual(report['changed'], 1)
            self.assertNotIn('stale', store.sections)

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
                with self.assertRaises(ValueError): fixture_apply(store, root, notes, ['a'], 'attach', 'fixture')
                self.assertEqual(store.applies, [])
                self.assertEqual(store.sections['confirmed']['body']['text'], original)
                self.assertEqual(store.logs, [])



if __name__ == '__main__': unittest.main()
