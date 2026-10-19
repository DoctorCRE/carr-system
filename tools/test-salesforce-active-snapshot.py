import sys
import copy
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import salesforce_active_snapshot as active


PSV = 'sf_id_tail|name|type|close|commission|phase|transaction|owner|company\n'


class ActiveSnapshotTests(unittest.TestCase):
    def test_active_snapshot_plans_phase_and_unpark_without_inventing_full_ids(self):
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Dell|Example Practice\n'
        board = {'deals': [{'id': 'd1', 'name': 'Example office', 'phase': 'research',
                           'owner': 'dell', 'operating_state': 'parked', 'client_ref': 'C-001',
                           'field_base': {'operating_state': {'id': 'event-1'}}}], 'accounts': []}
        plan = active.build_plan(source, board, expected_count=1, bindings={})
        self.assertEqual(plan['counts'], {'source': 1, 'create': 0, 'update': 1, 'unpark': 1, 'unchanged': 0, 'held': 0})
        item = plan['items'][0]
        self.assertEqual(item['desired']['phase'], 'legal')
        self.assertEqual(item['desired']['operating_state'], 'active')
        self.assertNotIn('salesforce_id', item['desired'])

    def test_counterpart_owner_is_a_market_agent_and_partner_override_wins(self):
        source = PSV + '00001AAA|Example franchise - West|L|12/1/2026|15000|Legal|Start Up|Outside Agent|Example Brand\n'
        bindings = {'00001AAA': {'salesforce_id': '006000000000001AAA', 'account_client_id': 'account-1',
                                'client_name': 'Example franchise', 'market': 'West', 'owner': 'dell'}}
        plan = active.build_plan(source, {'deals': [], 'accounts': []}, expected_count=1, bindings=bindings)
        item = plan['items'][0]
        self.assertEqual(item['kind'], 'create')
        self.assertEqual(item['create']['verb'], 'create-national-market-deal')
        self.assertEqual(item['create']['args']['agent_name'], 'Outside Agent')
        self.assertEqual(item['desired']['owner'], 'dell')
        self.assertEqual(item['desired']['salesforce_id'], '006000000000001AAA')

    def test_missing_id_mapping_and_duplicate_targets_are_held(self):
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Joe|Example Practice\n'
        self.assertEqual(active.build_plan(source, {'deals': [], 'accounts': []}, expected_count=1, bindings={})['counts']['held'], 1)
        board = {'deals': [{'id': 'd1', 'name': 'Example office', 'phase': 'legal', 'owner': 'joe',
                            'operating_state': 'active', 'field_base': {}}], 'accounts': []}
        duplicated = source + source.splitlines()[1].replace('00001AAA', '00002AAA') + '\n'
        self.assertEqual(active.build_plan(duplicated, board, expected_count=2, bindings={})['counts']['held'], 2)

    def test_ambiguous_intake_is_held_while_independent_records_continue(self):
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Dell|Example Practice\n' + \
                       '00002AAA|Existing office|L|12/1/2026|15000|Legal|Start Up|Dell|Existing Practice\n'
        binding = {'00001AAA': {'salesforce_id': '006000000000001AAA',
            'intake': {'party': {'name': 'Example Practice', 'kind': 'org'}, 'client': {}}}}
        row = {'id': 'd2', 'name': 'Existing office', 'phase': 'research', 'owner': 'dell',
               'operating_state': 'active', 'salesforce_id': None, 'base_version': 1}
        board = {'deals': [row], 'accounts': []}
        writes = []
        def record(verb, args):
            if verb == 'deal-room-board':
                return copy.deepcopy(board)
            if verb in ('read-deal-reconciliation', 'get-deal-room'):
                return copy.deepcopy(row)
            writes.append(verb)
            if verb == 'add-party':
                return {'needs_confirm': True, 'candidates': [{'id': 'existing-party'}]}
            if verb == 'update-deal':
                row.update(args['fields'])
                return {'ok': True}
            self.fail(verb)
        plan = active.build_plan(source, board, expected_count=2, bindings=binding)
        with tempfile.TemporaryDirectory() as folder:
            result = active.apply_plan(plan, record, Path(folder) / 'receipts')
        self.assertEqual(result, {'verified': 1, 'held': 1})
        self.assertEqual(writes, ['add-party', 'update-deal'])

    def test_uncertain_write_leaves_pending_receipt_and_is_never_retried(self):
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Dell|Example Practice\n'
        row = {'id': 'd1', 'name': 'Example office', 'phase': 'research', 'owner': 'dell',
               'operating_state': 'active', 'salesforce_id': None, 'base_version': 1}
        board = {'deals': [row], 'accounts': []}
        attempts = []
        def record(verb, args):
            if verb == 'deal-room-board':
                return copy.deepcopy(board)
            if verb in ('read-deal-reconciliation', 'get-deal-room'):
                return copy.deepcopy(row)
            attempts.append(verb)
            raise TimeoutError('response lost')
        plan = active.build_plan(source, board, expected_count=1, bindings={})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipts'
            with self.assertRaises(TimeoutError):
                active.apply_plan(plan, record, path)
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['status'], 'pending')
            with self.assertRaisesRegex(ValueError, 'attempt_already_recorded'):
                active.apply_plan(plan, record, path)
        self.assertEqual(attempts, ['update-deal'])

    def test_create_can_intake_a_source_backed_client_without_inventing_contact_values(self):
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Dell|Example Practice\n'
        binding = {'00001AAA': {'salesforce_id': '006000000000001AAA',
            'intake': {'party': {'name': 'Example Practice', 'kind': 'org'},
                       'client': {'status': 'active_deal', 'acquisition_source': 'unknown'}}}}
        rows = []
        writes = []
        def record(verb, args):
            if verb == 'deal-room-board':
                return {'deals': copy.deepcopy(rows), 'accounts': []}
            if verb == 'get-deal-room':
                return copy.deepcopy(rows[0])
            writes.append(verb)
            self.assertIn('idempotency_key', args)
            if verb == 'add-party':
                self.assertFalse({'phone', 'email', 'specialty'} & args.keys())
                return {'ok': True, 'party_id': 'party-1'}
            if verb == 'new-client':
                self.assertEqual(args['party_id'], 'party-1')
                return {'ok': True, 'ref': 'C-001'}
            if verb == 'new-deal':
                self.assertEqual(args['client'], 'C-001')
                rows.append({'id': 'd1', 'phase': args['phase'], 'salesforce_id': args['salesforce_id'],
                             'owner': None, 'operating_state': 'active', 'base_version': 1})
                return {'ok': True, 'deal_id': 'd1'}
            if verb == 'patch-deal-field':
                rows[0][args['field']] = args['value']
                return {'ok': True}
            self.fail(verb)
        plan = active.build_plan(source, {'deals': [], 'accounts': []}, expected_count=1, bindings=binding)
        with tempfile.TemporaryDirectory() as folder:
            result = active.apply_plan(plan, record, Path(folder) / 'receipts')
        self.assertEqual(result, {'verified': 1, 'held': 0})
        self.assertEqual(writes, ['add-party', 'new-client', 'new-deal', 'patch-deal-field'])

    def test_cli_reads_psv_and_private_bindings_without_printing_names(self):
        from lib import salesforce_deal_sync as sync
        import contextlib
        import io
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Dell|Example Practice\n'
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'source.psv').write_text(source)
            (folder / 'bindings.json').write_text(json.dumps({'00001AAA': {
                'salesforce_id': '006000000000001AAA', 'client_ref': 'C-001'}}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = sync.main([str(folder / 'source.psv'), '--bindings', str(folder / 'bindings.json'),
                    '--expected-count', '1', '--out', str(folder / 'plan.json')],
                    call_verb=lambda verb, args: {'deals': [], 'accounts': []})
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['create'], 1)
            self.assertNotIn('Example', output.getvalue())
            self.assertEqual((folder / 'plan.json').stat().st_mode & 0o777, 0o600)

    def test_apply_uses_guarded_verbs_and_verifies_phase_owner_and_active_state(self):
        source = PSV + '00001AAA|Example office|L|12/1/2026|15000|Legal|Start Up|Dell|Example Practice\n'
        row = {'id': 'd1', 'name': 'Example office', 'phase': 'research', 'owner': 'joe',
               'salesforce_id': '006000000000001AAA', 'base_version': 1,
               'operating_state': 'parked', 'field_base': {'operating_state': {'id': 'e1'}}}
        board = {'deals': [row], 'accounts': []}
        calls = []
        def record(verb, args):
            calls.append((verb, copy.deepcopy(args)))
            if verb == 'deal-room-board':
                return copy.deepcopy(board)
            if verb in ('read-deal-reconciliation', 'get-deal-room'):
                return {k: copy.deepcopy(v) for k, v in row.items() if k != 'field_base'}
            self.assertIn('idempotency_key', args)
            if verb == 'update-deal':
                self.assertEqual(args['base_version'], row['base_version'])
                row.update(args['fields'])
                row['base_version'] += 1
            elif verb == 'patch-deal-field':
                self.assertEqual(args['base_event_id'], row['field_base'].get(args['field'], {}).get('id'))
                row[args['field']] = args['value']['state'] if args['field'] == 'operating_state' else args['value']
            else:
                self.fail(verb)
            return {'ok': True}
        plan = active.build_plan(source, board, expected_count=1, bindings={})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipts.jsonl'
            result = active.apply_plan(plan, record, path)
            self.assertEqual(result['verified'], 1)
            self.assertEqual((row['phase'], row['owner'], row['operating_state']), ('legal', 'dell', 'active'))
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['status'], 'complete')
            with self.assertRaisesRegex(ValueError, 'attempt_already_recorded'):
                active.apply_plan(plan, record, path)
        self.assertFalse(any('salesforce' in verb for verb, _ in calls))

    def test_fully_closed_out_is_won_and_closed_lost_is_a_distinct_status(self):
        for stage, status, outcome in [('Fully Closed Out', 'closed', 'won'), ('Closed Lost', 'lost', 'lost')]:
            source = PSV + f'00001AAA|Example office|L|12/1/2026|15000|{stage}|Start Up|Joe|Example Practice\n'
            binding = {'00001AAA': {'salesforce_id': '006000000000001AAA', 'client_ref': 'C-001'}}
            item = active.build_plan(source, {'deals': [], 'accounts': []}, expected_count=1, bindings=binding)['items'][0]
            self.assertEqual(item['status'], status)
            self.assertEqual(item['desired']['outcome'], outcome)
            if status == 'lost':
                self.assertNotEqual(item['desired'].get('phase'), 'closed')


if __name__ == '__main__':
    unittest.main()
