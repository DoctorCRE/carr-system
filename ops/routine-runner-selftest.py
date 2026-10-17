#!/usr/bin/env python3
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
    def record_transport(self, calls, *, revoke_after_board=False):
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
            if revoke_after_board and verb == 'loop-board':
                os.environ.pop('CARR_MCP_UNATTENDED_TOKEN', None)
            return subprocess.CompletedProcess(argv, 0, json.dumps({'ok': True, 'loops': []}), '')

        return transport

    def test_all_record_calls_select_unattended_credentials(self):
        calls = []
        env = {'CARR_MCP_CLIENT_PROFILE': 'unattended',
               'CARR_MCP_LOCAL_TOKEN': 'fixture-local', 'CARR_MCP_UNATTENDED_TOKEN': 'fixture-unattended'}
        transport = self.record_transport(calls)
        with patch.dict(os.environ, env, clear=True), patch.object(record_call.subprocess, 'run', transport):
            ctx = runtime.Context('lead-signals-weekly', now=NOW)
            ctx.read('find', {})
            ctx.write('add-loop', {'title': 'Fixture proposal'}, 'proposal')
            ctx.failure('fixture failure')
        self.assertEqual(calls, [(verb, {'profile': 'unattended',
            'tokenVariable': 'CARR_MCP_UNATTENDED_TOKEN', 'hasToken': True, 'borrowedLocal': False})
            for verb in ['find', 'add-loop', 'loop-board', 'add-loop']])

    def test_missing_unattended_token_never_borrows_local_for_records_or_failure(self):
        for operation in ['read', 'write', 'failure', 'failure-write']:
            with self.subTest(operation=operation):
                calls = []
                env = {'CARR_MCP_CLIENT_PROFILE': 'unattended', 'CARR_MCP_LOCAL_TOKEN': 'fixture-local'}
                if operation == 'failure-write':
                    env['CARR_MCP_UNATTENDED_TOKEN'] = 'fixture-unattended'
                transport = self.record_transport(calls, revoke_after_board=operation == 'failure-write')
                with patch.dict(os.environ, env, clear=True), patch.object(record_call.subprocess, 'run', transport):
                    ctx = runtime.Context('lead-signals-weekly', now=NOW)
                    with self.assertRaisesRegex(RuntimeError, 'missing CARR_MCP_UNATTENDED_TOKEN'):
                        if operation == 'read':
                            ctx.read('find', {})
                        elif operation == 'write':
                            ctx.write('add-loop', {'title': 'Fixture proposal'}, 'proposal')
                        else:
                            ctx.failure('fixture failure')
                self.assertEqual([verb for verb, _ in calls], {
                    'read': ['find'], 'write': ['add-loop'], 'failure': ['loop-board'],
                    'failure-write': ['loop-board', 'add-loop']}[operation])
                self.assertEqual(calls[-1][1], {'profile': 'unattended',
                    'tokenVariable': 'CARR_MCP_UNATTENDED_TOKEN', 'hasToken': False, 'borrowedLocal': False})

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
        from lib.headless_tasks import config
        self.assertIn('npi-sweep-weekly', config(ROOT)['tasks'])



if __name__ == '__main__':
    unittest.main()
