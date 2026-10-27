# Test the pull request review command.
import json
import runpy
from pathlib import Path
import subprocess
from unittest import TestCase, main, mock

CLI = Path(__file__).resolve().parents[1] / 'bin/review-pr'


class ReviewPR(TestCase):
    def run_review(self, reviewer='grok', verdict='APPROVE', moved=False, malformed=False):
        calls, comments = [], []
        def run(*command, **options):
            name, *args = command
            request = options.get('input', '')
            output = ''
            calls.append((name, args, request))
            if name == 'git' and 'fetch' in args and args[-1] != 'pull/42/head':
                raise subprocess.CalledProcessError(1, command, stderr='fork SHA is not advertised by origin')
            elif name == 'gh':
                if args[1] == 'view':
                    output = json.dumps(dict(headRefOid=('b' if moved and (sum(n == 'gh' and a[1] == 'view' for n, a, _ in calls) > 1 if moved is True else comments) else 'a')*40, title='Fix correctness'))
                elif args[1] == 'diff': output = 'diff --git a/example b/example'
                elif args[1] == 'comment': comments.append(Path(args[args.index('--body-file')+1]).read_text())
                elif args[1] == 'merge' and moved == 'after-comment':
                    raise subprocess.CalledProcessError(1, command, stderr='head does not match')
            elif name == 'grok-run.sh':
                self.assertEqual(options['cwd'].name, 'repo')
                request = Path(args[-1]).read_text()
                output = 'No verdict' if malformed else 'Finding\nVERDICT: '+verdict
            elif name == 'claude':
                if 'mcp__claude_ai_Slack__slack_send_message' in args: output = '123.456'
                else: output = ('VERDICT: '+verdict+'\nReviewed-SHA: '+('b' if malformed else 'a')*40+'\nFinding') if any(n == 'sleep' and a == ['900'] for n, a, _ in calls) else 'WAIT'
            calls[-1] = (name, args, request)
            return output
        main = runpy.run_path(str(CLI))['main']
        with mock.patch('sys.argv', [str(CLI), 'owner/repo', '42', '--reviewer', reviewer]), mock.patch.dict(main.__globals__, run=run), mock.patch('shutil.which', return_value='grok-run.sh'):
            try: main()
            except (ValueError, subprocess.CalledProcessError) as failure: return str(failure), calls, ''.join(comments)
        return None, calls, ''.join(comments)

    def test_help_assigns_review_to_orchestrator(self):
        help_text = ' '.join(subprocess.check_output(['python3', str(CLI), '--help'], text=True).split())
        self.assertRegex(help_text, 'must never review its own PR.*orchestrator runs reviews')

    def test_approve_posts_pinned_review_and_arms_auto_merge(self):
        error, calls, comment = self.run_review()
        self.assertIsNone(error)
        self.assertFalse(any(name == 'sleep' for name, _, _ in calls))
        self.assertTrue(comment.startswith('VERDICT: APPROVE\nReviewed-SHA: '+'a'*40+'\nReviewer: grok\n'))
        self.assertTrue(any('--auto' in args and '--squash' in args and args[-2:] == ['--match-head-commit', 'a'*40] for name, args, _ in calls if name == 'gh'))
        self.assertTrue(any(args[:3] == ['clone', '--filter=blob:none', '--no-checkout'] for name, args, _ in calls if name == 'git'))
        self.assertTrue(any(args[-3:] == ['checkout', '--detach', 'a'*40] for name, args, _ in calls if name == 'git'))
        self.assertTrue(any('Machinery rule: reject (VERDICT: CHANGES)' in request and 'diff --git' in request for name, _, request in calls if name == 'grok-run.sh'))

    def test_changes_stale_and_invalid_reviews(self):
        for reviewer, options in [('grok', {'verdict': 'CHANGES'}), ('dot', {'verdict': 'CHANGES'}), ('grok', {'moved': True}), ('dot', {'moved': True}), ('grok', {'moved': 'after-comment'}), ('dot', {'moved': 'after-comment'}), ('grok', {'malformed': True}), ('dot', {'malformed': True})]:
            with self.subTest(reviewer=reviewer, options=options):
                error, calls, comment = self.run_review(reviewer, **options)
                self.assertEqual(error is None, options.get('verdict') == 'CHANGES', error)
                self.assertEqual(bool(comment), options.get('verdict') == 'CHANGES' or options.get('moved') == 'after-comment')
                self.assertEqual(any(args[1:2] == ['merge'] for name, args, _ in calls if name == 'gh'), options.get('moved') == 'after-comment')

    def test_dot_polls_thread_on_paid_run_schedule(self):
        error, calls, comment = self.run_review('dot')
        self.assertIsNone(error)
        self.assertIn('Reviewer: the Dot\n', comment)
        self.assertEqual([args for name, args, _ in calls if name == 'sleep' and args != ['2']], [['600'], ['900']])
        requests = [request for name, _, request in calls if name == 'claude']
        self.assertIn('<@U0C5R40MBPE>', requests[0])
        self.assertTrue(all('thread 123.456' in request for request in requests[1:]))


if __name__ == '__main__':
    main()
