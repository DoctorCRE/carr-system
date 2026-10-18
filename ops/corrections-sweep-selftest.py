#!/usr/bin/env python3
"""Synthetic native transcripts exercise the correction audit's interface."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('sweep', Path(__file__).with_name('corrections-sweep.py'))
if spec is None or spec.loader is None:
    raise RuntimeError('correction collector could not be loaded')
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


class CorrectionAuditTests(unittest.TestCase):
    def scan(self, family, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.jsonl'
            path.write_text('\n'.join(map(json.dumps, rows)) + '\n')
            return sweep.scan_transcripts({'claude': [path] if family == 'claude' else [],
                                           'codex': [path] if family == 'codex' else []},
                                          '2026-10-01', '2026-10-02')

    def test_short_and_long_corrections_survive(self):
        long_text = '# Review\n' + 'Use the verified source. ' * 100
        rows = [{'type': 'user', 'timestamp': '2026-10-01T12:00:00Z', 'uuid': 'one', 'message': {'content': 'no'}},
                {'type': 'user', 'timestamp': '2026-10-01T12:00:01Z', 'uuid': 'two', 'message': {'content': long_text}},
                {'type': 'assistant', 'timestamp': '2026-10-01T12:00:02Z', 'message': {'content': 'wrong'}},
                {'type': 'user', 'timestamp': '2026-10-01T12:00:03Z', 'message': {'content': [{'type': 'tool_result', 'content': 'wrong'}]}}]
        result = self.scan('claude', rows)
        self.assertEqual([row['text'] for row in result['turns']], ['no', long_text.strip()])
        self.assertEqual(result['coverage']['claude']['files_read'], 1)

    def test_codex_ignores_guardian_and_duplicate_event(self):
        message = {'type': 'response_item', 'timestamp': '2026-10-01T00:00:00Z', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'That is wrong.'}]}}
        rows = [{'type': 'session_meta', 'payload': {'id': 'task', 'source': 'exec'}}, message,
                {'type': 'event_msg', 'timestamp': message['timestamp'], 'payload': {'type': 'user_message', 'message': 'That is wrong.'}}]
        self.assertEqual(len(self.scan('codex', rows)['turns']), 1)
        rows[0]['payload']['source'] = {'subagent': {'other': 'guardian'}}
        self.assertEqual(self.scan('codex', rows)['turns'], [])

    def test_half_open_date_window(self):
        rows = [{'type': 'user', 'timestamp': '2026-10-02T00:00:00Z', 'message': {'content': 'wrong'}}]
        self.assertEqual(self.scan('claude', rows)['turns'], [])

    def test_copies_merge_refs_and_repeat_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('a.jsonl', 'b.jsonl')]
            rows = [{'type': 'user', 'uuid': key, 'sessionId': 'session', 'timestamp': f'2026-10-01T12:00:0{i}Z', 'message': {'content': 'no'}} for i, key in enumerate(('same', 'again'))]
            for path in paths:
                path.write_text('\n'.join(map(json.dumps, rows)) + '\n')
            result = sweep.scan_transcripts({'claude': paths, 'codex': []}, '2026-10-01', '2026-10-02')
            self.assertEqual(len(result['turns']), 2)
            self.assertEqual(len(result['turns'][0]['sources']), 2)

    def test_review_requires_upstream_route(self):
        turns = [{'id': 'one'}]
        review = [{'id': 'one', 'disposition': 'correction', 'partner': 'joe', 'route': 'stale_source', 'source_refs': ['source:version:3'], 'mechanism': 'output_patch', 'fix': 'rewrite the sentence'}]
        self.assertTrue(sweep.review_gaps(turns, review))
        review[0].update(mechanism='source_version_check', fix='Read current source before relying on the dated record.')
        self.assertEqual(sweep.review_gaps(turns, review), [])
        self.assertTrue(sweep.review_gaps(turns, []))

    def test_blank_refs_and_fix_cannot_pass_review(self):
        row = {'id': 'one', 'disposition': 'correction', 'partner': 'joe',
               'route': 'capture', 'mechanism': 'capture_contract',
               'source_refs': ' ', 'fix': ' '}
        self.assertTrue(sweep.review_gaps([{'id': 'one'}], [row]))
        row.update(source_refs=['source:version:3'], fix='Validate capture status.')
        row.pop('partner')
        self.assertTrue(sweep.review_gaps([{'id': 'one'}], [row]))

    def test_non_object_metadata_and_conduct_are_gaps(self):
        result = self.scan('codex', [{'type': 'session_meta', 'payload': []}])
        self.assertEqual(result['gaps'][0]['reason'], 'invalid session metadata')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'conduct.jsonl'
            path.write_text('[]\n')
            result = sweep.scan_conduct(path, '2026-10-01', '2026-10-02')
            self.assertEqual(result['gaps'][0]['reason'], 'non-object conduct row')

    def test_nested_invalid_native_fields_are_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.jsonl'
            path.write_text(json.dumps({'type': 'session_meta', 'payload': {'cwd': ['synthetic']}}) + '\n')
            result = sweep.scan_transcripts({'codex': [path]}, '2026-10-01', '2026-10-02', cwd_roots={Path(directory).resolve()})
            self.assertEqual(result['gaps'][0]['reason'], 'invalid session cwd')
            path.write_text(json.dumps({'ts': '2026-10-01T12:00:00Z', 'classes': None}) + '\n')
            result = sweep.scan_conduct(path, '2026-10-01', '2026-10-02')
            self.assertEqual(result['gaps'][0]['reason'], 'invalid conduct classes')
        self.assertTrue(sweep.review_gaps([{'id': 'one'}], [None, {'id': []}]))

    def test_private_artifact_refuses_checkout(self):
        with self.assertRaises(ValueError):
            sweep.write_private(Path(sweep.REPO) / 'out' / 'retro-evidence.json', {'text': 'synthetic'})

    def test_activity_pages_and_search_caps_remain_explicit(self):
        calls = []
        def read(verb, args):
            calls.append((verb, args))
            if verb == 'find-precedent':
                return {'ok': True, 'count': 25, 'rulings': []}
            if verb == 'read-doc-activity':
                return {'ok': True, 'entries': [{'id': 'later' if args.get('cursor') else 'first'}],
                        'next_cursor': None if args.get('cursor') else {'at': '2026-10-01T12:00:00Z', 'id': 'cursor'}}
            return {'ok': True}
        result = sweep.collect_records('2026-10-01', '2026-10-02', call=read)
        self.assertEqual([row['id'] for row in result['records']['rule_activity']], ['first', 'later'])
        self.assertEqual(len(result['gaps']), 6)
        self.assertEqual({name for name, _ in calls}, sweep.READ_VERBS)

    def test_unknown_or_failed_read_cannot_run_write(self):
        with self.assertRaises(ValueError):
            sweep.read_verb('teach', {})
        def fail(_verb, _args):
            raise RuntimeError('unavailable')
        result = sweep.collect_records('2026-10-01', '2026-10-02', call=fail)
        self.assertTrue(result['gaps'])

    def test_machine_user_rows_are_excluded(self):
        texts = ['<hook_prompt>wrong</hook_prompt>', 'The following is the Codex agent history whose request action you are assessing. wrong',
                 'Another Claude session sent a message: wrong']
        rows = [{'type': 'user', 'timestamp': '2026-10-01T12:00:00Z', 'message': {'content': text}} for text in texts]
        self.assertEqual(self.scan('claude', rows)['turns'], [])

    def test_malformed_row_is_a_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.jsonl'
            path.write_text('{bad\n')
            result = sweep.scan_transcripts({'claude': [path]}, '2026-10-01', '2026-10-02')
            self.assertEqual(len(result['gaps']), 1)

    def test_secret_redaction_and_private_permissions(self):
        rows = [{'type': 'user', 'timestamp': '2026-10-01T12:00:00Z', 'message': {'content': 'Wrong API_KEY=synthetic-test-value'}}]
        result = self.scan('claude', rows)
        self.assertNotIn('synthetic-test-value', result['turns'][0]['text'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            sweep.write_private(path, result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                sweep.write_private(path, result)

    def test_prose_credentials_are_redacted_at_collection_and_storage(self):
        text = 'Wrong route. Make the password synthetic-portal-value\nThe passphrase is "synthetic phrase"\nAdd password protection.'
        rows = [{'type': 'user', 'timestamp': '2026-10-01T12:00:00Z',
                 'message': {'content': text}}]
        result = self.scan('claude', rows)
        self.assertNotIn('synthetic-portal-value', result['turns'][0]['text'])
        self.assertNotIn('synthetic phrase', result['turns'][0]['text'])
        self.assertIn('Add password protection.', result['turns'][0]['text'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'records.json'
            sweep.write_private(path, {'records': [{'human_said': text}]})
            saved = json.loads(path.read_text())
            self.assertNotIn('synthetic-portal-value', saved['records'][0]['human_said'])
            self.assertNotIn('synthetic phrase', saved['records'][0]['human_said'])

    def test_prose_redaction_preserves_policy_and_trailing_correction(self):
        text = 'Make the new password synthetic-value. You ignored the scope.\nChange the password policy to require MFA. Keep the approved scope.'
        rows = [{'type': 'user', 'timestamp': '2026-10-01T12:00:00Z',
                 'message': {'content': text}}]
        result = self.scan('claude', rows)
        saved = result['turns'][0]['text']
        self.assertNotIn('synthetic-value', saved)
        self.assertIn('You ignored the scope.', saved)
        self.assertIn('Change the password policy to require MFA. Keep the approved scope.', saved)

    def test_complete_multiword_passphrases_and_sentence_boundaries(self):
        for value in ('cobalt otter meadow lantern', '"cobalt otter meadow lantern"', "'cobalt otter meadow lantern'", '`cobalt otter meadow lantern`'):
            text = 'Set the passphrase to ' + value + '. Keep the correction.'
            redacted = sweep.redact_evidence({'text': text})['text']
            for word in ('cobalt', 'otter', 'meadow', 'lantern'):
                self.assertNotIn(word, redacted)
            self.assertIn('Keep the correction.', redacted)

    def test_internal_credential_punctuation_is_not_a_sentence_boundary(self):
        for value in ('cobalt.otter meadow.lantern', 'cobalt!otter meadow?lantern',
                      'cobalt;otter meadow.lantern'):
            for boundary in ('. ', '! ', '? ', '; ', '\n'):
                redacted = sweep.redact_evidence({
                    'text': 'Set the passphrase to ' + value + boundary + 'Keep the correction.'
                })['text']
                for word in ('cobalt', 'otter', 'meadow', 'lantern'):
                    self.assertNotIn(word, redacted)
                self.assertIn('Keep the correction.', redacted)


if __name__ == '__main__':
    unittest.main()
