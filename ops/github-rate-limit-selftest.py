#!/usr/bin/env python3
"""Provider holds, cross-process state and bounded pagination through real reader calls."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from email.utils import formatdate
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.github_reader import GitHubReader, GitHubUnreadable, resolve_gh
from lib.github_rate_limit import GitHubReadBudget, GitHubReadPaused, retry_deadline


class BudgetReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.json'
        self.env = {'GH_LIMITER_DIR': str(Path(self.tmp.name) / 'legacy-budget')}
        self.now = 1000.0
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def budget(self, **kw):
        return GitHubReadBudget(self.env, path=self.path, clock=lambda: self.now, spacing=0, **kw)

    def reader(self, results, **kw):
        def gh(argv, **options):
            self.calls.append(argv)
            rc, out, err = results.pop(0)
            return subprocess.CompletedProcess(argv, rc, out, err)
        return GitHubReader(gh='gh', runner=gh, budget=self.budget(), **kw)

    def test_primary_reset_shared_by_readers_and_no_local_extension(self):
        first = self.reader([(1, 'HTTP/2.0 403\nX-RateLimit-Remaining: 0\nX-RateLimit-Reset: 1350\nX-RateLimit-Resource: core\n\n{}', 'gh: API rate limit exceeded (HTTP 403)')])
        with self.assertRaises(GitHubUnreadable) as error:
            first.api('repos/o/r/pulls/1')
        self.assertEqual((error.exception.kind, error.exception.attempts), ('rate_limit', 1))
        before = self.path.read_bytes()
        self.now = 1100
        second = self.reader([(0, '{"ok":true}', '')])
        with self.assertRaises(GitHubUnreadable) as blocked:
            second.api('repos/o/r/pulls/2')
        self.assertEqual((blocked.exception.kind, blocked.exception.attempts), ('rate_limit', 0))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        self.now = 1351
        self.assertEqual(second.api('repos/o/r/pulls/2'), {'ok': True})
        self.assertEqual(len(self.calls), 2)

    def test_retry_after_uses_provider_date_even_when_response_is_old(self):
        headers = {'retry-after': '60', 'date': formatdate(1000, usegmt=True)}
        self.assertEqual(retry_deadline(headers, 'HTTP 429', 1040), 1060)
        self.now = 1050
        self.budget().observe('core', headers, 'HTTP 429', 1050)
        state = self.path.read_bytes()
        self.now = 1070
        self.budget().observe('core', headers, 'HTTP 429', 1070)
        self.assertEqual(self.path.read_bytes(), state)
        self.budget().check('core')

    def test_http_date_retry_after_and_later_primary_reset(self):
        headers = {'retry-after': formatdate(1200, usegmt=True), 'x-ratelimit-reset': '1400', 'x-ratelimit-remaining': '0'}
        self.assertEqual(retry_deadline(headers, 'HTTP 403 rate limit', 1000), 1400)

    def test_success_with_zero_remaining_stops_next_network_read(self):
        reader = self.reader([(0, 'HTTP/2.0 200\nX-RateLimit-Remaining: 0\nX-RateLimit-Reset: 1200\n\n{"number":1}', ''), (0, '{}', '')])
        self.assertEqual(reader.api('x'), {'number': 1})
        with self.assertRaises(GitHubUnreadable) as error:
            reader.api('y')
        self.assertEqual(error.exception.attempts, 0)
        self.assertEqual(len(self.calls), 1)

    def test_local_refusal_never_creates_a_provider_deadline(self):
        self.assertIsNone(retry_deadline({}, 'CARR_GITHUB_LOCAL_HOLD: provider rate limit', 1000))
        self.assertFalse(self.path.exists())

    def test_authentication_is_not_a_rate_hold(self):
        self.assertIsNone(retry_deadline({}, 'HTTP 401: verify credentials, not rate limit', 1000))

    def test_resource_and_principal_pools_are_separate(self):
        self.budget().observe('core', {'x-ratelimit-reset': '1500', 'x-ratelimit-remaining': '0'}, '', 1000)
        self.budget().check('graphql')
        app = GitHubReadBudget({**self.env, 'CARR_GITHUB_BUDGET_PRINCIPAL': 'app:123:org/repo'}, path=self.path, clock=lambda: self.now, spacing=0)
        app.check('core')
        with self.assertRaises(GitHubReadPaused):
            self.budget().check('core')

    def test_pacing_and_hold_arriving_during_queue_wait(self):
        gate = GitHubReadBudget(self.env, path=self.path, clock=lambda: self.now, spacing=2)
        self.assertEqual(gate.reserve('core'), 0)
        self.assertEqual(gate.reserve('core'), 2)
        def pause(seconds):
            self.now += seconds
            gate.observe('core', {'x-ratelimit-reset': '1400', 'x-ratelimit-remaining': '0'}, '', self.now)
        reader = self.reader([(0, '{}', '')], sleep=pause)
        reader.budget = gate
        with self.assertRaises(GitHubUnreadable) as error:
            reader.api('x')
        self.assertEqual((error.exception.kind, error.exception.attempts), ('rate_limit', 0))
        self.assertEqual(self.calls, [])

    def test_corrupt_state_fails_closed_without_network(self):
        self.path.write_text('not JSON')
        with self.assertRaises(GitHubUnreadable) as error:
            self.reader([(0, '{}', '')]).api('x')
        self.assertEqual(error.exception.kind, 'budget_unreadable')
        self.assertEqual(self.calls, [])

    def test_provider_pages_are_read_individually_and_headers_are_removed(self):
        next_page = 'HTTP/2.0 200\nLink: <https://api.github.com/repos/o/r/comments?page=2>; rel="next"\n\n[{"id":1}]'
        final_page = 'HTTP/2.0 200\nX-RateLimit-Remaining: 20\n\n[{"id":2}]'
        rows = self.reader([(0, next_page, ''), (0, final_page, '')]).api('repos/o/r/comments', paginate=True)
        self.assertEqual(rows, [{'id': 1}, {'id': 2}])
        self.assertEqual([a[2] for a in self.calls], ['repos/o/r/comments?per_page=100&page=1', 'repos/o/r/comments?per_page=100&page=2'])
        self.assertTrue(all('--paginate' not in a and '--include' in a for a in self.calls))

    def test_pagination_cap_never_returns_partial_evidence(self):
        response = 'HTTP/2.0 200\nLink: <https://api.github.com/x?page=2>; rel="next"\n\n[{"id":1}]'
        with self.assertRaises(GitHubUnreadable) as error:
            self.reader([(0, response, '')]).api('x', paginate=True, max_pages=1)
        self.assertEqual((error.exception.kind, len(self.calls)), ('pagination_limit', 1))

    def test_long_redacted_error_keeps_1640_classification(self):
        token = 'ghp_' + 'A' * 40
        reader = self.reader([(1, '', 'gh: API rate limit exceeded (HTTP 403) ' + token + ' ' + 'tail ' * 100)])
        with self.assertRaises(GitHubUnreadable) as error:
            reader.api('x')
        self.assertEqual((error.exception.kind, error.exception.transient, error.exception.attempts), ('rate_limit', True, 1))
        self.assertNotIn(token, str(error.exception))

    def test_cli_pagination_uses_shared_gate_and_returns_compatible_json(self):
        fixture = Path(self.tmp.name) / 'fake-gh'
        fixture.write_text('#!/usr/bin/env python3\nimport json,sys\nprint("HTTP/2.0 200\\nX-RateLimit-Remaining: 20\\n\\n"+json.dumps([{ "id":7 }]))\n')
        fixture.chmod(0o700)
        env = dict(os.environ, GH_LIMITER_REAL=str(fixture), CARR_GITHUB_READ_BUDGET=str(self.path))
        result = subprocess.run([sys.executable, str(ROOT / 'ops/github-gh.py'), 'api', '--paginate', 'repos/o/r/comments'], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, [{'id': 7}]))
        self.assertEqual(result.stderr, '')

    def test_secondary_hold_blocks_other_principals_without_shortening_primary_hold(self):
        gate = self.budget()
        gate.observe('unknown', {}, 'API rate limit exceeded', 1000)
        gate.observe('core', {'retry-after': '60'}, 'HTTP 429', 1000)
        app = GitHubReadBudget({**self.env, 'CARR_GITHUB_BUDGET_PRINCIPAL': 'app:123:org/repo'},
                               path=self.path, clock=lambda: self.now, spacing=0)
        with self.assertRaises(GitHubReadPaused) as app_hold:
            app.check('graphql')
        self.assertEqual(app_hold.exception.until, 1060)
        with self.assertRaises(GitHubReadPaused) as user_hold:
            gate.check('core')
        self.assertEqual(user_hold.exception.until, 1900)
        self.now = 1061
        app.check('graphql')
        with self.assertRaises(GitHubReadPaused):
            gate.check('core')

    def test_nested_local_refusal_and_expired_provider_reset_never_rearm(self):
        gate = self.budget()
        gate.observe('core', {}, 'gh api x exited 1: CARR_GITHUB_LOCAL_HOLD: provider rate limit', 1000)
        gate.observe('core', {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '900'}, '', 1000)
        self.assertFalse(self.path.exists())

    def test_invalid_deadlines_fail_closed(self):
        for value in ('NaN', '-5'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                retry_deadline({'retry-after': value}, 'HTTP 429', 1000)
        self.path.write_text('{"github.com:stored-login":{"holds":{"core":NaN}}}')
        with self.assertRaises(GitHubUnreadable) as error:
            self.reader([(0, '{}', '')]).api('x')
        self.assertEqual(error.exception.kind, 'budget_unreadable')
        self.assertEqual(self.calls, [])

    def test_utc_deadline_spans_midnight_but_expired_same_day_is_not_renewed(self):
        self.assertEqual(retry_deadline({}, 'rate limit: retry after 00:05:00 UTC', 86340), 86700)
        self.assertEqual(retry_deadline({}, 'rate limit: retry after 10:30:00 UTC', 39600), 37800)

    def test_native_pagination_is_refused_before_any_request(self):
        with self.assertRaises(GitHubUnreadable) as error:
            self.reader([(0, '[]', '')]).text(['api', 'x', '--paginate'])
        self.assertEqual((error.exception.kind, error.exception.attempts), ('invalid_response', 0))
        self.assertEqual(self.calls, [])

    def test_cli_keeps_actual_short_pages_and_applies_jq_per_page(self):
        fixture = Path(self.tmp.name) / 'fake-gh'
        fixture.write_text('#!/usr/bin/env python3\nimport sys\n'
            'print("HTTP/2.0 200\\nLink: <https://api.github.com/x?page=2>; rel=\\\"next\\\"\\n\\n[{\\\"id\\\":1}]" '
            'if "&page=1" in sys.argv[2] else "HTTP/2.0 200\\nDate: Thu, 01 Jan 1970 00:00:00 GMT\\n\\n[{\\\"id\\\":2}]")\n')
        fixture.chmod(0o700)
        env = dict(os.environ, GH_LIMITER_REAL=str(fixture), CARR_GITHUB_READ_BUDGET=str(self.path))
        for flags, expected in ((['--slurp'], '[[{"id": 1}], [{"id": 2}]]\n'),
                                (['--jq', 'length'], '1\n1\n'),
                                ([], '[{"id": 1}]\n[{"id": 2}]\n')):
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, str(ROOT / 'ops/github-gh.py'),
                    'api', 'x', '--paginate', *flags], env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ''))

    def test_cli_unsupported_pagination_flags_stop_before_request(self):
        env = dict(os.environ, GH_LIMITER_REAL='/usr/bin/false', CARR_GITHUB_READ_BUDGET=str(self.path))
        result = subprocess.run([sys.executable, str(ROOT / 'ops/github-gh.py'),
            'api', 'x', '--paginate', '--method', 'POST'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('supports GET paths', result.stderr)
        self.assertFalse(self.path.exists())

    def test_native_reader_avoids_budgeting_its_own_cli_wrapper_twice(self):
        link = Path(self.tmp.name) / 'gh'
        link.symlink_to(ROOT / 'ops/github-gh.py')
        binary = resolve_gh({}, which=lambda *a, **kw: str(link),
                            executable=lambda path: path == '/opt/homebrew/bin/gh')
        self.assertEqual(binary, '/opt/homebrew/bin/gh')


if __name__ == '__main__':
    unittest.main()
