#!/usr/bin/env python3
"""Offline native-JS boundary tests for dispatcher worktree assignments."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'room-bridge'))
spec = importlib.util.spec_from_file_location('adapter_fixture', ROOT / 'tools/room-bridge/test_engineering_dispatch_adapter_unit.py')
assert spec is not None and spec.loader is not None
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
adapter = fixture.adapter


def record(section_id, body):
    text = json.dumps(body, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {'id': section_id, 'status': 'active', 'current_version': '3',
            'body': {'text': text}, 'content_hash': digest}


def scenario():
    binding = fixture.synthetic_binding(source_merge_required=False)
    binding['slice_ref'] = 'R09'
    meta = {**adapter.R09_ASSIGNMENT, 'envelope_id': 'env:fresh',
            'envelope_digest': 'sha256:' + 'b' * 64, 'attempt_id': 'attempt:1'}
    name = 'wr70-r09-runtime-isolation'
    paths = ['tools/room-bridge/worktree_runtime_isolation.py', 'tools/test-worktree-runtime-isolation.py']
    scope = {'work_request': {'ref': binding['work_request_ref'], 'accepted_plan': binding['accepted_plan_revision']['id']},
             'registered_plan': {'slice_ref': 'R09'},
             'exact_scoped_source_lease': {'paths': paths, 'worktree': '/Users/booko/carr-system/.claude/worktrees/' + name,
               'branch': 'codex/' + name}}
    # Literal canonical boundary is the product contract, independent of this test checkout.
    worktree = '/Users/booko/carr-system/.claude/worktrees/' + name
    scope['exact_scoped_source_lease']['worktree'] = worktree
    scope_row = record(meta['scope_section_id'], scope)
    packet_row = record(meta['packet_section_id'], {'slice': 'R09', 'packet': {'required_behavior': 'fake-only atomic allocation'}})
    meta['scope_sha256'] = scope_row['content_hash']
    meta['packet_sha256'] = packet_row['content_hash']
    binding['operator_assignment'] = meta
    assignment = {'schema_version': 'engineering-source-assignment.v1', 'state': 'assigned',
                  'work_request': binding['work_request_ref'], 'accepted_plan': binding['accepted_plan_revision']['id'],
                  'accepted_plan_digest': binding['accepted_plan_revision']['digest'], 'slice_ref': 'R09',
                  'envelope_id': meta['envelope_id'], 'envelope_digest': meta['envelope_digest'],
                  'attempt_id': meta['attempt_id'], 'source_main': 'd' * 40, 'paths': paths,
                  'worktree': worktree, 'branch': 'codex/' + name, 'helper_name': name,
                  'scope_section_id': meta['scope_section_id'], 'scope_sha256': meta['scope_sha256']}
    response = {'ok': True, 'missing': [], 'sections': [record(meta['section_id'], assignment), scope_row, packet_row]}
    return binding, assignment, response


def execute(binding, response):
    body = fixture.synthetic_runbook_body(600)
    return fixture.execute_source_projection(binding, fixture.synthetic_source(binding, body, source_merge=None),
        fixture.synthetic_doctrine(body), assignment_response=response)


class AssignmentBoundaryTests(unittest.TestCase):
    def test_fresh_exact_attempt_delivers_scope_and_complete_implementation_packet(self):
        binding, assignment, response = scenario()
        result = execute(binding, response)
        self.assertIsNone(result['error'])
        projection = result['output'][0]['operator_assignment']
        for key, value in assignment.items():
            self.assertEqual(projection[key], value)
        chunks = [row for row in result['output'] if row.get('schema_version') == 'engineering-assignment-native-chunk.v1']
        self.assertTrue(chunks)
        self.assertEqual(chunks[-1]['remaining'], 0)
        derived_body = ''.join(row['text'] for row in chunks)
        self.assertEqual(chunks[-1]['content_hash'], 'sha256:' + hashlib.sha256(derived_body.encode()).hexdigest())
        supplemental = json.loads(derived_body)
        self.assertEqual(supplemental['assignment'], assignment)
        self.assertEqual(supplemental['implementation_packet']['required_behavior'], 'fake-only atomic allocation')
        self.assertEqual(len(result['calls']), 3)

    def test_stale_attempt_state_or_scope_refuses_before_projection(self):
        changes = {'state': 'exhausted', 'envelope_id': 'env:predecessor',
            'envelope_digest': 'sha256:' + 'c' * 64, 'attempt_id': 'attempt:2',
            'accepted_plan': 'PLAN-other-v1', 'accepted_plan_digest': 'sha256:' + 'f' * 64,
            'work_request': 'WR-000999', 'slice_ref': 'R07', 'scope_sha256': 'e' * 64,
            'worktree': '/tmp/unassigned', 'branch': 'codex/other', 'helper_name': 'other',
            'paths': ['hooks/not-authorized.py'], 'source_main': 'origin/main'}
        for field, value in changes.items():
            with self.subTest(field=field):
                binding, assignment, response = scenario()
                assignment[field] = value
                response['sections'][0] = record(binding['operator_assignment']['section_id'], assignment)
                result = execute(binding, response)
                self.assertIsNotNone(result['error'])
                self.assertEqual(result['output'], [])

    def test_missing_tampered_or_duplicated_canonical_scope_refuses(self):
        for mode in ['missing', 'duplicate', 'hash', 'scope', 'inactive', 'shape']:
            with self.subTest(mode=mode):
                binding, assignment, response = scenario()
                if mode == 'missing': response['missing'] = [binding['operator_assignment']['scope_section_id']]
                elif mode == 'duplicate': response['sections'][1] = response['sections'][0]
                elif mode == 'hash': response['sections'][0]['content_hash'] = 'a' * 64
                elif mode == 'scope':
                    scope = json.loads(response['sections'][1]['body']['text'])
                    scope['exact_scoped_source_lease']['paths'] = ['other.py']
                    response['sections'][1] = record(binding['operator_assignment']['scope_section_id'], scope)
                elif mode == 'inactive': response['sections'][0]['status'] = 'archived'
                else:
                    assignment['override'] = True
                    response['sections'][0] = record(binding['operator_assignment']['section_id'], assignment)
                self.assertIsNotNone(execute(binding, response)['error'])

    def test_completed_claim_is_rejected_at_adapter_return_for_wrong_worktree(self):
        assignment = copy.deepcopy(adapter.R09_ASSIGNMENT)
        receipt = fixture.valid_receipt()
        receipt['outcome'] = 'claimed_complete'
        original = adapter.bind_operator_assignment
        def assigned(binding, task, envelope):
            return {**original(binding, task, envelope), 'operator_assignment': assignment}
        def dispatch(*args, **kwargs):
            return {'status': 'completed', 'result': json.dumps(receipt)}
        # These are perfectly valid opaque refs, so schema validation alone is insufficient.
        receipt['source_evidence']['worktree_ref'] = 'worktree:sha256:' + 'a' * 64
        receipt['source_evidence']['branch_ref'] = assignment['expected_branch_ref']
        fixture.passport.validate_engineering_slice_receipt(receipt, fixture.PLAN, fixture.ENVELOPE)
        with patch.object(adapter, 'bind_operator_assignment', assigned):
            with self.assertRaisesRegex(adapter.DispatchRefusal, 'dispatcher assignment'):
                adapter.run(fixture.request(), dispatch_fn=dispatch, registry=fixture.ValidEngineeringDesk())
            receipt['source_evidence']['worktree_ref'] = assignment['expected_worktree_ref']
            self.assertTrue(adapter.run(fixture.request(), dispatch_fn=dispatch,
                                       registry=fixture.ValidEngineeringDesk())['ok'])
            receipt['source_evidence']['branch_ref'] = 'branch:sha256:' + 'a' * 64
            with self.assertRaisesRegex(adapter.DispatchRefusal, 'dispatcher assignment'):
                adapter.run(fixture.request(), dispatch_fn=dispatch, registry=fixture.ValidEngineeringDesk())
            # A blocked observation of the wrong checkout is evidence, never a completed claim.
            receipt['outcome'] = 'blocked'
            self.assertTrue(adapter.run(fixture.request(), dispatch_fn=dispatch,
                                       registry=fixture.ValidEngineeringDesk())['ok'])

    def test_stable_pointer_only_binds_exact_accepted_r09_plan(self):
        binding = fixture.synthetic_binding(source_merge_required=False)
        task = {'attempt_id': 'attempt:1'}
        self.assertNotIn('operator_assignment', adapter.bind_operator_assignment(binding, task, fixture.ENVELOPE))
        binding.update(work_request_ref='WR-000070', slice_ref='R09')
        with self.assertRaises(adapter.DispatchRefusal):
            adapter.bind_operator_assignment(binding, task, fixture.ENVELOPE)
        binding['accepted_plan_revision'] = {'id': 'PLAN-745ea4f7e374-v1', 'revision': 1,
            'digest': 'sha256:745ea4f7e3745c86ee6aae273e5ec915a539493f4a1b0dafe2144b7d3d70eb60'}
        with self.assertRaisesRegex(adapter.DispatchRefusal, 'attempt id'):
            adapter.bind_operator_assignment(binding, {}, fixture.ENVELOPE)
        bound = adapter.bind_operator_assignment(binding, task, fixture.ENVELOPE)
        self.assertEqual(bound['operator_assignment']['section_id'], adapter.R09_ASSIGNMENT['section_id'])
        self.assertEqual(bound['operator_assignment']['envelope_id'], fixture.ENVELOPE['envelope_id'])


if __name__ == '__main__':
    unittest.main()
