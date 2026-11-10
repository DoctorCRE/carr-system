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
)


def probe(name, phase, outcome='unavailable'):
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
                    and n.name == ('main' if name == 'post-call-exact-edge-whitespace' else 'observe'))
    body_try = next(n for n in function.body if isinstance(n, ast.Try))
    first_line = body_try.body[0].lineno
    # The migrated body is inside the shared alarm context.
    if isinstance(body_try.body[0], ast.With):
        first_line = body_try.body[0].body[0].lineno

    def trace(frame, event, arg):
        if frame.f_code.co_filename == str(path) and event == 'line' and frame.f_lineno == first_line:
            sys.settrace(None)
            if phase == 'observation':
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
            if outcome == 'error':
                raise RuntimeError('unexpected programming error')
            if outcome == 'contradiction':
                raise getattr(module, 'Contradiction', AssertionError)('test_contradiction')
            raise module.Unavailable('test_source_unavailable')
        return trace

    argv = [str(path), '--origin', 'https://example.invalid', '--call-wrapper', '/unused',
            '--timeout-seconds', '20']
    if name == 'identity-domain-recorded-addresses':
        argv += ['--receipt', '/unused']
    output = io.StringIO()
    try:
        with patch.object(signal, 'setitimer', timer), patch.object(sys, 'argv', argv), patch('sys.stdout', output):
            sys.settrace(trace)
            code = module.main()
        lines = output.getvalue().splitlines()
        assert len(lines) == 1, lines
        receipt = json.loads(lines[0])
        assert isinstance(receipt, dict) and ('status' in receipt or 'result' in receipt), receipt
        expected = 1 if phase == 'none' and outcome == 'contradiction' else 78
        assert code == expected, (code, receipt)
        assert signal.getsignal(signal.SIGALRM) == previous, 'previous handler not restored'
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0, 'timer still armed'
    finally:
        sys.settrace(None)
        real_timer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class ProofDeadlineLifecycleTests(unittest.TestCase):
    def test_expiry_during_setup_observation_and_teardown(self):
        for name in SCRIPTS:
            for phase in ('setup', 'observation', 'teardown'):
                with self.subTest(script=name, phase=phase):
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), name, phase],
                                           capture_output=True, text=True, timeout=10)
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)


    def test_teardown_expiry_overrides_contradiction(self):
        for name in SCRIPTS:
            if name == 'post-call-exact-edge-whitespace':
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
    if len(sys.argv) > 1 and sys.argv[1] in SCRIPTS:
        probe(*sys.argv[1:])
    else:
        unittest.main()
