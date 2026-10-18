"""Cloudflare spend guard: every branch runs against a mocked Cloudflare API.

No test touches the network. FakeCloudflare stands in for the three API
surfaces the guard uses (billable usage info, billable usage, Worker
workers.dev subdomain) and records every call, so a test can assert both
what the guard decided and exactly which Worker endpoints it touched.
"""

import copy
import errno
import http.client
import importlib.util
import io
import json
import re
import threading
import time
import urllib.request
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'cloudflare_spend_guard', Path(__file__).with_name('cloudflare_spend_guard.py'))
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def _no_real_reporter(*args, **kwargs):
    raise AssertionError('a test reached the real record-layer reporter; pass spawn_reporter')


# No test may start the real reporter, which would call the live record layer.
guard._loop_watch().spawn_reporter = _no_real_reporter

ACCOUNT = '0123456789abcdef0123456789abcdef'
BASE = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}'
GRAPHQL = 'https://api.cloudflare.com/client/v4/graphql'
OFF = {'enabled': False, 'previews_enabled': False}
ON = {'enabled': True, 'previews_enabled': True}
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
STAGING = ['doctorcre-app-staging', 'carr-mcp-staging']
PRODUCTION = ['carr-mcp', 'doctorcre-app']


_inventory_tmp = tempfile.TemporaryDirectory()
INVENTORY = {}
for script in STAGING + PRODUCTION:
    path = Path(_inventory_tmp.name) / (script + '.json')
    path.write_text(json.dumps({'env': {'staging': {'name': script, 'workers_dev': False, 'routes': []}}}))
    INVENTORY[script] = str(path)


def config(**overrides):
    base = {
        'schema_version': 1,
        'price_model': {'version': '2026-10-08-workers-logs-events-v1',
                        'valid_from': '2026-01-01', 'valid_until': '2026-12-01'},
        'account_id': ACCOUNT,
        'token_name': 'CLOUDFLARE_SPEND_GUARD_TOKEN',
        'state_dir': '~/.local/state/carr/cloudflare-spend-guard',
        'request_timeout_seconds': 20,
        'max_usage_age_hours': 54,
        'e2e_max_evaluation_age_hours': 30,
        'thresholds': {'warn_allowance_fraction': 0.5, 'stop_allowance_fraction': 0.8,
                       'stop_metered_usd_above': 0},
        'staging_workers': list(STAGING),
        'staging_configs': dict(INVENTORY),
        'production_workers': list(PRODUCTION),
        'stop_production_workers': False,
        'allowances': [
            {'key': 'workers-requests', 'service_names': ['Workers Standard'], 'consumed_unit': '',
             'included_per_period': 10_000_000, 'source': 'https://developers.cloudflare.com/workers/platform/pricing/'},
            {'key': 'r2-class-a', 'service_names': ['R2 Class A Operations'], 'consumed_unit': '',
             'included_per_period': 1_000_000, 'source': 'https://developers.cloudflare.com/r2/pricing/'},
        ],
        'fast_path': {
            'max_query_span_days': 7,
            'query_limit': 10000,
            'burst_window_minutes': 15,
            'burst_requests_staging': 20000,
            'r2_class_b_actions': ['HeadBucket', 'HeadObject', 'GetObject', 'UsageSummary', 'GetBucketEncryption',
                                   'GetBucketLocation', 'GetBucketCors', 'GetBucketLifecycleConfiguration'],
            'r2_free_actions': ['DeleteObject', 'DeleteBucket', 'AbortMultipartUpload'],
            'allowances': [
                {'key': 'workers-requests', 'metric': 'workers.requests', 'included_per_period': 10_000_000},
                {'key': 'kv-reads', 'metric': 'kv.read', 'included_per_period': 10_000_000},
                {'key': 'kv-writes', 'metric': 'kv.write', 'included_per_period': 1_000_000},
                {'key': 'kv-deletes', 'metric': 'kv.delete', 'included_per_period': 1_000_000},
                {'key': 'kv-lists', 'metric': 'kv.list', 'included_per_period': 1_000_000},
                {'key': 'r2-class-a', 'metric': 'r2.class_a', 'included_per_period': 1_000_000},
                {'key': 'r2-class-b', 'metric': 'r2.class_b', 'included_per_period': 10_000_000},
                {'key': 'durable-objects-requests', 'metric': 'do.requests', 'included_per_period': 1_000_000},
            ],
        },
        'runaway': {'floor_requests': 2000, 'spike_multiple': 5, 'history_days': 7, 'min_history_days': 3,
                    'error_ratio_above': 0.5, 'error_min_requests': 500},
        'loop_watch_heartbeat_stale_seconds': 300,
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
        self.schedules = {name: [] for name in STAGING + PRODUCTION}
        self.calls = []
        self.fail = {}  # (method, path-suffix) -> exception instance or (status, payload)
        self.ignore_posts = False  # a POST that reports success but changes nothing
        self.on_post = None  # called with the script name just before a POST is applied
        self.gql = {}  # alias -> rows for the fast path's single GraphQL request
        self.gql_fail = None  # exception instance or (status, payload)
        self.queries = []

    def __call__(self, method, url, headers, body, timeout):
        assert headers['Authorization'] == 'Bearer synthetic-token', 'token was not cleaned'
        if url == GRAPHQL:
            return self._graphql(method, body)
        assert url.startswith(BASE), url
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
        if method == 'PATCH' and path.startswith('/workers/workers/'):
            script = {'worker-id-0': STAGING[0], 'worker-id-1': STAGING[1]}[path.split('/')[-1]]
            self.subdomains[script] = dict(body['subdomain'])
            return 200, {'success': True, 'result': {'id': path.split('/')[-1],
                                                  'subdomain': dict(self.subdomains[script])}}
        if path.startswith('/workers/scripts/') and path.endswith('/schedules'):
            script = path.split('/')[3]
            if method == 'PUT':
                self.schedules[script] = copy.deepcopy(body)
            return 200, {'success': True, 'result': {'schedules': copy.deepcopy(self.schedules[script])}}
        if path.startswith('/workers/scripts/') and path.endswith('/subdomain'):
            script = path.split('/')[3]
            if method == 'POST':
                if self.on_post:
                    self.on_post(script)
                if not self.ignore_posts:
                    self.subdomains[script] = {'enabled': body['enabled'],
                                               'previews_enabled': body['previews_enabled']}
            return 200, {'success': True, 'errors': [], 'messages': [],
                         'result': dict(self.subdomains[script])}
        raise AssertionError(f'unexpected call {method} {path}')

    def _graphql(self, method, body):
        assert method == 'POST'
        self.calls.append(('POST', '/graphql', None))
        self.queries.append(body)
        if isinstance(self.gql_fail, BaseException):
            raise self.gql_fail
        if self.gql_fail:
            return self.gql_fail
        account = {}
        for alias, node in re.findall(r'(\w+)\s*:\s*(\w+)\s*\(', body['query']):
            default = []
            if alias.startswith('w'):
                default = [mtd('carr-mcp', 1)]
            elif alias == 'cur':
                default = [window(s, 1) for s in STAGING + PRODUCTION]
            account[alias] = copy.deepcopy(self.gql.get(alias, default))
        return 200, {'data': {'viewer': {'accounts': [account]}}, 'errors': None}

    def graphql_calls(self):
        return [c for c in self.calls if c[1] == '/graphql']

    def worker_calls(self):
        return [(m, p) for m, p, _ in self.calls if p.startswith('/workers/')]

    def posts(self):
        return [(p.split('/')[3], b) for m, p, b in self.calls if m == 'POST' and p.startswith('/workers/')]


class GuardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / 'state'
        self.spawned = []

    def tearDown(self):
        self._tmp.cleanup()

    def run_guard(self, fake, cfg=None, now=NOW, token='synthetic-token'):
        return guard.run(cfg or config(), http=fake, token=token, now=now, state_dir=self.state,
                         spawn_reporter=lambda: self.spawned.append(1))

    def outbox(self):
        folder = self.state / 'outbox'
        return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))] if folder.exists() else []

    def gate(self, cfg=None, now=NOW):
        return guard.e2e_gate(cfg or config(), now=now, state_dir=self.state)


class PreRunGate(GuardCase):
    def test_pre_run_refuses_each_allowance_at_fifty_percent_without_writes(self):
        for service, included in (('Workers Standard', 10_000_000), ('R2 Class A Operations', 1_000_000)):
            with self.subTest(service=service):
                for consumed, expected in ((included / 2 - 1, 0), (included / 2, 1), (included, 1)):
                    fake = FakeCloudflare([row(service=service, consumed=consumed)])
                    code, out, err = call_main(['check-e2e'], self.state, http=fake)
                    self.assertEqual(code, expected, out + err)
                    self.assertTrue(all(m == 'GET' and p.startswith('/billable-usage') for m, p, b in fake.calls))
                    self.assertFalse(self.state.exists(), 'pre-run must not write receipt, lock or outbox')

    def test_existing_stop_or_unknown_analytics_cannot_be_cleared_by_pre_run(self):
        self.run_guard(FakeCloudflare([row(cost=0.01)]))
        before = files_text(self.state)
        fake = FakeCloudflare()
        self.assertEqual(call_main(['check-e2e'], self.state, http=fake)[0], 1)
        self.assertEqual(files_text(self.state), before)
        self.assertEqual(fake.calls, [])


class PatchReplacement(GuardCase):
    def test_opt_in_patch_uses_worker_id_and_nested_subdomain_and_default_stays_post(self):
        cfg = config(use_worker_patch=True, worker_ids={s: f'worker-id-{i}' for i, s in enumerate(STAGING)})
        fake = FakeCloudflare([row(cost=0.01)])
        self.assertEqual(self.run_guard(fake, cfg)['verdict'], 'STOP')
        patches = [(p, b) for m, p, b in fake.calls if m == 'PATCH']
        self.assertEqual(patches, [(f'/workers/workers/worker-id-{i}', {'subdomain': OFF}) for i in range(2)])
        self.assertFalse(fake.posts())
        self.assertTrue(guard.restore(cfg, http=fake, token='synthetic-token', now=NOW, state_dir=self.state)['restored'])
        self.assertTrue(all(fake.subdomains[s] == ON for s in STAGING))
        self.tearDown(); self.setUp()
        fake = FakeCloudflare([row(cost=0.01)])
        self.run_guard(fake)
        self.assertEqual(len(fake.posts()), 2)
        self.assertFalse(any(m == 'PATCH' for m, p, b in fake.calls))

    def test_patch_failure_is_action_failed_four(self):
        cfg = config(use_worker_patch=True, worker_ids={s: f'worker-id-{i}' for i, s in enumerate(STAGING)})
        fake = FakeCloudflare([row(cost=0.01)])
        fake.fail[('PATCH', 'worker-id-0')] = (403, {'success': False})
        result = self.run_guard(fake, cfg)
        self.assertEqual(guard._exit_for(result['verdict'], result['action_errors']), 4)


class PriceModel(GuardCase):
    def test_workers_logs_cutover_fails_closed_before_any_api_call(self):
        cfg = config()
        for operation in (guard.run, guard.fast):
            with self.subTest(operation=operation.__name__):
                fake = FakeCloudflare()
                result = operation(cfg, http=fake, token='synthetic-token',
                                   now=datetime(2026, 12, 1, tzinfo=timezone.utc), state_dir=self.state,
                                   spawn_reporter=lambda: None)
                self.assertEqual(result['verdict'], 'UNKNOWN')
                self.assertIn('price model', ' '.join(result['reasons']))
                self.assertEqual(fake.calls, [])
                self.assertFalse(guard.e2e_gate(cfg, now=NOW, state_dir=self.state)[0])


class DeployDrift(GuardCase):
    def test_hold_requires_workers_dev_false_and_check_is_read_only(self):
        self.run_guard(FakeCloudflare([row(cost=0.01)]))
        before = guard.receipt_path(self.state).read_bytes()
        path = Path(self._tmp.name) / 'wrangler.json'
        path.write_text(json.dumps({'env': {'staging': {'name': STAGING[0], 'workers_dev': True}}}))
        cfg = config(staging_configs={**INVENTORY, STAGING[0]: str(path)})
        self.assertFalse(guard.deploy_gate(cfg, state_dir=self.state)[0])
        self.assertIn('workers_dev=false', guard.deploy_gate(cfg, state_dir=self.state)[1])
        path.write_text(json.dumps({'env': {'staging': {'name': STAGING[0], 'workers_dev': False}}}))
        self.assertTrue(guard.deploy_gate(cfg, state_dir=self.state)[0])
        self.assertEqual(guard.receipt_path(self.state).read_bytes(), before)
        with mock.patch.object(guard, 'read_token', side_effect=AssertionError('read-only check read token')):
            code = guard.main(['check-deploy'], cfg=cfg, state_dir=self.state, now=NOW)
        self.assertEqual(code, 0)


class InventoryGate(GuardCase):
    def test_unsupported_staging_triggers_refuse_stop_without_cloudflare_writes(self):
        for field, value in (('queues', {'consumers': [{'queue': 'q'}]}),
                             ('durable_objects', {'bindings': [{'name': 'ALARM', 'class_name': 'Alarm'}]}),
                             ('workflows', [{'binding': 'FLOW', 'class_name': 'Flow'}])):
            with self.subTest(field=field):
                path = Path(self._tmp.name) / 'wrangler.json'
                path.write_text(json.dumps({'env': {'staging': {'name': STAGING[0], 'workers_dev': False,
                                                               field: value}}}))
                cfg = config(staging_configs={**INVENTORY, STAGING[0]: str(path)})
                fake = FakeCloudflare([row(cost=0.01)])
                result = self.run_guard(fake, cfg)
                self.assertEqual(result['verdict'], 'UNKNOWN')
                self.assertIn(field, ' '.join(result['reasons']))
                self.assertFalse(any(m != 'GET' for m, p, b in fake.calls))
                self.assertFalse(self.gate(cfg)[0])

    def test_runaway_with_alarm_binding_reports_unknown_without_claiming_a_disable(self):
        cfg = config(staging_workers=['carr-mcp-staging'], staging_configs={
            'carr-mcp-staging': str(Path(__file__).resolve().parents[1] / 'mcp-server/wrangler.toml')})
        fake = FakeCloudflare()
        fake.gql = {'cur': [window('carr-mcp-staging', 600, errors=400)]}
        with redirect_stderr(io.StringIO()) as err:
            result = guard.fast(cfg, http=fake, token='synthetic-token', now=NOW, state_dir=self.state,
                                spawn_reporter=lambda: None)
        self.assertEqual(result['verdict'], 'UNKNOWN')
        self.assertEqual(fake.worker_calls(), [])
        self.assertIn('no stop attempted', err.getvalue())
        self.assertFalse(any('workers.dev disabled on' in x['args']['actual'] for x in self.outbox()))

    def test_repository_staging_has_alarm_capable_binding(self):
        cfg = config(staging_workers=['carr-mcp-staging'], staging_configs={
            'carr-mcp-staging': str(Path(__file__).resolve().parents[1] / 'mcp-server/wrangler.toml')})
        result = self.run_guard(FakeCloudflare([row(cost=0.01)]), cfg)
        self.assertEqual(result['verdict'], 'UNKNOWN')
        self.assertIn('durable_objects', ' '.join(result['reasons']))


class CronStop(GuardCase):
    def test_cron_write_failure_keeps_snapshot_and_exit_four_then_retry_restores(self):
        fake = FakeCloudflare([row(cost=0.01)])
        prior = [{'cron': '*/5 * * * *'}]
        fake.schedules[STAGING[0]] = copy.deepcopy(prior)
        fake.fail[('PUT', '/schedules')] = TimeoutError('response lost')
        result = self.run_guard(fake)
        self.assertEqual(guard._exit_for(result['verdict'], result['action_errors']), 4)
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['disabled_schedules'][0]['prior'], prior)
        self.assertEqual(receipt['disabled_schedules'][0]['status'], 'pending')
        # The request may have succeeded remotely even though its response was lost.
        fake.schedules[STAGING[0]] = []
        fake.fail.clear()
        restored = guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertTrue(restored['restored'])
        self.assertEqual(fake.schedules[STAGING[0]], prior)

    def test_missing_inventory_fails_closed_with_no_disable(self):
        fake = FakeCloudflare([row(cost=0.01)])
        cfg = config(staging_configs={})
        result = self.run_guard(fake, cfg)
        self.assertEqual(result['verdict'], 'UNKNOWN')
        self.assertEqual(fake.worker_calls(), [])

    def test_stop_snapshots_cron_even_when_public_entry_already_off_and_restores(self):
        fake = FakeCloudflare([row(cost=0.01)], subdomains={s: dict(OFF) for s in STAGING + PRODUCTION})
        prior = [{'cron': '*/5 * * * *', 'created_on': '2026-10-01T00:00:00Z'}]
        fake.schedules[STAGING[0]] = copy.deepcopy(prior)
        original = guard._write_receipt
        def write_before_put(state, receipt, event):
            if event['event'] == 'cron-disable-pending':
                self.assertEqual(fake.schedules[STAGING[0]], prior)
                self.assertEqual(receipt['disabled_schedules'][0]['prior'], prior)
            original(state, receipt, event)
        with mock.patch.object(guard, '_write_receipt', side_effect=write_before_put):
            result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'STOP')
        self.assertEqual(fake.schedules[STAGING[0]], [])
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['disabled_schedules'][0]['prior'], prior)
        self.assertEqual(receipt['stop_propagation']['not_before'], '2026-10-08T12:15:00Z')
        self.assertIn('15 minutes', receipt['stop_propagation']['note'])
        result = guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertTrue(result['restored'])
        self.assertEqual(fake.schedules[STAGING[0]], prior)

    def test_new_stop_after_restore_gets_a_new_propagation_window(self):
        fake = FakeCloudflare([row(cost=0.01)])
        self.run_guard(fake)
        self.assertTrue(guard.restore(config(), http=fake, token='synthetic-token', now=NOW,
                                      state_dir=self.state)['restored'])
        self.run_guard(fake, now=NOW + timedelta(hours=1))
        self.assertEqual(guard.read_receipt(self.state)['stop_propagation']['not_before'],
                         '2026-10-08T13:15:00Z')


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
        self.assertFalse(self.gate()[0])

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

    def test_unmapped_service_with_usage_stops_and_names_it(self):
        fake = FakeCloudflare([row(service='Queues Standard Operations', consumed=10)])
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'STOP')
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
        self.assertEqual(first[1], {'script': 'carr-mcp-staging', 'status': 'confirmed',
                                    'prior': {'enabled': True, 'previews_enabled': False}})
        self.assertEqual(len([e for e in guard.read_history(self.state) if e['event'] == 'run']), 2)

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
        self.assertFalse(self.gate()[0], 'restore must not open the gate before a fresh evaluation')
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
        quarantined = receipt['quarantined_file']
        self.assertFalse(guard.restore(config(), http=FakeCloudflare(), token='synthetic-token', now=NOW,
                                       state_dir=self.state)['restored'])
        result = guard.restore(config(), http=FakeCloudflare(), token='synthetic-token', now=NOW, state_dir=self.state,
                               ack_quarantine=quarantined)
        self.assertTrue(result['restored'])
        self.run_guard(FakeCloudflare([row()]))
        self.assertTrue(self.gate()[0])

    def test_receipt_write_is_atomic_and_leaves_no_temp_files(self):
        self.run_guard(FakeCloudflare([row()]))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ['guard.lock', 'history.jsonl', 'receipt.json'])

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


# ── round 2 ──────────────────────────────────────────────────────────────────

def call_main(argv, state, *, http=None, token='synthetic-token', now=NOW, isatty=True, typed=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = guard.main(argv, http=http, token_reader=lambda cfg: token, now=now, state_dir=state,
                          cfg=config(), isatty=lambda: isatty, prompt=lambda text: typed,
                          spawn_reporter=lambda: None)
    return code, out.getvalue(), err.getvalue()


def files_text(folder):
    return ''.join(p.read_text(errors='replace') for p in Path(folder).rglob('*') if p.is_file())


class TokenNeverLeaks(GuardCase):
    def test_clean_token_strips_whitespace_and_refuses_non_printable(self):
        self.assertEqual(guard.clean_token('synthetic-token\n'), 'synthetic-token')
        self.assertEqual(guard.clean_token('  synthetic-token \r\n'), 'synthetic-token')
        for bad in ('syn thetic', 'tok\x00en', 'tök', '', '\n', None, 42):
            self.assertIsNone(guard.clean_token(bad), bad)

    def test_read_token_cleans_what_tokens_env_holds(self):
        fake = type(sys)('credential_env')
        fake.TokensFilePermissionError = type('TokensFilePermissionError', (RuntimeError,), {})
        for raw, expected in (('synthetic-token\n', 'synthetic-token'), ('bad\x07token', None)):
            fake.load_carr_tokens = lambda names, raw=raw: {names[0]: raw}
            with mock.patch.dict(sys.modules, {'credential_env': fake}):
                self.assertEqual(guard.read_token(config()), expected)

    def test_trailing_newline_token_never_appears_in_output_or_files(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.fail[('POST', '/workers/scripts/carr-mcp-staging/subdomain')] = RuntimeError('Bearer synthetic-token')
        code, out, err = call_main(['run'], self.state, http=fake, token='synthetic-token\n')
        self.assertEqual(code, 4)
        for text in (out, err, files_text(self.state)):
            self.assertNotIn('synthetic-token', text)

    def test_unexpected_error_text_is_reduced_to_its_type_name(self):
        fake = FakeCloudflare()
        fake.fail[('GET', '/billable-usage')] = ValueError('Bearer synthetic-token leaked in a message')
        result = self.run_guard(fake)
        self.assertEqual(result['verdict'], 'UNKNOWN')
        self.assertIn('ValueError', ' '.join(result['reasons']))
        self.assertNotIn('synthetic-token', files_text(self.state))

    def test_cloudflare_error_detail_is_redacted(self):
        fake = FakeCloudflare()
        fake.fail[('GET', '/billable-usage')] = (400, {'success': False, 'errors': [
            {'code': 9106, 'message': 'bad header Authorization: Bearer synthetic-token'}]})
        self.run_guard(fake)
        text = files_text(self.state)
        self.assertIn('9106', text)
        self.assertNotIn('synthetic-token', text)

    def test_redact_scrubs_bearer_values_at_any_depth(self):
        self.assertEqual(guard.redact({'a': ['x Bearer abc.def-123 y', 3], 'b': 'Bearer\tq'}),
                         {'a': ['x Bearer [redacted] y', 3], 'b': 'Bearer [redacted]'})

    def test_action_error_of_an_unexpected_type_is_named_not_quoted(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.fail[('POST', '/workers/scripts/carr-mcp-staging/subdomain')] = RuntimeError('Bearer synthetic-token')
        result = self.run_guard(fake)
        self.assertTrue(any('RuntimeError' in e for e in result['action_errors']))
        self.assertNotIn('synthetic-token', json.dumps(result))


class RestoreHoldsTheGate(GuardCase):
    def stop(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        self.run_guard(fake)
        return fake

    def test_restore_holds_until_a_fresh_non_stop_run(self):
        fake = self.stop()
        guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        allowed, reason = self.gate()
        self.assertFalse(allowed)
        self.assertIn('since', reason)
        self.run_guard(FakeCloudflare([row()]))
        self.assertTrue(self.gate()[0])

    def test_restore_cli_refuses_without_a_terminal(self):
        fake = self.stop()
        fake.calls.clear()
        code, _, err = call_main(['restore'], self.state, http=fake, isatty=False)
        self.assertNotEqual(code, 0)
        self.assertIn('terminal', err)
        self.assertEqual(fake.posts(), [])

    def test_restore_cli_needs_the_typed_phrase(self):
        fake = self.stop()
        fake.calls.clear()
        code, _, _ = call_main(['restore'], self.state, http=fake, typed='yes')
        self.assertNotEqual(code, 0)
        self.assertEqual(fake.posts(), [])
        code, out, _ = call_main(['restore'], self.state, http=fake, typed=guard.RESTORE_PHRASE)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.posts()), 2)

    def test_receipt_corrupt_hold_needs_the_quarantine_acknowledged(self):
        self.stop()
        guard.receipt_path(self.state).write_text('{torn')
        self.run_guard(FakeCloudflare([row()]))
        name = guard.read_receipt(self.state)['quarantined_file']
        code, out, err = call_main(['restore'], self.state, http=FakeCloudflare(), typed=guard.RESTORE_PHRASE)
        self.assertNotEqual(code, 0)
        self.assertIn(name, out + err)
        code, out, _ = call_main(['restore', '--ack-quarantine', name], self.state, http=FakeCloudflare(),
                                 typed=guard.RESTORE_PHRASE)
        self.assertEqual(code, 0)
        self.assertIn(name, out)


class WriteAhead(GuardCase):
    def test_prior_state_is_on_disk_and_pending_before_each_post(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        seen = []

        def check(script):
            entries = {w['script']: w for w in guard.read_receipt(self.state)['disabled_workers']}
            seen.append((script, entries[script]['status'], guard.read_receipt(self.state)['hold']))
        fake.on_post = check
        self.run_guard(fake)
        self.assertEqual(seen, [(s, 'pending', 'spend') for s in STAGING])
        statuses = [w['status'] for w in guard.read_receipt(self.state)['disabled_workers']]
        self.assertEqual(statuses, ['confirmed', 'confirmed'])

    def test_enospc_on_the_write_ahead_skips_the_post(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        real = guard._write_receipt

        def full_disk(state_dir, receipt, event):
            if event.get('event') == 'disable-pending':
                raise OSError(errno.ENOSPC, 'No space left on device')
            return real(state_dir, receipt, event)
        with mock.patch.object(guard, '_write_receipt', full_disk):
            result = self.run_guard(fake)
        self.assertEqual(fake.posts(), [])
        self.assertTrue(result['action_errors'])
        self.assertTrue(all('ENOSPC' in e for e in result['action_errors']))
        receipt = guard.read_receipt(self.state)
        self.assertEqual((receipt['hold'], receipt['disabled_workers']), ('spend', []))

    def test_unverifiable_read_back_is_still_recorded(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        fake.ignore_posts = True
        result = self.run_guard(fake)
        entries = guard.read_receipt(self.state)['disabled_workers']
        self.assertEqual([(w['script'], w['status']) for w in entries], [(s, 'unverified') for s in STAGING])
        self.assertEqual(len(result['action_errors']), 2)

    def test_read_back_that_errors_is_still_recorded(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        posted = set()
        real = fake.__call__

        def flaky(method, url, headers, body, timeout):
            script = url.rsplit('/', 2)[-2] if url.endswith('/subdomain') else None
            if method == 'GET' and script in posted:
                raise TimeoutError()
            if method == 'POST':
                posted.add(script)
            return real(method, url, headers, body, timeout)
        result = guard.run(config(), http=flaky, token='synthetic-token', now=NOW, state_dir=self.state,
                           spawn_reporter=lambda: None)
        entries = guard.read_receipt(self.state)['disabled_workers']
        self.assertEqual([w['status'] for w in entries], ['unverified', 'unverified'])
        self.assertTrue(result['action_errors'])


class Concurrency(GuardCase):
    def test_b_reads_a_stops_b_writes_cannot_lose_the_stop(self):
        reached, release = threading.Event(), threading.Event()
        slow = FakeCloudflare([row()])
        real = slow.__call__

        def blocking(method, url, headers, body, timeout):
            if url.endswith('/billable-usage'):
                reached.set()
                release.wait(5)
            return real(method, url, headers, body, timeout)
        b = threading.Thread(target=lambda: guard.run(config(), http=blocking, token='synthetic-token', now=NOW,
                                                      state_dir=self.state, spawn_reporter=lambda: None))
        b.start()
        self.assertTrue(reached.wait(5))
        a = threading.Thread(target=lambda: self.run_guard(FakeCloudflare([row(cost=1, pricing=1)])))
        a.start()
        time.sleep(0.3)
        release.set()
        b.join(5)
        a.join(5)
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['hold'], 'spend')
        self.assertEqual([w['script'] for w in receipt['disabled_workers']], STAGING)


class PartialResponses(GuardCase):
    def test_every_read_exception_is_unknown_with_only_its_type(self):
        for exc in (http.client.IncompleteRead(b'partial Bearer synthetic-token'), http.client.HTTPException('x'),
                    ConnectionResetError(errno.ECONNRESET, 'reset'), RuntimeError('boom'), MemoryError()):
            with self.subTest(exc=type(exc).__name__):
                self.tearDown(); self.setUp()
                fake = FakeCloudflare()
                fake.fail[('GET', '/billable-usage')] = exc
                result = self.run_guard(fake)
                self.assertEqual(result['verdict'], 'UNKNOWN')
                self.assertIn(type(exc).__name__, ' '.join(result['reasons']))
                self.assertNotIn('synthetic-token', files_text(self.state))

    def test_main_crash_exits_three_not_one(self):
        out, err = io.StringIO(), io.StringIO()

        def explode(cfg):
            raise RuntimeError('Bearer synthetic-token')
        with redirect_stdout(out), redirect_stderr(err):
            code = guard.main(['run'], http=FakeCloudflare(), token_reader=explode, now=NOW, state_dir=self.state,
                              cfg=config(), spawn_reporter=lambda: None)
        self.assertEqual(code, 3)
        self.assertIn('RuntimeError', err.getvalue())
        self.assertNotIn('synthetic-token', out.getvalue() + err.getvalue())


class ClockAndData(GuardCase):
    def test_gate_holds_when_the_evaluation_is_from_the_future(self):
        self.run_guard(FakeCloudflare([row()]), now=NOW + timedelta(minutes=10))
        allowed, reason = self.gate(now=NOW)
        self.assertFalse(allowed)
        self.assertIn('clock', reason)
        self.assertTrue(self.gate(now=NOW + timedelta(minutes=6))[0])

    def test_gate_holds_when_the_data_behind_a_clear_is_older_than_54h(self):
        self.run_guard(FakeCloudflare([row(day=6)]))  # data through 2026-10-07T00:00, 36h old
        self.assertTrue(self.gate(now=NOW + timedelta(hours=17))[0])
        allowed, reason = self.gate(now=NOW + timedelta(hours=19))
        self.assertFalse(allowed)
        self.assertIn('data', reason)

    def test_cost_is_the_largest_of_the_cost_columns(self):
        r = dict(row(cost=0.0), EffectiveCost=0.5, ListCost=0.75)
        result = self.run_guard(FakeCloudflare([r]))
        self.assertEqual(result['verdict'], 'STOP')
        self.assertIn('$0.75', ' '.join(result['reasons']))

    def test_a_row_without_any_cost_column_is_unknown(self):
        r = row()
        for key in ('BilledCost', 'ContractedCost'):
            del r[key]
        self.assertEqual(self.run_guard(FakeCloudflare([r]))['verdict'], 'UNKNOWN')


class RestoreSafety(GuardCase):
    def test_restore_skips_a_worker_the_guard_did_not_leave_off(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        self.run_guard(fake)
        fake.subdomains['carr-mcp-staging'] = dict(ON)  # redeployed by hand since the stop
        fake.calls.clear()
        result = guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        self.assertTrue(result['restored'])
        self.assertEqual([s for s, _ in fake.posts()], ['doctorcre-app-staging'])
        self.assertTrue(any('carr-mcp-staging' in n and 'skipped' in n for n in result['notes']))

    def test_restore_reads_current_state_before_posting(self):
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        self.run_guard(fake)
        fake.calls.clear()
        guard.restore(config(), http=fake, token='synthetic-token', now=NOW, state_dir=self.state)
        first = [(m, p.split('/')[3]) for m, p, _ in fake.calls[:3]]
        self.assertEqual(first, [('GET', 'doctorcre-app-staging'), ('POST', 'doctorcre-app-staging'),
                                 ('GET', 'doctorcre-app-staging')])


class LowItems(GuardCase):
    def test_redirects_are_refused(self):
        request = urllib.request.Request('https://api.cloudflare.com/client/v4/x')
        with self.assertRaises(guard.ApiError):
            guard._RefuseRedirect().redirect_request(request, None, 302, 'Found', {},
                                                     'https://api.cloudflare.com/client/v4/moved')
        self.assertTrue(any(isinstance(h, guard._RefuseRedirect) for h in guard._OPENER.handlers))

    def test_quarantine_names_never_collide(self):
        for _ in range(2):
            self.run_guard(FakeCloudflare([row()]))
            guard.receipt_path(self.state).write_text('{torn')
            self.run_guard(FakeCloudflare([row()]))
        self.assertEqual(len(list(self.state.glob('receipt.corrupt.*.json'))), 2)

    def test_exit_codes_tell_disabled_from_failed(self):
        self.assertEqual(call_main(['run'], self.state, http=FakeCloudflare([row(cost=1, pricing=1)]))[0], 2)
        self.tearDown(); self.setUp()
        fake = FakeCloudflare([row(cost=1, pricing=1)])
        for script in STAGING:
            fake.fail[('POST', f'/workers/scripts/{script}/subdomain')] = (403, {'success': False})
        code, _, err = call_main(['run'], self.state, http=fake)
        self.assertEqual(code, 4)
        self.assertIn('ACTION FAILED', err)
        self.assertIn('deprecated', err)
        self.assertEqual(len(set(guard.EXIT_CODES.values())), len(guard.EXIT_CODES))

    def test_map_services_lists_names_and_writes_nothing(self):
        fake = FakeCloudflare([row(), row(service='Queues Standard Operations', consumed=10)])
        lines = guard.map_services(config(), http=fake, token='synthetic-token', now=NOW)
        text = '\n'.join(lines)
        self.assertIn('Workers Standard', text)
        self.assertIn('UNMAPPED', text)
        self.assertEqual(fake.worker_calls(), [])
        self.assertFalse(self.state.exists())


class Activation(GuardCase):
    def test_check_e2e_without_a_token_holds_with_a_clear_message(self):
        code, out, _ = call_main(['check-e2e'], self.state, http=FakeCloudflare(), token=None)
        self.assertEqual(code, 1)
        self.assertIn('CLOUDFLARE_SPEND_GUARD_TOKEN not configured', out)

    def test_check_e2e_with_a_token_reads_fresh_billable_usage(self):
        fake = FakeCloudflare([row()])
        code, out, _ = call_main(['check-e2e'], self.state, http=fake)
        self.assertEqual(code, 0)
        self.assertIn(('GET', '/billable-usage', None), fake.calls)

    def test_health_row_before_the_guard_has_run_is_soft(self):
        line, failed, not_run = guard.health_row(config(), state_dir=self.state)
        self.assertTrue(failed and not_run)
        self.assertIn('on breach', line)

    def test_health_check_carries_the_row_and_a_provisioning_entry(self):
        source = (Path(__file__).with_name('health-check.py')).read_text()
        self.assertIn('"cloudflare_spend"', source)
        self.assertIn('health_row', source)


# ── item 11: the GraphQL fast path ───────────────────────────────────────────

def mtd(script, requests, cpu_p99_us=1000, subrequests=0, hour='2026-10-05T10:00:00Z'):
    return {'sum': {'requests': requests, 'subrequests': subrequests, 'errors': 0},
            'quantiles': {'cpuTimeP99': cpu_p99_us}, 'dimensions': {'scriptName': script, 'datetimeHour': hour}}


def ops(action, n):
    return {'sum': {'requests': n}, 'dimensions': {'actionType': action}}


def window(script, requests, errors=0, status='success'):
    return {'sum': {'requests': requests, 'errors': errors},
            'dimensions': {'scriptName': script, 'status': status, 'datetime': '2026-10-08T12:00:00Z'}}


def observed(*rows):
    scripts = {r['dimensions']['scriptName'] for r in rows}
    return list(rows) + [window(s, 1) for s in STAGING if s not in scripts]


def history(script, requests):
    return {'sum': {'requests': requests}, 'dimensions': {'scriptName': script}}


class FastCase(GuardCase):
    def setUp(self):
        super().setUp()
        self.beat(NOW)

    def beat(self, moment):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'loop-watch.json').write_text(json.dumps(
            {'schema': 'carr-loop-watch-state.v1', 'heartbeat': moment.timestamp()}))

    def fast(self, fake, now=NOW, cfg=None):
        return guard.fast(cfg or config(), http=fake, token='synthetic-token', now=now, state_dir=self.state,
                          spawn_reporter=lambda: self.spawned.append(1))


class FastPath(FastCase):
    def test_missing_workers_bucket_keeps_runaway_hold_and_restore_snapshot(self):
        fake = FakeCloudflare()
        fake.gql = {'cur': observed(window('carr-mcp-staging', 600, errors=400))}
        self.assertEqual(self.fast(fake)['verdict'], 'RUNAWAY')
        before = guard.read_receipt(self.state)['disabled_workers']
        fake.gql = {'cur': []}
        fake.calls.clear()
        self.assertEqual(self.fast(fake)['verdict'], 'UNKNOWN')
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['hold'], 'runaway')
        self.assertEqual(receipt['disabled_workers'], before)
        self.assertEqual(fake.worker_calls(), [])

    def test_cpu_quantiles_cannot_decide_cost_and_billing_reads_no_analytics(self):
        fake = FakeCloudflare()
        fake.gql = {'w0': [mtd('carr-mcp', 1000, cpu_p99_us=1_000_000_000)]}
        self.assertEqual(self.fast(fake)['verdict'], 'OK')
        self.assertNotIn('cpuTimeP99', fake.queries[0]['query'])
        bill = FakeCloudflare([row(cost=0.01)])
        self.assertEqual(self.run_guard(bill)['verdict'], 'STOP')
        self.assertEqual(bill.graphql_calls(), [])
        self.assertEqual([p for m, p, _ in bill.calls if p.startswith('/billable')],
                         ['/billable-usage/info', '/billable-usage'])

    def test_missing_or_late_buckets_are_unknown_and_preserve_a_stop(self):
        self.run_guard(FakeCloudflare([row(cost=1)]))
        before = guard.read_receipt(self.state)['disabled_workers']
        late = window('carr-mcp-staging', 10)
        late['dimensions']['datetime'] = '2026-10-08T11:00:00Z'
        for nodes in ({'cur': []}, {'w0': []}, {'cur': [late]}):
            with self.subTest(nodes=nodes):
                fake = FakeCloudflare()
                fake.gql = nodes
                self.assertEqual(self.fast(fake)['verdict'], 'UNKNOWN')
                receipt = guard.read_receipt(self.state)
                self.assertEqual(receipt['hold'], 'spend')
                self.assertEqual(receipt['disabled_workers'], before)
                self.assertFalse(self.gate()[0])
                self.assertEqual(fake.worker_calls(), [])

    def test_quiet_month_is_ok_in_one_graphql_call_with_no_worker_calls(self):
        fake = FakeCloudflare()
        fake.gql = {'w0': [mtd('carr-mcp', 1000)], 'kv0': [ops('read', 10)]}
        result = self.fast(fake)
        self.assertEqual(result['verdict'], 'OK')
        self.assertEqual(len(fake.graphql_calls()), 1)
        self.assertEqual(fake.worker_calls(), [])

    def test_query_stays_inside_the_documented_limits(self):
        fake = FakeCloudflare()
        self.fast(fake)
        query = fake.queries[0]['query']
        for part in ('workersInvocationsAdaptive', 'subrequests', 'kvOperationsAdaptiveGroups',
                     'r2OperationsAdaptiveGroups', 'durableObjectsInvocationsAdaptiveGroups', ACCOUNT):
            self.assertIn(part, query)
        self.assertEqual(query.count('accounts('), 1)
        self.assertTrue(all(int(n) <= 10000 for n in re.findall(r'limit:\s*(\d+)', query)))
        spans = re.findall(r'datetime_geq:\s*"([^"]+)",\s*datetime_leq:\s*"([^"]+)"', query)
        self.assertTrue(spans)
        for start, end in spans:
            delta = (datetime.fromisoformat(end.replace('Z', '+00:00'))
                     - datetime.fromisoformat(start.replace('Z', '+00:00')))
            self.assertLessEqual(delta, timedelta(days=7))

    def test_month_to_date_requests_at_eighty_percent_stop_staging(self):
        fake = FakeCloudflare()
        fake.gql = {'w0': [mtd('carr-mcp', 8_000_000)]}
        result = self.fast(fake)
        self.assertEqual(result['verdict'], 'STOP')
        self.assertEqual(sorted(s for s, _ in fake.posts()), sorted(STAGING))
        self.assertEqual(guard.read_receipt(self.state)['hold'], 'spend')

    def test_kv_r2_and_durable_object_counts(self):
        for alias, rows, key in (('kv0', [ops('write', 800_000)], 'kv-writes'),
                                 ('r20', [ops('PutObject', 500_000), ops('SomeNewAction', 300_000)], 'r2-class-a'),
                                 ('r20', [ops('GetObject', 8_000_000), ops('DeleteObject', 9_000_000)], 'r2-class-b'),
                                 ('do0', [{'sum': {'requests': 900_000}}], 'durable-objects-requests')):
            with self.subTest(key=key):
                self.tearDown(); self.setUp()
                fake = FakeCloudflare()
                fake.gql = {alias: rows}
                result = self.fast(fake)
                self.assertEqual(result['verdict'], 'STOP')
                self.assertIn(key, ' '.join(result['reasons']))

    def test_staging_burst_in_fifteen_minutes_stops(self):
        fake = FakeCloudflare()
        fake.gql = {'cur': [window('carr-mcp-staging', 15_000), window('doctorcre-app-staging', 5_001)],
                    'h1': [history('carr-mcp-staging', 15_000), history('doctorcre-app-staging', 5_001)]}
        for k in range(2, 8):
            fake.gql[f'h{k}'] = fake.gql['h1']
        result = self.fast(fake)
        self.assertEqual(result['verdict'], 'STOP')
        self.assertIn('burst', ' '.join(result['reasons']))

    def test_production_traffic_does_not_count_toward_the_staging_burst(self):
        fake = FakeCloudflare()
        fake.gql = {'cur': observed(window('carr-mcp', 30_000))}
        for k in range(1, 8):
            fake.gql[f'h{k}'] = [history('carr-mcp', 30_000)]
        self.assertEqual(self.fast(fake)['verdict'], 'OK')

    def test_graphql_errors_are_unknown_hold_e2e_and_change_nothing(self):
        self.run_guard(FakeCloudflare([row()]))
        self.assertTrue(self.gate()[0])
        fake = FakeCloudflare()
        fake.gql_fail = (200, {'data': None, 'errors': [{'message': 'Account has exceeded its rate limit',
                                                         'extensions': {'code': 'budget'}}]})
        result = self.fast(fake)
        self.assertEqual(result['verdict'], 'UNKNOWN')
        self.assertIn('budget', ' '.join(result['reasons']))
        self.assertEqual(fake.worker_calls(), [])
        self.assertFalse(self.gate()[0])
        clean = FakeCloudflare()
        self.assertEqual(self.fast(clean)['verdict'], 'OK')
        self.assertTrue(self.gate()[0])

    def test_writes_only_when_the_verdict_changes(self):
        self.fast(FakeCloudflare())
        before = guard.receipt_path(self.state).read_bytes(), len(guard.read_history(self.state))
        self.beat(NOW + timedelta(minutes=15))
        self.fast(FakeCloudflare(), now=NOW + timedelta(minutes=15))
        after = guard.receipt_path(self.state).read_bytes(), len(guard.read_history(self.state))
        self.assertEqual(before, after)

    def test_graphql_error_text_never_leaks_the_token(self):
        fake = FakeCloudflare()
        fake.gql_fail = (200, {'errors': [{'message': 'Bearer synthetic-token rejected'}]})
        self.fast(fake)
        self.assertNotIn('synthetic-token', files_text(self.state))

    def test_billing_period_comes_from_the_receipt_once_the_daily_run_cached_it(self):
        self.run_guard(FakeCloudflare([row()]))
        fake = FakeCloudflare()
        self.fast(fake)
        self.assertEqual([c for c in fake.calls if c[1] == '/billable-usage/info'], [])
        start = re.search(r'datetime_geq:\s*"([^"]+)"', fake.queries[0]['query']).group(1)
        self.assertTrue(start.startswith('2026-10-01T00:00'))

    def test_stale_loop_watch_heartbeat_warns_and_files_a_defect(self):
        self.beat(NOW - timedelta(minutes=6))
        result = self.fast(FakeCloudflare())
        self.assertEqual(result['verdict'], 'WARN')
        self.assertIn('loop-watch heartbeat', ' '.join(result['reasons']))
        items = [json.loads(p.read_text()) for p in (self.state / 'outbox').glob('*.json')]
        self.assertEqual([i['args']['defect_class'] for i in items], ['loop-watch-heartbeat-stale'])
        self.assertEqual(self.spawned, [1])


# ── item 12: runaway detector ────────────────────────────────────────────────

class Runaway(FastCase):
    def run_window(self, script, now_requests, history_requests, errors=0, days=7):
        fake = FakeCloudflare()
        fake.gql = {'cur': observed(window(script, now_requests, errors=errors))}
        for k in range(1, days + 1):
            fake.gql[f'h{k}'] = [history(script, history_requests)]
        return fake, self.fast(fake)

    def test_steady_baseline_does_not_fire(self):
        fake, result = self.run_window('carr-mcp-staging', 1_200, 1_000)
        self.assertEqual(result['runaways'], [])
        self.assertEqual(fake.posts(), [])

    def test_six_times_spike_above_the_floor_fires_and_disables_only_that_staging_worker(self):
        fake, result = self.run_window('carr-mcp-staging', 6_000, 1_000)
        self.assertEqual([r['script'] for r in result['runaways']], ['carr-mcp-staging'])
        self.assertEqual(result['verdict'], 'RUNAWAY')
        self.assertEqual(fake.posts(), [('carr-mcp-staging', OFF)])
        receipt = guard.read_receipt(self.state)
        self.assertEqual(receipt['hold'], 'runaway')
        self.assertEqual([w['script'] for w in receipt['disabled_workers']], ['carr-mcp-staging'])
        self.assertFalse(self.gate()[0])

    def test_spike_below_the_floor_does_not_fire(self):
        _, result = self.run_window('carr-mcp-staging', 600, 100)
        self.assertEqual(result['runaways'], [])

    def test_high_error_ratio_fires(self):
        fake, result = self.run_window('carr-mcp-staging', 600, 600, errors=400)
        self.assertEqual([r['script'] for r in result['runaways']], ['carr-mcp-staging'])
        self.assertIn('error', ' '.join(result['runaways'][0]['reasons']))

    def test_missing_history_uses_the_floor_alone_and_says_so(self):
        _, result = self.run_window('carr-mcp-staging', 1_500, 0, days=0)
        self.assertEqual(result['runaways'], [])
        self.assertIn('floor only', result['line'])
        self.tearDown(); self.setUp()
        _, result = self.run_window('carr-mcp-staging', 2_500, 0, days=2)
        self.assertEqual([r['script'] for r in result['runaways']], ['carr-mcp-staging'])
        self.assertIn('floor only', result['line'])

    def test_production_runaway_alerts_only(self):
        fake, result = self.run_window('carr-mcp', 6_000, 1_000)
        self.assertEqual([r['script'] for r in result['runaways']], ['carr-mcp'])
        self.assertEqual(fake.worker_calls(), [])
        self.assertIsNone(guard.read_receipt(self.state)['hold'])
        self.assertFalse(self.gate()[0])

    def test_runaway_receipt_and_alert_are_written_before_the_disable(self):
        fake = FakeCloudflare()
        fake.gql = {'cur': observed(window('carr-mcp-staging', 6_000))}
        for k in range(1, 8):
            fake.gql[f'h{k}'] = [history('carr-mcp-staging', 1_000)]
        seen = []
        fake.on_post = lambda script: seen.append([e['event'] for e in guard.read_history(self.state)])
        self.fast(fake)
        self.assertIn('runaway-alert', seen[0])


class FindingsReachTheRecord(FastCase):
    def test_a_new_stop_files_one_record_layer_report_and_a_repeat_files_none(self):
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        self.run_guard(FakeCloudflare([row(cost=1, pricing=1)]))
        items = self.outbox()
        self.assertEqual([i['args']['defect_class'] for i in items], ['cloudflare-spend-guard-stop'])
        self.assertIn('metered', items[0]['args']['actual'])
        self.assertEqual(self.spawned, [1])

    def test_a_runaway_alert_reaches_the_record_even_for_production(self):
        fake = FakeCloudflare()
        fake.gql = {'cur': observed(window('carr-mcp', 6_000))}
        for k in range(1, 8):
            fake.gql[f'h{k}'] = [history('carr-mcp', 1_000)]
        self.fast(fake)
        self.fast(fake, now=NOW + timedelta(seconds=1))
        classes = [i['args']['defect_class'] for i in self.outbox()]
        self.assertEqual(classes, ['cloudflare-runaway-worker'])

    def test_an_ok_run_files_nothing(self):
        self.run_guard(FakeCloudflare([row()]))
        self.fast(FakeCloudflare())
        self.assertEqual(self.outbox(), [])


class ConfigSections(unittest.TestCase):
    def test_committed_config_carries_the_fast_path_runaway_and_loop_watch_settings(self):
        cfg = guard.load_config()
        self.assertEqual(cfg['fast_path']['burst_requests_staging'], 20000)
        self.assertEqual(cfg['runaway']['floor_requests'], 2000)
        self.assertEqual(cfg['runaway']['spike_multiple'], 5)
        self.assertEqual(cfg['loop_watch']['max_kills_per_run'], 3)
        self.assertEqual(Path(cfg['state_dir']).expanduser(),
                         Path('~/.local/state/carr/cloudflare-spend-guard').expanduser())


if __name__ == '__main__':
    unittest.main()
