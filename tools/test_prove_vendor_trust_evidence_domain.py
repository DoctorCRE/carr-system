"""Exercise the exact proof CLI with synthetic sessions and HTTP origins."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import http.cookiejar
from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MARKER = json.loads((ROOT / 'lib/release-reader-marker.json').read_text())
# Recorded synthetic /app-release response from the real app handler at
# jbookout/doctorcre-app 03cf3a04664f842c3ff5d01b2424cc6ba21a3dba.
APP_RELEASE = {'service': 'doctorcre-app',
 'environment': 'production',
 'source_commit': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
 'provider_version_id': None,
 'provider_version_tag': None,
 'provider_version_created_at': None,
 'carr_contract': {'schema': 'doctorcre-carr-interface.v1', 'version': '1.56.0'},
 'route_contract': {'schema': 'doctorcre-app-routes.v1', 'version': '1.21.0'}}

RUNNER = Path(__file__).with_name('prove-vendor-trust-evidence-domain.py')
RATED = {'computed_tier': 'Proven', 'coverage_verified_at': '2020-01-01T00:00:00Z',
         'deals_worked': 8, 'deals_referred': 1, 'won': 7, 'lost': 1, 'win_rate': 7/8}
UNVERIFIED = {'computed_tier': 'Unrated', 'coverage_verified_at': None,
              'deals_worked': None, 'deals_referred': None, 'won': None, 'lost': None, 'win_rate': None}


@lru_cache(maxsize=8)
def endpoint_responses(pages, width):
    source = r"""
import {readBusinessList, parseBusinessQuery} from './mcp-server/src/workspace-business-read.js';
import {buildRelease} from './mcp-server/src/release.js';
const rated = {coverage_verified_at:'2020-01-01T00:00:00Z', deals_worked:8,
  deals_referred:1, won:7, lost:1, first_worked_at:'2020-01-01T00:00:00Z',
  last_contacted_at:'2026-01-01T00:00:00Z'};
const [pages, width] = process.argv.slice(1).map(Number);
const responses = [];
for (let page=1; page<=Math.min(pages,5); page++) {
  const rows = Array.from({length:width}, (_,i)=>({id:`synthetic-${i}`,
    name:'PRIVATE_VENDOR', relationship:i===width-1 ? {} : rated}));
  const client = {query: async text => ({rows:text.startsWith('with filtered')
    ? [{total_count:pages===1 ? width : pages*25, viewer_owner_resolved:true, rows}]
    : [{}]})};
  responses.push(await readBusinessList({client, actor:{slug:'joe'},
    query:parseBusinessQuery('vendors',new URLSearchParams({contract:'vendor-directory.v1',scope:'team',page:String(page)}),'joe'),
    correlationId:'synthetic-proof-contract', now:()=>new Date('2026-01-02T00:00:00Z')}));
}
const release = await buildRelease({env:{CARR_ENV:'production',GIT_SHA:'b'.repeat(40)},
  sql:async()=>[],verbCount:1,now:()=>new Date('2026-01-02T00:00:00Z')});
console.log(JSON.stringify({responses,release}));
"""
    result = subprocess.run(['node', '--input-type=module', '-e', source, str(pages), str(width)],
                            cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


@contextmanager
def fixture(rows=None, pages=1, status=200, delay=0, redirect=False, release=True, require_identity=False):
    requests = []
    generated = endpoint_responses(pages, 2 if rows is None else len(rows))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get('Cookie')))
            if require_identity and (self.headers.get('User-Agent') !=
                    'doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)' or
                    self.headers.get(MARKER['header']) != MARKER['value']):
                self.send_error(403)
                return
            if self.path == '/app-release':
                value = copy.deepcopy(APP_RELEASE)
                if not release:
                    value['source_commit'] = None
            elif self.path == '/release':
                value = generated['release']
            else:
                if delay:
                    time.sleep(delay)
                if redirect:
                    self.send_response(302)
                    self.send_header('Location', '/auth/login')
                    self.end_headers()
                    return
                page = int(self.path.rsplit('=', 1)[1])
                value = copy.deepcopy(generated['responses'][page - 1])
                # Inject contradictions only after the real producer has built its response.
                if rows is not None:
                    for row, stats in zip(value['rows'], rows):
                        row['relationship'] = stats
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

    def test_shared_transport_identity_on_every_read(self):
        with fixture(require_identity=True) as (base, requests):
            self.assertEqual(self.run_cli(base)[0], 0)
        self.assertEqual(len(requests), 3)

    def test_shared_reader_cookie_session_and_redirect_refusal(self):
        sys.path.insert(0, str(ROOT))
        from lib.release_reader import read
        jar = http.cookiejar.CookieJar()
        jar.set_cookie(http.cookiejar.Cookie(
            0, 'session', 'SYNTHETIC_SECRET', None, False, '127.0.0.1', False,
            False, '/', True, False, None, True, None, None, {}, False))
        with fixture(require_identity=True) as (base, requests):
            self.assertEqual(read(base + '/app-release').status, 200)
            self.assertEqual(read(base + '/api/v1/business/vendors?contract=vendor-directory.v1&scope=team&page=1',
                                  cookie_jar=jar).status, 200)
            self.assertEqual(read(base + '/release').status, 200)
        self.assertEqual([cookie for _, cookie in requests],
                         [None, 'session=SYNTHETIC_SECRET', None])
        with fixture(redirect=True) as (base, requests):
            self.assertEqual(read(base + '/api/v1/business/vendors?contract=vendor-directory.v1&scope=team&page=1', cookie_jar=jar).status, 302)
        self.assertEqual(len(requests), 1)

    def test_fixture_endpoint_contract(self):
        with fixture() as (base, _):
            request = urllib.request.Request(base + '/api/v1/business/vendors?contract=vendor-directory.v1&scope=team&page=1',
                                             headers={'Cookie': 'session=SYNTHETIC_SECRET'})
            with urllib.request.urlopen(request) as response:
                value = json.load(response)
        self.assertEqual(value['dataset'], 'vendors')
        self.assertEqual(value['query']['contract'], 'vendor-directory.v1')
        self.assertEqual(value['total'], len(value['rows']))
        self.assertEqual(value['source']['observed_at'], '2026-01-02T00:00:00.000Z')
        self.assertIn('facets', value)
        self.assertIn('partial', value)

    def test_contradictions(self):
        for update in [{'coverage_verified_at': 'not-a-date'}, {'coverage_verified_at': '2099-01-01T00:00:00Z'},
                       {'coverage_verified_at': '2026-02-30'}, {'lost': -1}, {'won': 9},
                       {'deals_worked': 1.5}, {'won': True}, {'deals_referred': 9007199254740992},
                       {'win_rate': 1.1}, {'win_rate': float('nan')}, {'win_rate': 0.1}]:
            with self.subTest(update=update), fixture([{**RATED, **update}, UNVERIFIED]) as (base, _):
                self.assertEqual(self.run_cli(base)[0], 1)
        with fixture([RATED, {**UNVERIFIED, 'won': 0}]) as (base, _):
            self.assertEqual(self.run_cli(base)[0], 1)

    def test_timezone_offset_components(self):
        for offset in ['+00:99', '-00:99', '+23:60', '-23:60', '+24:00', '-24:00']:
            with self.subTest(offset=offset), fixture([
                    {**RATED, 'coverage_verified_at': '2020-01-01T00:00:00' + offset},
                    UNVERIFIED]) as (base, _):
                code, result = self.run_cli(base)
                self.assertEqual(code, 1)
                self.assertEqual(result['reason'], 'rated_evidence_invalid')
        for offset in ['+00:59', '-00:59', '+23:59', '-23:59']:
            with self.subTest(offset=offset), fixture([
                    {**RATED, 'coverage_verified_at': '2020-01-01T00:00:00' + offset},
                    UNVERIFIED]) as (base, _):
                self.assertEqual(self.run_cli(base)[0], 0)

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
        with fixture(delay=2) as (base, requests):
            self.assertEqual(self.run_cli(base, timeout=1)[0], 78)
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
