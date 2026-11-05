"""Test the declared CLI with synthetic HTTP and executable wrapper fixtures."""
import contextlib
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

RUNNER = Path(__file__).with_name('prove-lease-comparison-tiny-rate-stability.py')


def economics(rate):
    interest = 33224.6 if rate == 0.06 else 0
    option = dict(annual_operating_expenses=5000, cost_of_rent=125000, free_base_rent=0,
                  free_operating_expenses=0, total_free_rent_and_opex=0,
                  estimated_buildout_cost=100000, landlord_ti_allowance=0,
                  tenant_ti_contribution=100000, total_interest_on_tenant_ti=interest,
                  total_cost_of_buildout=100000 + interest,
                  base_rent_schedule=[dict(year=i, per_sf=20, annual_base_rent=20000)
                                      for i in range(1, 6)])
    return dict(calculation_basis='carr-lease-comparison-5vs10-v1', long_term=option,
                short_term=option.copy(), comparison=dict(rent_cost_difference=0,
                lost_concessions_difference=0, buildout_cost_difference=0,
                additional_cost_of_shorter_term=0,
                additional_years_of_long_term_year_one_rent=0, direction='equivalent'))


class ProofTest(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, release=None, status=200):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(release if release is not None else
                                            dict(git_sha='a' * 40)).encode())

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                yield Path(tmp), f'http://127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def run_cli(self, directory, origin, mode='pass', timeout='30'):
        payload = {str(rate): economics(rate) for rate in (1e-20, 0, 0.06)}
        if mode in ('null', 'infinity'):
            payload['1e-20']['long_term']['total_interest_on_tenant_ti'] = (
                None if mode == 'null' else float('inf'))
        if mode == 'wrong':
            payload['1e-20']['long_term']['total_cost_of_buildout'] = 100001
        if mode == 'ordinary':
            payload['0.06']['long_term']['total_interest_on_tenant_ti'] = 0
        if mode == 'malformed':
            payload = {key: {} for key in payload}
        (directory / 'payload.json').write_text(json.dumps(payload))
        wrapper = directory / 'wrapper'
        calls = directory / 'calls.jsonl'
        wrapper.write_text(f'''#!{sys.executable}
import json, pathlib, sys, time
assert sys.argv[1:3] == ['call', 'compare-lease-terms']
data = json.loads(sys.argv[3])
with pathlib.Path({str(calls)!r}).open('a') as log:
    log.write(json.dumps(data) + '\\n')
if {mode!r} == 'failed': sys.exit(1)
if {mode!r} == 'timeout': time.sleep(10)
if {mode!r} == 'json': print('invalid'); sys.exit(0)
payload = json.loads(pathlib.Path({str(directory / 'payload.json')!r}).read_text())
print(json.dumps(payload[str(data['financing']['annual_interest_rate'])]))
''')
        wrapper.chmod(0o700)
        result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                 '--call-wrapper', str(wrapper), '--timeout-seconds', timeout],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.stderr, '')
        return result, ([json.loads(row) for row in calls.read_text().splitlines()]
                        if calls.exists() else [])

    def test_success_uses_exact_inputs_and_budget(self):
        with self.fixture() as (directory, origin, requests):
            result, calls = self.run_cli(directory, origin)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(requests, ['/release'])
            self.assertEqual([c['financing']['annual_interest_rate'] for c in calls], [1e-20, 0, 0.06])
            for call in calls:
                self.assertEqual(call['square_feet'], 1000)
                self.assertEqual(call['financing']['amortization_years'], 10)
                for side, years in [('long_term', 10), ('short_term', 5)]:
                    self.assertEqual(call[side], dict(label=f'{years} Year Lease', term_years=years,
                        base_rent_per_sf=[20]*5, operating_expenses_per_sf=5, free_rent_months=0,
                        free_opex_months=0, buildout_cost_per_sf=100, landlord_ti_per_sf=0))
            self.assertEqual(json.loads(result.stdout)['release']['git_sha'], 'a'*40)

    def test_contradictions(self):
        for mode in ('null', 'infinity', 'wrong', 'ordinary'):
            with self.subTest(mode=mode), self.fixture() as (directory, origin, _):
                result, calls = self.run_cli(directory, origin, mode)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertLessEqual(len(calls), 3)
                self.assertEqual(json.loads(result.stdout)['status'], 'contradiction')

    def test_wrapper_unavailability(self):
        for mode in ('failed', 'json', 'malformed'):
            with self.subTest(mode=mode), self.fixture() as (directory, origin, _):
                result, calls = self.run_cli(directory, origin, mode)
                self.assertEqual(result.returncode, 78, result.stdout)
                self.assertEqual(len(calls), 1)

    def test_release_failure_prevents_calls(self):
        for release, status in [({}, 200), ({'git_sha':'short'}, 200), ({}, 503), ({}, 302)]:
            with self.subTest(status=status), self.fixture(release, status) as (directory, origin, requests):
                result, calls = self.run_cli(directory, origin)
                self.assertEqual(result.returncode, 78, result.stdout)
                self.assertEqual(calls, [])
                self.assertEqual(requests, ['/release'])

    def test_total_deadline_stops_wrapper_without_retry(self):
        with self.fixture() as (directory, origin, requests):
            start = time.monotonic()
            result, calls = self.run_cli(directory, origin, 'timeout', '0.2')
            self.assertEqual(result.returncode, 78, result.stdout)
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(requests, ['/release'])


if __name__ == '__main__':
    unittest.main()
