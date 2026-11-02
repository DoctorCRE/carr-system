#!/usr/bin/env python3
"""Exercise the orchestrator question gate through the Claude process boundary."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'hooks/orchestrator-question.py'
MARKER = 'CARR-Session-Role: orchestrator'
REASON = ('Orchestrator sessions never block on a question (rule d1a7973a, standing order 24). '
          'Park the call as a gate with a default, apply the default or route work around it, '
          'keep draining, and put the question as plain text in your report.')


class Questions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.transcript = Path(self.temp.name) / 'session.jsonl'
        self.write([self.user(MARKER)])

    def user(self, content, **fields):
        return dict(type='user', sessionId='test', message=dict(content=content), **fields)

    def write(self, rows):
        self.transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def run_hook(self, **overrides):
        event = dict(hook_event_name='PreToolUse', tool_name='AskUserQuestion',
                     session_id='test', transcript_path=str(self.transcript), tool_input={})
        event.update(overrides)
        result = subprocess.run([sys.executable, HOOK], input=json.dumps(event),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def test_marked_question_is_denied(self):
        self.assertEqual(self.run_hook(), dict(hookSpecificOutput=dict(
            hookEventName='PreToolUse', permissionDecision='deny', permissionDecisionReason=REASON)))

    def test_exit_plan_mode_is_denied(self):
        self.assertEqual(self.run_hook(tool_name='ExitPlanMode')['hookSpecificOutput']
                         ['permissionDecision'], 'deny')

    def test_other_tools_and_events_are_untouched(self):
        for tool in ('Read', 'Bash', 'EnterPlanMode', 'Agent', 'mcp__example__lookup'):
            with self.subTest(tool=tool):
                self.assertIsNone(self.run_hook(tool_name=tool))
        self.assertIsNone(self.run_hook(hook_event_name='PostToolUse'))

    def test_workers_and_later_markers_are_untouched(self):
        for prompt in ('Worker', 'prefix ' + MARKER, MARKER + ' suffix', ' ' + MARKER):
            with self.subTest(prompt=prompt):
                self.write([self.user(prompt), self.user(MARKER)])
                self.assertIsNone(self.run_hook())

    def test_meta_sidechain_agent_events_and_other_sessions_do_not_opt_in(self):
        self.write([self.user(MARKER, isMeta=True), self.user(MARKER, isSidechain=True),
                    dict(type='agent', message=dict(content=MARKER)),
                    dict(type='user', sessionId='other', message=dict(content=MARKER)),
                    self.user('Worker')])
        self.assertIsNone(self.run_hook())

    def test_text_blocks_after_meta_and_sidechain_are_marked(self):
        self.write([self.user('metadata', isMeta=True), self.user('worker', isSidechain=True),
                    self.user([dict(type='image'), dict(type='text', text='Start\n' + MARKER)])])
        self.assertIsNotNone(self.run_hook())

    def test_agent_event_is_untouched(self):
        self.assertIsNone(self.run_hook(agent_id='worker'))

    def test_unreadable_transcript_fails_open(self):
        self.transcript.unlink()
        self.assertIsNone(self.run_hook())

    def test_malformed_transcript_fails_open(self):
        self.transcript.write_text('{broken')
        self.assertIsNone(self.run_hook())

    def test_missing_session_fails_open(self):
        for session in ('', None, 3):
            self.assertIsNone(self.run_hook(session_id=session))

    def test_usage_counters_do_not_affect_question_denial(self):
        self.write([self.user(MARKER), dict(type='assistant', message=dict(usage={}))])
        self.assertIsNotNone(self.run_hook())

    def test_registration(self):
        config = json.loads((ROOT / 'ops/config/hooks.json').read_text())
        groups = [g for g in config['PreToolUse'] if any('orchestrator-question.py' in
                  h.get('command', '') for h in g['hooks'])]
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0]['matcher'].split('|')), {'AskUserQuestion', 'ExitPlanMode'})
        self.assertNotIn('orchestrator-question.py', (ROOT / 'ops/config/codex-hooks.json').read_text())


if __name__ == '__main__':
    unittest.main()
