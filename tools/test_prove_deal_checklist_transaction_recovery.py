"""Exercise the exact proof CLI with synthetic subprocess replies."""
from pathlib import Path
import http.server
import importlib.util
import threading
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch
import json
import subprocess
import sys
import unittest

RUNNER = Path(__file__).with_name('prove-deal-checklist-transaction-recovery.py')
SPEC = importlib.util.spec_from_file_location('deal_proof', RUNNER)
assert SPEC is not None and SPEC.loader is not None
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


class ProofCliTest(unittest.TestCase):
    def test_release_transport_reaches_deal_reads(self):
        release = json.loads(RUNNER.with_name('fixtures').joinpath('proof-release-production.json').read_text())
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.headers.get('User-Agent', ''))
                self.send_response(403 if requests[-1].startswith('Python-urllib') else 200)
                self.end_headers()
                self.wfile.write(json.dumps(release).encode())

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        popen, open_url = subprocess.Popen, urllib.request.urlopen
        def execute(argv, **kwargs):
            if argv[0] == 'curl':
                argv = [*argv[:-1], origin + '/release']
            elif argv[0] == 'git':
                output = release['git_sha']['value'] if argv[1] == 'rev-parse' else ''
                argv = [sys.executable, '-c', 'print(' + repr(output) + ')']
            elif argv[1:3] in (['call', 'deal-room-board'], ['call', 'get-deal-room']):
                argv = [sys.executable, str(RUNNER), '--fixture-verb', 'current', *argv[2:]]
            return popen(argv, **kwargs)
        try:
            with patch.object(PROOF.subprocess, 'Popen', side_effect=execute), patch.object(
                    urllib.request, 'urlopen', side_effect=lambda *a, **kw: open_url(origin + '/release', **kw)):
                PROOF.prove(SimpleNamespace(timeout_seconds=5, expected_revision='HEAD', fixture=False, max_records=1))
            self.assertEqual(len(requests), 1)
            self.assertFalse(requests[0].startswith('Python-urllib'))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

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
