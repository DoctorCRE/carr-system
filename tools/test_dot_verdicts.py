#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dot_review', ROOT / 'bin/dot-review.py')
assert spec is not None and spec.loader is not None
dot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dot)
SHA = 'a' * 40


def fake_slack(report):
    slack = type('Slack', (), {'channel': 'fixture-channel'})()
    slack.replies = lambda thread: [{'ts': thread, 'user': 'dot-user', 'text': report}]
    return slack


SLACK_REPORTS: dict[str, dict[str, dict[str, str]]] = {}


def publish_review(directory, *args, **kwargs):
    if dot.dot_relay._protocol(args[1])[1] is None:
        args = (args[0], args[1]+'\nDOT-REPORT-END', *args[2:])
    kwargs.setdefault('reviewer', 'dot-user')
    kwargs.setdefault('relay_run_id', 'fixture-channel:1.000001' if Path(directory).name == 'thread' else 'fixture-channel:2.000001')
    messages = SLACK_REPORTS.setdefault(str(Path(directory).parent), {})
    ts = kwargs['relay_run_id'].rsplit(':', 1)[-1]
    messages[ts] = {'ts': ts, 'user': kwargs['reviewer'], 'text': args[1]}
    slack = fake_slack(args[1])
    slack.replies = lambda thread: [messages[thread]] if thread in messages else []
    with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(Path(directory).parent/'relay-receipts')}), patch.object(
            dot.dot_review_receipts, '_slack_transport', return_value=slack):
        body = dot.publication_body(args[0], args[1], kwargs.get('known_secrets', ()))
        dot.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
        dot.dot_review_receipts.attest_run(args[0], body, reviewer=kwargs['reviewer'],
                                           relay_run_id=kwargs['relay_run_id'], report=args[1])
        return dot.publish(directory, *args, **kwargs)


class RoutingTests(unittest.TestCase):
    def test_free_first_and_every_exception(self):
        cases = [({}, 'dot', 'free capacity'),
                 ({'delay': 1801}, 'codex', 'queue delay'),
                 ({'delay': 1800}, 'dot', 'free capacity'),
                 ({'labels': ['urgent']}, 'codex', 'urgent'),
                 ({'listed': True}, 'codex', 'urgent'),
                 ({'diff': '+with threading.Lock():'}, 'codex', 'concurrency/locking'),
                 ({'files': ['lib/locking.py']}, 'codex', 'concurrency/locking'),
                 ({'labels': ['hands-on-testing']}, 'codex', 'hands-on testing')]
        for args, seat, reason in cases:
            with self.subTest(args=args):
                self.assertEqual(dot.choose_route(**args), (seat, reason))

    def test_idle_adoption_requires_explicit_opt_in_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = Path(tmp)
            source = orch / 'queue/codex'
            source.mkdir(parents=True)
            (source / 'yes.txt').write_text('dot-ok: true\nTask: read-only design critique')
            (source / 'no.txt').write_text('dot-ok: false\nTask: build')
            self.assertEqual(dot.adopt(orch), 1)
            self.assertEqual(dot.adopt(orch), 0)
            self.assertTrue((source / 'no.txt').exists())
            self.assertFalse((source / 'yes.txt').exists())
            self.assertIn('design critique', next((orch / 'dot/queue').glob('*.md')).read_text())

class RelayTests(unittest.TestCase):
    def test_contradictory_authenticated_relay_report_alarms_before_attestation(self):
        import io
        from contextlib import redirect_stderr
        class Slack:
            channel = 'fixture-channel'
            def post(self, text, thread=None):
                return '1.000001'
            def replies(self, thread):
                return [
                    {'ts': '2.000001', 'user': 'dot-user', 'text': 'APPROVE\nReviewed-SHA: '+SHA},
                    {'ts': '3.000001', 'user': 'dot-user', 'text': 'REVIEW: BLOCKED\nP1 blocker\nDOT-REPORT-END'},
                ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root/'repo'
            repo.mkdir()
            relay = dot.ReviewRelay(Slack(), root/'state', repo, 'dot-user')
            thread = relay.send_job('Dot-Review: '+json.dumps({'repo': 'jbookout/carr-system', 'pr': 1, 'sha': SHA}))
            with patch.object(dot.dot_review_receipts, 'attest_run') as attest, patch.object(dot, 'publish') as publish:
                with redirect_stderr(io.StringIO()) as alarm, self.assertRaisesRegex(ValueError, 'multiple verdicts'):
                    relay.poll(thread, execute=True)
                self.assertIn('DOT_REVIEW_RECEIPT_ALARM', alarm.getvalue())
                attest.assert_not_called()
                publish.assert_not_called()

    def test_authenticated_multipart_completion_posts_once_and_survives_restart(self):
        class Slack:
            channel = 'fixture-channel'
            messages = []
            def post(self, text, thread=None):
                return '1.000001'
            def replies(self, thread):
                return self.messages
        with tempfile.TemporaryDirectory() as tmp:
            state, repo = Path(tmp) / 'state', Path(tmp) / 'repo'
            repo.mkdir()
            slack = Slack()
            engine = dot.ReviewRelay(slack, state, repo, 'dot-user')
            meta = {'repo': 'jbookout/carr-system', 'pr': 1, 'sha': SHA}
            thread = engine.send_job('Dot-Review: ' + json.dumps(meta))
            slack.messages = [{'ts': '2.000001', 'user': 'other',
                               'text': 'REVIEW: BLOCKED\nReviewed-SHA: '+SHA+'\nDOT-REPORT-END'},
                              {'ts': '3.000001', 'user': 'dot-user', 'text': 'Still reading'},
                              {'ts': '4.000001', 'user': 'dot-user', 'text': 'APPROVE\nReviewed-SHA: '+SHA+'\nPart one'},
                              {'ts': '5.000001', 'user': 'dot-user', 'text': 'Part two\nDOT-REPORT-END'}]
            posts = []
            comments = []
            def api(path, **kw):
                if kw:
                    posts.append(kw['body'])
                    comments.append({'body': kw['body'], 'author_association': 'OWNER'})
                    return {'id': 1}
                if '/commits?' in path:
                    return [{'author':{'login':'commit-author'}}]
                if '/comments?' in path:
                    return comments
                return {'head': {'sha': SHA}, 'state': 'open', 'user': {'login': 'builder'}}
            actual = dot.publish
            def publish(*args, **kw):
                kw['api'] = api
                kw['orch'] = Path(tmp)/'orch'
                with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(Path(tmp)/'relay-receipts')}):
                    return actual(*args, **kw)
            with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(Path(tmp)/'relay-receipts')}), patch.object(dot, 'publish', side_effect=publish), patch.object(dot.dot_review_receipts, '_slack_transport', return_value=slack):
                dot.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
                self.assertTrue(engine.poll(thread, execute=True))
                restarted = dot.ReviewRelay(slack, state, repo, 'dot-user')
                self.assertTrue(restarted.poll(thread, execute=True))
            self.assertEqual(len(posts), 1)
            self.assertIn('Part one\nPart two', posts[0])
            self.assertNotIn('Still reading', posts[0])
            self.assertNotIn('REVIEW: BLOCKED', posts[0])
            with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(Path(tmp)/'relay-receipts')}), patch.object(dot.dot_review_receipts, '_slack_transport', return_value=slack):
                receipt = dot.dot_review_receipts.matching(meta, posts[0], api=api)
                self.assertEqual(receipt['slack_message_ts'], '4.000001')
                self.assertEqual(receipt['relay_run_id'], 'fixture-channel:4.000001')
                import hashlib
                self.assertEqual(receipt['report_sha256'], hashlib.sha256(
                    ('APPROVE\nReviewed-SHA: '+SHA+'\nPart one\nPart two\nDOT-REPORT-END').encode()).hexdigest())


class EvidenceTests(unittest.TestCase):
    def test_shim_git_is_resolved_before_sandbox_execution(self):
        self.check_git_toolchain(shim=True)

    def test_homebrew_git_symlink_is_resolved_before_sandbox_execution(self):
        self.check_git_toolchain(shim=False)

    def check_git_toolchain(self, *, shim):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            tree = root / 'head'; tree.mkdir()
            developer = root / 'CommandLineTools'
            binary = root / ('CommandLineTools/usr/bin/git' if shim else 'Cellar/git/bin/git')
            binary.parent.mkdir(parents=True)
            helpers = binary.parent.parent / 'libexec/git-core'; helpers.mkdir(parents=True)
            binary.write_text('#!/bin/sh\n'
                              'test -d "$TMPDIR" || exit 2\n'
                              'test "$GIT_EXEC_PATH" = '+repr(str(helpers))+' || exit 3\n'
                              'printf "RESOLVED-GIT\\n"\n')
            binary.chmod(0o755)
            shim_dir = root / 'shim'; shim_dir.mkdir()
            (shim_dir / 'git').write_text('#!/bin/sh\necho "xcrun shim denied" >&2\nexit 126\n')
            (shim_dir / 'git').chmod(0o755)
            installed = root / 'homebrew/bin/git'
            installed.parent.mkdir(parents=True)
            installed.symlink_to(binary)
            env = {'PATH': str(shim_dir)+':/usr/bin:/bin', 'HOME': '/nonexistent'}
            self.assertEqual(subprocess.run(['git', 'rev-parse', 'HEAD'], env=env,
                                           capture_output=True).returncode, 126)
            actual_run = subprocess.run
            calls = []
            def metadata(argv, **kwargs):
                calls.append(argv)
                output = {('/usr/bin/xcode-select', '-p'): str(developer),
                          ('/usr/bin/xcrun', '--find', 'git'): str(binary),
                          (str(binary), '--exec-path'): str(helpers)}.get(tuple(argv))
                if output is None:
                    return actual_run(argv, **kwargs)
                return subprocess.CompletedProcess(argv, 0, output+'\n', '')
            with patch.object(dot.sys, 'platform', 'darwin'), \
                 patch.object(dot.shutil, 'which', return_value='/usr/bin/git' if shim else str(installed)), \
                 patch.object(dot.os.path, 'exists', return_value=True), \
                 patch.object(dot.subprocess, 'run', side_effect=metadata):
                prepared, execs = dot.prepare_test_sandbox(tree, env)
                argv = dot.sandbox_command(['git', 'rev-parse', 'HEAD'], tree, execs=execs)
            result = actual_run(argv[3:], cwd=tree, env=prepared, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'RESOLVED-GIT')
            self.assertEqual(prepared['PATH'].split(os.pathsep)[0], str(binary.parent))
            self.assertTrue(Path(prepared['TMPDIR']).is_relative_to(tree))
            self.assertEqual(prepared.get('DEVELOPER_DIR'), str(developer) if shim else None)
            self.assertEqual(prepared['GIT_OPTIONAL_LOCKS'], '0')
            self.assertEqual(execs, (str(binary), str(helpers)))
            profile = argv[2]
            self.assertIn(f'(subpath "{binary}")', profile)
            self.assertIn(f'(subpath "{helpers}")', profile)
            self.assertNotIn(f'(subpath "{developer}")', profile)
            self.assertIn('(deny network*)', profile)
            self.assertNotIn('(allow network', profile)
            self.assertIn('(deny file-write*)', profile)
            self.assertIn('(deny file-write* (regex', profile)
            self.assertEqual(sum('--exec-path' in call for call in calls), 1)
            self.assertEqual(any('xcrun' in call[0] for call in calls), shim)

    def test_runs_module_in_scratch_at_bound_head_with_bounded_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(source), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (source / 'tools').mkdir()
            module = source / 'tools/test_area.py'
            module.write_text('import subprocess\nprint(subprocess.check_output(["git", "rev-parse", "HEAD"]).decode())\nprint("BOUND-HEAD-TEST")\n')
            git('add', 'tools/test_area.py')
            git('commit', '-qm', 'fixture')
            sha = git('rev-parse', 'HEAD')
            module.write_text('raise RuntimeError("wrong working tree")')
            if sys.platform != 'darwin':
                with self.assertRaisesRegex(ValueError, 'restricted test execution unavailable'):
                    dot.test_evidence('jbookout/carr-system', sha, ['tools/area.py'], origin=str(source))
                return
            evidence = dot.test_evidence('jbookout/carr-system', sha, ['tools/area.py'], origin=str(source))
            self.assertIn('BOUND-HEAD-TEST', evidence)
            self.assertIn('exit: 0', evidence)
            self.assertIn(sha, evidence)
            self.assertNotIn('wrong working tree', evidence)
            self.assertLessEqual(len(evidence), 12100)

class HandoffTests(unittest.TestCase):
    def test_request_wires_routing_dedup_evidence_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = Path(tmp)
            def api(path):
                if '/files?' in path:
                    return [{'filename': 'tools/area.py', 'patch': '+print(1)'}]
                if '/comments?' in path:
                    return []
                return {'head': {'sha': SHA}, 'state': 'open', 'labels': []}
            calls = []
            def evidence(*args):
                calls.append(args)
                return 'LOCAL-TEST-OUTPUT exit: 0'
            result = dot.request('jbookout/carr-system', 1, orch=orch, api=api, evidence_runner=evidence)
            self.assertEqual(result['seat'], 'dot')
            brief = Path(result['brief']).read_text()
            self.assertEqual(dot.metadata(brief)['sha'], SHA)
            self.assertIn('LOCAL-TEST-OUTPUT', brief)
            self.assertEqual(dot.request('jbookout/carr-system', 1, orch=orch, api=api, evidence_runner=evidence), {**result, 'enqueued': False})
            self.assertEqual(len(calls), 1)
            self.assertEqual(len((orch / 'review-routing.jsonl').read_text().splitlines()), 1)

    def test_paid_exception_handoff_names_codex_seat(self):
        with tempfile.TemporaryDirectory() as tmp:
            def api(path):
                if '/files?' in path or '/comments?' in path:
                    return []
                return {'head': {'sha': SHA}, 'state': 'open', 'labels': [{'name': 'urgent'}]}
            result = dot.request('jbookout/carr-system', 1, orch=tmp, api=api,
                                 evidence_runner=lambda *args: self.fail('Dot tests not needed for paid route'),
                                 paid_launcher=lambda receipt: {'status': 'dispatched', 'desk': 'fixture'})
            self.assertEqual(result['seat'], 'codex')
            self.assertEqual(result['reason'], 'urgent')
            self.assertIn('Model Room desk: Codex; family: sol', Path(result['brief']).read_text())


    def test_paid_dispatch_is_named_fresh_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / 'review.txt'
            brief.write_text('independent review')
            with patch.object(dot.subprocess, 'Popen') as spawn, patch.object(dot.os, 'kill'):
                spawn.return_value.pid = 12345
                first = dot.launch_paid({'brief': str(brief)})
                self.assertEqual(dot.launch_paid({'brief': str(brief)}), first)
                self.assertEqual(spawn.call_count, 1)
                argv = spawn.call_args.args[0]
                self.assertIn('--fresh', argv)
                self.assertEqual(argv[-5:], ['--family', 'sol', '--effort', 'high', '--fresh'])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict('os.environ', {'CARR_ORCH_DIR': self.tmp.name+'/orch'})
        env.start(); self.addCleanup(env.stop)
        self.directory = Path(self.tmp.name) / 'thread'
        self.directory.mkdir()
        self.meta = {'repo': 'jbookout/carr-system', 'pr': 123, 'sha': SHA}
        self.comments = []
        self.posts = []
        self.head = SHA
        self.requeues = []

    def api(self, path, **kwargs):
        if kwargs:
            self.posts.append(kwargs['body'])
            self.comments.append({'body': kwargs['body'], 'author_association': 'OWNER'})
            return {'id': 42}
        if '/commits?' in path:
            return [{'author':{'login':'commit-author'}}]
        if '/comments' in path:
            return self.comments
        return {'head': {'sha': self.head}, 'state': 'open', 'user': {'login': 'builder'}}

    def finish(self, verdict='APPROVE'):
        return publish_review(self.directory, self.meta,
                           f'{verdict}\nReviewed-SHA: {SHA}\nNo blockers.\nDOT-REPORT-END',
                           self.api, self.requeues.append, orch=self.directory.parent/'orch')

    def test_exact_head_one_comment_on_replay(self):
        self.assertEqual(self.finish(), 'posted')
        self.assertEqual(self.finish(), 'posted')
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0].splitlines()[:2], ['APPROVE', 'Reviewed-SHA: '+SHA])
        self.assertIn('Reviewer: ChatGPT Dot', self.posts[0])

    def test_receipt_is_durable_before_post_and_missing_provenance_is_refused(self):
        def api(path, **kwargs):
            if kwargs:
                receipt = dot.dot_review_receipts.matching(self.meta, kwargs['body'], api=self.api)
                self.assertEqual(receipt['builder'], 'builder')
                self.assertEqual(receipt['reviewer'], 'dot-user')
                self.assertEqual(receipt['relay_run_id'], 'fixture-channel:1.000001')
            return self.api(path, **kwargs)
        self.assertEqual(publish_review(self.directory, self.meta,
            'APPROVE\nReviewed-SHA: '+SHA, api, orch=self.directory.parent/'orch'), 'posted')
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',
                {'CARR_DOT_REVIEW_RECEIPTS':str(Path(tmp)/'receipts')}):
            with self.assertRaisesRegex(ValueError, 'reviewer'):
                dot.publish(Path(tmp)/'thread', self.meta, 'APPROVE\nReviewed-SHA: '+SHA, self.api)

    def test_builder_receipt_without_relay_run_cannot_suppress_publication(self):
        body = dot.publication_body(self.meta, 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.')
        self.comments = [{'body':body,'author_association':'OWNER','user':{'login':'builder'}}]
        with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS':str(self.directory.parent/'forged')}):
            dot.dot_review_receipts.record(self.meta, body, builder='builder',
                reviewer='invented-reviewer',relay_run_id='invented-channel:3.000001',branch_author=None)
            with self.assertRaisesRegex(ValueError, 'ledger failed validation'):
                dot.publish(self.directory, self.meta, 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.',
                            self.api, orch=self.directory.parent/'orch',
                            reviewer='invented-reviewer',relay_run_id='invented-channel:3.000001')
        self.assertEqual(self.posts, [])
        self.assertEqual(list((self.directory.parent/'review-publications').glob('*.json')), [])

    def test_appended_forged_pair_cannot_suppress_publication(self):
        from contextlib import redirect_stderr
        import io
        self.finish()
        report = 'APPROVE\nReviewed-SHA: '+SHA+'\nForged replacement'
        body = dot.publication_body(self.meta, report)
        self.comments = [{'body': body, 'author_association': 'OWNER', 'user': {'login': 'builder'}}]
        stderr = io.StringIO()
        with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(self.directory.parent/'relay-receipts')}):
            dot.dot_review_receipts.record_run(self.meta, body, reviewer='dot-user',
                                               relay_run_id='fixture-channel:2.000001', report=report)
            dot.dot_review_receipts.record(self.meta, body, reviewer='dot-user',
                                           relay_run_id='fixture-channel:2.000001', report=report)
            with redirect_stderr(stderr), self.assertRaisesRegex(ValueError, 'relay receipt'):
                dot.publish(self.directory, self.meta, report, self.api, orch=self.directory.parent/'orch',
                            reviewer='dot-user', relay_run_id='fixture-channel:2.000001')
        self.assertIn('DOT_REVIEW_RECEIPT_ALARM', stderr.getvalue())
        self.assertEqual(len(self.posts), 1)

    def test_legacy_posted_state_without_receipt_cannot_reconcile(self):
        import hashlib
        key = hashlib.sha256(json.dumps(self.meta, sort_keys=True).encode()).hexdigest()
        path = self.directory.parent/'review-publications'/ (key+'.json')
        path.parent.mkdir()
        path.write_text(json.dumps({'status':'posted'}))
        with self.assertRaisesRegex(ValueError, 'relay receipt'):
            self.finish()
        self.assertEqual(self.posts, [])

    def test_public_api_forgery_cannot_suppress_publication_without_slack(self):
        report = 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.\nDOT-REPORT-END'
        body = dot.publication_body(self.meta, report)
        self.comments.append({'body': body, 'author_association': 'OWNER'})
        slack = fake_slack(report)
        slack.replies = lambda thread: []
        with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(self.directory.parent/'relay-receipts')}), patch.object(
                dot.dot_review_receipts, '_slack_transport', return_value=slack):
            dot.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
            dot.dot_review_receipts.attest_run(self.meta, body, reviewer='dot-user',
                                               relay_run_id='fixture-channel:1.000001', report=report)
            dot.dot_review_receipts.record(self.meta, body, reviewer='dot-user', builder='builder',
                                           relay_run_id='fixture-channel:1.000001', report=report, anchor=True)
            import io
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()) as alarm, self.assertRaisesRegex(ValueError, 'relay run receipt'):
                dot.publish(self.directory, self.meta, report, self.api,
                            orch=self.directory.parent/'orch', reviewer='dot-user',
                            relay_run_id='fixture-channel:1.000001')
            self.assertIn('DOT_REVIEW_RECEIPT_ALARM', alarm.getvalue())
        self.assertEqual(self.posts, [])

    def test_public_api_append_cannot_publish_or_reconcile_a_different_slack_report(self):
        import io
        from contextlib import redirect_stderr
        candidate = 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.\nDOT-REPORT-END'
        body = dot.publication_body(self.meta, candidate)
        reports = [
            'APPROVE\nReviewed-SHA: '+SHA+'\nREVIEW: BLOCKED\nP1 blocker\nDOT-REPORT-END',
            'APPROVE\nReviewed-SHA: '+SHA+'\nOnly 2 tests passed.\nDOT-REPORT-END',
            'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.',
            'APPROVE\nReviewed-SHA: '+SHA+'\nReviewed-SHA: '+SHA+'\nDOT-REPORT-END',
        ]
        for report in reports:
            for reconcile in (False, True):
                with self.subTest(report=report, reconcile=reconcile), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    directory = root/'thread'
                    directory.mkdir()
                    self.comments = [{'body': body, 'author_association': 'OWNER'}] if reconcile else []
                    with patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': str(root/'receipts')}), patch.object(
                            dot.dot_review_receipts, '_slack_transport', return_value=fake_slack(report)):
                        dot.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
                        dot.dot_review_receipts.attest_run(self.meta, body, reviewer='dot-user',
                            relay_run_id='fixture-channel:1.000001', report=report)
                        dot.dot_review_receipts.record(self.meta, body, reviewer='dot-user',
                            relay_run_id='fixture-channel:1.000001', report=report, anchor=True)
                        if reconcile:
                            key = dot.hashlib.sha256(json.dumps(self.meta, sort_keys=True).encode()).hexdigest()
                            dot.save(root/'review-publications'/(key+'.json'), {'status': 'posted'})
                        with redirect_stderr(io.StringIO()) as alarm, self.assertRaises(ValueError):
                            dot.publish(directory, self.meta, candidate, self.api, orch=root/'orch',
                                        reviewer='dot-user', relay_run_id='fixture-channel:1.000001')
                        self.assertIn('DOT_REVIEW_RECEIPT_ALARM', alarm.getvalue())
                        self.assertEqual(self.posts, [])

    def test_reposted_thread_shares_publication_receipt(self):
        self.finish()
        second = self.directory.parent / 'reposted-thread'
        second.mkdir()
        publish_review(second, self.meta, 'APPROVE\nReviewed-SHA: '+SHA, self.api, self.requeues.append, orch=self.directory.parent/'orch')
        self.assertEqual(len(self.posts), 1)

    def test_stale_posts_nothing_and_requeues_once(self):
        self.head = 'b' * 40
        self.assertEqual(self.finish(), 'stale')
        self.finish()
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.requeues), 1)
        self.assertEqual(self.requeues[0]['sha'], self.head)

    def test_blocked_preserved(self):
        self.finish('REVIEW: BLOCKED')
        self.assertTrue(self.posts[0].startswith('REVIEW: BLOCKED\n'))

    def test_wrong_sha_or_prose_cannot_authorize(self):
        for text in ('No blockers', 'APPROVE\nReviewed-SHA: '+ 'b'*40):
            with self.assertRaises(ValueError):
                publish_review(self.directory, self.meta, text, self.api, self.requeues.append, orch=self.directory.parent/'orch')
        self.assertEqual(self.posts, [])

    def test_ambiguous_post_is_not_repeated_and_can_reconcile(self):
        def uncertain(path, **kw):
            result = self.api(path, **kw)
            if kw:
                raise OSError('connection lost after write')
            return result
        with self.assertRaises(OSError):
            publish_review(self.directory, self.meta, 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.\nDOT-REPORT-END', uncertain, self.requeues.append)
        self.assertEqual(self.finish(), 'posted')
        self.assertEqual(len(self.posts), 1)

    def test_uncertain_absent_receipt_refuses_retry(self):
        def uncertain(path, **kw):
            if kw:
                raise OSError('uncertain')
            return self.api(path)
        with self.assertRaises(OSError):
            publish_review(self.directory, self.meta, 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.\nDOT-REPORT-END', uncertain, self.requeues.append)
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            self.finish()
        self.assertEqual(self.posts, [])


class BlockerRegressionTests(unittest.TestCase):
    def api(self, path, **kw):
        if '/commits?' in path:
            return [{'author':{'login':'commit-author'}}]
        if '/files?' in path:
            return [{'filename': 'tools/area.py', 'patch': '+print(1)'}]
        if '/comments?' in path:
            return []
        return {'head': {'sha': SHA}, 'state': 'open', 'labels': getattr(self, 'labels', []), 'user': {'login': 'builder'}}

    def test_evidence_sets_id_descendant_cannot_outlive_timeout(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root/'detached-child'
            child = "import time;from pathlib import Path;time.sleep(.3);Path("+repr(str(marker))+ ").write_text('survived');time.sleep(.1)"
            code = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"],start_new_session=True);time.sleep(2)"
            status, _ = dot.run_bounded([sys.executable, '-c', code], root, {}, timeout=.1, limit=1024)
            time.sleep(.6)
            print('setsid descendant: status='+status+' marker-exists='+str(marker.exists()))
            self.assertEqual(status, 'timeout')
            self.assertFalse(marker.exists())

    def test_cleanup_sweeps_a_real_escaped_child_with_fixture_process_inventory(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker, pidfile = root/'marker', root/'pid'
            child = ("import os,time;from pathlib import Path;Path("+repr(str(pidfile))+ ").write_text(str(os.getpid())+' '+str(os.getppid()));"
                     "time.sleep(.6);Path("+repr(str(marker))+").write_text('survived')")
            code = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"],start_new_session=True);time.sleep(2)"
            actual_run = subprocess.run
            def inventory(argv, **kwargs):
                import os
                if argv[0] == 'ps':
                    rows = [f'{os.getpid()} {os.getppid()} {os.getuid()}']
                    if pidfile.exists():
                        rows.append(pidfile.read_text()+' '+str(os.getuid()))
                    return subprocess.CompletedProcess(argv, 0, stdout='\n'.join(rows), stderr='')
                return actual_run(argv, **kwargs)
            with patch.object(subprocess, 'run', side_effect=inventory):
                status, _ = dot.run_bounded([sys.executable,'-c',code], root, {}, timeout=.25, limit=1024)
            self.assertEqual(status, 'timeout')
            time.sleep(.65)
            self.assertFalse(marker.exists())

    def test_no_inventory_refuses_execution_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(subprocess, 'run', side_effect=PermissionError('ps unavailable')), \
                 patch.object(subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'evidence execution refused'):
                    dot.run_bounded(['untrusted'], Path(tmp), {}, timeout=.1, limit=1024)
            spawn.assert_not_called()

    def test_confirmed_termination_after_execution_does_not_replay(self):
        for result in ({'status':'failed','termination_confirmed':True},
                       {'status':'failed','detail':'codex is not on PATH','termination_confirmed':True,'execution_started':True},
                       {'status':'timed_out','termination_confirmed':True}):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                brief=Path(tmp)/'review.txt';brief.write_text('review')
                brief.with_suffix('.launch.json').write_text(json.dumps({'status':'dispatched','pid':999999,'desk':'sol'}))
                brief.with_suffix('.dispatch.jsonl').write_text(json.dumps(result)+'\n')
                with patch.object(dot.subprocess,'Popen') as spawn, patch.object(dot.os,'kill'):
                    receipt=dot.launch_paid({'brief':str(brief)})
                self.assertNotEqual(receipt['status'], 'failed')
                spawn.assert_not_called()

    def test_dispatch_actual_missing_codex_shape_is_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp)/'review.txt'
            brief.write_text('review')
            brief.with_suffix('.launch.json').write_text(json.dumps({'status':'dispatched','pid':999999,'desk':'sol'}))
            brief.with_suffix('.dispatch.jsonl').write_text(json.dumps({
                'status':'failed','detail':'codex is not on PATH','termination_confirmed':True})+'\n')
            with patch.object(dot.subprocess, 'Popen') as spawn:
                spawn.return_value.pid = 222
                result = dot.launch_paid({'brief':str(brief)})
                print('actual producer result: status='+result['status']+' spawn-count='+str(spawn.call_count))
                self.assertEqual(result['status'], 'dispatched')
                self.assertEqual(spawn.call_count, 1)

    def test_temporary_clone_error_does_not_authorize_paid_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.labels = []
            calls = []
            def unavailable(*args):
                raise OSError('temporary clone unavailable')
            result = dot.request('jbookout/carr-system', 1, orch=tmp, api=self.api,
                                 evidence_runner=unavailable,
                                 paid_launcher=lambda r: calls.append(r) or {'status':'dispatched'})
            print('temporary clone error: seat='+result['seat']+' paid-launches='+str(len(calls)))
            self.assertEqual(result['seat'], 'dot')
            self.assertEqual(calls, [])
            self.assertIn('OSError', Path(result['brief']).read_text())

    def test_builder_exact_body_without_receipt_cannot_reconcile(self):
        import hashlib
        meta = {'repo':'jbookout/carr-system','pr':1,'sha':SHA}
        marker = '<!-- dot-review:'+hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()+' -->'
        body = 'APPROVE\nReviewed-SHA: '+SHA+'\n\nNo blockers.\n\nReviewer: ChatGPT Dot\n'+marker
        posts = []
        def api(path, **kw):
            if kw:
                posts.append(kw['body'])
                return {'id':1}
            if '/comments?' in path:
                return [{'body':body,'author_association':'OWNER','user':{'login':'builder'}}]
            return self.api(path)
        with tempfile.TemporaryDirectory() as tmp:
            result = publish_review(Path(tmp)/'thread', meta,
                                 'APPROVE\nReviewed-SHA: '+SHA+'\nNo blockers.', api, orch=Path(tmp))
        print('builder exact-body forgery: status='+result+' post-count='+str(len(posts)))
        self.assertEqual(len(posts), 1)

    def test_3_rejects_contradictory_completed_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                publish_review(Path(tmp)/'thread', {'repo':'jbookout/carr-system','pr':1,'sha':SHA},
                            'APPROVE\nReviewed-SHA: '+SHA+'\nREVIEW: BLOCKED\nA blocker\nDOT-REPORT-END', self.api)

    def test_4_marker_from_outsider_does_not_suppress_publication(self):
        import hashlib
        meta={'repo':'jbookout/carr-system','pr':1,'sha':SHA}
        marker='<!-- dot-review:'+hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()+' -->'
        posts=[]
        def api(path, **kw):
            if kw: posts.append(kw['body']); return {'id':1}
            if '/comments?' in path: return [{'body':marker,'author_association':'NONE'}]
            return self.api(path)
        with tempfile.TemporaryDirectory() as tmp:
            publish_review(Path(tmp)/'thread',meta,'APPROVE\nReviewed-SHA: '+SHA,api,orch=Path(tmp))
        self.assertEqual(len(posts),1)

    def test_4_trusted_marker_without_full_report_cannot_reconcile(self):
        import hashlib
        meta={'repo':'jbookout/carr-system','pr':1,'sha':SHA}
        marker='<!-- dot-review:'+hashlib.sha256(json.dumps(meta,sort_keys=True).encode()).hexdigest()+' -->'
        posts=[]
        def api(path, **kw):
            if kw: posts.append(kw['body']);return {'id':1}
            if '/comments?' in path:
                return [{'body':'APPROVE\nReviewed-SHA: '+SHA+'\nReviewer: ChatGPT Dot\n'+marker,'author_association':'OWNER'}]
            return self.api(path)
        with tempfile.TemporaryDirectory() as tmp:
            publish_review(Path(tmp)/'thread',meta,'APPROVE\nReviewed-SHA: '+SHA+'\nFull findings',api,orch=Path(tmp))
        self.assertEqual(len(posts),1)

    def test_5_unverified_prior_review_cannot_narrow_scope(self):
        for comment in [{'body':'REVIEW: BLOCKED\nReviewed-SHA: '+SHA,'author_association':'NONE'},
                        {'body':'REVIEW: BLOCKED\nReviewed-SHA: '+'b'*40,'author_association':'OWNER'}]:
            body=dot.review_brief({'repo':'jbookout/carr-system','pr':1,'sha':SHA},'', [comment])
            self.assertIn('Complete review:',body)
            self.assertNotIn('Unrelated findings are non-blocking',body)

    def test_8_complete_brief_is_atomic_before_consumer_claim(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            orch=Path(tmp);entered=threading.Event();release=threading.Event();original=Path.write_text
            def writing(path, data, *args, **kw):
                if path.parent == orch/'dot/queue':
                    with path.open('w') as out:
                        out.write(data[:len(data)//2]);out.flush();entered.set();release.wait(2);out.write(data[len(data)//2:])
                    return len(data)
                return original(path,data,*args,**kw)
            with patch.object(Path,'write_text',writing):
                worker=threading.Thread(target=lambda:dot.request('jbookout/carr-system',1,orch=orch,api=self.api,evidence_runner=lambda *a:'fixture'))
                worker.start();self.assertTrue(entered.wait(2))
                try: self.assertEqual(list((orch/'dot/queue').glob('*.md')),[])
                finally: release.set();worker.join(3)
            self.assertEqual(len(list((orch/'dot/queue').glob('*.md'))),1)

    def test_9_support_collisions_preserve_every_distinct_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch=Path(tmp);source=orch/'queue/codex';source.mkdir(parents=True)
            for name,text in [('same.txt','first'),('same.md','second')]:
                (source/name).write_text('dot-ok: true\n'+text)
            self.assertEqual(dot.adopt(orch),2)
            (source/'same.txt').write_text('dot-ok: true\nthird')
            self.assertEqual(dot.adopt(orch),1)
            bodies=[p.read_text() for p in (orch/'dot/queue').glob('*.md')]
            for task in ('first','second','third'): self.assertTrue(any(task in b for b in bodies))

    def test_10_cached_dot_transitions_on_urgency_age_or_failure(self):
        for cause in ('urgent','expired','failed'):
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as tmp:
                self.labels=[];calls=[]
                kw=dict(orch=tmp,api=self.api,evidence_runner=lambda *a:'fixture',paid_launcher=lambda r:calls.append(r) or {'status':'dispatched'})
                first=dot.request('jbookout/carr-system',1,**kw)
                if cause=='urgent': self.labels=[{'name':'urgent'}]
                elif cause=='expired':
                    import os,time
                    os.utime(first['brief'],(time.time()-1900,)*2)
                else:
                    failed=Path(tmp)/'dot/failed';failed.mkdir();Path(first['brief']).rename(failed/Path(first['brief']).name)
                next_receipt=dot.request('jbookout/carr-system',1,**kw)
                self.assertEqual(next_receipt['seat'],'codex');self.assertEqual(len(calls),1)
                self.assertFalse(Path(first['brief']).exists())

    def test_10_sent_fallback_revokes_old_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.labels=[]
            first=dot.request('jbookout/carr-system',1,orch=tmp,api=self.api,evidence_runner=lambda *a:'fixture')
            sent=Path(tmp)/'dot/sent';sent.mkdir()
            Path(first['brief']).rename(sent/Path(first['brief']).name)
            self.labels=[{'name':'urgent'}]
            next_receipt=dot.request('jbookout/carr-system',1,orch=tmp,api=self.api,paid_launcher=lambda r:{'status':'dispatched'})
            self.assertEqual(next_receipt['seat'],'codex')
            self.assertEqual(publish_review(Path(tmp)/'thread',{'repo':'jbookout/carr-system','pr':1,'sha':SHA},
                                         'APPROVE\nReviewed-SHA: '+SHA,self.api,orch=Path(tmp)), 'cancelled')

    def test_11_proven_launch_failure_is_recoverable_but_uncertainty_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.labels=[{'name':'urgent'}];calls=[]
            def launch(receipt):
                calls.append(receipt)
                if len(calls)==1: raise OSError('spawn failed before process creation')
                return {'status':'dispatched'}
            kw=dict(orch=tmp,api=self.api,paid_launcher=launch)
            with self.assertRaises(OSError): dot.request('jbookout/carr-system',1,**kw)
            self.assertEqual(dot.request('jbookout/carr-system',1,**kw)['dispatch']['status'],'dispatched')
            self.assertEqual(len(calls),2)

    def test_11_terminal_dispatch_failure_is_not_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief=Path(tmp)/'review.txt';brief.write_text('review')
            brief.with_suffix('.launch.json').write_text(json.dumps({'status':'dispatched','pid':999999,'desk':'sol'}))
            brief.with_suffix('.dispatch.jsonl').write_text(json.dumps({'status':'failed','detail':'spawn failed','execution_started':False})+'\n')
            with patch.object(dot.subprocess,'Popen') as spawn:
                spawn.return_value.pid=222
                self.assertEqual(dot.launch_paid({'brief':str(brief)})['pid'],222)
                self.assertEqual(spawn.call_count,1)

    def test_13_idle_refill_skips_approved_and_active_loop(self):
        import runpy
        refill=runpy.run_path(str(ROOT/'out/orch/dot/dot-autofill.py'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'dot';root.mkdir()
            requests=[]
            def request(*a,**kw): requests.append(kw);return {'seat':'dot','status':'ineligible'}
            with patch('runpy.run_path',return_value={'request':request}):
                self.assertEqual(refill['autofill'](root,list_prs=lambda r:[{'number':1,'isDraft':False}]),0)
            self.assertTrue(all(k.get('idle') for k in requests))

    def test_20_cached_consumed_receipt_does_not_count_as_work(self):
        import runpy
        refill=runpy.run_path(str(ROOT/'out/orch/dot/dot-autofill.py'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'dot';root.mkdir()
            with patch('runpy.run_path',return_value={'request':lambda *a,**kw:{'seat':'dot','status':'queued','brief':str(root/'queue/missing.md')}}):
                self.assertEqual(refill['autofill'](root,list_prs=lambda r:[{'number':1,'isDraft':False}]),0)

    def test_19_submission_is_durable_without_evidence_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(dot,'gh_api',side_effect=AssertionError('network')),patch.object(dot,'test_evidence',side_effect=AssertionError('evidence')):
                result=dot.submit('jbookout/carr-system',1,Path(tmp))
            self.assertEqual(result['status'],'submitted')
            self.assertTrue(Path(result['request']).exists())
            calls=[]
            dot.drain(Path(tmp),router=lambda repo,n,**kw:calls.append((repo,n)) or {'status':'queued'})
            self.assertEqual(calls,[('jbookout/carr-system',1)])
            self.assertFalse(Path(result['request']).exists())

    def test_1_sandbox_denies_external_reads_writes_and_network(self):
        import sys
        if sys.platform!='darwin': self.skipTest('macOS sandbox integration')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tree=root/'tree';tree.mkdir();secret=root/'external';secret.write_text('harmless')
            code=f"import socket\nfrom pathlib import Path\nfor action in [lambda:Path({str(secret)!r}).read_text(), lambda:Path({str(root/'marker')!r}).write_text('bad'),lambda:socket.create_connection(('127.0.0.1',9),1)]:\n try: action();print('UNRESTRICTED')\n except OSError: print('DENIED')\n"
            env,execs=dot.prepare_test_sandbox(tree,{'PATH':dot.os.environ.get('PATH','/usr/bin:/bin')})
            argv=dot.sandbox_command([sys.executable,'-c',code],tree,execs=execs)
            status,output=dot.run_bounded(argv,tree,env,timeout=3,limit=4096)
            self.assertEqual(status,'0');self.assertEqual(output.count('DENIED'),3);self.assertFalse((root/'marker').exists())

    def test_2_timeout_kills_descendants_and_capture_stops_at_byte_limit(self):
        import sys,time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'child'
            code=f"import subprocess,time,sys\nsubprocess.Popen([sys.executable,'-c',\"import time;from pathlib import Path;time.sleep(.5);Path({str(marker)!r}).write_text('bad')\"])\ntime.sleep(2)"
            status,_=dot.run_bounded([sys.executable,'-c',code],root,{},timeout=.1,limit=4096)
            self.assertEqual(status,'timeout');time.sleep(.6);self.assertFalse(marker.exists())
            status,output=dot.run_bounded([sys.executable,'-c',"import os\nwhile True: os.write(1,b'x'*65536)"],root,{},timeout=3,limit=1024)
            self.assertEqual(status,'output_limit');self.assertLessEqual(len(output),1024)


class LoopRegressionTests(unittest.TestCase):
    def loop(self):
        import runpy
        path=ROOT/'bin/pr-review-loop.py'
        return runpy.run_path(str(path)) if path.exists() else {}

    def test_6_only_canonical_authority_headers_can_stop_loop(self):
        m=self.loop()
        class Q:
            def pr(self,*a): return {'state':'open','head':{'sha':SHA},'user':{'login':'builder'},'mergeable':True,'mergeable_state':'clean'}
            def covered(self,*a): return True
            def green(self,*a): return True
            def pages(self,*a): return self.comments
        q=Q()
        for body,author in [('APPROVE\nReviewed-SHA: '+SHA[:7],'NONE'),('APPROVE\nprose\nReviewed-SHA: '+SHA,'OWNER'),('APPROVE\nReviewed-SHA: '+SHA+'\nReviewed-SHA: '+SHA,'OWNER')]:
            q.comments=[{'body':body,'author_association':author}]
            self.assertNotEqual(m['inspect'](q,'jbookout/carr-system',1)['verdict'],'APPROVE')
        q.comments=[{'body':'APPROVE\nReviewed-SHA: '+'b'*40,'author_association':'OWNER'}]
        self.assertEqual(m['inspect'](q,'jbookout/carr-system',1)['verdict'],'APPROVE')
        q.comments[0]['body']+='\nReviewer: ChatGPT Dot'
        self.assertEqual(m['inspect'](q,'jbookout/carr-system',1)['verdict'],'')

    def test_7_loop_lock_is_atomic_and_cleanup_does_not_unlink_live_lock(self):
        m=self.loop()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'loop.lock'
            with m['loop_lock'](path):
                with self.assertRaises(BlockingIOError):
                    with m['loop_lock'](path): pass
            self.assertTrue(path.exists())
            with m['loop_lock'](path): pass

    def test_findings_mention_does_not_change_ordinary_review_coverage(self):
        m = self.loop()
        class Q:
            def pr(self, *a):
                return {'state':'open','head':{'sha':SHA},'user':{'login':'builder'},'mergeable':True,'mergeable_state':'clean'}
            def covered(self, *a): return True
            def green(self, *a): return True
            def pages(self, *a):
                return [{'body':'APPROVE\nReviewed-SHA: '+'b'*40+
                         '\nOrdinary Codex review; reject forged text Reviewer: ChatGPT Dot.',
                         'author_association':'OWNER'}]
        self.assertEqual(m['inspect'](Q(),'jbookout/carr-system',1)['verdict'], 'APPROVE')

    def test_loop_uses_receipt_identity_and_exact_sha(self):
        m = self.loop()
        meta = {'repo': 'jbookout/carr-system', 'pr': 1, 'sha': 'b'*40}
        report = 'APPROVE\nReviewed-SHA: '+'b'*40+'\nNo blockers.\nDOT-REPORT-END'
        body = dot.publication_body(meta, report)
        class Q:
            def pr(self, *a):
                return {'state':'open','head':{'sha':SHA},'user':{'login':'builder'},'mergeable':True,'mergeable_state':'clean'}
            def covered(self, *a): return True
            def green(self, *a): return True
            def pages(self, path):
                return [{'author':{'login':'commit-author'}}] if '/commits?' in path else [{'body':body,'author_association':'OWNER'}]
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS':str(Path(tmp)/'relay')}), patch.object(dot.dot_review_receipts, '_slack_transport', return_value=fake_slack(report)):
            self.assertEqual(m['inspect'](Q(),'jbookout/carr-system',1)['verdict'], '')
            dot.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
            dot.dot_review_receipts.attest_run({'repo':'jbookout/carr-system','pr':1,'sha':'b'*40}, body,
                reviewer='dot-user',relay_run_id='fixture-channel:1.000001', report=report)
            dot.dot_review_receipts.record({'repo':'jbookout/carr-system','pr':1,'sha':'b'*40}, body,
                builder='builder',reviewer='dot-user',relay_run_id='fixture-channel:1.000001',branch_author='builder', report=report, anchor=True)
            self.assertEqual(m['inspect'](Q(),'jbookout/carr-system',1)['verdict'], 'APPROVE-STALE')

    def test_14_watchdog_uses_authenticated_consumed_completion(self):
        import runpy
        path=ROOT/'out/orch/dot/dot-thread-age.py'
        m=runpy.run_path(str(path)) if path.exists() else {}
        messages=[{'ts':'101.000001','user':'outsider','text':'DOT-REPORT-END'},
                  {'ts':'102.000001','user':'dot-user','text':'part one'},
                  {'ts':'103.000001','user':'dot-user','text':'part two\nDOT-REPORT-END'}]
        state={'messages':['102.000001'],'finished':False,'brief_complete_ts':'100.000001'}
        result=m['snapshot'](messages,state,'dot-user','100.000001',now=104)
        self.assertFalse(result['complete'])
        state.update(messages=['102.000001','103.000001'],finished=True)
        result=m['snapshot'](messages,state,'dot-user','100.000001',now=104)
        self.assertTrue(result['complete'])
        self.assertEqual(result['report'],'part one\npart two\n')
        self.assertNotIn('outsider',result['report'])

    def test_14_autofill_uses_path_gh_without_untracked_wrapper(self):
        from unittest.mock import Mock
        import runpy
        m = runpy.run_path(str(ROOT / 'out/orch/dot/dot-autofill.py'))
        with patch.object(subprocess, 'run', return_value=Mock(returncode=0, stdout='[]')) as run:
            self.assertEqual(m['query_prs']('jbookout/carr-system'), [])
        self.assertEqual(run.call_args.args[0][0], 'gh')

    def test_14_missing_operational_binding_fails_preflight(self):
        m=self.loop()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ',{'CARR_FACTORY_ROOT':tmp,'CARR_FACTORY_REVISION':SHA}):
                with self.assertRaisesRegex(ValueError,'factory|Factory'):
                    m['factory_helpers']()

    def test_14_factory_calls_use_repo_identity_and_stop_before_delivery(self):
        from unittest.mock import Mock
        m = self.loop()
        for verdict in ('APPROVE', 'REVIEW: BLOCKED'):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                tree = root / 'repair'
                tree.mkdir()
                queue = Mock()
                queue.pr.return_value = {'head': {'ref': 'fix', 'sha': 'b' * 40}}
                policy = {**m['POLICY'], 'Queue': Mock(return_value=queue)}
                helpers = {name: root / (name + '.sh') for name in ('branch-wt', 'fix-pr', 'ci-fix')}
                with patch.dict(m['main'].__globals__, ROOT=root, POLICY=policy,
                                DOT={'submit': lambda *a: {'status': 'submitted'}},
                                inspect=lambda *a: {'state': 'open', 'head': SHA, 'verdict': verdict, 'ready': False},
                                factory_helpers=lambda: (helpers, {})), patch.object(subprocess, 'run') as run:
                    run.return_value.stdout = str(tree)
                    self.assertEqual(m['main'](['jbookout/carr-system', '1', '-', '1']), 20)
                self.assertEqual(run.call_args_list[0].args[0][2], 'jbookout/carr-system')
                self.assertIn('--no-loop', run.call_args_list[1].args[0])

    def test_16_requires_canonical_green_and_affirmative_mergeability(self):
        m=self.loop()
        class Q:
            green_result=False
            mergeable=None
            def pr(self,*a): return {'state':'open','head':{'sha':SHA},'mergeable':self.mergeable,'mergeable_state':'unknown'}
            def pages(self,*a): return [{'body':'APPROVE\nReviewed-SHA: '+SHA,'author_association':'OWNER'}]
            def green(self,*a): return self.green_result
        q=Q()
        for green,mergeable in [(False,True),(True,None),(True,False)]:
            q.green_result=green;q.mergeable=mergeable
            self.assertFalse(m['inspect'](q,'jbookout/carr-system',1)['ready'])
        q.mergeable=True
        self.assertTrue(m['inspect'](q,'jbookout/carr-system',1)['ready'])

    def test_17_failed_state_fetch_cannot_mean_closed(self):
        from unittest.mock import Mock
        m = self.loop()
        queue = Mock()
        queue.pr.side_effect = RuntimeError('GitHub read failed')
        with self.assertRaisesRegex(RuntimeError, 'GitHub read failed'):
            m['inspect'](queue, 'jbookout/carr-system', 1)
        queue.pages.assert_not_called()
        queue.green.assert_not_called()
        queue.pr.side_effect = None
        queue.pr.return_value = {'state': 'closed'}
        self.assertEqual(m['inspect'](queue, 'jbookout/carr-system', 1)['state'], 'closed')

if __name__ == '__main__':
    unittest.main()
