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

    def test_data_defects_remain_incomplete_at_pretooluse(self):
        gaps = [dict(id='22222222', statement='Moment rule.',
                     scope={'moment': 'Before review'}, personal_to=None),
                dict(id='33333333', statement='Invalid scope.', scope={}, personal_to=None)]
        with tempfile.TemporaryDirectory() as raw, patch.object(hook, 'GAP_LOG', Path(raw) / 'gaps.jsonl', create=True):
            for event in ('PreToolUse', 'UserPromptSubmit', 'Stop'):
                output = self.invoke(gaps, event=event)
                context = (output or {}).get('hookSpecificOutput', {}).get('additionalContext', '')
                if event == 'PreToolUse':
                    self.assertFalse(json.loads(context)['complete'])
                else:
                    self.assertEqual(len(context.encode()), 0)
            rows = [json.loads(line) for line in hook.GAP_LOG.read_text().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]['unresolved'], [])
            self.assertEqual(rows[0]['data_defects'][0]['rule_id'], '33333333')

    def test_configured_judge_failure_keeps_rules_and_unresolved_entries(self):
        known = dict(id='11111111', statement='Inspect the current diff.',
                     scope={'tools': ['Bash']}, personal_to=None)
        gap = dict(id='22222222', statement='Moment rule.',
                   scope={'moment': 'Before review'}, personal_to=None)
        for event in ('PreToolUse', 'UserPromptSubmit'):
            output = self.invoke([known, gap], event=event, judge=lambda *_: None)
            row = json.loads(output['hookSpecificOutput']['additionalContext'])
            self.assertEqual(row['rules'], [known])
            self.assertEqual(row['unresolved'], [{'rule_id': '22222222', 'reason': 'moment_judgment_unavailable'}])
            self.assertFalse(row['complete'])
            self.assertTrue(validate_lookup_receipt(row, repo=REPO))

    def test_stop_with_no_rules_and_gaps_stays_silent(self):
        gap = dict(id='22222222', statement='Moment rule.', scope={'moment': 'Before review'}, personal_to=None)
        out = self.invoke([gap], payload={'hook_event_name': 'Stop', 'last_assistant_message': 'done'})
        self.assertIsNone(out)

    def test_lookup_outage_is_advisory_not_a_denial(self):
        def broken(command, **kwargs):
            raise OSError('store unreachable')
        payload = {'hook_event_name': 'PreToolUse', 'session_id': 's', 'tool_use_id': 't',
                   'tool_name': 'Write', 'tool_input': {'file_path': '/tmp/x', 'content': 'y'}, 'cwd': str(hook.REPO)}
        out = hook.process(payload, runner=broken, dedupe=False)
        specific = out['hookSpecificOutput']
        self.assertNotIn('permissionDecision', specific)
        self.assertIn('RULE LOOKUP UNAVAILABLE', specific['additionalContext'])
        self.assertFalse(json.loads(specific['additionalContext'])['complete'])
        self.assertIsNone(hook.process(dict(payload, hook_event_name='Stop'), runner=broken, dedupe=False))

    def test_stop_never_blocks_on_lookup_failure(self):
        """A blocked Stop re-opens the turn; Codex desks looped for hours on it (2026-10-18)."""
        def crash(command, **kwargs):
            raise ValueError('unparseable catalog')
        base = {'hook_event_name': 'Stop', 'session_id': 's', 'turn_id': 't',
                'last_assistant_message': 'done', 'cwd': str(hook.REPO)}
        self.assertIsNone(hook.process(base, runner=crash, dedupe=False))
        self.assertIsNone(hook.process(dict(base, session_id=''), runner=crash, dedupe=False))
        mismatch = [dict(id='11111111', statement='Binding text', scope={'tools': ['Bash']}, personal_to='dell')]
        self.assertIsNone(self.invoke(mismatch, event='Stop'))

    def test_sponsor_mismatch_still_refuses_the_action(self):
        out = self.invoke([dict(id='11111111', statement='Binding text',
                               scope={'tools': ['Bash']}, personal_to='dell')])
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_read_only_reviewer_is_complete_without_unpicked_rule_ids(self):
        moments = [dict(id=f'{i:08x}', statement='Before code review.',
                        scope={'moment': 'Before reviewing code'}, personal_to=None)
                   for i in range(200, 400)]
        payload = {'tool_name': 'collaborationspawn_agent',
                   'session_id': '01a15200-75fd-7b12-aabd-d28e8c56e6ff',
                   'turn_id': '01a15207-5306-7310-8a41-5845b376b858',
                   'tool_use_id': 'call_4a2b92f3ea1446b987813b120927b21c',
                   'tool_input': {'task_name': 'review', 'message': 'Read-only review of the PR.'}}
        with tempfile.TemporaryDirectory() as raw, patch.object(hook, 'GAP_LOG', Path(raw) / 'gaps.jsonl'):
            output = self.invoke(moments, payload=payload)
            specific = output['hookSpecificOutput']
            self.assertNotIn('permissionDecision', specific)
            text = specific['additionalContext']
            row = json.loads(text)
            self.assertTrue(row['complete'])
            self.assertEqual(row['selection_mode'], 'deterministic')
            self.assertEqual(row['rules'], [])
            for field in ('unresolved', 'unresolved_count', 'unresolved_reasons'):
                self.assertNotIn(field, row)
            self.assertNotIn('incomplete selection', text)
            self.assertNotIn('000000c8', text)
            self.assertLess(len(text.encode()), 1024)
            self.assertTrue(validate_lookup_receipt(row, repo=REPO))
            for change in ({'selection_mode': 'unknown'}, {'unresolved': []},
                           {'data_defects': [{'reason': 'missing_scope'}]}, {'complete': False}):
                bad = dict(row, **change)
                bad['receipt_id'] = hook.receipt_id(bad)
                self.assertFalse(validate_lookup_receipt(bad, repo=REPO))
            self.assertFalse(hook.GAP_LOG.exists())

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
                if issue_key == 'unresolved':
                    self.assertEqual(row['unresolved'], [{'rule_id': '22222222', 'reason': reason}])
                else:
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
        rows = [dict(id='11111111', statement='Undeliverable rule.', scope={}, personal_to=None)]
        self.assertIsNone(self.invoke(rows, event='Stop'))
        self.assertIsNone(self.invoke(rows, event='UserPromptSubmit'))

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

    def test_codex_response_item_user_message_is_the_task(self):
        # Codex 0.159 rollouts carry user turns only as response_item messages.
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rollout.jsonl'
            msg=lambda role, text: json.dumps({'type':'response_item','payload':{'type':'message','role':role,
                'content':[{'type':'input_text','text':text}]}})
            path.write_text('\n'.join([msg('developer','skills'), msg('user','Desk task'), msg('developer','hook context')])+'\n')
            self.assertEqual(hook.task_text({'transcript_path':str(path)}),'Desk task')

    def test_task_changes_and_child_transcripts_are_read_without_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'child.jsonl'
            path.write_text(json.dumps({'type':'user','message':{'role':'user','content':'First task'}})+'\n')
            self.assertEqual(hook.task_text({'transcript_path':str(path)}),'First task')
            with path.open('a') as f:
                f.write(json.dumps({'type':'event_msg','payload':{'type':'user_message','message':'Changed task'}})+'\n')
            self.assertEqual(hook.task_text({'transcript_path':str(path)}),'Changed task')

    def test_codex_native_child_uses_its_explicit_rollout(self):
        agent = '01a1520b-a310-7cc1-86c0-4d3c49f8cec3'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f'rollout-2026-10-18T21-44-16-{agent}.jsonl'
            path.write_text(json.dumps({'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'Read-only child PR review'}]}}) + '\n')
            output = self.invoke([], payload={'prompt': None, 'transcript_path': str(path),
                'agent_id': agent, 'turn_id': 'native-turn'})
            specific = output['hookSpecificOutput']
            self.assertNotIn('permissionDecision', specific)
            row = json.loads(specific['additionalContext'])
            self.assertTrue(row['complete'])
            self.assertEqual(row['task_sha256'], digest('Read-only child PR review'))
            mismatch = self.invoke([], payload={'prompt': None, 'transcript_path': str(path),
                'agent_id': 'another-child', 'turn_id': 'native-turn'})
            self.assertEqual(mismatch['hookSpecificOutput']['permissionDecision'], 'deny')

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

    def test_unavailable_catalog_is_advisory_and_recovery_remains_reachable(self):
        def unavailable(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, '', 'secret provider detail')
        event = {'hook_event_name': 'PreToolUse', 'session_id': 'fixture',
                 'tool_use_id': 'call', 'tool_name': 'Bash', 'tool_input': {'command': 'git push'}}
        output = hook.process(event, runner=unavailable)
        self.assertNotIn('permissionDecision', output['hookSpecificOutput'])
        self.assertIn('RULE LOOKUP UNAVAILABLE', output['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('secret', json.dumps(output))
        event['tool_input'] = {'command': './run.sh call standing-context \'{}\''}
        self.assertIsNone(hook.process(event, runner=unavailable))
        event['tool_input'] = {'command': './run.sh call read-doctrine \'{"document":"engineering-workflow-sop"}\''}
        self.assertIsNone(hook.process(event, runner=unavailable))
        event['tool_input']['command'] += ' && touch forbidden'
        self.assertNotIn('permissionDecision', hook.process(event, runner=unavailable)['hookSpecificOutput'])


class CardDeliveryTests(unittest.TestCase):
    """PLANNING/TOOL cards ride PreToolUse, PROMPT cards ride UserPromptSubmit."""
    UNCOVERED = dict(id='11111111', statement='Inspect the current diff.',
                     scope={'tools': ['Bash']}, personal_to=None)

    @staticmethod
    def covered(prefix, scope=None):
        return dict(id=f'{prefix}-0000-0000-0000-000000000000', statement=f'Rule {prefix} as text.',
                    scope=scope or {'tools': ['Bash']}, personal_to=None)

    def invoke(self, rows, *, event='PreToolUse', payload=None, dedupe=False, judge=None):
        source = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a'*64,
                  'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                               'sponsoring_human_id': 'joe'}, 'rules': rows}
        base = {'hook_event_name': event, 'session_id': 'cards', 'tool_use_id': 'call',
                'tool_name': 'Bash', 'tool_input': {'command': 'git diff'}, 'prompt': 'Review this change'}
        base.update(payload or {})
        runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(source), '')
        return hook.process(base, runner=runner, dedupe=dedupe, judge=judge)

    def receipt(self, output):
        specific = output['hookSpecificOutput']
        self.assertNotIn('permissionDecision', specific)
        row = json.loads(specific['additionalContext'])
        self.assertTrue(validate_lookup_receipt(row, repo=REPO))
        return row

    def test_tool_card_replaces_its_source_rule(self):
        row = self.receipt(self.invoke([self.UNCOVERED, self.covered('24e10ee8')]))
        self.assertEqual(row['rule_ids'], ['11111111'])
        self.assertNotIn('Rule 24e10ee8', json.dumps(row))
        self.assertEqual([c['id'] for c in row['cards']], ['A01'])
        self.assertEqual(row['cards'][0]['acknowledgement'],
                         'Before acting, answer one line: applies yes/no; action or reason.')

    def test_planning_card_matches_its_own_pattern_at_pretooluse(self):
        payload = {'tool_name': 'EnterPlanMode',
                   'tool_input': {'plan': 'Should we add a new table for tour feedback or reuse notes?'}}
        row = self.receipt(self.invoke([], payload=payload))
        self.assertIn('L07', [c['id'] for c in row['cards']])

    def test_prompt_card_rides_user_prompt_submit(self):
        output = self.invoke([], event='UserPromptSubmit',
                             payload={'prompt': 'its asking me to share the transcript'})
        row = self.receipt(output)
        self.assertEqual(row['event_name'], 'UserPromptSubmit')
        self.assertEqual([c['id'] for c in row['cards']], ['R24'])
        self.assertEqual(row['rules'], [])

    def test_at_most_three_non_core_cards_per_event(self):
        rows = [self.covered(p) for p in ('8400cd3d', '2e362970', '43e2ef76', 'a3f6c7f9', 'b42f11a0')]
        row = self.receipt(self.invoke(rows))
        self.assertEqual(len(row['cards']), 3)
        self.assertEqual(row['rules'], [])
        self.assertTrue(all(c['id'][0] != 'K' for c in row['cards']))

    def test_stop_gets_no_cards_and_card_rules_never_block(self):
        self.assertIsNone(self.invoke([self.covered('24e10ee8')], event='Stop'))

    def test_card_covered_unresolved_moments_are_not_reported_twice(self):
        def unavailable(*_):
            raise TimeoutError('provider detail')
        moment = self.covered('24e10ee8', scope={'moment': 'Before a retry loop'})
        row = self.receipt(self.invoke([self.UNCOVERED, moment], judge=unavailable))
        self.assertEqual(row['unresolved'], [])
        self.assertTrue(row['complete'])

    def test_cards_are_sent_once_per_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'CARR_RULE_ROUTE_DEDUPE_DIR': os.path.join(tmp, 'routes'),
                'CARR_RULE_BOOT_STATE_DIR': os.path.join(tmp, 'boot')}):
            prompt = {'prompt': 'its asking me to share the transcript'}
            first = self.receipt(self.invoke([], event='UserPromptSubmit', payload=prompt, dedupe=True))
            self.assertEqual([c['id'] for c in first['cards']], ['R24'])
            self.assertIsNone(self.invoke([], event='UserPromptSubmit', payload=prompt, dedupe=True))

    def test_card_load_failure_fails_open_to_todays_rule_delivery(self):
        with patch.object(hook.rule_cards, 'load_cards', side_effect=ValueError('broken cards')):
            row = self.receipt(self.invoke([self.UNCOVERED, self.covered('24e10ee8')]))
        self.assertEqual(row['rule_ids'], ['11111111', '24e10ee8-0000-0000-0000-000000000000'])
        self.assertNotIn('cards', row)

    def test_selector_failure_delivers_nothing_extra_and_never_blocks(self):
        with patch.object(hook.rule_cards, 'routed_cards', side_effect=RuntimeError('selector bug')):
            row = self.receipt(self.invoke([self.UNCOVERED, self.covered('24e10ee8')]))
            self.assertIsNone(self.invoke([], event='UserPromptSubmit',
                                          payload={'prompt': 'its asking me to share the transcript'}))
        self.assertEqual(row['rule_ids'], ['11111111'])
        self.assertNotIn('cards', row)

    def test_receipt_rejects_malformed_or_oversized_card_lists(self):
        row = self.receipt(self.invoke([self.covered(p) for p in ('8400cd3d', '2e362970', '43e2ef76')]))
        for cards in ([], row['cards'] * 2, [dict(row['cards'][0], extra='x')]):
            bad = dict(row, cards=cards)
            bad['receipt_id'] = hook.receipt_id(bad)
            self.assertFalse(validate_lookup_receipt(bad, repo=REPO))


if __name__ == '__main__':
    unittest.main()
