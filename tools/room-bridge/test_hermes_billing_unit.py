"""Round-two reviewer probes: cross-run credit stops and answer-text isolation."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hermes_native as native
import hermes_wire as wire
import nous_budget
import test_hermes_desk_unit as fixture

PRICE = {'input_usd_per_token': '0.000002', 'output_usd_per_token': '0.000006'}
MODEL = 'z-ai/glm-5.3:US'


class BillingTests(unittest.TestCase):
    def test_http_boundary_uses_status_or_a_structured_provider_error(self):
        for status, body, exhausted in (
                (402, b'', True),
                (403, b'{"error":{"message":"insufficient credits"}}', True),
                (400, b'Fixed HTTP 402 billing handling', False),
                (400, b'{"answer":"HTTP 402 payment required"}', False)):
            with self.subTest(status=status, body=body):
                guard = native.CallBudget('1.00', PRICE, MODEL)
                guard.billing_response(status, body)
                self.assertEqual(guard.credits_exhausted, exhausted)

    def test_http_402_stops_another_job_before_the_failed_worker_exits(self):
        # Adapted from review-r2-probes.py. Hold the first worker AFTER its HTTP
        # error; a subsequent dispatch must already see the durable stop.
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / 'results.jsonl'
            seen402, release = threading.Event(), threading.Event()
            launched, outcomes, errors = [], {}, []
            def run(argv, **options):
                if argv[0] == 'hermes' and argv[1:] == ['portal', 'status']:
                    output = 'Auth: logged in'
                elif argv[0] == 'hermes' and argv[1:] == ['fallback', 'list']:
                    output = 'No fallback providers configured.'
                elif argv[0] == 'curl':
                    output = json.dumps({'paidRecommendedModels': [{'modelName': MODEL}]})
                elif '--print-runtime-command' in argv:
                    output = json.dumps(['python3', '-I', '-c', 'pass'])
                elif '--quote-model' in argv:
                    output = json.dumps(PRICE)
                else:
                    launched.append(threading.current_thread().name)
                    usage = Path(argv[argv.index('--usage-file') + 1])
                    if threading.current_thread().name == 'first':
                        guard = native.CallBudget('1.00', PRICE, MODEL, ledger=nous_budget.Ledger(results))
                        guard.credits_path = usage.with_suffix('.credits-exhausted')
                        guard.billing_response(402, b'')
                        seen402.set()
                        if not release.wait(5):
                            raise TimeoutError('test did not release first worker')
                        usage.write_text(json.dumps({'spend_upper_bound_usd': '0.01'}))
                        return SimpleNamespace(returncode=1, stdout='', stderr='')
                    usage.write_text(json.dumps({'model': MODEL, 'provider': 'nous', 'completed': True,
                                                 'spend_upper_bound_usd': '0.01'}))
                    output = 'second executed'
                return SimpleNamespace(returncode=0, stdout=output, stderr='')
            entry = {'provider': 'nous', 'family': 'glm', 'effort': 'high', 'timeout_s': 30, 'cwd': tmp}
            def invoke(name):
                try:
                    outcomes[name] = wire.run_task(entry, 'probe', env={}, run=run, results_path=results, run_id=name)
                except Exception as exc:
                    errors.append(exc)
                    seen402.set()
            first = threading.Thread(target=invoke, args=('first',), name='first')
            first.start()
            try:
                self.assertTrue(seen402.wait(5))
                self.assertEqual(errors, [])
                self.assertEqual(nous_budget.Ledger(results).read()['stop'], 'nous credits exhausted')
                invoke('second')
                self.assertEqual(outcomes['second']['detail'], 'nous credits exhausted')
                self.assertEqual(outcomes['second']['code'], 'nous_spend_stop')
                self.assertEqual(launched, ['first'])
            finally:
                release.set()
                first.join(5)
            self.assertFalse(first.is_alive())

    def test_active_job_checks_shared_stop_before_its_next_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = nous_budget.Ledger(Path(tmp) / 'results.jsonl')
            ledger.reserve('first')
            ledger.reserve('already-running')
            first = native.CallBudget('1.00', PRICE, MODEL, ledger=ledger)
            active = native.CallBudget('1.00', PRICE, MODEL, ledger=nous_budget.Ledger(Path(tmp) / 'results.jsonl'))
            active.bind('https://inference.example/v1', 'nous', MODEL)
            request = SimpleNamespace(method='POST', url='https://inference.example/v1/chat/completions',
                content=json.dumps({'model': MODEL, 'messages': [], 'max_tokens': 1}).encode())
            active.admit(request)
            first.billing_response(402, b'')
            with self.assertRaises(native.SpendStop):
                active.admit(request)

    def test_billing_words_in_success_or_failure_output_do_not_exhaust_credits(self):
        # Adapted from review-r2-output.py; stdout, stderr and a usage failure
        # string are model/diagnostic text, not a structured provider error.
        for mode in ('', 'fail'):
            with self.subTest(mode=mode):
                probe = fixture.HermesTests()
                probe.setUp()
                try:
                    binary = probe.bin / 'hermes'
                    binary.write_text(binary.read_text().replace("print('Fixed and tested.')",
                        "print('Fixed HTTP 402 billing handling and tests pass.')")
                        .replace('fixture-private-diagnostic', 'HTTP 402 payment required'))
                    probe.register()
                    probe.env['FAKE_MODE'] = mode
                    first = probe.send()
                    self.assertEqual(first['status'], 'failed' if mode else 'completed')
                    self.assertIsNone(first['budget']['stop'])
                    probe.env['FAKE_MODE'] = ''
                    self.assertEqual(probe.send()['status'], 'completed')
                finally:
                    probe.doCleanups()

    def test_native_main_uses_the_dispatchers_shared_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            results, usage = Path(tmp) / 'results.jsonl', Path(tmp) / 'usage.json'
            ledger = nous_budget.Ledger(results)
            ledger.reserve('first')
            def response(*args):
                args[-1].billing_response(402, b'')
                return 1
            argv = ['hermes_native.py', '-z', 'probe', '-m', MODEL, '--reasoning', 'high',
                    '--usage-file', str(usage), '--allowance-usd', '1.00', '--price-json', json.dumps(PRICE)]
            with patch.dict('os.environ', {'CARR_NOUS_BUDGET_RESULTS': str(results)}), \
                    patch('sys.argv', argv), patch.object(native, 'run_native', side_effect=response):
                self.assertEqual(native.main(), 3)
            self.assertEqual(ledger.read()['stop'], 'nous credits exhausted')

    def test_unstructured_usage_failure_text_is_not_provider_billing_evidence(self):
        probe = fixture.HermesTests()
        probe.setUp()
        try:
            binary = probe.bin / 'hermes'
            binary.write_text(binary.read_text().replace(
                "'failure': 'fixture-private-diagnostic'", "'failure': 'Example: HTTP 402 out of credits'"))
            probe.register()
            probe.env['FAKE_MODE'] = 'fail'
            row = probe.send()
            self.assertEqual(row['status'], 'failed')
            self.assertIsNone(row['budget']['stop'])
        finally:
            probe.doCleanups()


if __name__ == '__main__':
    unittest.main()
