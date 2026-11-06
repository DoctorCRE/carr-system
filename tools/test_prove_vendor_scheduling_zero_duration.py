"""Exercise the proof CLI with a synthetic wrapper and local release origin."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from proof_release_fixture import ReleaseFixtureHandler
import json
import importlib.util
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

RUNNER = Path(__file__).with_name('prove-vendor-scheduling-zero-duration.py')
SPEC = importlib.util.spec_from_file_location('vendor_proof', RUNNER)
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)
SHA = 'a' * 40


class ProofCLI(unittest.TestCase):
    @contextmanager
    def fixture(self, state='unavailable', gaps=None, rows=None, change=None, fail=False, release_change=False, registry_owner=None):
        now = datetime.now(timezone.utc)
        at = lambda hours: (now + timedelta(hours=hours)).isoformat(timespec='milliseconds')
        section = {'state': 'ready', 'observed_at': at(0), 'notes': [],
                   'sources': {'outlook_calendar': {'state': 'ready'}}}
        vendor = {'schema': 'vendor-meetings.v1', 'items': [], 'calendar': {**section, 'scheduling': {
            'state': state, 'items': [] if gaps is None else gaps,
            'window': {'start': at(0), 'end': at(24)}}}}
        agenda = {'sections': {'calendar': {**section, 'items': rows if rows is not None else [
            {'title': 'PRIVATE SYNTHETIC IDENTITY', 'start': at(1), 'end': at(1), 'all_day': False}]}}}
        if change:
            change(vendor, agenda, at)
        requests = []

        class Handler(ReleaseFixtureHandler):
            def release_GET(self):
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
                wrapper.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, sys
root = pathlib.Path(__file__).parent
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
assert sys.argv[1] == 'call'
assert sys.argv[2] in {'read-vendor-meetings', 'morning-brief'}
assert json.loads(sys.argv[3]) == ({'view': 'research', 'limit': 1} if sys.argv[2] == 'read-vendor-meetings' else {})
''' + ('print("PRIVATE AUTH FAILURE", file=sys.stderr)\nsys.exit(1)\n' if fail else
                    "print(json.dumps(json.loads((root / 'data.json').read_text())[sys.argv[2]]))\n"))
                if registry_owner:
                    source = RUNNER.parent.parent / 'mcp-server' / 'src'
                    registry = root / 'registry.mjs'
                    registry.write_text(f"import {{ agentActorForToken }} from {json.dumps((source / 'identity.js').as_uri())};\n" +
                        f"import {{ executeRegisteredTool }} from {json.dumps((source / 'tools.js').as_uri())};\n" +
                        f"const owner = {json.dumps(registry_owner)};\n" +
                        f"const data = {json.dumps({'agenda': agenda})};\n" +
                        "const actor = agentActorForToken('Bearer synthetic-only-token', JSON.stringify({[owner + '-local']: 'synthetic-only-token'}), 'local-token');\n" +
                        "const c = {async query(sql, params) {\n" +
                        "  if (sql.includes('with candidates')) return {rows: [{total: 0, items: []}]};\n" +
                        "  if (sql.includes('from board_snapshot') && params[2] === 'calendar-agenda') {\n" +
                        "    if (params[1] !== owner) throw Error('wrong sponsor');\n" +
                        "    return {rows: [{snapshot_json: {schema: 'carr-calendar-agenda.v1', observed_at: data.agenda.sections.calendar.observed_at, warnings: [], items: data.agenda.sections.calendar.items}}]};\n" +
                        "  } return {rows: []};\n" +
                        "}};\n" +
                        "const value = await executeRegisteredTool(c, actor, process.argv[3], JSON.parse(process.argv[4]));\n" +
                        "console.log(JSON.stringify(value));\n")
                    wrapper.write_text('#!' + sys.executable + '\nimport os, sys\n' +
                        f"os.execv({shutil.which('node')!r}, ['node', {str(registry)!r}, *sys.argv[1:]])\n")
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

    def test_real_local_identity_can_observe_zero_duration(self):
        for owner in ('joe', 'dell'):
            with self.subTest(owner=owner):
                receipt = self.run_cli(0, registry_owner=owner)
                self.assertEqual(receipt['eligible_records'], 1)
                self.assertEqual(receipt['passed_assertions'], 2)

    def test_regression_is_a_contradiction(self):
        self.assertEqual(self.run_cli(1, state='ready', gaps=[{'start': 'private'}])['status'], 'contradiction')
        self.run_cli(1, state='unavailable', gaps=[{'start': 'private'}])
        self.run_cli(1, state='ready')

    def test_normal_ready_and_empty_calendars_cannot_prove_regression(self):
        self.assertEqual(self.run_cli(78, state='ready', rows=[])['status'], 'insufficient_natural_evidence')
        self.run_cli(78, change=lambda v, a, at: a['sections']['calendar']['items'][0].update(end=at(2)))

    def test_stale_warning_missing_or_changed_source_is_insufficient(self):
        for change in [
            lambda v, a, at: a['sections']['calendar'].update(state='partial'),
            lambda v, a, at: v['calendar'].update(observed_at=at(-19)),
            lambda v, a, at: a['sections']['calendar'].update(observed_at=at(-1)),
            lambda v, a, at: a['sections']['calendar'].update(items=None),
            lambda v, a, at: a['sections']['calendar']['items'][0].update(all_day=True),
            lambda v, a, at: a['sections']['calendar']['items'][0].update(time_changed=True),
            lambda v, a, at: a['sections']['calendar']['items'][0].update(start=at(30), end=at(30)),
        ]:
            with self.subTest(change=change):
                self.run_cli(78, change=change)

    def test_excess_records_are_not_examined(self):
        self.run_cli(78, change=lambda v, a, at: a['sections']['calendar'].update(items=a['sections']['calendar']['items'] * 21))

    def test_release_change_is_not_a_passing_observation(self):
        self.assertEqual(self.run_cli(78, release_change=True)['status'], 'release_changed')

    def test_auth_failure_stops_without_retry_or_private_output(self):
        self.assertEqual(self.run_cli(78, fail=True)['record_reads'], 1)

    def test_stdout_cap_stops_wrapper_before_oversized_output_completes(self):
        with self.fixture() as (root, wrapper, origin, requests):
            wrapper.write_text('#!' + sys.executable + '\n' +
                "import pathlib, sys\n" +
                "sys.stdout.buffer.write(b'x' * (2 * 1048576))\n" +
                "sys.stdout.buffer.flush()\n" +
                "pathlib.Path(__file__).with_name('oversized-completed').touch()\n")
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                     '--call-wrapper', str(wrapper), '--timeout-seconds', '30'],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 78)
            self.assertEqual(json.loads(result.stdout)['record_reads'], 1)
            self.assertFalse((root / 'oversized-completed').exists(),
                             'wrapper must stop before draining two MiB')

    def test_stdout_exact_byte_cap_is_allowed(self):
        with self.fixture() as (root, wrapper, origin, requests):
            wrapper.write_text('#!' + sys.executable + '\n' +
                "import json, pathlib, sys\n" +
                "root = pathlib.Path(__file__).parent\n" +
                "value = json.dumps(json.loads((root / 'data.json').read_text())[sys.argv[2]])\n" +
                "sys.stdout.write(value + ' ' * (1048576 - len(value.encode())))\n")
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                     '--call-wrapper', str(wrapper), '--timeout-seconds', '30'],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['passed_assertions'], 2)

    def test_total_deadline_stops_slow_wrapper(self):
        process = MagicMock()
        process.pid = 123456789
        selector = MagicMock()
        selector.select.return_value = []
        clock = SimpleNamespace(monotonic=lambda: 100)
        reply = SimpleNamespace(status=200, body=json.dumps({'git_sha': {'value': SHA}}).encode())
        with patch.object(PROOF, 'time', clock), patch.object(PROOF, 'read_http', return_value=reply), \
                patch.object(PROOF.signal, 'setitimer'), \
                patch.object(PROOF.subprocess, 'Popen', return_value=process), \
                patch.object(PROOF.selectors, 'DefaultSelector') as select, \
                patch.object(PROOF.os, 'killpg') as kill, \
                patch.object(sys, 'argv', [str(RUNNER), '--origin', 'http://127.0.0.1',
                                         '--call-wrapper', '/synthetic', '--timeout-seconds', '1']):
            process.__enter__.return_value = process
            select.return_value.__enter__.return_value = selector
            self.assertEqual(PROOF.main(), 78)
            selector.select.assert_called_once_with(timeout=1)
            kill.assert_called_once_with(process.pid, PROOF.signal.SIGKILL)
            process.__exit__.assert_called_once()

    def test_native_alarm_stops_ready_wrapper_and_reaps_it(self):
        with self.fixture() as (root, wrapper, origin, requests):
            wrapper.write_text('#!' + sys.executable + '\n' +
                "import os, pathlib, signal\n" +
                "root = pathlib.Path(__file__).parent\n" +
                "(root / 'pid.tmp').write_text(str(os.getpid()))\n" +
                "(root / 'pid.tmp').replace(root / 'pid')\n" +
                "os.kill(os.getppid(), signal.SIGALRM)\n" +
                "signal.pause()\n")
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                     '--call-wrapper', str(wrapper)],
                                    capture_output=True, text=True, timeout=35)
            self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['record_reads'], 1)
            pid = int((root / 'pid').read_text())
            with self.assertRaises(ProcessLookupError):
                PROOF.os.kill(pid, 0)

    def test_native_alarm_adapter_restores_handler_and_disarms(self):
        previous = PROOF.signal.getsignal(PROOF.signal.SIGALRM)
        with patch.object(sys, 'argv', [str(RUNNER), '--origin', 'http://127.0.0.1',
                                      '--call-wrapper', '/synthetic']), \
                patch.object(PROOF, 'observe', side_effect=lambda *_: PROOF.signal.raise_signal(PROOF.signal.SIGALRM)):
            self.assertEqual(PROOF.main(), 78)
        self.assertEqual(PROOF.signal.getsignal(PROOF.signal.SIGALRM), previous)
        self.assertEqual(PROOF.signal.getitimer(PROOF.signal.ITIMER_REAL), (0, 0))

    def test_expiry_during_disarm_returns_insufficient_and_restores_handler(self):
        with patch.object(sys, 'argv', [str(RUNNER), '--origin', 'http://127.0.0.1',
                                      '--call-wrapper', '/synthetic']), \
                patch.object(PROOF, 'observe', return_value=0), \
                patch.object(PROOF.signal, 'signal', return_value=PROOF.signal.SIG_DFL) as handler, \
                patch.object(PROOF.signal, 'setitimer', side_effect=[None, TimeoutError()]):
            self.assertEqual(PROOF.main(), 78)
            self.assertEqual(handler.call_args.args, (PROOF.signal.SIGALRM, PROOF.signal.SIG_DFL))


if __name__ == '__main__':
    unittest.main()
