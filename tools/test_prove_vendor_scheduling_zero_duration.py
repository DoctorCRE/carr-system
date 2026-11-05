"""Exercise the proof CLI with a synthetic wrapper and local release origin."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

RUNNER = Path(__file__).with_name('prove-vendor-scheduling-zero-duration.py')
SHA = 'a' * 40


class ProofCLI(unittest.TestCase):
    @contextmanager
    def fixture(self, state='unavailable', gaps=None, rows=None, change=None, fail=False, delay=0, release_change=False):
        now = datetime.now(timezone.utc)
        at = lambda hours: (now + timedelta(hours=hours)).isoformat()
        section = {'state': 'ready', 'observed_at': at(0), 'notes': [],
                   'sources': {'outlook_calendar': {'state': 'ready'}}}
        vendor = {'schema': 'vendor-meetings.v1', 'items': [], 'calendar': {**section, 'scheduling': {
            'state': state, 'items': [] if gaps is None else gaps,
            'window': {'start': at(0), 'end': at(24)}}}}
        agenda = {'calendar': {**section, 'items': rows if rows is not None else [
            {'title': 'PRIVATE SYNTHETIC IDENTITY', 'start': at(1), 'end': at(1), 'all_day': False}]}}
        if change:
            change(vendor, agenda, at)
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'git_sha': {'value': 'b' * 40 if release_change and len(requests) > 1 else SHA}}).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'data.json').write_text(json.dumps({'read-vendor-meetings': vendor, 'morning-brief': agenda}))
                wrapper = root / 'wrapper'
                wrapper.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
assert sys.argv[1] == 'call'
assert sys.argv[2] in {'read-vendor-meetings', 'morning-brief'}
assert json.loads(sys.argv[3]) == ({'view': 'research', 'limit': 1} if sys.argv[2] == 'read-vendor-meetings' else {})
''' + f'time.sleep({delay})\n' + ('print("PRIVATE AUTH FAILURE", file=sys.stderr)\nsys.exit(1)\n' if fail else
                    "print(json.dumps(json.loads((root / 'data.json').read_text())[sys.argv[2]]))\n"))
                wrapper.chmod(0o700)
                yield root, wrapper, f'http://127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def run_cli(self, expected, timeout=30, **kwargs):
        with self.fixture(**kwargs) as (root, wrapper, origin, requests):
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                     '--call-wrapper', str(wrapper), '--timeout-seconds', str(timeout)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            receipt = json.loads(result.stdout)
            self.assertNotIn('PRIVATE', result.stdout + result.stderr)
            self.assertLessEqual(receipt['record_reads'], 2)
            calls = (root / 'calls.jsonl').read_text().splitlines() if (root / 'calls.jsonl').exists() else []
            self.assertLessEqual(len(calls), 2)
            self.assertTrue(all(path == '/release' for path in requests))
            return receipt

    def test_natural_zero_duration_withholds_candidates(self):
        receipt = self.run_cli(0)
        self.assertEqual(receipt['release_sha'], SHA)
        self.assertEqual(receipt['eligible_records'], 1)
        self.assertEqual(receipt['passed_assertions'], 2)

    def test_regression_is_a_contradiction(self):
        self.assertEqual(self.run_cli(1, state='ready', gaps=[{'start': 'private'}])['status'], 'contradiction')
        self.run_cli(1, state='unavailable', gaps=[{'start': 'private'}])
        self.run_cli(1, state='ready')

    def test_normal_ready_and_empty_calendars_cannot_prove_regression(self):
        self.assertEqual(self.run_cli(78, state='ready', rows=[])['status'], 'insufficient_natural_evidence')
        self.run_cli(78, change=lambda v, a, at: a['calendar']['items'][0].update(end=at(2)))

    def test_stale_warning_missing_or_changed_source_is_insufficient(self):
        for change in [
            lambda v, a, at: a['calendar'].update(state='partial'),
            lambda v, a, at: v['calendar'].update(observed_at=at(-19)),
            lambda v, a, at: a['calendar'].update(observed_at=at(-1)),
            lambda v, a, at: a['calendar'].update(items=None),
            lambda v, a, at: a['calendar']['items'][0].update(all_day=True),
            lambda v, a, at: a['calendar']['items'][0].update(time_changed=True),
            lambda v, a, at: a['calendar']['items'][0].update(start=at(30), end=at(30)),
        ]:
            with self.subTest(change=change):
                self.run_cli(78, change=change)

    def test_excess_records_are_not_examined(self):
        self.run_cli(78, change=lambda v, a, at: a['calendar'].update(items=a['calendar']['items'] * 21))

    def test_release_change_is_not_a_passing_observation(self):
        self.assertEqual(self.run_cli(78, release_change=True)['status'], 'release_changed')

    def test_auth_failure_stops_without_retry_or_private_output(self):
        self.assertEqual(self.run_cli(78, fail=True)['record_reads'], 1)

    def test_total_deadline_stops_slow_wrapper(self):
        self.assertLessEqual(self.run_cli(78, timeout=1, delay=10)['record_reads'], 1)


if __name__ == '__main__':
    unittest.main()
