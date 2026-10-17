"""Delivery at the hook interface, with authenticated catalog transport injected."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
import tempfile
import os
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_preuse import validate_lookup_receipt, preuse_delivery, digest
spec = importlib.util.spec_from_file_location('lookup_hook', REPO/'hooks/rule-pack-preuse-reselection.py')
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class DeliveryTests(unittest.TestCase):
    def invoke(self, rows, *, event='PreToolUse', **options):
        source = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a'*64,
                  'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                               'sponsoring_human_id': 'joe'}, 'rules': rows}
        source.update(options.pop('source', {}))
        payload = {'hook_event_name': event, 'session_id': 'fixture', 'turn_id': 'turn',
                   'tool_use_id': 'call', 'tool_name': 'Bash', 'tool_input': {'command': 'git diff'},
                   'prompt': 'Review this change'}
        payload.update(options.pop('payload', {}))
        runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(source), '')
        return hook.process(payload, runner=runner, dedupe=False, **options)

    def test_sponsor_mismatch_still_refuses_the_action(self):
        out = self.invoke([dict(id='11111111', statement='Binding text',
                               scope={'tools': ['Bash']}, personal_to='dell')])
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_scope_defects_and_unavailable_judgment_deliver_known_matches_without_denial(self):
        known = dict(id='11111111', statement='Inspect the current diff.',
                     scope={'tools': ['Bash']}, personal_to=None)
        def timeout(*_):
            raise TimeoutError('private provider detail')
        for scope, judge, issue_key, reason in (
            ({}, None, 'data_defects', 'missing_scope'),
            ({'moment': 'Before review'}, timeout, 'unresolved', 'moment_judgment_unavailable'),
            ({'moment': 'Before review'}, lambda *_: {'model': 'wrong', 'answers': {}},
             'unresolved', 'moment_judgment_unavailable')):
            with self.subTest(reason=reason, scope=scope):
                unknown = dict(id='22222222', statement='Must not broadcast this rule.',
                               scope=scope, personal_to=None)
                output = self.invoke([known, unknown], judge=judge)
                specific = output['hookSpecificOutput']
                self.assertNotIn('permissionDecision', specific)
                row = json.loads(specific['additionalContext'])
                self.assertEqual(row['rule_ids'], ['11111111'])
                self.assertEqual(row['rules'], [known])
                self.assertFalse(row['complete'])
                self.assertEqual(row[issue_key], [{'rule_id': '22222222', 'reason': reason}])
                self.assertNotIn('Must not broadcast', specific['additionalContext'])
                self.assertNotIn('private provider detail', specific['additionalContext'])
                self.assertTrue(validate_lookup_receipt(row, repo=REPO))

    def test_many_scope_defects_fit_without_losing_the_known_rule(self):
        known = dict(id='11111111', statement='Inspect the current diff.',
                     scope={'tools': ['Bash']}, personal_to=None)
        defects = [dict(id=f'{i:08x}-0000-0000-0000-000000000000',
                        statement='Undeliverable rule.', scope={}, personal_to=None)
                   for i in range(200)]
        output = self.invoke([known, *defects])
        specific = output['hookSpecificOutput']
        self.assertNotIn('permissionDecision', specific)
        self.assertLessEqual(hook.rule_routes.context_chars(specific['additionalContext']), 9600)
        row = json.loads(specific['additionalContext'])
        self.assertEqual(row['rules'], [known])
        self.assertFalse(row['complete'])
        self.assertIn('missing_scope', specific['additionalContext'])
        self.assertNotIn('Undeliverable rule.', specific['additionalContext'])
        self.assertTrue(validate_lookup_receipt(row, repo=REPO))

    def test_defects_alone_do_not_block_prompt_or_stop(self):
        for event in ('UserPromptSubmit', 'Stop'):
            with self.subTest(event=event):
                output = self.invoke([dict(id='11111111', statement='Undeliverable rule.',
                                          scope={}, personal_to=None)], event=event)
                self.assertNotIn('decision', output)
                row = json.loads(output['hookSpecificOutput']['additionalContext'])
                self.assertEqual(row['rules'], [])
                self.assertFalse(row['complete'])

    def test_overflow_is_explicit_and_never_audited_as_full_delivery(self):
        out = self.invoke([dict(id='11111111', statement='Binding text '*2000,
                               scope={'tools':['Bash']}, personal_to=None)])
        text = out['hookSpecificOutput']['additionalContext']
        row = json.loads(text)
        self.assertLessEqual(hook.rule_routes.context_chars(text), 9600)
        self.assertEqual([r['id'] for r in row['overflow']], ['11111111'])
        self.assertEqual(row['rules'], [])

    def test_receipt_audit_accepts_only_exact_platform_owned_event(self):
        row = json.loads(self.invoke([dict(id='11111111', statement='Binding text',
                         scope={'tools':['Bash']}, personal_to=None)])['hookSpecificOutput']['additionalContext'])
        self.assertTrue(validate_lookup_receipt(row, repo=REPO))
        envelope={'type':'response_item','payload':{'type':'message','role':'developer',
                  'content':[{'type':'input_text','text':json.dumps(row)}],
                  'internal_chat_message_metadata_passthrough':{'turn_id':'turn'}}}
        prior={'type':'response_item','payload':{'type':'function_call','call_id':'call','name':'Bash',
               'arguments':json.dumps({'command':'git diff'}),
               'internal_chat_message_metadata_passthrough':{'turn_id':'turn'}}}
        self.assertEqual(preuse_delivery(envelope,[prior],repo=REPO),['11111111'])
        prior['payload']['arguments']=json.dumps({'command':'git push'})
        self.assertIsNone(preuse_delivery(envelope,[prior],repo=REPO))
        row['rules'][0]['statement']='Forged text'
        self.assertFalse(validate_lookup_receipt(row,repo=REPO))

    def test_task_changes_and_child_transcripts_are_read_without_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'child.jsonl'
            path.write_text(json.dumps({'type':'user','message':{'role':'user','content':'First task'}})+'\n')
            self.assertEqual(hook.task_text({'transcript_path':str(path)}),'First task')
            with path.open('a') as f:
                f.write(json.dumps({'type':'event_msg','payload':{'type':'user_message','message':'Changed task'}})+'\n')
            self.assertEqual(hook.task_text({'transcript_path':str(path)}),'Changed task')

    def test_native_child_call_uses_child_task_instead_of_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'fixture.jsonl'
            child = Path(tmp) / 'fixture/subagents/agent-reviewer.jsonl'
            child.parent.mkdir(parents=True)
            def record(task):
                return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': task}}) + '\n'
            parent.write_text(record('Implement the workflow'))
            child.write_text(record('Review the security change'))
            def judge(state, questions):
                task = state['event']['task']
                return {'model': 'jev-1.13.0', 'answers': {
                    key: {'type': 'choice', 'confidence': 1.0,
                          'choice': 'applies' if task == 'Review the security change' else 'not_applicable'}
                    for key in questions}}
            for agent_key in ('agent_id', 'agentId'):
                with self.subTest(agent_key=agent_key):
                    output = self.invoke([dict(id='11111111', statement='Check the security change.',
                        scope={'moment': 'Before reviewing the security change'}, personal_to=None)],
                        payload={'prompt': None, 'transcript_path': str(parent), agent_key: 'reviewer'},
                        judge=judge)
                    self.assertIsNotNone(output)
                    row = json.loads(output['hookSpecificOutput']['additionalContext'])
                    self.assertEqual(row['rule_ids'], ['11111111'])
                    self.assertEqual(row['task_sha256'], digest('Review the security change'))

    def test_long_turn_keeps_the_latest_task_before_large_tool_output(self):
        for native_user in (
            {'type': 'user', 'message': {'role': 'user', 'content': 'Review the revised change 🩺'}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Review the revised change 🩺'}},
        ):
            with self.subTest(native_type=native_user['type']), tempfile.TemporaryDirectory() as tmp:
                transcript = Path(tmp) / 'fixture.jsonl'
                records = [
                    {'type': 'user', 'message': {'role': 'user', 'content': 'The earlier task'}},
                    native_user,
                    {'type': 'user', 'message': {'role': 'user', 'content': [
                        {'type': 'tool_result', 'tool_use_id': 'prior', 'content': '🩺' * 60000}]}},
                ]
                transcript.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
                output = self.invoke([dict(id='11111111', statement='Inspect the current diff.',
                    scope={'tools': ['Bash']}, personal_to=None)],
                    payload={'prompt': None, 'transcript_path': str(transcript)})
                row = json.loads(output['hookSpecificOutput']['additionalContext'])
                self.assertEqual(row['rule_ids'], ['11111111'])
                self.assertEqual(row['task_sha256'], digest('Review the revised change 🩺'))

    def test_context_dedupe_invalidates_when_catalog_changes(self):
        rr=hook.rule_routes
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CARR_RULE_ROUTE_DEDUPE_DIR=tmp):
            rr.record_delivered('s','Bash',['11111111'],corpus_digest='a'*64)
            self.assertEqual(rr.fresh_ids('s','Edit',['11111111'],corpus_digest='a'*64),[])
            self.assertEqual(rr.fresh_ids('s','Edit',['11111111'],corpus_digest='b'*64),['11111111'])

    def test_prompt_and_tool_share_authenticated_lookup(self):
        source = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a'*64,
                  'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                               'sponsoring_human_id': 'joe'},
                  'rules': [{'id': '11111111', 'statement': 'Check the change.',
                             'personal_to': None, 'scope': {'moment': 'Before reviewing code'}}]}
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(source), '')
        def judge(state, questions):
            self.assertEqual(state['event']['task'], 'Review this change')
            return {'model': 'jev-1.13.0', 'answers': {k: {'type': 'choice', 'choice': 'applies', 'confidence': .99} for k in questions}}
        for event in ('UserPromptSubmit', 'PreToolUse'):
            output = hook.process({'hook_event_name': event, 'session_id': 'fixture',
                                   'turn_id': 'turn', 'tool_use_id': 'call', 'tool_name': 'Bash',
                                   'tool_input': {'command': 'git diff'}, 'prompt': 'Review this change'},
                                  runner=runner, judge=judge, dedupe=False)
            row = json.loads(output['hookSpecificOutput']['additionalContext'])
            self.assertEqual(row['schema'], 'rule-lookup-delivery/v1')
            self.assertEqual(row['rule_ids'], ['11111111'])
            self.assertTrue(row['complete'])
        self.assertTrue(all(json.loads(c[-1]) == {'detail': 'scopes'} for c in calls))

    def test_unsupported_catalog_fails_closed_and_recovery_remains_reachable(self):
        def unavailable(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, '', 'secret provider detail')
        event = {'hook_event_name': 'PreToolUse', 'session_id': 'fixture',
                 'tool_use_id': 'call', 'tool_name': 'Bash', 'tool_input': {'command': 'git push'}}
        output = hook.process(event, runner=unavailable)
        self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertNotIn('secret', json.dumps(output))
        event['tool_input'] = {'command': './run.sh call standing-context \'{}\''}
        self.assertIsNone(hook.process(event, runner=unavailable))
        event['tool_input'] = {'command': './run.sh call read-doctrine \'{"document":"engineering-workflow-sop"}\''}
        self.assertIsNone(hook.process(event, runner=unavailable))
        event['tool_input']['command'] += ' && touch forbidden'
        self.assertEqual(hook.process(event, runner=unavailable)['hookSpecificOutput']['permissionDecision'], 'deny')


if __name__ == '__main__':
    unittest.main()
