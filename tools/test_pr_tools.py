#!/usr/bin/env python3
"""Exercise PR tools against a local GitHub CLI stub, including pacing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PrToolsTests(unittest.TestCase):
    def run_tool(self, tool, views=(), *, update=0, merge=0, behind=0, args=('12',)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / 'calls.jsonl'
            (root / 'views.json').write_text(json.dumps(list(views)))
            gh = root / 'gh'
            gh.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, sys
root = pathlib.Path(os.environ['PR_TOOL_FIXTURE'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(['gh', *args]) + '\\n')
if args[:2] == ['pr', 'view']:
    path = root / 'views.json'
    views = json.loads(path.read_text())
    print(json.dumps(views.pop(0)))
    path.write_text(json.dumps(views))
elif args[:2] == ['pr', 'merge']:
    sys.exit(int(os.environ['PR_TOOL_MERGE']))
elif 'PUT' in args:
    sys.exit(int(os.environ['PR_TOOL_UPDATE']))
elif args[0] == 'api' and '/compare/' in args[1]:
    print(os.environ['PR_TOOL_BEHIND'])
else:
    print('jbookout/carr-system\\t12\\tMERGEABLE\\tCLEAN')
''')
            gh.chmod(0o755)
            sleep = root / 'sleep'
            sleep.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, sys
with (pathlib.Path(os.environ['PR_TOOL_FIXTURE']) / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(['sleep', *sys.argv[1:]]) + '\\n')
''')
            sleep.chmod(0o755)
            env = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}',
                   'PR_TOOL_FIXTURE': tmp, 'PR_TOOL_UPDATE': str(update),
                   'PR_TOOL_MERGE': str(merge), 'PR_TOOL_BEHIND': str(behind), 'R': 'jbookout/carr-system'}
            result = subprocess.run(['bash', str(ROOT / 'tools' / tool), *args],
                                    env=env, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    def view(self, state='CLEAN', conclusion='SUCCESS', pr_state='OPEN'):
        return {'state': pr_state, 'mergeStateStatus': state, 'headRefOid': 'abc123',
                'statusCheckRollup': [{'name': 'CI', 'conclusion': conclusion,
                                       'status': 'COMPLETED' if conclusion else 'IN_PROGRESS'}]}

    def test_update_before_red_then_wait_for_green_and_merge_exact_head(self):
        result, calls = self.run_tool('mq.sh', [self.view('BEHIND', 'FAILURE'),
            self.view('BLOCKED', None), self.view()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['UPDATED 12', 'MERGED 12 abc123'])
        gh = [call for call in calls if call[0] == 'gh']
        self.assertEqual(len(gh), 6)
        self.assertIn('repos/jbookout/carr-system/pulls/12/update-branch', gh[1])
        self.assertIn('repos/jbookout/carr-system/compare/main...abc123', gh[3])
        self.assertIn('--squash', gh[-1])
        self.assertEqual(gh[-1][-2:], ['--match-head-commit', 'abc123'])
        for index, call in enumerate(calls):
            if call[0] == 'gh':
                self.assertEqual(calls[index - 1], ['sleep', '2'])

    def test_blocked_head_behind_main_is_updated_before_red(self):
        result, calls = self.run_tool('mq.sh', [self.view('BLOCKED', 'FAILURE'), self.view()], behind=3)
        self.assertEqual(result.stdout.splitlines(), ['UPDATED 12', 'MERGED 12 abc123'])
        self.assertTrue(any('update-branch' in ' '.join(call) for call in calls))

    def test_terminal_queue_outputs(self):
        for view, merge, expected in (
            (self.view('DIRTY'), 0, 'CONFLICT 12'),
            (self.view('BLOCKED', 'FAILURE'), 0, 'RED 12 abc123: CI'),
            (self.view(pr_state='MERGED'), 0, 'MERGED 12 (already)'),
            (self.view(), 1, 'MERGEFAIL 12'),
        ):
            with self.subTest(expected=expected):
                result, calls = self.run_tool('mq.sh', [view], merge=merge)
                self.assertEqual(result.stdout.strip(), expected)
                self.assertEqual(sum(call[:3] == ['gh', 'pr', 'merge'] for call in calls),
                                 1 if merge else 0)

    def test_open_prs_uses_one_cross_repo_search_with_merge_state(self):
        result, calls = self.run_tool('open-prs.sh', args=())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('jbookout/carr-system', result.stdout)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ['gh', 'api', 'graphql'])
        query = ' '.join(calls[0])
        for field in ('user:jbookout', 'is:pr', 'is:open', 'mergeable', 'mergeStateStatus', 'hasNextPage'):
            self.assertIn(field, query)

    def test_burst_refusal_points_to_open_prs(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, 'CARR_GITHUB_BURST_STATE': str(Path(tmp) / 'state.json'),
                   'CARR_HOOK_GUARD_LOG': str(Path(tmp) / 'hook.log')}
            result = subprocess.run([sys.executable, str(ROOT / 'hooks/github-burst-guard.py')],
                input=json.dumps({'tool_name': 'Bash', 'tool_input': {
                    'command': 'for p in $(seq 1 11); do gh pr view "$p"; done'}}),
                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('tools/open-prs.sh', result.stderr)


if __name__ == '__main__':
    unittest.main()
