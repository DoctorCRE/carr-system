"""Verify that production proof needs bound hosted receipts and the native App actor."""
import copy
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.loadpy import load_module_from_path
m = load_module_from_path('verdict_proof_test', str(Path(__file__).with_name('verdict-delivery-proof.py')))


class Proof(unittest.TestCase):
    def setUp(self):
        self.data = {'actions/workflows/verdict-delivery.yml': {'id': 10},
                     'queue/12': {'headRefOid': 'a' * 40, 'isInMergeQueue': False, 'mergeQueueEntry': None}}
        self.receipts = {}
        for run_id, state, number in [(1, 'armed', 12), (2, 'held', 2988)]:
            self.data[f'actions/runs/{run_id}'] = {
                'workflow_id': 10, 'conclusion': 'success', 'run_attempt': 1, 'event': 'status',
                'repository': {'full_name': m.m.REPO}, 'head_repository': {'full_name': m.m.REPO},
                'html_url': f'https://github.com/DoctorCRE/carr-system/actions/runs/{run_id}'}
            self.data[f'actions/runs/{run_id}/artifacts?per_page=100'] = {
                'total_count': 1, 'artifacts': [{'id': run_id,
                    'name': f'verdict-delivery-{run_id}-1', 'expired': False, 'size_in_bytes': 500}]}
            self.receipts[run_id] = [{'schema': 'carr-verdict-delivery.v1', 'repository': m.m.REPO,
                'state': state, 'pull_request': number, 'head_sha': 'a' * 40, 'reason': 'verified fact'}]
            self.data[f'pulls/{number}'] = {'head': {'sha': 'a' * 40}, 'labels': [],
                'auto_merge': {'enabled_by': {'login': m.m.APP}}}

    def read(self, path):
        return copy.deepcopy(self.data[path])

    def archive(self, artifact_id):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as target:
            target.writestr('receipt.json', json.dumps(self.receipts[artifact_id]))
        return buffer.getvalue()

    def observe(self):
        return m.observe(1, 2, self.read, self.archive)

    def test_bound_receipts_and_native_actor_verify(self):
        self.assertEqual(self.observe()['state'], 'verified')

    def test_coordinator_actor_or_merged_pr_cannot_pass(self):
        self.data['pulls/12']['auto_merge']['enabled_by']['login'] = 'coordinator'
        with self.assertRaises(ValueError): self.observe()
        self.data['pulls/12']['auto_merge'] = None
        with self.assertRaises(ValueError): self.observe()

    def test_head_moved_refuses(self):
        self.data['pulls/12']['head']['sha'] = 'b' * 40
        with self.assertRaises(ValueError): self.observe()

    def test_untrusted_workflow_and_attempt_refuse(self):
        self.data['actions/runs/1']['workflow_id'] = 99
        with self.assertRaises(ValueError): self.observe()
        self.data['actions/runs/1']['workflow_id'] = 10
        self.data['actions/runs/1']['run_attempt'] = 2
        with self.assertRaises(ValueError): self.observe()

    def test_wrong_held_pr_refuses(self):
        self.receipts[2][0]['pull_request'] = 13
        with self.assertRaises(ValueError): self.observe()

    def test_expired_or_truncated_artifacts_refuse(self):
        self.data['actions/runs/1/artifacts?per_page=100']['artifacts'][0]['expired'] = True
        with self.assertRaises(ValueError): self.observe()
        self.data['actions/runs/1/artifacts?per_page=100']['total_count'] = 2
        with self.assertRaises(ValueError): self.observe()

    def test_archive_never_extracts_untrusted_paths(self):
        def archive(artifact_id):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as target:
                target.writestr('../receipt.json', '{}')
            return buffer.getvalue()
        with self.assertRaises(ValueError): m.observe(1, 2, self.read, archive)

    def test_native_queue_actor_verifies_without_auto_merge_request(self):
        self.data['pulls/12']['auto_merge'] = None
        self.data['queue/12'] = {'headRefOid': 'a' * 40, 'isInMergeQueue': True,
            'mergeQueueEntry': {'enqueuer': {'login': 'doctorcre-carr-review', '__typename': 'Bot'}}}
        self.assertEqual(self.observe()['state'], 'verified')
        self.data['queue/12']['mergeQueueEntry']['enqueuer']['login'] = 'coordinator'
        with self.assertRaises(ValueError): self.observe()

    def test_queue_entry_cannot_verify_a_different_pr_head(self):
        self.data['pulls/12']['auto_merge'] = None
        self.data['queue/12'] = {'headRefOid': 'b' * 40, 'isInMergeQueue': True,
            'mergeQueueEntry': {'enqueuer': {'login': 'doctorcre-carr-review', '__typename': 'Bot'}}}
        with self.assertRaises(ValueError): self.observe()


if __name__ == '__main__':
    unittest.main()
