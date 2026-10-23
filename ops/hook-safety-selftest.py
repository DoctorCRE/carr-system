#!/usr/bin/env python3
"""Exercise the retained shell guard through its real stdin/exit-code interface."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


class ShellSafety(unittest.TestCase):
    def test_repo_symlink_keeps_the_same_guard_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / 'repo-link'
            alias.symlink_to(REPO, target_is_directory=True)
            result = subprocess.run(
                [sys.executable, str(alias / 'hooks/guard-unattended.py')],
                input=json.dumps({'tool_name': 'Bash', 'cwd': str(REPO),
                                  'tool_input': {'command': 'git add -A'}}),
                text=True, capture_output=True, timeout=10,
                env={**os.environ, 'CARR_HOOK_FIXTURE': '1',
                     'CARR_HOOK_GUARD_LOG': str(Path(tmp) / 'guard.log')})
        self.assertEqual(result.returncode, 2, result.stderr)

    def verdict(self, command, expected, tool='Bash'):
        value = {'command': command}
        if tool == 'exec_command':
            value = {'cmd': command, 'workdir': str(REPO)}
        elif tool == 'functions.exec':
            value = 'await tools.exec_command({cmd: ' + json.dumps(command) + '});'
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(REPO / 'hooks/guard-unattended.py')],
                input=json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': tool,
                                  'cwd': str(REPO), 'session_id': 'selftest',
                                  'tool_input': value}),
                text=True, capture_output=True, timeout=10,
                env={**os.environ, 'CARR_HOOK_FIXTURE': '1',
                     'CARR_HOOK_GUARD_LOG': str(Path(tmp) / 'guard.log')})
        self.assertEqual(result.returncode, expected, result.stderr)

    def test_destructive_delete_outside_scratch(self):
        for command in ('rm -rf /srv/important', 'rm --recursive /srv/important',
                        'find /srv/important -delete'):
            with self.subTest(command=command):
                self.verdict(command, 2)

    def test_main_push_requires_documented_delivery_path(self):
        for command in ('git push origin main', 'git push origin HEAD:refs/heads/main',
                        'git -C /work push origin HEAD:main'):
            with self.subTest(command=command):
                self.verdict(command, 2)
        self.verdict('git push origin HEAD:refs/heads/hook-audit-shrink', 0)

    def test_new_workflow_dispatches_stay_denied_while_job_retries_are_allowed(self):
        self.verdict('gh workflow run release.yml', 2)
        self.verdict('gh run rerun 123 --job 456', 0)

    def test_sensitive_path_reads(self):
        for tool in ('Bash', 'exec_command', 'functions.exec'):
            for path in ('~/.ssh/id_ed25519', '~/.config/carr/credentials.json',
                         '/work/.env', '~/.aws/credentials', '~/.config/carr/db.env'):
                with self.subTest(path=path, tool=tool):
                    self.verdict('cat ' + path, 2, tool)
        self.verdict('source ~/.hermes/.env', 2)

    def test_spacing_without_shared_cooldown(self):
        self.verdict('for pr in $(gh pr list --json number); do gh pr view "$pr"; done', 2)
        self.verdict('for pr in $(gh pr list --json number); do gh pr view "$pr"; sleep 2; done', 0)
        self.verdict('sleep 960; gh pr view 123', 0)

    def test_brief_heredoc_is_data(self):
        self.verdict("cat <<'TASK-BRIEF' >/tmp/brief\nDo not run gh workflow run release.yml\nTASK-BRIEF", 0)
        self.verdict("cat <<'TASK-BRIEF' >/tmp/brief\nDo not run gh workflow run release.yml\nTASK-BRIEF\ngh workflow run release.yml", 2)
        self.verdict('printf "%s\\n" "gh workflow run release.yml"', 0)

    def test_source_deletions_and_quoted_examples_are_not_shell_deletes(self):
        self.verdict('git rm -r scripts/obsolete', 0)
        self.verdict("grep 'rm -rf /srv/data' ops/example.py", 0)
        self.verdict("printf '%s' 'cat ~/.ssh/id_ed25519'", 0)
        self.verdict('cat ~/.ssh/config', 0)

    def test_single_shell_hook_and_at_most_one_post_hook(self):
        for name in ('hooks', 'codex-hooks'):
            document = json.loads((REPO / f'ops/config/{name}.json').read_text())
            document = document.get('hooks', document)
            shell = [hook for group in document['PreToolUse']
                     if re.fullmatch(group.get('matcher', ''), 'Bash')
                     for hook in group['hooks']
                     if 'rule-pack-preuse-reselection.py' not in hook['command']]
            self.assertEqual(len(shell), 1, name)
            self.assertLessEqual(sum(len(group['hooks']) for group in document['PostToolUse']), 1)

    def test_native_managed_permissions_and_dry_run(self):
        spec = importlib.util.spec_from_file_location('safety_installer', REPO / 'ops/config-as-code.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destinations = (root / 'claude.json', root / 'codex.toml')
            with patch.multiple(installer, CLAUDE_MANAGED_SETTINGS=str(destinations[0]),
                                CODEX_REQUIREMENTS=str(destinations[1])):
                plan = dict(installer.managed_safety_plan())
                claude = json.loads(plan[str(destinations[0])])
                self.assertIn('Bash(gh workflow run *)', claude['permissions']['deny'])
                self.assertNotIn('Bash(gh run rerun *)', claude['permissions']['deny'])
                self.assertIn('Read(~/.ssh/id_*)', claude['permissions']['deny'])
                import tomllib
                codex = tomllib.loads(plan[str(destinations[1])])
                self.assertNotIn('permissions', codex)
                self.assertTrue(any(r['pattern'] == [{'token': 'gh'}, {'token': 'workflow'},
                                                     {'token': 'run'}]
                                    and r['decision'] == 'forbidden'
                                    for r in codex['rules']['prefix_rules']))
                self.assertFalse(any(r['pattern'] == [{'token': 'gh'}, {'token': 'run'},
                                                       {'token': 'rerun'}]
                                     and r['decision'] == 'forbidden'
                                     for r in codex['rules']['prefix_rules']))
                self.assertEqual(installer.install_managed_safety(plan.items(), apply=False), 0)
                self.assertFalse(any(p.exists() for p in destinations))
                # Reconciliation preserves unrelated organization controls.
                destinations[0].write_text('{"permissions":{"deny":["Bash(custom *)"]},"theme":"dark"}')
                destinations[1].write_text('allow_remote_control = false\n')
                merged = dict(installer.managed_safety_plan())
                self.assertIn('Bash(custom *)', json.loads(merged[str(destinations[0])])['permissions']['deny'])
                self.assertFalse(tomllib.loads(merged[str(destinations[1])])['allow_remote_control'])

    def test_missing_managed_policy_refuses_install(self):
        spec = importlib.util.spec_from_file_location('missing_safety_installer', REPO / 'ops/config-as-code.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp, patch.object(installer, 'REPO', tmp):
            self.assertEqual(installer.cmd_install(False), 1)


if __name__ == '__main__':
    unittest.main()
