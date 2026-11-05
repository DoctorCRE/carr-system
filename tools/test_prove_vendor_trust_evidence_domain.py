"""Exercise the exact proof CLI with synthetic sessions and HTTP origins."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

RUNNER = Path(__file__).with_name('prove-vendor-trust-evidence-domain.py')
RATED = {'computed_tier': 'Proven', 'coverage_verified_at': '2020-01-01T00:00:00Z',
         'deals_worked': 8, 'deals_referred': 1, 'won': 7, 'lost': 1, 'win_rate': 7/8}
UNVERIFIED = {'computed_tier': 'Unrated', 'coverage_verified_at': None,
              'deals_worked': None, 'deals_referred': None, 'won': None, 'lost': None, 'win_rate': None}


@contextmanager
def fixture(rows=None, pages=1, status=200, delay=0, redirect=False, release=True):
    requests = []
    rows = [RATED, UNVERIFIED] if rows is None else rows

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get('Cookie')))
            if self.path == '/app-release':
                value = {'service': 'doctorcre-app', 'environment': 'production', 'source_commit': 'a'*40 if release else None}
            elif self.path == '/release':
                value = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'b'*40}}
            else:
                if delay:
                    time.sleep(delay)
                if redirect:
                    self.send_response(302)
                    self.send_header('Location', '/auth/login')
                    self.end_headers()
                    return
                page = int(self.path.rsplit('=', 1)[1])
                value = {'page': page, 'page_size': 25, 'page_count': pages,
                         'rows': [{'name': 'PRIVATE_VENDOR', 'relationship': row} for row in rows]}
                if self.headers.get('Cookie') != 'session=SYNTHETIC_SECRET':
                    self.send_response(401)
                    self.end_headers()
                    return
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(json.dumps(value).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ProofCLI(unittest.TestCase):
    def run_cli(self, base, cookies=True, timeout=30, domain='127.0.0.1'):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / 'session.json'
            if cookies:
                session.write_text(json.dumps({'cookies': [{'name': 'session', 'value': 'SYNTHETIC_SECRET',
                    'domain': domain, 'path': '/', 'secure': False, 'expires': -1}]}))
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', base, '--carr-origin', base,
                '--storage-state', str(session), '--timeout-seconds', str(timeout)],
                capture_output=True, text=True, timeout=5)
        for private in ['PRIVATE_VENDOR', 'SYNTHETIC_SECRET', 'session=']:
            self.assertNotIn(private, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def test_success_and_cookies_only_on_authenticated_route(self):
        with fixture() as (base, requests):
            code, result = self.run_cli(base)
        self.assertEqual(code, 0)
        self.assertEqual((result['rated'], result['unverified'], result['rows']), (1, 1, 2))
        self.assertEqual(result['app_release'], 'a'*40)
        self.assertEqual(result['carr_release'], 'b'*40)
        self.assertEqual([cookie for _, cookie in requests[:2]], [None, None])
        self.assertIn('contract=vendor-directory.v1&scope=team&page=1', requests[2][0])

    def test_contradictions(self):
        for update in [{'coverage_verified_at': 'not-a-date'}, {'coverage_verified_at': '2099-01-01T00:00:00Z'},
                       {'coverage_verified_at': '2026-02-30'}, {'lost': -1}, {'won': 9},
                       {'deals_worked': 1.5}, {'won': True}, {'deals_referred': 9007199254740992},
                       {'win_rate': 1.1}, {'win_rate': float('nan')}, {'win_rate': 0.1}]:
            with self.subTest(update=update), fixture([{**RATED, **update}, UNVERIFIED]) as (base, _):
                self.assertEqual(self.run_cli(base)[0], 1)
        with fixture([RATED, {**UNVERIFIED, 'won': 0}]) as (base, _):
            self.assertEqual(self.run_cli(base)[0], 1)

    def test_missing_evidence_and_authorization(self):
        for rows in [[], [RATED], [UNVERIFIED], [None]]:
            with self.subTest(rows=rows), fixture(rows) as (base, _):
                self.assertEqual(self.run_cli(base)[0], 78)
        for options in [{'status': 401}, {'status': 503}, {'redirect': True}, {'release': False}]:
            with self.subTest(options=options), fixture(**options) as (base, _):
                self.assertEqual(self.run_cli(base)[0], 78)
        with fixture() as (base, requests):
            self.assertEqual(self.run_cli(base, cookies=False)[0], 78)
            self.assertEqual(len(requests), 0)
            self.assertEqual(self.run_cli(base, domain='other.invalid')[0], 78)

    def test_total_timeout(self):
        with fixture(delay=0.3) as (base, requests):
            self.assertEqual(self.run_cli(base, timeout=0.1)[0], 78)
            self.assertEqual(len(requests), 3)

    def test_bounded_pagination(self):
        with fixture([RATED, UNVERIFIED], pages=20) as (base, requests):
            code, result = self.run_cli(base)
        self.assertEqual(code, 0)
        self.assertEqual((result['pages'], result['rows'], len(requests)), (5, 10, 7))
        with fixture([RATED]*24 + [UNVERIFIED], pages=20) as (base, requests):
            code, result = self.run_cli(base)
        self.assertEqual(code, 0)
        self.assertEqual((result['pages'], result['rows'], len(requests)), (4, 100, 6))

    def test_zero_resolved_outcomes(self):
        with fixture([{**RATED, 'computed_tier': 'Trial', 'won': 0, 'lost': 0, 'win_rate': None}, UNVERIFIED]) as (base, _):
            self.assertEqual(self.run_cli(base)[0], 0)


if __name__ == '__main__':
    unittest.main()
