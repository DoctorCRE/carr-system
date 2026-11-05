"""Exercise the exact proof CLI with synthetic subprocess replies."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

RUNNER = Path(__file__).with_name('prove-deal-checklist-transaction-recovery.py')


class ProofCliTest(unittest.TestCase):
    def run_cli(self, *flags):
        return subprocess.run([sys.executable, str(RUNNER), *flags], capture_output=True, text=True, timeout=10)

    def test_help(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--timeout-seconds', result.stdout)

    def test_exact_fixture_flags_and_honest_states(self):
        for state in ('current', 'unavailable', 'source_changed'):
            with self.subTest(state=state):
                result = self.run_cli('--fixture', '--timeout-seconds', '30', '--max-records', '3', '--scenario', state)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {'deal_reads': 3, 'record_reads': 4, 'states': [state] * 3})
                self.assertNotIn('11111111', result.stdout)

    def test_failures_return_nonzero(self):
        for scenario in ('auth', 'empty', 'invalid', 'aborted', 'wrong-id', 'wrong-release', 'dishonest', 'bad-checklist', 'timeout'):
            with self.subTest(scenario=scenario):
                result = self.run_cli('--fixture', '--timeout-seconds', '0.5', '--max-records', '3', '--scenario', scenario)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr.strip(), 'proof_failed')
                self.assertEqual(result.stdout, '')

    def test_record_cap(self):
        result = self.run_cli('--fixture', '--max-records', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['record_reads'], 2)

    def test_invalid_budgets_and_modes(self):
        for flags in ([], ['--live', '--fixture'], ['--fixture', '--max-records', '4'],
                      ['--fixture', '--timeout-seconds', '31'], ['--fixture', '--timeout-seconds', '0'],
                      ['--live', '--scenario', 'auth']):
            self.assertEqual(self.run_cli(*flags).returncode, 2)


if __name__ == '__main__':
    unittest.main()
