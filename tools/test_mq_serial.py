#!/usr/bin/env python3
"""Serial merge ownership through the shell command interface."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class SerialQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'PATH': f'{self.root}:{os.environ["PATH"]}',
                    'CARR_MQ_LOCK_ROOT': str(self.root / 'locks'),
                    'MQ_FIXTURE': str(self.root), 'R': 'test/repo'}
        self.script('sleep', '#!/bin/sh\nexit 0\n')
        self.script('gh', f'#!{sys.executable}\n' + '''
import json, os, sys, time
from pathlib import Path
root = Path(os.environ['MQ_FIXTURE'])
with (root / 'calls').open('a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['pr', 'view']:
    marker = root / 'entered'
    if not marker.exists():
        marker.touch()
        while not (root / 'release').exists(): time.sleep(.01)
    print(json.dumps(dict(state='MERGED', mergeStateStatus='CLEAN', headRefOid='abc', statusCheckRollup=[])))
''')

    def script(self, name, content):
        path = self.root / name
        path.write_text(content)
        path.chmod(0o755)

    def command(self, *args):
        return ['bash', str(ROOT / 'tools/mq.sh'), *args]

    def start(self, *args):
        proc = subprocess.Popen(self.command(*args), env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.monotonic() + 5
        while not (self.root / 'entered').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.root / 'entered').exists())
        return proc

    def test_second_runner_for_repo_is_refused_before_github(self):
        first = self.start('12')
        try:
            second = subprocess.run(self.command('13'), env=self.env, text=True,
                                    capture_output=True, timeout=5)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('already running', second.stderr)
            self.assertIn('test/repo', second.stderr)
            calls = (self.root / 'calls').read_text().splitlines()
            self.assertEqual(len(calls), 1)
        finally:
            (self.root / 'release').touch()
            first.communicate(timeout=5)

    def test_dead_runner_is_recovered(self):
        first = self.start('12')
        lock, = (self.root / 'locks').rglob('*.lock')
        self.assertEqual(int((lock / 'pid').read_text()), first.pid)
        self.assertGreater(float((lock / 'start').read_text()), 0)
        first.kill()
        first.wait(timeout=5)
        (self.root / 'release').touch()
        first.communicate(timeout=5)
        result = subprocess.run(self.command('13'), env=self.env, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MERGED 13', result.stdout)
        self.assertFalse(lock.exists())

    def test_other_repository_can_run_while_first_is_live(self):
        first = self.start('12')
        try:
            env = {**self.env, 'R': 'test/other'}
            second = subprocess.run(self.command('13'), env=env, text=True,
                                    capture_output=True, timeout=5)
            self.assertEqual(second.returncode, 0, second.stderr)
        finally:
            (self.root / 'release').touch()
            first.communicate(timeout=5)

    def test_queue_merges_top_down_logs_and_consumes_terminal_entries(self):
        queue = self.root / 'queue'
        log = self.root / 'queue.log'
        queue.write_text('12\n13\n')
        self.script('sleep', f'#!{sys.executable}\n' + '''
import os, signal, sys
if sys.argv[1] == '60': os.kill(os.getppid(), signal.SIGTERM)
''')
        self.script('gh', f'#!{sys.executable}\n' + '''
import json, os, sys
from pathlib import Path
root = Path(os.environ['MQ_FIXTURE'])
args = sys.argv[1:]
with (root / 'calls').open('a') as log: log.write(json.dumps(args) + '\\n')
if args[:2] == ['pr', 'view']:
    print(json.dumps(dict(state='OPEN', mergeStateStatus='CLEAN', headRefOid='abc', statusCheckRollup=[])))
''')
        result = subprocess.run(self.command('--queue', 'test/repo', str(queue), str(log)),
                                env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertEqual(queue.read_text(), '')
        self.assertEqual([line.split(' test/repo ')[1] for line in log.read_text().splitlines()],
                         ['MERGED 12 abc', 'MERGED 13 abc'])
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertEqual([call[:3] for call in calls],
                         [['pr', 'view', '12'], ['pr', 'merge', '12'],
                          ['pr', 'view', '13'], ['pr', 'merge', '13']])
        self.assertFalse(list((self.root / 'locks').rglob('*.lock')))

    def test_queue_and_direct_modes_share_one_lock(self):
        queue = self.root / 'queue'
        queue.write_text('12\n')
        first = self.start('--queue', 'test/repo', str(queue), str(self.root / 'queue.log'))
        try:
            result = subprocess.run(self.command('13'), env=self.env, text=True,
                                    capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('already running', result.stderr)
        finally:
            first.terminate()
            (self.root / 'release').touch()
            first.communicate(timeout=5)


class MergeHookTests(unittest.TestCase):
    def test_native_codex_javascript_commands_are_inspected(self):
        code = 'await tools.exec_command({cmd: "gh pr merge 12 --squash"});'
        for tool_input in (code, {'code': code}):
            with self.subTest(tool_input=tool_input), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run([sys.executable, str(ROOT / 'hooks/github-burst-guard.py')],
                    input=json.dumps({'tool_name': 'functions.exec', 'tool_input': tool_input}),
                    env={**os.environ, 'CARR_GITHUB_BURST_STATE': tmp + '/state',
                         'CARR_HOOK_GUARD_LOG': tmp + '/log'}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('tools/mq.sh', result.stderr)

    def test_all_desks_wire_the_same_pretool_guard(self):
        for client in ('hooks.json', 'codex-hooks.json'):
            config = json.loads((ROOT / 'ops/config' / client).read_text())
            commands = [hook['command'] for group in config.get('hooks', config)['PreToolUse']
                        for hook in group['hooks']]
            self.assertTrue(any('hooks/github-burst-guard.py' in cmd for cmd in commands), client)

    def test_direct_mutations_are_denied_in_every_tool_shape(self):
        commands = [
            'gh pr merge 12 --squash', 'gh pr update-branch 12',
            'gh pr ' + chr(92) + '\nmerge 12',
            '/usr/local/bin/gh pr merge 12', 'gh --repo=o/r pr merge 12',
            'gh -R o/r pr merge 12', 'gh pr --repo o/r merge 12',
            'printf 12 | xargs gh pr merge',
            'for n in 12 13; do gh pr merge "$n"; sleep 2; done',
            '(gh pr update-branch 12)', 'echo "$(gh pr merge 12)"',
            "bash -c 'gh pr merge 12'", 'command gh pr merge 12',
            'gh api -X PUT repos/o/r/pulls/12/update-branch',
            'gh api repos/o/r/pulls/12/merge --method PUT',
            'gh api "repos/o/r/pulls/$n/update-branch" -X PUT',
            'curl -X PUT https://api.github.com/repos/o/r/pulls/12/merge',
            'tools/mq.sh 12; gh pr merge 13',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for tool, key in [('Bash', 'command'), ('exec_command', 'cmd'), ('functions.exec', 'cmd')]:
                for cmd in commands:
                    with self.subTest(tool=tool, cmd=cmd):
                        result = subprocess.run([sys.executable, str(ROOT / 'hooks/github-burst-guard.py')],
                            input=json.dumps({'tool_name': tool, 'tool_input': {key: cmd}}),
                            env={**os.environ, 'CARR_GITHUB_BURST_STATE': tmp + '/state',
                                 'CARR_HOOK_GUARD_LOG': tmp + '/log'}, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn('tools/mq.sh', result.stderr)

    def test_queue_invocations_and_read_only_prose_are_allowed(self):
        for cmd in ['bash tools/mq.sh 12', 'R=o/r tools/mq.sh 12 13',
                    'tools/mq.sh --queue o/r queue.txt queue.log',
                    'gh pr view 12', 'echo "gh pr merge 12"',
                    "grep 'pulls/12/merge' file", "cat <<'EOF'\ngh pr merge 12\nEOF"]:
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run([sys.executable, str(ROOT / 'hooks/github-burst-guard.py')],
                    input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': cmd}}),
                    env={**os.environ, 'CARR_GITHUB_BURST_STATE': tmp + '/state',
                         'CARR_HOOK_GUARD_LOG': tmp + '/log'}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class WorkflowTests(unittest.TestCase):
    def test_required_ci_checks_requested_trigger_and_all_yaml_validates(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            workflow = yaml.safe_load(path.read_text())
            self.assertIsInstance(workflow.get('jobs'), dict, path.name)
        ci = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(ci['on']['merge_group']['types'], ['checks_requested'])


if __name__ == '__main__':
    unittest.main()
