#!/usr/bin/env python3
"""Exercise delivery observation with synthetic transport and persisted receipts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import social_delivery as delivery

RECORDED = json.loads((Path(__file__).parent / 'fixtures/social-batch/blotato.json').read_text())
NOW = 1793700000


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'out/social-batch'
        self.calls = []
        self.responses = {}
        self.clock = float(NOW)
        self.post('old', '2026-10-26', 'first')

    def post(self, submission, week, draft, utc='2026-11-02T16:23:00Z'):
        folder = self.out / week
        folder.mkdir(parents=True, exist_ok=True)
        row = dict(event='posted', draft=draft, platform='linkedin', utc=utc, submission_id=submission)
        with (folder / 'ledger.jsonl').open('a') as handle:
            handle.write(json.dumps(row) + '\n')

    def send(self, method, url, headers, data):
        self.assertIn(method, ('GET', 'HEAD'))
        self.assertIsNone(data)
        self.calls.append((method, url, self.clock))
        value = self.responses.get(url.rsplit('/', 1)[-1], {'status': 'in-progress'})
        return value if isinstance(value, tuple) else (200, json.dumps(value).encode())

    def run_check(self, **kwargs):
        def sleep(seconds):
            self.clock += seconds
        return delivery.check(self.out, self.send, accounts=RECORDED['accounts'],
                              now=lambda: self.clock, sleep=sleep, **kwargs)

    def test_due_failure_precedes_10_and_45_unreachable_links(self):
        import job_watchdog as watchdog
        for count in (10, 45):
            with self.subTest(count=count):
                self.out = Path(self.temp.name) / f'case{count}/out/social-batch'
                self.clock = float(NOW)
                self.post('old', '2026-10-26', 'first')
                self.responses['old'] = {'status': 'scheduled', 'scheduledTime': '2026-11-03T16:23:00Z'}
                self.run_check()
                source, _ = delivery.discover(self.out, RECORDED['accounts'])
                prior = json.loads((self.out / delivery.RECEIPT).read_text())
                for index in range(count):
                    name = f'blocked{count}_{index}'
                    self.post(name, '2026-11-02', name)
                    prior['posts']['2026-11-02/' + name] = {
                        **source['2026-10-26/first'], 'submission_id': name,
                        'status': 'published', 'observed_at': '2026-11-01T00:00:00Z',
                        'visibility': 'unverified', 'publicUrl': 'https://x.com/blocked'}
                (self.out / delivery.RECEIPT).write_text(json.dumps(prior))
                self.responses['blocked'] = (302, b'')
                self.responses['old'] = {'status': 'failed', 'errorMessage': 'Account expired'}
                self.clock += 86400
                self.calls.clear()
                result = self.run_check()
                self.assertEqual(result['posts']['2026-10-26/first']['status'], 'failed')
                self.assertTrue(self.calls[0][1].endswith('/old'))
                found = watchdog.social_delivery_findings(self.out.parents[1], self.clock)
                self.assertTrue(any('Account expired' in row['reason'] for row in found))
                for path in self.out.glob('*/ledger.jsonl'):
                    if path.parent.name != '2026-10-26':
                        path.unlink()

    def test_visibility_attempts_end_with_deduplicated_cannot_verify(self):
        import job_watchdog as watchdog
        from unittest.mock import patch
        self.responses['old'] = {'status': 'published', 'publicUrl': 'https://x.com/blocked'}
        self.responses['blocked'] = (302, b'')
        self.run_check()
        self.clock += 86400
        result = self.run_check()
        self.assertEqual(result['posts']['2026-10-26/first']['visibility'], 'cannot_verify')
        self.assertEqual(result['posts']['2026-10-26/first']['visibility_attempts'], 2)
        count = len(self.calls)
        root = self.out.parents[1]
        config = watchdog.load_config(delivery.ROOT / 'ops/config/job-watchdog.json')
        with patch.object(watchdog.time, 'time', side_effect=lambda: self.clock):
            watchdog.deliver_findings(root, config, self.clock)
            inbox = root / config['paths']['orchestrator_inbox']
            first = inbox.read_bytes()
            self.assertIn('cannot verify public link', first.decode())
            self.clock += 86400
            self.run_check()
            watchdog.deliver_findings(root, config, self.clock)
            self.assertEqual(inbox.read_bytes(), first)
        self.assertEqual(len(self.calls), count)

    def test_404_is_quiet_terminal_cancellation(self):
        import job_watchdog as watchdog
        self.responses['old'] = (404, b'{}')
        result = self.run_check()
        self.assertEqual(result['posts']['2026-10-26/first']['status'], 'cancelled')
        self.clock += 86400
        self.run_check()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(watchdog.social_delivery_findings(self.out.parents[1], self.clock), [])

    def test_rate_limit_defers_trigger_and_rotates_before_links(self):
        import job_watchdog as watchdog
        self.post('second', '2026-11-02', 'second')
        self.responses['old'] = (429, b'{}')
        result = self.run_check()
        self.assertIn('2026-10-26/first', result['deferred'])
        self.assertFalse(any('unavailable or malformed' in row['reason']
                             for row in watchdog.social_delivery_findings(self.out.parents[1], self.clock)))
        self.clock += 86400
        self.responses['second'] = {'status': 'failed', 'errorMessage': 'Account expired'}
        self.calls.clear()
        result = self.run_check()
        self.assertTrue(self.calls[0][1].endswith('/second'))
        self.assertEqual(result['posts']['2026-11-02/second']['status'], 'failed')

    def test_32_historical_posts_complete_in_two_daily_runs(self):
        for index in range(31):
            self.post(str(index), '2026-11-02', str(index))
        for name in ['old'] + [str(index) for index in range(31)]:
            self.responses[name] = {'status': 'published', 'publicUrl': 'https://x.com/reachable'}
        self.responses['reachable'] = (200, b'')
        first = self.run_check()
        self.assertEqual(first['requests'], 40)
        self.assertEqual(sum(p['status'] == 'published' for p in first['posts'].values()), 32)
        self.assertEqual(len(first['deferred']), 24)
        self.clock += 86400
        second = self.run_check()
        self.assertEqual(second['requests'], 24)
        self.assertEqual(second['deferred'], [])
        self.assertTrue(all(p['visibility'] == 'verified' for p in second['posts'].values()))

    def test_published_and_restart_preserve_evidence(self):
        self.responses['old'] = {**RECORDED['post_status'], 'status': 'published', 'publicUrl': 'https://www.linkedin.com/feed/update/synthetic'}
        self.responses['synthetic'] = (200, b'')
        result = self.run_check()
        post = next(iter(result['posts'].values()))
        self.assertEqual((post['status'], post['visibility']), ('published', 'verified'))
        self.assertEqual(post['account'], RECORDED['accounts']['linkedin'])
        self.assertEqual(post['original_fire_time'], '2026-11-02T16:23:00Z')
        self.assertIn('observed_at', post)
        calls = len(self.calls)
        self.assertEqual(self.run_check()['posts'], result['posts'])
        self.assertEqual(len(self.calls), calls)

    def test_failed_pending_malformed_404_and_cancelled(self):
        for response, expected in [({'status': 'failed', 'errorMessage': 'account expired'}, 'failed'),
                                   ({'status': 'scheduled'}, 'unresolved'),
                                   ({'status': 'in-progress'}, 'unresolved'),
                                   ((200, b'[]'), 'unresolved'), ((404, b'{}'), 'cancelled'),
                                   ({'status': 'cancelled'}, 'cancelled')]:
            with self.subTest(response=response):
                (self.out / delivery.RECEIPT).unlink(missing_ok=True)
                self.responses['old'] = response
                post = next(iter(self.run_check()['posts'].values()))
                self.assertEqual(post['status'], expected)
                if expected == 'failed':
                    self.assertEqual(post['errorMessage'], 'account expired')

    def test_not_due_moved_and_due_again(self):
        self.responses['old'] = {'status': 'scheduled', 'scheduledTime': '2026-11-09T16:23:00Z'}
        result = self.run_check()
        self.assertEqual(next(iter(result['posts'].values()))['status'], 'moved')
        count = len(self.calls)
        self.run_check()
        self.assertEqual(len(self.calls), count)
        self.clock += 8 * 86400
        self.responses['old'] = {'status': 'published'}
        post = next(iter(self.run_check()['posts'].values()))
        self.assertEqual((post['status'], post['visibility']), ('published', 'unverified'))

    def test_cross_week_adopted_and_future(self):
        self.post(None, '2026-11-02', 'adopted')
        self.post('future', '2026-11-09', 'future', '2026-11-09T16:23:00Z')
        result = self.run_check()
        self.assertEqual(len(result['posts']), 3)
        self.assertEqual(result['posts']['2026-11-02/adopted']['status'], 'unresolved')
        self.assertEqual(result['posts']['2026-11-09/future']['status'], 'not_due')
        self.assertEqual(len(self.calls), 1)

    def test_rate_limit_auth_and_budget_defer(self):
        self.post('second', '2026-11-02', 'second')
        self.responses['old'] = (429, b'{}')
        result = self.run_check()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(result['deferred']), 2)
        self.run_check()
        self.assertEqual(len(self.calls), 1)
        self.clock += 61
        self.responses['second'] = (401, b'{}')
        result = self.run_check()
        self.assertEqual(result['stop_reason'], 'authentication')
        self.responses = {}
        result = self.run_check(max_requests=1)
        self.assertEqual(len(result['deferred']), 1)

    def test_visibility_blocked_is_not_failure(self):
        self.responses['old'] = {'status': 'published', 'publicUrl': 'https://www.linkedin.com/blocked'}
        self.responses['blocked'] = (403, b'')
        post = next(iter(self.run_check()['posts'].values()))
        self.assertEqual((post['status'], post['visibility']), ('published', 'unverified'))
        self.responses['blocked'] = (200, b'')
        post = next(iter(self.run_check()['posts'].values()))
        self.assertEqual((post['status'], post['visibility']), ('published', 'verified'))

    def test_missing_or_malformed_logs_leave_unknown_coverage(self):
        (self.out / '2026-10-26/ledger.jsonl').unlink()
        self.assertEqual(self.run_check()['coverage'], 'unknown')
        (self.out / '2026-10-26/ledger.jsonl').write_text('{')
        self.assertEqual(self.run_check()['coverage'], 'unknown')

    def test_request_spacing_and_fair_deferral(self):
        self.post('second', '2026-11-02', 'second')
        self.run_check(max_requests=1)
        self.run_check(max_requests=1)
        self.assertTrue(self.calls[-1][1].endswith('/second'))
        self.run_check()
        self.assertGreaterEqual(self.calls[-1][2] - self.calls[-2][2], 1.1 - 0.00001)

    def test_server_breaker_and_deadline(self):
        for index in range(5):
            self.post(str(index), '2026-11-02', str(index))
            self.responses[str(index)] = (503, b'{}')
        self.responses['old'] = (503, b'{}')
        result = self.run_check()
        self.assertEqual((result['requests'], result['stop_reason']), (3, 'transport_failures'))
        self.assertEqual(len(result['deferred']), 4)
        original = self.send
        def slow(*args):
            self.clock += 60
            return original(*args)
        self.send = slow
        self.responses = {}
        result = self.run_check()
        self.assertEqual(result['requests'], 2)
        self.assertEqual(len(result['deferred']), 4)

    def test_receipt_replace_failure_keeps_previous_evidence(self):
        from unittest.mock import patch
        self.run_check()
        before = (self.out / delivery.RECEIPT).read_bytes()
        with patch.object(delivery.os, 'replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.run_check()
        self.assertEqual((self.out / delivery.RECEIPT).read_bytes(), before)

    def test_visibility_rejects_non_public_or_credential_urls(self):
        for url in ('http://www.linkedin.com/post', 'https://localhost/post',
                    'https://www.linkedin.com@127.0.0.1/', 'https://www.linkedin.com:8443/post'):
            self.assertFalse(delivery.public_url(url))

    def test_cancelled_evidence_and_failed_recovery(self):
        self.responses['old'] = {'status': 'cancelled'}
        self.run_check()
        calls = len(self.calls)
        self.run_check()
        self.assertEqual(len(self.calls), calls)
        self.post('other', '2026-11-02', 'other')
        self.responses['other'] = {'status': 'failed', 'errorMessage': 'expired account'}
        self.assertEqual(self.run_check()['posts']['2026-11-02/other']['status'], 'failed')
        self.responses['other'] = {'status': 'published'}
        self.assertEqual(self.run_check()['posts']['2026-11-02/other']['status'], 'published')


if __name__ == '__main__':
    unittest.main()
