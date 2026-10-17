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

    def test_defects_uncertainty_and_sponsor_mismatch_refuse_the_action(self):
        for scope, owner in (({}, None), ({'moment': 'Before review'}, None), ({'tools': ['Bash']}, 'dell')):
            out = self.invoke([dict(id='11111111', statement='Binding text', scope=scope, personal_to=owner)],
                              judge=lambda *_: {'model': 'wrong', 'answers': {}})
            self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')

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
