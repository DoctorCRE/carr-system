#!/usr/bin/env python3
"""Weekly output verification through the command interface and synthetic HTTP."""
import contextlib
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SocialBatchTests(unittest.TestCase):
    def run_check(self, pages, friday="2026-10-30", authenticated=True):
        spec = importlib.util.spec_from_file_location('social_batch_check', ROOT / 'tools/social-batch-check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        def respond(request, **kwargs):
            calls.append((request.get_method(), request.full_url))
            response = pages[len(calls) - 1]
            if isinstance(response, Exception):
                raise response
            return contextlib.closing(io.BytesIO(json.dumps(response).encode()))
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 30, 16, tzinfo=tz)
        output = io.StringIO()
        with patch.object(module, 'urlopen', side_effect=respond), \
             patch.dict(module.os.environ, {'BLOTATO_API_KEY': 'synthetic-only'} if authenticated else {}, clear=True), \
             patch.object(module, 'datetime', Clock), \
             contextlib.redirect_stdout(output):
            status = module.main(['--friday', friday])
        return status, json.loads(output.getvalue()), calls

    def test_empty_output_fails_instead_of_trusting_scheduler_success(self):
        status, result, calls = self.run_check([{'items': [], 'count': '0'}])
        self.assertEqual(status, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'no_posts_next_week')
        self.assertEqual(result['posts'], [])
        self.assertEqual(calls, [('GET', 'https://backend.blotato.com/v2/schedules?limit=50')])


    @staticmethod
    def post(identifier, when, account='21335'):
        return {'id': identifier, 'scheduledAt': when, 'account': {'id': account}}

    def test_paginated_sunday_output_beyond_seven_days_is_verified(self):
        status, result, calls = self.run_check([
            {'items': [self.post('old', '2026-11-01T12:00:00Z')], 'cursor': 'page+2'},
            {'items': [self.post('sunday', '2026-11-09T05:59:59Z')]},
        ])
        self.assertEqual(status, 0)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['week_start'], '2026-11-02T00:00:00-06:00')
        self.assertEqual(result['week_end_exclusive'], '2026-11-09T00:00:00-06:00')
        self.assertEqual(result['posts'], [{'id': 'sunday', 'account_id': '21335',
                                          'scheduled_at': '2026-11-09T05:59:59+00:00'}])
        self.assertEqual(calls[-1], ('GET', 'https://backend.blotato.com/v2/schedules?limit=50&cursor=page%2B2'))

    def test_other_accounts_disconnected_and_adjacent_weeks_do_not_count(self):
        rows = [self.post('before', '2026-11-02T05:59:59Z'),
                self.post('after', '2026-11-09T06:00:00Z'),
                self.post('dell', '2026-11-04T12:00:00Z', '42191'),
                {'id': 'disconnected', 'account': None}]
        status, result, _ = self.run_check([{'items': rows}])
        self.assertEqual(status, 1)
        self.assertEqual(result['reason'], 'no_posts_next_week')

    def test_each_owned_account_can_supply_output_and_ids_are_deduplicated(self):
        for account in ('21335', '27007', '39262', '56336'):
            with self.subTest(account=account):
                row = self.post('monday', '2026-11-02T06:00:00Z', account)
                status, result, _ = self.run_check([{'items': [row, row]}])
                self.assertEqual(status, 0)
                self.assertEqual(len(result['posts']), 1)
                self.assertEqual(result['posts'][0]['account_id'], account)

    def test_unreadable_or_incomplete_evidence_never_reports_success(self):
        valid = self.post('one', '2026-11-03T12:00:00Z')
        cases = [[OSError('synthetic secret must not leak')], [{}],
                 [{'items': [dict(valid, scheduledAt='no timestamp')]}],
                 [{'items': [dict(valid, scheduledAt='2026-11-03T12:00:00')]}],
                 [{'items': [valid], 'cursor': 'again'}, {'items': [], 'cursor': 'again'}],
                 [{'items': [valid], 'cursor': 'later'}, OSError('later page failed')],
                 [{'items': [valid], 'cursor': 4}], [{'items': [None]}]]
        for pages in cases:
            with self.subTest(pages=pages):
                status, result, _ = self.run_check(pages)
                self.assertEqual(status, 1)
                self.assertEqual(result, {'status': 'failed', 'reason': 'output_unverifiable', 'posts': []})

    def test_missing_auth_invalid_friday_and_started_window_do_not_fetch(self):
        for friday, auth in [('2026-10-30', False), ('2026-10-29', True), ('2026-10-23', True)]:
            status, result, calls = self.run_check([], friday, auth)
            self.assertEqual(status, 1)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(calls, [])

    def test_actual_cli_fails_closed_without_auth(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/social-batch-check.py')],
                                env={}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'output_unverifiable')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
