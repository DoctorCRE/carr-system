#!/usr/bin/env python3
"""Exercise persistent Neon admission and its secret-safe creation interface."""
from __future__ import annotations

import copy
import io
import os
import shutil
import subprocess
import urllib.error
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
SOURCE_URI = 'postgresql://' + 'source:pw@127.0.0.1:1/source?sslmode=require'
DESTINATION_URI = 'postgresql://' + 'destination:pw@127.0.0.1:1/destination?sslmode=require'

REGISTRATION = {
    'name': 'engine-test',
    'source': {'branch_name': 'production', 'branch_id': 'br-summer-breeze-aw7jh2m5',
               'project_id': 'steep-field-48688294', 'database_name': 'neondb',
               'role_name': 'neondb_owner'},
    'destination': {'database_name': 'neondb', 'role_name': 'neondb_owner'},
    'initialization': {'dump_path': '/Users/booko/engine-facts/full-schema.sql'},
    'org_id': 'org-dry-dew-75906281', 'owner': 'DoctorCRE engine',
    'endpoint': {'type': 'read_write', 'autoscaling_limit_min_cu': 0.25,
                 'autoscaling_limit_max_cu': 0.25, 'suspend_timeout_seconds': 300},
    'monthly_cost_ceiling_usd': 40,
}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.request = {'registration': copy.deepcopy(REGISTRATION),
                        'existing_registered_projects': 0, 'monthly_projection_usd': 1,
                        'organization_spending_limit_usd': 40, 'meter_month': '2026-11'}

    def admit(self):
        return authorize_metered_execution(POLICY, 'neon-persistent-branch', self.request,
                                           today=date(2026, 11, 11))

    def test_exact_engine_registration(self):
        self.assertTrue(self.admit()['admitted'])

    def test_every_registration_mismatch(self):
        def mutations(value):
            for field, child in value.items():
                if isinstance(child, dict):
                    for replacement in mutations(child):
                        yield {**value, field: replacement}
                else:
                    yield {**value, field: 'unregistered'}
        for registration in mutations(REGISTRATION):
            self.request['registration'] = registration
            with self.assertRaises(MeteringRefusal):
                self.admit()

    def test_old_same_project_registration_is_refused(self):
        self.request['registration'] = {**REGISTRATION, 'init_source': 'schema-only',
            'parent_id': REGISTRATION['source']['branch_id']}
        with self.assertRaises(MeteringRefusal):
            self.admit()

    def test_second_branch_and_invalid_budget_evidence(self):
        for field, value in [('existing_registered_projects', 1), ('monthly_projection_usd', 40),
                             ('monthly_projection_usd', 41), ('monthly_projection_usd', float('nan')),
                             ('monthly_projection_usd', float('inf')), ('monthly_projection_usd', -1),
                             ('monthly_projection_usd', True), ('monthly_projection_usd', None),
                             ('organization_spending_limit_usd', 41),
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
VALIDATE_DUMP_PATH = creator.validate_dump_path


class CreatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.intent = Path(self.tmp.name) / 'intent.json'
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        self.costs = json.loads((ROOT / 'ops/config/system-costs.v1.json').read_text())
        self.dump_path = Path(self.tmp.name) / 'schema.sql'
        # Exercise real path checks under the test directory, including CLI tests.
        probe = patch.object(creator, 'validate_dump_path',
                             side_effect=lambda path: VALIDATE_DUMP_PATH(self.dump_path))
        probe.start()
        self.addCleanup(probe.stop)
        self.calls = []
        self.branches = [{'id': REGISTRATION['source']['branch_id'], 'name': 'production', 'default': True}]
        self.consumption = json.loads((ROOT / 'tools/fixtures/system-costs/neon.json').read_text())
        self.consumption['projects'][0]['project_id'] = REGISTRATION['source']['project_id']
        frame = self.consumption['projects'][0]['periods'][0]['consumption'][0]
        frame.update(timeframe_start='2026-11-01T00:00:00Z', timeframe_end='2026-12-01T00:00:00Z')
        frame['metrics'] = [{'metric_name': name, 'value': 0} for name in self.costs['neon']['rates']]
        frame['metrics'][0]['value'] = 7200
        self.limit = 4000
        self.projects = []
        self.initializations = []
        # Neon v2 Endpoint example and required fields; synthetic IDs and host.
        self.reply = {'project': {'id': 'fresh-project', 'name': 'engine-test', 'org_id': REGISTRATION['org_id']}, 'branch': {'id': 'br-test', 'name': 'engine-test', 'project_id': 'fresh-project', 'default': True, 'parent_id': None},
                      'endpoints': [{'id': 'ep-test', 'branch_id': 'br-test',
                                    'project_id': 'fresh-project', 'host': 'ep-test.example.invalid',
                                    'region_id': 'aws-us-east-1', 'type': 'read_write',
                                    'autoscaling_limit_min_cu': 0.25, 'autoscaling_limit_max_cu': 0.25,
                                    'suspend_timeout_seconds': 0, 'current_state': 'init',
                                    'settings': {'pg_settings': {}}, 'pooler_enabled': False,
                                    'pooler_mode': 'transaction', 'disabled': False,
                                    'passwordless_access': False, 'creation_source': 'api',
                                    'created_at': '2026-11-11T00:00:00Z',
                                    'updated_at': '2026-11-11T00:00:00Z',
                                    'proxy_host': 'example.invalid', 'provisioner': 'k8s-neonvm'}],
                      'connection_uris': [{'connection_uri': 'secret-response-must-not-escape'}]}

    def api(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == 'POST':
            self.assertTrue(self.intent.exists())
            self.assertEqual(path, '/projects')
            self.assertEqual(payload['project']['org_id'], REGISTRATION['org_id'])
            self.assertEqual(payload['project']['branch'], {'name': 'engine-test', **REGISTRATION['destination']})
            self.assertEqual(payload['project']['default_endpoint_settings'],
                             {k: (0 if k == 'suspend_timeout_seconds' else v)
                              for k, v in REGISTRATION['endpoint'].items() if k != 'type'})
            return self.reply
        if path.startswith('/projects?'):
            return {'projects': self.projects}
        if '/connection_uri?' in path:
            return {'uri': SOURCE_URI if REGISTRATION['source']['project_id'] in path else DESTINATION_URI}
        if '/consumption_history/' in path:
            self.assertIn('granularity=monthly', path)
            return self.consumption
        if '/spending_limit' in path:
            return {'spending_limit_cents': self.limit}
        if path.endswith('/branches'):
            return {'branches': self.branches}
        return {'project': {'org_id': REGISTRATION['org_id'], 'region_id': 'aws-us-east-1', 'pg_version': 17}}

    def create(self):
        return creator.create_registered_project('engine-test', POLICY, self.costs,
                                                 self.api, self.intent, self.now, initialize=self.initialize)

    def initialize(self, source, destination, path):
        self.initializations.append((source, destination, path))
        return {'schema_parity': True, 'zero_rows': True}

    def test_fresh_project_identity_and_proof(self):
        receipt = self.create()
        self.assertEqual(receipt['project_id'], 'fresh-project')
        self.assertEqual(self.initializations, [(SOURCE_URI, DESTINATION_URI,
            Path('/Users/booko/engine-facts/full-schema.sql'))])
        self.assertEqual(receipt['schema_proof'], {'schema_parity': True, 'zero_rows': True})

    def test_wrong_org_parent_or_proof_refuses(self):
        original = copy.deepcopy(self.reply)
        for mutation in ('org', 'source_project', 'parent', 'wrong_branch_project', 'proof'):
            with self.subTest(mutation=mutation):
                self.reply = copy.deepcopy(original)
                self.intent.unlink(missing_ok=True)
                if mutation == 'org':
                    self.reply['project']['org_id'] = 'other'
                elif mutation == 'source_project':
                    self.reply['project']['id'] = REGISTRATION['source']['project_id']
                elif mutation == 'parent':
                    self.reply['branch']['parent_id'] = 'source'
                elif mutation == 'wrong_branch_project':
                    self.reply['branch']['project_id'] = 'other'
                else:
                    self.initialize = lambda *args: {'schema_parity': True, 'zero_rows': False}
                with self.assertRaises(MeteringRefusal):
                    self.create()
                self.assertTrue(self.intent.exists())

    def test_root_without_parent_field_is_valid(self):
        del self.reply['branch']['parent_id']
        self.assertTrue(self.create()['creation_confirmed'])

    def test_incomplete_or_malformed_projects_refuse(self):
        normal = self.api
        for inventory in ({'projects': [{}]}, {'projects': None}):
            self.api = lambda method, path, payload=None: (inventory if path.startswith('/projects?')
                                                          else normal(method, path, payload))
            with self.assertRaises(MeteringRefusal):
                self.create()
            self.assert_no_post()

    def test_project_inventory_pages_and_single_page(self):
        normal = self.api
        for pages in ([{'projects': [], 'pagination': {}}],
                      [{'projects': [{'name': 'production', 'id': 'p'}],
                        'pagination': {'cursor': 'p'}}, {'projects': [], 'pagination': {}}],
                      [{'projects': [{'name': 'other', 'id': 'a'}], 'pagination': {'cursor': 'a'}},
                       {'projects': [{'name': 'engine-test', 'id': 'b'}], 'pagination': {'cursor': 'b'}},
                       {'projects': [], 'pagination': {}}]):
            with self.subTest(pages=pages):
                self.intent.unlink(missing_ok=True)
                self.calls.clear()
                remaining = iter(pages)
                cursors = []
                def paginated(method, path, payload=None):
                    if path.startswith('/projects?'):
                        cursors.append(creator.urllib.parse.parse_qs(creator.urllib.parse.urlsplit(path).query).get('cursor'))
                        return next(remaining)
                    return normal(method, path, payload)
                self.api = paginated
                if len(pages) == 3:
                    with self.assertRaises(MeteringRefusal):
                        self.create()
                    self.assert_no_post()
                else:
                    self.assertTrue(self.create()['creation_confirmed'])
                self.assertEqual(len(cursors), len(pages))
                if len(pages) > 1:
                    self.assertEqual(cursors[1], [pages[0]['pagination']['cursor']])

    def test_paginated_rejection_proves_absence(self):
        normal = self.api
        posted = False
        reads = []
        def rejecting(method, path, payload=None):
            nonlocal posted
            if method == 'POST':
                posted = True
                raise creator.ProviderError(412, 'rejected')
            if posted and path.startswith('/projects?'):
                reads.append(path)
                if len(reads) == 1:
                    return {'projects': [{'name': 'other', 'id': 'a'}],
                            'pagination': {'cursor': 'a'}}
                return {'projects': []}
            return normal(method, path, payload)
        self.api = rejecting
        with self.assertRaises(creator.ProviderError) as caught:
            self.create()
        self.assertEqual(caught.exception.creation_outcome, 'not_created')
        self.assertFalse(self.intent.exists())
        self.assertEqual(len(reads), 2)
        self.assertIn('cursor=a', reads[1])

    def test_project_pagination_is_bounded(self):
        for repeated in (True, False):
            calls = []
            def endless(method, path, payload=None):
                calls.append(path)
                return {'projects': [{'name': 'other'}], 'pagination': {'cursor': 'a' if repeated else str(len(calls))}}
            with self.assertRaises(MeteringRefusal):
                creator._project_inventory(endless, REGISTRATION['org_id'])
            self.assertEqual(len(calls), 2 if repeated else 100)

    def test_unavailable_project_details_refuse_and_retain_intent(self):
        normal = self.api
        for after_post in (False, True):
            with self.subTest(after_post=after_post):
                self.intent.unlink(missing_ok=True)
                self.calls.clear()
                posted = False
                def unavailable(method, path, payload=None):
                    nonlocal posted
                    if method == 'POST':
                        posted = True
                        self.calls.append((method, path, payload))
                        raise creator.ProviderError(422, 'rejected')
                    if path.startswith('/projects?') and (posted or not after_post):
                        return {'projects': [], 'unavailable_project_ids': ['unknown'],
                                'pagination': {}}
                    return normal(method, path, payload)
                self.api = unavailable
                with self.assertRaises(MeteringRefusal):
                    self.create()
                self.assertEqual(self.intent.exists(), after_post)
                if not after_post:
                    self.assert_no_post()

    def test_dump_path_refuses_before_intent_and_post(self):
        for kind in ('exists', 'broken_symlink', 'parent_file', 'unwritable'):
            with self.subTest(kind=kind):
                self.dump_path = Path(self.tmp.name) / kind / 'dump.sql'
                self.dump_path.parent.mkdir()
                if kind == 'exists':
                    self.dump_path.touch()
                elif kind == 'broken_symlink':
                    self.dump_path.symlink_to(self.dump_path.parent / 'absent')
                elif kind == 'parent_file':
                    self.dump_path.parent.rmdir()
                    self.dump_path.parent.touch()
                else:
                    self.dump_path.parent.chmod(0o500)
                try:
                    with self.assertRaises((MeteringRefusal, OSError)):
                        self.create()
                    self.assert_no_post()
                    self.assertFalse(self.intent.exists())
                finally:
                    if kind == 'unwritable':
                        self.dump_path.parent.chmod(0o700)

    def test_branch_next_refuses_before_creation(self):
        normal = self.api
        self.api = lambda method, path, payload=None: (
            {'branches': self.branches, 'pagination': {'next': 'another'}}
            if path.endswith('/branches') else normal(method, path, payload))
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_initialize_failure_retains_intent(self):
        self.initialize = lambda *args: (_ for _ in ()).throw(MeteringRefusal('restore failed'))
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assertTrue(self.intent.exists())

    def assert_no_post(self):
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_create_once_and_secret_free_receipt(self):
        receipt = self.create()
        self.assertEqual(receipt['branch_id'], 'br-test')
        self.assertAlmostEqual(receipt['monthly_spend_usd'], 0.212)
        self.assertAlmostEqual(receipt['monthly_projection_usd'], 0.578182)
        self.assertNotIn('secret-response', json.dumps(receipt))
        self.calls.clear()
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_existing_branch_refuses_without_intent(self):
        self.projects.append({'id': 'existing', 'name': 'engine-test'})
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_unknown_name_refuses_before_reads(self):
        with self.assertRaises(MeteringRefusal):
            creator.create_registered_project('other', POLICY, self.costs, self.api, self.intent, self.now, initialize=self.initialize)
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
        self.limit = 4001
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
        self.consumption['projects'][0]['periods'][0]['consumption'][0]['metrics'][0]['value'] = 1800000
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()

    def test_monthly_projection_refuses_before_creation(self):
        self.now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.consumption['projects'][0]['periods'][0]['consumption'][0]['metrics'][0]['value'] = 180000
        with self.assertRaises(MeteringRefusal):
            self.create()
        self.assert_no_post()
        self.assertFalse(self.intent.exists())

    def test_sparse_monthly_consumption_and_inactive_projects(self):
        frame = self.consumption['projects'][0]['periods'][0]['consumption'][0]
        frame['metrics'] = [m for m in frame['metrics'] if m['metric_name'] not in
                            ('private_network_transfer_bytes', 'snapshot_storage_bytes_month')]
        shapes = [('compute_unit_seconds', 'root_branch_bytes_month',
                   'instant_restore_bytes_month', 'public_network_transfer_bytes')]
        shapes += [('root_branch_bytes_month',)] * 6
        for index, names in enumerate(shapes):
            other = copy.deepcopy(self.consumption['projects'][0])
            other['project_id'] = f'fixture-project-{index}'
            other['periods'][0]['consumption'][0]['metrics'] = [
                {'metric_name': name, 'value': 0} for name in names]
            self.consumption['projects'].append(other)
        for index in range(2):
            self.consumption['projects'].append({'project_id': f'inactive-{index}', 'periods': []})
        receipt = self.create()
        self.assertAlmostEqual(receipt['monthly_spend_usd'], 0.212)
        self.assertAlmostEqual(receipt['monthly_projection_usd'], 0.578182)

    def test_invalid_monthly_evidence_refuses_before_intent(self):
        original = copy.deepcopy(self.consumption)
        for mutation in ('missing_target_periods', 'missing_consumption', 'empty_metrics',
                         'stale_start', 'partial_end', 'duplicate_metric', 'unknown_metric'):
            with self.subTest(mutation=mutation):
                self.consumption = copy.deepcopy(original)
                project = self.consumption['projects'][0]
                period = project['periods'][0]
                frame = period['consumption'][0]
                if mutation == 'missing_target_periods':
                    project['periods'] = []
                elif mutation == 'missing_consumption':
                    period['consumption'] = []
                elif mutation == 'empty_metrics':
                    frame['metrics'] = []
                elif mutation == 'stale_start':
                    frame['timeframe_start'] = '2026-10-01T00:00:00Z'
                elif mutation == 'partial_end':
                    frame['timeframe_end'] = '2026-11-02T00:00:00Z'
                elif mutation == 'duplicate_metric':
                    frame['metrics'].append(copy.deepcopy(frame['metrics'][0]))
                else:
                    frame['metrics'].append({'metric_name': 'unpriced_metric', 'value': 1})
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
        self.reply['branch']['parent_id'] = 'production'
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

    def test_default_suspend_readback(self):
        receipt = self.create()
        self.assertEqual(receipt['suspend_timeout_seconds'], 300)
        self.assertEqual(len(self.initializations), 1)

    def test_registered_owner_in_receipt(self):
        self.assertEqual(self.create()['owner'], REGISTRATION['owner'])

    def test_invalid_provider_result_keeps_intent(self):
        for timeout in (60, -1, 301, None, False, '0'):
            with self.subTest(timeout=timeout):
                self.intent.unlink(missing_ok=True)
                self.reply['endpoints'][0]['suspend_timeout_seconds'] = timeout
                with self.assertRaises(MeteringRefusal):
                    self.create()
                self.assertTrue(self.intent.exists())
                self.assertEqual(self.initializations, [])

    def test_source_parameters_refuse_before_creation(self):
        import psycopg
        normal = self.api
        for uri in (SOURCE_URI + '&application_name=unsupported', 'invalid uri'):
            with self.subTest(uri=uri):
                self.calls.clear()
                def invalid(method, path, payload=None):
                    if '/connection_uri?' in path:
                        return {'uri': uri}
                    return normal(method, path, payload)
                self.api = invalid
                with self.assertRaises((MeteringRefusal, psycopg.ProgrammingError)):
                    self.create()
                self.assert_no_post()
                self.assertFalse(self.intent.exists())

    def test_empty_terminal_project_pages(self):
        for pagination in ({}, {'cursor': 'a'}):
            with self.subTest(pagination=pagination):
                pages = iter([{'projects': [{'name': 'other'}], 'pagination': {'cursor': 'a'}},
                              {'projects': [], 'pagination': pagination}])
                self.assertEqual(creator._project_inventory(lambda *args: next(pages),
                    REGISTRATION['org_id']), [{'name': 'other'}])

    def test_read_error_stops_before_intent(self):
        def failing(*args):
            raise ConnectionError('provider unavailable')
        self.api = failing
        with self.assertRaises(ConnectionError):
            self.create()
        self.assertFalse(self.intent.exists())

    def test_http_error_message_is_safe_and_structured(self):
        body = {'message': 'invalid suspend timeout; Bearer synthetic-token ' +
                           ('postgresql://' + 'user:password@host/db'),
                'connection_uri': 'secret-response-must-not-escape'}
        error = urllib.error.HTTPError(creator.API, 400, 'bad',
                                       {'Authorization': 'secret-header'},
                                       io.BytesIO(json.dumps(body).encode()))
        with patch.object(creator.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(MeteringRefusal) as caught:
                creator._provider('synthetic-token')('POST', '/projects/test/branches', {})
        receipt = caught.exception.receipt()
        self.assertEqual(receipt['http_status'], 400)
        self.assertIn('invalid suspend timeout', receipt['provider_message'])
        for secret in ('synthetic-token', 'password', 'secret-header', 'secret-response'):
            self.assertNotIn(secret, json.dumps(receipt))

    def test_provider_error_body_variants(self):
        for body in (b'not JSON', b'[]', b'{"message": null}'):
            error = creator._provider_error(503, body, 'synthetic-token')
            self.assertEqual(error.status, 503)
            self.assertEqual(error.message, 'provider returned no JSON error message')
        error = creator._provider_error(400, json.dumps({
            'message': 'bad token=synthetic-value password=synthetic-password'}).encode(),
            'synthetic-token')
        self.assertNotIn('synthetic-value', error.message)
        self.assertNotIn('synthetic-password', error.message)

    def test_provider_rejection_reconciles_intent(self):
        for status, outcome in ((400, 'absent'), (412, 'absent'), (409, 'present'), (422, 'incomplete'),
                                (403, 'unavailable'), (408, 'absent'), (500, 'absent')):
            with self.subTest(status=status, outcome=outcome):
                self.setUp()
                normal = CreatorTests.api.__get__(self)
                posted = False
                def failing(method, path, payload=None):
                    nonlocal posted
                    if method == 'POST':
                        posted = True
                        raise creator.ProviderError(status, 'invalid suspend timeout')
                    if posted and path.startswith('/projects?'):
                        if outcome == 'unavailable':
                            raise TimeoutError('readback unavailable')
                        if outcome == 'present':
                            return {'projects': [{'name': 'engine-test', 'id': 'existing'}]}
                        if outcome == 'incomplete':
                            return {'projects': [], 'unavailable_project_ids': ['unknown']}
                        return {'projects': []}
                    return normal(method, path, payload)
                self.api = failing
                with self.assertRaises(creator.ProviderError) as caught:
                    self.create()
                cleared = status in (400, 412)
                self.assertEqual(self.intent.exists(), not cleared)
                self.assertEqual(caught.exception.receipt()['creation_outcome'],
                                 'not_created' if cleared else 'unknown')
                if cleared:
                    self.api = normal
                    self.assertTrue(self.create()['creation_confirmed'])
                else:
                    with self.assertRaises(MeteringRefusal):
                        self.create()

    def test_provider_refusal_cli_receipt(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['creator', 'engine-test']), \
                patch.dict('os.environ', {'NEON_API_KEY': 'synthetic-token'}), \
                patch.object(creator.Path, 'home', return_value=Path(self.tmp.name)), \
                patch.object(creator, 'create_registered_project',
                             side_effect=creator.ProviderError(422, 'invalid suspend timeout')), \
                patch('sys.stdout', output), patch('sys.stderr', errors):
            self.assertEqual(creator.main(), 77)
        self.assertEqual(json.loads(output.getvalue())['http_status'], 422)
        self.assertIn('422', errors.getvalue())
        self.assertIn('invalid suspend timeout', errors.getvalue())

    def test_cli_pass_and_refusal(self):
        import io
        for limit, expected in [(1000, 0), (None, 77)]:
            self.setUp()
            self.limit = limit
            output = io.StringIO()
            with patch.object(sys, 'argv', ['neon-persistent-branch.py', 'engine-test']), \
                    patch.dict('os.environ', {'NEON_API_KEY': 'synthetic-token'}), \
                    patch.object(creator, '_provider', return_value=self.api), \
                    patch.object(creator, 'initialize_schema', side_effect=self.initialize), \
                    patch.object(creator.Path, 'home', return_value=Path(self.tmp.name)), \
                    patch.object(creator, 'datetime') as clock, \
                    patch('sys.stdout', output), patch('sys.stderr', output):
                clock.now.return_value = self.now
                # The command owns the state directory; use the same intent in the fake provider.
                self.intent = Path(self.tmp.name) / '.cache/carr/neon-persistent-branch/creation-intent.json'
                self.assertEqual(creator.main(), expected)
            self.assertNotIn('secret-response', output.getvalue())
            self.assertNotIn('synthetic-token', output.getvalue())


class SchemaInitializationTests(unittest.TestCase):
    def test_real_libpq_clients_use_host_without_secret_disclosure(self):
        uri = 'postgresql://' + 'synthetic:pw@127.0.0.1:1/synthetic?sslmode=require'
        for binary in ('pg_dump', 'psql'):
            with self.subTest(binary=binary):
                executable = shutil.which(binary) or str(Path('/opt/homebrew/opt/libpq/bin') / binary)
                self.assertTrue(Path(executable).is_file(), 'real PostgreSQL client required')
                build = creator.postgres_environment
                with patch.dict(os.environ, {'PGHOST': '/nonexistent-socket', 'PGSERVICE': 'wrong'}):
                    env = build(uri, '-c statement_timeout=2000')
                env['PGCONNECT_TIMEOUT'] = '2'
                args = [executable, '--no-password']
                args += ['--schema-only'] if binary == 'pg_dump' else ['-X', '-c', 'SELECT 1']
                result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('127.0.0.1', result.stderr)
                self.assertIn('port 1', result.stderr)
                self.assertNotIn('socket', result.stderr)
                for secret in ('pw', uri):
                    self.assertNotIn(secret, str(args) + result.stdout + result.stderr)
                self.assertEqual(env['PGDATABASE'], 'synthetic')
                self.assertEqual(env['PGSSLMODE'], 'require')

    def test_connection_options_are_preserved_with_required_settings_last(self):
        env = creator.postgres_environment(SOURCE_URI + '&options=-c%20search_path%3Dpublic',
                                           '-c default_transaction_read_only=on')
        self.assertEqual(env['PGOPTIONS'], '-c search_path=public -c default_transaction_read_only=on')

    def test_schema_only_restore_proof_and_failures(self):
        from unittest.mock import MagicMock
        import psycopg
        source_uri = 'postgresql://' + 'source:pw@127.0.0.1:1/source?sslmode=require'
        destination_uri = 'postgresql://' + 'destination:pw@127.0.0.1:1/destination?sslmode=require'
        for failure in (None, 'parity', 'rows', 'dump', 'dump-empty', 'restore', 'timeout', 'foreign'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'full-schema.sql'
                source, destination, check = MagicMock(), MagicMock(), MagicMock()
                counts = [('public', 'table', 1), ('public', 'function', 2)]
                source.execute.return_value.fetchall.return_value = (
                    [('public', 'foreign_table', 1)] if failure == 'foreign' else counts)
                destination.execute.return_value.fetchall.return_value = (
                    [] if failure == 'parity' else counts)
                check.execute.return_value.fetchall.return_value = [('public', 'deal')]
                check.execute.return_value.fetchone.return_value = (failure == 'rows',)
                connections = [source, destination, check]
                def connect(uri, **kwargs):
                    self.assertIn('default_transaction_read_only=on', kwargs['options'])
                    self.assertEqual(uri, source_uri if len(connections) == 3 else destination_uri)
                    context = MagicMock()
                    context.__enter__.return_value = connections.pop(0)
                    return context
                commands = []
                def run(args, **kwargs):
                    commands.append(args)
                    self.assertNotIn('pw', ' '.join(args))
                    if args[0] == 'pg_dump' and failure == 'dump-empty':
                        raise creator.subprocess.CalledProcessError(1, args)
                    if args[0] == 'pg_dump' and failure == 'timeout':
                        raise creator.subprocess.TimeoutExpired(args, 300)
                    if args[0] == 'pg_dump':
                        self.assertIn('--schema-only', args)
                        for flag in ('--no-owner', '--no-privileges', '--no-policies', '--no-subscriptions'):
                            self.assertIn(flag, args)
                        self.assertEqual(kwargs['env']['PGDATABASE'], 'source')
                        self.assertIn('default_transaction_read_only=on', kwargs['env']['PGOPTIONS'])
                        kwargs['stdout'].write('-- synthetic schema only')
                    else:
                        self.assertIn('--single-transaction', args)
                        self.assertEqual(kwargs['env']['PGDATABASE'], 'destination')
                    if failure == ('dump' if args[0] == 'pg_dump' else 'restore'):
                        raise creator.subprocess.CalledProcessError(1, args)
                with patch.object(psycopg, 'connect', side_effect=connect), \
                        patch.object(creator.subprocess, 'run', side_effect=run):
                    if failure:
                        with self.assertRaises(MeteringRefusal) as caught:
                            creator.initialize_schema(source_uri, destination_uri, path)
                        if failure in ('dump', 'dump-empty', 'restore', 'timeout'):
                            self.assertFalse(path.exists())
                            self.assertIn('reconcile creation intent', str(caught.exception))
                            self.assertIn('destination project', str(caught.exception))
                            self.assertIn('dump removed', str(caught.exception))
                            creator.validate_dump_path(path)
                    else:
                        proof = creator.initialize_schema(source_uri, destination_uri, path)
                        self.assertTrue(proof['zero_rows'])
                        self.assertTrue(proof['schema_parity'])
                        self.assertEqual(proof['user_tables_checked'], 1)
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                if failure == 'foreign':
                    self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
