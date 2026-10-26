#!/usr/bin/env python3
# Test routine runner scheduling and execution.
from datetime import datetime
import io
import fcntl
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import record_call
from lib.record_call import VerbResult, OK, TRANSIENT
from tools.routines import runtime

NOW = datetime.fromisoformat('2026-10-05T07:00:00-05:00')


class RunnerTests(unittest.TestCase):
    def record_transport(self, calls):
        run = subprocess.run
        node = shutil.which('node')

        def transport(argv, **kwargs):
            selected = run([node, '--input-type=module', '-e',
                'import {selectLocalClientCredential} from "./mcp-server/local-client-auth.mjs"; '
                'let raw = ""; for await (const chunk of process.stdin) raw += chunk; '
                'const env = JSON.parse(raw); const value = selectLocalClientCredential(env); '
                'console.log(JSON.stringify({profile: value.profile, '
                'tokenVariable: value.tokenVariable, hasToken: Boolean(value.token), '
                'borrowedLocal: value.token === env.CARR_MCP_LOCAL_TOKEN}));'],
                cwd=ROOT, input=json.dumps(kwargs['env']), text=True, capture_output=True, check=True)
            credential = json.loads(selected.stdout)
            verb = argv[2]
            calls.append((verb, credential))
            if not credential['hasToken']:
                return subprocess.CompletedProcess(argv, 1, '', 'missing ' + credential['tokenVariable'])
            return subprocess.CompletedProcess(argv, 0, json.dumps({'ok': True, 'loops': []}), '')

        return transport

    def test_all_record_calls_use_the_local_machine_credential(self):
        # The unattended profile was never provisioned on the Studio; routines use
        # the same door as every other scheduled job, even if a profile is inherited.
        calls = []
        env = {'CARR_MCP_CLIENT_PROFILE': 'unattended',
               'CARR_MCP_LOCAL_TOKEN': 'fixture-local', 'CARR_MCP_UNATTENDED_TOKEN': 'fixture-unattended'}
        transport = self.record_transport(calls)
        with patch.dict(os.environ, env, clear=True), patch.object(record_call, '_run_process', transport):
            ctx = runtime.Context('lead-signals-weekly', now=NOW)
            ctx.read('find', {})
            ctx.write('add-loop', {'title': 'Fixture proposal'}, 'proposal')
            ctx.failure('fixture failure')
        self.assertEqual(calls, [(verb, {'profile': 'local',
            'tokenVariable': 'CARR_MCP_LOCAL_TOKEN', 'hasToken': True, 'borrowedLocal': True})
            for verb in ['find', 'add-loop', 'loop-board', 'add-loop']])

    def test_missing_local_token_fails_visibly_for_records_and_failure(self):
        for operation in ['read', 'write', 'failure']:
            with self.subTest(operation=operation):
                calls = []
                transport = self.record_transport(calls)
                with patch.dict(os.environ, {}, clear=True), patch.object(record_call, '_run_process', transport):
                    ctx = runtime.Context('lead-signals-weekly', now=NOW)
                    with self.assertRaisesRegex(RuntimeError, 'missing CARR_MCP_LOCAL_TOKEN'):
                        if operation == 'read':
                            ctx.read('find', {})
                        elif operation == 'write':
                            ctx.write('add-loop', {'title': 'Fixture proposal'}, 'proposal')
                        else:
                            ctx.failure('fixture failure')
                self.assertEqual([verb for verb, _ in calls], {
                    'read': ['find'], 'write': ['add-loop'], 'failure': ['loop-board']}[operation])

    def test_due_respects_chicago_schedule_and_completion(self):
        self.assertFalse(runtime.due('lead-signals-weekly', NOW.replace(hour=6, minute=59), False))
        self.assertTrue(runtime.due('lead-signals-weekly', NOW, False))
        self.assertFalse(runtime.due('lead-signals-weekly', NOW, True))
        self.assertEqual(runtime.period(datetime.fromisoformat('2026-10-05T00:30:00+00:00')), '2026-09-28')

    def test_catch_up_runs_after_missed_monday(self):
        self.assertTrue(runtime.due('lead-signals-weekly', NOW.replace(day=6), False))

    def test_failure_reporting_holds_the_execution_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            held = []
            def failure(ctx, detail):
                with (root / 'out/routines/lead-signals-weekly/run.lock').open('a') as other:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(True)
            with patch.object(runtime, 'ROOT', root), patch.object(sys, 'argv', ['routine', 'lead-signals-weekly']), \
                 patch.object(runtime, 'execute', side_effect=RuntimeError('fixture failure')), \
                 patch.object(runtime.Context, 'failure', failure), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(runtime.main(), 1)
            self.assertEqual(held, [True])

    def test_failure_reporting_logs_redacted_cause_and_keeps_job_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = 'synthetic-private-value'
            with patch.object(runtime, 'ROOT', root), patch.object(sys, 'argv', ['routine', 'lead-signals-weekly']), \
                 patch.dict(os.environ, {'CARR_MCP_UNATTENDED_TOKEN': secret}), \
                 patch.object(runtime, 'execute', side_effect=ValueError('fixture provider failure')), \
                 patch.object(runtime.Context, 'failure', side_effect=RuntimeError(
                     'loop-board failed: no MCP token for client profile unattended; ' + secret)), \
                 patch('sys.stderr', new_callable=io.StringIO) as stderr:
                self.assertEqual(runtime.main(), 1)
            output = stderr.getvalue()
            self.assertIn('FAIL ValueError: fixture provider failure', output)
            self.assertIn('no MCP token for client profile unattended', output)
            self.assertNotIn(secret, output)
            self.assertIn('[REDACTED]', output)

    def test_final_failure_record_pairs_primary_and_reporting_errors(self):
        for reporting_failed in (False, True):
            with self.subTest(reporting_failed=reporting_failed), tempfile.TemporaryDirectory() as folder:
                secret = 'synthetic-private-value'
                with patch.object(runtime, 'ROOT', Path(folder)), \
                     patch.object(sys, 'argv', ['routine', 'lead-signals-weekly']), \
                     patch.dict(os.environ, {'CARR_MCP_UNATTENDED_TOKEN': secret}), \
                     patch.object(runtime, 'execute', side_effect=ValueError('primary ' + secret + '\nsecond line')), \
                     patch.object(runtime.Context, 'failure', side_effect=(
                         RuntimeError('reporting ' + secret) if reporting_failed else None)), \
                     patch('sys.stderr', new_callable=io.StringIO) as stderr:
                    self.assertEqual(runtime.main(), 1)
                output = stderr.getvalue()
                last_line = output.splitlines()[-1]
                self.assertTrue(last_line.startswith('routine failure evidence: '), last_line)
                evidence = json.loads(last_line.removeprefix('routine failure evidence: '))
                self.assertEqual(evidence, {
                    'primary_error': 'ValueError: primary [REDACTED]\nsecond line',
                    'reporting_error': 'RuntimeError: reporting [REDACTED]' if reporting_failed else None,
                    'routine': 'lead-signals-weekly',
                })
                self.assertNotIn(secret, output)

    def test_previous_period_resumes_before_new_period(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'out/routines/lead-signals-weekly'
            directory.mkdir(parents=True)
            (directory / '2026-01-05.json').write_text(json.dumps({'started_at': '2026-01-05T07:30:00-06:00', 'plan': {'work': True}}))
            periods = []
            def execute(ctx):
                periods.append(runtime.period(ctx.now))
                ctx.state['completed'] = True
                ctx.save()
                return 0
            with patch.object(runtime, 'ROOT', root), patch.object(sys, 'argv', ['routine', 'lead-signals-weekly']), \
                 patch.object(runtime, 'execute', execute), patch.object(runtime.Context, 'failure', side_effect=AssertionError('failed recovery')):
                self.assertEqual(runtime.main(), 0)
            self.assertEqual(periods[0], '2026-01-05')
            self.assertEqual(len(periods), 2)

    def test_confirmed_effect_is_reused_after_restarting_context(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'state.json'
            ctx = runtime.Context('lead-signals-weekly', journal=journal, now=NOW)
            with patch.object(runtime, 'call_verb', return_value=VerbResult('add-party', OK, {'party_id': 'fixture'})) as calls:
                self.assertEqual(ctx.write('add-party', {'name': 'Fixture'}, 'provider:1'), {'party_id': 'fixture'})
                ctx = runtime.Context('lead-signals-weekly', journal=journal, now=NOW)
                self.assertEqual(ctx.write('add-party', {'name': 'Fixture'}, 'provider:1'), {'party_id': 'fixture'})
                self.assertEqual(calls.call_count, 1)
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    ctx.write('add-party', {'name': 'Other'}, 'provider:1')

    def test_uncertain_effect_stops_without_replaying(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'state.json'
            ctx = runtime.Context('lead-signals-weekly', journal=journal, now=NOW)
            with patch.object(runtime, 'call_verb', return_value=VerbResult('add-party', TRANSIENT, detail='timeout')) as calls:
                with self.assertRaises(RuntimeError):
                    ctx.write('add-party', {'name': 'Fixture'}, 'provider:1')
                ctx = runtime.Context('lead-signals-weekly', journal=journal, now=NOW)
                with self.assertRaisesRegex(RuntimeError, 'readback'):
                    ctx.write('add-party', {'name': 'Fixture'}, 'provider:1')
                self.assertEqual(calls.call_count, 1)
            self.assertEqual(journal.stat().st_mode & 0o777, 0o600)

    def test_no_work_completes_period_without_record_effect(self):
        class Module:
            @staticmethod
            def prepare(ctx): return {'work': False}
            @staticmethod
            def execute(ctx, plan): raise AssertionError('no-work execution')
        with tempfile.TemporaryDirectory() as folder:
            ctx = runtime.Context('lead-signals-weekly', journal=Path(folder) / 'state.json', now=NOW)
            with patch.object(runtime, 'call_verb', side_effect=AssertionError('record effect')):
                self.assertEqual(runtime.run_module(ctx, Module), 0)
            self.assertTrue(json.loads(ctx.journal.read_text())['completed'])

    def test_completed_period_does_not_execute_again(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx = runtime.Context('lead-signals-weekly', journal=Path(folder) / 'state.json', now=NOW)
            ctx.state['completed'] = True
            ctx.save()
            with patch.object(runtime.importlib, 'import_module', side_effect=AssertionError('completed rerun')):
                self.assertEqual(runtime.execute(ctx), 0)

    def test_fixture_cli_never_creates_live_journal(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/routines/runtime.py'), 'lead-signals-weekly',
            '--dry-run', '--fixture', str(ROOT / 'ops/fixtures/routines/leads.json'), '--now', NOW.isoformat()],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['candidate_count'], 2)
        self.assertEqual(value['record_writes'], 0)
        self.assertEqual(value['local_writes'], 0)
        self.assertEqual(value['model_calls'], 0)

    def test_judgment_routine_remains_configured(self):
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        npi = next(row for row in services if row['key'] == 'npi-sweep-weekly')
        self.assertEqual(npi['runtime'], 'claude-code-scheduled-task')
        self.assertTrue((ROOT / 'ops/scheduled-tasks/npi-sweep-weekly.SKILL.md').is_file())



if __name__ == '__main__':
    unittest.main()
