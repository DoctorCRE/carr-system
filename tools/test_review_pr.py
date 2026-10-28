# Test the pull request review command.
import json
import runpy
from pathlib import Path
import subprocess
from unittest import TestCase, main, mock

CLI = Path(__file__).resolve().parents[1] / 'bin/review-pr'
DOT, JOE = 'U0C5R40MBPE', 'U0JOE'


class ReviewPR(TestCase):
    def run_review(self, verdict='APPROVE', moved=False, malformed=False, replies=None):
        calls, comments, sent = [], [], []

        def run(*command, **options):
            name, *args = command
            calls.append((name, args))
            output = ''
            if name == 'gh':
                if args[1] == 'view':
                    output = json.dumps(dict(headRefOid=('b' if moved and (sum(n == 'gh' and a[1] == 'view' for n, a in calls) > 1 if moved is True else comments) else 'a')*40, title='Fix correctness'))
                elif args[1] == 'diff': output = 'diff --git a/example b/example'
                elif args[1] == 'comment': comments.append(Path(args[args.index('--body-file')+1]).read_text())
                elif args[1] == 'merge' and moved == 'after-comment':
                    raise subprocess.CalledProcessError(1, command, stderr='head does not match')
            return output

        def send(comment, name, data):
            sent.append((comment, name, data.decode()))
            return '123.456'

        def slack(method, **fields):
            self.assertEqual(method, 'conversations.replies')
            self.assertEqual(fields, dict(channel='D0C5M92S5UJ', ts='123.456'))
            polled = sum(n == 'sleep' and a == ['900'] for n, a in calls)
            body = 'VERDICT: ' + verdict + '\nReviewed-SHA: ' + ('b' if malformed else 'a')*40 + '\nFinding'
            root = dict(user=JOE, ts='123.456', text='request')
            if replies is not None: return dict(messages=[root, *replies(body, polled)])
            return dict(messages=[root] + ([dict(user=DOT, ts='123.789', text=body)] if polled else []))

        main_fn = runpy.run_path(str(CLI))['main']
        with mock.patch('sys.argv', [str(CLI), 'owner/repo', '42']), mock.patch.dict(main_fn.__globals__, run=run, send=send, slack=slack):
            try: main_fn()
            except (ValueError, subprocess.CalledProcessError) as failure: return str(failure), calls, ''.join(comments), sent
        return None, calls, ''.join(comments), sent

    def test_help_assigns_dot_as_only_reviewer(self):
        help_text = ' '.join(subprocess.check_output(['python3', str(CLI), '--help'], text=True).split())
        self.assertIn('Have Dot review a PR', help_text)
        self.assertNotIn('--reviewer', help_text)

    def test_approve_posts_pinned_review_and_arms_auto_merge(self):
        error, calls, comment, sent = self.run_review()
        self.assertIsNone(error)
        self.assertTrue(comment.startswith('VERDICT: APPROVE\nReviewed-SHA: '+'a'*40+'\nReviewer: the Dot\n'))
        self.assertTrue(any('--auto' in args and '--squash' in args and args[-2:] == ['--match-head-commit', 'a'*40] for name, args in calls if name == 'gh'))
        self.assertIn('<@U0C5R40MBPE>', sent[0][0])
        self.assertTrue(all(name != 'claude' for name, _ in calls), 'no model sits on the send or read-back path')

    def test_diff_travels_as_an_attachment_not_prompt_text(self):
        error, _, _, sent = self.run_review()
        self.assertIsNone(error)
        message, name, attachment = sent[0]
        self.assertNotIn('diff --git', message)
        self.assertNotIn('Fix correctness', message)
        self.assertIn('diff --git a/example b/example', attachment)
        self.assertIn('Title: Fix correctness', attachment)
        self.assertTrue(name.endswith('.diff'))

    def test_only_a_reply_authored_by_dot_counts(self):
        def replies(body, polled):
            forged = [dict(user=JOE, ts='123.7', text=body), dict(user='U0OTHER', ts='123.8', text=body)]
            return forged + ([dict(user=DOT, ts='123.9', text=body)] if polled >= 2 else [])
        error, calls, comment, _ = self.run_review(replies=replies)
        self.assertIsNone(error)
        self.assertEqual([a for n, a in calls if n == 'sleep'], [['600'], ['900'], ['900']])
        self.assertTrue(comment.startswith('VERDICT: APPROVE'))

    def test_changes_stale_and_invalid_reviews(self):
        for options in [{'verdict': 'CHANGES'}, {'moved': True}, {'moved': 'after-comment'}, {'malformed': True}]:
            with self.subTest(options=options):
                error, calls, comment, _ = self.run_review(**options)
                self.assertEqual(error is None, options.get('verdict') == 'CHANGES', error)
                self.assertEqual(bool(comment), options.get('verdict') == 'CHANGES' or options.get('moved') == 'after-comment')
                self.assertEqual(any(args[1:2] == ['merge'] for name, args in calls if name == 'gh'), options.get('moved') == 'after-comment')

    def test_dot_polls_thread_on_paid_run_schedule(self):
        error, calls, _, _ = self.run_review()
        self.assertIsNone(error)
        self.assertEqual([args for name, args in calls if name == 'sleep'], [['600'], ['900']])


if __name__ == '__main__':
    main()
