#!/usr/bin/env python3
"""T3 dispatch admission through configured matching and hook JSON input."""
import copy
import json
from pathlib import Path
import re
import runpy
import subprocess
import unittest

REPO = Path(__file__).resolve().parent.parent
AgentCardGate = runpy.run_path(str(REPO / 'ops/agent-card-gate-selftest.py'))['AgentCardGate']


class T3AgentCardGate(AgentCardGate):
    def dispatches(self, prompt):
        return {
            'delegate_task': {'task': prompt},
            't3_queue': {'action': 'edit', 'queuedRunId': 'fixture', 'text': prompt},
            't3_thread_launch': {'title': 'Review', 'message': prompt},
            'create_threads': {'threads': [{'prompt': prompt}, {'prompt': prompt}]},
            't3_thread_send': {'threadId': 'fixture', 'message': prompt},
            'schedule_task': {'prompt': prompt, 'schedule': {'type': 'interval', 'everyMs': 60000}},
            't3_thread_send_attachments': {'threadId': 'fixture', 'message': prompt,
                                           'attachments': [{'id': 'fixture'}]},
        }

    def configured_call(self, name, inputs):
        tool = 'mcp__t3-code__' + name
        groups = json.loads((REPO / '.claude/settings.json').read_text())['hooks']['PreToolUse']
        if any(re.search(group.get('matcher', ''), tool) and
               any('agent-card-gate.py' in hook['command'] for hook in group['hooks'])
               for group in groups):
            return self.run_hook(tool, **inputs)
        return subprocess.CompletedProcess([], 0, '', '')

    def test_t3_dispatch_requires_registered_card_through_matcher(self):
        self.register_fixture()
        for prompt, code in [('Review.', 2), ('Card: WR557.S6.J1\nReview.', 0),
                             ('Card: WR557.S6.J99999\nReview.', 2)]:
            for name, inputs in self.dispatches(prompt).items():
                with self.subTest(name=name, prompt=prompt):
                    result = self.configured_call(name, inputs)
                    self.assertEqual(result.returncode, code, result.stderr)

    def test_every_batch_prompt_needs_its_own_card(self):
        self.register_fixture()
        for bad in ['Review.', 'Card: WR557.S6.J99999', None]:
            for index in [0, 1]:
                inputs = copy.deepcopy(self.dispatches('Card: WR557.S6.J1')['create_threads'])
                inputs['threads'][index]['prompt'] = bad
                with self.subTest(bad=bad, index=index):
                    self.assertEqual(self.configured_call('create_threads', inputs).returncode, 2)

    def test_missing_or_malformed_dispatch_prompts_fail_closed(self):
        for name in self.dispatches('Review.'):
            with self.subTest(name=name):
                self.assertEqual(self.configured_call(name, {}).returncode, 2)
        for threads in [[], None, 'Review.', [{}], [None]]:
            with self.subTest(threads=threads):
                self.assertEqual(self.configured_call('create_threads', {'threads': threads}).returncode, 2)

    def test_opaque_dispatch_cannot_bypass_prompt_validation(self):
        for name, inputs in [('run_scheduled_task_now', {'taskId': 'fixture'}),
                             ('t3_queue', {'action': 'promote_to_steer', 'queuedRunId': 'fixture'})]:
            with self.subTest(name=name):
                self.assertEqual(self.configured_call(name, inputs).returncode, 2)

    def test_queue_reads_and_cancellations_do_not_check_cards(self):
        self.lookup.write_text('raise SystemExit("queue must not be read")')
        for action in ['list', 'read', 'cancel', 'reorder']:
            with self.subTest(action=action):
                self.assertEqual(self.configured_call('t3_queue', {'action': action}).returncode, 0)

    def test_wrong_field_cannot_authorize_dispatch(self):
        self.register_fixture()
        for name, inputs in self.dispatches('Review.').items():
            inputs['description'] = 'Card: WR557.S6.J1'
            with self.subTest(name=name):
                self.assertEqual(self.configured_call(name, inputs).returncode, 2)

    def test_unattended_guard_sees_dispatch_tools(self):
        for relative in ['ops/config/hooks.json', 'ops/config/codex-hooks.json']:
            document = json.loads((REPO / relative).read_text())
            groups = document.get('hooks', document)['PreToolUse']
            for name in self.dispatches('Review.'):
                with self.subTest(config=relative, name=name):
                    self.assertTrue(any(re.search(group.get('matcher', ''), 'mcp__t3-code__' + name)
                                        and any('guard-unattended.py' in h['command']
                                                for h in group['hooks']) for group in groups))

    def test_read_tools_are_untouched(self):
        self.lookup.write_text('raise SystemExit("queue must not be read")')
        groups = json.loads((REPO / '.claude/settings.json').read_text())['hooks']['PreToolUse']
        for name in ['t3_thread_list', 't3_thread_read', 'task_status', 't3_thread_wait',
                     'list_scheduled_tasks', 'orchestrator_capabilities']:
            tool = 'mcp__t3-code__' + name
            with self.subTest(name=name):
                self.assertFalse(any(re.search(group.get('matcher', ''), tool) and
                                     any('agent-card-gate.py' in h['command'] for h in group['hooks'])
                                     for group in groups))
                self.assertEqual(self.run_hook(tool).returncode, 0)


del AgentCardGate


if __name__ == '__main__':
    unittest.main()
