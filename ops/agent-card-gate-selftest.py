#!/usr/bin/env python3
"""Exercise launch admission through the hook's JSON stdin boundary."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent


class AgentCardGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('hooks', 'lib', 'tools'):
            (self.root / directory).mkdir()
        for relative in ('hooks/agent-card-gate.py', 'hooks/cmd_text.py', 'hooks/guard-unattended.py',
                         'hooks/gate_paths.py', 'hooks/refused_content.py', 'lib/hook_runtime.py'):
            if (REPO / relative).exists():
                shutil.copyfile(REPO / relative, self.root / relative)
        self.lookup = self.root / 'tools/call-verb.py'
        self.lookup.write_text('import json, sys\nassert sys.argv[1:] == ["work-queue", "{}"]\n'
                               'print(json.dumps({"ok": True, "jobs": []}))\n')

    def run_hook(self, tool='Agent', **inputs):
        return subprocess.run([sys.executable, self.root / 'hooks/agent-card-gate.py'],
                              input=json.dumps({'hook_event_name': 'PreToolUse',
                                                'tool_name': tool, 'tool_input': inputs}),
                              text=True, capture_output=True, env=fixture_env(), timeout=10)

    def test_agent_without_card_denied_with_mint_command(self):
        result = self.run_hook(prompt='Review the plan.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('./run.sh call dispatch-job', result.stderr)

    def test_unregistered_card_denied(self):
        result = self.run_hook(prompt='Card: WR557.S6.J99999\nReview the plan.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('not registered', result.stderr)

    def register_fixture(self):
        self.lookup.write_text('import json, sys\nassert sys.argv[1:] == ["work-queue", "{}"]\n'
                               'print(json.dumps({"ok": True, "jobs": [{"card_id": "WR557.S6.J1"}]}))\n')

    def test_registered_card_allowed(self):
        self.register_fixture()
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview the plan.')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bash_codex_exec_without_card_denied(self):
        result = self.run_hook('Bash', command='codex exec "Review the plan."')
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_other_bash_and_launcher_untouched(self):
        for command in ('rg Card hooks', './bin/codex-job DoctorCRE/carr-system review "Review the plan."',
                        "printf '%s' 'codex exec review'", "cat <<'EOF'\ncodex exec review\nEOF"):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook('Bash', command=command).returncode, 0)

    def test_bash_registered_prompt_allowed(self):
        self.register_fixture()
        for command in ("codex exec 'Card: WR557.S6.J1\nReview.'",
                        "timeout 30 /opt/bin/codex exec -c 'model=default' 'Card: WR557.S6.J1\nReview.'",
                        "codex -m default exec --skip-git-repo-check 'Card: WR557.S6.J1\nReview.'"):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_card_outside_launch_prompt_does_not_authorize(self):
        self.register_fixture()
        for command in ("printf 'Card: WR557.S6.J1\n'; codex exec Review",
                        "codex exec -c 'Card: WR557.S6.J1\n' Review",
                        "codex exec 'Card: WR557.S6.J1\nReview.'; codex exec Review",
                        "codex -m default exec Review"):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_explore_has_no_exemption(self):
        self.assertEqual(self.run_hook(prompt='Search code.', subagent_type='Explore').returncode, 2)

    def test_lookup_failure_denies(self):
        for source in ('raise SystemExit(1)', 'print("invalid JSON")',
                       '''print('{"ok": false, "jobs": []}')''', '''print('{"ok": true}')'''):
            with self.subTest(source=source):
                self.lookup.write_text(source)
                result = self.run_hook(prompt='Card: WR557.S6.J1\nReview.')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('lookup unreachable or invalid', result.stderr)

    def test_project_settings_guard_both_launch_tools(self):
        groups = json.loads((REPO / '.claude/settings.json').read_text())['hooks']['PreToolUse']
        for tool in ('Agent', 'Bash'):
            self.assertTrue(any(tool in group.get('matcher', '').split('|') and
                                any('agent-card-gate.py' in hook['command']
                                    for hook in group['hooks']) for group in groups))

    def test_queue_timeout_denies(self):
        self.lookup.write_text('import time\ntime.sleep(8)\n')
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('TimeoutExpired', result.stderr)

    def test_malformed_or_duplicate_card_denied(self):
        self.register_fixture()
        for prompt in ('Card: WR0.S6.J1', 'Card: WR557.S6.J0',
                       'Card: WR557.S6.J1 extra', 'text Card: WR557.S6.J1',
                       'Card: WR557.S6.J1\nCard: WR557.S6.J1'):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.run_hook(prompt=prompt).returncode, 2)


if __name__ == '__main__':
    unittest.main()
