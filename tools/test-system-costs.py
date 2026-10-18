import importlib.util
import json
import unittest
import tempfile
import sys
import subprocess
from unittest.mock import patch
from datetime import date, datetime, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('system_costs', Path(__file__).with_name('system_costs.py'))
assert SPEC is not None and SPEC.loader is not None
costs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(costs)


class MonthlyCosts(unittest.TestCase):
    def test_ai_fixture_costs_use_vendor_money_units(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/system-costs/ai.json').read_text())
        self.assertEqual(costs.normalize('claude', fixture['claude'])['rows'][0]['usd'], 1.25)
        self.assertEqual(costs.normalize('openai', fixture['openai'])['rows'][0]['usd'], 2.5)

    def test_first_of_month_checks_last_complete_day_for_spike(self):
        config = {'budget_usd': 100, 'providers': {'jev': {'label': 'Jev', 'plan': 'usage', 'monthly_usd': 0}}}
        rows = [{'day': f'2026-09-{day:02}', 'usd': 1, 'driver': 'review'} for day in range(16, 30)]
        rows.append({'day': '2026-09-30', 'usd': 8, 'driver': 'review'})
        report = costs.summarize(config, {'jev': {'state': 'ready', 'rows': rows}}, date(2026, 9, 30), month='2026-10')
        self.assertEqual(report['alerts'][0]['kind'], 'daily_spike')
        self.assertEqual(report['providers'][0]['mtd_usd'], 0)

    def test_pending_add_recovers_lost_response_using_same_intent(self):
        report = {'state': 'ready', 'through': '2026-10-05', 'alerts': [
            {'provider': 'jev', 'driver': 'review', 'kind': 'daily_spike', 'amount_usd': 8, 'threshold_usd': 2}],
            'providers': [{'provider': 'jev', 'state': 'ready'}]}
        calls = []
        def interrupted(name, payload):
            calls.append((name, payload))
            raise TimeoutError('lost response')
        def recovered(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': 'loop-1', 'version': 1, 'status': 'open'}}
            calls.append((name, payload))
            return {'ok': True, 'replayed': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'loops.json'
            with self.assertRaises(TimeoutError):
                costs.reconcile(report, path, interrupted)
            costs.reconcile(report, path, recovered)
            self.assertEqual(calls[0], calls[1])
            self.assertIsNone(json.loads(path.read_text())['pending'])

    def test_old_jev_missing_receipt_does_not_poison_current_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jev.jsonl'
            path.write_text(json.dumps({'ts': '2026-01-01T00:00:00Z', 'ok': True, 'usage': {}}))
            self.assertEqual(costs.jev_usage(path, .042, start=date(2026, 9, 1))['state'], 'ready')

    def test_scalar_jev_receipt_is_partial_and_does_not_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jev.jsonl'
            path.write_text('null')
            self.assertEqual(costs.jev_usage(path, .042)['state'], 'partial')

    def test_malformed_nested_snapshot_is_unavailable(self):
        report = costs.summarize({'budget_usd': 100, 'providers': {
            'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}},
            {'github': {'state': 'ready', 'rows': []}}, date(2026, 10, 4), observed_at='2026-10-05T00:00:00Z')
        report['providers'][0]['daily'][0]['drivers'] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertEqual(costs.load_snapshot(path, datetime(2026, 10, 5, tzinfo=timezone.utc))['state'], 'unavailable')

    def test_billing_fixture_becomes_daily_cost_without_payment_details(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/system-costs/github.json').read_text())
        report = costs.summarize(
            {'budget_usd': 100, 'providers': {'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4, 'budget_usd': 10}}},
            {'github': costs.normalize('github', fixture)}, through=date(2026, 10, 4))
        github = report['providers'][0]
        self.assertAlmostEqual(github['mtd_usd'], 2.516129, places=6)
        day = next(row for row in github['daily'] if row['day'] == '2026-10-03')
        self.assertEqual(day['drivers']['Actions Linux'], 2)
        self.assertNotIn('payment_metadata', json.dumps(report))
        self.assertNotIn('do-not-persist', json.dumps(report))

    def test_neon_fixture_prices_all_project_timeframes(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/system-costs/neon.json').read_text())
        normalized = costs.normalize('neon', fixture, {'compute_unit_seconds': {'usd': .106, 'units': 3600}})
        self.assertEqual(normalized['rows'], [{'day': '2026-10-04', 'usd': .212, 'driver': 'compute_unit_seconds'}])

    def test_neon_extra_branch_meter_is_hours_and_egress_allowance_is_monthly(self):
        fixture = {'projects': [{'periods': [{'consumption': [
            {'timeframe_start': '2026-10-01T00:00:00Z', 'metrics': [
                {'metric_name': 'extra_branches_month', 'value': 24},
                {'metric_name': 'public_network_transfer_bytes', 'value': 90000000000}]},
            {'timeframe_start': '2026-10-02T00:00:00Z', 'metrics': [
                {'metric_name': 'public_network_transfer_bytes', 'value': 20000000000}]}]}]}]}
        normalized = costs.normalize('neon', fixture, {
            'extra_branches_month': {'usd': .002, 'units': 1},
            'public_network_transfer_bytes': {'usd': .1, 'units': 1000000000, 'included_units_per_month': 100000000000}})
        self.assertEqual([r['usd'] for r in normalized['rows']], [.048, 0, 1])

    def test_jev_only_counts_billed_receipts_and_uses_call_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in [
                {'ts': '2026-10-04T01:00:00Z', 'caller': 'rule_selector', 'usage': {'input_tokens': 1000000}, 'ok': True},
                {'ts': '2026-10-04T01:00:00Z', 'usage': {}, 'ok': False, 'http_status': 403},
                {'ts': '2026-10-04T02:00:00Z', 'cache_hit': True, 'usage': {'input_tokens': 1000000}, 'ok': True}]))
            source = costs.jev_usage(path, .042)
            self.assertEqual(source['state'], 'ready')
            self.assertEqual(source['rows'], [{'day': '2026-10-04', 'usd': .042, 'driver': 'rule_selector'}])

    def test_spikes_use_previous_fourteen_complete_days_and_budget(self):
        config = {'budget_usd': 100, 'providers': {'jev': {'label': 'Jev', 'plan': 'usage', 'monthly_usd': 0, 'budget_usd': 60}}}
        rows = [{'day': f'2026-09-{day:02}', 'usd': 1, 'driver': 'review'} for day in range(21, 31)]
        rows += [{'day': f'2026-10-{day:02}', 'usd': 1, 'driver': 'review'} for day in range(1, 5)]
        rows += [{'day': '2026-10-05', 'usd': 8, 'driver': 'rule-selector'}]
        report = costs.summarize(config, {'jev': {'state': 'ready', 'rows': rows}}, date(2026, 10, 5))
        self.assertEqual(report['providers'][0]['call_sites'], {'review': 4, 'rule-selector': 8})
        self.assertEqual([(a['provider'], a['kind'], a['threshold_usd']) for a in report['alerts']],
                         [('jev', 'daily_spike', 2), ('jev', 'budget_projection', 60)])
        self.assertEqual(report['providers'][0]['projection_usd'], 74.4)

    def test_snapshot_load_rejects_stale_and_drops_unknown_fields(self):
        config = {'budget_usd': 100, 'providers': {'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}}
        report = costs.summarize(config, {'github': {'state': 'ready', 'rows': []}}, date(2026, 10, 4),
                                 observed_at='2026-10-05T00:00:00Z')
        report['secret'] = 'never-print'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertNotIn('secret', costs.load_snapshot(path, datetime(2026, 10, 5, tzinfo=timezone.utc)))
            self.assertEqual(costs.load_snapshot(path, datetime(2026, 10, 7, tzinfo=timezone.utc))['state'], 'unavailable')

    def test_loop_reconciliation_deduplicates_and_reopens_after_clear(self):
        report = {'state': 'ready', 'through': '2026-10-05', 'alerts': [
            {'provider': 'jev', 'driver': 'review', 'kind': 'daily_spike', 'amount_usd': 8, 'threshold_usd': 2}],
            'providers': [{'provider': 'jev', 'state': 'ready'}]}
        writes = []
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': payload['loop_id'], 'version': 1, 'status': 'open'}}
            writes.append((name, payload))
            return {'ok': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'loops.json'
            costs.reconcile(report, state, verb)
            costs.reconcile(report, state, verb)
            self.assertEqual([w[0] for w in writes], ['add-loop'])
            report['alerts'] = []
            report['through'] = '2026-10-06'
            report['providers'][0]['state'] = 'partial'
            self.assertEqual(costs.reconcile(report, state, verb), 1)
            self.assertEqual([w[0] for w in writes], ['add-loop'])
            report['providers'][0]['state'] = 'ready'
            costs.reconcile(report, state, verb)
            self.assertEqual([w[0] for w in writes], ['add-loop', 'close-loop'])
            report['alerts'] = [{'provider': 'jev', 'driver': 'review', 'kind': 'daily_spike', 'amount_usd': 9, 'threshold_usd': 2}]
            costs.reconcile(report, state, verb)
            self.assertNotEqual(writes[0][1]['idempotency_key'], writes[-1][1]['idempotency_key'])

    def test_collector_paginates_neon_without_persisting_raw_response(self):
        config = {'budget_usd': 100, 'providers': {'neon': {'label': 'Neon', 'plan': 'Launch', 'monthly_usd': 0}},
                  'neon': {'org_id': 'fixture-org', 'rates': {'compute_unit_seconds': {'usd': .106, 'units': 3600}}}}
        urls = []
        def get(url, headers):
            urls.append(url)
            if 'cursor=' not in url:
                return {'projects': [], 'pagination': {'cursor': 'page-two'}}
            return json.loads((Path(__file__).parent / 'fixtures/system-costs/neon.json').read_text())
        report = costs.collect(config, {'NEON_API_KEY': 'fixture-credential'}, get,
                               now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(len(urls), 2)
        self.assertEqual(report['providers'][0]['mtd_usd'], .212)
        self.assertNotIn('fixture-credential', json.dumps(report))

    def test_neon_changed_plan_refuses_launch_price_estimate(self):
        config = {'budget_usd': 100, 'providers': {'neon': {'label': 'Neon', 'plan': 'Launch', 'monthly_usd': 0}},
                  'neon': {'org_id': 'fixture-org', 'plan': 'launch', 'rates': {}}}
        report = costs.collect(config, {'NEON_API_KEY': 'fixture-credential'},
            lambda *args: {'projects': [{'periods': [{'period_plan': 'scale', 'consumption': []}]}]},
            now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(report['providers'][0]['state'], 'unavailable')

    def test_missing_billing_token_is_named_and_never_zero_coverage(self):
        config = {'budget_usd': 100, 'providers': {'cloudflare': {'label': 'Cloudflare', 'plan': 'unconfirmed', 'monthly_usd': None}}}
        report = costs.collect(config, {}, lambda *a: self.fail('no token means no request'),
                               now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(report['state'], 'partial')
        self.assertIn('CLOUDFLARE_BILLING_READ_TOKEN', report['providers'][0]['reason'])

    def test_health_surface_prints_bound_response_for_unreadable_source(self):
        import subprocess
        result = subprocess.run([sys.executable,
                                 str(Path(__file__).with_name('health-check.py')), '--section', 'costs', '--fixture', '/dev/null'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn('UNKNOWN system costs', result.stdout)
        for text in ('deduplicated loop', 'owner orchestrator', 'remediation', 'verify', 'auto-clear'):
            self.assertIn(text, result.stdout)

    def test_unknown_provider_text_invalidates_snapshot(self):
        report = costs.summarize({'budget_usd': 100, 'providers': {
            'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}},
            {'github': {'state': 'ready', 'rows': []}}, date(2026, 10, 4), observed_at='2026-10-05T00:00:00Z')
        report['providers'][0]['reason'] = {'payment_metadata': 'never-persist'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertEqual(costs.load_snapshot(path, datetime(2026, 10, 5, tzinfo=timezone.utc))['state'], 'unavailable')


class ReviewRegressions(unittest.TestCase):
    def report(self, amount=8, through='2026-10-05', observed='2026-10-06T01:00:00Z'):
        return {'state': 'ready', 'through': through, 'observed_at': observed,
                'alerts': [] if amount is None else [{'provider': 'jev', 'driver': 'review',
                    'kind': 'daily_spike', 'amount_usd': amount, 'threshold_usd': 2}],
                'providers': [{'provider': 'jev', 'state': 'ready'}]}

    def config(self, provider, fixed=0):
        return {'budget_usd': 100, 'providers': {provider: {
            'label': provider, 'plan': 'fixture', 'monthly_usd': fixed}},
            'neon': {'org_id': 'fixture', 'rates': {}}, 'cloudflare_account_id': 'fixture'}

    def test_transport_refusal_redecides_instead_of_replaying_rejected_write(self):
        for refusal in ('version_conflict', 'loop_not_open'):
            with self.subTest(refusal=refusal), tempfile.TemporaryDirectory() as tmp:
                calls, writes = [], []
                remote = {'version': 1, 'status': 'open'}
                def transport(args, **kwargs):
                    name, payload = args[2], json.loads(args[3])
                    calls.append(name)
                    if name == 'read-loop':
                        answer = {'loop': {'loop_id': 'loop-1', **remote}}
                    else:
                        writes.append((name, payload))
                        if len(writes) == 2:
                            remote.update(version=2, status='closed' if refusal == 'loop_not_open' else 'open')
                            return subprocess.CompletedProcess(args, 1, '', 'local-verb identity\nTOOL ERROR ' +
                                json.dumps({'error': refusal}, indent=2) + '\n')
                        answer = {'ok': True, 'loop_id': 'loop-1'}
                    return subprocess.CompletedProcess(args, 0, json.dumps(answer), '')
                path = Path(tmp) / 'loops.json'
                with patch('lib.record_call.subprocess.run', side_effect=transport):
                    costs.reconcile(self.report(), path)
                    costs.reconcile(self.report(9), path)
                    costs.reconcile(self.report(9), path)
                self.assertIsNone(json.loads(path.read_text())['pending'])
                self.assertEqual(calls, ['add-loop', 'read-loop', 'update-loop', 'read-loop',
                                        'add-loop' if refusal == 'loop_not_open' else 'update-loop'])
                self.assertNotEqual(writes[1][1]['idempotency_key'], writes[2][1]['idempotency_key'])
                if refusal == 'version_conflict':
                    self.assertEqual(writes[2][1]['base_version'], 2)

    def test_transport_uncertain_error_preserves_exact_intent(self):
        for failure in ('service_unavailable', 'timeout', 'malformed'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                writes = []
                def transport(args, **kwargs):
                    if args[2] == 'read-loop':
                        return subprocess.CompletedProcess(args, 0, json.dumps({'loop': {
                            'loop_id': 'loop-1', 'status': 'open', 'version': 1}}), '')
                    writes.append((args[2], json.loads(args[3])))
                    if len(writes) == 1:
                        if failure == 'timeout':
                            raise subprocess.TimeoutExpired(args, 35)
                        stderr = ('TOOL ERROR {"error":"service_unavailable"}' if failure == 'service_unavailable'
                                  else 'TOOL ERROR not-json')
                        return subprocess.CompletedProcess(args, 1, '', stderr)
                    return subprocess.CompletedProcess(args, 0, '{"ok":true,"loop_id":"loop-1"}', '')
                path = Path(tmp) / 'loops.json'
                with patch('lib.record_call.subprocess.run', side_effect=transport):
                    with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                        costs.reconcile(self.report(), path)
                    self.assertIsNotNone(json.loads(path.read_text())['pending'])
                    costs.reconcile(self.report(), path)
                self.assertEqual(writes[0], writes[1])
                self.assertIsNone(json.loads(path.read_text())['pending'])

    def test_transport_nonzero_cannot_confirm_success_or_rpc_refusal(self):
        for stderr in ('TOOL ERROR {"ok":true}', 'RPC ERROR {"error":"version_conflict"}'):
            with self.subTest(stderr=stderr), patch('lib.record_call.subprocess.run', return_value=
                    subprocess.CompletedProcess([], 1, '{"ok":true}', stderr)):
                with self.assertRaises(RuntimeError):
                    costs.cost_verb('update-loop', {})

    def test_transport_tool_error_overrides_success_flag_and_ignores_guidance(self):
        result = subprocess.CompletedProcess([], 1, '',
            'local-verb identity\nTOOL ERROR {"ok":true,"error":"version_conflict"}\nhelp text\n')
        with patch('lib.record_call.subprocess.run', return_value=result):
            self.assertEqual(costs.cost_verb('update-loop', {}), {'ok': False, 'error': 'version_conflict'})

    def test_partial_jev_daily_warning_has_one_owner_and_cannot_clear(self):
        config = json.loads((costs.ROOT / 'ops/config/system-costs.v1.json').read_text())
        config['providers'] = {'jev': config['providers']['jev']}
        for spiking in (True, False):
            with self.subTest(spiking=spiking), tempfile.TemporaryDirectory() as tmp:
                local, factory = Path(tmp) / 'local.jsonl', Path(tmp) / 'factory.jsonl'
                local.write_text('')
                factory.write_text('\n'.join(json.dumps({'ts': f'2026-10-{day:02}T00:00:00Z',
                    'ok': True, 'caller': 'factory', 'usage': {'input_tokens':
                        30000000 if day == 15 else 1000000 if spiking else 30000000}})
                    for day in range(1, 16)))
                reader = costs.jev_sources
                def source(local, price, start, through):
                    return reader(local, price, start, through, extra_logs=(factory,))
                with patch.object(costs, 'jev_sources', side_effect=source):
                    report = costs.collect(config, {}, jev_path=local,
                        now=datetime(2026, 10, 16, tzinfo=timezone.utc))
                self.assertEqual(report['providers'][0]['state'], 'partial')
                warning = next((a for a in report['alerts'] if a['kind'] == 'daily_warning'), None)
                self.assertIsNotNone(warning)
                self.assertEqual(warning['amount_usd'], 1.26)
                self.assertEqual(warning['threshold_usd'], .5)
                writes = []
                def verb(name, payload):
                    if name == 'read-loop':
                        return {'loop': {'loop_id': 'loop-1', 'version': 1, 'status': 'open'}}
                    writes.append(name)
                    return {'ok': True, 'loop_id': 'loop-1'}
                path = Path(tmp) / 'loops.json'
                costs.reconcile(report, path, verb)
                costs.reconcile(report, path, verb)
                healthy = {**report, 'through': '2026-10-16', 'observed_at': '2026-10-17T00:00:00Z', 'alerts': []}
                costs.reconcile(healthy, path, verb)
                self.assertEqual(writes, ['add-loop'])
                self.assertIn('daily warning threshold', costs.ACTION)

    def test_3_action_keys_bind_operation_and_revision(self):
        manifests, writes = {}, []
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': 'loop-1', 'status': 'open', 'version': len(writes)}}
            key = payload['idempotency_key']
            manifest = (name, json.dumps(payload, sort_keys=True))
            if key in manifests and manifests[key] != manifest:
                return {'ok': False, 'error': 'key_reuse'}
            manifests[key] = manifest
            writes.append(name)
            return {'ok': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            for value in (8, 9, 8, 9):
                costs.reconcile(self.report(value), Path(tmp) / 'loops.json', verb)
        self.assertEqual(writes, ['add-loop', 'update-loop', 'update-loop', 'update-loop'])

    def test_4_confirmed_refusal_redecides_from_remote_state(self):
        for refusal in ('version_conflict', 'loop_not_open'):
            with self.subTest(refusal=refusal), tempfile.TemporaryDirectory() as tmp:
                writes, remote = [], {'version': 1, 'status': 'open'}
                def verb(name, payload):
                    if name == 'read-loop':
                        return {'loop': {'loop_id': 'loop-1', **remote}}
                    writes.append((name, dict(payload)))
                    if len(writes) == 2:
                        remote.update(version=2, status='closed' if refusal == 'loop_not_open' else 'open')
                        return {'ok': False, 'error': refusal}
                    return {'ok': True, 'loop_id': 'loop-1'}
                path = Path(tmp) / 'loops.json'
                costs.reconcile(self.report(), path, verb)
                costs.reconcile(self.report(9), path, verb)
                costs.reconcile(self.report(9), path, verb)
                self.assertIsNone(json.loads(path.read_text())['pending'])
                self.assertEqual(writes[-1][0], 'add-loop' if refusal == 'loop_not_open' else 'update-loop')
                if refusal == 'version_conflict':
                    self.assertEqual(writes[-1][1]['base_version'], 2)
                    self.assertNotEqual(writes[1][1]['idempotency_key'], writes[-1][1]['idempotency_key'])

    def test_5_report_highwater_survives_closure_and_orders_corrections(self):
        writes = []
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': 'loop-1', 'version': 1, 'status': 'open'}}
            writes.append(name)
            return {'ok': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'loops.json'
            costs.reconcile(self.report(), path, verb)
            costs.reconcile(self.report(None, '2026-10-06', '2026-10-07T01:00:00Z'), path, verb)
            costs.reconcile(self.report(), path, verb)
            costs.reconcile(self.report(8, '2026-10-06', '2026-10-07T00:00:00Z'), path, verb)
        self.assertEqual(writes, ['add-loop', 'close-loop'])

    def test_4_closed_remote_loop_clears_confirmed_refusal(self):
        writes, remote = [], {'version': 1, 'status': 'open'}
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': 'loop-1', **remote}}
            writes.append(name)
            if name == 'close-loop':
                remote['status'] = 'closed'
                return {'ok': False, 'error': 'loop_not_open'}
            return {'ok': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'loops.json'
            costs.reconcile(self.report(), path, verb)
            healthy = self.report(None, '2026-10-06', '2026-10-07T01:00:00Z')
            costs.reconcile(healthy, path, verb)
            self.assertEqual(costs.reconcile(healthy, path, verb), 0)
            self.assertEqual(json.loads(path.read_text())['refusals'], {})

    def test_4_uncertain_service_error_replays_exact_intent(self):
        writes = []
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': 'loop-1', 'version': 1, 'status': 'open'}}
            writes.append((name, dict(payload)))
            return {'error': 'service_unavailable'} if len(writes) == 1 else {'ok': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'loops.json'
            with self.assertRaises(RuntimeError):
                costs.reconcile(self.report(), path, verb)
            costs.reconcile(self.report(), path, verb)
            self.assertEqual(writes[0], writes[1])

    def test_7_factory_known_costs_and_partial_coverage_prevent_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            local, factory = Path(tmp) / 'local.jsonl', Path(tmp) / 'factory.jsonl'
            local.write_text('')
            factory.write_text(json.dumps({'ts': '2026-10-04T00:00:00Z', 'ok': True,
                                           'caller': 'factory', 'usage': {'input_tokens': 1000000}}))
            source = costs.jev_sources(local, .042, date(2026, 10, 1), date(2026, 10, 4), extra_logs=(factory, factory))
            self.assertEqual(source['rows'], [{'day': '2026-10-04', 'usd': .042, 'driver': 'factory'}])
            report = costs.summarize(self.config('jev'), {'jev': source}, date(2026, 10, 6))
            writes = []
            def verb(name, payload):
                if name == 'read-loop':
                    return {'loop': {'loop_id': 'loop-1', 'version': 1, 'status': 'open'}}
                writes.append(name)
                return {'ok': True, 'loop_id': 'loop-1'}
            path = Path(tmp) / 'loops.json'
            costs.reconcile(self.report(), path, verb)
            self.assertEqual(costs.reconcile(report, path, verb), 1)
            self.assertEqual(writes, ['add-loop'])

    def test_8_existing_jev_loop_is_migrated_without_another_add(self):
        writes = []
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': 'legacy-loop', 'version': 7, 'status': 'open'}}
            writes.append((name, dict(payload)))
            return {'ok': True}
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'legacy.json'
            legacy.write_text('{"loop_id":"legacy-loop","day":"2026-10-05"}')
            costs.reconcile(self.report(), Path(tmp) / 'loops.json', verb, legacy_path=legacy)
        self.assertEqual(writes[0][0], 'update-loop')
        self.assertEqual(writes[0][1]['loop_id'], 'legacy-loop')

    def test_6_atomic_writers_use_distinct_temporary_files(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        barrier, original = threading.Barrier(2), costs.os.replace
        paths = []
        def replace(source, target):
            paths.append(str(source))
            barrier.wait(timeout=5)
            original(source, target)
        with tempfile.TemporaryDirectory() as tmp, patch.object(costs.os, 'replace', side_effect=replace):
            target = Path(tmp) / 'snapshot.json'
            with ThreadPoolExecutor(2) as pool:
                results = [pool.submit(costs._save_state, target, {'writer': n}) for n in (1, 2)]
                for result in results:
                    result.result(timeout=10)
            self.assertIn(json.loads(target.read_text())['writer'], (1, 2))
        self.assertEqual(len(set(paths)), 2)

    def test_7_local_jev_evidence_cannot_claim_complete_provider_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            path.write_text('')
            report = costs.collect(self.config('jev'), {}, now=datetime(2026, 10, 5, tzinfo=timezone.utc), jev_path=path)
        self.assertEqual(report['providers'][0]['state'], 'partial')
        self.assertIn('Worker', report['providers'][0]['reason'])

    def test_8_legacy_spend_reader_does_not_own_a_second_response_loop(self):
        import jev_spend_health as spend
        writes = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            path.write_text(json.dumps({'ts': '2026-10-05T00:00:00Z', 'ok': True,
                                       'usage': {'input_tokens': 20000000}}))
            with patch.object(spend, '_run_verb', side_effect=lambda *args: writes.append(args)):
                line = spend.check_spend(path, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertIn('WARN', line)
        self.assertEqual(spend.ACTION, costs.ACTION)
        self.assertEqual(writes, [])
        nightly = (Path(__file__).parents[1] / 'bin/nightly.sh').read_text()
        self.assertFalse('step "Jev daily spend alarm"' in nightly)

    def test_9_credential_files_use_permission_checked_named_loader(self):
        import os
        from credential_env import TokensFilePermissionError
        for filename in ('tokens.env', 'db.env'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp) / '.config/carr'
                folder.mkdir(parents=True)
                token = folder / filename
                token.write_text('NEON_API_KEY=synthetic-value\n')
                token.chmod(0o644)
                with patch.object(costs.Path, 'home', return_value=Path(tmp)), patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(TokensFilePermissionError):
                        costs.read_tokens()

    def test_10_malformed_external_shapes_preserve_other_providers(self):
        cases = [('neon', {'projects': [], 'pagination': None}),
                 ('claude', {'data': [{'starting_at': '2026-10-04', 'results': [{'currency': None, 'amount': 1}]}]}),
                 ('openai', {'data': [None]}), ('openai', {'data': [{'start_time': None, 'results': None}]})]
        for provider, response in cases:
            with self.subTest(provider=provider, response=response):
                config = self.config(provider)
                config['providers']['github'] = self.config('github', 4)['providers']['github']
                report = costs.collect(config, {costs.TOKEN_NAMES[provider]: 'synthetic'}, lambda *a: response,
                                       now=datetime(2026, 10, 5, tzinfo=timezone.utc))
                self.assertIn(report['providers'][0]['state'], ('partial', 'unavailable'))
                self.assertGreater(report['providers'][1]['mtd_usd'], 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            path.write_text('{"ts":null,"ok":true,"usage":{}}')
            self.assertEqual(costs.jev_usage(path, .042)['state'], 'partial')

    def test_11_null_known_amount_survives_health_cli(self):
        import subprocess
        now = datetime.now(timezone.utc)
        report = costs.summarize(self.config('github'), {'github': {'state': 'ready', 'rows': []}},
                                 now.date(), observed_at=now.isoformat())
        report['state'] = 'partial'
        report['providers'][0].update(state='unavailable', mtd_usd=None, projection_usd=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertEqual(costs.load_snapshot(path)['providers'][0]['mtd_usd'], None)
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('health-check.py')),
                                     '--section', 'costs', '--fixture', str(path)], capture_output=True, text=True, timeout=15)
        self.assertIn('known MTD $0.00', result.stdout)
        self.assertIn('partial coverage', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_12_neon_daily_request_never_exceeds_retention(self):
        import urllib.parse
        def fetch(url, headers):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            start = datetime.fromisoformat(query['from'][0].replace('Z', '+00:00'))
            end = datetime.fromisoformat(query['to'][0].replace('Z', '+00:00'))
            self.assertLessEqual((end - start).days, 60)
            return {'projects': []}
        report = costs.collect(self.config('neon'), {'NEON_API_KEY': 'synthetic'}, fetch,
                               now=datetime(2026, 8, 31, tzinfo=timezone.utc))
        self.assertEqual(report['providers'][0]['state'], 'partial')
        self.assertIn('retention', report['providers'][0]['reason'])

    def test_13_invoices_are_not_daily_usage_or_duplicate_fixed_fees(self):
        for invoiced, fixed, expected in ((50, 0, 50), (5, 5, 5)):
            with self.subTest(invoiced=invoiced):
                response = {'success': True, 'result': [{'currency': 'usd', 'type': 'invoice',
                    'action': 'charge', 'amount': invoiced, 'occurred_at': '2026-10-01'}]}
                report = costs.collect(self.config('cloudflare', fixed), {'CLOUDFLARE_BILLING_READ_TOKEN': 'synthetic'},
                                       lambda *a: response, now=datetime(2026, 10, 2, tzinfo=timezone.utc))
                self.assertEqual(report['providers'][0]['projection_usd'], expected)
                self.assertEqual(report['alerts'], [])

    def test_15_fixed_fee_with_missing_usage_is_partial_known_cost(self):
        report = costs.collect(self.config('github', 4), {}, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        provider = report['providers'][0]
        self.assertEqual(provider['state'], 'partial')
        self.assertEqual(provider['projection_usd'], 4)
        self.assertGreater(provider['mtd_usd'], 0)


if __name__ == '__main__':
    unittest.main()
