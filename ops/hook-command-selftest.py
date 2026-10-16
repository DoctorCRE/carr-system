#!/usr/bin/env python3
import json
import os
from pathlib import Path
from contextlib import contextmanager
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
SOURCES = ('ops/config/hooks.json', 'ops/config/codex-hooks.json',
           'ops/config/claude-continuity-hooks.json', '.claude/settings.json')


def commands():
    for source in SOURCES:
        document = json.loads((REPO / source).read_text())
        for event, groups in document.get('hooks', document).items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                for hook in group.get('hooks', []):
                    yield source, event, hook['command']


@contextmanager
def fixture(template):
    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        repo = home / 'carr-system'
        (repo / 'hooks').mkdir(parents=True)
        (repo / 'ops').mkdir()
        (repo / '.venv/bin').mkdir(parents=True)
        (repo / '.venv/bin/python').symlink_to(sys.executable)
        command = template.replace('{{REPO}}', str(repo))
        relative = re.search(r'/(hooks|ops)/([\w-]+\.py)', command)
        script = repo / relative.group(1) / relative.group(2)
        env = {**os.environ, 'HOME': str(home), 'CLAUDE_PROJECT_DIR': str(home / 'branch')}
        def run(payload='payload'):
            return subprocess.run(['/bin/sh', '-c', command], input=payload,
                                  text=True, capture_output=True, env=env, timeout=5)
        yield script, run


class HookCommands(unittest.TestCase):
    def test_installer_discovers_only_the_invoked_script(self):
        installer = runpy.run_path(str(REPO / 'ops/config-as-code.py'))
        command_target = runpy.run_path(str(REPO / 'ops/hook-deletion-check.py'))['command_target']
        root = Path(installer['REPO']).resolve()
        for source, event, template in commands():
            with self.subTest(source=source, event=event, command=template):
                block = {event: [{'hooks': [{'command': template}]}]}
                self.assertEqual(installer['hook_block_script_paths'](block),
                                 [str(command_target(template, root).resolve())])

    def test_all_configured_hooks_have_an_explicit_missing_script_policy(self):
        policy = json.loads((REPO / 'ops/config/delegation-gate-hook.json').read_text())['missing_script_policy']
        safety = set(policy['safety'])
        optional = set(policy['optional'])
        self.assertFalse(safety & optional)
        self.assertEqual(policy['safety_stop_failure_limit'], 1)
        self.assertTrue({'hooks/guard-unattended.py', 'hooks/gate-integrity.py',
                         'hooks/scheduled-run-record.py'} <= safety)
        seen = set()
        for source, event, template in commands():
            with self.subTest(source=source, event=event, command=template), fixture(template) as (script, run):
                path = f'{script.parent.name}/{script.name}'
                seen.add(path)
                self.assertIn(path, safety | optional)
                result = run()
                self.assertEqual(result.returncode, 0, result.stderr)
                if path in safety:
                    output = json.loads(result.stdout)
                    self.assertIn('CARR_SAFETY_HOOK_MISSING', result.stderr)
                    if event == 'PreToolUse':
                        self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny')
                    else:
                        self.assertIs(output['continue'], False)
                        self.assertIn(path, output['stopReason'])
                else:
                    self.assertEqual(result.stdout, '')
                    self.assertIn('WARNING', result.stderr)
        self.assertEqual(seen, safety | optional)

    def test_missing_safety_hook_denies(self):
        for source, event, template in commands():
            if event != 'PreToolUse' or 'guard-unattended.py' not in template:
                continue
            with self.subTest(source=source), fixture(template) as (script, run):
                result = run()
                self.assertEqual(result.returncode, 0, result.stderr)
                decision = json.loads(result.stdout)['hookSpecificOutput']
                self.assertEqual(decision['hookEventName'], 'PreToolUse')
                self.assertEqual(decision['permissionDecision'], 'deny')
                self.assertIn('CARR_SAFETY_HOOK_MISSING', result.stderr)
                self.assertIn(script.name, decision['permissionDecisionReason'])

    def test_missing_integrity_checker_terminates(self):
        for source, event, template in commands():
            if 'gate-integrity.py' not in template:
                continue
            with self.subTest(source=source, event=event), fixture(template) as (script, run):
                result = run()
                output = json.loads(result.stdout)
                self.assertIs(output['continue'], False)
                self.assertIn('CARR_SAFETY_HOOK_MISSING', output['stopReason'])
                self.assertIn(script.name, result.stderr)

    def test_missing_optional_hook_warns_and_passes(self):
        for source, event, template in commands():
            if 'session-presence-hook.py' not in template:
                continue
            with self.subTest(source=source, event=event), fixture(template) as (script, run):
                result = run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('WARNING', result.stderr)
                self.assertIn(script.name, result.stderr)

    def test_missing_safety_stop_is_terminal_within_one_failure(self):
        stops = [row for row in commands() if row[1] == 'Stop' and 'scheduled-run-record.py' in row[2]]
        self.assertTrue(stops, 'exercise a real configured safety Stop')
        for source, event, template in stops:
            with self.subTest(source=source), fixture(template) as (script, run):
                for active in (False, True):
                    result = run(json.dumps({'hook_event_name': event, 'stop_hook_active': active}))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = json.loads(result.stdout)
                    self.assertIs(output['continue'], False)
                    self.assertNotEqual(output.get('decision'), 'block')
                    self.assertIn('CARR_SAFETY_HOOK_MISSING', output['stopReason'])
                    self.assertIn(script.name, result.stderr)

    def test_present_scripts_keep_their_exit_status(self):
        for source, event, template in commands():
            with self.subTest(source=source, event=event, command=template), fixture(template) as (script, run):
                script.write_text('import os, sys\nprint(sys.stdin.read())\nprint(sys.argv[1:])\n'
                                  'print(os.environ.get("CARR_MCP_CLIENT_PROFILE", ""))\nsys.exit(2)\n')
                result = run()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('payload', result.stdout)
                if 'continuity' in script.name:
                    self.assertIn('continuity', result.stdout)
                if 'session-presence' in script.name:
                    self.assertIn('codex' if 'codex' in source else 'claude', result.stdout)
                script.unlink()
                script.mkdir()
                result = run('')
                if result.returncode == 0:
                    output = json.loads(result.stdout)
                    self.assertTrue(output.get('continue') is False or
                                    output.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny',
                                    'an invalid safety script must refuse')


if __name__ == '__main__':
    unittest.main()
