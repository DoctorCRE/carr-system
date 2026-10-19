import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_receipts
import dispatch


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

    def test_only_successful_real_reads_count(self):
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md'))['opened'],
                         ['/skills/tdd/SKILL.md'])
        for command in ('echo /skills/tdd/SKILL.md', 'rg --files /skills/tdd/SKILL.md',
                        'cat /skills/tdd/SKILL.md > /tmp/copy',
                        'echo "cat /skills/tdd/SKILL.md"'):
            self.assertEqual(self.scan(self.codex(command))['opened'], [], command)
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md',
                                             'Process exited with code 1'))['opened'], [])

    def test_exec_wrapper_and_claude_read(self):
        rows = self.codex('unused')
        rows[0]['payload'].update(name='functions.exec', arguments=json.dumps({
            'code': 'text(await tools.exec_command({cmd:"cat /skills/tdd/SKILL.md"}));'}))
        rows[1]['payload']['output'] = '{"exit_code":0,"output":"skill"}'
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

    def test_prior_job_reads_and_unfinished_calls_do_not_count(self):
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md'),
            since='2026-10-19T16:00:00Z')['opened'], [])
        self.assertEqual(self.scan(self.codex('cat /skills/tdd/SKILL.md')[:1])['opened'], [])

    def test_aliases_and_missing_inventory_fail_before_launch(self):
        self.assertEqual(skill_receipts.claims('Skills: Pocock TDD, Prove It Works; poteto-mode'),
                         ['tdd', 'prove-it-works', 'poteto-mode'])
        with self.assertRaisesRegex(ValueError, 'not available.*grill-with-docs'):
            skill_receipts.require_skills('Skills: grill-with-docs', {'available': ['tdd']})
        with self.assertRaisesRegex(ValueError, 'inventory unknown'):
            skill_receipts.require_skills('Skills: tdd', None)

    def test_completed_job_uses_its_own_thread_and_dispatch_time(self):
        row = {'msg_id': 'job', 'status': 'completed', 'thread_id': 'own-session',
               'kind': 'codex-session', 'dispatched_at': '2026-10-19T15:00:00Z',
               'result': 'https://github.com/jbookout/carr-system/pull/2000'}
        with patch.object(skill_receipts, 'session_path', return_value=Path('/own-log')) as locate, \
             patch.object(skill_receipts, 'scan', return_value={'available': ['tdd'], 'opened': []}) as scan, \
             patch.object(dispatch, '_skill_gh', side_effect=[
                 {'head': {'sha': 'a' * 40}, 'body': 'Skills used: tdd'}, [],
                 {'html_url': 'https://github.com/jbookout/carr-system/issues/2000#issuecomment-1'}, {}]):
            value = dispatch.finish_skill_receipt(row)
        locate.assert_called_once_with('own-session', 'codex')
        scan.assert_called_once_with(Path('/own-log'), since=row['dispatched_at'])
        self.assertFalse(value['skills'][0]['opened'])

    def test_delivery_is_not_completion(self):
        with patch.object(dispatch, '_skill_gh') as gh:
            self.assertIsNone(dispatch.finish_skill_receipt({'status': 'delivered'}))
        gh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
