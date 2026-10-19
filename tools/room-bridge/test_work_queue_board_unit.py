"""The board consumes the queue's ordered export, including parent rollups."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_queue import WorkQueue, board_projection
import progress_board


class QueueBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'queue.sqlite3'
        self.q = WorkQueue(self.path)
        self.addCleanup(self.q.close)

    def add(self, card, wr, slice):
        return self.q.enqueue(card, desk='desk-a', task='Private prompt stays local', actor='test',
                              wr=wr, slice=slice, title='Queue card', description='Move work in priority order.')

    def test_export_orders_parents_by_first_child_and_rolls_up_details(self):
        self.add('WR14.S1.J1', 'WR-000014', 'S1')
        self.add('WR20.S1.J1', 'WR-000020', 'S1')
        self.add('WR14.S2.J1', 'WR-000014', 'S2')
        self.q.update('WR14.S2.J1', actor='reviewer', reason='review receipt', findings=[
            {'id': 'f1', 'level': 'P1', 'summary': 'Preserve focus', 'resolved': False}])
        self.q.prioritize('WR14.S2.J1', actor='orchestrator', reason='Unblocks the app')
        tree = self.q.export()['board']
        self.assertEqual(tree['order'], ['WR14', 'WR20'])
        self.assertEqual(tree['nodes']['WR14']['children'], ['WR14.S2', 'WR14.S1'])
        self.assertEqual(tree['nodes']['WR14.S2']['children'], ['WR14.S2.J1'])
        self.assertEqual(tree['nodes']['WR14']['totals']['jobs'], 2)
        self.assertEqual(tree['nodes']['WR14']['totals']['findings_opened'], 1)
        self.assertEqual(tree['nodes']['WR14']['totals']['findings_resolved'], 0)
        self.q.prioritize('WR20.S1.J1', actor='orchestrator', reason='Next dependency')
        self.assertEqual(self.q.export()['board']['order'], ['WR20', 'WR14'])

    def test_live_stage_time_and_priority_log_are_exported_without_private_text(self):
        self.add('WR14.S1.J1', 'WR-000014', 'S1')
        with patch('work_queue.time.time', return_value=100):
            self.q.claim(eligible_desks={'desk-a'}, actor='runner', owner={'pid': 42})
        self.q.prioritize('WR14.S1.J1', actor='orchestrator', reason='Product dependency')
        with patch('work_queue.time.time', return_value=112):
            item = self.q.export()['items'][0]
        self.assertEqual(item['stages'][-1]['elapsed'], 12)
        self.assertEqual(item['total_time_s'], 12)
        self.assertEqual(item['priority_changes'][-1]['reason'], 'Product dependency')
        self.assertNotIn('task', item)
        self.assertNotIn('owner', item)

    def test_hierarchy_snapshot_carries_the_queue_export_even_with_no_board_task(self):
        self.add('WR14.S1.J1', 'WR-000014', 'S1')
        with patch.dict(os.environ, CARR_WORK_QUEUE_DB=str(self.path)):
            snap = progress_board.board_snapshot({'project': 'carr-v5', 'work_requests': {
                'WR-000014': {'card_title': 'Queue order', 'card_description': 'Keep work moving.'}}, 'tasks': {}}, costs={})
        self.assertEqual(snap['work_queue'], board_projection(self.path))
        self.assertEqual(snap['work_queue']['items'][0]['card_id'], 'WR14.S1.J1')


if __name__ == '__main__':
    unittest.main()
