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
    def test_timeout_without_rollout_uses_captured_bytes(self):
        with tempfile.TemporaryDirectory() as tmp, catalog_fixture():
            root = Path(tmp)
            reg = desks.Registry(root / 'desks.json')
            reg.register('timeout', 'codex-session', family='sol', effort='high', cwd=tmp)
            output = (b'{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}\n'
                      b'{"type":"item.started","item":{"type":"command_execution"}}\n'
                      b'{"type":"partial')
            expired = subprocess.TimeoutExpired('codex', 1, output=output)
            with patch.object(dispatch.subprocess, 'run', side_effect=expired):
                row = dispatch.dispatch('timeout', 'inspect', registry=reg,
                    results_path=root / 'results.jsonl', codex_timeout_s=1)
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
