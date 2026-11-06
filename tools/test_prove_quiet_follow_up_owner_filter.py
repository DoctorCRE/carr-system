"""Replay actual registered responses through the proof's production parser."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-quiet-follow-up-owner-filter.py'
spec = importlib.util.spec_from_file_location('quiet_owner_proof', RUNNER)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class OwnerProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recording = json.loads((ROOT / 'tools/fixtures/quiet-follow-up-owner-filter.json').read_text())

    def run_fixture(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.json'
            path.write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(RUNNER), '--fixture', str(path)],
                                    text=True, capture_output=True, timeout=10)
            return result.returncode, json.loads(result.stdout)

    def test_registered_recording_passes_response_contract_and_cli(self):
        for key in ('all', 'owned', 'unassigned'):
            proof.response(self.recording[key])
        self.assertEqual(self.run_fixture(self.recording)[0], 0)

    def test_subset_assertion_failure_exits_one(self):
        value = copy.deepcopy(self.recording)
        value['owned']['proposals'] = []
        code, result = self.run_fixture(value)
        self.assertEqual((code, result['reason']), (1, 'owned_subset_changed'))

    def test_flags_cadence_order_and_unassigned_contradictions(self):
        for mode in ('sent', 'cadence', 'order', 'unassigned'):
            with self.subTest(mode=mode):
                value = copy.deepcopy(self.recording)
                if mode == 'sent': value['all']['sent'] = True
                if mode == 'cadence': value['owned']['cadence']['lead_days'] = 1
                if mode == 'order': value['all']['proposals'].reverse()
                if mode == 'unassigned': value['unassigned']['proposals'] = []
                self.assertEqual(self.run_fixture(value)[0], 1)

    def test_insufficient_contract_or_cohorts_exit_78(self):
        for mode in ('missing', 'shape', 'cohorts', 'provenance'):
            value = copy.deepcopy(self.recording)
            if mode == 'missing': del value['owned']
            if mode == 'shape': value['owned'] = {'ok': True}
            if mode == 'cohorts': value['all']['proposals'] = []
            if mode == 'provenance': value['provenance']['query']['owned'] = {}
            self.assertEqual(self.run_fixture(value)[0], 78)

    def test_identity_only_is_insufficient(self):
        from lib.release_reader import Reply
        release = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'a' * 40}}
        with patch.object(proof, 'release_read', return_value=Reply(200, {}, json.dumps(release).encode())) as read:
            self.assertEqual(proof.main(['--origin', 'https://api.doctorcre.com']), 78)
            read.assert_called_once()


if __name__ == '__main__':
    unittest.main()
