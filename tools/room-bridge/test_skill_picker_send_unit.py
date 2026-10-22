"""The send CLI queues a baseline-enriched brief even when scoring fails."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
from work_queue import WorkQueue


class SkillSendTests(unittest.TestCase):
    def test_sdk_failure_still_queues_baseline_and_records_only_picks(self):
        class BrokenClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def system_one(self, **_):
                raise RuntimeError('do not record provider body')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('poteto-mode', 'principle-prove-it-works'):
                path = root / '.agents/skills' / name / 'SKILL.md'
                path.parent.mkdir(parents=True)
                path.write_text(f'---\nname: {name}\ndescription: Fixture\n---\nAll {name} instructions.\n')
            registry = desks.Registry(root / 'registry.json')
            registry.register('fixture-desk', 'codex-session', family='sol', effort='low', cwd=tmp)
            queue_path = root / 'queue.sqlite3'
            out = io.StringIO()
            with patch.dict(os.environ, {'HOME': tmp, 'CARR_WORK_QUEUE_DB': str(queue_path),
                                         'TYPESAFE_API_KEY': 'fixture-only'}, clear=True), \
                 patch.object(dispatch.seat_gate, 'check', return_value=None), \
                 patch('work_runner.kick') as kick, \
                 patch('typesafe_sdk.AsyncTypeSafeClient', return_value=BrokenClient()) as sdk, \
                 contextlib.redirect_stdout(out):
                code = dispatch.main(['--registry', str(registry.path), '--results', str(root / 'results.jsonl'),
                                      'send', 'fixture-desk', 'Delete an obsolete wrapper.', '--card-id', 'fixture'])
            self.assertEqual(code, 0, out.getvalue())
            self.assertEqual(json.loads(out.getvalue())['status'], 'queued')
            sdk.assert_called_once()
            self.assertEqual(sdk.call_args.kwargs['timeout'], 5)
            self.assertEqual(sdk.call_args.kwargs['retry'].max_retries, 0)
            kick.assert_called_once()
            with WorkQueue(queue_path) as queue:
                card = queue.get('fixture')
                self.assertEqual(card['required_skills'], [
                    {'name': 'poteto-mode', 'probability': None},
                    {'name': 'principle-prove-it-works', 'probability': None}])
                claimed = queue.claim(eligible_desks={'fixture-desk'}, actor='test', owner='test')
                self.assertIn('All poteto-mode instructions.', claimed['task'])
                self.assertIn('picker skipped: RuntimeError', claimed['task'])
                self.assertNotIn('do not record provider body', claimed['task'])
                self.assertEqual(card['description'], '')
            self.assertFalse((root / 'results.jsonl').exists())

    def test_builder_points_at_picker_without_manual_skill_declarations(self):
        builder = (Path(__file__).parent / 'briefs/builder.md').read_text()
        self.assertIn('Required skills', builder)
        self.assertIn('dispatch.py send', builder)
        self.assertNotIn('Skills:', builder)
        footer = (Path(__file__).parent / 'brief-footer.md').read_text()
        self.assertFalse(any(line.startswith('Skills:') for line in footer.splitlines()))


if __name__ == '__main__':
    unittest.main()
