#!/usr/bin/env python3
"""Synthetic subprocess fixtures for the research proof. Never reads business records."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-prospect-research-future-evidence.py'
READ_CAP_SECONDS = json.loads((ROOT / 'ops/config/ci-unit-durations.json').read_text())[
    'prospect_research_proof_measurement']['read_cap_seconds']
LEAD = '00000000-0000-4000-8000-000000000001'
FINDING = {'kind': 'verified', 'found': True, 'expired': False, 'proposes_correction': False,
           'source': 'https://example.test/synthetic', 'observed_at': '2000-01-01T00:00:00Z'}
RESEARCH = {'schema_version': 'prospect-research.v1', 'sent': False,
            'research': {'state': 'verified', 'coverage': 'recorded_findings_only',
                         'external_search_performed': False, 'evidence': [FINDING]}}


def stub():
    args = sys.argv[sys.argv.index('--stub') + 1:]
    if args == ['release']:
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        print(json.dumps({'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': sha}}))
    elif args == ['call', 'lead-board', '{}']:
        print(json.dumps({'leads': [{'id': LEAD}]}))
    elif args == ['call', 'prospect-research', json.dumps({'subject': LEAD})]:
        print(json.dumps(RESEARCH))
    else:
        sys.exit(2)


class ProofCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT, text=True,
                              capture_output=True, timeout=READ_CAP_SECONDS + 5)

    def scenario(self, research=None, board=None, release=None, failure=False, delay=0, flags=()):
        with tempfile.TemporaryDirectory(prefix='research-proof-') as directory:
            path = Path(directory) / 'stub.py'
            path.write_text('import json,sys,time\n'
                            f'time.sleep({delay!r})\n'
                            + ('sys.exit(1)\n' if failure else '')
                            + "args=sys.argv[2:]\n"
                            + (f'print(json.dumps({release!r}))\n' if release is not None else
                               f"sys.path.insert(0,{str(ROOT / 'tools')!r})\n"
                               'from test_prove_prospect_research_future_evidence import stub\n'
                               "if args == ['release']: stub(); sys.exit()\n"
                               f"print(json.dumps({(board if board is not None else {'leads': [{'id': LEAD}]})!r} if args[1] == 'lead-board' else {(research if research is not None else RESEARCH)!r}))\n"))
            return self.run_cli('--fixture', str(path), '--timeout-seconds', '5', '--max-records', '20', *flags)

    def test_help_and_committed_fixture_exact_flags(self):
        self.assertEqual(self.run_cli('--help').returncode, 0)
        result = self.run_cli('--fixture', '--timeout-seconds', '30', '--max-records', '20')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('records=1 findings=1 relevant=1 verified=1 reads=2', result.stdout)
        self.assertNotIn(LEAD, result.stdout)
        self.assertNotIn('example.test', result.stdout)

    def test_future_invalid_expired_and_false_cannot_verify(self):
        for patch in [{'observed_at': '2999-01-01T00:00:00Z'}, {'observed_at': 'bad'},
                      {'expired': True}, {'found': False}, {'source': ' '}]:
            with self.subTest(patch=patch):
                response = copy.deepcopy(RESEARCH)
                response['research']['evidence'][0].update(patch)
                self.assertNotEqual(self.scenario(research=response).returncode, 0)
                response['research']['state'] = 'needs_research'
                self.assertEqual(self.scenario(research=response).returncode, 0)

    def test_conflict_precedence_and_future_conflict(self):
        response = copy.deepcopy(RESEARCH)
        conflict = {**FINDING, 'kind': 'discrepancy'}
        response['research']['evidence'].append(conflict)
        self.assertNotEqual(self.scenario(research=response).returncode, 0)
        response['research']['state'] = 'needs_review'
        self.assertEqual(self.scenario(research=response).returncode, 0)
        conflict['observed_at'] = '2999-01-01T00:00:00Z'
        response['research']['state'] = 'verified'
        self.assertEqual(self.scenario(research=response).returncode, 0)

    def test_empty_findings_report_limited_coverage(self):
        response = copy.deepcopy(RESEARCH)
        response['research'].update(state='needs_research', evidence=[])
        result = self.scenario(research=response)
        self.assertEqual(result.returncode, 0)
        self.assertIn('COVERAGE limited', result.stdout)

    def test_auth_empty_invalid_contract_release_and_timeout_fail(self):
        for kwargs in [dict(failure=True), dict(board={'leads': []}), dict(board={'leads': [{'id': 'bad'}]}),
                       dict(research={'error': 'unauthorized'}), dict(research={'schema_version': 'bad'}),
                       dict(release={'ok': False}), dict(delay=0.2, flags=('--timeout-seconds', '0.05')),
                       dict(flags=('--merge-sha', 'f' * 40))]:
            with self.subTest(kwargs=kwargs):
                self.assertNotEqual(self.scenario(**kwargs).returncode, 0)

    def test_read_cap_and_budgets(self):
        board = {'leads': [{'id': f'00000000-0000-4000-8000-{i:012d}'} for i in range(25)]}
        result = self.scenario(board=board, flags=('--timeout-seconds', str(READ_CAP_SECONDS)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('records=20', result.stdout)
        self.assertIn('reads=21', result.stdout)
        for flags in [('--max-records', '21'), ('--max-records', '0'), ('--timeout-seconds', '31'), ('--timeout-seconds', '0')]:
            self.assertNotEqual(self.run_cli('--fixture', *flags).returncode, 0)


if __name__ == '__main__':
    if '--stub' in sys.argv:
        stub()
    else:
        unittest.main()
