"""Hermes desk behavior using fake executables and retained job clones."""
import contextlib
import io
import json
import os
import signal
import subprocess
import time
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import desks
import dispatch
import write_ownership
from ops.git_env import fixture_env


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        hermes = self.bin / 'hermes'
        hermes.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, subprocess, time
mode = os.environ.get('FAKE_MODE', '')
if sys.argv[1:2] == ['--print-runtime-command']:
    code = 'import runpy; runpy.run_path(' + repr(str(pathlib.Path(__file__).resolve())) + ", run_name='__main__'); raise SystemExit(0)"
    print(json.dumps([sys.executable, '-I', '-c', code]))
    sys.exit(0)
if sys.argv[1:2] == ['--quote-model']:
    pathlib.Path('quoted-model').write_text(sys.argv[2])
    print(json.dumps(None if os.environ.get('FAKE_UNPRICED') else {
        'model': sys.argv[2], 'input_usd_per_token': '0.000002',
        'output_usd_per_token': '0.000006', 'source': 'hermes_picker_v1_models'}))
    sys.exit(0)
if sys.argv[1:] == ['fallback', 'list']:
    print('Fallback chain: other paid provider' if os.environ.get('FAKE_FALLBACK')
          else '  No fallback providers configured.')
    sys.exit(0)
if sys.argv[1:] == ['portal', 'status']:
    if mode == 'auth-failure':
        print('fixture-private-diagnostic', file=sys.stderr)
        sys.exit(7)
    if mode == 'signed-out':
        print('Auth:    not logged in')
        sys.exit(0)
    if mode == 'slow-auth':
        time.sleep(30)
    print('Auth:    ✓ logged in')
    sys.exit(0)
pathlib.Path('invocation.json').write_text(json.dumps(sys.argv[1:]))
pathlib.Path('native_env.json').write_text(json.dumps({key: os.environ.get(key) for key in
    ('HERMES_YOLO_MODE', 'HERMES_IGNORE_USER_CONFIG', 'CARR_NOUS_BUDGET_RESULTS')}))
if mode == 'timeout':
    child = subprocess.Popen([sys.executable, '-c', "import time; time.sleep(0.5); open('escaped', 'w').write('bad')"])
    pathlib.Path('child.pid').write_text(str(child.pid))
    time.sleep(30)
model = sys.argv[sys.argv.index('-m') + 1]
if mode == 'missing-usage':
    print('No usage receipt')
    sys.exit(0)
pathlib.Path(sys.argv[sys.argv.index('--usage-file') + 1]).write_text(json.dumps({
    'model': 'other/model' if mode == 'mismatch' else model,
    'provider': 'nous', 'completed': mode not in ('fail', 'partial'), 'input_tokens': 123,
    'output_tokens': 45, 'api_calls': 2,
    **({'spend_limit_reached': True, 'spend_upper_bound_usd': '0.12'} if mode == 'spend-stop' else {}),
    'estimated_cost_usd': None if mode == 'token-cost' else float(os.environ.get('FAKE_COST', '0.02')),
    'failure': 'fixture-private-diagnostic'}))
if mode == 'fail':
    print('Job failed.')
    print('fixture-private-diagnostic', file=sys.stderr)
    sys.exit(3)
if mode == 'credits':
    usage_path = pathlib.Path(sys.argv[sys.argv.index('--usage-file') + 1])
    receipt = json.loads(usage_path.read_text())
    receipt.update(json.loads(os.environ['FAKE_CREDIT_ERROR']))
    usage_path.write_text(json.dumps(receipt))
    print(os.environ['FAKE_CREDIT_ERROR'], file=sys.stderr)
    sys.exit(3)
if mode == 'silent-credits':
    usage_path = pathlib.Path(sys.argv[sys.argv.index('--usage-file') + 1])
    usage_path.write_text(json.dumps({'spend_upper_bound_usd': '0.00'}))
    usage_path.with_suffix('.credits-exhausted').write_text('nous credits exhausted')
    print('Partial work before the provider failed.')
    sys.exit(1)
if mode == 'write':
    pathlib.Path('fixed.py').write_text('fixed = True\\n')
    for command in (['git', 'add', 'fixed.py'], ['git', 'commit', '-m', 'Hermes fix'],
                    ['git', 'push', 'origin', 'HEAD'], ['gh', 'pr', 'create', '--title', 'Hermes fix', '--body', 'Tested']):
        subprocess.run(command, check=True, capture_output=True)
print('Fixed and tested.')
''')
        hermes.chmod(0o755)
        catalog = self.bin / 'curl'
        catalog.write_text('''#!/usr/bin/env python3
import json
print(json.dumps({'paidRecommendedModels': [
    {'modelName': 'z-ai/glm-8.9'}, {'modelName': 'z-ai/glm-8.10:US',
     'tokenPrice': 'in $2.00 / out $6.00 per 1M'},
    {'modelName': 'z-ai/glm-8.10-flash:US'}, {'modelName': 'deepseek/deepseek-v7-pro',
     'tokenPrice': 'in $2.00 / out $6.00 per 1M'}]}))
''')
        catalog.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'CARR_HERMES_SLOT_DIR': str(self.root / 'hermes-slots')}
        self.reg = desks.Registry(self.root / 'desks.json')
        self.results = self.root / 'results.jsonl'
        default_results = patch.object(dispatch, 'DEFAULT_RESULTS', self.results)
        default_results.start()
        self.addCleanup(default_results.stop)

    def register(self, **options):
        return self.reg.register('nous-builder', 'hermes-nous', cwd=str(self.root),
                                 family='glm', provider='nous', **options)

    def send(self, **options):
        return dispatch.dispatch('nous-builder', 'Fix and test the build.', registry=self.reg,
            results_path=self.results, env=self.env, **options)

    def test_success_records_final_usage_and_pins_model_provider(self):
        self.register()
        row = self.send()
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['result'], 'Fixed and tested.')
        self.assertEqual(row['model'], 'z-ai/glm-8.10:US')
        self.assertEqual(row['provider'], 'nous')
        self.assertEqual(row['usage']['input_tokens'], 123)
        self.assertEqual(json.loads(self.results.read_text()), row)
        argv = json.loads((self.root / 'invocation.json').read_text())
        self.assertIn('--allowance-usd', argv)
        self.assertEqual(argv[argv.index('--allowance-usd') + 1], '1.00')
        self.assertIn('--price-json', argv)
        self.assertIn('-z', argv)
        self.assertEqual(json.loads((self.root / 'native_env.json').read_text()),
                         {'HERMES_YOLO_MODE': '1', 'HERMES_IGNORE_USER_CONFIG': '1',
                          'CARR_NOUS_BUDGET_RESULTS': str(self.results.absolute())})

    def test_failure_records_usage_without_private_diagnostics(self):
        self.register()
        self.env['FAKE_MODE'] = 'fail'
        row = self.send()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['exit_code'], 3)
        self.assertEqual(row['usage']['api_calls'], 2)
        self.assertNotIn('fixture-private-diagnostic', self.results.read_text())

    def test_native_spend_stop_is_terminal_and_books_the_request_ceiling(self):
        self.register()
        self.env['FAKE_MODE'] = 'spend-stop'
        row = self.send()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['code'], 'nous_spend_stop')
        self.assertEqual(row['detail'], 'Nous run spending limit reached')
        self.assertEqual(row['budget']['spent_usd'], '0.12')

    def test_missing_failed_turn_usage_cannot_lose_the_billing_stop(self):
        self.register()
        self.env['FAKE_MODE'] = 'silent-credits'
        row = self.send()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['detail'], 'nous credits exhausted')
        self.assertEqual(row['code'], 'nous_spend_stop')
        self.assertEqual(row['budget']['stop'], 'nous credits exhausted')
        (self.root / 'invocation.json').unlink()
        self.assertEqual(self.send()['detail'], 'nous credits exhausted')
        self.assertFalse((self.root / 'invocation.json').exists())

    def test_cost_ledger_persists_spend_and_refuses_the_next_estimate(self):
        self.register()
        self.env['FAKE_COST'] = '21.50'
        first = self.send()
        self.assertEqual(first['status'], 'completed')
        ledger = self.results.with_name(self.results.name + '.nous-spend.json')
        saved = json.loads(ledger.read_text())
        self.assertEqual(saved['runs'][first['msg_id']]['cost_usd'], '21.50')
        self.assertEqual(saved['runs'][first['msg_id']]['cost_source'], 'hermes_usage_usd')
        (self.root / 'invocation.json').unlink()
        second = self.send()
        self.assertEqual(second['status'], 'failed')
        self.assertEqual(second['detail'], 'Nous credit budget spent')
        self.assertEqual(second['code'], 'nous_spend_stop')
        self.assertFalse((self.root / 'invocation.json').exists())

    def test_redirected_results_do_not_create_a_fresh_budget(self):
        self.register()
        self.env['FAKE_COST'] = '21.50'
        self.send()
        self.results = self.root / 'redirected-results.jsonl'
        self.assertEqual(self.send()['detail'], 'Nous credit budget spent')

    def test_credit_errors_set_a_sticky_stop_across_later_dispatches(self):
        self.register()
        for index, message in enumerate(({'status_code': 402}, {'error': {'code': 'insufficient_credits'}},
                                         {'error': {'message': 'out of credits'}},
                                         {'error': {'message': 'quota exhausted'}},
                                         {'status_code': 402, 'error': 'billing'})):
            with self.subTest(message=message):
                self.results = self.root / f'credit-{index}.jsonl'
                dispatch.DEFAULT_RESULTS = self.results
                self.env.update(FAKE_MODE='credits', FAKE_CREDIT_ERROR=json.dumps(message))
                first = self.send()
                self.assertEqual(first['status'], 'failed')
                self.assertEqual(first['detail'], 'nous credits exhausted')
                self.env['FAKE_MODE'] = ''
                (self.root / 'invocation.json').unlink()
                second = self.send()
                self.assertEqual(second['detail'], 'nous credits exhausted')
                self.assertFalse((self.root / 'invocation.json').exists())

    def test_missing_cost_stops_later_spending_instead_of_assuming_zero(self):
        self.register()
        self.env['FAKE_MODE'] = 'missing-usage'
        self.send()
        self.env['FAKE_MODE'] = ''
        self.assertEqual(self.send()['detail'], 'Nous run cost unavailable; reconcile ledger')

    def test_paid_fallback_configuration_refuses_inference(self):
        self.register()
        self.env['FAKE_FALLBACK'] = 'paid'
        row = self.send()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['detail'], 'Hermes fallback chain must be empty')
        self.assertFalse((self.root / 'invocation.json').exists())

    def test_tokens_use_the_selected_portal_price_when_dollars_are_absent(self):
        self.register()
        self.env['FAKE_MODE'] = 'token-cost'
        row = self.send()
        self.assertEqual(row['status'], 'completed')
        path = self.results.with_name(self.results.name + '.nous-spend.json')
        receipt = json.loads(path.read_text())['runs'][row['msg_id']]
        self.assertEqual(receipt['cost_source'], 'portal_tokens')
        self.assertEqual(receipt['calculated_cost_usd'], '0.000516')
        self.assertEqual(receipt['cost_usd'], '0.01')
        self.assertEqual(receipt['price']['source'], 'hermes_picker_v1_models')

    def test_unpriced_model_is_refused_before_any_inference(self):
        self.reg.register('nous-builder', 'hermes-nous', cwd=str(self.root), family='deepseek')
        catalog = self.bin / 'curl'
        catalog.write_text(catalog.read_text().replace('in $2.00 / out $6.00 per 1M', 'unknown'))
        self.env['FAKE_UNPRICED'] = '1'
        row = self.send()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['code'], 'nous_spend_stop')
        self.assertEqual(row['detail'], 'Nous model price unavailable; cannot bound run spending')
        self.assertFalse((self.root / 'invocation.json').exists())

    def test_picker_price_supports_models_with_null_recommendation_prices(self):
        self.register()
        catalog = self.bin / 'curl'
        catalog.write_text(catalog.read_text().replace("'in $2.00 / out $6.00 per 1M'", 'None'))
        row = self.send()
        self.assertEqual(row['status'], 'completed')
        self.assertEqual((self.root / 'quoted-model').read_text(), 'z-ai/glm-8.10:US')

    def test_budget_read_command_prints_spend_without_changing_the_ledger(self):
        self.register()
        self.send()
        self.send()
        path = self.results.with_name(self.results.name + '.nous-spend.json')
        before = path.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = dispatch.main(['--results', str(self.results), 'nous-budget'])
        self.assertEqual(code, 0)
        readback = json.loads(output.getvalue())
        self.assertEqual(readback['spent_usd'], '0.04')
        self.assertEqual(readback['budget_usd'], '22.00')
        self.assertEqual(path.read_bytes(), before)

    def test_signed_out_fails_before_catalog_or_inference(self):
        self.register()
        self.env['FAKE_MODE'] = 'signed-out'
        (self.bin / 'curl').unlink()
        row = self.send()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['detail'], 'Nous Portal not signed in')
        self.assertFalse((self.root / 'invocation.json').exists())

    def test_status_command_failure_does_not_claim_the_user_is_signed_out(self):
        self.register()
        self.env['FAKE_MODE'] = 'auth-failure'
        row = self.send()
        self.assertEqual(row['detail'], 'Hermes auth status unavailable')
        self.assertNotIn('fixture-private-diagnostic', self.results.read_text())

    def test_timeout_kills_the_entire_process_group_and_records_once(self):
        self.register(timeout_s=0.3)
        self.env['FAKE_MODE'] = 'timeout'
        row = self.send()
        self.assertEqual(row['status'], 'timed_out')
        self.assertTrue(row['termination_confirmed'])
        time.sleep(0.6)
        self.assertFalse((self.root / 'escaped').exists())
        self.assertEqual(len(self.results.read_text().splitlines()), 1)

    def test_timeout_confirms_a_zombie_only_process_group(self):
        self.register(timeout_s=0.3)
        self.env['FAKE_MODE'] = 'timeout'
        killpg = os.killpg
        run = subprocess.run
        killed_group = []

        def group_probe(pgid, sig):
            if sig == 0 and pgid in killed_group:
                return None
            if sig == signal.SIGKILL:
                killed_group.append(pgid)
            return killpg(pgid, sig)

        def inventory(argv, **options):
            if argv == ['/bin/ps', '-axo', 'pgid=,stat=']:
                return subprocess.CompletedProcess(argv, 0, f'1 S\n{killed_group[-1]} Z\n', '')
            return run(argv, **options)

        with patch.object(write_ownership.os, 'killpg', side_effect=group_probe), \
             patch.object(write_ownership.subprocess, 'run', side_effect=inventory):
            row = self.send()
        self.assertEqual(row['status'], 'timed_out')
        self.assertTrue(row['termination_confirmed'], row)
        self.assertEqual(len(self.results.read_text().splitlines()), 1)

    def test_deadline_includes_auth_preflight(self):
        self.register(timeout_s=0.1)
        self.env['FAKE_MODE'] = 'slow-auth'
        started = time.monotonic()
        row = self.send()
        self.assertEqual(row['status'], 'timed_out')
        self.assertLess(time.monotonic() - started, 1)

    def test_hermes_queues_after_two_jobs_without_consuming_codex_slots(self):
        self.register()
        slots = {**self.env, 'CARR_CODEX_MAX_CONCURRENT': '1',
                 'CARR_CODEX_SLOT_DIR': str(self.root / 'codex-slots')}
        with dispatch._codex_slot(slots), dispatch._hermes_slot(slots), dispatch._hermes_slot(slots), \
             patch.object(dispatch, 'HERMES_SLOT_POLL_S', 0.01):
            completed = []
            job = threading.Thread(target=lambda: completed.append(self.send()))
            job.start()
            time.sleep(0.15)
            self.assertFalse((self.root / 'invocation.json').exists())
        job.join(5)
        self.assertFalse(job.is_alive())
        self.assertEqual(completed[0]['status'], 'completed')
        with dispatch._codex_slot(slots):
            self.assertEqual(self.send()['status'], 'completed')

    def test_write_job_commits_pushes_and_opens_pr_from_its_checkout(self):
        canonical = self.root / 'canonical'
        canonical.mkdir()
        def git(*args, cwd=canonical):
            return subprocess.run(['git', '-C', str(cwd), *args], env=fixture_env(),
                capture_output=True, text=True, check=True).stdout.strip()
        git('init', '-b', 'main')
        git('config', 'user.name', 'Fixture Author')
        git('config', 'user.email', 'jbookout@users.noreply.github.com')
        (canonical / 'AGENTS.md').write_text('Fixture instructions\n')
        git('add', 'AGENTS.md')
        git('commit', '-m', 'initial')
        origin = self.root / 'origin.git'
        subprocess.run(['git', 'clone', '--bare', str(canonical), str(origin)],
                       env=fixture_env(), capture_output=True, check=True)
        git('remote', 'add', 'origin', str(origin))
        gh = self.bin / 'gh'
        gh.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                      "pathlib.Path('pr-call').write_text(' '.join(sys.argv[1:]))\n")
        gh.chmod(0o755)
        self.env = fixture_env({**self.env, 'FAKE_MODE': 'write',
                               'GIT_DIR': str(canonical / '.git')})
        self.register()
        with patch.object(dispatch.codex_checkout, 'CANONICAL_REPO', canonical), \
             patch.object(dispatch.codex_checkout, 'CHECKOUT_ROOT', self.root):
            row = self.send(checkout='new:hermes-fix', writes=['fixed.py'])
        repo = Path(row['checkout_path'])
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(git('log', '-1', '--format=%s', cwd=repo), 'Hermes fix')
        self.assertEqual(git('rev-parse', 'hermes-fix', cwd=origin), git('rev-parse', 'HEAD', cwd=repo))
        self.assertTrue((repo / 'pr-call').read_text().startswith('pr create'))
        self.assertFalse((canonical / 'fixed.py').exists())
        argv = json.loads((repo / 'invocation.json').read_text())
        self.assertIn('--allowance-usd', argv)

    def test_cli_registers_and_documents_the_desk(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.main(['--help'])
        self.assertIn('hermes-nous', output.getvalue())
        self.assertIn('two', output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            code = dispatch.main(['--registry', str(self.reg.path), 'register', 'nous-builder',
                '--kind', 'hermes-nous', '--family', 'deepseek', '--provider', 'nous',
                '--cwd', str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(self.reg.resolve('nous-builder')['family'], 'deepseek')

    def test_mismatched_or_missing_usage_identity_cannot_complete(self):
        self.register()
        for mode in ('mismatch', 'missing-usage'):
            with self.subTest(mode=mode):
                self.env['FAKE_MODE'] = mode
                row = self.send()
                self.assertEqual(row['status'], 'failed')
                self.assertEqual(row['detail'], 'Hermes usage identity mismatch')

    def test_partial_turn_cannot_complete_even_with_zero_exit(self):
        self.register()
        self.env['FAKE_MODE'] = 'partial'
        self.assertEqual(self.send()['status'], 'failed')

    def test_deepseek_family_selects_live_catalog_without_changing_registration(self):
        self.reg.register('nous-builder', 'hermes-nous', cwd=str(self.root), family='deepseek')
        row = self.send()
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['model'], 'deepseek/deepseek-v7-pro')
        self.assertEqual(self.reg.resolve('nous-builder')['family'], 'deepseek')
        self.assertNotIn('model', self.reg.resolve('nous-builder'))

    def test_cli_failure_and_catalog_failure_record_a_failure_without_inference(self):
        self.register()
        (self.bin / 'curl').write_text('#!/bin/sh\nprintf "{bad json}"\n')
        self.assertEqual(self.send()['status'], 'failed')
        (self.bin / 'hermes').unlink()
        self.env['PATH'] = str(self.bin)
        self.assertEqual(self.send()['detail'], 'Hermes CLI or curl unavailable')
        self.assertFalse((self.root / 'invocation.json').exists())

    def test_edited_registry_cannot_change_provider_or_family(self):
        entry = self.register()
        for change in ({'provider': 'auto'}, {'family': 'sol'}, {'timeout_s': 0}):
            with self.subTest(change=change):
                self.reg.path.write_text(json.dumps({'desks': {'nous-builder': {**entry, **change}}}))
                with self.assertRaises(desks.DeskError):
                    self.reg.resolve('nous-builder')


if __name__ == '__main__':
    unittest.main()
