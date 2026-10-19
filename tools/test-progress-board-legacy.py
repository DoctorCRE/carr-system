#!/usr/bin/env python3
"""Legacy disposition through the CLI and published History contract."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import progress_board as board

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/progress-board-legacy.json').read_text())
AT = datetime(2026, 10, 19, 11, 29, 41, tzinfo=timezone.utc)


class LegacyDispositionTests(unittest.TestCase):
    def test_cli_keeps_oauth_card_pending_when_cited_pr_is_doc_plugin(self):
        task = {
            **FIXTURE['tasks']['build-j-doc-plugin'],
            'title': 'Harden the shared OAuth layer: consent, audience binding, PKCE challenge, Origin check',
        }
        info = {
            **FIXTURE['github']['jbookout/carr-system#1454'],
            'title': 'Doc V0: a ChatGPT plugin and Claude connector endpoint over the record layer',
            'files': [{'path': 'mcp-server/src/doc-profile.js'},
                      {'path': 'mcp-server/test/doc-oauth.test.mjs'}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'boards/test.json'
            path.parent.mkdir()
            state = {'project': 'test', 'tasks': {'build-r-oauth-hardening': task}}
            path.write_text(json.dumps(state))
            original = path.read_bytes()
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': directory}), \
                    patch.object(board, 'gh_json', return_value=info), patch.object(board.time, 'sleep'), \
                    patch.object(board, 'now_utc', return_value=AT), contextlib.redirect_stdout(io.StringIO()) as output:
                board.main(['dispose-legacy', 'test', '--apply'])
            report = json.loads(output.getvalue().split('\n', 1)[1])
            self.assertEqual(report['counts']['done'], 0)
            self.assertEqual(report['counts']['kept'], 1)
            self.assertIn('subject', report['samples'][0]['reason'])
            self.assertEqual(path.read_bytes(), original)

    def test_cli_keeps_named_pr_on_read_failure_or_mismatched_identity(self):
        for answer in (RuntimeError('GitHub unavailable'), {'number': 1448, 'state': 'MERGED'}):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'boards/test.json'
                path.parent.mkdir()
                path.write_text(json.dumps({'project': 'test', 'tasks': {
                    'build-card': FIXTURE['tasks']['build-j-doc-plugin']}}))
                original = path.read_bytes()
                github = {'side_effect': answer} if isinstance(answer, Exception) else {'return_value': answer}
                with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': directory}), \
                        patch.object(board, 'gh_json', **github), patch.object(board.time, 'sleep'), \
                        patch.object(board, 'now_utc', return_value=AT), contextlib.redirect_stdout(io.StringIO()):
                    board.main(['dispose-legacy', 'test', '--apply'])
                self.assertEqual(path.read_bytes(), original)

    def test_cli_refuses_apply_when_board_changes_during_github_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'boards/test.json'
            path.parent.mkdir()
            state = {'project': 'test', 'tasks': {'build-card': FIXTURE['tasks']['build-j-doc-plugin']}}
            path.write_text(json.dumps(state))
            changed = {**state, 'notes': [{'text': 'Concurrent operator note'}]}
            def github(args):
                path.write_text(json.dumps(changed))
                return FIXTURE['github']['jbookout/carr-system#1454']
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': directory}), \
                    patch.object(board, 'gh_json', side_effect=github), patch.object(board.time, 'sleep'), \
                    patch.object(board, 'now_utc', return_value=AT), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, 'Board changed'):
                    board.main(['dispose-legacy', 'test', '--apply'])
            self.assertEqual(json.loads(path.read_text()), changed)

    def test_preview_samples_cover_each_disposition_even_after_many_kept_cards(self):
        tasks = {f'ordinary-{i}': FIXTURE['tasks']['events-verify'] for i in range(20)}
        tasks.update(FIXTURE['tasks'])
        facts = {(key.split('#')[0], int(key.split('#')[1])): (value, None)
                 for key, value in FIXTURE['github'].items()}
        _, report = board.dispose_legacy({'project': 'test', 'tasks': tasks}, facts, AT)
        self.assertEqual(len(report['samples']), 10)
        self.assertEqual({row['class'] for row in report['samples']}, {'done', 'dropped', 'legacy, unverified', 'kept'})

    def test_active_new_ambiguous_and_unverifiable_cards_stay_untouched(self):
        base = copy.deepcopy(FIXTURE['tasks']['build-j-doc-plugin'])
        variants = {
            'build-recent-update': {'updated_at': '2026-10-19T10:00:00Z'},
            'build-new': {'created_at': '2026-10-19T10:00:00Z'},
            'build-unknown-date': {'created_at': 'invalid'},
            'build-multiple': {'note': 'PR 1454 and PR 1448'},
            'build-open': {'note': 'PR 7'},
            'build-failed-read': {'note': 'PR 8'},
            'build-bad-sha': {'note': 'PR 9'},
            'build-proof': {'consumer_proof': {'outcome': 'fail'}},
            'build-retired': {'status': 'failed'},
            'ordinary': {'note': 'No PR mentioned'},
            'build-missing-update': {'note': '', 'updated_at': None},
        }
        state = {'project': 'test', 'tasks': {key: {**base, **values} for key, values in variants.items()}}
        facts = {('jbookout/carr-system', 1454): (FIXTURE['github']['jbookout/carr-system#1454'], None),
                 ('jbookout/carr-system', 7): ({'state': 'OPEN'}, None),
                 ('jbookout/carr-system', 8): (None, 'GitHub unavailable'),
                 ('jbookout/carr-system', 9): ({'state': 'MERGED', 'mergeCommit': {'oid': 'invalid'}}, None)}
        result, report = board.dispose_legacy(state, facts, AT)
        self.assertEqual(result, state)
        self.assertEqual(report['counts']['kept'], 11)

    def test_cli_defaults_to_read_only_and_applies_idempotently_without_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'boards/test.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'project': 'test', 'tasks': FIXTURE['tasks']}))
            original = path.read_bytes()
            def github(args):
                return FIXTURE['github'][f'{args[4]}#{args[2]}']
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': directory}), \
                    patch.object(board, 'now_utc', return_value=AT), \
                    patch.object(board, 'gh_json', side_effect=github), \
                    patch.object(board.time, 'sleep') as pause, \
                    patch.object(board, 'publish_board', side_effect=AssertionError('publication')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                board.main(['dispose-legacy', 'test'])
                self.assertIn('DRY RUN', output.getvalue())
                report = json.loads(output.getvalue().split('\n', 1)[1])
                self.assertEqual(report['counts']['legacy, unverified'], 3)
                self.assertEqual(len(report['samples']), 10)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(path.parent.iterdir()), [path])
                board.main(['dispose-legacy', 'test', '--apply'])
                applied = path.read_bytes()
                self.assertEqual(len(json.loads(applied)['legacy_history']), 8)
                board.main(['dispose-legacy', 'test', '--apply'])
                self.assertEqual(path.read_bytes(), applied)
                self.assertTrue(all(call.args == (2,) for call in pause.call_args_list))

    def test_retention_archives_unverified_producers_after_fourteen_days_only(self):
        task = copy.deepcopy(FIXTURE['tasks']['dot-job10'])
        task['updated_at'] = (AT - timedelta(days=14)).isoformat()
        state = {'project': 'test', 'tasks': {'dot-old': task}}
        with patch.object(board, 'now_utc', return_value=AT):
            self.assertEqual(board.retain_recent_cards(state, {}), 0)
        with patch.object(board, 'now_utc', return_value=AT + timedelta(seconds=1)):
            self.assertEqual(board.retain_recent_cards(state, {}), 1)
            self.assertEqual(board.retain_recent_cards(state, {}), 0)
        self.assertEqual(state['legacy_history']['dot-old']['status'], 'archived')
        self.assertIn('legacy, unverified', board.board_snapshot(state, costs={})['history']['dot-old']['reason'])

    def test_ten_evidence_cards_archive_only_stale_retired_producers(self):
        state = {'project': 'test', 'tasks': FIXTURE['tasks']}
        facts = {tuple([key.split('#')[0], int(key.split('#')[1])]): (value, None)
                 for key, value in FIXTURE['github'].items()}
        result, report = board.dispose_legacy(state, facts, AT)
        self.assertEqual(report['counts'], {'done': 3, 'dropped': 2, 'legacy, unverified': 3, 'kept': 2})
        self.assertEqual(len(report['samples']), 10)
        self.assertEqual(set(result['tasks']), {'events-verify', 'jev-apply'})
        snapshot = board.board_snapshot(result, costs={})
        for key in ('dot-job10', 'dot-job7', 'dot-job15'):
            self.assertEqual(snapshot['history'][key]['status'], 'archived')
            self.assertIn('legacy, unverified', snapshot['history'][key]['reason'])
            self.assertEqual(result['legacy_history'][key]['note'], state['tasks'][key]['note'])
        self.assertEqual(board.dispose_legacy(result, facts, AT)[0], result)

    def test_closed_unmerged_is_dropped_with_receipt_and_never_done(self):
        state = {'project': 'test', 'tasks': {'old': FIXTURE['tasks']['build-i-pr1448']}}
        facts = {('jbookout/carr-system', 1448): (FIXTURE['github']['jbookout/carr-system#1448'], None)}
        result, report = board.dispose_legacy(state, facts, AT)
        snapshot = board.board_snapshot(result, costs={})
        self.assertEqual(report['counts']['dropped'], 1)
        self.assertEqual(snapshot['tasks'], {})
        self.assertEqual(snapshot['history']['old']['status'], 'dropped')
        self.assertIn('merge SHA none', snapshot['history']['old']['reason'])
        self.assertIn('legacy pre-proof card verified by GitHub state', snapshot['history']['old']['reason'])

    def test_merged_legacy_note_moves_to_history_without_weakening_new_proof(self):
        task = copy.deepcopy(FIXTURE['tasks']['cloud-jev-codereview'])
        state = {'project': 'test', 'tasks': {'old': task, 'new': {
            'title': 'New completion', 'created_at': '2026-10-19T10:00:00Z',
            'updated_at': '2026-10-19T10:00:00Z', 'status': 'done',
            'note': 'PR 1408'}}}
        facts = {('jbookout/carr-system', 1408): (FIXTURE['github']['jbookout/carr-system#1408'], None)}
        with patch.object(board, 'now_utc', return_value=AT):
            result, report = board.dispose_legacy(state, facts, AT)
            snapshot = board.board_snapshot(result, costs={})
        self.assertEqual(report['counts'], {'done': 1, 'dropped': 0, 'legacy, unverified': 0, 'kept': 1})
        self.assertIn('old', state['tasks'])
        self.assertNotIn('old', snapshot['tasks'])
        self.assertEqual(snapshot['history']['old']['status'], 'done')
        receipt = snapshot['history']['old']['reason']
        self.assertIn('legacy pre-proof card verified by GitHub state', receipt)
        self.assertIn('jbookout/carr-system#1408', receipt)
        self.assertIn(FIXTURE['github']['jbookout/carr-system#1408']['mergeCommit']['oid'], receipt)
        self.assertEqual(snapshot['tasks']['new']['status'], 'review')
        again, _ = board.dispose_legacy(result, facts, AT)
        self.assertEqual(again, result)


if __name__ == '__main__':
    unittest.main()
