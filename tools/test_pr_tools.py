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
