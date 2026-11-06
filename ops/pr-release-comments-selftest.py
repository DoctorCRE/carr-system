#!/usr/bin/env python3
"""Synthetic acceptance for GitHub merge and verified-release notifications."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess
import json
import tempfile
import os

spec = importlib.util.spec_from_file_location('comments', Path(__file__).with_name('pr-release-comments.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Comments(unittest.TestCase):
    def test_matcher(self):
        for body, expected in [('\nWaits-on: #3046\n', {3046}),
                               ('Waits-on: #3046, #3047 #3048', {3046, 3047, 3048}),
                               ('Waits-on: #3046\r\nWaits-on: #3047', {3046, 3047}),
                               (None, set()), ('unrelated', set()),
                               ('waits on 3046', set()), ('waits-on: #3046', set()),
                               ('prefix Waits-on: #3046', set()),
                               ('Waits-on: #3046 extra', set()),
                               ('Waits-on: #30460', {30460})]:
            with self.subTest(body=body):
                self.assertEqual(m.waits_on(body), expected)

    def test_idempotence_and_untrusted_marker(self):
        comments = [{'body': '<!-- carr-merge:3046 -->', 'user': {'login': 'human'}}]
        sent = []
        def api(path, body=None):
            if body is not None:
                sent.append(body)
                comments.append({'body': body['body'], 'user': {'login': 'github-actions[bot]'}})
            return comments
        for _ in range(2):
            m.comment_once(api, 3044, '<!-- carr-merge:3046 -->', 'Merged.')
        self.assertEqual(len(sent), 1)

    def test_merge_fanout_partial_rerun(self):
        stored = {}; sent = []
        def api(path, body=None):
            if path.startswith('pulls?'):
                return [{'number': 4, 'body': 'Waits-on: #6 #7'},
                        {'number': 5, 'body': 'waits on 6'}]
            if body is not None:
                sent.append(path)
                stored[path] = [{'body': body['body'], 'user': {'login': 'github-actions[bot]'}}]
            return stored.get(path, [])
        m.notify_merge(api, 6)
        m.notify_merge(api, 6)
        self.assertEqual(sent, ['issues/4/comments'])

    def test_deploy_range_deduplicates_and_filters(self):
        seen = []
        def api(path, body=None):
            seen.append((path, body))
            if path.startswith('compare/'):
                return [{'sha': 'c1'}, {'sha': 'c2'}]
            if path.startswith('commits/'):
                return [{'number': 8, 'merged_at': 'date', 'merge_commit_sha': 'c2',
                         'base': {'ref': 'main'}},
                        {'number': 9, 'merged_at': None, 'base': {'ref': 'main'}}]
            return []
        m.notify_deploy(api, 'a'*40, 'b'*40)
        self.assertEqual([p for p, b in seen if b], ['issues/8/comments'])
        seen.clear()
        m.notify_deploy(api, 'b'*40, 'b'*40)
        self.assertEqual(seen, [])

    def test_pagination_and_post_transport(self):
        replies = [subprocess.CompletedProcess([], 0, json_text) for json_text in
                   [json.dumps([{'sha': 'c'}] * 100),
                    '[{"sha": "last"}]', '{"id": 1}']]
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/repo'}), patch.object(
                m.subprocess, 'run', side_effect=replies) as run:
            self.assertEqual(len(m.github_api('commits/c/pulls')), 101)
            self.assertIn('page=2', run.call_args.args[0][-1])
            self.assertEqual(m.github_api('issues/1/comments', {'body': 'hello'}), {'id': 1})
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            self.assertEqual(run.call_args.kwargs['input'], '{"body": "hello"}')

    def test_compare_pagination_and_failure(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/repo'}), patch.object(
                m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"commits": []}')):
            self.assertEqual(m.github_api('compare/a...b'), [])
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/repo'}), patch.object(
                m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'denied')):
            with self.assertRaises(subprocess.CalledProcessError):
                m.github_api('pulls?state=open')

    def test_partial_send_rerun(self):
        stored = {}; sent = []; fail = [True]
        def api(path, body=None):
            if path.startswith('pulls?'):
                return [{'number': n, 'body': 'Waits-on: #6'} for n in (4, 5)]
            if body is not None:
                if path == 'issues/5/comments' and fail and fail.pop():
                    raise RuntimeError('send failed')
                sent.append(path)
                stored[path] = [{'body': body['body'], 'user': {'login': 'github-actions[bot]'}}]
            return stored.get(path, [])
        with self.assertRaises(RuntimeError):
            m.notify_merge(api, 6)
        m.notify_merge(api, 6)
        self.assertEqual(sent, ['issues/4/comments', 'issues/5/comments'])

    def test_cli_merge_event_and_nonfatal_deploy_wiring(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            for merged in (False, True):
                event.write_text(json.dumps({'pull_request': {'merged': merged,
                    'base': {'ref': 'main'}, 'number': 6}}))
                with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(event)}), patch.object(
                        m.sys, 'argv', ['comments', 'merge']), patch.object(m, 'notify_merge') as notify:
                    self.assertEqual(m.main(), 0)
                    self.assertEqual(notify.call_count, int(merged))
        with patch.dict(os.environ, {'PREVIOUS_SHA': 'invalid', 'SHA': 'b'*40}), patch.object(
                m.sys, 'argv', ['comments', 'deploy']), patch('sys.stderr') as stderr:
            self.assertEqual(m.main(), 1)
            self.assertIn('::warning::', ''.join(c.args[0] for c in stderr.write.call_args_list))
        root = Path(__file__).resolve().parents[1]
        merge_workflow = (root / '.github/workflows/pr-merge-comments.yml').read_text()
        self.assertIn('pull_request_target:', merge_workflow)
        self.assertIn('ref: main', merge_workflow)
        self.assertIn('persist-credentials: false', merge_workflow)
        workflow = (root / '.github/workflows/deploy.yml').read_text()
        notification = workflow.split('- name: Comment on newly shipped PRs')[1].split('- name:')[0]
        self.assertIn('continue-on-error: true', notification)
        self.assertIn('timeout-minutes: 2', notification)
        self.assertLess(workflow.index('- name: Verify production serves this commit'),
                        workflow.index('- name: Comment on newly shipped PRs'))

    def test_api_failure_stops(self):
        def api(*args):
            raise RuntimeError('rate limited')
        with self.assertRaisesRegex(RuntimeError, 'rate limited'):
            m.notify_merge(api, 6)


if __name__ == '__main__':
    unittest.main()
