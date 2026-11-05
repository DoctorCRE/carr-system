"""Run the proof CLI against synthetic executable and HTTP ports only."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-meeting-prep-savepoint-lifetime.py'
SHA = 'a' * 40
SOURCE_PATH = '/DoctorCRE/carr-system/' + SHA + '/mcp-server/src/calendar-meeting-prep.js'
SOURCE = (ROOT / 'mcp-server/src/calendar-meeting-prep.js').read_text()
NATURAL = {'state': 'ready', 'sources': {'outlook_calendar': {'state': 'current'}},
           'notes': [], 'items': [{'title': 'Private synthetic meeting', 'possible_deals': []}]}


@contextmanager
def server(routes):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            status, data = routes.get(self.path, (404, b''))
            self.send_response(status)
            if status == 302:
                self.send_header('Location', '/other')
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(http.server_port), requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


class ProofCliTests(unittest.TestCase):
    def invoke(self, *, source=SOURCE, natural=NATURAL, release=None, status=200,
               wrapper_code=0, timeout=5, wrapper_sleep=0, source_status=200):
        release = release if release is not None else {'git_sha': {'value': SHA}, 'env': {'value': 'production'}}
        routes = {'/release': (status, json.dumps(release).encode()), SOURCE_PATH: (source_status, source.encode())}
        with tempfile.TemporaryDirectory() as folder, server(routes) as (origin, requests):
            wrapper = Path(folder) / 'call-wrapper'
            log = Path(folder) / 'calls.json'
            wrapper.write_text('#!' + sys.executable + '\nimport json,sys,time\n'
                               + f'open({str(log)!r},"w").write(json.dumps(sys.argv[1:]))\n'
                               + f'time.sleep({wrapper_sleep})\n'
                               + f'print({json.dumps(natural)!r})\nsys.exit({wrapper_code})\n')
            wrapper.chmod(0o700)
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                     '--call-wrapper', str(wrapper), '--timeout-seconds', str(timeout)],
                                    capture_output=True, text=True, timeout=10)
            calls = json.loads(log.read_text()) if log.exists() else None
        return result, requests, calls

    def test_separate_natural_and_release_bound_synthetic_evidence(self):
        result, requests, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['release_sha'], SHA)
        self.assertTrue(evidence['natural_read']['structurally_valid'])
        self.assertTrue(evidence['release_bound_synthetic']['subsequent_query'])
        self.assertFalse(evidence['live_proven'])
        self.assertEqual(requests, ['/release', SOURCE_PATH])
        self.assertEqual(calls, ['call', 'calendar-meeting-prep', '{}'])
        self.assertNotIn('Private synthetic', result.stdout + result.stderr)

    def test_old_released_savepoint_is_contradiction(self):
        line = '    if (c.inTransaction) await c.query("release savepoint calendar_meeting_prep");\n'
        old = SOURCE.replace(line, '').replace('    const byId', line + '    const byId')
        result, _, _ = self.invoke(source=old)
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_invalid_natural_shape_is_contradiction(self):
        result, _, _ = self.invoke(natural={'state': 'ready', 'items': 'wrong'})
        self.assertEqual(result.returncode, 1)

    def test_unavailable_natural_evidence_stops_before_source(self):
        result, requests, _ = self.invoke(natural={'state': 'unavailable', 'items': []})
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release'])

    def test_wrapper_failure_is_unavailable(self):
        result, requests, _ = self.invoke(wrapper_code=1)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release'])

    def test_source_unavailable_retains_release_and_natural_identity(self):
        result, requests, _ = self.invoke(source_status=404)
        self.assertEqual(result.returncode, 78)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['release_sha'], SHA)
        self.assertTrue(evidence['natural_read']['structurally_valid'])
        self.assertEqual(requests, ['/release', SOURCE_PATH])

    def test_oversized_wrapper_output_is_unavailable(self):
        result, requests, _ = self.invoke(natural={'padding': 'x' * 1048577})
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release'])

    def test_bad_release_is_unavailable(self):
        result, requests, calls = self.invoke(release={'git_sha': {'value': 'main'}})
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release'])
        self.assertIsNone(calls)

    def test_staging_release_is_unavailable(self):
        result, _, _ = self.invoke(release={'git_sha': {'value': SHA}, 'env': {'value': 'staging'}})
        self.assertEqual(result.returncode, 78)

    def test_redirect_is_unavailable_without_following(self):
        result, requests, _ = self.invoke(status=302)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release'])

    def test_source_size_bound(self):
        result, requests, _ = self.invoke(source='x' * 1048577)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release', SOURCE_PATH])

    def test_unexpected_dependency_fails_closed(self):
        result, _, _ = self.invoke(source="import 'https://untrusted.invalid/evil.js';\n" + SOURCE)
        self.assertEqual(result.returncode, 1)

    def test_total_deadline_stops_wrapper(self):
        result, requests, _ = self.invoke(timeout=0.2, wrapper_sleep=2)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(requests, ['/release'])

    def test_cli_refuses_budget_over_thirty_seconds(self):
        result, requests, _ = self.invoke(timeout=31)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
