#!/usr/bin/env python3
"""Exercise persistent Neon admission and its secret-safe creation interface."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
from datetime import datetime, timezone
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.platform_metering import MeteringRefusal, authorize_metered_execution

POLICY = json.loads((ROOT / 'ops/config/platform-metering.v1.json').read_text())
REGISTRATION = {
    'name': 'engine-test', 'parent_name': 'production',
    'parent_id': 'br-summer-breeze-aw7jh2m5', 'project_id': 'steep-field-48688294',
    'org_id': 'org-dry-dew-75906281', 'init_source': 'schema-only',
    'owner': 'DoctorCRE engine',
    'endpoint': {'type': 'read_write', 'autoscaling_limit_min_cu': 0.25,
                 'autoscaling_limit_max_cu': 0.25, 'suspend_timeout_seconds': 300},
    'monthly_cost_ceiling_usd': 10,
}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.request = {'registration': copy.deepcopy(REGISTRATION),
                        'existing_registered_branches': 0, 'monthly_spend_usd': 1,
                        'organization_spending_limit_usd': 10, 'meter_month': '2026-11'}

    def admit(self):
        return authorize_metered_execution(POLICY, 'neon-persistent-branch', self.request,
                                           today=date(2026, 11, 11))

    def test_exact_engine_registration(self):
        self.assertTrue(self.admit()['admitted'])

    def test_every_registration_mismatch(self):
        for field, value in [('name', 'another'), ('parent_name', 'staging'),
                             ('parent_id', 'other'), ('project_id', 'other'),
                             ('org_id', 'other'), ('init_source', 'parent-data'),
                             ('owner', 'other'), ('monthly_cost_ceiling_usd', 11),
                             ('endpoint', {**REGISTRATION['endpoint'], 'autoscaling_limit_max_cu': 0.5}),
                             ('endpoint', {**REGISTRATION['endpoint'], 'suspend_timeout_seconds': 0})]:
            with self.subTest(field=field, value=value):
                self.setUp()
                self.request['registration'][field] = value
                with self.assertRaises(MeteringRefusal):
                    self.admit()

    def test_second_branch_and_invalid_budget_evidence(self):
        for field, value in [('existing_registered_branches', 1), ('monthly_spend_usd', 10),
                             ('monthly_spend_usd', 11), ('monthly_spend_usd', float('nan')),
                             ('monthly_spend_usd', float('inf')), ('monthly_spend_usd', -1),
                             ('monthly_spend_usd', True), ('monthly_spend_usd', None),
                             ('organization_spending_limit_usd', 11),
                             ('organization_spending_limit_usd', None), ('meter_month', '2026-10')]:
            with self.subTest(field=field, value=value):
                self.setUp()
                self.request[field] = value
                with self.assertRaises(MeteringRefusal):
                    self.admit()


spec = importlib.util.spec_from_file_location('persistent_neon', ROOT / 'ops/neon-persistent-branch.py')
assert spec is not None and spec.loader is not None
creator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(creator)


class CreatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.intent = Path(self.tmp.name) / 'intent.json'
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        self.costs = json.loads((ROOT / 'ops/config/system-costs.v1.json').read_text())['neon']
        self.calls = []
        self.branches = [{'id': REGISTRATION['parent_id'], 'name': 'production', 'default': True}]
        self.consumption = json.loads((ROOT / 'tools/fixtures/system-costs/neon.json').read_text())
        self.consumption['projects'][0]['project_id'] = REGISTRATION['project_id']
        frame = self.consumption['projects'][0]['periods'][0]['consumption'][0]
        frame.update(timeframe_start='2026-11-01T00:00:00Z', timeframe_end='2026-12-01T00:00:00Z')
        frame['metrics'] = [{'metric_name': name, 'value': 0} for name in self.costs['rates']]
        frame['metrics'][0]['value'] = 7200
        self.limit = 1000
        self.reply = {'branch': {'id': 'br-test', 'name': 'engine-test', 'init_source': 'schema-only'},
                      'endpoints': [{'id': 'ep-test', 'branch_id': 'br-test', **REGISTRATION['endpoint']}],
                      'connection_uris': [{'connection_uri': 'secret-response-must-not-escape'}]}

    def api(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == 'POST':
            self.assertTrue(self.intent.exists())
            self.assertEqual(payload, {'branch': {'name': 'engine-test',
                'parent_id': REGISTRATION['parent_id'], 'init_source': 'schema-only'},
                'endpoints': [REGISTRATION['endpoint']]})
            return self.reply
        if '/consumption_history/' in path:
            self.assertIn('granularity=monthly', path)
            return self.consumption
        if '/spending_limit' in path:
            return {'spending_limit_cents': self.limit}
        if path.endswith('/branches'):
            return {'branches': self.branches}
        return {'project': {'org_id': REGISTRATION['org_id']}}

    def create(self):
        return creator.create_registered_branch('engine-test', POLICY, self.costs,
                                                 self.api, self.intent, self.now)

    def assert_no_post(self):
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_create_once_and_secret_free_receipt(self):
        receipt = self.create()
        self.assertEqual(receipt['branch_id'], 'br-test')
        self.assertAlmostEqual(receipt['monthly_spend_usd'], 0.212)
        self.assertNotIn('secret-response', json.dumps(receipt))
        self.calls.clear()
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_existing_branch_refuses_without_intent(self):
        self.branches.append({'id': 'br-existing', 'name': 'engine-test'})
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_unknown_name_refuses_before_reads(self):
        with self.assertRaises(MeteringRefusal):
            creator.create_registered_branch('other', POLICY, self.costs, self.api, self.intent, self.now)
        self.assertEqual(self.calls, [])

    def test_missing_parent_refuses(self):
        self.branches.clear()
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_no_vendor_limit_refuses(self):
        self.limit = None
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_over_vendor_limit_refuses(self):
        self.limit = 1001
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_empty_or_stale_meter_refuses(self):
        for consumption in ({'projects': []},
                            json.loads((ROOT / 'tools/fixtures/system-costs/neon.json').read_text())):
            self.consumption = consumption
            with self.assertRaises(MeteringRefusal):
                self.create()
            self.assert_no_post()

    def test_meter_ceiling_refuses(self):
        self.consumption['projects'][0]['periods'][0]['consumption'][0]['metrics'][0]['value'] = 360000
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_monthly_projection_refuses_before_creation(self):
        self.now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.consumption['projects'][0]['periods'][0]['consumption'][0]['metrics'][0]['value'] = 36000
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()
        self.assertFalse(self.intent.exists())

    def test_target_missing_from_consumption_refuses(self):
        self.consumption['projects'][0]['project_id'] = 'other-project'
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_non_schema_result_refuses(self):
        self.reply['branch']['init_source'] = 'parent-data'
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assertTrue(self.intent.exists())

    def test_ambiguous_post_keeps_intent_and_refuses_retry(self):
        normal = self.api
        def failing(method, path, payload=None):
            if method == 'POST':
                self.assertTrue(self.intent.exists())
                raise TimeoutError('unknown remote effect')
            return normal(method, path, payload)
        self.api = failing
        with self.assertRaises(TimeoutError):
            self.create()
        with self.assertRaises(MeteringRefusal):
            self.create()

    def test_invalid_provider_result_keeps_intent(self):
        self.reply['endpoints'][0]['suspend_timeout_seconds'] = 0
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assertTrue(self.intent.exists())

    def test_read_error_stops_before_intent(self):
        def failing(*args):
            raise ConnectionError('provider unavailable')
        self.api = failing
        with self.assertRaises(ConnectionError):
            self.create()
        self.assertFalse(self.intent.exists())

    def test_cli_pass_and_refusal(self):
        import io
        for limit, expected in [(1000, 0), (None, 77)]:
            self.setUp()
            self.limit = limit
            output = io.StringIO()
            with patch.object(sys, 'argv', ['neon-persistent-branch.py', 'engine-test']), \
                    patch.dict('os.environ', {'NEON_API_KEY': 'synthetic-token'}), \
                    patch.object(creator, '_provider', return_value=self.api), \
                    patch.object(creator.Path, 'home', return_value=Path(self.tmp.name)), \
                    patch.object(creator, 'datetime') as clock, \
                    patch('sys.stdout', output), patch('sys.stderr', output):
                clock.now.return_value = self.now
                # The command owns the state directory; use the same intent in the fake provider.
                self.intent = Path(self.tmp.name) / '.cache/carr/neon-persistent-branch/creation-intent.json'
                self.assertEqual(creator.main(), expected)
            self.assertNotIn('secret-response', output.getvalue())
            self.assertNotIn('synthetic-token', output.getvalue())


if __name__ == '__main__':
    unittest.main()
