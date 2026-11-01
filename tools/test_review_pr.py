# Test the pull request review command.
import json
import runpy
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from unittest import TestCase, main, mock

CLI = Path(__file__).resolve().parents[1] / 'bin/review-pr'


class ReviewPR(TestCase):
    def run_review(self, verdict='APPROVE', moved=False, malformed=False, missing=()):
        calls, comments = [], []

        def run(*command, **options):
            name, *args = command
            request = options.get('input', '')
            output = ''
            calls.append((name, args, request))
            if name == 'gh':
                if args[1] == 'view':
                    output = json.dumps(dict(headRefOid=('b' if moved and (sum(n == 'gh' and a[1] == 'view' for n, a, _ in calls) > 1 if moved is True else comments) else 'a')*40, title='Fix correctness', body='Card: WR622.S1.J37'))
                elif args[1] == 'diff': output = 'diff --git a/example b/example'
                elif args[1] == 'comment': raise AssertionError('VERDICT comment publisher retired')
                elif args[1] == 'merge' and moved == 'after-comment':
                    raise subprocess.CalledProcessError(1, command, stderr='head does not match')
            elif name == sys.executable:
                self.assertTrue(args[0].endswith('ops/review-submit.py'))
                self.assertEqual(args[1], '42')
                self.assertIn(args[2], ('approve', 'request-changes'))
                self.assertEqual(args[-2:], ['--reviewed-sha', 'a'*40])
                comments.append(args[2] + '\n' + Path(args[args.index('--body-file')+1]).read_text())
            elif name == 'git' and args[0] == 'rev-parse': output = 'a'*40
            elif name == 'claude':
                if 'mcp__claude_ai_Slack__slack_send_message' in args: output = '123.456'
                else: output = ('VERDICT: '+verdict+'\nReviewed-SHA: '+('b' if malformed else 'a')*40+'\nFinding') if any(n == 'sleep' and a == ['900'] for n, a, _ in calls) else 'WAIT'
            calls[-1] = (name, args, request)
            return output

        main_fn = runpy.run_path(str(CLI))['main']
        with mock.patch('sys.argv', [str(CLI), 'DoctorCRE/carr-system', '42']), mock.patch.dict(main_fn.__globals__, run=run, skill_findings=lambda body: list(missing)):
            try: main_fn()
            except (ValueError, subprocess.CalledProcessError) as failure: return str(failure), calls, ''.join(comments)
        return None, calls, ''.join(comments)

    def test_help_assigns_dot_as_only_reviewer(self):
        help_text = ' '.join(subprocess.check_output(['python3', str(CLI), '--help'], text=True).split())
        self.assertIn('Have Dot review a PR', help_text)
        self.assertNotIn('--reviewer', help_text)
        self.assertIn('authoring job must never review its own PR', help_text)

    def test_approve_posts_pinned_review_and_arms_auto_merge(self):
        error, calls, comment = self.run_review()
        self.assertIsNone(error)
        self.assertTrue(any(name == 'git' and args == ['fetch', 'origin', 'pull/42/head'] for name, args, _ in calls))
        self.assertTrue(comment.startswith('approve\nCard: WR622.S1.J37\n'))
        self.assertTrue(any('--auto' in args and '--squash' in args and args[-2:] == ['--match-head-commit', 'a'*40] for name, args, _ in calls if name == 'gh'))
        self.assertIn('<@U0C5R40MBPE>', next(request for name, _, request in calls if name == 'claude'))
        self.assertTrue(all(name != 'grok-run.sh' for name, _, _ in calls))

    def test_changes_stale_and_invalid_reviews(self):
        for options in [{'verdict': 'CHANGES'}, {'moved': True}, {'moved': 'after-comment'}, {'malformed': True}]:
            with self.subTest(options=options):
                error, calls, comment = self.run_review(**options)
                self.assertEqual(error is None, options.get('verdict') == 'CHANGES', error)
                self.assertEqual(bool(comment), options.get('verdict') == 'CHANGES' or options.get('moved') == 'after-comment')
                self.assertFalse(any(args[1:2] == ['merge'] for name, args, _ in calls if name == 'gh'))

    def test_missing_skill_is_posted_and_prevents_auto_merge(self):
        error, calls, comment = self.run_review(missing=['Required skill did not run: example'])
        self.assertIsNone(error)
        self.assertTrue(comment.startswith('request-changes\n'))
        self.assertIn('Required skill did not run: example', comment)
        self.assertFalse(any(args[1:2] == ['merge'] for name, args, _ in calls if name == 'gh'))

    def test_inconclusive_skill_findings_preserve_reviewer_verdict(self):
        for finding in ('Build session log missing for Card WR1.S1.J1',
                        'Multiple build session logs found for Card WR1.S1.J1; skill use cannot be determined',
                        'Unknown job type: future'):
            for verdict in ('APPROVE', 'CHANGES'):
                with self.subTest(finding=finding, verdict=verdict):
                    error, _, comment = self.run_review(verdict=verdict, missing=[finding])
                    self.assertIsNone(error)
                    self.assertTrue(comment.startswith(('approve' if verdict == 'APPROVE' else 'request-changes') + '\n'))
                    self.assertIn(finding, comment)

    def test_dot_polls_thread_on_paid_run_schedule(self):
        error, calls, _ = self.run_review()
        self.assertIsNone(error)
        self.assertEqual([args for name, args, _ in calls if name == 'sleep' and args != ['2']], [['600'], ['900']])
        requests = [request for name, _, request in calls if name == 'claude']
        self.assertIn('<@U0C5R40MBPE>', requests[0])
        self.assertTrue(all('thread 123.456' in request for request in requests[1:]))


if __name__ == '__main__':
    main()
