#!/usr/bin/env python3
"""Offline contracts for the local production ledger proof."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('production_proof', ROOT / 'ops/migration-production-proof.py')
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proof
spec.loader.exec_module(proof)


class ProofTests(unittest.TestCase):
    def test_pending_suffix_passes(self):
        result = proof.ledger_proof({'0001_a.sql': 'a', '0003_c.sql': 'c'}, {'0001_a.sql': 'a'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['pending'], ['0003_c.sql'])

    def test_ledger_rejects_missing_changed_and_backfilled_files(self):
        result = proof.ledger_proof(
            {'0001_a.sql': 'wrong', '0002_backfill.sql': 'b', '0003_c.sql': 'c'},
            {'0001_a.sql': 'a', '0003_c.sql': 'c', '0004_missing.sql': 'd'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['missing'], ['0004_missing.sql'])
        self.assertEqual(result['checksum_mismatch'], ['0001_a.sql'])
        self.assertEqual(result['out_of_order'], ['0002_backfill.sql'])

    def test_same_number_pending_is_not_a_forward_suffix(self):
        self.assertFalse(proof.ledger_proof(
            {'0001_a.sql': 'a', '0001_b.sql': 'b'}, {'0001_a.sql': 'a'})['ok'])

    def test_empty_production_fails_closed(self):
        self.assertFalse(proof.ledger_proof({'0001_a.sql': 'a'}, {})['ok'])

    def test_hosted_refuses_before_credential_resolution(self):
        result = subprocess.run([sys.executable, str(ROOT / 'ops/migration-production-proof.py')],
                                env=dict(os.environ, GITHUB_ACTIONS='true'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('LOCAL-ONLY', result.stdout)

    def test_ci_reports_live_proofs_not_run_on_hosted_runners(self):
        source = (ROOT / 'ops/ci.sh').read_text()
        start = source.index('  excluded_reason()')
        end = source.index('  local eligible=', start)
        script = 'scope="$1"; PY="$2"; CARR_CI_PORTABLE_ONLY=0\n' + source[start:end] + '\nexcluded_reason migration-production-proof.py\n'
        result = subprocess.run(['bash', '-c', script, 'scope-fixture',
                                 str(ROOT / 'ops/config/ci-check-scope.json'), sys.executable],
                                env=dict(os.environ, GITHUB_ACTIONS='true'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LOCAL-ONLY:', result.stdout)

    def test_failed_subprocess_diagnostics_redact_credentials(self):
        response = subprocess.CompletedProcess([], 1, b'', b'password=placeholder postgres://reader:placeholder@localhost/example')  # ci-secret-scan: allow synthetic placeholder
        with patch.object(proof.subprocess, 'run', return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                proof.checked(['pg_dump'], {'PGPASSWORD': 'placeholder'}, 'fixture dump')
        self.assertNotIn('placeholder', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
