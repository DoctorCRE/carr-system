"""Native Grok hook lifecycle, without starting a model or changing user config."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from hooks import grok_invocation

spec = importlib.util.spec_from_file_location('boot_fixture', REPO / 'ops/rule-boot-gate-selftest.py')
assert spec is not None and spec.loader is not None
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class GrokBootTests(unittest.TestCase):
    def test_registered_post_hooks_observe_native_terminal_and_mcp_fetches(self):
        hooks = json.loads((REPO / 'ops/config/hooks.json').read_text())
        for event in ('PostToolUse', 'PostToolUseFailure'):
            for tool in ('run_terminal_command', 'carr__standing_context'):
                matches = [h for entry in hooks[event] if re.fullmatch(entry['matcher'], tool)
                           for h in entry['hooks'] if 'rule-boot-gate.py' in h['command']]
                self.assertTrue(matches, (event, tool))

    def test_single_retrieval_is_exempt_but_workspace_and_prompt_flags_are_not(self):
        prefix = 'grok --model latest --reasoning-effort high --max-turns 20 --always-approve --sandbox '
        for suffix, expected in (
                ('read-only --output-format streaming-json --single explain', True),
                ('read-only --output-format streaming-json --print explain', True),
                ('workspace --output-format streaming-json --single explain --sandbox read-only', False),
                ('workspace --output-format streaming-json --single --print', False)):
            with self.subTest(suffix=suffix), patch.dict(os.environ, {grok_invocation.READ_ONLY_ENV: '1'}), \
                    patch.object(grok_invocation, '_grok_owner_command', return_value=prefix + suffix):
                self.assertEqual(grok_invocation.bounded_grok_read_only(), expected)

    def test_installed_native_image_is_an_authenticated_grok_owner(self):
        result = subprocess.CompletedProcess([], 0, '1 grok --single task', '')
        with patch.object(grok_invocation.subprocess, 'run', return_value=result), \
                patch.object(grok_invocation, '_process_image', return_value='/opt/grok/bin/grok-native'):
            self.assertTrue(grok_invocation.grok_session())

    def test_writable_grok_is_held_until_native_fetch_result_confirms_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = fixture.Case(str(REPO), tmp)
            case.stub_sized('abcd1234', pages=1)
            def invoke(event, **fields):
                native_event = {'SessionStart': 'session_start', 'PreToolUse': 'pre_tool_use', 'PostToolUse': 'post_tool_use'}[event]
                payload = {'hook_event_name': event, 'hookEventName': native_event,
                           'sessionId': fixture.SESSION, 'cwd': str(REPO), **fields}
                proc = subprocess.run([sys.executable, str(REPO / 'hooks/rule-boot-gate.py')],
                    input=json.dumps(payload), capture_output=True, text=True, env=case.env, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                return json.loads(proc.stdout) if proc.stdout.strip() else {}
            start = invoke('SessionStart', source='new')
            self.assertIn('RULE BOOT', start['systemMessage'])
            ordinary = {'toolName': 'run_terminal_command', 'toolInput': {'command': 'printf grok-tool-ok'}}
            self.assertEqual(invoke('PreToolUse', **ordinary)['hookSpecificOutput']['permissionDecision'], 'deny')
            # Grok exposes native MCP names and camelCase hook aliases.
            fetch = {'toolName': 'carr__standing_context', 'toolInput': {'detail': 'boot', 'page': 1}}
            self.assertNotIn('permissionDecision', invoke('PreToolUse', **fetch).get('hookSpecificOutput', {}))
            invoke('PostToolUse', **fetch, toolResponse=[{'type': 'text', 'text': json.dumps({
                'ok': True, 'rule_boot': case.boot(1)})}])
            self.assertNotIn('permissionDecision', invoke('PreToolUse', **ordinary).get('hookSpecificOutput', {}))
            actual = subprocess.run(['sh', '-c', ordinary['toolInput']['command']], capture_output=True, text=True)
            self.assertEqual(actual.stdout, 'grok-tool-ok')

    def test_authenticated_writable_owner_does_not_skip_boot(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(grok_invocation, '_grok_owner_command', return_value='grok --sandbox workspace --single fix'):
            case = fixture.Case(str(REPO), tmp)
            case.stub_sized('abcd1234', pages=1)
            case.arm()
            self.assertEqual(case.call('run_terminal_command', {'command': 'pwd'})['permissionDecision'], 'deny')

    def test_native_terminal_fetch_uses_the_same_strict_fetch_grammar(self):
        from lib.rule_boot_gate import classify
        command = str(REPO / 'run.sh') + ' call standing-context \'{"detail":"boot","page":1}\''
        self.assertEqual(classify('run_terminal_command', {'command': command}, str(REPO)), ('fetch', 1))
        self.assertEqual(classify('run_terminal_command', {'command': command + '; touch /tmp/effect'}, str(REPO))[0], 'other')


if __name__ == '__main__':
    unittest.main()
