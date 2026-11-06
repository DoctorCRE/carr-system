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
assert spec is not None and spec.loader is not None
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


import copy

FIXTURES = Path(__file__).resolve().parents[1] / 'test/fixtures/pr-release-comments'
CONTRACT = json.loads((FIXTURES / 'github-schema.json').read_text())

def schema(value):
    while '$ref' in value:
        value = CONTRACT['components']['schemas'][value['$ref'].split('/')[-1]]
    return value

MISSING=object()
def complete(spec,value=MISSING):
    spec=schema(spec)
    if value is None or value is MISSING and spec.get('nullable'):return None
    if 'allOf' in spec:
        result={}
        for part in spec['allOf']: result.update(complete(part,value))
        return result
    if 'oneOf' in spec or 'anyOf' in spec:
        choices=spec.get('oneOf',spec.get('anyOf'))
        for choice in choices:
            try:
                result=complete(choice,value);validate(choice,result);return result
            except (AssertionError,TypeError):pass
        raise AssertionError('no union option')
    kind=spec.get('type')
    if kind=='object':
        result={} if value is MISSING else copy.deepcopy(value)
        for key in set(spec.get('required',[]))|set(result):
            result[key]=complete(spec.get('properties',{}).get(key,{}),result.get(key,MISSING))
        return result
    if kind=='array':return [complete(spec['items'],v) for v in ([] if value is MISSING else value)]
    if value is not MISSING:return value
    if 'enum' in spec:return spec['enum'][0]
    return {'string':'synthetic','integer':1,'number':1,'boolean':False}.get(kind,{})
def validate(spec,value):
    spec=schema(spec)
    if value is None:
        assert spec.get('nullable') or spec.get('type')=='null';return
    for part in spec.get('allOf',[]):validate(part,value)
    if 'oneOf' in spec or 'anyOf' in spec:
        for choice in spec.get('oneOf',spec.get('anyOf')):
            try:validate(choice,value);break
            except AssertionError:pass
        else:raise AssertionError('union mismatch')
    kind=spec.get('type')
    if kind:assert isinstance(value,{'object':dict,'array':list,'string':str,'integer':int,'number':(int,float),'boolean':bool}[kind]),(kind,value)
    if 'enum' in spec:assert value in spec['enum'],value
    if kind=='object':
        assert set(spec.get('required',[]))<=value.keys()
        for key,item in value.items():
            if key in spec.get('properties',{}):validate(spec['properties'][key],item)
    if kind=='array':
        for item in value:validate(spec['items'],item)

def response(name,value):
    spec=CONTRACT['components']['schemas'][name]
    result=complete(spec,value);validate(spec,result);return result


def checked_api(api):
    def call(path, body=None):
        value = api(path, body)
        if body is not None:
            return value
        if path.startswith('compare/'):
            return response('commit-comparison', {'commits': value})['commits']
        kind = 'pull-request-simple' if path.startswith(('pulls?', 'commits/')) else 'issue-comment'
        return [response(kind, row) for row in value]
    return call


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
            m.comment_once(checked_api(api), 3044, '<!-- carr-merge:3046 -->', 'Merged.')
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
        m.notify_merge(checked_api(api), 6)
        m.notify_merge(checked_api(api), 6)
        self.assertEqual(sent, ['issues/4/comments'])

    def test_deploy_recovery(self):
        stored = {}; sent = []; failures = {'issues/8/comments'}
        def api(path, body=None):
            if path.startswith('compare/'):
                return [{'sha': 'b'*40}]
            if path.startswith('commits/'):
                return [{'number': 8, 'merged_at': 'date', 'merge_commit_sha': 'b'*40,
                         'base': {'ref': 'main'}}]
            if body is not None:
                if path in failures:
                    raise RuntimeError('send failed')
                sent.append(path)
                stored[path] = [{'body': body['body'], 'user': {'login': 'github-actions[bot]'}}]
            return stored.get(path, [])
        with self.assertRaises(RuntimeError):
            m.notify_deploy(checked_api(api), 'b'*40)
        failures.clear()
        m.notify_deploy(checked_api(api), 'b'*40)
        self.assertEqual(sent, ['issues/8/comments'])
        m.notify_deploy(checked_api(api), 'c'*40)
        self.assertEqual(sent, ['issues/8/comments'])

    def test_terminal_deploy_guard(self):
        import yaml
        workflow = yaml.safe_load((Path(__file__).resolve().parents[1] /
                                   '.github/workflows/deploy.yml').read_text())
        job = workflow['jobs']['notify']
        self.assertEqual(set(job['needs']), {'deploy', 'staging'})
        guard = job['if']
        for deploy in ('success', 'failure', 'cancelled'):
            for staging in ('success', 'skipped', 'failure', 'cancelled'):
                expression = guard.replace('always()', 'True').replace('&&', ' and ').replace('||', ' or ')
                expression = expression.replace('needs.deploy.result', repr(deploy))
                expression = expression.replace('needs.staging.result', repr(staging))
                self.assertEqual(eval(expression), deploy == 'success' and staging in ('success', 'skipped'))
        self.assertIn('needs.deploy.outputs.sha', job['env']['SHA'])
        self.assertFalse(any('pr-release-comments.py' in step.get('run', '')
                             for step in workflow['jobs']['deploy']['steps']))

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
        m.notify_deploy(checked_api(api), 'b'*40)
        self.assertEqual([p for p, b in seen if b], ['issues/8/comments'])
        seen.clear()
        m.notify_deploy(checked_api(api), 'b'*40)
        self.assertEqual([p for p, b in seen if b], ['issues/8/comments'])

    def test_pagination_and_post_transport(self):
        replies = [subprocess.CompletedProcess([], 0, json_text) for json_text in
                   [json.dumps([response('issue-comment', {'body': '', 'user': {'login': 'github-actions[bot]'}})] * 100),
                    json.dumps([response('issue-comment', {'body': '', 'user': {'login': 'github-actions[bot]'}})]), json.dumps(response('issue-comment', {'body': 'hello', 'user': {'login': 'github-actions[bot]'}}))]]
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/repo'}), patch.object(
                m.subprocess, 'run', side_effect=replies) as run:
            self.assertEqual(len(m.github_api('issues/1/comments')), 101)
            self.assertIn('page=2', run.call_args.args[0][-1])
            self.assertEqual(m.github_api('issues/1/comments', {'body': 'hello'})['body'], 'hello')
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            self.assertEqual(run.call_args.kwargs['input'], '{"body": "hello"}')

    def test_compare_pagination_and_failure(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/repo'}), patch.object(
                m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response('commit-comparison', {'commits': []})))):
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
            m.notify_merge(checked_api(api), 6)
        m.notify_merge(checked_api(api), 6)
        self.assertEqual(sent, ['issues/4/comments', 'issues/5/comments'])

    def test_cli_merge_event_and_invalid_deploy_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            for merged in (False, True):
                event.write_text(json.dumps({'pull_request': {'merged': merged,
                    'base': {'ref': 'main'}, 'number': 6}}))
                with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(event)}), patch.object(
                        m.sys, 'argv', ['comments', 'merge']), patch.object(m, 'notify_merge') as notify:
                    self.assertEqual(m.main(), 0)
                    self.assertEqual(notify.call_count, int(merged))
        with patch.dict(os.environ, {'PREVIOUS_SHA': 'invalid', 'SHA': 'invalid'}), patch.object(
                m.sys, 'argv', ['comments', 'deploy']), patch('sys.stderr') as stderr:
            self.assertEqual(m.main(), 1)
            self.assertIn('::warning::', ''.join(c.args[0] for c in stderr.write.call_args_list))
        root = Path(__file__).resolve().parents[1]
        merge_workflow = (root / '.github/workflows/pr-merge-comments.yml').read_text()
        self.assertIn('pull_request_target:', merge_workflow)
        self.assertIn('ref: main', merge_workflow)
        self.assertIn('persist-credentials: false', merge_workflow)
        workflow = (root / '.github/workflows/deploy.yml').read_text()

    def test_proof_pass_missing_duplicate_and_human(self):
        root = Path(__file__).resolve().parents[1]
        command = [os.sys.executable, str(root / 'ops/pr-release-comments-proof.py'),
                   '--waiting-pr', '4', '--merge-pr', '30', '--shipped-pr', '8', '--merge-sha', 'b'*40]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            for name in ('merge', 'deploy'):
                rows = json.loads((FIXTURES / (name + '.json')).read_text())
                for row in rows:
                    validate(CONTRACT['components']['schemas']['issue-comment'], row)
                (fixture / (name + '.json')).write_text(json.dumps(rows))
            def run():
                return subprocess.run(command + ['--fixture', directory], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            rows = json.loads((fixture / 'deploy.json').read_text())
            for invalid in ([], rows * 2, [{**rows[0], 'user': {**rows[0]['user'], 'login': 'human'}}]):
                (fixture / 'deploy.json').write_text(json.dumps(invalid))
                self.assertEqual(run().returncode, 78)

    def test_structured_proof_admission(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from ops.delivery_plan import validate as admit
        root = Path(__file__).resolve().parents[1]
        policy = json.loads((root / 'contracts/production-proof-plan.v1.json').read_text())
        requirement = json.loads((root / 'proofs/requirements/pr-release-comments.v1.json').read_text())
        admit(requirement, policy)
        runner = policy['x-runners']['pr-release-comments.v1']
        import hashlib
        for path, digest in runner['sources'].items():
            self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), digest)

    def test_api_failure_stops(self):
        def api(*args):
            raise RuntimeError('rate limited')
        with self.assertRaisesRegex(RuntimeError, 'rate limited'):
            m.notify_merge(checked_api(api), 6)


if __name__ == '__main__':
    unittest.main()
