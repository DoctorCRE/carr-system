#!/usr/bin/env python3
"""Exercise rotation through the Claude hook process boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from concurrent.futures import ThreadPoolExecutor

HOOK = Path(__file__).resolve().parent.parent / 'hooks/orchestrator-rotation.py'
MARKER = 'CARR-Session-Role: orchestrator'


class Rotation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.transcript = self.home / 'session.jsonl'
        self.write()

    def write(self, tokens=500000, prompt=MARKER, tail=()):
        rows = [dict(type='user', sessionId='test', message=dict(role='user', content=prompt)),
                dict(type='assistant', sessionId='test', message=dict(model='claude-opus-test',
                     usage=dict(input_tokens=10, cache_read_input_tokens=tokens-20,
                                cache_creation_input_tokens=10)))]
        self.transcript.write_text(''.join(json.dumps(r)+'\n' for r in [*rows, *tail]))

    def run_hook(self, event='Stop', **overrides):
        data = dict(hook_event_name=event, session_id='test', transcript_path=str(self.transcript))
        data.update(overrides)
        return subprocess.run([sys.executable, HOOK], input=json.dumps(data), text=True,
                              capture_output=True, env={**os.environ, 'HOME':str(self.home)}, timeout=5)

    def blocked(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['decision'], 'block')
        self.assertIn('handoff/orchestrator-latest', result.stdout)

    def allowed(self, result):
        self.assertEqual((result.returncode, result.stdout), (0, ''), result.stderr)

    def test_below(self):
        self.write(499999)
        self.allowed(self.run_hook())

    def test_at_threshold_once(self):
        self.blocked(self.run_hook())
        self.allowed(self.run_hook())

    def test_above(self):
        self.write(600000)
        self.blocked(self.run_hook())

    def test_worker(self):
        self.write(prompt='Card: WR622.S1.J41\nImplement the hook.')
        self.allowed(self.run_hook())
        self.allowed(self.run_hook('PreCompact', trigger='auto'))

    def test_sidechain(self):
        self.allowed(self.run_hook(agent_id='worker'))

    def test_later_marker_does_not_opt_in(self):
        self.write(prompt='Worker', tail=[dict(type='user', message=dict(content=MARKER))])
        self.allowed(self.run_hook())

    def test_missing(self):
        self.transcript.unlink()
        self.allowed(self.run_hook())

    def test_malformed(self):
        self.transcript.write_text('{broken')
        self.allowed(self.run_hook())

    def test_invalid_usage(self):
        self.write(tail=[dict(type='assistant', message=dict(usage=dict(input_tokens='500000')))])
        self.allowed(self.run_hook())

    def test_latest_not_sum(self):
        self.write(1, tail=[dict(type='assistant', message=dict(usage=dict(input_tokens=20,
                   cache_read_input_tokens=0, cache_creation_input_tokens=0)))])
        self.allowed(self.run_hook())

    def test_manual(self):
        self.allowed(self.run_hook('PreCompact', trigger='manual'))
        self.blocked(self.run_hook('PreCompact', trigger='auto'))

    def test_precompact_first_then_allow(self):
        self.write(200001)
        self.blocked(self.run_hook('PreCompact', trigger='auto'))
        self.allowed(self.run_hook('PreCompact', trigger='auto'))
        self.blocked(self.run_hook())
        self.allowed(self.run_hook())

    def test_precompact_after_turn_instruction(self):
        self.blocked(self.run_hook())
        self.blocked(self.run_hook('PreCompact', trigger='auto'))
        self.allowed(self.run_hook('PreCompact', trigger='auto'))
        self.allowed(self.run_hook())

    def test_sessions_independent(self):
        self.blocked(self.run_hook())
        self.write()
        self.allowed(self.run_hook(session_id='other'))

    def test_concurrent_stops_instruct_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.run_hook(), range(4)))
        self.assertEqual(sum(bool(r.stdout) for r in results), 1)
        self.assertTrue(all(r.returncode == 0 for r in results))

    def test_list_prompt(self):
        self.write(prompt=[dict(type='text', text=MARKER)])
        self.blocked(self.run_hook())

    def test_declarations(self):
        repo = HOOK.parent.parent
        for relative in ('ops/config/hooks.json', '.claude/settings.json'):
            config = json.loads((repo / relative).read_text())
            hooks = config.get('hooks', config)
            for event in ('Stop', 'PreCompact'):
                groups = [g for g in hooks[event] if any('orchestrator-rotation.py' in
                          h.get('command', '') for h in g['hooks'])]
                self.assertEqual(len(groups), 1)
                if event == 'PreCompact':
                    self.assertEqual(groups[0]['matcher'], 'auto')
        self.assertEqual(config['autoCompactWindow'], 700000)
        self.assertNotIn('orchestrator-rotation.py',
                         (repo / 'ops/config/codex-hooks.json').read_text())

    def test_corrupt_state_fails_open(self):
        self.blocked(self.run_hook())
        for path in self.home.glob('.cache/carr/orchestrator-rotation/*.json'):
            path.write_text('{broken')
        self.allowed(self.run_hook())

    def test_configuration(self):
        with unittest.mock.patch.dict(os.environ, CARR_ORCHESTRATOR_ROTATION_TOKENS='600000'):
            self.allowed(self.run_hook())


if __name__ == '__main__':
    unittest.main()
