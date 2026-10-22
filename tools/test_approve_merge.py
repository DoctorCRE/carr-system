"""Exercise the approval command with a fake GitHub CLI, never a live merge."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


class ApproveMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / 'calls'
        self.snapshot = self.root / 'pr.json'
        self.pr = dict(state='OPEN', headRefOid=SHA, labels=[], autoMergeRequest=None)
        gh = self.root / 'gh'
        gh.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ['GH_TEST_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['pr', 'view']:
    print(Path(os.environ['GH_TEST_PR']).read_text())
elif sys.argv[1:3] != ['pr', 'merge']:
    sys.exit('unexpected GitHub command')
''')
        gh.chmod(0o755)
        sleep = self.root / 'sleep'
        sleep.write_text('#!/bin/sh\nexit 0\n')
        sleep.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        GH_TEST_LOG=str(self.log), GH_TEST_PR=str(self.snapshot))

    def run_command(self, *args):
        self.snapshot.write_text(json.dumps(self.pr))
        return subprocess.run(['bash', str(ROOT / 'tools/approve-merge.sh'), *args],
                              env=self.env, text=True, capture_output=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_head_mismatch_refuses_without_a_merge_request(self):
        self.pr['headRefOid'] = 'b' * 40
        result = self.run_command('example/repo', '12', SHA)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('head', result.stderr.lower())
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0][:2], ['pr', 'view'])

    def test_do_not_merge_label_refuses(self):
        self.pr['labels'] = [{'name': 'do_not_merge'}]
        result = self.run_command('example/repo', '12', SHA)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('do_not_merge', result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_happy_path_requests_squash_auto_merge_for_exact_head(self):
        result = self.run_command('example/repo', '12', SHA)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], ['pr', 'merge', '12', '--repo', 'example/repo',
                                          '--auto', '--squash', '--match-head-commit', SHA])

    def test_missing_label_evidence_refuses(self):
        del self.pr['labels']
        result = self.run_command('example/repo', '12', SHA)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_missing_queue_refuses_without_github_calls(self):
        self.env['CARR_WORK_QUEUE_DB'] = str(self.root / 'missing.sqlite3')
        result = self.run_command('--from-work-queue')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_invalid_approval_arguments_refuse_without_github_calls(self):
        for args in [('example/repo', '12', 'short'), ('example/repo', '-1', SHA), ('--bad', '12', SHA)]:
            with self.subTest(args=args):
                result = self.run_command(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_closed_pr_refuses(self):
        self.pr['state'] = 'CLOSED'
        result = self.run_command('example/repo', '12', SHA)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('open', result.stderr.lower())
        self.assertEqual(len(self.calls()), 1)

    def test_work_queue_uses_only_merging_pins_and_skips_enabled_prs(self):
        import sys
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        from work_queue import WorkQueue
        path = self.root / 'work.sqlite3'
        self.env['CARR_WORK_QUEUE_DB'] = str(path)
        with WorkQueue(path) as queue:
            for card, stage in [('ready', 'merging'), ('building', 'review')]:
                queue.enqueue(card, desk='sol', task='synthetic', actor='test')
                claim = queue.claim(eligible_desks={'sol'}, actor='test', owner={})
                queue.finish(card, claim['claim'], {'status': 'completed'}, actor='test')
                queue.update(card, actor='test', reason='approved head',
                             prs=[{'repo': 'example/repo', 'number': 12, 'head': SHA}])
                if stage == 'merging':
                    queue.transition(card, stage, actor='test', reason='approved')
        result = self.run_command('--from-work-queue')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([call for call in self.calls() if call[:2] == ['pr', 'merge']]), 1)
        self.log.unlink()
        self.pr['autoMergeRequest'] = {'enabledAt': '2026-01-01T00:00:00Z'}
        result = self.run_command('--from-work-queue')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:2] for call in self.calls()], [['pr', 'view']])


if __name__ == '__main__':
    unittest.main()
