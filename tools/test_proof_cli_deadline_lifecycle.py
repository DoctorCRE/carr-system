"""Deadline expiry must leave one classified receipt and restore process state."""
import ast
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    'mail-policy-multiple-recorded-addresses',
    'duplicate-candidates-address-membership',
    'notification-feed-evidence-integrity',
    'lead-contact-address-membership',
    'email-next-step-closed-date',
    'identity-domain-recorded-addresses',
    'post-call-exact-edge-whitespace',
    'lease-comparison-tiny-rate-stability',
    'client-anniversary-read',
)


def probe(name, phase, outcome='unavailable', field=None, expected_value=None):
    path = ROOT / 'tools' / f'prove-{name}.py'
    spec = importlib.util.spec_from_file_location('proof', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_timer = signal.setitimer
    previous = signal.getsignal(signal.SIGALRM)
    injected = False

    def timer(which, duration, *args):
        nonlocal injected
        result = real_timer(which, duration, *args)
        if not injected and ((phase == 'setup' and duration > 0)
                             or (phase == 'teardown' and duration == 0)):
            injected = True
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        return result

    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == ('main' if name in ('post-call-exact-edge-whitespace', 'client-anniversary-read') else 'observe'))
    body_try = next(n for n in function.body if isinstance(n, ast.Try))
    first_line = body_try.body[0].lineno
    # The migrated body is inside the shared alarm context.
    if isinstance(body_try.body[0], ast.With):
        first = body_try.body[0].body[0]
        while isinstance(first, ast.Try):
            first = first.body[0]
        first_line = first.lineno

    def trace(frame, event, arg):
        if frame.f_code.co_filename == str(path) and event == 'line' and frame.f_lineno == first_line:
            sys.settrace(None)
            if phase == 'observation':
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
            if outcome == 'error':
                raise RuntimeError('unexpected programming error')
            if outcome == 'contradiction':
                raise getattr(module, 'Contradiction', AssertionError)('test_contradiction')
            raise getattr(module, 'Unavailable', ValueError)('test_source_unavailable')
        return trace

    argv = [str(path), '--origin', 'https://example.invalid', '--call-wrapper', '/unused',
            '--timeout-seconds', '20']
    if name == 'identity-domain-recorded-addresses':
        argv += ['--receipt', '/unused']
    if name == 'client-anniversary-read':
        argv = [str(path)]
    output = io.StringIO()
    try:
        with patch.object(signal, 'setitimer', timer), patch.object(sys, 'argv', argv), patch('sys.stdout', output):
            sys.settrace(trace)
            code = module.main()
        lines = output.getvalue().splitlines()
        assert len(lines) == 1, lines
        receipt = json.loads(lines[0])
        assert isinstance(receipt, dict) and ('status' in receipt or 'result' in receipt or 'state' in receipt), receipt
        expected = 1 if phase == 'none' and outcome == 'contradiction' else 78
        assert code == expected, (code, receipt)
        if field:
            assert receipt[field] == expected_value, receipt
        assert signal.getsignal(signal.SIGALRM) == previous, 'previous handler not restored'
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0, 'timer still armed'
    finally:
        sys.settrace(None)
        real_timer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def mail_revalidation():
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location('proof', ROOT / 'tools/prove-mail-policy-multiple-recorded-addresses.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Handler(BaseHTTPRequestHandler):
        reads = 0

        def log_message(self, *_args):
            pass

        def do_GET(self):
            Handler.reads += 1
            body = json.dumps({'git_sha': {'value': 'a' * 40}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                if Handler.reads == 1:
                    self.wfile.write(body)
                else:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.01)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with patch.object(module.subprocess, 'run', side_effect=module.Contradiction('recorded_restriction_not_blocked')):
            started = time.monotonic()
            code, receipt = module.observe(f'http://127.0.0.1:{server.server_port}', '/unused', .15)
            elapsed = time.monotonic() - started
        assert code == 78, receipt
        assert Handler.reads == 2, Handler.reads
        assert elapsed < .5, (elapsed, receipt)
    finally:
        server.shutdown()
        server.server_close()


class ProofDeadlineLifecycleTests(unittest.TestCase):
    def test_mail_contradiction_revalidation_has_total_deadline(self):
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'mail-revalidation'],
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_caller_specific_expiry_receipts(self):
        for name, field, expected in (
                ('identity-domain-recorded-addresses', 'reason', 'total_deadline'),
                ('post-call-exact-edge-whitespace', 'status', 'timeout')):
            with self.subTest(script=name):
                child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                        name, 'observation', 'unavailable', field, expected],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_expiry_during_setup_observation_and_teardown(self):
        for name in SCRIPTS:
            for phase in ('setup', 'observation', 'teardown'):
                with self.subTest(script=name, phase=phase):
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), name, phase],
                                           capture_output=True, text=True, timeout=10)
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)


    def test_teardown_expiry_overrides_contradiction(self):
        for name in SCRIPTS:
            if name in ('post-call-exact-edge-whitespace', 'client-anniversary-read'):
                continue
            with self.subTest(script=name):
                child = subprocess.run([sys.executable, str(Path(__file__).resolve()), name,
                                        'teardown', 'contradiction'],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_normal_pass_assertion_and_programming_error_restore_state(self):
        sys.path.insert(0, str(ROOT))
        from lib.proof_deadline import proof_deadline
        previous = signal.getsignal(signal.SIGALRM)
        for error in (None, AssertionError, RuntimeError):
            with self.subTest(error=error):
                try:
                    with proof_deadline(20, TimeoutError):
                        if error:
                            raise error('visible')
                        result = 0
                except (AssertionError, RuntimeError) as raised:
                    self.assertIs(type(raised), error)
                else:
                    self.assertIsNone(error)
                    self.assertEqual(result, 0)
                self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
                self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))

    def test_teardown_expiry_overrides_pending_pass(self):
        sys.path.insert(0, str(ROOT))
        from lib.proof_deadline import proof_deadline
        real_timer = signal.setitimer
        previous = signal.getsignal(signal.SIGALRM)

        def timer(which, seconds):
            result = real_timer(which, seconds)
            if seconds == 0:
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
            return result

        def passing():
            try:
                with proof_deadline(20, TimeoutError):
                    return 0
            except TimeoutError:
                return 78

        with patch.object(signal, 'setitimer', timer):
            self.assertEqual(passing(), 78)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'mail-revalidation':
        mail_revalidation()
    elif len(sys.argv) > 1 and sys.argv[1] in SCRIPTS:
        probe(*sys.argv[1:])
    else:
        unittest.main()
