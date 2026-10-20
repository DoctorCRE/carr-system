"""The CLI returns a durable receipt without launching or waiting for a model."""
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
import work_runner
from work_queue import WorkQueue, default_path
from test_codex_models_unit import catalog_fixture


class WorkCLITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / 'global.sqlite3'
        env = patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(self.db)})
        env.start()
        self.addCleanup(env.stop)

    def test_send_enqueues_and_existing_card_stage_reuses_identity(self):
        with tempfile.TemporaryDirectory() as tmp, catalog_fixture():
            root = Path(tmp)
            reg = desks.Registry(root / 'desks.json')
            reg.register('sol', 'codex-session', family='sol', effort='high')
            results = root / 'results.jsonl'
            prefix = ['--registry', str(reg.path), '--results', str(results)]
            output = io.StringIO()
            with patch.object(work_runner, 'kick') as kick, patch.object(dispatch, 'dispatch') as execute, \
                 patch.object(dispatch.seat_gate, 'check', return_value=None), contextlib.redirect_stdout(output):
                code = dispatch.main(prefix + ['send', 'sol', 'Build it', '--card-id', 'WR14.S2.J1.carr#1960',
                    '--title', 'Queue', '--description', 'One order', '--checkout', 'new:queue'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'queued')
            execute.assert_not_called()
            kick.assert_called_once()
            with WorkQueue(default_path()) as q:
                card = q.get('WR14.S2.J1.carr#1960')
                self.assertEqual((card['title'], card['description']), ('Queue', 'One order'))
                self.assertEqual((card['wr'], card['slice']), ('WR14', 'S2'))
                self.assertEqual(q.candidates()[0]['desk'], 'sol')

    def test_board_snapshot_exposes_the_same_order_and_details(self):
        import os
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import progress_board
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'work.sqlite3'
            with WorkQueue(db) as q:
                q.enqueue('A', desk='sol', task='private task', actor='test', title='First')
                q.enqueue('B', desk='luna', task='private task', actor='test', title='Priority')
                q.prioritize('B', actor='orchestrator', reason='next')
                with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(db)}):
                    snapshot = progress_board.board_snapshot({'project': 'carr-v5'}, costs={})
                self.assertEqual([i['card_id'] for i in snapshot['work_queue']['items']], ['B', 'A'])
                self.assertEqual(snapshot['work_queue']['items'][0]['title'], 'Priority')
                self.assertNotIn('private task', json.dumps(snapshot))

    def test_different_result_directories_share_the_global_queue(self):
        with tempfile.TemporaryDirectory() as tmp, catalog_fixture():
            root = Path(tmp)
            reg = desks.Registry(root / 'desks.json')
            reg.register('sol', 'codex-session', family='sol', effort='high')
            with patch.object(work_runner, 'kick'), patch.object(dispatch.seat_gate, 'check', return_value=None), \
                 contextlib.redirect_stdout(io.StringIO()):
                for name in ('one', 'two'):
                    self.assertEqual(dispatch.main(['--registry', str(reg.path), '--results', str(root / name / 'results.jsonl'),
                        'send', 'sol', name, '--card-id', name]), 0)
            with WorkQueue(self.db) as q:
                self.assertEqual([i['card_id'] for i in q.export()['items']], ['one', 'two'])


if __name__ == '__main__':
    unittest.main()
