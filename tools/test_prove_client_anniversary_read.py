"""Exercise proof admission through the production response parser."""
import json
import importlib.util
from copy import deepcopy
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-client-anniversary-read.py'
FIXTURE = ROOT / 'tools/fixtures/client-anniversary-read.json'


class ProofTest(unittest.TestCase):
    def test_fixture_pass_and_failure(self):
        for flags, code in [([], 0), (['--fixture-failure'], 78)]:
            result = subprocess.run(['python3', str(RUNNER), '--fixture', str(FIXTURE), *flags],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            self.assertNotIn('Synthetic', result.stdout)

    def test_missing_natural_evidence_refuses(self):
        result = subprocess.run(['python3', str(RUNNER), '--production', '--natural'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(json.loads(result.stdout)['state'], 'insufficient_evidence')

    def test_canonical_mismatch_and_empty_are_insufficient(self):
        spec = importlib.util.spec_from_file_location('anniversary_proof', RUNNER)
        proof = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proof)
        fixture = json.loads(FIXTURE.read_text())
        changed = deepcopy(fixture['reference'])
        changed['closed_on'] = '2019-01-01'
        with self.assertRaises(ValueError):
            proof.observe(fixture['response'], changed)
        empty = deepcopy(fixture['response'])
        empty.update(state='empty', rows=[], next_offset=None)
        with self.assertRaises(ValueError):
            proof.observe(empty, fixture['reference'])
        bad = deepcopy(fixture['response'])
        bad['rows'][0]['years'] += 1
        with self.assertRaises(ValueError):
            proof.observe(bad, fixture['reference'])

    def test_natural_reference_waits_for_public_post_contract(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import tempfile
        import io
        spec = importlib.util.spec_from_file_location('anniversary_proof', RUNNER)
        proof = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proof)
        fixture = json.loads(FIXTURE.read_text())
        current = datetime.now(ZoneInfo('America/Chicago')).date().isoformat()
        fixture['response']['as_of'] = current
        row = fixture['response']['rows'][0]
        row.update(closed_on='2020' + current[4:], anniversary_on=current, days_until=0,
                   years=int(current[:4]) - 2020)
        reads = []
        def read(url, **kwargs):
            reads.append(url)
            body = {'source_commit': 'a' * 40} if url.endswith('/release') else fixture['response']
            return SimpleNamespace(status=200, body=json.dumps(body).encode())
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'session'
            cookie.write_text('synthetic-session')
            cookie.chmod(0o600)
            output = io.StringIO()
            with patch.object(proof, 'read_http', read), patch('sys.stdout', output), patch('sys.argv',
                    [str(RUNNER), '--production', '--natural', '--active-cookie-file', str(cookie), '--merge-sha', 'a'*40]):
                self.assertEqual(proof.main(), 78)
            self.assertEqual(json.loads(output.getvalue())['missing_contract'],
                             'release_reader POST support for read-only MCP verbs')
            self.assertEqual(len(reads), 2)
