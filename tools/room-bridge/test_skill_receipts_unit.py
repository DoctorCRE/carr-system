import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_receipts
import dispatch
from ops.git_env import fixture_env


class SkillReceiptTests(unittest.TestCase):
    def scan(self, rows, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            return skill_receipts.scan(path, **kwargs)

    def codex(self, command, output='Process exited with code 0', stamp='2026-10-19T15:00:01Z'):
        return [
            {'timestamp': stamp, 'type': 'response_item', 'payload': {
                'type': 'function_call', 'name': 'exec_command', 'call_id': 'one',
                'arguments': json.dumps({'cmd': command})}},
            {'timestamp': stamp, 'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'one', 'output': output}},
        ]

    def test_menu_is_available_but_not_opened(self):
        result = self.scan([{'type': 'response_item', 'payload': {'type': 'message',
            'role': 'developer', 'content': [{'type': 'input_text', 'text':
            '### Available skills\n- tdd: Testing (file: /skills/tdd/SKILL.md)'}]}}])
        self.assertEqual(result['available'], ['tdd'])
        self.assertEqual(result['opened'], [])

    def test_claude_init_listing_seeds_only_its_own_desk_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'builder.log').write_text(json.dumps({'type': 'system', 'subtype': 'init',
                'session_id': 'a' * 36, 'skills': ['tdd', 'pstack:poteto-mode']}) + '\n')
            with patch.object(dispatch, 'DESK_STATE', root):
                value = dispatch.desk_skill_inventory('builder', {'kind': 'claude-session'}, root / 'results.jsonl')
                self.assertEqual(value['available'], ['poteto-mode', 'tdd'])
                self.assertIsNone(dispatch.desk_skill_inventory('other', {'kind': 'claude-session'}, root / 'results.jsonl'))

    def test_only_successful_real_reads_count(self):
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md'))['opened'],
                         ['/skills/tdd/SKILL.md'])
        for command in ('echo /skills/tdd/SKILL.md', 'rg --files /skills/tdd/SKILL.md',
                        'cat /skills/tdd/SKILL.md > /tmp/copy',
                        'echo "cat /skills/tdd/SKILL.md"'):
            self.assertEqual(self.scan(self.codex(command))['opened'], [], command)
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md',
                                             'Process exited with code 1'))['opened'], [])

    def test_compound_shell_status_does_not_prove_an_individual_read(self):
        for command in (
            'true || cat /missing/tdd/SKILL.md',
            'cat /missing/tdd/SKILL.md; true',
            'cat /missing/tdd/SKILL.md | true',
            'cat /missing/tdd/SKILL.md & true',
            'cat /missing/tdd/SKILL.md\ntrue',
            'false && cat /missing/tdd/SKILL.md || true',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.scan(self.codex(command))['opened'], [])
                rows = [
                    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'r',
                     'name': 'Bash', 'input': {'command': command}}]}},
                    {'type': 'user', 'message': {'content': [{'type': 'tool_result',
                     'tool_use_id': 'r', 'content': '', 'is_error': False}]}},
                ]
                self.assertEqual(self.scan(rows)['opened'], [])

    def test_reader_options_cannot_bypass_the_file(self):
        for command in (
            'cat /missing/tdd/SKILL.md --help',
            'head --version /missing/tdd/SKILL.md',
            'sed --version /missing/tdd/SKILL.md',
            "sed -n 'e true' /missing/tdd/SKILL.md",
            'cat /missing/tdd/SKILL.md#suffix',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.scan(self.codex(command))['opened'], [])
        for command in ('cat -- /skills/tdd/SKILL.md', 'head /skills/tdd/SKILL.md',
                        'tail /skills/tdd/SKILL.md', "sed -n '1,20p' /skills/tdd/SKILL.md"):
            self.assertEqual(self.scan(self.codex(command))['opened'], ['/skills/tdd/SKILL.md'])

    def test_wrapper_cannot_override_the_literal_command(self):
        rows = self.codex('unused')
        rows[0]['payload'].update(type='custom_tool_call', name='exec')
        rows[1]['payload'].update(type='custom_tool_call_output', output='{"exit_code":0}')
        for code in (
            'text(await tools.exec_command({cmd:"cat /missing/tdd/SKILL.md", cmd:"true"}));',
            'text(await tools.exec_command({cmd:"cat /missing/tdd/SKILL.md", ...options}));',
        ):
            with self.subTest(code=code):
                rows[0]['payload']['input'] = code
                self.assertEqual(self.scan(rows)['opened'], [])

    def test_exec_wrapper_and_claude_read(self):
        rows = self.codex('unused')
        rows[0]['payload'].update(name='functions.exec', arguments=json.dumps({
            'code': 'text(await tools.exec_command({cmd:"cat /skills/tdd/SKILL.md"}));'}))
        rows[1]['payload']['output'] = '{"exit_code":0,"output":"skill"}'
        self.assertEqual(self.scan(rows)['opened'], ['/skills/tdd/SKILL.md'])
        rows[1]['payload']['output'] = [{'type': 'text', 'text': 'hook receipt'},
            {'type': 'text', 'text': '{"exit_code":0,"output":"skill"}'}]
        self.assertEqual(self.scan(rows)['opened'], ['/skills/tdd/SKILL.md'])
        rows[0]['payload'].update(type='custom_tool_call', name='exec',
            input='text(await tools.exec_command({cmd:"cat /skills/tdd/SKILL.md"}));')
        rows[1]['payload']['type'] = 'custom_tool_call_output'
        self.assertEqual(self.scan(rows)['opened'], ['/skills/tdd/SKILL.md'])
        self.assertEqual(self.scan([
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'r',
             'name': 'Read', 'input': {'file_path': '/skills/poteto-mode/SKILL.md'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result',
             'tool_use_id': 'r', 'content': 'skill', 'is_error': False}]}},
        ])['opened'], ['/skills/poteto-mode/SKILL.md'])

    def test_claude_bash_success_and_failure(self):
        rows = [
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'r',
             'name': 'Bash', 'input': {'command': 'cat /skills/tdd/SKILL.md'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result',
             'tool_use_id': 'r', 'content': 'skill contents', 'is_error': False}]}},
        ]
        self.assertEqual(self.scan(rows)['opened'], ['/skills/tdd/SKILL.md'])
        rows[1]['message']['content'][0]['is_error'] = True
        self.assertEqual(self.scan(rows)['opened'], [])

    def test_wrapper_proves_the_executed_read_not_quoted_or_conditional_code(self):
        rows = self.codex('unused')
        rows[0]['payload'].update(type='custom_tool_call', name='exec')
        rows[1]['payload'].update(type='custom_tool_call_output', output='{"exit_code":0,"output":"ok"}')
        for code in [
            "const example = 'tools.exec_command({cmd:\"cat /skills/tdd/SKILL.md\"})'; text(await tools.exec_command({cmd:\"true\"}));",
            'if (false) { text(await tools.exec_command({cmd:"cat /skills/tdd/SKILL.md"})); } text(await tools.exec_command({cmd:"true"}));',
        ]:
            rows[0]['payload']['input'] = code
            self.assertEqual(self.scan(rows)['opened'], [])
        rows[0]['payload']['input'] = 'text(await tools.exec_command({cmd:"cat /skills/tdd/SKILL.md"})); text(await tools.exec_command({cmd:"false"}));'
        rows[1]['payload']['output'] = [{'type': 'text', 'text': '{"exit_code":0,"output":"skill"}'},
                                       {'type': 'text', 'text': '{"exit_code":1,"output":"unrelated error"}'}]
        self.assertEqual(self.scan(rows)['opened'], ['/skills/tdd/SKILL.md'])

    def test_inventory_error_prevents_executor_launch(self):
        class Registry:
            def resolve(self, name):
                return {'kind': 'codex-session', 'thread_id': None}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dispatch, 'desk_skill_inventory', return_value={'available': ['tdd']}), \
             patch.object(dispatch, '_execute') as execute:
            with self.assertRaisesRegex(dispatch.DeskError, 'not available.*grill-with-docs'):
                dispatch.dispatch('builder', 'Skills: grill-with-docs', registry=Registry(),
                                  results_path=Path(directory) / 'results.jsonl')
        execute.assert_not_called()

    def test_prior_job_reads_and_unfinished_calls_do_not_count(self):
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md'),
            since='2026-10-19T16:00:00Z')['opened'], [])
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md')[:1])['opened'], [])

    def test_reads_and_results_must_both_be_inside_the_job_window(self):
        start, end = '2026-10-19T15:00:00Z', '2026-10-19T15:00:02.500000Z'
        inside = self.codex('cat /skills/tdd/SKILL.md')
        self.assertEqual(self.scan(inside, since=start, until=end)['opened'], ['/skills/tdd/SKILL.md'])
        late = self.codex('cat /skills/tdd/SKILL.md', stamp='2026-10-19T15:00:02.500001Z')
        self.assertEqual(self.scan(late, since=start, until=end)['opened'], [])
        inside[1]['timestamp'] = late[1]['timestamp']
        self.assertEqual(self.scan(inside, since=start, until=end)['opened'], [])
        inside[1].pop('timestamp')
        self.assertEqual(self.scan(inside, since=start, until=end)['opened'], [])
        claude = [
            {'timestamp': start, 'type': 'assistant', 'message': {'content': [{'type': 'tool_use',
             'id': 'r', 'name': 'Read', 'input': {'file_path': '/skills/tdd/SKILL.md'}}]}},
            {'timestamp': late[1]['timestamp'], 'type': 'user', 'message': {'content': [{'type': 'tool_result',
             'tool_use_id': 'r', 'content': 'skill', 'is_error': False}]}},
        ]
        self.assertEqual(self.scan(claude, since=start, until=end)['opened'], [])
        claude[1]['timestamp'] = end
        self.assertEqual(self.scan(claude, since=start, until=end)['opened'], ['/skills/tdd/SKILL.md'])
        for kwargs in ({'since': 'invalid', 'until': end}, {'since': start, 'until': 'invalid'},
                       {'since': end, 'until': start}):
            with self.assertRaises(ValueError):
                self.scan(inside, **kwargs)

    def test_aliases_and_missing_inventory_fail_before_launch(self):
        self.assertEqual(skill_receipts.claims('Skills: Pocock TDD, Prove It Works; poteto-mode'),
                         ['tdd', 'prove-it-works', 'poteto-mode'])
        self.assertEqual(skill_receipts.claims((Path(__file__).parent / 'brief-footer.md').read_text()),
                         ['tdd', 'poteto-mode', 'prove-it-works', 'grill-with-docs'])
        with self.assertRaisesRegex(ValueError, 'not available.*grill-with-docs'):
            skill_receipts.require_skills('Skills: grill-with-docs', {'available': ['tdd']})
        with self.assertRaisesRegex(ValueError, 'inventory unknown'):
            skill_receipts.require_skills('Skills: tdd', None)

    def test_completed_job_uses_its_own_thread_and_dispatch_time(self):
        row = {'msg_id': 'job', 'status': 'completed', 'thread_id': 'own-session',
               'kind': 'codex-session', 'dispatched_at': '2026-10-19T15:00:00Z',
               'source_revision': 'a' * 40,
               'completed_at': '2026-10-19T15:00:02.500000Z',
               'result': 'https://github.com/jbookout/carr-system/pull/2000'}
        with patch.object(skill_receipts, 'session_path', return_value=Path('/own-log')) as locate, \
             patch.object(skill_receipts, 'scan', return_value={'available': ['tdd'], 'opened': []}) as scan, \
             patch.object(dispatch, '_skill_gh', side_effect=[
                 {'head': {'sha': 'a' * 40}, 'body': 'Skills used: tdd'}, [],
                 {'html_url': 'https://github.com/jbookout/carr-system/issues/2000#issuecomment-1'}, {}]):
            value = dispatch.finish_skill_receipt(row)
        locate.assert_called_once_with('own-session', 'codex')
        scan.assert_called_once_with(Path('/own-log'), since=row['dispatched_at'], until=row['completed_at'])
        self.assertFalse(value['skills'][0]['opened'])

    def test_job_revision_is_saved_before_publication_and_cannot_follow_a_new_head(self):
        class Registry:
            def resolve(self, name):
                return {'kind': 'claude-session', 'cwd': directory}
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(['git', *args], cwd=directory, env=fixture_env(),
                    check=True, capture_output=True, text=True).stdout.strip()
            git('init')
            def commit(message):
                git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--allow-empty', '-m', message)
                return git('rev-parse', 'HEAD')
            commit('before job')
            results = Path(directory) / 'results.jsonl'
            transcript = Path(directory) / 'session.jsonl'
            transcript.write_text('')
            pr = 'https://github.com/jbookout/carr-system/pull/2000'
            revision = None
            def execute(*args, **kwargs):
                nonlocal revision
                revision = commit('job output')
                return {'status': 'completed', 'session_id': 'own-session', 'result': pr}
            def fail_publication(*args, **kwargs):
                saved = json.loads(results.read_text().splitlines()[0])
                self.assertEqual(saved.get('source_revision'), revision)
                raise ValueError('publication failed')
            with patch.object(dispatch, 'desk_skill_inventory', return_value=None), \
                 patch.object(dispatch, '_execute', side_effect=execute), \
                 patch.object(skill_receipts, 'session_path', return_value=transcript), \
                 patch.object(dispatch, '_skill_gh', side_effect=fail_publication):
                row = dispatch.dispatch('builder', 'fix it', registry=Registry(), results_path=results)
            self.assertEqual(row.get('source_revision'), revision)
            newer = commit('later job')
            for head in (revision, newer):
                with self.subTest(head=head), \
                     patch.object(skill_receipts, 'session_path', return_value=transcript), \
                     patch.object(dispatch, '_execute') as rerun, \
                     patch.object(dispatch, '_skill_gh', side_effect=[
                         {'head': {'sha': head}, 'body': 'Skills used: tdd'}, [],
                         {'html_url': pr + '#issuecomment-1'}, {}]) as gh:
                    code = dispatch.main(['--results', str(results), 'receipt', row['msg_id']])
                    self.assertEqual(code, 0 if head == revision else 2)
                    rerun.assert_not_called()
                    if head == newer:
                        self.assertEqual(gh.call_count, 1, 'stale job must not publish to a newer PR head')
                    else:
                        saved = dispatch._receipt_job(results, row['msg_id'])
                        self.assertEqual(saved['skill_receipt']['head'], revision)

    def test_completion_boundary_survives_publication_failure_and_retry(self):
        class Registry:
            def resolve(self, name):
                return {'kind': 'claude-session'}
        start, end = '2026-10-19T15:00:00Z', '2026-10-19T15:00:02.500000Z'
        pr = 'https://github.com/jbookout/carr-system/pull/2000'
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / 'results.jsonl'
            transcript = Path(directory) / 'session.jsonl'
            transcript.write_text('')
            with patch.object(dispatch, 'desk_skill_inventory', return_value=None), \
                 patch.object(dispatch, '_execute', return_value={'status': 'completed',
                     'session_id': 'own-session', 'result': pr}), \
                 patch.object(dispatch, '_now', side_effect=[start, end]), \
                 patch.object(skill_receipts, 'session_path', return_value=transcript), \
                 patch.object(dispatch, '_skill_gh', side_effect=ValueError('publication failed')):
                row = dispatch.dispatch('builder', 'fix it', registry=Registry(), results_path=results)
            saved = json.loads(results.read_text().splitlines()[0])
            self.assertEqual(saved['completed_at'], end)
            self.assertIn('skill_receipt_error', row)
            transcript.write_text('\n'.join(json.dumps(event) for event in
                self.codex('cat /skills/tdd/SKILL.md', stamp='2026-10-19T15:00:02.500001Z')))
            with patch.object(skill_receipts, 'session_path', return_value=transcript), \
                 patch.object(dispatch, '_execute') as execute, \
                 patch.object(dispatch, '_skill_gh', side_effect=[
                     {'head': {'sha': 'a' * 40}, 'body': 'Skills used: tdd'}, [],
                     {'html_url': pr + '#issuecomment-1'}, {}]):
                self.assertEqual(dispatch.main(['--results', str(results), 'receipt', row['msg_id']]), 0)
            execute.assert_not_called()
            receipt = json.loads(dispatch._skill_ledger(results).read_text().splitlines()[-1])
            self.assertEqual(receipt['completed_at'], end)
            self.assertFalse(receipt['skill_receipt']['skills'][0]['opened'])

    def test_missing_completion_window_cannot_publish(self):
        row = {'msg_id': 'job', 'status': 'completed', 'kind': 'codex-session',
               'dispatched_at': '2026-10-19T15:00:00Z',
               'result': 'https://github.com/jbookout/carr-system/pull/2000'}
        for changes in ({}, {'completed_at': 'invalid'},
                        {'completed_at': '2026-10-19T15:00:02Z', 'dispatched_at': None}):
            with self.subTest(changes=changes), patch.object(dispatch, '_skill_gh') as gh:
                with self.assertRaisesRegex(ValueError, 'window'):
                    dispatch.finish_skill_receipt({**row, **changes})
                gh.assert_not_called()

    def test_async_completion_uses_the_terminal_message_and_retries_the_saved_window(self):
        start, end = '2026-10-19T15:00:00Z', '2026-10-19T15:00:02.500000Z'
        pr = 'https://github.com/jbookout/carr-system/pull/2000'
        row = {'msg_id': 'job', 'status': 'delivered', 'kind': 'claude-session',
               'dispatched_at': start, 'session_id': 'own-session'}
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / 'results.jsonl'
            results.write_text(json.dumps(row) + '\n')
            transcript = Path(directory) / 'session.jsonl'
            final = {'timestamp': end, 'type': 'assistant', 'message': {'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': pr}]}}
            transcript.write_text('\n'.join(json.dumps(event) for event in [final,
                *self.codex('cat /skills/tdd/SKILL.md', stamp='2026-10-19T15:00:03Z')]))
            def fail_after_boundary_saved(*args, **kwargs):
                saved = json.loads(dispatch._skill_ledger(results).read_text().splitlines()[-1])
                self.assertEqual(saved['completed_at'], end)
                raise ValueError('publication failed')
            with patch.object(skill_receipts, 'session_path', return_value=transcript), \
                 patch.object(dispatch, '_skill_gh', side_effect=fail_after_boundary_saved):
                dispatch.finish_pending_skill_receipt({'dispatch_msg_id': 'job'}, pr,
                    Path(directory) / 'log', results_path=results)
            with transcript.open('a') as handle:
                handle.write('\n' + json.dumps({**final, 'timestamp': '2026-10-19T16:00:00Z'}))
            with patch.object(skill_receipts, 'session_path', return_value=transcript), \
                 patch.object(dispatch, '_now') as now, \
                 patch.object(dispatch, '_execute') as execute, \
                 patch.object(dispatch, '_skill_gh', side_effect=[
                     {'head': {'sha': 'a' * 40}, 'body': 'Skills used: tdd'}, [],
                     {'html_url': pr + '#issuecomment-1'}, {}]):
                self.assertEqual(dispatch.main(['--results', str(results), 'receipt', 'job',
                    '--session-id', 'own-session', '--pr', pr]), 0)
            now.assert_not_called()
            execute.assert_not_called()
            saved = json.loads(dispatch._skill_ledger(results).read_text().splitlines()[-1])
            self.assertEqual(saved['completed_at'], end)
            self.assertFalse(saved['skill_receipt']['skills'][0]['opened'])

    def test_async_unreadable_completion_records_a_receipt_error_without_losing_the_job(self):
        row = {'msg_id': 'job', 'status': 'delivered', 'kind': 'claude-session',
               'dispatched_at': '2026-10-19T15:00:00Z', 'session_id': 'own-session'}
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / 'results.jsonl'
            results.write_text(json.dumps(row) + '\n')
            with patch.object(skill_receipts, 'session_path', return_value=Path(directory) / 'missing'), \
                 patch.object(dispatch, '_skill_gh') as gh:
                dispatch.finish_pending_skill_receipt({'dispatch_msg_id': 'job'}, 'done',
                    Path(directory) / 'log', results_path=results)
            gh.assert_not_called()
            saved = json.loads(dispatch._skill_ledger(results).read_text().splitlines()[-1])
            self.assertEqual(saved['status'], 'completed')
            self.assertIn('skill_receipt_error', saved)
            self.assertIsNone(saved.get('completed_at'))

    def test_completion_time_matches_the_first_terminal_reply_after_dispatch(self):
        start, end = '2026-10-19T15:00:00Z', '2026-10-19T15:00:02Z'
        for final in (
            {'type': 'assistant', 'message': {'stop_reason': 'end_turn',
                'content': [{'type': 'text', 'text': ' done ' }]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'channel': 'final', 'content': [{'type': 'output_text', 'text': 'done'}]}},
        ):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as directory:
                transcript = Path(directory) / 'session.jsonl'
                transcript.write_text('\n'.join(json.dumps(event) for event in (
                    {**final, 'timestamp': '2026-10-19T14:00:00Z'},
                    {**final, 'timestamp': end},
                    {**final, 'timestamp': '2026-10-19T16:00:00Z'})))
                self.assertEqual(skill_receipts.completion_time(transcript, since=start, result='done'), end)
                self.assertIsNone(skill_receipts.completion_time(transcript, since=start, result='other'))
                transcript.write_text(json.dumps(final))
                self.assertIsNone(skill_receipts.completion_time(transcript, since=start, result='done'))

    def test_delivery_is_not_completion(self):
        with patch.object(dispatch, '_skill_gh') as gh:
            self.assertIsNone(dispatch.finish_skill_receipt({'status': 'delivered'}))
        gh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
