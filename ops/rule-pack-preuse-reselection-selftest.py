"""Delivery at the hook interface, with authenticated catalog transport injected."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
import time
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


def logged_receipt():
    return json.loads(hook.RECEIPT_LOG.read_text().splitlines()[-1])


class HookTestCase(unittest.TestCase):
    def setUp(self):
        raw = tempfile.TemporaryDirectory()
        self.addCleanup(raw.cleanup)
        for name in ('GAP_LOG', 'RECEIPT_LOG'):
            patcher = patch.object(hook, name, Path(raw.name) / (name + '.jsonl'))
            patcher.start()
            self.addCleanup(patcher.stop)


class DeliveryTests(HookTestCase):
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
        options.setdefault('cache', False)
        return hook.process(payload, runner=runner, dedupe=False, **options)

    def test_defects_only_pretooluse_is_silent_and_logged(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(hook, 'GAP_LOG', Path(raw) / 'gaps.jsonl'):
            output = self.invoke([dict(id='22222222', statement='Invalid scope.', scope={}, personal_to=None)])
            self.assertIsNone(output)
            self.assertIn('missing_scope', hook.GAP_LOG.read_text())
            with patch.object(hook, 'process', return_value=output):
                verdict = hook.decide({})
            self.assertEqual((verdict.code, verdict.stdout), (0, ''))

    def test_log_failure_does_not_change_delivery_or_silence(self):
        with patch.object(hook, 'RECEIPT_LOG', Path('/dev/null/receipts.jsonl')), \
                patch.object(hook, 'GAP_LOG', Path('/dev/null/gaps.jsonl')):
            known = dict(id='11111111', statement='Inspect the diff.', scope={'tools': ['Bash']}, personal_to=None)
            out = self.invoke([known])
            self.assertEqual(json.loads(out['hookSpecificOutput']['additionalContext'])['rules'], [known])
            self.assertIsNone(self.invoke([dict(known, scope={})]))

    def test_complete_oversized_delivery_still_denies(self):
        rows = [dict(id=f'{i:08x}', statement='Binding rule.', scope={'tools': ['Bash']}, personal_to=None)
                for i in range(1000, 1600)]
        out = self.invoke(rows)
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_logged_receipt_preserves_validation_guards(self):
        self.invoke([dict(id='11111111', statement='Inspect the diff.', scope={'tools': ['Bash']}, personal_to=None)])
        row = logged_receipt()
        self.assertTrue(validate_lookup_receipt(row, repo=REPO))
        for change in ({'selection_mode': 'unknown'}, {'unresolved': []},
                       {'data_defects': [{'reason': 'missing_scope'}]}, {'complete': False}):
            bad = dict(row, **change)
            bad['receipt_id'] = hook.receipt_id(bad)
            self.assertFalse(validate_lookup_receipt(bad, repo=REPO))

    def test_empty_lookup_has_no_stdout(self):
        output = self.invoke([])
        self.assertIsNone(output)
        with patch.object(hook, 'process', return_value=output):
            verdict = hook.decide({})
        self.assertEqual((verdict.code, verdict.stdout), (0, ''))

    def test_nonempty_and_incomplete_outputs_still_reach_stdout(self):
        outputs = [
            self.invoke([dict(id='11111111', statement='Inspect the diff.',
                scope={'tools': ['Bash']}, personal_to=None)]),
            self.invoke([dict(id='33333333', statement='Binding text ' * 2000,
                scope={'tools': ['Bash']}, personal_to=None)]),
            hook.context('RULE LOOKUP INCOMPLETE. Full text could not fit.', 'PreToolUse'),
            hook.refused('PreToolUse'),
        ]
        for output in outputs:
            with self.subTest(output=output), patch.object(hook, 'process', return_value=output):
                verdict = hook.decide({})
                self.assertEqual(verdict.code, 0)
                self.assertEqual(json.loads(verdict.stdout), output)

    def test_data_defects_are_logged_for_all_events(self):
        gaps = [dict(id='22222222', statement='Moment rule.',
                     scope={'moment': 'Before review'}, personal_to=None),
                dict(id='33333333', statement='Invalid scope.', scope={}, personal_to=None)]
        with tempfile.TemporaryDirectory() as raw, patch.object(hook, 'GAP_LOG', Path(raw) / 'gaps.jsonl', create=True):
            for event in ('PreToolUse', 'UserPromptSubmit', 'Stop'):
                output = self.invoke(gaps, event=event)
                context = (output or {}).get('hookSpecificOutput', {}).get('additionalContext', '')
                self.assertEqual(len(context.encode()), 0)
            rows = [json.loads(line) for line in hook.GAP_LOG.read_text().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]['unresolved'], [])
            self.assertEqual(rows[0]['data_defects'][0]['rule_id'], '33333333')


    def test_stop_with_no_rules_and_gaps_stays_silent(self):
        gap = dict(id='22222222', statement='Moment rule.', scope={'moment': 'Before review'}, personal_to=None)
        out = self.invoke([gap], payload={'hook_event_name': 'Stop', 'last_assistant_message': 'done'})
        self.assertIsNone(out)

    def test_lookup_outage_is_advisory_not_a_denial(self):
        def broken(command, **kwargs):
            raise OSError('store unreachable')
        payload = {'hook_event_name': 'PreToolUse', 'session_id': 's', 'tool_use_id': 't',
                   'tool_name': 'Write', 'tool_input': {'file_path': '/tmp/x', 'content': 'y'}, 'cwd': str(hook.REPO)}
        out = hook.process(payload, runner=broken, dedupe=False, cache=False)
        specific = out['hookSpecificOutput']
        self.assertNotIn('permissionDecision', specific)
        self.assertIn('RULE LOOKUP UNAVAILABLE', specific['additionalContext'])
        self.assertFalse(json.loads(specific['additionalContext'])['complete'])
        self.assertIsNone(hook.process(dict(payload, hook_event_name='Stop'), runner=broken, dedupe=False, cache=False))

    def test_stop_never_blocks_on_lookup_failure(self):
        """A blocked Stop re-opens the turn; Codex desks looped for hours on it (2026-10-18)."""
        def crash(command, **kwargs):
            raise ValueError('unparseable catalog')
        base = {'hook_event_name': 'Stop', 'session_id': 's', 'turn_id': 't',
                'last_assistant_message': 'done', 'cwd': str(hook.REPO)}
        self.assertIsNone(hook.process(base, runner=crash, dedupe=False, cache=False))
        self.assertIsNone(hook.process(dict(base, session_id=''), runner=crash, dedupe=False, cache=False))
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
            self.assertIsNone(output)
            self.assertFalse(hook.GAP_LOG.exists())


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
        self.assertNotIn('missing_scope', specific['additionalContext'])
        self.assertIn('missing_scope', hook.GAP_LOG.read_text())
        self.assertFalse(logged_receipt()['complete'])
        self.assertNotIn('Undeliverable rule.', specific['additionalContext'])
        self.assertTrue(validate_lookup_receipt(logged_receipt(), repo=REPO))

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
        self.invoke([dict(id='11111111', statement='Binding text',
                         scope={'tools':['Bash']}, personal_to=None)])
        row = logged_receipt()
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
            self.assertIsNone(output)
            self.assertEqual(hook.task_text({'transcript_path': str(path), 'agent_id': agent,
                                             'turn_id': 'native-turn'}), 'Read-only child PR review')
            sibling = path.with_suffix('') / 'subagents' / 'agent-another-child.jsonl'
            sibling.parent.mkdir(parents=True)
            sibling.write_text(path.read_text())
            mismatch = self.invoke([], payload={'prompt': None, 'transcript_path': str(path),
                'agent_id': 'another-child', 'turn_id': 'native-turn'})
            self.assertEqual(mismatch['hookSpecificOutput']['permissionDecision'], 'deny')


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
                self.assertEqual([r['id'] for r in row['rules']], ['11111111'])
                self.assertEqual(logged_receipt()['task_sha256'], digest('Review the revised change 🩺'))

    def test_context_dedupe_invalidates_when_catalog_changes(self):
        rr=hook.rule_routes
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CARR_RULE_ROUTE_DEDUPE_DIR=tmp):
            rr.record_delivered('s','Bash',['11111111'],corpus_digest='a'*64)
            self.assertEqual(rr.fresh_ids('s','Edit',['11111111'],corpus_digest='a'*64),[])
            self.assertEqual(rr.fresh_ids('s','Edit',['11111111'],corpus_digest='b'*64),['11111111'])


    def test_unavailable_catalog_is_advisory_and_recovery_remains_reachable(self):
        def unavailable(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, '', 'secret provider detail')
        event = {'hook_event_name': 'PreToolUse', 'session_id': 'fixture',
                 'tool_use_id': 'call', 'tool_name': 'Bash', 'tool_input': {'command': 'git push'}}
        output = hook.process(event, runner=unavailable, cache=False)
        self.assertNotIn('permissionDecision', output['hookSpecificOutput'])
        self.assertIn('RULE LOOKUP UNAVAILABLE', output['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('secret', json.dumps(output))
        event['tool_input'] = {'command': './run.sh call standing-context \'{}\''}
        self.assertIsNone(hook.process(event, runner=unavailable, cache=False))
        event['tool_input'] = {'command': './run.sh call read-doctrine \'{"document":"engineering-workflow-sop"}\''}
        self.assertIsNone(hook.process(event, runner=unavailable, cache=False))
        event['tool_input']['command'] += ' && touch forbidden'
        self.assertNotIn('permissionDecision', hook.process(event, runner=unavailable, cache=False)['hookSpecificOutput'])


class CardDeliveryTests(HookTestCase):
    """PLANNING/TOOL cards ride PreToolUse, PROMPT cards ride UserPromptSubmit."""
    UNCOVERED = dict(id='11111111', statement='Inspect the current diff.',
                     scope={'tools': ['Bash']}, personal_to=None)

    @staticmethod
    def covered(prefix, scope=None):
        return dict(id=f'{prefix}-0000-0000-0000-000000000000', statement=f'Rule {prefix} as text.',
                    scope=scope or {'tools': ['Bash']}, personal_to=None)

    def invoke(self, rows, *, event='PreToolUse', payload=None, dedupe=False):
        source = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a'*64,
                  'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                               'sponsoring_human_id': 'joe'}, 'rules': rows}
        base = {'hook_event_name': event, 'session_id': 'cards', 'tool_use_id': 'call',
                'tool_name': 'Bash', 'tool_input': {'command': 'git diff'}, 'prompt': 'Review this change'}
        base.update(payload or {})
        runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(source), '')
        return hook.process(base, runner=runner, dedupe=dedupe, cache=False)

    def receipt(self, output):
        specific = output['hookSpecificOutput']
        self.assertNotIn('permissionDecision', specific)
        visible = json.loads(specific['additionalContext'])
        self.assertLessEqual(set(visible), {'schema', 'rules', 'overflow', 'cards', 'instruction'})
        row = logged_receipt()
        self.assertTrue(validate_lookup_receipt(row, repo=REPO))
        for key in ('rules', 'overflow', 'cards'):
            self.assertEqual(visible.get(key), row.get(key))
        return row

    def test_tool_card_replaces_its_source_rule(self):
        row = self.receipt(self.invoke([self.UNCOVERED, self.covered('24e10ee8')]))
        self.assertEqual(row['rule_ids'], ['11111111'])
        self.assertNotIn('Rule 24e10ee8', json.dumps(row))
        self.assertEqual([c['id'] for c in row['cards']], ['A01'])
        self.assertEqual(row['cards'][0]['acknowledgement'],
                         'Before acting, answer one line: applies yes/no; action or reason.')

    def test_card_only_lookup_still_reaches_stdout(self):
        output = self.invoke([], payload={'tool_name': 'EnterPlanMode',
            'tool_input': {'plan': 'Should we add a new table for tour feedback or reuse notes?'}})
        with patch.object(hook, 'process', return_value=output):
            verdict = hook.decide({})
        self.assertEqual(verdict.code, 0)
        row = self.receipt(json.loads(verdict.stdout))
        self.assertEqual(row['rules'], [])
        self.assertIn('L07', [card['id'] for card in row['cards']])

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


class CatalogCacheTests(HookTestCase):
    """standing-context is read once per user prompt, not once per tool call (WR573.S1.J2)."""
    RULE = dict(id='11111111', statement='Inspect the diff.', scope={'tools': ['Bash']}, personal_to=None)

    def setUp(self):
        super().setUp()
        raw = tempfile.TemporaryDirectory()
        self.addCleanup(raw.cleanup)
        self.root = Path(raw.name)
        env = patch.dict(os.environ, {'CARR_RULE_SCOPE_CACHE_DIR': raw.name})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []
        self.source = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a' * 64,
                       'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                                    'sponsoring_human_id': 'joe'}, 'rules': [self.RULE]}

    def runner(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.source), '')

    def prompt(self, session='s1'):
        return hook.process({'hook_event_name': 'UserPromptSubmit', 'session_id': session,
                             'prompt': 'Review this change'}, runner=self.runner, dedupe=False)

    def tool(self, session='s1', n=0, cache=True, tool_name='Bash', tool_input=None):
        payload = {'hook_event_name': 'PreToolUse', 'session_id': session, 'tool_use_id': f'call-{n}',
                   'tool_name': tool_name, 'tool_input': tool_input or {'command': 'git diff'},
                   'prompt': 'Review this change'}
        return hook.process(payload, runner=self.runner, dedupe=False, cache=cache)

    @staticmethod
    def rule_ids(output):
        if output is None:
            return []
        row = json.loads(output['hookSpecificOutput']['additionalContext'])
        return sorted(r['id'] for r in row['rules'] + row['overflow'])

    def test_one_fetch_per_prompt_and_tool_calls_reuse_it(self):
        self.prompt()
        outputs = [self.tool(n=n) for n in range(25)]
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(self.rule_ids(o) == ['11111111'] for o in outputs))
        self.prompt()
        self.tool(n=99)
        self.assertEqual(len(self.calls), 2, 'every user prompt reads the store once')
        self.tool(session='s2')
        self.assertEqual(len(self.calls), 3, 'another session reads its own catalog')

    def test_a_rule_changed_mid_prompt_applies_from_the_next_prompt(self):
        self.prompt()
        self.source.update(corpus_digest='b' * 64, rules=[])
        self.assertEqual(self.rule_ids(self.tool(n=1)), ['11111111'])
        self.prompt()
        self.assertEqual(self.rule_ids(self.tool(n=2)), [])

    def test_a_long_session_without_prompts_rereads_after_the_window(self):
        self.prompt()
        self.source.update(corpus_digest='b' * 64, rules=[])
        self.assertEqual(self.rule_ids(self.tool(n=1)), ['11111111'])
        cached = hook._catalog_path('s1')
        aged = time.time() - hook.CATALOG_MAX_SECONDS - 1
        os.utime(cached, (aged, aged))
        self.assertEqual(self.rule_ids(self.tool(n=2)), [], 'a stop rule reaches a prompt-less job')
        self.tool(n=3)
        self.assertEqual(len(self.calls), 2, 'then the fresh catalog is reused')

    def test_a_tool_call_before_any_prompt_fetches_once_and_caches(self):
        self.tool(n=1)
        self.tool(n=2)
        self.assertEqual(len(self.calls), 1)

    def test_a_rule_writing_verb_clears_the_cache_for_the_next_call(self):
        self.prompt()
        for name, tool_input in (('mcp__carr__adopt-rule', {'rule_id': 'x'}),
                                 ('mcp__carr__approve-rule', {'rule_id': 'x'}),
                                 ('mcp__carr__call-verb', {'verb': 'retire-rule', 'args': {}}),
                                 ('Bash', {'command': "./run.sh call retire-rule '{}'"}),
                                 ('Bash', {'command': "./run.sh call bind-rule-context-contract '{}'"})):
            with self.subTest(name=name):
                before = len(self.calls)
                self.tool(n=1, tool_name=name, tool_input=tool_input)
                self.assertEqual(len(self.calls), before + 1, 'the write call reads the store itself')
                self.source.update(corpus_digest='c' * 64, rules=[])
                self.assertEqual(self.rule_ids(self.tool(n=2)), [], 'the call after the write sees the new corpus')
                self.tool(n=3)
                self.assertEqual(len(self.calls), before + 2, 'then the new catalog is reused')
                self.source.update(corpus_digest='a' * 64, rules=[self.RULE])

    def test_a_catalog_that_fails_validation_is_never_cached(self):
        for bad in ({'ok': False}, {'rules': [dict(self.RULE, id='not-an-id')]},
                    {'rules': [dict(self.RULE, personal_to='dell')]}):
            with self.subTest(bad=bad):
                good = dict(self.source)
                self.source = dict(good, **bad)
                denied = self.prompt()
                self.assertEqual(denied.get('decision'), 'block')
                self.assertEqual(list(self.root.glob('*.json')), [], 'a refused catalog is not written')
                self.source = good
                before = len(self.calls)
                self.assertEqual(self.rule_ids(self.tool(n=1)), ['11111111'])
                self.assertEqual(len(self.calls), before + 1, 'the fixed data is read on the next call')
                for cached in self.root.glob('*.json'):
                    cached.unlink()

    def test_outages_and_corrupt_files_are_not_served(self):
        def down(command, **kwargs):
            self.calls.append(command)
            return subprocess.CompletedProcess(command, 1, '', 'store down')
        event = {'hook_event_name': 'PreToolUse', 'session_id': 's1', 'tool_use_id': 'c',
                 'tool_name': 'Bash', 'tool_input': {'command': 'git diff'}}
        out = hook.process(event, runner=down, dedupe=False)
        self.assertIn('RULE LOOKUP UNAVAILABLE', out['hookSpecificOutput']['additionalContext'])
        self.tool()
        self.assertEqual(len(self.calls), 2)
        cached = next(self.root.glob('*.json'))
        self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        cached.write_text('{not json')
        self.tool(n=1)
        self.assertEqual(len(self.calls), 3)

    def test_cached_output_is_identical_to_an_uncached_lookup(self):
        self.prompt()
        warm = [self.tool(n=n) for n in range(3)]
        cold = [self.tool(n=n, cache=False) for n in range(3)]
        self.assertEqual(warm, cold)

    def test_files_older_than_a_day_are_removed_when_a_catalog_is_written(self):
        stale, recent = self.root / ('0' * 64 + '.json'), self.root / ('1' * 64 + '.json')
        for path, age in ((stale, 86400 + 60), (recent, 60)):
            path.write_text('{}')
            os.utime(path, (time.time() - age, time.time() - age))
        self.prompt()
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())

    def test_a_busy_lock_falls_back_to_a_direct_read_without_waiting(self):
        import fcntl
        self.root.mkdir(exist_ok=True)
        lock_path = hook._catalog_path('s1').with_suffix('.lock')
        with open(lock_path, 'a') as held, patch.object(hook, 'CATALOG_LOCK_SECONDS', 0.2):
            fcntl.flock(held, fcntl.LOCK_EX)
            started = time.monotonic()
            self.assertEqual(self.rule_ids(self.tool()), ['11111111'])
            self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(list(self.root.glob('*.json')), [])


if __name__ == '__main__':
    unittest.main()
