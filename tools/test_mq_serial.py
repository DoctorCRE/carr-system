#!/usr/bin/env python3
"""Enqueue-only CLI, mutation guard and required-check workflow contracts."""
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


class EnqueueTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('enqueue_daemon', ROOT / 'tools/merge_queue/main.py')
        daemon = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(daemon)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.q = daemon.Queue(self.root / 'out/merge-queue', ROOT, gap=0)
        self.addCleanup(self.q.db.close)
        gh = self.root / 'gh'
        gh.write_text(f'#!{sys.executable}\n' + "import sys,json,time\nfrom pathlib import Path\n"
            + f"with Path({str(self.root / 'calls')!r}).open('a') as f: f.write(str(time.monotonic())+' '+' '.join(sys.argv[1:])+'\\n')\n"
            + "print(json.dumps(dict(number=int(sys.argv[2].split('/')[-1]),state='open',head=dict(sha='a'*40,ref='topic'),base=dict(sha='b'*40,ref='main'),mergeable_state='clean')))\n")
        gh.chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{self.root}:{os.environ["PATH"]}', 'CARR_ROOT': tmp.name,
                    'CARR_GITHUB_READ_BUDGET': str(self.root / 'budget'), 'GH_LIMITER_DIR': str(self.root / 'limiter')}

    def command(self, *args):
        return ['bash', str(ROOT / 'tools/mq.sh'), *args]

    def test_frontend_enqueues_pinned_heads_while_daemon_owns_lock(self):
        with self.q.runner_lock():
            for _ in range(2):
                result = subprocess.run(self.command('12'), env=self.env,
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(r['pr'], r['approved'], r['phase']) for r in self.q.db.execute('SELECT * FROM entries')],
                         [(12, 'a'*40, 'pending')])
        self.assertIn('ENQUEUED 12', result.stdout)
        calls = (self.root / 'calls').read_text().splitlines()
        self.assertTrue(all(' api repos/' in line for line in calls))
        self.assertGreaterEqual(float(calls[1].split()[0]) - float(calls[0].split()[0]), 1.9)

    def test_file_snapshot_waits_for_writer_and_never_consumes_input(self):
        import fcntl
        queue, log = self.root / 'queue', self.root / 'log'
        with queue.open('w') as writer:
            fcntl.flock(writer, fcntl.LOCK_EX)
            writer.write('12')
            writer.flush()
            proc = subprocess.Popen(self.command('--queue', 'jbookout/carr-system', str(queue), str(log)),
                                    env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(lambda: proc.poll() is None and proc.kill())
            time.sleep(.3)
            self.assertIsNone(proc.poll())
            self.assertFalse((self.root / 'calls').exists())
            writer.write('3\n124\n')
            writer.flush()
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual([r['pr'] for r in self.q.db.execute('SELECT * FROM entries ORDER BY id')], [123, 124])
        self.assertEqual(queue.read_text(), '123\n124\n')
        self.assertEqual(len(log.read_text().splitlines()), 2)
        self.assertIn('ENQUEUED 123', stdout)

    def test_partial_or_invalid_file_is_refused_before_github(self):
        queue = self.root / 'queue'
        for text in ('12', '12\ninvalid\n', '12\n0\n'):
            queue.write_text(text)
            result = subprocess.run(self.command('--queue', 'jbookout/carr-system', str(queue), str(self.root / 'log')),
                                    env=self.env, text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / 'calls').exists())


class MergeHookTests(unittest.TestCase):
    def test_native_codex_javascript_commands_are_inspected(self):
        codes = [
            'await tools.exec_command({cmd: "gh pr merge 12 --squash"});',
            'await tools.exec_command({cmd: `gh pr merge 12 --squash`});',
            'await tools.exec_command({"cmd": "gh pr merge 12 --squash"});',
            "await tools.exec_command({'cmd': 'gh pr update-branch 12'});",
            'await tools.exec_command({cmd: `gh pr merge ${pr}`});',
            r'await tools.exec_command({cmd: `gh pr \x6derge 12`});',
            'const other={cmd: "echo read only"}; await tools.exec_command({cmd: dynamicCommand});',
            'await tools.exec_command({cmd: "echo read only", ...dynamicArguments});',
        ]
        for tool_input in [item for code in codes for item in (code, {'code': code})]:
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
            'gh -Ro/r pr merge 12', 'gh -Ro/r pr update-branch 12',
            'bash -c \'env -i gh pr merge 12\'', 'echo "$(env -i gh pr merge 12)"',
            "gh api graphql -f 'query=mutation {mergePullRequest(input:{pullRequestId:\"id\"}){clientMutationId}}'",
            'env -S "gh pr merge 12"', 'env --split-string="gh pr update-branch 12"',
            'env -i gh pr merge 12', 'env -u HOME gh pr update-branch 12',
            'X=1 command gh pr merge 12', '"gh" pr merge 12',
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
                    'gh api repos/o/r/pulls/12/merge',
                    'gh api -X GET repos/o/r/pulls/12/merge',
                    'gh api repos/o/r/pulls/12/merge --method=GET',
                    'curl -X GET https://api.github.com/repos/o/r/pulls/12/merge',
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
