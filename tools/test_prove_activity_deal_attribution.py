"""Verify attribution proof contracts and refusal before production transport access."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-activity-deal-attribution.py'
FIXTURE = ROOT / 'tools/fixtures/activity-deal-attribution.json'
spec = importlib.util.spec_from_file_location('proof', RUNNER)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class AttributionProofTests(unittest.TestCase):
    def test_contract_checked_fixture_passes(self):
        self.assertEqual(proof.validate(json.loads(FIXTURE.read_text()))['direct_count'], 1)

    def test_missing_natural_evidence_fails(self):
        cohort = json.loads(FIXTURE.read_text())
        cohort['responses'] = cohort['responses'][:1]
        with self.assertRaises(ValueError):
            proof.validate(cohort)

    def test_production_parser_rejects_false_credit(self):
        cohort = json.loads(FIXTURE.read_text())
        cohort['responses'][1]['credited_activity_count'] = 1
        with self.assertRaises(subprocess.CalledProcessError):
            proof.validate(cohort)

    def test_canonical_mismatch_fails(self):
        cohort = json.loads(FIXTURE.read_text())
        cohort['canonical'][0]['deal_id'] = None
        with self.assertRaises(ValueError):
            proof.validate(cohort)

    def test_cli_pass_and_failure_exits(self):
        for extra, expected in [([], 0), (['--fixture-failure'], 78)]:
            r = subprocess.run(['python3', str(RUNNER), '--fixture', str(FIXTURE), *extra], capture_output=True)
            self.assertEqual(r.returncode, expected)

    def test_production_without_private_evidence_refuses(self):
        r = subprocess.run(['python3', str(RUNNER), '--production', '--natural'], capture_output=True)
        self.assertEqual(r.returncode, 78)
        self.assertNotIn(b'cookie', r.stdout)

    def test_missing_shared_contracts_refuse_before_credentials_or_calls(self):
        with patch.object(proof.Path, 'stat', side_effect=AssertionError('credential access')), \
                patch.object(proof, 'read', side_effect=AssertionError('HTTP read')), \
                patch.object(proof.subprocess, 'run', side_effect=AssertionError('process call')):
            self.assertEqual(proof.main(['--production', '--natural',
                                         '--session-headers-file', '/nonexistent/private',
                                         '--merge-sha', 'a' * 40]), 78)
        result = subprocess.run(['python3', str(RUNNER), '--production', '--natural'],
                                capture_output=True, text=True)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['state'], 'insufficient_evidence')
        self.assertEqual(receipt['missing_contracts'], [
            'release_reader_read_only_mcp_post', 'api_host_authenticated_proof_route'])
