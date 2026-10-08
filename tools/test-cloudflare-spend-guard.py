"""Cloudflare spend guard: every branch runs against a mocked Cloudflare API.

No test touches the network. FakeCloudflare stands in for the three API
surfaces the guard uses (billable usage info, billable usage, Worker
workers.dev subdomain) and records every call, so a test can assert both
what the guard decided and exactly which Worker endpoints it touched.
"""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    'cloudflare_spend_guard', Path(__file__).with_name('cloudflare_spend_guard.py'))
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

ACCOUNT = '0123456789abcdef0123456789abcdef'
BASE = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}'
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
STAGING = ['doctorcre-app-staging', 'carr-mcp-staging']
PRODUCTION = ['carr-mcp', 'doctorcre-app']


def config(**overrides):
    base = {
        'schema_version': 1,
        'account_id': ACCOUNT,
        'token_name': 'CLOUDFLARE_SPEND_GUARD_TOKEN',
        'state_dir': '~/.local/state/carr/cloudflare-spend-guard',
        'request_timeout_seconds': 20,
        'max_usage_age_hours': 54,
        'e2e_max_evaluation_age_hours': 30,
        'thresholds': {'warn_allowance_fraction': 0.5, 'stop_allowance_fraction': 0.8,
                       'stop_metered_usd_above': 0},
        'staging_workers': list(STAGING),
        'production_workers': list(PRODUCTION),
        'stop_production_workers': False,
        'allowances': [
            {'key': 'workers-requests', 'service_names': ['Workers Standard'], 'consumed_unit': '',
             'included_per_period': 10_000_000, 'source': 'https://developers.cloudflare.com/workers/platform/pricing/'},
            {'key': 'r2-class-a', 'service_names': ['R2 Class A Operations'], 'consumed_unit': '',
             'included_per_period': 1_000_000, 'source': 'https://developers.cloudflare.com/r2/pricing/'},
        ],
    }
    base.update(overrides)
    return guard.validate_config(base)


def row(service='Workers Standard', consumed=1000, cost=0.0, pricing=0, day=7, unit='', currency='USD',
        period_start='2026-10-01T00:00:00Z'):
    start = datetime(2026, 10, day, tzinfo=timezone.utc)
    return {'BilledCost': cost, 'ContractedCost': cost, 'BillingCurrency': currency,
            'BillingPeriodStart': period_start, 'ChargeCategory': 'Usage',
            'ChargePeriodStart': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'ChargePeriodEnd': (start + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'ConsumedQuantity': consumed, 'ConsumedUnit': unit, 'CumulatedPricingQuantity': pricing,
            'PricingQuantity': pricing, 'ServiceName': service, 'ServiceFamilyName': service.split()[0],
            'SubscriptionId': 'SUB1'}


class FakeCloudflare:
    """A recording stand-in for the Cloudflare API endpoints the guard calls."""

    def __init__(self, rows=None, subdomains=None):
        self.info = {'success': True, 'errors': [], 'messages': [], 'result': {
            'covered': True, 'subscriptions': [{'id': 'SUB1', 'start_timestamp': '2026-01-01T00:00:00Z',
                                                'billing_cycle_anchor_timestamp': '2026-01-01T00:00:00Z'}]}}
        self.usage = {'success': True, 'errors': [], 'messages': [],
                      'result': rows if rows is not None else [row()]}
        self.subdomains = subdomains or {name: {'enabled': True, 'previews_enabled': True}
                                         for name in STAGING + PRODUCTION}
        self.calls = []
        self.fail = {}  # (method, path-suffix) -> exception instance or (status, payload)

    def __call__(self, method, url, headers, body, timeout):
        assert url.startswith(BASE), url
        assert headers['Authorization'] == 'Bearer synthetic-token'
        path = url[len(BASE):]
        self.calls.append((method, path, body))
        for (fail_method, suffix), outcome in self.fail.items():
            if fail_method == method and path.endswith(suffix):
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
        if method == 'GET' and path == '/billable-usage/info':
            return 200, copy.deepcopy(self.info)
        if method == 'GET' and path == '/billable-usage':
            return 200, copy.deepcopy(self.usage)
        if path.startswith('/workers/scripts/') and path.endswith('/subdomain'):
            script = path.split('/')[3]
            if method == 'POST':
                self.subdomains[script] = {'enabled': body['enabled'],
                                           'previews_enabled': body['previews_enabled']}
            return 200, {'success': True, 'errors': [], 'messages': [],
                         'result': dict(self.subdomains[script])}
        raise AssertionError(f'unexpected call {method} {path}')

    def worker_calls(self):
        return [(m, p) for m, p, _ in self.calls if p.startswith('/workers/')]

    def posts(self):
        return [(p.split('/')[3], b) for m, p, b in self.calls if m == 'POST']


class GuardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / 'state'

    def tearDown(self):
        self._tmp.cleanup()

    def run_guard(self, fake, cfg=None, now=NOW, token='synthetic-token'):
        return guard.run(cfg or config(), http=fake, token=token, now=now, state_dir=self.state)

    def gate(self, cfg=None, now=NOW):
        return guard.e2e_gate(cfg or config(), now=now, state_dir=self.state)


class Thresholds(GuardCase):
    def test_under_threshold_is_ok_and_touches_no_worker(self):
        fake = FakeCloudflare([row(consumed=4_000_000)])
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'OK')
        self.assertEqual(fake.worker_calls(), [])
        self.assertEqual(guard.read_receipt(self.state)['state'], 'CLEAR')
        self.assertEqual(self.gate()[0], True)

    def test_fifty_percent_of_an_allowance_warns_without_acting(self):
        fake = FakeCloudflare([row(consumed=5_000_000)])
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'WARN')
        self.assertIn('workers-requests', ' '.join(result['reasons']))
        self.assertEqual(fake.worker_calls(), [])
        self.assertTrue(self.gate()[0])

    def test_eighty_percent_of_an_allowance_stops(self):
        fake = FakeCloudflare([row(service='R2 Class A Operations', consumed=800_000)])
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'STOP')
        self.assertEqual(sorted(s for s, _ in fake.posts()), sorted(STAGING))

    def test_one_cent_metered_stops_staging_only(self):
        fake = FakeCloudflare([row(consumed=10_100_000, cost=0.01, pricing=100_000)])
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'STOP')
        self.assertIn('$0.01', ' '.join(result['reasons']))
        self.assertEqual(fake.posts(), [(s, {'enabled': False, 'previews_enabled': False}) for s in STAGING])
        touched = {p.split('/')[3] for _, p in fake.worker_calls()}
        self.assertEqual(touched, set(STAGING))
        for name in PRODUCTION:
            self.assertEqual(fake.subdomains[name], {'enabled': True, 'previews_enabled': True})
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['state'], 'STOPPED')
        self.assertEqual(receipt['hold'], 'spend')
        allowed, reason = self.gate()
        self.assertFalse(allowed)
        self.assertIn('STOPPED', reason)

    def test_beyond_included_quantity_stops_even_before_it_is_priced(self):
        fake = FakeCloudflare([row(consumed=10_000_001, cost=0.0, pricing=1)])
        self.assertEqual(self.run_guard(fake)['verdict'], 'STOP')

    def test_production_flag_off_reports_production_without_touching_it(self):
        fake = FakeCloudflare([row(cost=2.5, pricing=10)])
        result = self.run_guard(fake)
        self.assertEqual(result['production'], {'mode': 'report-only', 'workers': PRODUCTION})
        self.assertNotIn('carr-mcp', {p.split('/')[3] for _, p in fake.worker_calls()})

    def test_production_flag_on_also_stops_production(self):
        fake = FakeCloudflare([row(cost=2.5, pricing=10)])
        result = self.run_guard(fake, cfg=config(stop_production_workers=True))
        self.assertEqual(result['production']['mode'], 'stop')
        self.assertEqual(sorted(s for s, _ in fake.posts()), sorted(STAGING + PRODUCTION))

    def test_unmapped_service_with_usage_warns_and_names_it(self):
        fake = FakeCloudflare([row(service='Queues Standard Operations', consumed=10)])
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'WARN')
        self.assertIn('Queues Standard Operations', ' '.join(result['reasons']))


class FailClosed(GuardCase):
    def assert_unknown_without_worker_change(self, fake, token='synthetic-token'):
        result = self.run_guard(fake, token=token)
        self.assertEqual(result['verdict'], 'UNKNOWN')
        self.assertEqual(fake.worker_calls(), [])
        receipt = guard.read_receipt(self.state)
        self.assertEqual((receipt['state'], receipt['hold']), ('STOPPED', 'unknown'))
        allowed, reason = self.gate()
        self.assertFalse(allowed)
        self.assertIn('UNKNOWN', reason)
        return result

    def test_http_error_is_unknown(self):
        fake = FakeCloudflare()
        fake.fail[('GET', '/billable-usage')] = (500, {'success': False, 'errors': [{'code': 1, 'message': 'x'}]})
        self.assert_unknown_without_worker_change(fake)

    def test_timeout_is_unknown(self):
        fake = FakeCloudflare()
        fake.fail[('GET', '/billable-usage/info')] = TimeoutError('timed out')
        self.assert_unknown_without_worker_change(fake)

    def test_malformed_payloads_are_unknown(self):
        for broken in ({'success': True}, {'success': True, 'result': [{'BilledCost': 'abc'}]},
                       {'success': False, 'result': []}, ['not', 'an', 'object'],
                       {'success': True, 'result': [dict(row(), BilledCost=-1)]},
                       {'success': True, 'result': [dict(row(), BilledCost=float('nan'))]}):
            with self.subTest(broken=broken):
                self.tearDown(); self.setUp()
                fake = FakeCloudflare()
                fake.usage = broken
                self.assert_unknown_without_worker_change(fake)

    def test_non_json_body_is_unknown(self):
        fake = FakeCloudflare()
        fake.fail[('GET', '/billable-usage')] = guard.UsageUnavailable('response body is not JSON')
        self.assert_unknown_without_worker_change(fake)

    def test_missing_token_is_unknown_and_calls_nothing(self):
        fake = FakeCloudflare()
        result = self.assert_unknown_without_worker_change(fake, token=None)
        self.assertEqual(fake.calls, [])
        self.assertIn('token', ' '.join(result['reasons']))

    def test_uncovered_account_non_usd_unit_drift_and_stale_data_are_unknown(self):
        cases = {
            'uncovered': lambda f: f.info['result'].update(covered=False),
            'currency': lambda f: f.usage.update(result=[row(currency='EUR')]),
            'unit': lambda f: f.usage.update(result=[row(unit='GB-months')]),
            'stale': lambda f: f.usage.update(result=[row(day=1)]),
            'mixed periods': lambda f: f.usage.update(result=[row(), row(period_start='2026-09-01T00:00:00Z')]),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.tearDown(); self.setUp()
                fake = FakeCloudflare()
                mutate(fake)
                self.assert_unknown_without_worker_change(fake)

    def test_no_rows_is_ok_early_in_the_period_and_unknown_later(self):
        early = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
        self.assertEqual(self.run_guard(FakeCloudflare([]), now=early)['verdict'], 'OK')
        self.assertEqual(self.run_guard(FakeCloudflare([]), now=NOW)['verdict'], 'UNKNOWN')

    def test_unknown_hold_clears_on_the_next_readable_run(self):
        broken = FakeCloudflare()
        broken.fail[('GET', '/billable-usage')] = TimeoutError()
        self.run_guard(broken)
        self.assertFalse(self.gate()[0])
        self.run_guard(FakeCloudflare([row()]))
        self.assertEqual(guard.read_receipt(self.state)['state'], 'CLEAR')
        self.assertTrue(self.gate()[0])

    def test_unknown_after_stop_keeps_the_spend_hold_and_its_restore_list(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        broken = FakeCloudflare()
        broken.fail[('GET', '/billable-usage')] = TimeoutError()
        self.run_guard(broken)
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['hold'], 'spend')
        self.assertEqual([w['script'] for w in receipt['disabled_workers']], STAGING)


class StopIsIdempotentAndReversible(GuardCase):
    def test_rerun_does_not_repost_and_keeps_the_original_prior_state(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.subdomains['carr-mcp-staging'] = {'enabled': True, 'previews_enabled': False}
        self.run_guard(fake)
        first = guard.read_receipt(self.state)['disabled_workers']
        fake.calls.clear()
        self.assertEqual(self.run_guard(fake)['verdict'], 'STOP')
        self.assertEqual(fake.posts(), [])
        self.assertEqual(guard.read_receipt(self.state)['disabled_workers'], first)
        self.assertEqual(first[1], {'script': 'carr-mcp-staging',
                                    'prior': {'enabled': True, 'previews_enabled': False}})
        self.assertEqual(len(guard.read_history(self.state)), 2)

    def test_redeploy_between_stops_is_disabled_again_without_a_second_restore_entry(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        self.run_guard(fake)
        first = guard.read_receipt(self.state)['disabled_workers']
        fake.subdomains['carr-mcp-staging'] = {'enabled': True, 'previews_enabled': True}  # wrangler deploy --env staging
        fake.calls.clear()
        self.run_guard(fake)
        self.assertEqual(fake.posts(), [('carr-mcp-staging', {'enabled': False, 'previews_enabled': False})])
        self.assertEqual(guard.read_receipt(self.state)['disabled_workers'], first)

    def test_ok_read_after_stop_keeps_the_hold_until_restore(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        result = self.run_guard(FakeCloudflare([row()]))
        self.assertEqual(result['verdict'], 'OK')
        self.assertEqual(guard.read_receipt(self.state)['state'], 'STOPPED')
        self.assertFalse(self.gate()[0])

    def test_already_disabled_worker_is_not_recorded_for_restore(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.subdomains['doctorcre-app-staging'] = {'enabled': False, 'previews_enabled': False}
        self.run_guard(fake)
        self.assertEqual([s for s, _ in fake.posts()], ['carr-mcp-staging'])
        disabled = guard.read_receipt(self.state)['disabled_workers']
        self.assertEqual([w['script'] for w in disabled], ['carr-mcp-staging'])

    def test_failed_disable_still_writes_the_receipt_and_reports_the_error(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.fail[('POST', '/workers/scripts/carr-mcp-staging/subdomain')] = (403, {'success': False})
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'STOP')
        self.assertTrue(result['action_errors'])
        self.assertEqual(guard.read_receipt(self.state)['state'], 'STOPPED')
        self.assertFalse(self.gate()[0])

    def test_restore_reenables_exactly_what_the_guard_disabled(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.subdomains['carr-mcp-staging'] = {'enabled': True, 'previews_enabled': False}
        self.run_guard(fake)
        fake.calls.clear()
        result = guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertTrue(result['restored'])
        self.assertEqual(fake.posts(), [('doctorcre-app-staging', {'enabled': True, 'previews_enabled': True}),
                                        ('carr-mcp-staging', {'enabled': True, 'previews_enabled': False})])
        self.assertEqual(guard.read_receipt(self.state)['state'], 'CLEAR')
        self.assertTrue(self.gate()[0])
        fake.calls.clear()
        guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertEqual(fake.posts(), [])

    def test_partial_restore_keeps_the_hold_and_the_remaining_worker(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        self.run_guard(fake)
        fake.fail[('POST', '/workers/scripts/carr-mcp-staging/subdomain')] = TimeoutError()
        result = guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertFalse(result['restored'])
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['state'], 'STOPPED')
        self.assertEqual([w['script'] for w in receipt['disabled_workers']], ['carr-mcp-staging'])

    def test_restore_without_token_changes_nothing(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        before = guard.read_receipt(self.state)
        result = guard.restore(config(), http=FakeCloudflare(), token=None, now=NOW, state_dir=self.state)
        self.assertFalse(result['restored'])
        self.assertEqual(guard.read_receipt(self.state), before)


class Receipt(GuardCase):
    def test_receipt_survives_a_fresh_interpreter(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        script = ("import importlib.util,json,sys;from datetime import datetime,timezone;"
                  "s=importlib.util.spec_from_file_location('g',sys.argv[1]);g=importlib.util.module_from_spec(s);"
                  "s.loader.exec_module(g);cfg=g.load_config();"
                  "print(json.dumps(g.e2e_gate(cfg,now=datetime.fromisoformat(sys.argv[3]),state_dir=sys.argv[2])))")
        out = subprocess.run([sys.executable, '-c', script, str(Path(__file__).with_name('cloudflare_spend_guard.py')),
                              str(self.state), NOW.isoformat()], capture_output=True, text=True, check=True)
        allowed, reason = json.loads(out.stdout)
        self.assertFalse(allowed)
        self.assertIn('STOPPED', reason)

    def test_gate_holds_without_receipt_on_corruption_and_when_stale(self):
        self.assertFalse(self.gate()[0])
        self.run_guard(FakeCloudflare([row()]))
        self.assertTrue(self.gate()[0])
        self.assertFalse(self.gate(now=NOW + timedelta(hours=31))[0])
        guard.receipt_path(self.state).write_text('{not json')
        self.assertFalse(self.gate()[0])

    def test_corrupt_receipt_is_quarantined_and_holds_until_restore(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        guard.receipt_path(self.state).write_text('{torn write')
        result = self.run_guard(FakeCloudflare([row()]))
        self.assertEqual(result['verdict'], 'OK')
        receipt = guard.read_receipt(self.state)
        self.assertEqual((receipt['state'], receipt['hold']), ('STOPPED', 'receipt-corrupt'))
        quarantined = list(self.state.glob('receipt.corrupt.*.json'))
        self.assertEqual([p.read_text() for p in quarantined], ['{torn write'])
        self.assertFalse(self.gate()[0])
        self.run_guard(FakeCloudflare([row()]))
        self.assertFalse(self.gate()[0])
        guard.restore(config(), http=FakeCloudflare(), token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertTrue(self.gate()[0])

    def test_receipt_write_is_atomic_and_leaves_no_temp_files(self):
        self.run_guard(FakeCloudflare([row()]))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ['history.jsonl', 'receipt.json'])

    def test_health_line_names_its_bound_action(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        line = guard.health_line(guard.read_receipt(self.state))
        self.assertTrue(line.startswith('STOP cloudflare spend'))
        for part in ('on breach:', 'doctorcre-app-staging', 'carr-mcp-staging', 'owner joe',
                     'remediation', './run.sh cloudflare-spend-guard restore', 'verify', 'auto-clear'):
            self.assertIn(part, line)
        self.assertNotIn('synthetic-token', line)


class Config(unittest.TestCase):
    def test_committed_config_holds_the_ruled_thresholds(self):
        cfg = guard.load_config()
        self.assertEqual(cfg['thresholds'], {'warn_allowance_fraction': 0.5, 'stop_allowance_fraction': 0.8,
                                             'stop_metered_usd_above': 0})
        self.assertEqual(cfg['staging_workers'], STAGING)
        self.assertIs(cfg['stop_production_workers'], False)
        self.assertEqual(cfg['account_id'], '12ccca77eb49142a6be8eb84c0d6a3a0')

    def test_invalid_configs_are_refused(self):
        for bad in ({'thresholds': {'warn_allowance_fraction': 0.8, 'stop_allowance_fraction': 0.5,
                                    'stop_metered_usd_above': 0}},
                    {'production_workers': ['carr-mcp-staging']},
                    {'staging_workers': []},
                    {'account_id': 'nope'},
                    {'stop_production_workers': 'yes'}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    config(**bad)

    def test_cli_has_no_threshold_or_target_overrides(self):
        for argv in (['run', '--warn', '0.9'], ['run', '--config', 'x.json'], ['run', '--state-dir', '/tmp/x']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                guard.parse_args(argv)


if __name__ == '__main__':
    unittest.main()
