#!/usr/bin/env python3
"""Receipt provenance and author independence at the consumer interface."""
from contextlib import redirect_stderr, closing
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import dot_review_receipts as receipts


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name) / 'relay'
        self.store.mkdir()
        self.env = patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(self.store)})
        self.env.start()
        self.addCleanup(self.env.stop)
        receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
        self.meta = {'repo': 'fixture/repo', 'pr': 1, 'sha': 'a' * 40}
        self.report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']+'\nNo blockers.\nDOT-REPORT-END'
        self.body = receipts.publication_body(self.meta, self.report)
        self.pr = {'user': {'login': 'builder'}}
        self.commits = [{'author': {'login': 'other-builder'},
                         'commit': {'author': {'name': 'Other Builder', 'email': 'other@example.test'}}}]
        self.calls = []
        self.messages = {}
        self.slack = type('Slack', (), {'channel': 'fixture-channel'})()
        self.slack.replies = lambda thread: list(self.messages.values())
        self.reader_factory = receipts._slack_transport
        reader = patch.object(receipts, '_slack_transport', return_value=self.slack)
        reader.start()
        self.addCleanup(reader.stop)
        self.policy = {
            'verdict': lambda body, config: body.startswith('APPROVE\n'),
            'reviewed_header_sha': lambda body: body.splitlines()[1].split(': ', 1)[1],
            'trusted_commenter': lambda comment, config: comment.get('author_association') == 'OWNER',
        }

    def api(self, path):
        self.calls.append(path)
        if path == 'repos/fixture/repo/pulls/1':
            return self.pr
        if path == 'repos/fixture/repo/pulls/1/commits?per_page=100':
            return self.commits
        raise AssertionError(path)

    def record(self, body=None, meta=None, run='fixture-channel:1.000001', reviewer='dot-user', anchor=False):
        return receipts.record(meta or self.meta, body or self.body, builder='invented-builder',
                               reviewer=reviewer, relay_run_id=run, branch_author='invented-author', anchor=anchor)

    def pair(self, body=None, meta=None, run='fixture-channel:1.000001', reviewer='dot-user'):
        ts = run.rsplit(':', 1)[-1]
        report = (body or self.body).rsplit('\n\nReviewer: ChatGPT Dot\n<!-- dot-review:', 1)[0]+'\nDOT-REPORT-END'
        self.messages[ts] = {'ts': ts, 'user': reviewer, 'text': report}
        receipts.attest_run(meta or self.meta, body or self.body, reviewer=reviewer, relay_run_id=run, report=report)
        return receipts.record(meta or self.meta, body or self.body, reviewer=reviewer,
                               relay_run_id=run, report=report, anchor=True)

    def matching(self, body=None, meta=None):
        return receipts.matching(meta or self.meta, body or self.body, api=self.api)

    def assert_alarm_refusal(self, action):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertIsNone(action())
            self.assertEqual(receipts.deciding([{'id': 1, 'body': self.body, 'author_association': 'OWNER'}],
                'fixture/repo', 1, policy=self.policy, config={}, api=self.api), (None, None))
        self.assertIn('DOT_REVIEW_RECEIPT_ALARM', stderr.getvalue())

    def test_public_api_pair_cannot_hide_contradictory_multipart_report(self):
        report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']+'\nFirst part\nREVIEW: BLOCKED\nP1 blocker\nDOT-REPORT-END'
        self.append_report(report)
        self.assert_alarm_refusal(self.matching)

    def test_public_api_pair_cannot_change_findings_or_test_counts(self):
        report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']+'\nOnly 2 tests passed.\nDOT-REPORT-END'
        self.append_report(report)
        self.assert_alarm_refusal(self.matching)

    def test_public_api_pair_cannot_authorize_unfinished_report(self):
        report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']
        self.append_report(report)
        self.assert_alarm_refusal(self.matching)

    def test_public_api_pair_cannot_authorize_duplicate_sha(self):
        report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']+'\nReviewed-SHA: '+self.meta['sha']+'\nDOT-REPORT-END'
        self.append_report(report)
        self.assert_alarm_refusal(self.matching)

    def append_report(self, report):
        parts = report.split('\nFirst part\n', 1)
        self.messages['1.000001'] = {'ts': '1.000001', 'user': 'dot-user', 'text': parts[0]}
        if len(parts) == 2:
            self.messages['2.000001'] = {'ts': '2.000001', 'user': 'dot-user', 'text': 'First part\n'+parts[1]}
        receipts.attest_run(self.meta, self.body, reviewer='dot-user',
                            relay_run_id='fixture-channel:1.000001', report=report)
        receipts.record(self.meta, self.body, reviewer='dot-user',
                        relay_run_id='fixture-channel:1.000001', report=report, anchor=True)

    def test_run_and_receipt_must_share_report_digest_message_and_thread(self):
        self.pair()
        ledgers = receipts._read_ledgers(self.store)
        for field, value in [('report_sha256', 'b'*64), ('slack_message_ts', '2.000001'),
                             ('slack_thread_ts', '3.000001')]:
            altered = {**ledgers, 'runs': [{**ledgers['runs'][0], field: value}]}
            with self.subTest(field=field), patch.object(receipts, '_read_ledgers', return_value=altered), patch.object(
                    receipts, '_verify_heads', return_value={'sender': 'dot-user', 'channel': 'fixture-channel',
                                                            'github_actor': 'dot-github-user'}):
                self.assert_alarm_refusal(self.matching)

    def test_canonical_redaction_is_derived_from_slack_with_relay_token(self):
        self.slack.token = 'synthetic-relay-token'
        report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']+'\nToken synthetic-relay-token\nDOT-REPORT-END'
        self.body = receipts.publication_body(self.meta, report, (self.slack.token,))
        self.append_report(report)
        self.assertIsNotNone(self.matching())
        self.assertIn('[REDACTED]', self.body)

    def test_public_attestation_with_invented_slack_timestamp_refuses_and_alarms(self):
        self.pair()
        self.messages.clear()
        self.assert_alarm_refusal(self.matching)

    def test_slack_report_text_mismatch_refuses_and_alarms(self):
        self.pair()
        self.messages['1.000001']['text'] += '\nDifferent Slack report'
        self.assert_alarm_refusal(self.matching)

    def test_slack_unreachable_refuses_and_alarms(self):
        self.pair()
        self.slack.replies = lambda thread: (_ for _ in ()).throw(ConnectionError('offline'))
        self.assert_alarm_refusal(self.matching)

    def test_genuine_slack_report_accepts(self):
        original = self.pair()
        self.assertEqual(self.matching(), original)

    def test_wrong_slack_sender_refuses_and_alarms(self):
        self.pair()
        self.messages['1.000001']['user'] = 'outsider'
        self.assert_alarm_refusal(self.matching)

    def test_wrong_slack_channel_refuses_and_alarms(self):
        self.pair()
        self.slack.channel = 'other-channel'
        self.assert_alarm_refusal(self.matching)

    def test_real_block_report_cannot_attest_approve(self):
        report = self.report.replace('APPROVE', 'REVIEW: BLOCKED', 1)
        receipts.attest_run(self.meta, self.body, reviewer='dot-user',
                            relay_run_id='fixture-channel:1.000001', report=report)
        receipts.record(self.meta, self.body, reviewer='dot-user',
                        relay_run_id='fixture-channel:1.000001', report=report, anchor=True)
        self.messages['1.000001'] = {'ts': '1.000001', 'user': 'dot-user', 'text': report}
        self.assert_alarm_refusal(self.matching)

    def test_small_clock_skew_can_be_recorded_but_not_accepted_in_future(self):
        with patch.object(receipts.time, 'time', return_value=100):
            self.pair(run='fixture-channel:101.000001')
            self.assert_alarm_refusal(self.matching)
        with patch.object(receipts.time, 'time', return_value=102):
            self.assertIsNotNone(self.matching())

    def test_multipart_report_verifies_in_its_original_thread(self):
        report = 'APPROVE\nReviewed-SHA: '+self.meta['sha']+'\nFirst part\nSecond part\nDOT-REPORT-END'
        self.body = receipts.publication_body(self.meta, report)
        receipts.attest_run(self.meta, self.body, reviewer='dot-user',
                            relay_run_id='fixture-channel:2.000001', report=report, thread_ts='1.000001')
        original = receipts.record(self.meta, self.body, reviewer='dot-user',
                                   relay_run_id='fixture-channel:2.000001', report=report,
                                   thread_ts='1.000001', anchor=True)
        self.slack.replies = lambda thread: [
            {'ts': '1.000001', 'user': 'orchestrator', 'text': 'Review brief'},
            {'ts': '2.000001', 'user': 'dot-user', 'text': report.split('\nSecond part')[0]},
            {'ts': '3.000001', 'user': 'outsider', 'text': 'Untrusted interruption'},
            {'ts': '4.000001', 'user': 'dot-user', 'text': 'Second part\nDOT-REPORT-END'},
            {'ts': '5.000001', 'user': 'dot-user', 'text': 'After completion'}] if thread == '1.000001' else []
        self.assertEqual(self.matching(), original)

    def test_edited_slack_message_refuses_and_alarms(self):
        self.pair()
        self.messages['1.000001']['edited'] = {'ts': '2.000001'}
        self.assert_alarm_refusal(self.matching)

    def test_existing_relay_reader_uses_configured_channel_and_thread(self):
        self.pair()
        calls = []
        def api(method, payload):
            calls.append((method, payload))
            return {'ok': True, 'messages': list(self.messages.values()), 'has_more': False}
        transport = receipts.dot_relay.SlackTransport
        config = {'token': 'synthetic-test-token', 'channel': 'fixture-channel', 'sender': 'dot-user'}
        with patch.object(receipts, '_slack_transport', side_effect=self.reader_factory), patch.object(
                receipts.dot_relay, 'read_config', return_value=config), patch.object(
                receipts.dot_relay, 'SlackTransport', side_effect=lambda token, channel:
                    transport(token, channel, api=api, use_sdk=False)):
            self.assertIsNotNone(self.matching())
        self.assertEqual(calls, [('conversations.replies',
            {'channel': 'fixture-channel', 'ts': '1.000001', 'limit': 15})])

    def test_future_timestamp_is_refused_at_record_time(self):
        import time
        run = 'fixture-channel:' + str(int(time.time()) + 60) + '.000001'
        for action in (lambda: self.record(run=run),
                       lambda: receipts.attest_run(self.meta, self.body, reviewer='dot-user',
                                                  relay_run_id=run, report=self.body)):
            with self.subTest(action=action), self.assertRaises(ValueError):
                action()

    def test_appended_run_and_receipt_pair_alarm_and_refuse(self):
        self.pair()
        forged = self.body.replace('No blockers.', 'No blockers.' + '\nForged appended verdict')
        receipts.record_run(self.meta, forged, reviewer='dot-user', relay_run_id='fixture-channel:2.000001')
        self.record(body=forged, run='fixture-channel:2.000001')
        self.assert_alarm_refusal(lambda: self.matching(body=forged))

    def test_rewritten_tail_pair_alarm_and_refuse(self):
        self.pair()
        self.pair(body=self.body.replace('No blockers.', 'No blockers.' + '\nOriginal tail'), run='fixture-channel:2.000001')
        forged = self.body.replace('No blockers.', 'No blockers.' + '\nRewritten tail')
        for ledger in ('runs', 'receipts'):
            path = sorted((self.store / ledger).glob('*.json'))[-1]
            value = json.loads(path.read_text())
            value['body_sha256'] = hashlib.sha256(forged.encode()).hexdigest()
            value['sha256'] = receipts._digest({k: v for k, v in value.items() if k != 'sha256'})
            path.write_text(json.dumps(value))
        self.assert_alarm_refusal(lambda: self.matching(body=forged))

    def test_truncated_tail_pair_alarm_and_refuse_even_for_older_verdict(self):
        self.pair()
        self.pair(body=self.body.replace('No blockers.', 'No blockers.' + '\nTail'), run='fixture-channel:2.000001')
        for ledger in ('runs', 'receipts'):
            sorted((self.store / ledger).glob('*.json'))[-1].unlink()
        self.assert_alarm_refusal(self.matching)

    def test_wrong_configured_sender_and_channel_alarm_before_attestation(self):
        for reviewer, run in [('invented-reviewer', 'fixture-channel:1.000001'),
                              ('dot-user', 'wrong-channel:1.000001')]:
            with self.subTest(reviewer=reviewer, run=run):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(ValueError):
                    receipts.attest_run(self.meta, self.body, reviewer=reviewer,
                                        relay_run_id=run, report=self.body)
                self.assertIn('DOT_REVIEW_RECEIPT_ALARM', stderr.getvalue())
                self.assertIsNone(self.matching())

    def test_anchors_are_outside_store_and_cannot_be_reenrolled(self):
        self.pair()
        self.assertFalse(receipts.anchor_database().is_relative_to(self.store))
        with self.assertRaisesRegex(ValueError, 'differs'):
            receipts.configure_actor('other-sender', 'fixture-channel', 'dot-github-user')
        with patch.dict('os.environ', {'CARR_DOT_REVIEW_ANCHORS': str(self.store / 'anchor.sqlite3')}):
            self.assert_alarm_refusal(self.matching)

    def test_actor_mapping_does_not_compare_slack_id_to_github_login(self):
        self.pair()
        self.pr['user']['login'] = 'dot-user'
        self.assertIsNotNone(self.matching())
        self.pr['user']['login'] = 'dot-github-user'
        self.assert_alarm_refusal(self.matching)

    def test_anchor_rollback_is_detected_even_with_truncated_ledgers(self):
        import sqlite3
        first = self.pair()
        self.pair(body=self.body.replace('No blockers.', 'No blockers.' + '\nTail'), run='fixture-channel:2.000001')
        for ledger in ('runs', 'receipts'):
            sorted((self.store / ledger).glob('*.json'))[-1].unlink()
            head = json.loads(next((self.store / ledger).glob('*.json')).read_text())
            with closing(sqlite3.connect(receipts.anchor_database())) as db:
                db.execute('UPDATE heads SET sequence=?,sha256=? WHERE ledger=?',
                           (first['sequence'], head['sha256'], ledger))
                db.commit()
        self.assert_alarm_refusal(self.matching)

    def test_archive_v1_has_operator_receipt_and_restores_new_reviews(self):
        original = self.pair()
        legacy = self.store / ('f' * 64)
        legacy.mkdir()
        content = json.dumps({'schema': 'carr-dot-review-receipt/v1'})
        (legacy / 'old.json').write_text(content)
        self.assert_alarm_refusal(self.matching)
        archive = receipts.archive_legacy(operator='fixture-operator')
        destination = Path(archive['destination'])
        self.assertFalse(legacy.exists())
        self.assertEqual((destination / legacy.name / 'old.json').read_text(), content)
        self.assertEqual(json.loads((destination / 'archive-receipt.json').read_text()), archive)
        self.assertEqual(archive['folders'][legacy.name]['old.json'], hashlib.sha256(content.encode()).hexdigest())
        self.assertIsNone(receipts.archive_legacy(operator='fixture-operator'))
        self.assertEqual(self.matching(), original)

    def test_archive_cli_returns_operator_receipt_without_live_store_access(self):
        import subprocess
        legacy = self.store / ('e' * 64)
        legacy.mkdir()
        (legacy / 'v1.json').write_text(json.dumps({'schema': 'carr-dot-review-receipt/v1'}))
        configured = subprocess.run([sys.executable, str(ROOT / 'bin/dot-review.py'), 'configure-actor',
            '--sender', 'dot-user', '--channel', 'fixture-channel', '--github-actor', 'dot-github-user'],
            text=True, capture_output=True, check=True, timeout=10)
        self.assertTrue(json.loads(configured.stdout)['configured'])
        result = subprocess.run([sys.executable, str(ROOT / 'bin/dot-review.py'), 'archive-v1',
            '--operator', 'fixture-cli'], text=True, capture_output=True, check=True, timeout=10)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['operator'], 'fixture-cli')
        self.assertTrue((Path(receipt['destination']) / 'archive-receipt.json').exists())
        self.assertFalse(legacy.exists())
        self.pair()
        self.assertIsNotNone(self.matching())

    def test_archive_unknown_storage_refuses_without_moving(self):
        unknown = self.store / 'unrecognized'
        unknown.mkdir()
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            receipts.archive_legacy(operator='fixture-operator')
        self.assertTrue(unknown.exists())

    def test_record_without_run_is_refused(self):
        self.record()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertIsNone(receipts.matching(self.meta, self.body))
        self.assertIn('external anchor', stderr.getvalue())
        self.assert_alarm_refusal(self.matching)
        self.assertEqual(self.calls, [])

    def test_genuine_run_and_receipt_are_accepted_using_live_authors(self):
        receipt = self.pair()
        self.assertEqual(self.matching(), receipt)
        self.assertEqual(self.calls, ['repos/fixture/repo/pulls/1',
                                     'repos/fixture/repo/pulls/1/commits?per_page=100'])

    def test_retry_is_idempotent_and_conflicting_reuse_is_refused(self):
        receipt = self.pair()
        self.assertEqual(self.pair(), receipt)
        self.assertEqual(len(list(self.store.rglob('*.json'))), 2)
        with self.assertRaisesRegex(ValueError, 'append-only'):
            self.record(reviewer='different-reviewer')
        with self.assertRaisesRegex(ValueError, 'append-only'):
            receipts.record_run(self.meta, self.body, reviewer='different-reviewer',
                                relay_run_id='fixture-channel:1.000001')

    def test_append_preserves_prior_entries_and_links_each_chain(self):
        first = self.pair()
        paths = sorted(self.store.rglob('*.json'))
        original = {p: p.read_bytes() for p in paths}
        second = self.pair(body=self.body.replace('No blockers.', 'No blockers.' + '\nSecond report'), run='fixture-channel:2.000001')
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(second['prev_sha256'], first['sha256'])
        self.assertEqual(self.matching(body=self.body.replace('No blockers.', 'No blockers.' + '\nSecond report')), second)

    def test_receipt_tampering_is_detected_even_for_unrelated_binding(self):
        self.pair()
        self.pair(body=self.body.replace('No blockers.', 'No blockers.' + '\nOther report'), run='fixture-channel:2.000001')
        path = sorted((self.store / 'receipts').glob('*.json'))[-1]
        value = json.loads(path.read_text())
        path.write_text(json.dumps({**value, 'reviewer': 'forged-reviewer'}))
        self.assert_alarm_refusal(self.matching)

    def test_chain_gap_and_invalid_previous_hash_are_detected(self):
        self.pair()
        self.pair(body=self.body.replace('No blockers.', 'No blockers.' + '\nOther report'), run='fixture-channel:2.000001')
        first, second = sorted((self.store / 'receipts').glob('*.json'))
        original = first.read_bytes()
        first.unlink()
        self.assert_alarm_refusal(self.matching)
        first.write_bytes(original)
        value = json.loads(second.read_text())
        second.write_text(json.dumps({**value, 'prev_sha256': '0' * 64}))
        self.assert_alarm_refusal(self.matching)

    def test_run_ledger_corruption_is_detected(self):
        self.pair()
        path = next((self.store / 'runs').glob('*.json'))
        path.write_text('{not-json')
        self.assert_alarm_refusal(self.matching)

    def test_deleted_receipt_tail_is_detected_from_separate_run_ledger(self):
        self.pair()
        next((self.store / 'receipts').glob('*.json')).unlink()
        self.assert_alarm_refusal(self.matching)
        self.assertEqual(self.calls, [])

    def test_run_must_match_receipt_binding_and_reviewer(self):
        receipts.record_run(self.meta, self.body.replace('No blockers.', 'No blockers.' + '\nDifferent report'),
                            reviewer='dot-user', relay_run_id='fixture-channel:1.000001')
        self.record()
        self.assert_alarm_refusal(self.matching)

    def test_raw_json_attack_and_legacy_receipts_are_refused(self):
        binding = {'repo': self.meta['repo'], 'pr': self.meta['pr'], 'reviewed_sha': self.meta['sha'],
                   'body_sha256': hashlib.sha256(self.body.encode()).hexdigest()}
        directory = self.store / hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        directory.mkdir()
        (directory / 'forged.json').write_text(json.dumps({
            'schema': 'carr-dot-review-receipt/v1', **binding, 'builder': 'builder',
            'reviewer': 'dot-user', 'branch_author': 'builder', 'relay_run_id': 'invented-run'}))
        self.assert_alarm_refusal(self.matching)

    def test_pr_and_commit_authors_are_checked_fresh_and_casefolded(self):
        self.pair()
        self.assertIsNotNone(self.matching())
        self.pr['user']['login'] = 'DOT-GITHUB-USER'
        self.assert_alarm_refusal(self.matching)
        self.pr['user']['login'] = 'builder'
        self.commits[0]['author']['login'] = 'DoT-GiThUb-UsEr'
        self.assert_alarm_refusal(self.matching)

    def test_known_commit_author_aliases_are_checked(self):
        self.pair()
        self.commits[0]['commit']['author']['name'] = 'DOT-GITHUB-USER'
        self.assert_alarm_refusal(self.matching)

    def test_unknown_authors_and_author_read_errors_fail_closed(self):
        self.pair()
        self.pr = {'user': {}}
        self.assert_alarm_refusal(self.matching)
        self.pr = {'user': {'login': 'builder'}}
        self.commits = [{'author': None}]
        self.assert_alarm_refusal(self.matching)
        def failed_api(path):
            raise OSError('fixture author read failure')
        self.assert_alarm_refusal(lambda: receipts.matching(self.meta, self.body, api=failed_api))
        self.assert_alarm_refusal(lambda: receipts.matching(self.meta, self.body))

    def test_receipt_binding_rejects_different_sha_body_repo_or_pr(self):
        self.pair()
        for meta in ({**self.meta, 'sha': 'b' * 40}, {**self.meta, 'repo': 'fixture/other'},
                     {**self.meta, 'pr': 2}):
            with self.subTest(meta=meta):
                self.assertIsNone(self.matching(meta=meta))
        self.assertIsNone(self.matching(body=self.body.replace('No blockers.', 'No blockers.' + '\nAdded body')))
        self.assertEqual(self.calls, [])

    def test_claimed_dot_comment_without_receipt_is_not_owner_approval(self):
        comment = {'id': 1, 'body': self.body, 'author_association': 'OWNER'}
        self.assertEqual(receipts.deciding([comment], 'fixture/repo', 1, policy=self.policy,
                                          config={}, api=self.api), (None, None))
        self.assertEqual(self.calls, [])

    def test_ordinary_trusted_comment_preserves_coverage_without_author_reads(self):
        comment = {'id': 1, 'body': 'APPROVE\nReviewed-SHA: ' + self.meta['sha'],
                   'author_association': 'OWNER'}
        self.assertEqual(receipts.deciding([comment], 'fixture/repo', 1, policy=self.policy,
                                          config={}, api=self.api), (comment, None))
        self.assertEqual(self.calls, [])

    def test_fenced_marker_and_stored_receipt_still_carry_review(self):
        body = receipts.publication_body(self.meta, 'APPROVE\nReviewed-SHA: ' + self.meta['sha'] + '\n```\nReviewer: ChatGPT Dot\n```\nDOT-REPORT-END')
        receipt = self.pair(body=body)
        comment = {'id': 1, 'body': body, 'author_association': 'NONE'}
        self.assertEqual(receipts.deciding([comment], 'fixture/repo', 1, policy=self.policy,
                                          config={}, api=self.api), (comment, receipt))


if __name__ == '__main__':
    unittest.main()
