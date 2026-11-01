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
    def run_review(self, verdict='APPROVE', moved=False, malformed=False, missing=(), diff='diff --git a/example b/example', timestamp='123.456', reply_thread='123.456', reply_channel='D0C5M92S5UJ', bad_reply_at=1, title='Fix correctness'):
        calls, comments = [], []

        def run(*command, **options):
            name, *args = command
            request = options.get('input', '')
            output = ''
            calls.append((name, args, request))
            if name == 'gh':
                if args[1] == 'view':
                    output = json.dumps(dict(headRefOid=('b' if moved and (sum(n == 'gh' and a[1] == 'view' for n, a, _ in calls) > 1 if moved is True else comments) else 'a')*40, title=title, body='Card: WR622.S1.J37'))
                elif args[1] == 'diff': output = diff
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
                if 'mcp__claude_ai_Slack__slack_send_message' in args:
                    is_reply = 'in thread ' in request.split('using Slack.\n', 1)[0]
                    reply_number = sum(n == 'claude' and 'slack_send_message' in ' '.join(a) for n, a, _ in calls) - 1
                    actual_thread = reply_thread if reply_number == bad_reply_at else '123.456'
                    payload = {'message_context': {'message_ts': f'123.{456 + reply_number}' if is_reply else timestamp,
                                                   'channel_id': reply_channel if reply_number == bad_reply_at else 'D0C5M92S5UJ'},
                               'message_link': 'https://example.slack.com/archives/D0C5M92S5UJ/p123456' +
                               (f'?thread_ts={actual_thread}&cid=D0C5M92S5UJ' if is_reply and actual_thread else '')}
                    output = json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': [{'type': 'text', 'text': json.dumps(payload)}]}]}})
                else: output = ('VERDICT: '+verdict+'\nReviewed-SHA: '+('b' if malformed else 'a')*40+'\nFinding') if any(n == 'sleep' and a == ['900'] for n, a, _ in calls) else 'WAIT'
            calls[-1] = (name, args, request)
            return output

        main_fn = runpy.run_path(str(CLI))['main']
        with mock.patch('sys.argv', [str(CLI), 'DoctorCRE/carr-system', '42']), mock.patch.dict(main_fn.__globals__, run=run, skill_findings=lambda body: list(missing)):
            try: main_fn()
            except (ValueError, subprocess.CalledProcessError) as failure: return str(failure), calls, ''.join(comments)
        return None, calls, ''.join(comments)

    def test_large_diff_is_sent_in_bounded_parts(self):
        for diff in ('diff --git a/example b/example\n+ASCII text <@U123> ** _ >\n' * 1200,
                     'a' * 3399 + '😀x' * 30000, ''):
            with self.subTest(size=len(diff)):
                error, calls, _ = self.run_review(diff=diff)
                self.assertIsNone(error)
                requests = [request for name, args, request in calls if name == 'claude' and 'slack_send_message' in ' '.join(args)]
                messages = [request.split('using Slack.\n', 1)[1] for request in requests]
                self.assertTrue(all(len(message) <= 3500 for message in messages))
                self.assertTrue(all(len(message.encode()) <= 8000 for message in messages))
                parts = messages[1:-1]
                self.assertTrue(parts)
                self.assertEqual(''.join(message.split('```\n', 1)[1].removesuffix('\n```') for message in parts), diff)
                self.assertNotIn('<@U0C5R40MBPE>', messages[0])
                self.assertIn('<@U0C5R40MBPE>', messages[-1])
                self.assertTrue(all('in thread 123.456' in request for request in requests[1:]))
                self.assertTrue(all('verbatim' in request for request in requests[1:-1]))

    def test_wrong_reply_placement_aborts_before_mention(self):
        for options in ({'reply_thread': None}, {'reply_thread': '999.000'}, {'reply_channel': 'OTHER'}):
            with self.subTest(options=options):
                error, calls, comment = self.run_review(diff='x' * 50000, bad_reply_at=2, **options)
                self.assertIsNotNone(error)
                self.assertFalse(comment)
                self.assertFalse(any('<@U0C5R40MBPE>' in request for name, _, request in calls if name == 'claude'))
                self.assertFalse(any(name == 'sleep' for name, _, _ in calls))

    def test_oversize_root_is_refused_before_send(self):
        for title in ('x' * 3500, '😀' * 2100):
            with self.subTest(title_chars=len(title)):
                error, calls, comment = self.run_review(title=title)
                self.assertIsNotNone(error)
                self.assertFalse(comment)
                self.assertFalse(any(name == 'claude' for name, _, _ in calls))

    def test_bad_timestamp_refused(self):
        for timestamp in (None, '', 'not-a-ts'):
            with self.subTest(timestamp=timestamp):
                error, _, comment = self.run_review(timestamp=timestamp)
                self.assertIn('missing valid message_context.message_ts', error)
                self.assertFalse(comment)

    def test_timestamp_accepts_string_and_block_results(self):
        parse = runpy.run_path(str(CLI))['thread_timestamp']
        payload = json.dumps({'message_context': {'message_ts': '123.457', 'channel_id': 'D0C5M92S5UJ'},
                              'message_link': 'https://example.slack.com/archives/D0C5M92S5UJ/p123457?thread_ts=123.456&cid=D0C5M92S5UJ'})
        for content in (payload, [{'type': 'text', 'text': payload}]):
            with self.subTest(content=content):
                stream = json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': content}]}})
                self.assertEqual(parse(stream, expected_thread='123.456'), '123.457')

    def test_interpreter_selection(self):
        fn = runpy.run_path(str(CLI))['submit_python']
        with mock.patch.object(Path, 'exists', return_value=True):
            self.assertEqual(fn(), str(CLI.resolve().parents[1] / '.venv/bin/python'))
        with mock.patch.object(Path, 'exists', return_value=False):
            self.assertEqual(fn(), sys.executable)

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
        self.assertIn('<@U0C5R40MBPE>', [request for name, args, request in calls if name == 'claude' and 'slack_send_message' in ' '.join(args)][-1])
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
        requests = [request for name, args, request in calls if name == 'claude' and ('slack_read_thread' in ' '.join(args) or 'slack_send_message' in ' '.join(args))]
        self.assertNotIn('<@U0C5R40MBPE>', requests[0])
        self.assertTrue(all('thread 123.456' in request for request in requests[1:]))


if __name__ == '__main__':
    main()
