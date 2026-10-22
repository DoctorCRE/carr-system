#!/usr/bin/env python3
"""Timeout diagnostics and claim release through the real dispatch boundary."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
import write_ownership
from test_codex_models_unit import catalog_fixture
from ops.git_env import fixture_env


class DispatchTimeoutTests(unittest.TestCase):
    def run_job(self, script, *, task='inspect', stream=False, owned=True):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(catalog_fixture())
        subprocess.run(['git', 'init', '-q', str(root)], env=fixture_env(), check=True)
        reg = desks.Registry(root / 'desks.json')
        reg.register('timeout', 'codex-session', family='sol', effort='high', cwd=str(root))
        fake = root / 'codex'
        fake.write_text(f'#!{sys.executable}\n' + script)
        fake.chmod(0o755)
        env = fixture_env({**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}'})
        with contextlib.redirect_stdout(io.StringIO()), \
             patch.object(dispatch, 'CODEX_TIMEOUT_S', 0.5), \
             patch.object(dispatch, 'CODEX_DEADLINE_GRACE_S', 0.8):
            row = dispatch.dispatch('timeout', task.replace('$ROOT', str(root)), registry=reg,
                env=env, writes=['src/*'] if owned else None, results_path=root / 'results.jsonl',
                stream_output=stream)
        self.assertEqual(json.loads((root / 'results.jsonl').read_text().splitlines()[-1]), row)
        with write_ownership.checkout_writer(root):
            pass
        return row, root

    def test_completed_at_deadline_recovers_answer_and_report(self):
        row, root = self.run_job("""
import pathlib, sys, time
root = pathlib.Path(__file__).parent
(root / 'report.txt').write_text('delivery receipt\\n')
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('Report delivered.')
time.sleep(30)
""", task='inspect\nCompletion artifact: {"kind":"report_line","path":"$ROOT/report.txt","contains":"delivery receipt"}')
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['result'], 'Report delivered.')
        self.assertTrue(row['termination_confirmed'])
        self.assertEqual(row['deadline_reconciliation']['artifact']['status'], 'verified')

    def test_still_active_gets_grace_without_relaunch(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                row, root = self.run_job("""
import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
with (root / 'launches').open('a') as f:
    f.write('launch\\n')
for _ in range(8):
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution'}}), flush=True)
    time.sleep(0.1)
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('Completed during grace.')
""", stream=stream)
                self.assertEqual(row['status'], 'completed')
                self.assertEqual(row['result'], 'Completed during grace.')
                self.assertEqual((root / 'launches').read_text(), 'launch\n')
                self.assertEqual(row['deadline_reconciliation']['grace_seconds'], 0.8)
                self.assertTrue(row['deadline_reconciliation']['final_message'])

    def test_stalled_times_out_with_native_state_and_no_replay(self):
        row, root = self.run_job("""
import json, os, pathlib, time
from datetime import datetime, timezone
root = pathlib.Path(__file__).parent
(root / 'launches').write_text('launch')
thread = '11111111-1111-4111-8111-111111111111'
p = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/21' / ('rollout-test-' + thread + '.jsonl')
p.parent.mkdir(parents=True)
p.write_text(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'type': 'event_msg',
    'payload': {'type': 'task_started'}}) + '\\n')
print(json.dumps({'type': 'thread.started', 'thread_id': thread}), flush=True)
time.sleep(30)
""")
        self.assertEqual(row['status'], 'timed_out')
        self.assertTrue(row['termination_confirmed'])
        self.assertEqual(row['deadline_reconciliation']['native_thread']['state'], 'in_progress')
        self.assertEqual((root / 'launches').read_text(), 'launch')
        self.assertNotIn('grace_seconds', row['deadline_reconciliation'])

    def test_completed_at_deadline_checks_pushed_head(self):
        for pushed in (True, False):
            with self.subTest(pushed=pushed):
                row, root = self.run_job("""
import pathlib, subprocess, sys, time
root = pathlib.Path(__file__).parent
def git(*args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
git('init', '--bare', str(root / 'remote.git'))
git('remote', 'add', 'origin', str(root / 'remote.git'))
git('-c', 'user.name=Test', '-c', 'user.email=jbookout@users.noreply.github.com', 'commit', '--allow-empty', '-m', 'delivered')
""" + ("git('push', 'origin', 'HEAD')\n" if pushed else '') + """
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('PR ready.')
time.sleep(30)
""", task='inspect\nCompletion artifact: {"kind":"pushed_head"}')
                self.assertEqual(row['status'], 'completed')
                self.assertEqual(row['deadline_reconciliation']['artifact']['status'],
                                 'verified' if pushed else 'unverified')

    def test_native_completion_recovers_without_last_message_or_write_claim(self):
        row, root = self.run_job("""
import json, os, pathlib, time
from datetime import datetime, timezone
thread = '11111111-1111-4111-8111-111111111111'
p = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/21' / ('rollout-test-' + thread + '.jsonl')
p.parent.mkdir(parents=True)
p.write_text(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'type': 'event_msg',
    'payload': {'type': 'task_complete', 'last_agent_message': 'Native final answer.'}}) + '\\n')
print(json.dumps({'type': 'thread.started', 'thread_id': thread}), flush=True)
time.sleep(30)
""", owned=False)
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['result'], 'Native final answer.')
        self.assertTrue(row['termination_confirmed'])
        self.assertEqual(row['deadline_reconciliation']['native_thread']['state'], 'completed')

    def test_grace_expires_once_even_with_continuing_events(self):
        row, root = self.run_job("""
import json, pathlib, time
(pathlib.Path(__file__).parent / 'launches').write_text('launch')
while True:
    print(json.dumps({'type': 'item.updated', 'item': {'type': 'command_execution'}}), flush=True)
    time.sleep(0.1)
""")
        self.assertEqual(row['status'], 'timed_out')
        self.assertEqual(row['deadline_reconciliation']['grace_seconds'], 0.8)
        self.assertTrue(row['termination_confirmed'])
        self.assertEqual((root / 'launches').read_text(), 'launch')

    def test_old_native_answer_is_not_recovered(self):
        row, root = self.run_job("""
import json, os, pathlib, time
thread = '11111111-1111-4111-8111-111111111111'
p = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/21' / ('rollout-test-' + thread + '.jsonl')
p.parent.mkdir(parents=True)
p.write_text(json.dumps({'timestamp': '2020-01-01T00:00:00Z', 'type': 'event_msg',
    'payload': {'type': 'task_complete', 'last_agent_message': 'Previous task answer.'}}) + '\\n')
print(json.dumps({'type': 'thread.started', 'thread_id': thread}), flush=True)
time.sleep(30)
""")
        self.assertEqual(row['status'], 'timed_out')
        self.assertEqual(row['deadline_reconciliation']['native_thread']['state'], 'unavailable')
        self.assertNotIn('result', row)

    def test_timeout_without_rollout_uses_captured_bytes(self):
        with tempfile.TemporaryDirectory() as tmp, catalog_fixture():
            root = Path(tmp)
            reg = desks.Registry(root / 'desks.json')
            reg.register('timeout', 'codex-session', family='sol', effort='high', cwd=tmp)
            output = (b'{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}\n'
                      b'{"type":"item.started","item":{"type":"command_execution"}}\n'
                      b'{"type":"partial')
            expired = subprocess.TimeoutExpired('codex', 1, output=output)
            with patch.object(dispatch.subprocess, 'run', side_effect=expired) as provider:
                row = dispatch.dispatch('timeout', 'inspect', registry=reg,
                    results_path=root / 'results.jsonl', codex_timeout_s=1, provider_run=provider)
            self.assertEqual(row['status'], 'timed_out')
            self.assertIn('last event item.started', row['detail'])
            self.assertEqual(row['last_activity'], {'source': 'executor_output',
                'last_event_type': 'item.started', 'hook_failures': 0})

    def test_timeout_reports_rollout_hook_loop_and_releases_claim(self):
        for stream in (False, True):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as tmp, catalog_fixture():
                root = Path(tmp)
                subprocess.run(['git', 'init', '-q', str(root)],
                               env=fixture_env(), check=True)
                reg = desks.Registry(root / 'desks.json')
                reg.register('timeout', 'codex-session', family='sol', effort='high', cwd=tmp)
                fake = root / 'codex'
                fake.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, time
pathlib.Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
thread = '11111111-1111-4111-8111-111111111111'
root = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions' / '2026' / '10' / '16'
root.mkdir(parents=True)
p = root / ('rollout-2026-10-16T09-00-00-' + thread + '.jsonl')
failure = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
    'content': [{'type': 'input_text', 'text': '<hook_prompt hook_run_id="stop:16">python: can\\'t open file: [Errno 2] No such file or directory</hook_prompt>'}]}}
with p.open('w') as f:
    f.write(json.dumps({'type': 'session_meta', 'payload': {'id': thread}}) + '\\n')
    f.write(json.dumps({'type': 'response_item', 'payload': {'type': 'function_call_output',
        'output': 'quoted <hook_prompt> [Errno 2] No such file or directory'}}) + '\\n')
    for _ in range(3):
        f.write(json.dumps(failure) + '\\n')
    f.write(json.dumps({'type': 'response_item', 'payload': ['malformed']}) + '\\n')
    f.write('{"type":"response_item","payload":')
print(json.dumps({'type': 'thread.started', 'thread_id': thread}), flush=True)
time.sleep(30)
''')
                fake.chmod(0o755)
                env = fixture_env({**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}'})
                with contextlib.redirect_stdout(io.StringIO()):
                    row = dispatch.dispatch('timeout', 'repair', registry=reg, env=env,
                        writes=['src/*'], results_path=root / 'results.jsonl',
                        codex_timeout_s=0.5, stream_output=stream)
                    self.assertEqual(row['status'], 'timed_out')
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(int(fake.with_suffix('.pid').read_text()), 0)
                    with write_ownership.checkout_writer(tmp):
                        pass
                    self.assertIn('last event response_item.message', row['detail'])
                    self.assertIn('hook failures in rollout tail: 3', row['detail'])
                    self.assertEqual(row['thread_id'], '11111111-1111-4111-8111-111111111111')
                    self.assertEqual(row['last_activity']['hook_failures'], 3)
                    self.assertEqual(json.loads((root / 'results.jsonl').read_text().splitlines()[-1]), row)


if __name__ == '__main__':
    unittest.main()
