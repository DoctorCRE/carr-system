"""Offline Claude API desk contract. No real keychain or paid requests."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops.git_env import fixture_env
import claude_api_wire as wire
import desks
import dispatch
from work_runner import Resources
import codex_checkout
from claude_api_transport import Transport
from test_claude_api_transport_unit import Response

REAL_RUN = subprocess.run


class ApiDeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sdk = self.root / 'claude_agent_sdk'
        self.sdk.mkdir()
        (self.sdk / '_bundled').mkdir()
        cli = self.sdk / '_bundled/claude'
        cli.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"authMethod":"api_key","apiKeySource":"ANTHROPIC_API_KEY","apiProvider":"firstParty"}\'\n')
        cli.chmod(0o755)
        (self.sdk / '__init__.py').write_text('''
from types import SimpleNamespace
__version__ = '0.2.168'
class ClaudeAgentOptions:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
class ResultMessage: pass
async def transport_fixture(options):
    import json, urllib.request
    request = urllib.request.Request(options.env['ANTHROPIC_BASE_URL'] + '/v1/messages',
        data=json.dumps({'model': options.model, 'messages': [{'role':'user','content':'fixture'}],
                         'max_tokens':800}).encode(), headers={'content-type':'application/json'})
    with urllib.request.urlopen(request, timeout=2) as response: response.read()
async def query(*, prompt, options):
    assert options.permission_mode == 'dontAsk'
    assert all(t in options.allowed_tools for t in ['Read', 'Write', 'Edit', 'Bash'])
    assert options.setting_sources == [] and options.strict_mcp_config
    assert options.env['ANTHROPIC_API_KEY'] == 'model-room-transport'
    assert not any(k in options.env for k in ['CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_AUTH_TOKEN'])
    await transport_fixture(options)
    yield SimpleNamespace(model=options.model)
    msg = ResultMessage()
    msg.__dict__.update(is_error=False, subtype='success', result='finished',
        total_cost_usd=1.25, usage={'input_tokens': 120, 'output_tokens': 40}, session_id='fake-session')
    yield msg
''')
        self.env = fixture_env({'PATH': os.environ['PATH'], 'HOME': str(self.root),
                                'PYTHONPATH': str(self.root),
                                'CLAUDE_CODE_OAUTH_TOKEN': 'fixture',
                                'ANTHROPIC_AUTH_TOKEN': 'fixture'})
        self.entry = {'model': 'claude-opus-5-5', 'effort': 'medium',
                      'timeout_s': 5, 'cwd': str(self.root), 'checkout_path': str(self.root)}
        self.ledger = wire.Ledger(self.root / 'ledger.json')
        self.keychain = lambda *a, **k: subprocess.CompletedProcess(a, 0, 'fake-api-key\n', '')
        self.api_body = {'content': [], 'usage': {'input_tokens': 1000, 'output_tokens': 800}}
        def upstream(path, *_args):
            return Response(json.dumps({'input_tokens':1000} if path.endswith('count_tokens')
                                       else self.api_body).encode())
        transport = patch.object(wire, 'Transport', lambda key, model, deadline:
                                 Transport(key, model, deadline, upstream=upstream))
        transport.start()
        self.addCleanup(transport.stop)

    def seed_spend(self, cost):
        with self.ledger.locked() as data:
            data['months'][wire.month_now()] = {'spent_usd': str(cost), 'reservations': {}}

    def run_task(self, **kwargs):
        return wire.run_task(self.entry, 'finish the job', env=self.env,
                             ledger=self.ledger, keychain_run=self.keychain, **kwargs)

    def test_request_bounds_and_month_admission_leave_reservation_margin(self):
        self.entry['model'] = 'claude-sonnet-5-5'
        code = (self.sdk / '__init__.py').read_text().replace(
            '    yield SimpleNamespace(model=options.model)', """
    assert options.max_turns == 16
    assert options.max_budget_usd == 7.0
    assert options.env['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] == '8192'
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        self.seed_spend('190.00')
        row = self.run_task()
        self.assertEqual(row['status'], 'completed', row)
        self.assertEqual(self.ledger.read()['spent_usd'], '190.01')
        self.assertEqual(self.run_task()['detail'], 'monthly_budget_exceeded')

    def test_detached_tool_is_dead_after_timeout_and_receipt_is_truthful(self):
        code = (self.sdk / '__init__.py').read_text().replace(
            '    yield SimpleNamespace(model=options.model)', """
    import subprocess, sys, pathlib, asyncio
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pathlib.Path(options.cwd, 'detached.pid').write_text(str(child.pid))
    await asyncio.sleep(60)
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        self.entry['timeout_s'] = 0.5
        result = self.run_task()
        self.assertEqual(result['status'], 'timed_out', result)
        pid = int((self.root / 'detached.pid').read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        else:
            alive = True
            os.kill(pid, 9)
        self.assertFalse(alive, 'detached Bash/tool process survived the job')
        self.assertTrue(result['termination_confirmed'], result)

    def test_tool_subprocess_cannot_read_transport_key(self):
        code = (self.sdk / '__init__.py').read_text().replace(
            '    yield SimpleNamespace(model=options.model)', """
    import subprocess, sys
    subprocess.run([sys.executable, '-c',
        "import os,pathlib;pathlib.Path('tool-env.txt').write_text(str(dict(os.environ)))"],
        cwd=options.cwd, check=True)
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        result = self.run_task()
        self.assertEqual(result['status'], 'completed', result)
        self.assertNotIn('fake-api-key', (self.root / 'tool-env.txt').read_text())

    def test_completed_job_cleans_reparented_tool_and_leaves_unrelated_process(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (sentinel.kill(), sentinel.wait()) if sentinel.poll() is None else None)
        code = (self.sdk / '__init__.py').read_text().replace(
            '    yield SimpleNamespace(model=options.model)', """
    import subprocess, sys, asyncio
    subprocess.run([sys.executable, '-c',
        "import os,time,pathlib;pid=os.fork(); "
        "pathlib.Path('orphan.pid').write_text(str(pid)) if pid else os.setsid(); "
        "os._exit(0) if pid else time.sleep(60)"], cwd=options.cwd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    await asyncio.sleep(0.1)
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        row = self.run_task()
        pid = int((self.root / 'orphan.pid').read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        else:
            alive = True
            os.kill(pid, 9)
        self.assertFalse(alive, 'reparented detached tool survived completion')
        self.assertEqual(row['status'], 'completed', row)
        self.assertTrue(row['termination_confirmed'], row)
        self.assertIsNone(sentinel.poll())

    def test_unconfirmed_cleanup_retains_reservation_and_executor_claim(self):
        events = []
        original = wire.JobTree.terminate
        def unconfirmed(tree):
            original(tree)
            return False
        with patch.object(wire.JobTree, 'terminate', unconfirmed):
            row = self.run_task(on_executor=events.append)
        self.assertEqual(row['status'], 'failed')
        self.assertFalse(row['termination_confirmed'])
        self.assertEqual(events[-1]['kind'], 'unconfirmed_tree')
        self.assertEqual(self.ledger.read()['reserved_usd'], '10.00')
        self.assertEqual(self.ledger.read()['stop'], 'termination_unconfirmed')

    def test_unpriced_model_and_unsafe_reservation_refuse_before_key_lookup(self):
        with patch.object(wire.config, 'RUN_ESTIMATE_USD', wire.Decimal('9.00')):
            self.assertEqual(self.run_task()['detail'], 'claude_api_unpriced_model')
        self.entry['model'] = 'claude-future-unpriced'
        with patch.object(wire.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
            self.assertEqual(self.run_task()['detail'], 'claude_api_unpriced_model')

    def test_unexpected_cost_bound_breach_stops_admission_without_hiding_charge(self):
        self.api_body['usage'] = {'input_tokens': 0, 'output_tokens': 525000}
        row = self.run_task()
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['detail'], 'claude_api_cost_bound_breached')
        self.assertEqual(self.ledger.read()['spent_usd'], '10.50')
        self.assertEqual(self.ledger.read()['stop'], 'cost_bound_breached')

    def test_sonnet_default_keeps_opus_selectable_per_desk(self):
        registry = desks.Registry(self.root / 'desks.json')
        default = registry.register('api-default', 'claude-api', effort='medium')
        hard = registry.register('api-hard', 'claude-api', effort='high', model='claude-opus-5-5')
        self.assertEqual(default['model'], 'claude-sonnet-5-5')
        self.assertEqual(hard['model'], 'claude-opus-5-5')
        for selected in (default, hard):
            self.entry.update(selected)
            row = self.run_task()
            self.assertEqual(row['model'], selected['model'])
            self.assertEqual(row['actual_model'], selected['model'])

    def test_ledger_retains_each_model_and_cost_after_settlement(self):
        for model in ('claude-sonnet-5-5', 'claude-opus-5-5'):
            self.entry['model'] = model
            row = self.run_task()
            run = self.ledger.read()['runs'][row['spend_reservation']['run_id']]
            self.assertEqual(run, {'model': model, 'actual_model': model,
                                   'status': 'completed', 'cost_usd': '0.01' if model == 'claude-sonnet-5-5' else '0.02'})
        report = wire.Ledger(self.ledger.path).read()
        self.assertEqual(len(report['runs']), 2)
        self.assertEqual(report['spent_usd'], '0.03')
        self.assertEqual(report['reserved_usd'], '0')

    def test_model_records_preserve_legacy_balances_and_unknown_reservations(self):
        month = wire.month_now()
        self.ledger.path.write_text(json.dumps({'months': {month: {
            'spent_usd': '3.00', 'reservations': {'legacy-run': '4.00'}}}}))
        reservation = self.ledger.reserve('claude-sonnet-5-5')
        run = self.ledger.read()['runs'][reservation[1]]
        self.assertEqual(run['model'], 'claude-sonnet-5-5')
        self.assertEqual(run['status'], 'reserved')
        self.ledger.settle(reservation, None, status='timed_out')
        report = self.ledger.read()
        self.assertEqual(report['spent_usd'], '3.00')
        self.assertEqual(report['reserved_usd'], '14.00')
        self.assertEqual(report['runs'][reservation[1]]['model'], 'claude-sonnet-5-5')
        self.assertEqual(report['runs'][reservation[1]]['status'], 'accounting_unknown')

    def test_corrupt_ledger_fails_closed_and_invalid_sdk_cost_is_only_diagnostic(self):
        self.ledger.path.write_text('{broken')
        result = self.run_task()
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(result['detail'], 'claude_api_ledger_invalid')
        self.ledger.path.unlink()
        code = (self.sdk / '__init__.py').read_text().replace('total_cost_usd=1.25', "total_cost_usd=float('nan')")
        (self.sdk / '__init__.py').write_text(code)
        result = self.run_task()
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['sdk_cost_usd'])
        self.assertEqual(result['cost_usd'], 0.02)
        self.assertIsNone(self.ledger.read()['stop'])

    def test_sdk_exception_billing_error_is_sticky_without_logging_secret(self):
        code = (self.sdk / '__init__.py').read_text().replace(
            '    yield SimpleNamespace(model=options.model)',
            "    raise RuntimeError('billing_error 402 fake-api-key')\n    yield SimpleNamespace(model=options.model)")
        (self.sdk / '__init__.py').write_text(code)
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('fake-api-key', json.dumps(result))
        self.assertEqual(self.ledger.read()['stop'], 'billing_error')

    def test_ledger_includes_concurrent_reservations_in_budget(self):
        self.seed_spend('185.00')
        self.ledger.reserve(self.entry['model'])
        self.assertEqual(self.run_task()['detail'], 'monthly_budget_exceeded')
        self.assertEqual(self.ledger.read()['reserved_usd'], '10.00')

    def test_worker_launch_failure_returns_failure_without_spend(self):
        with patch.object(wire.subprocess, 'Popen', side_effect=OSError('fixture launch failure')):
            result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['detail'], 'sdk_worker_unavailable')
        self.assertEqual(self.ledger.read()['reserved_usd'], '0')

    def test_reservations_survive_rollover_and_cross_month_cost_is_conservative(self):
        with patch.object(wire, 'month_now', return_value='2026-01'):
            reservation = self.ledger.reserve(self.entry['model'])
        with patch.object(wire, 'month_now', return_value='2026-02'):
            self.assertEqual(self.ledger.read()['reserved_usd'], '10.00')
            self.ledger.settle(reservation, '2.50')
            self.assertEqual(self.ledger.read()['spent_usd'], '2.50')
        with patch.object(wire, 'month_now', return_value='2026-01'):
            self.assertEqual(self.ledger.read()['spent_usd'], '2.50')

    def test_missing_relay_usage_charges_full_reservation_and_sets_stop(self):
        del self.api_body['usage']
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.ledger.read()['reserved_usd'], '0')
        self.assertEqual(self.ledger.read()['spent_usd'], '10.00')
        self.assertEqual(self.ledger.read()['stop'], 'accounting_unknown')

    def test_timeout_with_inflight_relay_request_charges_full_reservation(self):
        import time
        class SlowResponse(Response):
            def read1(self, size):
                time.sleep(1)
                return super().read1(size)
        def upstream(path, *_args):
            if path.endswith('count_tokens'):
                return Response(b'{"input_tokens":1000}')
            return SlowResponse(json.dumps(self.api_body).encode())
        self.entry['timeout_s'] = 0.3
        with patch.object(wire, 'Transport', lambda key, model, deadline:
                          Transport(key, model, deadline, upstream=upstream)):
            row = self.run_task()
        self.assertEqual(row['status'], 'timed_out', row)
        self.assertEqual(row['cost_usd'], 10)
        self.assertEqual(self.ledger.read()['spent_usd'], '10.00')
        self.assertEqual(self.ledger.read()['stop'], 'accounting_unknown')

    def test_wrong_model_cannot_be_reported_completed(self):
        code = (self.sdk / '__init__.py').read_text().replace('model=options.model', "model='claude-sonnet-5'")
        (self.sdk / '__init__.py').write_text(code)
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['detail'], 'sdk_model_mismatch')
        self.assertEqual(self.ledger.read()['spent_usd'], '0.02')

    def test_oauth_auth_report_is_refused_before_sdk_query(self):
        cli = self.sdk / '_bundled/claude'
        cli.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"authMethod":"oauth_token"}\'\n')
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['detail'], 'api_only_auth_refused')
        self.assertEqual(self.ledger.read()['reserved_usd'], '0')
        self.assertEqual(self.ledger.read()['spent_usd'], '0')

    def test_spend_cli_and_help_expose_budget_month_and_keychain_item(self):
        self.seed_spend('4.50')
        import contextlib, io
        output = io.StringIO()
        with patch.object(wire.config, 'LEDGER_PATH', self.ledger.path), contextlib.redirect_stdout(output):
            self.assertEqual(dispatch.main(['claude-api-spend']), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['spent_usd'], '4.50')
        self.assertEqual(report['budget_usd'], '200.00')
        self.assertIn('month', report)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.main(['--help'])
        self.assertIn('carr-model-room-claude-api', output.getvalue())

    def test_two_api_slots_are_independent_of_a_busy_codex_pool(self):
        code = (self.sdk / '__init__.py').read_text().replace(
            "    yield SimpleNamespace(model=options.model)", """
    import asyncio, os, time, json
    fd = os.open(options.cwd + '/activity.jsonl', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    os.write(fd, (json.dumps([time.monotonic(), 1]) + '\\n').encode())
    await asyncio.sleep(0.3)
    os.write(fd, (json.dumps([time.monotonic(), -1]) + '\\n').encode())
    os.close(fd)
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        busy_env = {'CARR_CODEX_MAX_CONCURRENT': '1', 'CARR_CODEX_SLOT_DIR': str(self.root / 'codex-slots')}
        resources = Resources(self.root / 'desk-locks', slots=busy_env['CARR_CODEX_SLOT_DIR'], limit=1)
        handles = resources.acquire('codex', codex=True)
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                rows = list(pool.map(lambda _: self.run_task(), range(3)))
        finally:
            for handle in handles:
                handle.close()
        self.assertEqual([r['status'] for r in rows], ['completed'] * 3)
        active = peak = 0
        for _, change in sorted(json.loads(x) for x in (self.root / 'activity.jsonl').read_text().splitlines()):
            active += change
            peak = max(peak, active)
        self.assertEqual(peak, 2)
        self.assertEqual(self.ledger.read()['spent_usd'], '0.06')

    def test_write_job_uses_new_checkout_commits_pushes_and_records_result(self):
        source = self.root / 'source'
        source.mkdir()
        def git(*args, cwd=source):
            return REAL_RUN(['git', '-C', str(cwd), *args], env=fixture_env(),
                            capture_output=True, text=True, check=True).stdout.strip()
        git('init', '-b', 'main')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'jbookout@users.noreply.github.com')
        (source / 'AGENTS.md').write_text('fixture instructions')
        git('add', 'AGENTS.md')
        git('commit', '-m', 'fixture')
        origin = self.root / 'origin.git'
        git('clone', '--bare', str(source), str(origin))
        git('remote', 'add', 'origin', str(origin))
        binary = self.root / 'bin'
        binary.mkdir()
        gh = binary / 'gh'
        gh.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" > "$GH_FIXTURE_CALL"\n')
        gh.chmod(0o755)
        self.env['PATH'] = str(binary) + os.pathsep + self.env['PATH']
        self.env['GH_FIXTURE_CALL'] = str(self.root / 'gh-call.txt')
        code = (self.sdk / '__init__.py').read_text().replace(
            "    yield SimpleNamespace(model=options.model)", """
    import pathlib, subprocess
    pathlib.Path(options.cwd, 'done.txt').write_text('built')
    for args in [['add', 'done.txt'], ['commit', '-m', 'build'], ['push', 'origin', 'HEAD']]:
        subprocess.run(['git', '-C', options.cwd, *args], check=True, capture_output=True)
    subprocess.run(['gh', 'pr', 'create', '--title', 'Fixture build', '--body', 'Tested'],\n                   cwd=options.cwd, check=True, capture_output=True)
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        registry = desks.Registry(self.root / 'desks.json')
        entry = registry.register('api-build', 'claude-api', effort='high')
        self.assertEqual(entry['model'], 'claude-sonnet-5-5')
        def fake_keychain(argv, **kwargs):
            if argv[0] == '/usr/bin/security':
                return self.keychain(argv, **kwargs)
            return REAL_RUN(argv, **kwargs)
        results = self.root / 'results.jsonl'
        with patch.object(codex_checkout, 'CANONICAL_REPO', source), \
                patch.object(codex_checkout, 'CHECKOUT_ROOT', self.root), \
                patch.object(wire.config, 'LEDGER_PATH', self.ledger.path), \
                patch.object(wire.subprocess, 'run', side_effect=fake_keychain), \
                patch.object(Resources, 'acquire', side_effect=AssertionError('Codex slot used')):
            row = dispatch.dispatch('api-build', 'Build done.txt', registry=registry,
                                    results_path=results, env=self.env,
                                    checkout='new:api-job', writes=['done.txt'])
        self.assertEqual(row['status'], 'completed', row)
        self.assertFalse(Path(row['checkout_workspace']).exists())
        self.assertEqual(row['checkout_cleanup']['status'], 'deleted')
        self.assertEqual(git('show', 'api-job:done.txt', cwd=origin), 'built')
        self.assertEqual(git('log', '-1', '--format=%ae', 'api-job', cwd=origin),
                         'jbookout@users.noreply.github.com')
        self.assertFalse((source / 'done.txt').exists())
        self.assertEqual(json.loads(results.read_text())['cost_usd'], 0.01)
        self.assertEqual(row['model'], 'claude-sonnet-5-5')
        self.assertIn('pr create --title Fixture build', (self.root / 'gh-call.txt').read_text())

    def test_api_checkout_stays_exclusive_without_declared_paths(self):
        REAL_RUN(['git', 'init', '-q', str(self.root)], env=fixture_env(), check=True)
        registry = desks.Registry(self.root / 'desks.json')
        registry.register('api-build', 'claude-api', effort='high')
        prepared = {'checkout_path': str(self.root), 'checkout_workspace': str(self.root)}
        def execute(request, *, on_executor):
            with self.assertRaisesRegex(desks.DeskError, 'another writer'):
                with dispatch.write_ownership.checkout_writer(str(self.root)):
                    pass
            self.assertTrue(callable(on_executor))
            return {'status': 'completed', 'termination_confirmed': True}
        with patch.object(codex_checkout, 'prepare', return_value=prepared), \
                patch.object(dispatch, '_execute', side_effect=execute):
            row = dispatch.dispatch('api-build', 'Inspect the checkout', registry=registry,
                                    results_path=self.root / 'results.jsonl', env=self.env,
                                    checkout='api-job')
        self.assertEqual(row['status'], 'completed')
        with dispatch.write_ownership.checkout_writer(str(self.root)):
            pass

    def test_missing_key_fails_without_launch_or_spend_and_gives_setup_command(self):
        self.keychain = lambda *a, **k: subprocess.CompletedProcess(a, 44, '', 'missing')
        with patch.object(wire.subprocess, 'Popen') as launch:
            result = self.run_task()
        self.assertEqual(result['detail'], 'Claude API key not set up')
        self.assertEqual(result['model'], self.entry['model'])
        self.assertEqual(result['setup_command'],
                         'security add-generic-password -U -a model-room -s carr-model-room-claude-api -w')
        launch.assert_not_called()
        self.assertEqual(self.ledger.read()['reserved_usd'], '0')

    def test_failure_is_charged_and_does_not_claim_completion(self):
        code = (self.sdk / '__init__.py').read_text().replace("is_error=False", "is_error=True")
        (self.sdk / '__init__.py').write_text(code)
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.ledger.read()['spent_usd'], '0.02')
        self.assertIsNone(self.ledger.read()['stop'])

    def test_billing_error_stops_across_new_ledger_instances_until_cleared(self):
        code = (self.sdk / '__init__.py').read_text()
        code = code.replace("is_error=False, subtype='success', result='finished'",
                            "is_error=True, subtype='error_during_execution', result='HTTP 402 credit balance too low'")
        (self.sdk / '__init__.py').write_text(code)
        result = self.run_task()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.ledger.read()['stop'], 'billing_error')
        self.ledger = wire.Ledger(self.ledger.path)
        self.assertEqual(self.run_task()['detail'], 'claude_api_stopped')
        self.ledger.clear_stop()
        self.assertIsNone(self.ledger.read()['stop'])
        self.assertEqual(self.ledger.read()['spent_usd'], '0.02')

    def test_timeout_kills_worker_and_tool_process_group(self):
        code = (self.sdk / '__init__.py').read_text()
        code = code.replace("    yield SimpleNamespace(model=options.model)", """
    import subprocess, sys, pathlib, asyncio
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    pathlib.Path(options.cwd, 'child.pid').write_text(str(child.pid))
    await asyncio.sleep(60)
    yield SimpleNamespace(model=options.model)""")
        (self.sdk / '__init__.py').write_text(code)
        self.entry['timeout_s'] = 0.5
        result = self.run_task()
        self.assertEqual(result['status'], 'timed_out', result)
        pid = int((self.root / 'child.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.ledger.read()['reserved_usd'], '0')
        self.assertEqual(self.ledger.read()['spent_usd'], '0.02')

    def test_budget_refuses_before_worker_and_reports_month(self):
        self.seed_spend('195.00')
        with patch.object(wire.subprocess, 'Popen') as launch:
            result = self.run_task()
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(result['detail'], 'monthly_budget_exceeded')
        self.assertEqual(result['model'], self.entry['model'])
        launch.assert_not_called()
        self.assertEqual(self.ledger.read()['spent_usd'], '195.00')
        self.assertEqual(self.ledger.read()['budget_usd'], '200.00')
        self.assertRegex(self.ledger.read()['month'], r'^\d{4}-\d{2}$')

    def test_success_records_final_model_usage_and_durable_cost(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['result'], 'finished')
        self.assertEqual(result['actual_model'], 'claude-opus-5-5')
        self.assertEqual(result['usage'], {'input_tokens': 120, 'output_tokens': 40})
        self.assertEqual(result['cost_usd'], 0.02)
        self.assertEqual(result['sdk_cost_usd'], 1.25)
        self.assertEqual(wire.Ledger(self.ledger.path).read()['spent_usd'], '0.02')
        self.assertNotIn('fake-api-key', json.dumps(result) + self.ledger.path.read_text())


if __name__ == '__main__':
    unittest.main()
