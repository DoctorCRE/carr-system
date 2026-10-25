import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'bin/review-pr'


class ReviewPR(unittest.TestCase):
    def run_review(self, reviewer='grok', verdict='APPROVE', moved=False, malformed=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = '''#!/usr/bin/env python3
import json, pathlib, sys
p = pathlib.Path(__file__).resolve().parent
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
c = json.loads((p/'case').read_text())
with (p/'calls').open('a') as f: f.write(json.dumps([name, args, sys.stdin.read() if name == 'claude' else pathlib.Path(args[-1]).read_text() if name == 'grok-run.sh' else ''])+'\\n')
if name == 'git' and args[0] == 'clone': pathlib.Path(args[-1]).mkdir()
if name == 'gh':
 if args[1] == 'view':
  seen = (p/'seen').exists(); (p/'seen').touch()
  print(json.dumps({'headRefOid': 'b'*40 if seen and c['moved'] else 'a'*40, 'title': 'Fix correctness'}))
 elif args[1] == 'diff': print('diff --git a/example b/example')
 elif args[1] == 'comment': (p/'comment').write_text(pathlib.Path(args[args.index('--body-file')+1]).read_text())
elif name == 'grok-run.sh':
 assert pathlib.Path.cwd().name == 'repo'
 (p/'prompt').write_text(pathlib.Path(args[-1]).read_text()); print('Finding\\nVERDICT: '+c['verdict'] if not c['malformed'] else 'No verdict')
elif name == 'claude':
 if 'mcp__claude_ai_Slack__slack_send_message' in args: print('123.456')
 elif not (p/'polled').exists(): (p/'polled').touch(); print('WAIT')
 else: print('VERDICT: '+c['verdict']+'\\nReviewed-SHA: '+('b' if c['malformed'] else 'a')*40+'\\nFinding')
'''
            for name in ('gh', 'git', 'sleep', 'grok-run.sh', 'claude'):
                (root/name).write_text(stub); (root/name).chmod(0o755)
            (root/'case').write_text(json.dumps(dict(verdict=verdict, moved=moved, malformed=malformed)))
            result = subprocess.run(['python3', str(CLI), 'owner/repo', '42', '--reviewer', reviewer],
                                    env={**os.environ, 'PATH': str(root)+os.pathsep+os.defpath}, capture_output=True, text=True)
            calls = [json.loads(line) for line in (root/'calls').read_text().splitlines()] if (root/'calls').exists() else []
            return result, calls, (root/'comment').read_text() if (root/'comment').exists() else ''

    def test_approve_posts_pinned_review_and_arms_auto_merge(self):
        result, calls, comment = self.run_review()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(comment.startswith('VERDICT: APPROVE\nReviewed-SHA: '+'a'*40+'\nReviewer: grok\n'))
        self.assertTrue(any('--auto' in args and '--squash' in args and args[-2:] == ['--match-head-commit', 'a'*40] for name, args, _ in calls if name == 'gh'))
        self.assertTrue(any(args[:3] == ['clone', '--filter=blob:none', '--no-checkout'] for name, args, _ in calls if name == 'git'))
        self.assertTrue(any(args[-3:] == ['checkout', '--detach', 'a'*40] for name, args, _ in calls if name == 'git'))
        self.assertEqual([args[:-1] for name, args, _ in calls if name == 'grok-run.sh'], [['--effort', 'medium', '--prompt-file']])
        self.assertTrue(any('Machinery rule: reject (VERDICT: CHANGES)' in request and 'diff --git' in request for name, _, request in calls if name == 'grok-run.sh'))

    def test_changes_stale_and_invalid_reviews(self):
        for reviewer, options in [('grok', {'verdict': 'CHANGES'}), ('dot', {'verdict': 'CHANGES'}), ('grok', {'moved': True}), ('dot', {'moved': True}), ('grok', {'malformed': True}), ('dot', {'malformed': True})]:
            with self.subTest(reviewer=reviewer, options=options):
                result, calls, comment = self.run_review(reviewer, **options)
                self.assertEqual(result.returncode == 0, options.get('verdict') == 'CHANGES', result.stderr)
                self.assertEqual(bool(comment), options.get('verdict') == 'CHANGES')
                self.assertFalse(any(args[1:2] == ['merge'] for name, args, _ in calls if name == 'gh'))

    def test_dot_polls_thread_on_paid_run_schedule(self):
        result, calls, comment = self.run_review('dot')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Reviewer: the Dot\n', comment)
        self.assertEqual([args for name, args, _ in calls if name == 'sleep' and args != ['2']], [['600'], ['900']])
        requests = [request for name, _, request in calls if name == 'claude']
        self.assertIn('<@U0C5R40MBPE>', requests[0])
        self.assertTrue(all('thread 123.456' in request for request in requests[1:]))


if __name__ == '__main__':
    unittest.main()
